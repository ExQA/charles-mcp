"""Map Remote must not outlive the dispatcher it points to.

Charles keeps Map Remote switched on after the dispatcher is gone: an MCP
client that was closed or killed, a session that ended without
`mock_dispatcher(action="stop")`. From then on every request under a mocked
route fails in the app. Found on a real machine: Map Remote had been on for
weeks with nothing listening on the dispatcher port.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest

import charles_mcp.server as server_module
from charles_mcp.client import CharlesClient
from charles_mcp.config import Config
from charles_mcp.server import create_server


def _tool_result(call_result):
    payload = call_result[1]
    return payload["result"] if isinstance(payload, dict) and "result" in payload else payload


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _fake_client_class() -> type:
    class FakeClient:
        """A Charles whose Map Remote switch can be read and set."""

        map_remote_enabled: bool | None = False
        tool_calls: list[str] = []

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

        async def get_info(self):
            return {"status": "connected"}

        async def export_session_json(self) -> list[dict]:
            return []

        async def set_tool_enabled(self, tool: str, enabled: bool) -> bool:
            type(self).tool_calls.append(f"{tool}:{'on' if enabled else 'off'}")
            type(self).map_remote_enabled = enabled
            return True

        async def get_tool_enabled(self, tool: str) -> bool | None:
            return type(self).map_remote_enabled

    FakeClient.tool_calls = []
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
    config.dispatcher_port = _free_port()
    return create_server(config=config), fake_client, config


async def _status(server) -> dict:
    return _tool_result(await server.call_tool("charles_status", {}))


async def _run_and_close(server, inside) -> None:
    """Run ``inside`` while the server's lifespan is open, then let it close."""
    async with server.settings.lifespan(server):
        await inside()


@pytest.mark.asyncio
async def test_closing_the_server_stops_the_dispatcher_and_switches_map_remote_off(env) -> None:
    server, fake_client, config = env

    async def session() -> None:
        started = _tool_result(await server.call_tool("mock_dispatcher", {"action": "start"}))
        assert started["running"] is True
        assert _port_open(config.dispatcher_port)

    await _run_and_close(server, session)

    assert fake_client.tool_calls == ["map-remote:on", "map-remote:off"]
    assert not _port_open(config.dispatcher_port)


@pytest.mark.asyncio
async def test_closing_the_server_leaves_a_map_remote_it_did_not_switch_on(env) -> None:
    server, fake_client, config = env
    fake_client.map_remote_enabled = True  # the user's own setting

    async def session() -> None:
        await server.call_tool("mock_dispatcher", {"action": "start", "toggle_map_remote": False})

    await _run_and_close(server, session)

    assert fake_client.tool_calls == []
    assert fake_client.map_remote_enabled is True
    assert not _port_open(config.dispatcher_port)


@pytest.mark.asyncio
async def test_a_map_remote_that_was_already_on_is_left_on(env) -> None:
    """Map Remote is one switch for all of Charles: it may be on for the user's own mappings."""
    server, fake_client, config = env
    fake_client.map_remote_enabled = True
    messages: list[str] = []

    async def session() -> None:
        started = _tool_result(await server.call_tool("mock_dispatcher", {"action": "start"}))
        messages.append(started["message"])

    await _run_and_close(server, session)

    assert fake_client.tool_calls == ["map-remote:on"]  # nothing switched it off
    assert fake_client.map_remote_enabled is True
    assert "already on" in messages[0] and "leaves it on" in messages[0]
    assert not _port_open(config.dispatcher_port)


@pytest.mark.asyncio
async def test_an_unreadable_switch_is_not_taken_for_ours(env) -> None:
    server, fake_client, _ = env
    fake_client.map_remote_enabled = None  # Charles does not say
    messages: list[str] = []

    async def session() -> None:
        started = _tool_result(await server.call_tool("mock_dispatcher", {"action": "start"}))
        messages.append(started["message"])

    await _run_and_close(server, session)

    assert fake_client.tool_calls == ["map-remote:on"]
    assert "could not be read" in messages[0]


