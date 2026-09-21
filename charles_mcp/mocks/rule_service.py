"""Create and manage body-aware dispatcher rules from captured Charles traffic."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs

import httpx

from charles_mcp.analyzers.resource_classifier import classify_entry
from charles_mcp.config import Config
from charles_mcp.mocks.charles_config import apply_map_remote_route
from charles_mcp.mocks.dispatcher import (
    HEALTH_SUFFIX,
    RULE_HEADER,
    DispatcherSettings,
    DispatcherThread,
)
from charles_mcp.mocks.fixtures import encode_fixture
from charles_mcp.mocks.json_patch import (
    JsonPatchError,
    apply_patches,
    get_pointer,
    pointers_may_alias,
    type_change_warnings,
)
from charles_mcp.mocks.rules import (
    MockRule,
    RouteConfig,
    RuleMatch,
    RuleRequest,
    RuleResponse,
    RuleStore,
    host_matches,
    make_rule_id,
    normalize_path_pattern,
    parse_body_document,
    route_covers_rule_path,
    rule_path_matches,
)
from charles_mcp.mocks.store import (
    ANY_HOST,
    MockStore,
    is_host_pattern,
    normalize_host,
    normalize_host_pattern,
    split_request_path,
)
from charles_mcp.schemas.mocks import (
    DispatcherStatusResult,
    MapRemoteRoute,
    RouteSetupResult,
    RuleContentResult,
    RuleListResult,
    RuleRemoveResult,
    RuleSummary,
    RuleWriteResult,
    VariantDiscoveryResult,
    VariantGroup,
)
from charles_mcp.schemas.traffic import CaptureSource, TrafficEntry
from charles_mcp.services.traffic_query_service import TrafficQueryService

_MAX_BODY_CHARS = 5_000_000
_LOOPBACK = "127.0.0.1"
_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Same noise classes the api_focus preset excludes.
_NOISE_CLASSES = {"control", "static_asset", "font", "media", "connect_tunnel"}
_NEXT_STEP_AFTER_RULE = (
    "Make the app repeat the request, then confirm with query_live_capture_entries "
    "(path_contains=<path>, response_header_name='X-Charles-MCP-Rule'): the dispatcher "
    "adds that header with the rule id. Requires a route covering the host and path "
    "(mock_route_setup) and a running dispatcher (mock_dispatcher action=start)."
)


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _summary(rule: MockRule, file: Path) -> RuleSummary:
    return RuleSummary(
        host=rule.host,
        id=rule.id,
        enabled=rule.enabled,
        priority=rule.priority,
        method=rule.match.method,
        path=rule.match.path,
        body_match_json=json.dumps(rule.match.body, ensure_ascii=False, sort_keys=True),
        response_mode=rule.response.mode,
        status=rule.response.status,
        delay_ms=rule.response.delay_ms,
        response_patches=len(rule.response.patches),
        request_patches=len(rule.request.patches),
        request_header_edits=len(rule.request.headers),
        file=str(file),
    )


def _normalize_path(path: str) -> str:
    segments, query = split_request_path(path)
    if query:
        raise ValueError("path must not contain a query string")
    return "/" + "/".join(segments)


def merge_patches(
    existing: list[dict[str, Any]], new: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Later edits win. A new patch drops any earlier one on the same pointer, and
    any earlier one that may address the same array element by a different mode
    (index vs ``[field=value]``) — otherwise both would apply and could hit
    different elements once the server reorders the list. Returns the merged
    patches and the pointers dropped as aliases (for a warning)."""
    kept: list[dict[str, Any]] = []
    aliased: list[str] = []
    for patch in existing:
        exact = any(patch["path"] == other["path"] for other in new)
        alias = any(pointers_may_alias(patch["path"], other["path"]) for other in new)
        if exact:
            continue
        if alias:
            aliased.append(patch["path"])
            continue
        kept.append(patch)
    return kept + list(new), aliased


