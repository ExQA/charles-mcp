import json
from copy import deepcopy
from pathlib import Path

import pytest

import charles_mcp.mocks.service as mock_service_module
import charles_mcp.server as server_module
from charles_mcp.config import Config
from charles_mcp.server import create_server


def _tool_result(call_result):
    payload = call_result[1]
    return payload["result"] if isinstance(payload, dict) and "result" in payload else payload


def _json_entry(
    *,
    path: str,
    body: dict,
    method: str = "GET",
    status: int = 200,
    query: str | None = None,
) -> dict:
    return {
        "scheme": "https",
        "host": "api.example.com",
        "method": method,
        "path": path,
        "query": query,
        "status": "COMPLETE",
        "times": {"start": "2026-09-10T10:00:01.000+00:00"},
        "request": {"headers": []},
        "response": {
            "status": status,
            "mimeType": "application/json",
            "headers": [{"name": "Content-Type", "value": "application/json"}],
            "body": {"text": json.dumps(body)},
        },
    }


def _fake_client_class() -> type:
    class FakeClient:
        current_export: list[dict] = []
        calls: list[str] = []

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
            type(self).calls.append(f"{tool}:{'enable' if enabled else 'disable'}")
            return True

    FakeClient.calls = []
    return FakeClient


FRESH_CHARLES_CONFIG = (
    "<?xml version='1.0' encoding='UTF-8' ?>\n<?charles serialisation-version='2.0' ?>\n"
    "<configuration><toolConfiguration><configs>"
    "<entry><string>Map Local</string><mapLocal /></entry>"
    "<entry><string>Rewrite</string><rewrite /></entry>"
    "</configs></toolConfiguration></configuration>\n"
)


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake_client = _fake_client_class()
    monkeypatch.setattr(server_module, "CharlesClient", fake_client)
    charles_config = tmp_path / "charles" / "com.xk72.charles.config"
    charles_config.parent.mkdir()
    charles_config.write_text(FRESH_CHARLES_CONFIG, encoding="utf-8")

    config = Config(mock_dir=str(tmp_path / "mocks"))
    # Config.__post_init__ auto-detects the real Charles config; never touch it in tests.
    config.config_path = str(charles_config)
    config.backup_dir = str(tmp_path / "backup")
    config.state_dir = str(tmp_path / "state")
    server = create_server(config=config)
    return server, fake_client, tmp_path / "mocks"


async def _capture_entry(server, fake_client, entry: dict) -> tuple[str, str]:
    started = _tool_result(await server.call_tool("start_live_capture", {}))
    fake_client.current_export = [entry]
    queried = _tool_result(await server.call_tool(
        "query_live_capture_entries",
        {"capture_id": started["capture_id"], "preset": "all_http"},
    ))
    assert queried["items"], queried
    return started["capture_id"], queried["items"][0]["entry_id"]


