"""Body-aware mock rule tools: one URL, different responses per body field."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from charles_mcp.mocks.rule_service import RuleService
from charles_mcp.schemas.mocks import (
    DispatcherStatusResult,
    RouteSetupResult,
    RuleContentResult,
    RuleListResult,
    RuleRemoveResult,
    RuleSummary,
    RuleWriteResult,
    VariantDiscoveryResult,
)
from charles_mcp.schemas.traffic import CaptureSource

RuleHost = Annotated[
    str,
    Field(
        description='Rule host: "*" (any routed host), a glob such as *.example.com, or a hostname.',
        json_schema_extra={"examples": ["*", "*.example.com", "api.example.com"]},
    ),
]
BodyFields = Annotated[
    list[str] | None,
    Field(
        description=(
            "JSON Pointers into the captured request body whose values the rule must "
            'match, e.g. ["/action"]. JSON and form bodies are supported.'
        ),
    ),
]
Patches = Annotated[
    list[dict[str, Any]] | None,
    Field(
        description=(
            'JSON Pointer edits: {"op": "set", "path": "/data/balance", "value": 0} or '
            '{"op": "remove", "path": "/data/banner"}; "/items/-" appends. Address array '
            'elements by a field, not an index, since servers may reorder them: '
            '"/data/balanceList/[currency=USD]/balance".'
        ),
    ),
]
RequestHeaderEdits = Annotated[
    dict[str, str | None] | None,
    Field(
        description=(
            "Request headers to change before the request is forwarded upstream "
            '(patch mode only): {"X-App-Version": "9.9.9", "X-Debug": null}. A value '
            "sets or replaces the header; null removes it. Host and Content-Length are "
            "managed by the dispatcher and cannot be set."
        ),
    ),
]


def register_mock_rule_tools(mcp: FastMCP, service: RuleService) -> None:
    @mcp.tool()
    async def mock_route_setup(
        host: Annotated[
            str,
            Field(
                description=(
                    "Hostname or domain glob. Use a glob such as *.example.com when the same "
                    "API runs on several hosts (dev, stage, ...)."
                ),
                json_schema_extra={"examples": ["*.example.com", "api.example.com"]},
            ),
        ],
        path: Annotated[
            str,
            Field(
                description="Path pattern: /* for every path, /api/* for a prefix, or one path.",
                json_schema_extra={"examples": ["/*", "/api/*"]},
            ),
        ] = "/*",
        apply: bool = False,
        port: int = 443,
        verify_tls: bool = True,
    ) -> RouteSetupResult:
        """Send a host (or domain glob) through the dispatcher; once per host/domain.
        One Charles Map Remote mapping (https://<host><path> -> local dispatcher,
        empty destination path, preserve host header) covers every path and action
        under it; after that, mocks are only data. Unmocked requests pass through
        unchanged. apply=true writes the mapping into the Charles config; only while
        Charles is closed, because Charles overwrites its config from memory on quit.
        This tool never quits or starts Charles: ask the user to save the session and
        quit Charles, not to reopen it until the write is done, then to start it.
        apply=false returns manual steps that work in the UI without a restart."""
        return service.setup_route(host, path, apply=apply, port=port, verify_tls=verify_tls)

    @mcp.tool()
    async def mock_discover_variants(
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
        """Overview of the whole captured session: API requests grouped by method, host,
        path and the value of a request-body field (default /action; "null" for GETs
        and bodies without it), with counts, sample entry_ids and response statuses.
        Use it right after reading a session so the user can name what to change.
        Filter with host_contains / path_contains / methods. live needs capture_id."""
        return await service.discover_variants(
            source=source,
            capture_id=capture_id,
            recording_path=recording_path,
            host_contains=host_contains,
            path_contains=path_contains,
            body_field=body_field,
            methods=methods,
            limit=limit,
            max_groups=max_groups,
        )

    @mcp.tool()
    async def mock_rule_create_from_entry(
        source: CaptureSource,
        entry_id: str,
        capture_id: str | None = None,
        recording_path: str | None = None,
        match_body_fields: BodyFields = None,
        match_query_fields: list[str] | None = None,
        mode: Literal["patch", "fixture"] = "patch",
        response_patches: Patches = None,
        request_patches: Patches = None,
        request_headers: RequestHeaderEdits = None,
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
        """Create or extend the rule for one request variant (method + path + body/query
        values) from a captured entry.
        match_body_fields picks the variant, e.g. ["/action"] for POSTs; leave it empty
        for GETs. host_scope="any" (default) applies the rule on every routed host, since
        the same API runs on several hosts; "exact" limits it to the entry's host, and
        `host` accepts a glob such as *.example.com. match_path replaces the entry's
        exact path with a pattern where `*` stands for one segment or part of one, for
        segments that differ by platform or app version: /api/*/payoneer covers
        /api/p24-aos2/payoneer and its iOS twin. It must cover the entry's path.
        mode="patch": the real server answers; request_patches edit the request body,
        request_headers set (or remove with null) request headers before it is forwarded,
        response_patches edit only the named response fields. mode="fixture": answer from
        the captured response with any status (e.g. 500), never contacting the server
        (request edits are ignored). status overrides the response code in either mode;
        delay_ms waits that many milliseconds before answering, to test loaders and client
        timeouts. merge=true adds to the variant's existing rule; a patch on the same
        pointer replaces the old one. Patches are checked against the captured entry."""
        return await service.create_rule_from_entry(
            source=source,
            entry_id=entry_id,
            capture_id=capture_id,
            recording_path=recording_path,
            match_body_fields=match_body_fields,
            match_query_fields=match_query_fields,
            mode=mode,
            response_patches=response_patches,
            request_patches=request_patches,
            request_headers=request_headers,
            status=status,
            delay_ms=delay_ms,
            priority=priority,
            host_scope=host_scope,
            host=host,
            match_path=match_path,
            merge=merge,
            rule_id=rule_id,
            description=description,
        )

    @mcp.tool()
    async def mock_rule_write(
        rule: dict[str, Any],
        fixture_json: Any = None,
        fixture_text: str | None = None,
        fixture_base64: str | None = None,
    ) -> RuleWriteResult:
        """Write a rule document directly (advanced; prefer mock_rule_create_from_entry).
        Shape: {"id", "host", "match": {"method", "path", "query", "headers",
        "body": {"/action": "init"}}, "request": {"patches"}, "response": {"mode":
        "fixture"|"patch", "status", "headers", "patches"}, "priority", "enabled"}.
        Fixture rules need one fixture unless it already exists: fixture_text
        keeps the bytes exactly as given (use it to match a captured body),
        fixture_json is written indented, and fixture_base64 carries a binary
        body such as protobuf — set its Content-Type in response.headers."""
        return service.write_rule(
            rule,
            fixture_json=fixture_json,
            fixture_text=fixture_text,
            fixture_base64=fixture_base64,
        )

    @mcp.tool()
    async def mock_rule_list(host: str | None = None) -> RuleListResult:
        """List dispatcher routes and rules, optionally for one host. Invalid rule
        files are reported in `errors` and ignored by the dispatcher."""
        return service.list_rules(host)

    @mcp.tool()
    async def mock_rule_get(host: RuleHost, rule_id: str, max_chars: int = 4000) -> RuleContentResult:
        """Show one rule document and, for fixture rules, the stored fixture."""
        return service.get_rule(host, rule_id, max_chars=max_chars)

    @mcp.tool()
    async def mock_rule_set_enabled(host: RuleHost, rule_id: str, enabled: bool) -> RuleSummary:
        """Enable or disable one rule without deleting it."""
        return service.set_rule_enabled(host, rule_id, enabled)

    @mcp.tool()
    async def mock_rule_remove(host: RuleHost, rule_id: str) -> RuleRemoveResult:
        """Archive one rule (and its fixture); matching requests pass through again."""
        return service.remove_rule(host, rule_id)

    @mcp.tool()
    async def mock_dispatcher(
        action: Literal["start", "stop", "status", "verify"] = "status",
        port: int | None = None,
        toggle_map_remote: bool = True,
    ) -> DispatcherStatusResult:
        """Start, stop, check or verify the local dispatcher that Map Remote points to.
        A dispatcher started here lives inside the MCP server and stops with it; for a
        long-running one use the `charles-mcp-dispatcher` command. By default start also
        enables Charles Map Remote and stop disables it (toggle_map_remote), so routed
        requests never point at a stopped dispatcher; mock files and rules stay.
        action="verify" proves the whole path end to end: it asks the dispatcher directly,
        then sends one harmless probe per route through the Charles proxy and reports
        whether Charles actually routed it. The probes are real requests, so they show
        up in the Charles session, and when Map Remote is off the probe for an
        exact-host route reaches that real server (a GET to /__charles-mcp/health,
        no credentials). Use it after writing a mapping with
        apply=true and starting Charles, because Charles loads Map Remote mappings only
        at startup — a mapping written while it ran, or written before the last start, is
        not active even though the file on disk looks right."""
        return await service.dispatcher(action, port, toggle_map_remote)

    @mcp.tool()
    async def mock_scenario_save(
        name: str,
        rules: list[dict[str, str]] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Save a named set of dispatcher rules to switch on and off together.

        `rules` is a list of {"host", "id"} (host defaults to "*"); omit it to save
        the rules that are enabled right now. A scenario only refers to rules — it
        never copies or deletes them. Name: lower-case letters, digits, . - _."""
        return service.save_scenario(name, rules, description)

    @mcp.tool()
    async def mock_scenario_list() -> dict[str, Any]:
        """List scenarios with their rules, whether each is fully active, and any
        rules it refers to that no longer exist."""
        return service.list_scenarios()

    @mcp.tool()
    async def mock_scenario_apply(
        name: str,
        enabled: bool = True,
        exclusive: bool = True,
    ) -> dict[str, Any]:
        """Switch a scenario on or off in one call.

        enabled=true turns its rules on and, with exclusive=true (default), turns
        every other rule off, so mocks from a previous flow cannot linger.
        enabled=false turns only its own rules off. Takes effect on the next request;
        the dispatcher must be running (mock_dispatcher action=start)."""
        return service.apply_scenario(name, enabled=enabled, exclusive=exclusive)

    @mcp.tool()
    async def mock_scenario_remove(name: str) -> dict[str, Any]:
        """Delete a scenario. Its rules and fixtures are left untouched."""
        return service.remove_scenario(name)