def probe_target(route: RouteConfig, path_pattern: str) -> tuple[str, str] | None:
    """Build a probe host and path for a route, or None when it cannot be probed.

    A host glob becomes a name that cannot resolve (`*.example.com` ->
    `charles-mcp-probe.example.com`), so an unmapped probe fails in Charles
    instead of reaching the real server. A route pinned to one exact path has no
    room for the probe path and is skipped.
    """
    host = route.host.replace("*", "charles-mcp-probe", 1) if "*" in route.host else route.host
    if path_pattern == "/*":
        return host, HEALTH_SUFFIX
    if path_pattern.endswith("/*"):
        return host, path_pattern[:-2] + HEALTH_SUFFIX
    return None


def _route_labels(routes: list[RouteConfig]) -> list[str]:
    """``host/path`` per route path, flagging routes with TLS checking disabled."""
    return [
        f"{route.host}{path}" + ("" if route.verify_tls else " (verify_tls=false)")
        for route in routes
        for path in route.paths
    ]


class RuleService:
    def __init__(
        self,
        config: Config,
        *,
        traffic_query_service: TrafficQueryService,
        client_factory: Any = None,
        store: RuleStore | None = None,
    ) -> None:
        self.config = config
        self.traffic_query_service = traffic_query_service
        self.client_factory = client_factory
        self.store = store or RuleStore(config.mock_dir)
        self._dispatcher: DispatcherThread | None = None

    # ---- routes -------------------------------------------------------------

    def setup_route(
        self,
        host: str,
        path: str = "/*",
        *,
        apply: bool = False,
        port: int = 443,
        verify_tls: bool = True,
    ) -> RouteSetupResult:
        host_pattern = normalize_host_pattern(host)
        if host_pattern == ANY_HOST:
            raise ValueError("a route needs a hostname or a domain glob such as *.example.com")
        existing = self.store.get_route(host_pattern)
        route = RouteConfig(
            host=host_pattern,
            upstream_port=port,
            verify_tls=verify_tls,
            paths=[*(existing.paths if existing else []), path],
        )
        path_pattern = normalize_path_pattern(path)
        route_file = self.store.save_route(route)
        destination = f"http://{_LOOPBACK}:{self.config.dispatcher_port}"
        wildcard_path = "*" in path_pattern
        rule = MapRemoteRoute(
            host=host_pattern,
            port=port,
            path=path_pattern,
            destination=destination if wildcard_path else f"{destination}{path_pattern}",
            path_unchanged=wildcard_path,
        )
        result = RouteSetupResult(
            host=host_pattern, path=path_pattern, route_file=str(route_file), map_remote_rule=rule
        )
        if path_pattern == "/*":
            result.warnings.append(
                "Every request to this host goes through the dispatcher. Unmocked requests pass "
                "through unchanged, but WebSocket upgrades are not supported and large downloads "
                "are buffered; narrow the route to the API prefix (e.g. /api/*) if the host "
                "serves those."
            )
        if not is_host_pattern(host_pattern) and not wildcard_path:
            mock_file = MockStore(self.config.mock_dir).locate(host_pattern, path_pattern).file
            if mock_file.is_file():
                result.warnings.append(
                    f"A Map Local mock exists for this path ({mock_file}); remove it with "
                    "mock_remove so requests reach the dispatcher."
                )
        if not verify_tls:
            result.warnings.append(
                f"TLS verification is OFF for {host_pattern}: the dispatcher trusts any "
                "upstream certificate on this route. Use it only for hosts with an internal "
                "CA, never for production, and re-run mock_route_setup with verify_tls=true "
                "to restore checking."
            )

        charles_to = (
            f"{destination} with an empty path (the original path is kept)"
            if wildcard_path
            else f"{destination}{path_pattern}"
        )
        if not apply:
            result.instructions = [
                (
                    "In Charles open Tools > Map Remote, tick Enable Map Remote and add a "
                    f"mapping: From Protocol https, Host {host_pattern}, Port {port}, "
                    f"Path {path_pattern}; To {charles_to}."
                ),
                "Tick 'Preserve host header' on that mapping; the dispatcher needs it.",
                "Press Done (Cancel discards the mapping).",
                "Start the dispatcher: mock_dispatcher(action='start').",
                "Keep SSL Proxying enabled for the host(s).",
                (
                    "Alternatively ask the user to quit Charles, then call mock_route_setup with "
                    "apply=true to write the mapping into the Charles config."
                ),
            ]
            return result

        if not self.config.config_path:
            raise ValueError(
                "Charles config file was not found; set CHARLES_CONFIG_PATH or run "
                "mock_route_setup with apply=false and follow the manual steps"
            )
        if _port_open(self.config.proxy_host, self.config.proxy_port):
            raise ValueError(
                "Charles is running. It rewrites its config file from memory on quit, so "
                "edits made now would be lost even after a restart. Ask the user to quit "
                "Charles (save the session first), then call mock_route_setup with apply=true."
            )
        change = apply_map_remote_route(
            self.config.config_path,
            host=host_pattern,
            path=path_pattern,
            backup_dir=Path(self.config.state_dir) / "charles-config-backups" / "map-remote",
            dest_host=_LOOPBACK,
            dest_port=self.config.dispatcher_port,
            port=port,
        )
        result.applied = True
        result.config_path = change.config_path
        result.config_backup = change.backup_path
        result.mapping_added = change.mapping_added
        result.warnings.extend(change.warnings)
        result.instructions = [
            "Start Charles; the Map Remote mapping is active.",
            "Start the dispatcher: mock_dispatcher(action='start').",
            "Keep SSL Proxying enabled for the host(s).",
        ]
        return result

    # ---- discovery ------------------------------------------------------------

    async def _entry(
        self,
        source: CaptureSource,
        entry_id: str,
        capture_id: str | None,
        recording_path: str | None,
    ) -> TrafficEntry:
        detail = await self.traffic_query_service.get_detail(
            source=source,
            entry_id=entry_id,
            capture_id=capture_id,
            recording_path=recording_path,
            include_full_body=True,
            max_body_chars=_MAX_BODY_CHARS,
        )
        return detail.detail.entry

    async def _raw_entries(
        self,
        source: CaptureSource,
        capture_id: str | None,
        recording_path: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """One export of the capture (or one read of the recording); never paged."""
        if source == "live":
            if not capture_id:
                raise ValueError("capture_id is required for source='live'")
            read = await self.traffic_query_service.live_service.read(
                capture_id, cursor=0, limit=limit, advance=False
            )
            return list(read.items), None
        history = self.traffic_query_service.history_service
        if recording_path:
            return await history.get_snapshot(recording_path), recording_path
        path, raw = await history.load_latest_with_path()
        return raw[:limit], path

    async def discover_variants(
        self,
        *,
        source: CaptureSource,
        capture_id: str | None = None,
        recording_path: str | None = None,
        host_contains: str | None = None,
        path_contains: str | None = None,
        body_field: str = "/action",
        methods: list[str] | None = None,
        limit: int = 5000,
        max_groups: int = 200,
    ) -> VariantDiscoveryResult:
        """Group captured API requests by method, host, path and a body field value.

        Reads the capture once and parses full request bodies, so long sessions cost
        one Charles export instead of one per page or per truncated preview.
        """
        raw_entries, identity = await self._raw_entries(source, capture_id, recording_path, limit)
        result = VariantDiscoveryResult(
            host_contains=host_contains,
            path_contains=path_contains,
            recording_path=identity,
            body_field=body_field,
            scanned=0,
        )
        wanted_methods = {method.upper() for method in methods or []}
        host_filter = (host_contains or "").lower()
        groups: dict[tuple[str, str, str, str], VariantGroup] = {}
        normalizer = self.traffic_query_service.normalizer
        for raw in raw_entries[:limit]:
            classification = classify_entry(raw)
            if classification.resource_class in _NOISE_CLASSES:
                continue
            method = str(raw.get("method") or "").upper()
            host = str(raw.get("host") or "").lower()
            path = str(raw.get("path") or "")
            if (
                (wanted_methods and method not in wanted_methods)
                or (host_filter and host_filter not in host)
                or (path_contains and path_contains not in path)
            ):
                continue
            entry = normalizer.normalize_entry(
                raw,
                capture_source=source,
                capture_id=capture_id if source == "live" else None,
                recording_path=identity,
                include_full_body=method in _BODY_METHODS,
                max_full_body_chars=_MAX_BODY_CHARS,
                classification=classification,
            )
            result.scanned += 1
            value: Any = None
            if method in _BODY_METHODS and entry.request.body.full_text:
                document = parse_body_document(
                    entry.request.body.full_text.encode("utf-8"),
                    entry.request.body.mime_type or entry.request.mime_type or "",
                )
                try:
                    value = get_pointer(document, body_field) if document is not None else None
                except KeyError:
                    value = None
            key_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
            key = (method, host, path, key_json)
            group = groups.get(key)
            if group is None:
                group = VariantGroup(method=method, host=host, path=path, value_json=key_json, count=0)
                groups[key] = group
            group.count += 1
            if len(group.entry_ids) < 3:
                group.entry_ids.append(entry.entry_id)
            if entry.response_status and entry.response_status not in group.response_statuses:
                group.response_statuses.append(entry.response_status)
        if len(raw_entries) > limit:
            result.warnings.append(f"only the first {limit} of {len(raw_entries)} entries read")
        ordered = sorted(groups.values(), key=lambda g: (g.host, g.path, g.method, g.value_json))
        result.total_groups = len(ordered)
        if len(ordered) > max_groups:
            result.warnings.append(
                f"{len(ordered)} groups; showing the first {max_groups}. Narrow with "
                "host_contains/path_contains."
            )
        result.groups = ordered[:max_groups]
        return result

    # ---- rules ----------------------------------------------------------------

    async def create_rule_from_entry(
        self,
        *,
        source: CaptureSource,
        entry_id: str,
        capture_id: str | None = None,
        recording_path: str | None = None,
        match_body_fields: list[str] | None = None,
        match_query_fields: list[str] | None = None,
        mode: Literal["fixture", "patch"] = "patch",
        response_patches: list[dict[str, Any]] | None = None,
        request_patches: list[dict[str, Any]] | None = None,
        request_headers: dict[str, str | None] | None = None,
        status: int | None = None,
        delay_ms: int | None = None,
        priority: int = 0,
        host_scope: Literal["any", "exact"] = "any",
        host: str | None = None,
        match_path: str | None = None,
        merge: bool = True,
        rule_id: str | None = None,
        description: str | None = None,
    ) -> RuleWriteResult:
        entry = await self._entry(source, entry_id, capture_id, recording_path)
        if not entry.host or not entry.path:
            raise ValueError(f"entry `{entry_id}` has no host/path")
        entry_host = normalize_host(entry.host)
        rule_host = normalize_host_pattern(host) if host else (
            ANY_HOST if host_scope == "any" else entry_host
        )
        if not host_matches(rule_host, entry_host):
            raise ValueError(f"host `{rule_host}` does not cover the entry host `{entry_host}`")
        path = _normalize_path(entry.path)
        if match_path:
            pattern = _normalize_path(match_path)
            if not rule_path_matches(pattern, path):
                raise ValueError(f"match_path `{pattern}` does not cover the entry path `{path}`")
            path = pattern
        method = (entry.method or "").upper() or None

        body_match: dict[str, Any] = {}
        request_document: Any = None
        if entry.request.body.full_text and not entry.request.body.full_text_truncated:
            request_document = parse_body_document(
                entry.request.body.full_text.encode("utf-8"), entry.request.body.mime_type or ""
            )
        if match_body_fields:
            if request_document is None:
                raise ValueError(f"entry `{entry_id}` request body is not JSON or form data")
            for pointer in match_body_fields:
                try:
                    body_match[pointer] = get_pointer(request_document, pointer)
                except KeyError:
                    raise ValueError(
                        f"request body of `{entry_id}` has no field `{pointer}`"
                    ) from None

        query_match: dict[str, str] = {}
        if match_query_fields:
            query = parse_qs(entry.query or "", keep_blank_values=True)
            for name in match_query_fields:
                if name not in query:
                    raise ValueError(f"query of `{entry_id}` has no parameter `{name}`")
                query_match[name] = query[name][0]

        response_body = entry.response.body
        response_document: Any = None
        if (
            response_body.full_text is not None
            and not response_body.full_text_truncated
            and response_body.kind == "json"
        ):
            response_document = json.loads(response_body.full_text)

        new_id = rule_id or make_rule_id(method, path, {**body_match, **query_match})
        response_patches = list(response_patches or [])
        request_patches = list(request_patches or [])
        request_header_edits = dict(request_headers or {})
        warnings: list[str] = []
        previous: MockRule | None = None
        if merge and self.store.rule_path(rule_host, new_id).is_file():
            previous = self.store.get_rule(rule_host, new_id)
            if previous.response.mode == mode:
                response_patches, response_aliased = merge_patches(
                    previous.response.patches, response_patches
                )
                request_patches, request_aliased = merge_patches(
                    previous.request.patches, request_patches
                )
                request_header_edits = {**previous.request.headers, **request_header_edits}
                status = status if status is not None else previous.response.status
                delay_ms = delay_ms if delay_ms is not None else previous.response.delay_ms
                warnings.append(
                    f"merged into existing rule `{new_id}`: {len(response_patches)} response and "
                    f"{len(request_patches)} request patch(es) now"
                )
                for pointer in response_aliased + request_aliased:
                    warnings.append(
                        f"dropped the previous patch `{pointer}`: a new patch addresses the same "
                        "array element by a different key, and keeping both could edit the wrong one"
                    )
            else:
                warnings.append(f"replaced rule `{new_id}` (mode {previous.response.mode} -> {mode})")

        for label, patches, document in (
            ("response", response_patches, response_document),
            ("request", request_patches, request_document),
        ):
            if patches and document is not None:
                warnings.extend(type_change_warnings(document, patches, label))
                try:
                    apply_patches(document, patches)
                except JsonPatchError as exc:
                    raise ValueError(
                        f"{label} patches do not fit the captured {label}: {exc}"
                    ) from exc
            elif patches:
                warnings.append(
                    f"the captured {label} is not JSON, so the {label} patches could not be "
                    "checked; they are skipped at runtime if the live body is not JSON either"
                )

        fixture: bytes | None = None
        headers: dict[str, str] = {}
        if mode == "fixture":
            if response_body.full_text is None or response_body.full_text_truncated:
                raise ValueError(f"entry `{entry_id}` has no complete response body for a fixture")
            # Verbatim, including whitespace and key order: the dispatcher
            # applies this rule's patches when it serves the fixture, so there
            # is nothing to re-serialise here. source_text is the body as the
            # server wrote it; full_text is the normalised rendering.
            fixture_text = response_body.source_text
            if fixture_text is None:
                fixture_text = response_body.full_text
                if response_body.kind == "json":
                    warnings.append(
                        "stored the normalised body: the captured original was too large to keep "
                        "whole, so its whitespace may differ from the server's response"
                    )
            content_type = (
                entry.response.mime_type or response_body.mime_type or "application/json; charset=utf-8"
            )
            fixture, content_type, charset_warning = encode_fixture(
                fixture_text, content_type, response_body.charset
            )
            if charset_warning:
                warnings.append(charset_warning)
            headers["Content-Type"] = content_type
            status = status or entry.response_status or 200
            if request_patches or request_header_edits:
                warnings.append(
                    "request patches and header edits are ignored in fixture mode: "
                    "nothing goes upstream"
                )

        rule = MockRule(
            id=new_id,
            host=rule_host,
            priority=priority,
            description=description or (previous.description if previous else None),
            match=RuleMatch(method=method, path=path, query=query_match, body=body_match),
            request=RuleRequest(patches=request_patches, headers=request_header_edits),
            response=RuleResponse(
                mode=mode,
                status=status,
                headers=headers,
                delay_ms=delay_ms,
                patches=response_patches,
            ),
            source_entry_id=entry_id,
        )
        return self._save(rule, fixture, warnings)

    def write_rule(
        self,
        rule: dict[str, Any],
        *,
        fixture_json: Any = None,
        fixture_text: str | None = None,
    ) -> RuleWriteResult:
        if fixture_json is not None and fixture_text is not None:
            raise ValueError("pass at most one of fixture_json or fixture_text")
        parsed = MockRule.model_validate(rule)
        fixture: bytes | None = None
        if fixture_json is not None:
            fixture = (json.dumps(fixture_json, ensure_ascii=False, indent=2) + "\n").encode()
        elif fixture_text is not None:
            fixture = fixture_text.encode("utf-8")
        if (
            parsed.response.mode == "fixture"
            and fixture is None
            and not self.store.fixture_path(parsed.host, parsed.id).is_file()
        ):
            raise ValueError("fixture rules need fixture_json or fixture_text")
        return self._save(parsed, fixture, [])

    def list_rules(self, host: str | None = None) -> RuleListResult:
        rules, errors = self.store.all_rules()
        if host:
            wanted = normalize_host_pattern(host)
            rules = [
                rule
                for rule in rules
                if rule.host == wanted
                or (not is_host_pattern(wanted) and host_matches(rule.host, wanted))
            ]
        routes = self.store.list_routes()
        return RuleListResult(
            mock_dir=str(self.store.root),
            routes=_route_labels(routes),
            total=len(rules),
            items=[_summary(rule, self.store.rule_path(rule.host, rule.id)) for rule in rules],
            errors=errors,
        )

    def get_rule(self, host: str, rule_id: str, *, max_chars: int = 4000) -> RuleContentResult:
        rule = self.store.get_rule(host, rule_id)
        result = RuleContentResult(rule_json=rule.model_dump_json(indent=2))
        if rule.response.mode == "fixture":
            try:
                text = self.store.read_fixture(host, rule_id).decode("utf-8", errors="replace")
            except FileNotFoundError:
                return result
            result.fixture_truncated = len(text) > max_chars
            result.fixture = text[:max_chars]
        return result

    def remove_rule(self, host: str, rule_id: str) -> RuleRemoveResult:
        archived = self.store.remove_rule(host, rule_id)
        return RuleRemoveResult(
            host=normalize_host_pattern(host),
            id=rule_id,
            archived_to=archived,
            next_step="Matching requests now pass through to the real server.",
        )

    def set_rule_enabled(self, host: str, rule_id: str, enabled: bool) -> RuleSummary:
        rule = self.store.get_rule(host, rule_id).model_copy(update={"enabled": enabled})
        path, _ = self.store.save_rule(rule, archive_previous=False)
        return _summary(rule, path)

    def _save(
        self, rule: MockRule, fixture: bytes | None, warnings: list[str]
    ) -> RuleWriteResult:
        covered = any(
            (is_host_pattern(rule.host) or host_matches(route.host, rule.host))
            and any(route_covers_rule_path(pattern, rule.match.path) for pattern in route.paths)
            for route in self.store.list_routes()
        )
        if not covered:
            warnings = [
                *warnings,
                f"no route sends {rule.match.path} to the dispatcher yet; run mock_route_setup "
                "for the host (or its domain glob).",
            ]
        path, archived = self.store.save_rule(rule, fixture)
        fixture_file = self.store.fixture_path(rule.host, rule.id)
        return RuleWriteResult(
            rule=_summary(rule, path),
            rule_json=rule.model_dump_json(indent=2),
            fixture_file=str(fixture_file) if fixture_file.is_file() else None,
            archived_previous=archived,
            warnings=warnings,
            next_step=_NEXT_STEP_AFTER_RULE,
        )

    # ---- dispatcher -----------------------------------------------------------

    async def _set_map_remote(self, enabled: bool) -> str:
        if self.client_factory is None:
            return ""
        try:
            async with self.client_factory(self.config) as client:
                ok = await client.set_tool_enabled("map-remote", enabled)
        except Exception as exc:  # Charles down or web interface off
            return f"; could not {'enable' if enabled else 'disable'} Map Remote in Charles: {exc}"
        state = "enabled" if enabled else "disabled"
        return f"; Map Remote {state} in Charles" if ok else f"; Charles refused to set Map Remote {state}"

    async def _probe_dispatcher(self, port: int) -> tuple[bool, str]:
        """Ask the dispatcher itself, without Charles in the path."""
        url = f"http://{_LOOPBACK}:{port}{HEALTH_SUFFIX}"
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            return False, f"the dispatcher does not answer on {port}: {type(exc).__name__}"
        if response.json().get("dispatcher") == "charles-mcp":
            return True, f"the dispatcher answers on {port}"
        return False, f"something else answers on {port}"

    async def _probe_through_charles(self, route: RouteConfig, path_pattern: str) -> str:
        """Send one probe through the Charles proxy: does Charles route it here?"""
        target = probe_target(route, path_pattern)
        if target is None:
            return (
                f"{route.host}{path_pattern}: skipped, an exact-path route leaves no room for a "
                "probe path; verify it with real app traffic instead"
            )
        host, path = target
        url = f"https://{host}{path}"
        try:
            # verify=False on purpose: Charles re-signs the probe with its own CA,
            # and the answer is checked by its marker, not by the certificate.
            async with httpx.AsyncClient(
                proxy=self.config.proxy_url, verify=False, trust_env=False, timeout=8.0
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            return (
                f"{route.host}{path_pattern}: Charles did not route the probe "
                f"({type(exc).__name__}). Map Remote is off, or the mapping is not loaded because "
                "Charles was not started after the config was written, or SSL Proxying does not "
                "cover the host"
            )
        if response.headers.get(RULE_HEADER) == "health":
            return f"{route.host}{path_pattern}: Charles routes it to the dispatcher"
        if response.status_code == 503:
            return (
                f"{route.host}{path_pattern}: Charles answered 503, so it tried the real host "
                "instead of the dispatcher; the mapping is not active"
            )
        return (
            f"{route.host}{path_pattern}: answered by something else (HTTP "
            f"{response.status_code}), so the mapping does not cover this path"
        )

    async def dispatcher(
        self,
        action: Literal["start", "stop", "status", "verify"],
        port: int | None = None,
        toggle_map_remote: bool = True,
    ) -> DispatcherStatusResult:
        """Start/stop the in-process dispatcher.

        With ``toggle_map_remote`` Charles Map Remote is enabled after a successful
        start and disabled after a stop, so routed traffic never points at a
        dispatcher that is not running.
        """
        port = port or self.config.dispatcher_port
        message = ""
        if action == "start":
            if self._dispatcher is None or not self._dispatcher.running:
                if _port_open(_LOOPBACK, port):
                    message = (
                        f"port {port} is already in use; a standalone dispatcher "
                        "(charles-mcp-dispatcher) may be running there"
                    )
                else:
                    self._dispatcher = DispatcherThread(
                        DispatcherSettings(
                            mock_dir=str(self.store.root),
                            timeout_seconds=self.config.dispatcher_timeout_seconds,
                        ),
                        _LOOPBACK,
                        port,
                    )
                    self._dispatcher.start()
                    message = "dispatcher started inside the MCP server; it stops with it"
            else:
                message = "dispatcher already running"
            if toggle_map_remote and _port_open(_LOOPBACK, port):
                message += await self._set_map_remote(True)
        elif action == "stop":
            if self._dispatcher is not None and self._dispatcher.running:
                self._dispatcher.stop()
                message = "dispatcher stopped"
                if toggle_map_remote:
                    message += await self._set_map_remote(False)
                else:
                    message += "; routed requests fail until it starts again or Map Remote is off"
            else:
                message = "this MCP server is not running a dispatcher"
        own = self._dispatcher is not None and self._dispatcher.running
        running = own or _port_open(_LOOPBACK, port)
        if action == "verify":
            reachable, message = await self._probe_dispatcher(port)
            checks = [message]
            if reachable:
                for route in self.store.list_routes():
                    for pattern in route.paths:
                        checks.append(await self._probe_through_charles(route, pattern))
            else:
                checks.append(
                    "skipped the end-to-end probe: start the dispatcher first "
                    "(mock_dispatcher action=start)"
                )
            message = ". ".join(checks)
        elif action == "status":
            message = (
                "running inside this MCP server" if own
                else "a dispatcher answers on this port" if running
                else "not running; mapped routes fail until it starts"
            )
        routes = self.store.list_routes()
        return DispatcherStatusResult(
            running=running,
            address=f"http://{_LOOPBACK}:{port}",
            mock_dir=str(self.store.root),
            routes=_route_labels(routes),
            message=message,
        )
