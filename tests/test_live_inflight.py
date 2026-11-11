"""A request Charles is still recording is one request, before and after it finishes.

Charles keeps updating an entry while it is in flight: an open CONNECT tunnel
changes its sizes and timings on every export, and a slow request gets its
response later. Found on a real session: two exports two seconds apart differed
in 39 of 597 entries, all of them open tunnels. A capture that tells entries
apart by those changing fields lists the same request again and again, and the
id it gave out for a request stops working the moment the request completes.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

import charles_mcp.server as server_module
from charles_mcp.config import Config
from charles_mcp.live_state import LiveCaptureManager, is_final_status
from charles_mcp.server import create_server
from charles_mcp.services.live_capture import LiveCaptureService

START = "2026-11-10T20:34:58.225+02:00"


def _request(
    path: str = "/api/v1/slow",
    *,
    done: bool,
    total_size: int = 300,
    start: str = START,
    method: str = "POST",
) -> dict:
    """One Charles entry, either still in flight or complete."""
    return {
        "status": "COMPLETE" if done else "RECEIVING_RESPONSE_BODY",
        "method": method,
        "scheme": "https",
        "host": "api.example.com",
        "path": path,
        "query": None,
        "clientAddress": "/127.0.0.1",
        "clientPort": 53001,
        "times": {"start": start, "end": start},
        "durations": {"total": 40 if done else None},
        "totalSize": 900 if done else total_size,
        "request": {
            "mimeType": "application/json",
            "header": {
                "firstLine": f"{method} {path} HTTP/1.1",
                "headers": [{"name": "Content-Type", "value": "application/json"}],
            },
            "sizes": {"body": 20},
            "body": {"text": '{"action":"init"}'},
        },
        "response": (
            {
                "status": 200,
                "mimeType": "application/json",
                "header": {
                    "firstLine": "HTTP/1.1 200 OK",
                    "headers": [{"name": "Content-Type", "value": "application/json"}],
                },
                "sizes": {"body": 30},
                "body": {"text": '{"data":{"ok":true}}'},
            }
            if done
            else {"status": None, "header": {}, "sizes": {"body": 0}}
        ),
    }


def _tunnel(total_size: int) -> dict:
    return {
        "status": "RECEIVING_RESPONSE_BODY",
        "method": "CONNECT",
        "host": "cdn.example.com",
        "path": None,
        "query": None,
        "clientAddress": "/127.0.0.1",
        "clientPort": 53002,
        "times": {"start": START, "end": START},
        "totalSize": total_size,
        "request": {"header": {"firstLine": "CONNECT cdn.example.com:443 HTTP/1.1"}},
        "response": {"status": 200, "sizes": {"body": total_size}},
    }


# ---------------------------------------------------------------- capture state


def test_only_complete_and_failed_entries_are_final() -> None:
    assert is_final_status("COMPLETE")
    assert is_final_status("EXCEPTION")
    assert is_final_status(None)  # saved recordings and synthetic entries carry no status
    assert not is_final_status("RECEIVING_RESPONSE_BODY")
    assert not is_final_status("RECEIVING_REQUEST_BODY")


def test_a_request_seen_in_flight_is_kept_once_and_reported_again_when_it_finishes() -> None:
    manager = LiveCaptureManager()
    capture = manager.start(managed=False, include_existing=True, baseline_items=[_request(done=False)])

    first = manager.read(capture.capture_id, [_request(done=False)])
    finished = manager.read(capture.capture_id, [_request(done=True)])
    later = manager.read(capture.capture_id, [_request(done=True)])

    assert [item["status"] for item in first.items] == ["RECEIVING_RESPONSE_BODY"]
    # The reader hears about the request once more, now with its response.
    assert [item["response"]["status"] for item in finished.items] == [200]
    assert later.items == []
    # And the capture holds one request, not one per state it went through.
    assert len(capture.items) == 1
    assert capture.items[0]["status"] == "COMPLETE"


def test_an_open_tunnel_that_keeps_changing_is_not_reported_on_every_read() -> None:
    manager = LiveCaptureManager()
    capture = manager.start(managed=False, include_existing=True, baseline_items=[_tunnel(1000)])

    first = manager.read(capture.capture_id, [_tunnel(1000)])
    second = manager.read(capture.capture_id, [_tunnel(2000)])
    third = manager.read(capture.capture_id, [_tunnel(3000)])

    assert len(first.items) == 1
    assert second.items == [] and third.items == []
    assert second.total_new_items == 0
    assert [item["totalSize"] for item in capture.items] == [3000]  # latest state, one entry


def test_a_read_from_the_start_returns_each_request_once() -> None:
    manager = LiveCaptureManager()
    capture = manager.start(managed=False, include_existing=True, baseline_items=[_request(done=False)])
    manager.read(capture.capture_id, [_request(done=True), _request("/api/v1/other", done=True)])

    everything = manager.read(
        capture.capture_id,
        [_request(done=True), _request("/api/v1/other", done=True)],
        cursor=0,
        limit=50,
        advance=False,
    )

    assert sorted(item["path"] for item in everything.items) == ["/api/v1/other", "/api/v1/slow"]
    assert everything.total_new_items == 2
    assert everything.truncated is False


def test_identical_requests_at_the_same_moment_stay_two_requests() -> None:
    """Without client ports (synthetic entries, older exports) position tells equals apart."""
    twin = {"host": "api.example.com", "method": "GET", "path": "/ping", "times": {"start": START}}
    manager = LiveCaptureManager()
    capture = manager.start(managed=False, include_existing=True, baseline_items=[twin, dict(twin)])

    result = manager.read(capture.capture_id, [twin, dict(twin)])

    assert len(result.items) == 2
    assert len(capture.items) == 2


def test_requests_hidden_at_the_start_stay_hidden_when_they_finish() -> None:
    manager = LiveCaptureManager()
    capture = manager.start(managed=True, include_existing=False, baseline_items=[_request(done=False)])

    result = manager.read(
        capture.capture_id, [_request(done=True), _request("/api/v1/new", done=True)]
    )

    assert [item["path"] for item in result.items] == ["/api/v1/new"]


def test_a_reset_noticed_while_peeking_still_reaches_the_reader() -> None:
    manager = LiveCaptureManager()
    capture = manager.start(managed=False, include_existing=True)
    manager.read(capture.capture_id, [_request("/a", done=True), _request("/b", done=True)])

    replacement = [_request("/after-clear", done=True, start="2026-11-10T21:00:00.000+02:00")]
    peek = manager.read(capture.capture_id, replacement, advance=False)
    read = manager.read(capture.capture_id, replacement)
    quiet = manager.read(capture.capture_id, replacement)

    assert peek.status == "reset_detected"
    assert [item["path"] for item in peek.items] == ["/after-clear"]
    # The cursor's owner is told too, and starts over instead of skipping the new session.
    assert read.status == "reset_detected"
    assert [item["path"] for item in read.items] == ["/after-clear"]
    assert quiet.status == "active" and quiet.items == []
    assert [item["path"] for item in capture.items] == ["/after-clear"]


# ------------------------------------------------------------------ tool level


def _tool_result(call_result):
    payload = call_result[1]
    return payload["result"] if isinstance(payload, dict) and "result" in payload else payload


def _fake_client_class() -> type:
    class FakeClient:
        current_export: list[dict] = []
        exports = 0

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
            type(self).exports += 1
            return deepcopy(type(self).current_export)

        async def start_recording(self) -> bool:
            return True

        async def stop_recording(self) -> bool:
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


async def _query(server, capture_id: str, **arguments) -> dict:
    return _tool_result(
        await server.call_tool(
            "query_live_capture_entries",
            {"capture_id": capture_id, "preset": "all_http", **arguments},
        )
    )


async def _detail(server, capture_id: str, entry_id: str, *, full: bool) -> dict:
    return _tool_result(
        await server.call_tool(
            "get_traffic_entry_detail",
            {
                "source": "live",
                "entry_id": entry_id,
                "capture_id": capture_id,
                "include_full_body": full,
            },
        )
    )


@pytest.mark.asyncio
async def test_an_entry_id_survives_the_completion_of_its_request(env) -> None:
    server, fake_client = env
    fake_client.current_export = [_request(done=False)]
    capture_id = _tool_result(await server.call_tool("start_live_capture", {}))["capture_id"]
    listed = await _query(server, capture_id)
    entry_id = listed["items"][0]["entry_id"]
    assert listed["items"][0].get("response_status") is None

    fake_client.current_export = [_request(done=True)]
    after = await _query(server, capture_id)
    summary = await _detail(server, capture_id, entry_id, full=False)
    full = await _detail(server, capture_id, entry_id, full=True)

    # One request before and after, under the id the agent already holds.
    assert after["total_items"] == 1
    assert [item["entry_id"] for item in after["items"]] == [entry_id]
    assert after["items"][0]["response_status"] == 200
    assert summary["detail"]["entry"]["response_status"] == 200
    assert json.loads(full["detail"]["entry"]["response"]["body"]["full_text"]) == {
        "data": {"ok": True}
    }


@pytest.mark.asyncio
async def test_detail_of_an_unfinished_request_is_never_served_from_the_cache(env) -> None:
    server, fake_client = env
    fake_client.current_export = [_request(done=False)]
    capture_id = _tool_result(await server.call_tool("start_live_capture", {}))["capture_id"]
    entry_id = (await _query(server, capture_id))["items"][0]["entry_id"]
    waiting = await _detail(server, capture_id, entry_id, full=False)

    fake_client.current_export = [_request(done=True)]
    arrived = await _detail(server, capture_id, entry_id, full=False)

    assert waiting["detail"]["entry"].get("response_status") is None
    assert arrived["detail"]["entry"]["response_status"] == 200


@pytest.mark.asyncio
async def test_detail_of_a_finished_request_does_not_export_the_session_again(env) -> None:
    """On a long session one export takes tens of seconds; a finished entry cannot change."""
    server, fake_client = env
    fake_client.current_export = [_request(done=True), _request("/api/v1/open", done=False)]
    capture_id = _tool_result(await server.call_tool("start_live_capture", {}))["capture_id"]
    items = {item["path"]: item["entry_id"] for item in (await _query(server, capture_id))["items"]}
    exports_after_query = fake_client.exports

    await _detail(server, capture_id, items["/api/v1/slow"], full=True)
    assert fake_client.exports == exports_after_query

    # A request still in flight may have its response by now: that one is fetched again.
    await _detail(server, capture_id, items["/api/v1/open"], full=True)
    assert fake_client.exports == exports_after_query + 1


@pytest.mark.asyncio
async def test_a_slow_export_is_reported_with_what_to_do_about_it(tmp_path: Path) -> None:
    fake_client = _fake_client_class()
    fake_client.current_export = [_request(done=True)]
    config = Config(state_dir=str(tmp_path / "state"), mock_dir=str(tmp_path / "mocks"))
    service = LiveCaptureService(config, client_factory=fake_client)

    quick = await service.start(adopt_existing=True)
    assert not any(warning.startswith("slow_export") for warning in quick.warnings)

    service.slow_export_seconds = 0.0  # every export counts as slow
    read = await service.read(quick.capture_id, advance=False)

    hints = [warning for warning in read.warnings if warning.startswith("slow_export")]
    assert len(hints) == 1
    assert "clear the Charles session" in hints[0] and "(1 entries)" in hints[0]
