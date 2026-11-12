"""reset_environment restores a saved Charles config, or does nothing at all.

With CHARLES_MANAGE_LIFECYCLE=false — the default and the recommended setting —
the server never saves a baseline. The tool then had nothing to restore but
still quit Charles (losing the unsaved session) and deleted every saved
recording, all under the description "restore the saved configuration".
"""

from __future__ import annotations

from pathlib import Path

import pytest

import charles_mcp.server as server_module
from charles_mcp.config import Config
from charles_mcp.server import create_server


def _tool_result(call_result):
    payload = call_result[1]
    return payload["result"] if isinstance(payload, dict) and "result" in payload else payload


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls: list[str] = []

    async def fake_restore(config: Config) -> bool:
        calls.append("restore")
        return True

    monkeypatch.setattr(server_module, "restore_config", fake_restore)
    config = Config(mock_dir=str(tmp_path / "mocks"))
    config.config_path = None
    config.backup_dir = str(tmp_path / "backup")
    config.state_dir = str(tmp_path / "state")
    config.package_dir = str(tmp_path / "package")
    recordings = Path(config.package_dir)
    recordings.mkdir()
    for name in ("20261110120000.chlsj", "20261110130000.chlsj"):
        (recordings / name).write_text("[]", encoding="utf-8")
    return create_server(config=config), config, calls


@pytest.mark.asyncio
async def test_without_a_saved_config_the_reset_changes_nothing(env) -> None:
    server, config, calls = env

    message = _tool_result(await server.call_tool("reset_environment", {}))

    assert calls == []  # Charles is not quit, nothing is deleted
    assert message.startswith("Nothing was changed")
    assert "CHARLES_MANAGE_LIFECYCLE=true" in message
    assert "2 saved recording(s)" in message
    assert len(list(Path(config.package_dir).glob("*.chlsj"))) == 2


@pytest.mark.asyncio
async def test_with_a_saved_config_the_reset_runs_and_says_what_it_did(env) -> None:
    server, config, calls = env
    baseline = Path(config.backup_dir) / "config" / "charles.config"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("<configuration />", encoding="utf-8")

    message = _tool_result(await server.call_tool("reset_environment", {}))

    assert calls == ["restore"]
    assert message.startswith("Environment reset completed")
    assert "asked to quit" in message and "2 saved recording(s) were deleted" in message
    assert "not started again" in message


@pytest.mark.asyncio
async def test_the_description_says_what_the_reset_destroys(env) -> None:
    server, _config, _calls = env
    description = {tool.name: tool.description or "" for tool in await server.list_tools()}[
        "reset_environment"
    ].lower()

    for fact in ("quit charles", "destructive", "session is lost", "recordings", "only when the user"):
        assert fact in description, fact
