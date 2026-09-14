"""Create and manage Map Local mocks from captured Charles traffic."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from charles_mcp.config import Config
from charles_mcp.mocks.charles_config import JSON_CONTENT_TYPE, apply_mock_host_rules
from charles_mcp.mocks.json_patch import apply_patches
from charles_mcp.mocks.store import MockRecord, MockStore
from charles_mcp.schemas.mocks import (
    MapLocalRule,
    MapLocalToggleResult,
    MockContentResult,
    MockFile,
    MockHostSetupResult,
    MockListResult,
    MockRemoveResult,
    MockWriteResult,
)
from charles_mcp.schemas.traffic import CaptureSource
from charles_mcp.services.traffic_query_service import TrafficQueryService

MAP_LOCAL_TOOL = "map-local"
# Upper bound for hydrating a captured response body into a mock.
_MAX_SOURCE_BODY_CHARS = 5_000_000

_NEXT_STEP_AFTER_WRITE = (
    "Make the app repeat the request, then confirm with query_live_capture_entries "
    "(path_contains=<path>, response_header_name='X-Charles-Map-Local'): Charles adds "
    "that header to every response it serves from a mock file. Requires the host to be "
    "mapped once (mock_setup_host) and Map Local to be enabled (mock_set_enabled)."
)


def _to_file(record: MockRecord) -> MockFile:
    return MockFile(
        host=record.host,
        path=record.path,
        file=record.file,
        size_bytes=record.size_bytes,
        modified_at=record.modified_at,
    )


def render_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _manual_setup_steps(rule: MapLocalRule) -> list[str]:
    return [
        "In Charles open Tools > Map Local and tick Enable Map Local.",
        (
            f"Add a mapping: Protocol {rule.protocol}, Host {rule.host}, "
            f"Port {rule.port}, Path {rule.path}; Local path {rule.local_path}."
        ),
        (
            "Open Tools > Rewrite, tick Enable Rewrite, add a set for the same host "
            "with a rule: Type Modify Header, Where Response, match header "
            "Content-Type value text/plain, replace with header Content-Type value "
            f"{JSON_CONTENT_TYPE}. Without it mocks without a file extension are "
            "served as text/plain."
        ),
        (
            "Make sure SSL Proxying is enabled for this host, otherwise Charles "
            "cannot map HTTPS requests."
        ),
        (
            "Alternatively quit Charles and call mock_setup_host with apply=true to "
            "write both rules into the Charles config automatically."
        ),
    ]


class MockService:
    def __init__(
        self,
        config: Config,
        *,
        traffic_query_service: TrafficQueryService,
        client_factory: Any,
        store: MockStore | None = None,
    ) -> None:
        self.config = config
        self.traffic_query_service = traffic_query_service
        self.client_factory = client_factory
        self.store = store or MockStore(config.mock_dir)

    def setup_host(self, host: str, *, apply: bool = False) -> MockHostSetupResult:
        host_dir = self.store.ensure_host_dir(host)
        rule = MapLocalRule(host=host_dir.name, local_path=str(host_dir))
        result = MockHostSetupResult(
            host=host_dir.name,
            host_dir=str(host_dir),
            map_local_rule=rule,
        )
        if not apply:
            result.instructions = _manual_setup_steps(rule)
            return result

        if not self.config.config_path:
            raise ValueError(
                "Charles config file was not found; set CHARLES_CONFIG_PATH or run "
                "mock_setup_host with apply=false and follow the manual steps"
            )
        if _port_open(self.config.proxy_host, self.config.proxy_port):
            raise ValueError(
                "Charles is running. It rewrites its config file on quit, so edits made "
                "now would be lost. Ask the user to quit Charles (or save the session "
                "first), then call mock_setup_host again with apply=true."
            )

        change = apply_mock_host_rules(
            self.config.config_path,
            host=rule.host,
            dest_dir=rule.local_path,
            backup_dir=Path(self.config.state_dir) / "charles-config-backups" / "mock-setup",
            protocol=rule.protocol,
            port=rule.port,
        )
        result.applied = True
        result.config_path = change.config_path
        result.config_backup = change.backup_path
        result.map_local_added = change.map_local_added
        result.rewrite_added = change.rewrite_added
        result.warnings = change.warnings
        result.instructions = [
            "Start Charles; the Map Local and Rewrite rules for this host are active.",
            (
                "Make sure SSL Proxying is enabled for this host, otherwise Charles "
                "cannot map HTTPS requests."
            ),
        ]
        return result

    async def create_from_entry(
        self,
        *,
        source: CaptureSource,
        entry_id: str,
        capture_id: str | None = None,
        recording_path: str | None = None,
        patches: list[dict[str, Any]] | None = None,
    ) -> MockWriteResult:
        detail_result = await self.traffic_query_service.get_detail(
            source=source,
            entry_id=entry_id,
            capture_id=capture_id,
            recording_path=recording_path,
            include_full_body=True,
            max_body_chars=_MAX_SOURCE_BODY_CHARS,
        )
        entry = detail_result.detail.entry
        if not entry.host or not entry.path:
            raise ValueError(f"entry `{entry_id}` has no host/path to map")

        body = entry.response.body
        if body.full_text is None:
            raise ValueError(
                f"entry `{entry_id}` has no usable response body (kind={body.kind}); "
                "write the mock explicitly with mock_write"
            )
        if body.full_text_truncated:
            raise ValueError(f"entry `{entry_id}` response body is too large to mock")

        patches = patches or []
        if body.kind == "json":
            document = apply_patches(json.loads(body.full_text), patches)
            content = render_json(document)
        elif patches:
            raise ValueError(
                f"entry `{entry_id}` response is `{body.kind}`, not JSON; patches need JSON"
            )
        else:
            content = body.full_text

        warnings: list[str] = []
        method = (entry.method or "").upper()
        if method and method not in {"GET", "HEAD"}:
            warnings.append(
                f"Map Local ignores the HTTP method: every method on {entry.path} "
                f"gets this file, not only {method}."
            )
        if entry.response_status and entry.response_status != 200:
            warnings.append(
                f"Original response status was {entry.response_status}; "
                "Map Local always answers 200."
            )

        request_path = entry.path if not entry.query else f"{entry.path}?{entry.query}"
        return self._write(
            entry.host,
            request_path,
            content,
            warnings=warnings,
            source_entry_id=entry_id,
            patches_applied=len(patches),
        )

    def write(
        self,
        *,
        host: str,
        path: str,
        body: Any = None,
        body_text: str | None = None,
    ) -> MockWriteResult:
        if (body is None) == (body_text is None):
            raise ValueError("pass exactly one of `body` (JSON value) or `body_text`")
        content = render_json(body) if body_text is None else body_text
        return self._write(host, path, content, warnings=[])

    def list_mocks(self, host: str | None = None) -> MockListResult:
        items = [_to_file(record) for record in self.store.list_mocks(host)]
        return MockListResult(mock_dir=str(self.store.root), total=len(items), items=items)

    def get(self, *, host: str, path: str, max_chars: int = 4000) -> MockContentResult:
        record, content = self.store.read(host, path)
        truncated = len(content) > max_chars
        return MockContentResult(
            mock=_to_file(record),
            content=content[:max_chars] if truncated else content,
            content_truncated=truncated,
        )

    def remove(self, *, host: str, path: str) -> MockRemoveResult:
        location = self.store.locate(host, path)
        archived = self.store.remove(host, path)
        return MockRemoveResult(
            host=location.host,
            path=location.path,
            archived_to=archived,
            next_step="Requests to this path now pass through to the real server.",
        )

    async def set_map_local_enabled(self, enabled: bool) -> MapLocalToggleResult:
        async with self.client_factory(self.config) as client:
            success = await client.set_tool_enabled(MAP_LOCAL_TOOL, enabled)
        state = "enabled" if enabled else "disabled"
        return MapLocalToggleResult(
            enabled=enabled,
            success=success,
            message=(
                f"Map Local {state}."
                if success
                else f"Charles did not accept the request; Map Local may not be {state}."
            ),
        )

    def _write(
        self,
        host: str,
        path: str,
        content: str,
        *,
        warnings: list[str],
        source_entry_id: str | None = None,
        patches_applied: int = 0,
    ) -> MockWriteResult:
        location = self.store.locate(host, path)
        if location.query_ignored:
            warnings = [
                *warnings,
                f"Query string `{location.query_ignored}` is ignored by Map Local: every "
                f"query variant of {location.path} gets this file.",
            ]
        if not self.store.host_dir(location.host).is_dir():
            warnings = [
                *warnings,
                f"{location.host} was not set up yet; run mock_setup_host once so Charles "
                "maps it to the mock directory.",
            ]
        record, archived = self.store.write(host, path, content)
        return MockWriteResult(
            mock=_to_file(record),
            archived_previous=archived,
            source_entry_id=source_entry_id,
            patches_applied=patches_applied,
            warnings=warnings,
            next_step=_NEXT_STEP_AFTER_WRITE,
        )
