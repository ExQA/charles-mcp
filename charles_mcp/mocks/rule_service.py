"""Create and manage body-aware dispatcher rules from captured Charles traffic."""

from __future__ import annotations

import asyncio
import base64
import binascii
import html
import json
import logging
import re
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
from charles_mcp.mocks.scenarios import (
    Scenario,
    ScenarioRuleRef,
    ScenarioStore,
    validate_scenario_name,
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
from charles_mcp.schemas.traffic import CaptureSource, ResourceClassification, TrafficEntry
from charles_mcp.services.traffic_query_orchestrator import READ_ALL, keep_newest
from charles_mcp.services.traffic_query_service import TrafficQueryService

logger = logging.getLogger(__name__)

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


_CHARLES_EXCEPTION = re.compile(
    r"The actual exception reported was:\s*([\w.$]+)(?::\s*(.*?))?\s*(?:Charles Proxy,|$)", re.S
)


def parse_charles_error_page(page: str) -> tuple[str, str] | None:
    """The Java exception Charles names on its error page, as (class, detail).

    Charles answers a request it cannot forward with a 503 page that ends with
    "The actual exception reported was: java.net.UnknownHostException: <name>:
    ...". That line says which host Charles really tried to reach, which is the
    difference between "the mapping did not apply" and "the mapping points
    somewhere that does not exist".
    """
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    match = _CHARLES_EXCEPTION.search(re.sub(r"\s+", " ", text))
    if match is None:
        return None
    return match.group(1), (match.group(2) or "").strip()


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
    different elements once the server reorders the list. Patches that certainly
    address different elements (``[id=1]`` and ``[id=2]``) are both kept. Returns
    the merged patches and the pointers dropped as aliases (for a warning)."""
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
        self.scenarios = ScenarioStore(self.store.root)
        self._dispatcher: DispatcherThread | None = None
        # True while Map Remote is on in Charles because this server turned it on:
        # it was off before, and nobody has switched it off since.
        self._map_remote_is_ours = False

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
            backup_dir=Path(self.config.backup_dir) / "map-remote",
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
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Every entry of the capture (or recording), from one export; never paged.

        The caller decides which entries to keep: cutting here would keep the
        oldest ones, and the flow the user has just run is at the other end.
        """
        if source == "live":
            if not capture_id:
                raise ValueError("capture_id is required for source='live'")
            read = await self.traffic_query_service.live_service.read(
                capture_id, cursor=0, limit=READ_ALL, advance=False
            )
            return list(read.items), None
        history = self.traffic_query_service.history_service
        if recording_path:
            return await history.get_snapshot(recording_path), recording_path
        path, raw = await history.load_latest_with_path()
        return raw, path

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
        limit: int = 2000,
        max_groups: int = 200,
    ) -> VariantDiscoveryResult:
        """Group captured API requests by method, host, path and a body field value.

        Reads the capture once and parses full request bodies, so long sessions cost
        one Charles export instead of one per page or per truncated preview.
        """
        if limit <= 0:
            raise ValueError("limit must be greater than 0")
        raw_entries, identity = await self._raw_entries(source, capture_id, recording_path)
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
        # Cheap pass over the whole capture first: noise classes and the
        # method/host/path filters need only routing fields. The limit then
        # applies to what is left, newest first, so a long session cannot push
        # the flow the user has just run out of the overview.
        wanted: list[tuple[dict[str, Any], ResourceClassification, str, str, str]] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
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
            wanted.append((raw, classification, method, host, path))
        if len(wanted) > limit:
            result.warnings.append(
                f"only the newest {limit} of {len(wanted)} matching entries read; narrow with "
                "host_contains/path_contains/methods or raise limit"
            )
            wanted = keep_newest(wanted, limit, raw_of=lambda item: item[0])
        for raw, classification, method, host, path in wanted:
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
        priority: int | None = None,
        host_scope: Literal["any", "exact"] = "any",
        host: str | None = None,
        match_path: str | None = None,
        merge: bool = True,
        rule_id: str | None = None,
        description: str | None = None,
    ) -> RuleWriteResult:
        """Create the rule for one request variant, or extend the one it already has.

        With ``merge`` an existing rule keeps everything this call does not set:
        whether it is enabled, its priority, its header conditions and response
        headers. Only ``merge=False`` starts the rule from scratch.
        """
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
        same_mode = False
        if merge and self.store.rule_path(rule_host, new_id).is_file():
            previous = self.store.get_rule(rule_host, new_id)
            same_mode = previous.response.mode == mode
            if same_mode:
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
                        f"dropped the previous patch `{pointer}`: a new patch may address the "
                        "same array element another way (a position against a [field=value] "
                        "selector, or selectors on different fields), and keeping both could "
                        "edit the wrong one"
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
        if mode == "fixture" and response_body.full_text is None and response_body.source_bytes:
            # A binary response (protobuf, an image): there is no text form, so
            # the fixture is the decoded payload, served byte for byte.
            if response_patches:
                raise ValueError(
                    f"entry `{entry_id}` has a binary response, and patches edit JSON or form "
                    "bodies only: create the fixture without response_patches"
                )
            fixture = response_body.source_bytes
            headers["Content-Type"] = (
                entry.response.mime_type or response_body.mime_type or "application/octet-stream"
            )
            status = status or entry.response_status or 200
        elif mode == "fixture":
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
        if mode == "fixture" and (request_patches or request_header_edits):
            warnings.append(
                "request patches and header edits are ignored in fixture mode: "
                "nothing goes upstream"
            )

        # An update keeps what this call does not set. Rebuilding the rule from
        # the arguments alone used to switch a disabled rule back on and reset
        # its priority, so a rule a scenario had turned off came back by itself.
        kept: dict[str, Any] = {}
        match_headers: dict[str, str] = {}
        if previous is not None:
            kept = {"enabled": previous.enabled, "created_at": previous.created_at}
            match_headers = dict(previous.match.headers)
            if priority is None:
                priority = previous.priority
            if same_mode:
                headers = {**previous.response.headers, **headers}
            if not previous.enabled:
                warnings.append(
                    f"rule `{new_id}` is disabled and stays disabled: the change is stored but "
                    f"does not apply until mock_rule_set_enabled(host=\"{rule_host}\", "
                    f"rule_id=\"{new_id}\", enabled=true)"
                )

        rule = MockRule(
            id=new_id,
            host=rule_host,
            priority=priority or 0,
            description=description or (previous.description if previous else None),
            match=RuleMatch(
                method=method,
                path=path,
                query=query_match,
                headers=match_headers,
                body=body_match,
            ),
            request=RuleRequest(patches=request_patches, headers=request_header_edits),
            response=RuleResponse(
                mode=mode,
                status=status,
                headers=headers,
                delay_ms=delay_ms,
                patches=response_patches,
            ),
            source_entry_id=entry_id,
            **kept,
        )
        return self._save(rule, fixture, warnings)

    def write_rule(
        self,
        rule: dict[str, Any],
        *,
        fixture_json: Any = None,
        fixture_text: str | None = None,
        fixture_base64: str | None = None,
    ) -> RuleWriteResult:
        given = [
            name
            for name, value in (
                ("fixture_json", fixture_json),
                ("fixture_text", fixture_text),
                ("fixture_base64", fixture_base64),
            )
            if value is not None
        ]
        if len(given) > 1:
            raise ValueError(f"pass at most one fixture, got {', '.join(given)}")
        parsed = MockRule.model_validate(rule)
        fixture: bytes | None = None
        if fixture_json is not None:
            fixture = (json.dumps(fixture_json, ensure_ascii=False, indent=2) + "\n").encode()
        elif fixture_text is not None:
            fixture = fixture_text.encode("utf-8")
        elif fixture_base64 is not None:
            try:
                fixture = base64.b64decode(fixture_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError(f"fixture_base64 is not valid base64: {exc}") from exc
        if (
            parsed.response.mode == "fixture"
            and fixture is None
            and not self.store.fixture_path(parsed.host, parsed.id).is_file()
        ):
            raise ValueError("fixture rules need fixture_json, fixture_text or fixture_base64")
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

    # ---- scenarios ----------------------------------------------------------

    def _rule_exists(self, ref: ScenarioRuleRef) -> bool:
        try:
            self.store.get_rule(ref.host, ref.id)
        except (FileNotFoundError, ValueError):
            return False
        return True

    def save_scenario(
        self,
        name: str,
        rules: list[dict[str, str]] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Name a set of rules; without `rules`, the rules enabled right now."""
        validate_scenario_name(name)  # before anything else: a bad name is the first error
        if rules is None:
            enabled, _errors = self.store.all_rules()
            refs = [ScenarioRuleRef(host=rule.host, id=rule.id) for rule in enabled if rule.enabled]
            if not refs:
                raise ValueError(
                    "no rules are enabled, so there is nothing to save: pass `rules` or enable "
                    "the scenario's rules first"
                )
        else:
            refs = [ScenarioRuleRef.model_validate(item) for item in rules]
        missing = [ref.label for ref in refs if not self._rule_exists(ref)]
        if missing:
            raise ValueError(f"these rules do not exist: {', '.join(missing)}")
        scenario = Scenario(name=name, description=description, rules=refs)
        path = self.scenarios.save(scenario)
        return {
            "name": scenario.name,
            "path": str(path),
            "rules": [ref.label for ref in refs],
            "next_step": f"Switch to it with mock_scenario_apply(name={scenario.name!r}).",
        }

    def list_scenarios(self) -> dict[str, Any]:
        scenarios, errors = self.scenarios.list()
        items = []
        for scenario in scenarios:
            # None marks a rule the scenario names but that no longer exists.
            states: list[tuple[str, bool | None]] = []
            for ref in scenario.rules:
                try:
                    states.append((ref.label, self.store.get_rule(ref.host, ref.id).enabled))
                except (FileNotFoundError, ValueError):
                    states.append((ref.label, None))
            missing = [label for label, state in states if state is None]
            items.append(
                {
                    "name": scenario.name,
                    "description": scenario.description,
                    "rules": [label for label, _ in states],
                    "active": not missing and all(state for _, state in states),
                    "missing_rules": missing,
                }
            )
        return {"total": len(items), "items": items, "errors": errors}

    def apply_scenario(
        self, name: str, *, enabled: bool = True, exclusive: bool = True
    ) -> dict[str, Any]:
        """Switch a scenario's rules on (and, if exclusive, every other rule off), or off."""
        scenario = self.scenarios.get(name)
        wanted = {(ref.host, ref.id) for ref in scenario.rules}
        switched_on: list[str] = []
        switched_off: list[str] = []
        missing: list[str] = []

        for ref in scenario.rules:
            try:
                rule = self.store.get_rule(ref.host, ref.id)
            except (FileNotFoundError, ValueError):
                missing.append(ref.label)
                continue
            if rule.enabled != enabled:
                self.set_rule_enabled(ref.host, ref.id, enabled)
                (switched_on if enabled else switched_off).append(ref.label)

        if enabled and exclusive:
            # Another flow's mocks left on are the classic "why is this mocked?".
            others, _errors = self.store.all_rules()
            for rule in others:
                if rule.enabled and (rule.host, rule.id) not in wanted:
                    self.set_rule_enabled(rule.host, rule.id, False)
                    switched_off.append(f"{rule.host}/{rule.id}")

        if enabled:
            next_step = (
                "Rules take effect on the next request. The dispatcher must be running "
                "(mock_dispatcher action=start); mock_dispatcher action=verify proves the path."
            )
        else:
            next_step = "Matching requests now pass through to the real servers."
        return {
            "name": scenario.name,
            "enabled": enabled,
            "switched_on": switched_on,
            "switched_off": switched_off,
            "missing_rules": missing,
            "next_step": next_step,
        }

    def remove_scenario(self, name: str) -> dict[str, Any]:
        removed = self.scenarios.remove(name)
        return {
            "name": name,
            "removed": removed,
            "note": "Only the scenario was removed; its rules and fixtures are untouched.",
        }

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

    async def _set_map_remote(self, enabled: bool) -> tuple[bool, str]:
        """Switch Map Remote in Charles: whether it worked, and what to tell the caller.

        Map Remote is one switch for all of Charles. Switching it off also stops
        mappings that have nothing to do with this server, so whether this
        server may do that on its own is decided by the callers, not here.
        """
        if self.client_factory is None:
            return False, ""
        try:
            async with self.client_factory(self.config) as client:
                ok = await client.set_tool_enabled("map-remote", enabled)
        except Exception as exc:  # Charles down or web interface off
            action = "enable" if enabled else "disable"
            return False, f"; could not {action} Map Remote in Charles: {exc}"
        if ok and not enabled:
            self._map_remote_is_ours = False
        state = "enabled" if enabled else "disabled"
        if not ok:
            return False, f"; Charles refused to set Map Remote {state}"
        return True, f"; Map Remote {state} in Charles"

    async def _enable_map_remote(self) -> str:
        """Switch Map Remote on for the dispatcher, remembering whether that changed anything.

        Only a switch this server turned on is its to turn off again when it
        exits. If Map Remote was on already — for the user's own mappings, or
        because another server instance is serving these routes — it is left
        as it was found.
        """
        was_enabled = await self._map_remote_enabled()
        ok, note = await self._set_map_remote(True)
        if not ok or self._map_remote_is_ours:
            return note
        if was_enabled is False:
            self._map_remote_is_ours = True
            return note
        reason = "it was already on" if was_enabled else "its earlier state could not be read"
        return f"{note} ({reason}, so this server leaves it on when it exits)"

    async def _map_remote_enabled(self) -> bool | None:
        """Whether Map Remote is on in Charles; None when Charles cannot say."""
        if self.client_factory is None:
            return None
        try:
            async with self.client_factory(self.config) as client:
                reader = getattr(client, "get_tool_enabled", None)
                return await reader("map-remote") if reader is not None else None
        except Exception:  # Charles down or web interface off
            return None

    async def routing_warnings(self) -> list[str]:
        """Say so when routed traffic is being sent to a dispatcher that is not there.

        Map Remote stays on in Charles after the dispatcher is gone — a client
        that was killed, a session that ended without mock_dispatcher stop. From
        then on every request under a route fails in the app, and nothing in a
        capture explains why.
        """
        routes = self.store.list_routes()
        port = self.config.dispatcher_port
        if not routes or _port_open(_LOOPBACK, port):
            return []
        if await self._map_remote_enabled() is not True:
            return []
        return [
            f"map_remote_without_dispatcher: Map Remote is ON in Charles but nothing answers on "
            f"the configured dispatcher port {port} (CHARLES_DISPATCHER_PORT), so requests to "
            f"{', '.join(_route_labels(routes))} fail in the app right now. Start the dispatcher "
            "with mock_dispatcher(action=\"start\") or turn Map Remote off with "
            "mock_dispatcher(action=\"stop\"). Only this port is checked: if a dispatcher was "
            "started on another one (charles-mcp-dispatcher --port N), routing works and this "
            "warning does not apply. Switching Map Remote off stops every Map Remote mapping "
            "in Charles, the user's own included."
        ]

    async def shutdown(self) -> None:
        """Leave Charles usable when the MCP server goes away.

        The dispatcher lives inside the server process and dies with it. If this
        server switched Map Remote on, it switches it off again; otherwise Charles
        would keep sending the routed traffic to a port nobody listens on. A
        Map Remote that was on before this server touched it is left on.
        """
        dispatcher = self._dispatcher
        if dispatcher is not None and dispatcher.running:
            dispatcher.stop()
            logger.info("dispatcher stopped with the MCP server")
        if self._map_remote_is_ours:
            _, outcome = await self._set_map_remote(False)
            logger.info("MCP server shutting down%s", outcome)

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

    async def _refused_tunnel_reason(self, host: str) -> tuple[str, str] | None:
        """Ask Charles for a tunnel to ``host`` and read why it says no.

        httpx reports a refused CONNECT as a bare ProxyError and drops the page
        Charles sent with it, so the page is fetched here with a plain socket.
        None when Charles accepts the tunnel or cannot be asked.
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.config.proxy_host, self.config.proxy_port),
                timeout=3.0,
            )
        except (OSError, asyncio.TimeoutError):
            return None
        try:
            target = f"{host}:443"
            writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode("ascii"))
            await writer.drain()
            data = b""
            while len(data) < 16384:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=8.0)
                if not chunk:
                    break
                data += chunk
                status_line = data.split(b"\r\n", 1)[0]
                if b"\r\n" in data and b" 200" in status_line:
                    return None  # the tunnel was granted; nothing to explain
        except (OSError, asyncio.TimeoutError, UnicodeError):
            return None
        finally:
            writer.close()
        return parse_charles_error_page(data.decode("latin-1"))

    def _explain_refused_probe(
        self, label: str, host: str, reason: tuple[str, str] | None
    ) -> str | None:
        if reason is None:
            return None
        exception, detail = reason
        port = self.config.dispatcher_port
        if exception.endswith("UnknownHostException"):
            # "<name>: nodename nor servname provided, or not known"
            name = detail.rsplit(": ", 1)[0].strip() if ": " in detail else detail
            if name.lower() == host.lower():
                return (
                    f"{label}: Charles tried to reach `{host}` itself and could not resolve it, "
                    "so no Map Remote mapping applied to the probe. Map Remote is off, or the "
                    "mapping is not loaded because Charles was not started after the config was "
                    "written"
                )
            return (
                f"{label}: Charles applied a Map Remote mapping, but its destination `{name}` is "
                "not a host name, so every request under this route fails with 503. In Charles "
                "open Tools > Map Remote, edit the mapping and set Map To: Protocol http, Host "
                f"127.0.0.1, Port {port}, Path empty. A whole URL typed into the Host field "
                "causes exactly this"
            )
        if exception.endswith("ConnectException"):
            return (
                f"{label}: Charles applied a Map Remote mapping, but nothing accepted the "
                f"connection at its destination ({detail}). The mapping must point to "
                f"http://127.0.0.1:{port}, where this dispatcher listens"
            )
        return f"{label}: Charles refused the probe ({exception}: {detail})"

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
            # Charles refused the tunnel. Its error page names the host it really
            # tried, which tells a mapping that did not apply from one that points
            # at a destination that does not exist.
            explained = self._explain_refused_probe(
                f"{route.host}{path_pattern}", host, await self._refused_tunnel_reason(host)
            )
            if explained is not None:
                return explained
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
        # The real server answered. The likeliest cause is that Map Remote is off
        # or this mapping is disabled — QA saw exactly that reported as "the
        # mapping does not cover this path", which sent people editing a
        # mapping that was fine.
        return (
            f"{route.host}{path_pattern}: the probe reached the real server (HTTP "
            f"{response.status_code}) instead of the dispatcher. Map Remote is off, this "
            "mapping is disabled in Charles, or it does not cover this path"
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
                message += await self._enable_map_remote()
        elif action == "stop":
            if self._dispatcher is not None and self._dispatcher.running:
                self._dispatcher.stop()
                message = "dispatcher stopped"
                if toggle_map_remote:
                    message += (await self._set_map_remote(False))[1]
                else:
                    message += "; routed requests fail until it starts again or Map Remote is off"
            else:
                # Still worth switching Map Remote off: it may have been left on
                # by a server that is gone, and then routed requests fail. Only
                # this port can be checked for a dispatcher that is still alive.
                message = "this MCP server is not running a dispatcher"
                if toggle_map_remote and not _port_open(_LOOPBACK, port):
                    message += (await self._set_map_remote(False))[1]
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
            if own:
                message = "running inside this MCP server"
            elif running:
                message = "a dispatcher answers on this port"
            else:
                warnings = await self.routing_warnings()
                message = warnings[0] if warnings else (
                    "not running; if Map Remote is on in Charles, mapped routes fail until it starts"
                )
        routes = self.store.list_routes()
        return DispatcherStatusResult(
            running=running,
            address=f"http://{_LOOPBACK}:{port}",
            mock_dir=str(self.store.root),
            routes=_route_labels(routes),
            message=message,
        )