@pytest.mark.asyncio
async def test_create_from_entry_writes_patched_json_file(mock_env) -> None:
    server, fake_client, root = mock_env
    capture_id, entry_id = await _capture_entry(
        server,
        fake_client,
        _json_entry(path="/api/v1/profile", body={"balance": 120, "unread": 0, "promo": "x"}),
    )

    result = _tool_result(await server.call_tool(
        "mock_create_from_entry",
        {
            "source": "live",
            "capture_id": capture_id,
            "entry_id": entry_id,
            "patches": [
                {"op": "set", "path": "/balance", "value": 0},
                {"op": "set", "path": "/unread", "value": 3},
                {"op": "remove", "path": "/promo"},
            ],
        },
    ))

    file = root / "api.example.com" / "api" / "v1" / "profile"
    assert json.loads(file.read_text(encoding="utf-8")) == {"balance": 0, "unread": 3}
    assert result["mock"]["path"] == "/api/v1/profile"
    assert result["patches_applied"] == 3
    assert result["source_entry_id"] == entry_id
    assert any("mock_setup_host" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_create_from_entry_warns_about_map_local_limits(mock_env) -> None:
    server, fake_client, _ = mock_env
    await server.call_tool("mock_setup_host", {"host": "api.example.com"})
    capture_id, entry_id = await _capture_entry(
        server,
        fake_client,
        _json_entry(
            path="/api/v1/orders", body={"error": "x"}, method="POST", status=500, query="page=2"
        ),
    )

    result = _tool_result(await server.call_tool(
        "mock_create_from_entry",
        {"source": "live", "capture_id": capture_id, "entry_id": entry_id},
    ))

    warnings = " ".join(result["warnings"])
    assert "ignores the HTTP method" in warnings
    assert "always answers 200" in warnings
    assert "page=2" in warnings
    assert "mock_setup_host" not in warnings


@pytest.mark.asyncio
async def test_write_list_get_remove_round_trip(mock_env) -> None:
    server, _, root = mock_env

    setup = _tool_result(await server.call_tool("mock_setup_host", {"host": "api.example.com"}))
    assert setup["map_local_rule"]["local_path"] == str(root / "api.example.com")

    await server.call_tool(
        "mock_write",
        {"host": "api.example.com", "path": "/api/v1/feed", "body": {"items": []}},
    )
    listed = _tool_result(await server.call_tool("mock_list", {}))
    assert [item["path"] for item in listed["items"]] == ["/api/v1/feed"]

    shown = _tool_result(await server.call_tool(
        "mock_get", {"host": "api.example.com", "path": "/api/v1/feed"}
    ))
    assert json.loads(shown["content"]) == {"items": []}

    removed = _tool_result(await server.call_tool(
        "mock_remove", {"host": "api.example.com", "path": "/api/v1/feed"}
    ))
    assert Path(removed["archived_to"]).exists()
    assert _tool_result(await server.call_tool("mock_list", {}))["total"] == 0


@pytest.mark.asyncio
async def test_write_requires_exactly_one_body(mock_env) -> None:
    server, _, _ = mock_env

    with pytest.raises(Exception, match="exactly one"):
        await server.call_tool("mock_write", {"host": "api.example.com", "path": "/x"})


@pytest.mark.asyncio
async def test_setup_host_apply_writes_charles_config_when_closed(
    mock_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, _, root = mock_env
    monkeypatch.setattr(mock_service_module, "_port_open", lambda host, port: False)

    result = _tool_result(await server.call_tool(
        "mock_setup_host", {"host": "api.example.com", "apply": True}
    ))

    assert result["applied"] and result["map_local_added"] and result["rewrite_added"]
    assert Path(result["config_path"]).is_relative_to(root.parent)
    assert Path(result["config_backup"]).read_text(encoding="utf-8") == FRESH_CHARLES_CONFIG
    written = Path(result["config_path"]).read_text(encoding="utf-8")
    assert f"<dest>{root / 'api.example.com'}</dest>" in written


@pytest.mark.asyncio
async def test_setup_host_apply_refuses_while_charles_runs(
    mock_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, _, root = mock_env
    monkeypatch.setattr(mock_service_module, "_port_open", lambda host, port: True)

    with pytest.raises(Exception, match="Charles is running"):
        await server.call_tool("mock_setup_host", {"host": "api.example.com", "apply": True})
    config_file = root.parent / "charles" / "com.xk72.charles.config"
    assert config_file.read_text(encoding="utf-8") == FRESH_CHARLES_CONFIG


@pytest.mark.asyncio
async def test_setup_host_default_only_returns_manual_steps(mock_env) -> None:
    server, _, root = mock_env

    result = _tool_result(await server.call_tool("mock_setup_host", {"host": "api.example.com"}))

    assert not result.get("applied")
    assert any("Tools > Rewrite" in step for step in result["instructions"])
    config_file = root.parent / "charles" / "com.xk72.charles.config"
    assert config_file.read_text(encoding="utf-8") == FRESH_CHARLES_CONFIG


@pytest.mark.asyncio
async def test_set_enabled_toggles_map_local_tool(mock_env) -> None:
    server, fake_client, _ = mock_env

    on = _tool_result(await server.call_tool("mock_set_enabled", {"enabled": True}))
    off = _tool_result(await server.call_tool("mock_set_enabled", {"enabled": False}))

    assert on["success"] and off["success"]
    assert fake_client.calls == ["map-local:enable", "map-local:disable"]
