"""Map Local mock tools: capture a response, edit it, let Charles serve it."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from charles_mcp.mocks.service import MockService
from charles_mcp.schemas.mocks import (
    MapLocalToggleResult,
    MockContentResult,
    MockHostSetupResult,
    MockListResult,
    MockRemoveResult,
    MockWriteResult,
)
from charles_mcp.schemas.traffic import CaptureSource

MockHost = Annotated[
    str,
    Field(
        description="Bare hostname without scheme or port.",
        json_schema_extra={"examples": ["api.example.com"]},
    ),
]

MockPath = Annotated[
    str,
    Field(
        description=(
            "Request path starting with `/`, naming a file, e.g. /api/v1/profile. "
            "A query string is accepted but ignored by Map Local."
        ),
        json_schema_extra={"examples": ["/api/v1/profile"]},
    ),
]

JsonPatches = Annotated[
    list[dict[str, Any]] | None,
    Field(
        description=(
            "Edits applied to the captured JSON response, addressed by JSON Pointer: "
            '{"op": "set", "path": "/data/balance", "value": 0} or '
            '{"op": "remove", "path": "/data/banner"}. Use "/items/-" to append and '
            '"/items/[id=42]/status" to pick an array element by a field.'
        ),
    ),
]


def register_mock_tools(mcp: FastMCP, service: MockService) -> None:
    @mcp.tool()
    async def mock_setup_host(host: MockHost, apply: bool = False) -> MockHostSetupResult:
        """One-time setup per host: create the mock directory and the Charles rules
        (Map Local https://<host>/* -> directory, Rewrite text/plain -> JSON type).
        After that, mocks are plain files: present = served by Charles, absent =
        real server. apply=false returns manual steps (UI, no restart); apply=true
        writes the rules into the Charles config and only works while Charles is
        closed (Charles overwrites its config on quit). This tool never quits or starts
        Charles: ask the user to save the session, quit Charles, and start it after the
        write. A config backup is made first."""
        return service.setup_host(host, apply=apply)

    @mcp.tool()
    async def mock_create_from_entry(
        source: CaptureSource,
        entry_id: str,
        capture_id: str | None = None,
        recording_path: str | None = None,
        patches: JsonPatches = None,
    ) -> MockWriteResult:
        """Turn a captured response into a Map Local mock, optionally editing it.
        Take entry_id from query_live_capture_entries (pass capture_id) or a history
        summary (pass recording_path). The mock is written for the entry's host and
        path; a previous mock at that path is archived, never deleted.
        Read `warnings`: Map Local always answers 200 and ignores method and query."""
        return await service.create_from_entry(
            source=source,
            entry_id=entry_id,
            capture_id=capture_id,
            recording_path=recording_path,
            patches=patches,
        )

    @mcp.tool()
    async def mock_write(
        host: MockHost,
        path: MockPath,
        body: Any = None,
        body_text: str | None = None,
    ) -> MockWriteResult:
        """Write a mock response from scratch. Pass `body` as a JSON value (written
        pretty-printed) or `body_text` as raw text, not both. Prefer
        mock_create_from_entry when a real response exists, so the shape stays valid."""
        return service.write(host=host, path=path, body=body, body_text=body_text)

    @mcp.tool()
    async def mock_list(host: str | None = None) -> MockListResult:
        """List active mock files, optionally for one host. Archived versions are
        not listed."""
        return service.list_mocks(host)

    @mcp.tool()
    async def mock_get(host: MockHost, path: MockPath, max_chars: int = 4000) -> MockContentResult:
        """Show the content of one active mock."""
        return service.get(host=host, path=path, max_chars=max_chars)

    @mcp.tool()
    async def mock_remove(host: MockHost, path: MockPath) -> MockRemoveResult:
        """Stop mocking one path: the file moves to the archive and requests pass
        through to the real server again."""
        return service.remove(host=host, path=path)

    @mcp.tool()
    async def mock_set_enabled(enabled: bool) -> MapLocalToggleResult:
        """Turn the whole Charles Map Local tool on or off. Off means every request
        goes to real servers; mock files stay on disk."""
        return await service.set_map_local_enabled(enabled)
