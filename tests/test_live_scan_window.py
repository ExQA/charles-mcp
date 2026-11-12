"""A capture longer than the scan limit must still show its newest traffic.

`start_live_capture` adopts the whole Charles session, so a capture of several
hundred entries is the normal case, not the edge case. These tests pin what the
analysis and mock tools do once the capture outgrows their limits.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import charles_mcp.server as server_module
from charles_mcp.config import Config
from charles_mcp.server import create_server

NOW = datetime.now(timezone.utc)
OLD = NOW - timedelta(minutes=30)


def _tool_result(call_result):
    payload = call_result[1]
    return payload["result"] if isinstance(payload, dict) and "result" in payload else payload


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{moment.microsecond // 1000:03d}+00:00"
    )


def _api_entry(
    path: str,
    *,
    start: datetime,
    host: str = "api.example.com",
    action: str = "list",
) -> dict:
    return {
        "status": "COMPLETE",
        "method": "POST",
        "scheme": "https",
        "host": host,
        "path": path,
        "query": None,
        "times": {"start": _iso(start), "end": _iso(start)},
        "durations": {"total": 18},
        "totalSize": 1200,
        "request": {
            "mimeType": "application/json",
            "header": {
                "firstLine": f"POST {path} HTTP/1.1",
                "headers": [{"name": "Content-Type", "value": "application/json"}],
            },
            "sizes": {"body": 32},
            "body": {"text": json.dumps({"action": action})},
        },
        "response": {
            "status": 200,
            "mimeType": "application/json",
            "header": {
                "firstLine": "HTTP/1.1 200 OK",
                "headers": [{"name": "Content-Type", "value": "application/json"}],
            },
            "sizes": {"body": 40},
            "body": {"text": json.dumps({"data": {"balance": "10.00"}, "action": action})},
        },
    }


def _image_entry(path: str, *, start: datetime) -> dict:
    return {
        "status": "COMPLETE",
        "method": "GET",
        "scheme": "https",
        "host": "cdn.example.com",
        "path": path,
        "query": None,
        "times": {"start": _iso(start), "end": _iso(start)},
        "durations": {"total": 5},
        "totalSize": 5000,
        "request": {"header": {"firstLine": f"GET {path} HTTP/1.1", "headers": []}},
        "response": {
            "status": 200,
            "mimeType": "image/png",
            "header": {
                "firstLine": "HTTP/1.1 200 OK",
                "headers": [{"name": "Content-Type", "value": "image/png"}],
            },
            "sizes": {"body": 4000},
        },
    }


def _tunnel_entry(host: str, *, start: datetime) -> dict:
    """An HTTPS connection Charles could not read: SSL Proxying is off for the host."""
    return {
        "status": "COMPLETE",
        "method": "CONNECT",
        "scheme": "https",
        "host": host,
        "path": None,
        "query": None,
        "times": {"start": _iso(start), "end": _iso(start)},
        "totalSize": 4096,
        "request": {"header": {"firstLine": f"CONNECT {host}:443 HTTP/1.1", "headers": []}},
        "response": {"status": 200, "header": {"firstLine": "HTTP/1.1 200 OK", "headers": []}},
    }


def _old_entries(count: int, *, path: str = "/api/old") -> list[dict]:
    """Entries half an hour old, in the start order Charles exports them in."""
    return [
        _api_entry(f"{path}/{index}", start=OLD + timedelta(milliseconds=index))
        for index in range(count)
    ]


def _fake_client_class() -> type:
    class FakeClient:
        current_export: list[dict] = []

        def __init__(self, config):
            self.config = config

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def connect(self):
            pass

        async def close(self):
            pass

        async def export_session_json(self) -> list[dict]:
            return deepcopy(type(self).current_export)

        async def start_recording(self) -> bool:
            return True

        async def stop_recording(self) -> bool:
            return True

        async def clear_session(self) -> bool:
            return True

        async def set_tool_enabled(self, tool: str, enabled: bool) -> bool:
            return True

    return FakeClient


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake_client = _fake_client_class()
    monkeypatch.setattr(server_module, "CharlesClient", fake_client)
    config = Config(mock_dir=str(tmp_path / "mocks"))
    # Never the real Charles config, state directory or mock directory in tests.
    config.config_path = None
    config.backup_dir = str(tmp_path / "backup")
    config.state_dir = str(tmp_path / "state")
    config.package_dir = str(tmp_path / "package")
    return create_server(config=config), fake_client


async def _capture(server, fake_client, entries: list[dict]) -> str:
    fake_client.current_export = entries
    started = _tool_result(await server.call_tool("start_live_capture", {}))
    return started["capture_id"]


async def _query(server, capture_id: str, **arguments) -> dict:
    return _tool_result(
        await server.call_tool(
            "query_live_capture_entries", {"capture_id": capture_id, **arguments}
        )
    )


@pytest.mark.asyncio
async def test_since_seconds_finds_recent_entries_behind_600_older_ones(env) -> None:
    server, fake_client = env
    recent = [_api_entry(f"/api/recent/{i}", start=NOW - timedelta(seconds=5)) for i in range(3)]
    capture_id = await _capture(server, fake_client, _old_entries(600) + recent)

    result = await _query(server, capture_id, since_seconds=60)

    assert result["matched_count"] == 3
    assert sorted(item["path"] for item in result["items"]) == [
        "/api/recent/0",
        "/api/recent/1",
        "/api/recent/2",
    ]
    assert result["total_items"] == 603
    assert "scan_limit_reached" not in result["warnings"]


@pytest.mark.asyncio
async def test_the_newest_entries_win_when_the_scan_limit_is_reached(env) -> None:
    server, fake_client = env
    newest = _api_entry("/api/just-now", start=NOW - timedelta(seconds=2))
    capture_id = await _capture(server, fake_client, _old_entries(600) + [newest])

    result = await _query(server, capture_id, scan_limit=100, max_items=200)

    paths = {item["path"] for item in result["items"]}
    assert "/api/just-now" in paths
    assert "/api/old/0" not in paths
    assert result["matched_count"] == 100
    assert result["truncated"] is True
    assert "scan_limit_reached" in result["warnings"]
    assert any("newest 100 of 601" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_a_host_filter_reaches_an_old_entry_behind_newer_traffic(env) -> None:
    server, fake_client = env
    payment = _api_entry("/pay/confirm", start=OLD - timedelta(minutes=5), host="pay.example.com")
    capture_id = await _capture(server, fake_client, [payment] + _old_entries(600))

    result = await _query(server, capture_id, host_contains="pay.example")

    assert [item["path"] for item in result["items"]] == ["/pay/confirm"]
    assert result["truncated"] is False
    assert "scan_limit_reached" not in result["warnings"]


@pytest.mark.asyncio
async def test_preset_noise_does_not_use_up_the_scan_limit(env) -> None:
    server, fake_client = env
    images = [
        _image_entry(f"/assets/{index}.png", start=OLD + timedelta(seconds=index))
        for index in range(600)
    ]
    first = _api_entry("/api/bootstrap", start=OLD - timedelta(minutes=1))
    capture_id = await _capture(server, fake_client, [first] + images)

    result = await _query(server, capture_id)

    assert [item["path"] for item in result["items"]] == ["/api/bootstrap"]
    assert result["scanned_count"] == 601
    assert result["filtered_out_count"] == 600
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_group_and_stats_cover_the_whole_capture(env) -> None:
    server, fake_client = env
    recent = [
        _api_entry("/pay/confirm", start=NOW - timedelta(seconds=3), host="pay.example.com")
    ]
    capture_id = await _capture(server, fake_client, _old_entries(600) + recent)

    groups = _tool_result(
        await server.call_tool(
            "group_capture_analysis",
            {"source": "live", "capture_id": capture_id, "group_by": "host", "scan_limit": 5000},
        )
    )
    stats = _tool_result(
        await server.call_tool(
            "get_capture_analysis_stats", {"source": "live", "capture_id": capture_id}
        )
    )

    assert {group["group_value"]: group["count"] for group in groups["groups"]} == {
        "api.example.com": 600,
        "pay.example.com": 1,
    }
    assert stats["scanned_count"] == 601
    assert stats["classified_counts"] == {"api_candidate": 601}


@pytest.mark.asyncio
async def test_next_cursor_separates_what_was_seen_from_what_came_later(env) -> None:
    server, fake_client = env
    capture_id = await _capture(server, fake_client, _old_entries(600))

    first = await _query(server, capture_id, scan_limit=100)
    assert first["next_cursor"] == 600

    later = _api_entry("/api/after", start=NOW)
    fake_client.current_export = _old_entries(600) + [later]
    second = await _query(server, capture_id, cursor=first["next_cursor"])

    assert second["total_items"] == 1
    assert [item["path"] for item in second["items"]] == ["/api/after"]


@pytest.mark.asyncio
async def test_detail_with_full_body_reaches_an_entry_beyond_position_500(env) -> None:
    server, fake_client = env
    newest = _api_entry("/api/just-now", start=NOW - timedelta(seconds=2), action="fresh")
    capture_id = await _capture(server, fake_client, _old_entries(600) + [newest])
    found = await _query(server, capture_id, path_contains="/api/just-now")
    entry_id = found["items"][0]["entry_id"]

    detail = _tool_result(
        await server.call_tool(
            "get_traffic_entry_detail",
            {
                "source": "live",
                "entry_id": entry_id,
                "capture_id": capture_id,
                "include_full_body": True,
            },
        )
    )

    entry = detail["detail"]["entry"]
    assert entry["path"] == "/api/just-now"
    assert json.loads(entry["request"]["body"]["full_text"]) == {"action": "fresh"}


@pytest.mark.asyncio
async def test_detail_lookup_keeps_the_summaries_of_the_last_query(env) -> None:
    """Looking one entry up on its own must not evict what the query cached."""
    server, fake_client = env
    capture_id = await _capture(server, fake_client, _old_entries(3))
    listed = await _query(server, capture_id, path_contains="/api/old/1")
    other = await _query(server, capture_id, path_contains="/api/old/2")

    for entry_id in (listed["items"][0]["entry_id"], other["items"][0]["entry_id"]):
        detail = _tool_result(
            await server.call_tool(
                "get_traffic_entry_detail",
                {"source": "live", "entry_id": entry_id, "capture_id": capture_id},
            )
        )
        assert detail["entry_id"] == entry_id


@pytest.mark.asyncio
async def test_an_unknown_entry_id_says_how_to_recover(env) -> None:
    server, fake_client = env
    capture_id = await _capture(server, fake_client, _old_entries(2))

    with pytest.raises(Exception, match="query the capture again"):
        await server.call_tool(
            "get_traffic_entry_detail",
            {"source": "live", "entry_id": "0" * 40, "capture_id": capture_id},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("order", "expected"),
    [
        (("api_focus", "all_http"), {"api_focus": 1, "all_http": 2}),
        (("all_http", "api_focus"), {"api_focus": 1, "all_http": 2}),
    ],
)
async def test_a_preset_result_does_not_depend_on_the_calls_before_it(
    env, order: tuple[str, str], expected: dict[str, int]
) -> None:
    server, fake_client = env
    capture_id = await _capture(
        server,
        fake_client,
        [
            _api_entry("/api/login", start=NOW - timedelta(seconds=9)),
            _image_entry("/logo.png", start=NOW - timedelta(seconds=8)),
        ],
    )

    counts = {}
    for preset in order:
        counts[preset] = (await _query(server, capture_id, preset=preset))["matched_count"]

    assert counts == expected


@pytest.mark.asyncio
async def test_a_rule_can_be_created_from_an_entry_beyond_position_500(env) -> None:
    server, fake_client = env
    newest = _api_entry("/api/v1/wallet", start=NOW - timedelta(seconds=2), action="get_balance")
    capture_id = await _capture(server, fake_client, _old_entries(600) + [newest])
    await server.call_tool("mock_route_setup", {"host": "api.example.com", "path": "/api/*"})

    variants = _tool_result(
        await server.call_tool(
            "mock_discover_variants",
            {"source": "live", "capture_id": capture_id, "path_contains": "/api/v1/wallet"},
        )
    )
    entry_id = variants["groups"][0]["entry_ids"][0]
    created = _tool_result(
        await server.call_tool(
            "mock_rule_create_from_entry",
            {
                "source": "live",
                "entry_id": entry_id,
                "capture_id": capture_id,
                "match_body_fields": ["/action"],
                "response_patches": [{"op": "set", "path": "/data/balance", "value": "0.00"}],
            },
        )
    )

    assert json.loads(created["rule"]["body_match_json"]) == {"/action": "get_balance"}
    assert created["rule"]["path"] == "/api/v1/wallet"


@pytest.mark.asyncio
async def test_a_mock_file_can_be_created_from_an_entry_beyond_position_500(env) -> None:
    server, fake_client = env
    newest = _api_entry("/api/v1/wallet", start=NOW - timedelta(seconds=2), action="get_balance")
    capture_id = await _capture(server, fake_client, _old_entries(600) + [newest])
    found = await _query(server, capture_id, path_contains="/api/v1/wallet")

    created = _tool_result(
        await server.call_tool(
            "mock_create_from_entry",
            {
                "source": "live",
                "entry_id": found["items"][0]["entry_id"],
                "capture_id": capture_id,
            },
        )
    )

    assert Path(created["mock"]["file"]).is_file()


@pytest.mark.asyncio
async def test_discover_variants_keeps_the_newest_entries_and_says_so(env) -> None:
    server, fake_client = env
    newest = _api_entry("/api/v1/wallet", start=NOW - timedelta(seconds=2), action="fresh")
    capture_id = await _capture(server, fake_client, _old_entries(100) + [newest])

    variants = _tool_result(
        await server.call_tool(
            "mock_discover_variants", {"source": "live", "capture_id": capture_id, "limit": 50}
        )
    )

    assert variants["scanned"] == 50
    assert any(group["value_json"] == '"fresh"' for group in variants["groups"])
    assert "/api/old/0" not in {group["path"] for group in variants["groups"]}
    assert any("only the newest 50 of 101" in warning for warning in variants["warnings"])


@pytest.mark.asyncio
async def test_discover_variants_filters_before_it_applies_the_limit(env) -> None:
    server, fake_client = env
    payment = _api_entry("/pay/confirm", start=OLD - timedelta(minutes=5), host="pay.example.com")
    capture_id = await _capture(server, fake_client, [payment] + _old_entries(100))

    variants = _tool_result(
        await server.call_tool(
            "mock_discover_variants",
            {"source": "live", "capture_id": capture_id, "host_contains": "pay.", "limit": 50},
        )
    )

    assert [group["path"] for group in variants["groups"]] == ["/pay/confirm"]
    assert variants["warnings"] == []


@pytest.mark.asyncio
async def test_a_capture_of_tunnels_only_says_ssl_proxying_is_off(env) -> None:
    server, fake_client = env
    tunnels = [_tunnel_entry("api.example.com", start=NOW - timedelta(seconds=i)) for i in range(3)]
    capture_id = await _capture(server, fake_client, tunnels)

    result = await _query(server, capture_id)

    assert result["items"] == []
    hints = [warning for warning in result["warnings"] if warning.startswith("ssl_proxying_off")]
    assert len(hints) == 1
    assert "SSL Proxying" in hints[0] and "(3)" in hints[0]


@pytest.mark.asyncio
async def test_the_ssl_proxying_hint_follows_the_host_filter(env) -> None:
    server, fake_client = env
    capture_id = await _capture(
        server,
        fake_client,
        [
            _tunnel_entry("pay.example.com", start=NOW - timedelta(seconds=4)),
            _api_entry("/api/login", start=NOW - timedelta(seconds=3)),
        ],
    )

    whole = await _query(server, capture_id)
    payments = await _query(server, capture_id, host_contains="pay.example")
    grouped = _tool_result(
        await server.call_tool(
            "group_capture_analysis",
            {
                "source": "live",
                "capture_id": capture_id,
                "group_by": "path",
                "host_contains": "pay.example",
            },
        )
    )

    # The capture as a whole has readable traffic, so there is nothing to explain.
    assert not any(warning.startswith("ssl_proxying_off") for warning in whole["warnings"])
    # The host the agent asked about has none.
    for result in (payments, grouped):
        assert any(
            warning.startswith("ssl_proxying_off") and "pay.example" in warning
            for warning in result["warnings"]
        )