@pytest.mark.asyncio
async def test_two_servers_do_not_switch_map_remote_off_under_each_other(
    env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With two MCP clients open, only the server that switched Map Remote on switches it off."""
    first, fake_client, config = env
    other_config = Config(mock_dir=str(tmp_path / "mocks"))
    other_config.config_path = None
    other_config.backup_dir = str(tmp_path / "backup-2")
    other_config.state_dir = str(tmp_path / "state-2")
    other_config.package_dir = str(tmp_path / "package-2")
    other_config.dispatcher_port = config.dispatcher_port
    second = create_server(config=other_config)

    async with first.settings.lifespan(first):
        await first.call_tool("mock_dispatcher", {"action": "start"})

        async def second_session() -> None:
            # Finds the first server's dispatcher on the port and Map Remote already on.
            await second.call_tool("mock_dispatcher", {"action": "start"})

        await _run_and_close(second, second_session)

        # The second server is gone; the first one is still serving.
        assert fake_client.map_remote_enabled is True
        assert _port_open(config.dispatcher_port)

    assert fake_client.map_remote_enabled is False
    assert fake_client.tool_calls == ["map-remote:on", "map-remote:on", "map-remote:off"]


@pytest.mark.asyncio
async def test_a_charles_that_is_gone_does_not_break_the_shutdown(env) -> None:
    server, fake_client, _ = env

    async def session() -> None:
        await server.call_tool("mock_dispatcher", {"action": "start"})

        async def unreachable(self, tool: str, enabled: bool) -> bool:
            raise ConnectionError("Charles has quit")

        fake_client.set_tool_enabled = unreachable

    await _run_and_close(server, session)  # must not raise


@pytest.mark.asyncio
async def test_stop_switches_map_remote_off_even_when_no_dispatcher_runs(env) -> None:
    """The only way out of the stuck state used to be the Charles menu."""
    server, fake_client, _ = env
    fake_client.map_remote_enabled = True  # left on by a server that is gone

    stopped = _tool_result(await server.call_tool("mock_dispatcher", {"action": "stop"}))

    assert fake_client.tool_calls == ["map-remote:off"]
    assert "Map Remote disabled" in stopped["message"]


@pytest.mark.asyncio
async def test_status_names_map_remote_left_on_without_a_dispatcher(env) -> None:
    server, fake_client, config = env
    await server.call_tool("mock_route_setup", {"host": "*.example.com", "path": "/api/*"})
    fake_client.map_remote_enabled = True

    status = await _status(server)
    dispatcher = _tool_result(await server.call_tool("mock_dispatcher", {"action": "status"}))

    assert len(status["warnings"]) == 1
    warning = status["warnings"][0]
    assert warning.startswith("map_remote_without_dispatcher")
    assert "*.example.com/api/*" in warning and str(config.dispatcher_port) in warning
    assert 'mock_dispatcher(action="stop")' in warning
    # It says which port it looked at, where that port is set, and what stop costs.
    assert "CHARLES_DISPATCHER_PORT" in warning and "Only this port is checked" in warning
    assert "the user's own included" in warning
    assert dispatcher["message"] == warning


@pytest.mark.asyncio
@pytest.mark.parametrize("map_remote", [False, None])
async def test_no_warning_when_map_remote_is_off_or_cannot_be_read(env, map_remote) -> None:
    server, fake_client, _ = env
    await server.call_tool("mock_route_setup", {"host": "*.example.com", "path": "/api/*"})
    fake_client.map_remote_enabled = map_remote

    assert (await _status(server))["warnings"] == []


@pytest.mark.asyncio
async def test_no_warning_without_routes_or_while_the_dispatcher_answers(env) -> None:
    server, fake_client, _ = env
    fake_client.map_remote_enabled = True
    assert (await _status(server))["warnings"] == []  # Map Remote is the user's own business

    await server.call_tool("mock_route_setup", {"host": "*.example.com", "path": "/api/*"})
    await server.call_tool("mock_dispatcher", {"action": "start"})
    try:
        assert (await _status(server))["warnings"] == []
    finally:
        await server.call_tool("mock_dispatcher", {"action": "stop"})


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        ("<h1>Map Remote</h1><p>Status: Enabled</p>", True),
        ("<h1>Map Remote</h1><p>Status: Disabled</p>", False),
        ("<h1>Something else</h1>", None),
    ],
)
def test_the_client_reads_a_tool_state_from_its_web_interface_page(page, expected, tmp_path) -> None:
    client = CharlesClient(Config(state_dir=str(tmp_path / "state"), mock_dir=str(tmp_path / "m")))
    requested: list[str] = []

    class Page:
        text = page

    async def fake_get(endpoint: str, **kwargs):
        requested.append(endpoint)
        return Page()

    client._get = fake_get  # type: ignore[method-assign]

    assert asyncio.run(client.get_tool_enabled("map-remote")) is expected
    assert requested == ["/tools/map-remote/"]
