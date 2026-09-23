import json
from copy import deepcopy

import pytest

import charles_mcp.server as server_module
from charles_mcp.server import create_server


def _tool_result(call_result):
    if isinstance(call_result, tuple):
        payload = call_result[1]
        return payload["result"] if isinstance(payload, dict) and "result" in payload else payload
    if isinstance(call_result, list) and call_result:
        return json.loads(call_result[0].text)
    return call_result


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

        async def clear_session(self) -> bool:
            return True

        async def start_recording(self) -> bool:
            return True

        async def get_info(self):
            return {"status": "connected"}

    return FakeClient


@pytest.mark.asyncio
async def test_charles_status_reports_active_live_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _fake_client_class()
    monkeypatch.setattr(server_module, "CharlesClient", fake_client)

    server = create_server()
    started = _tool_result(await server.call_tool("start_live_capture", {"reset_session": True}))
    status = _tool_result(await server.call_tool("charles_status", {}))

    assert status["connected"] is True
    assert status["live_capture"]["active_capture"]["capture_id"] == started["capture_id"]
    assert status["live_capture"]["active_capture"]["status"] == "active"
    assert status["recommended_next_action"]
    assert "query_live_capture_entries" in status["recommended_next_action"]
    assert started["capture_id"] in status["recommended_next_action"]


@pytest.mark.asyncio
async def test_charles_status_recommends_start_live_capture_when_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _fake_client_class()
    monkeypatch.setattr(server_module, "CharlesClient", fake_client)

    server = create_server()
    status = _tool_result(await server.call_tool("charles_status", {}))

    assert status["connected"] is True
    assert status["live_capture"]["active_capture"] is None
    assert status["recommended_next_action"]
    assert "start_live_capture" in status["recommended_next_action"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        # QA: a wrong password was reported as "Charles is unreachable", which
        # sent people restarting a Charles that was running fine.
        ("api-401", "rejected the Web Interface credentials (HTTP 401)"),
        ("connection", "Charles is unreachable"),
    ],
)
async def test_charles_status_tells_a_wrong_password_from_charles_being_down(
    monkeypatch, error: str, expected: str
) -> None:
    from charles_mcp.client import CharlesAPIError, CharlesConnectionError

    fake_client = _fake_client_class()

    async def failing_get_info(self):
        if error == "api-401":
            raise CharlesAPIError("API call failed [401]: /", status_code=401)
        raise CharlesConnectionError("cannot connect to Charles")

    fake_client.get_info = failing_get_info
    monkeypatch.setattr(server_module, "CharlesClient", fake_client)
    server = create_server()

    status = _tool_result(await server.call_tool("charles_status", {}))

    assert status["connected"] is False
    assert expected in status["recommended_next_action"]
