"""The post-install check that separates this fork from the upstream package."""

from __future__ import annotations

import pytest

from charles_mcp import main as entrypoint


def test_selftest_passes_and_names_the_build(capsys: pytest.CaptureFixture[str]) -> None:
    assert entrypoint.selftest() == 0

    printed = capsys.readouterr().out
    assert "charles-mcp" in printed
    assert "mocking tools: " in printed
    assert "OK" in printed
    # The count is the point: upstream reports zero here.
    assert "mocking tools: 0" not in printed


def test_selftest_fails_when_a_build_has_no_mocking_tools(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """What an install that reached PyPI instead of the archive would look like."""

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    class _UpstreamServer:
        async def list_tools(self) -> list[_Tool]:
            return [_Tool("charles_status"), _Tool("start_live_capture")]

    monkeypatch.setattr(entrypoint, "create_server", _UpstreamServer)

    assert entrypoint.selftest() == 1
    printed = capsys.readouterr().out
    assert "FAIL" in printed
    assert "upstream package" in printed


def test_selftest_flag_exits_before_the_server_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["charles-mcp", "--selftest"])
    monkeypatch.setattr(entrypoint, "selftest", lambda: 0)

    def _must_not_run() -> None:  # pragma: no cover - the assertion is that it never runs
        raise AssertionError("the stdio server was started by --selftest")

    monkeypatch.setattr(entrypoint, "create_server", _must_not_run)

    with pytest.raises(SystemExit) as exit_info:
        entrypoint.main()
    assert exit_info.value.code == 0


def test_unknown_arguments_fail_instead_of_starting_the_server(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["charles-mcp", "--self-test"])

    def _must_not_run() -> None:  # pragma: no cover - the assertion is that it never runs
        raise AssertionError("a typo started the stdio server")

    monkeypatch.setattr(entrypoint, "create_server", _must_not_run)

    with pytest.raises(SystemExit) as exit_info:
        entrypoint.main()
    assert exit_info.value.code == 2
    assert "unknown arguments: --self-test" in capsys.readouterr().err
