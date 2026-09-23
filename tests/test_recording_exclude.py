"""Excluding hosts from Charles recording through its config file.

The Web Interface cannot change Recording Settings, so the exclusion that stops
Charles recording this server's own exports is either made in the UI or
written into the config file while Charles is closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from charles_mcp.config import Config
from charles_mcp.mocks import rule_service
from charles_mcp.mocks.charles_config import apply_recording_exclude, recording_excludes
from charles_mcp.tools.charles_settings import exclude_from_recording

PROLOG = "<?xml version='1.0' encoding='UTF-8' ?>\n<?charles serialisation-version='2.0' ?>\n"
CONFIG = PROLOG + """<configuration>
  <proxyConfiguration>
    <port>8888</port>
  </proxyConfiguration>
  <accessControlConfiguration>
    <ipRanges/>
  </accessControlConfiguration>
  <toolConfiguration/>
</configuration>
"""


def _config_file(tmp_path: Path, text: str = CONFIG) -> Path:
    path = tmp_path / "com.xk72.charles.config"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_section_is_created_where_charles_would_write_it(tmp_path: Path) -> None:
    path = _config_file(tmp_path)

    change = apply_recording_exclude(path, host="control.charles", backup_dir=tmp_path / "b")

    assert change.added and change.backup_path
    text = path.read_text(encoding="utf-8")
    assert text.startswith(PROLOG)  # Charles' serialisation header survives
    # Between proxyConfiguration and accessControlConfiguration, as in charles.jar.
    assert (
        text.index("</proxyConfiguration>")
        < text.index("<recordingConfiguration>")
        < text.index("<accessControlConfiguration>")
    )
    assert recording_excludes(path) == ["control.charles"]
    assert (
        "<ignoreHosts><locationPatterns><locationMatch><location><host>control.charles"
        in "".join(line.strip() for line in text.splitlines())
    )


def test_an_existing_exclusion_is_left_alone(tmp_path: Path) -> None:
    path = _config_file(tmp_path)
    apply_recording_exclude(path, host="control.charles", backup_dir=tmp_path / "b")
    before = path.read_text(encoding="utf-8")

    again = apply_recording_exclude(path, host="control.charles", backup_dir=tmp_path / "b")

    assert again.already_excluded and not again.added
    assert again.backup_path is None
    assert path.read_text(encoding="utf-8") == before


def test_hosts_already_excluded_by_the_user_are_kept(tmp_path: Path) -> None:
    path = _config_file(
        tmp_path,
        CONFIG.replace(
            "</proxyConfiguration>",
            "</proxyConfiguration>\n  <recordingConfiguration><ignoreHosts><locationPatterns>"
            "<locationMatch><location><host>analytics.example.com</host></location>"
            "</locationMatch></locationPatterns></ignoreHosts></recordingConfiguration>",
        ),
    )

    apply_recording_exclude(path, host="control.charles", backup_dir=tmp_path / "b")

    assert recording_excludes(path) == ["analytics.example.com", "control.charles"]


def _settings(tmp_path: Path, path: Path) -> Config:
    config = Config()
    config.config_path = str(path)
    config.backup_dir = str(tmp_path / "backups")
    return config


def test_without_apply_it_only_reports_and_explains(tmp_path: Path) -> None:
    path = _config_file(tmp_path)
    before = path.read_text(encoding="utf-8")

    result = exclude_from_recording(_settings(tmp_path, path), "control.charles", apply=False)

    assert result["applied"] is False
    assert result["excluded_in_config_file"] is False
    assert any("Recording Settings" in step for step in result["instructions"])
    assert path.read_text(encoding="utf-8") == before


def test_apply_refuses_while_charles_is_running(tmp_path: Path, monkeypatch) -> None:
    path = _config_file(tmp_path)
    monkeypatch.setattr(rule_service, "_port_open", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "charles_mcp.tools.charles_settings._port_open", lambda *_args, **_kwargs: True
    )

    with pytest.raises(ValueError, match="Charles is running"):
        exclude_from_recording(_settings(tmp_path, path), "control.charles", apply=True)
    assert recording_excludes(path) == []


def test_apply_writes_when_charles_is_closed(tmp_path: Path, monkeypatch) -> None:
    path = _config_file(tmp_path)
    monkeypatch.setattr(
        "charles_mcp.tools.charles_settings._port_open", lambda *_args, **_kwargs: False
    )

    result = exclude_from_recording(_settings(tmp_path, path), "control.charles", apply=True)

    assert result["applied"] is True
    assert Path(result["config_backup"]).is_file()
    assert recording_excludes(path) == ["control.charles"]


def test_excluding_everything_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="stop Charles recording anything"):
        exclude_from_recording(_settings(tmp_path, _config_file(tmp_path)), "*", apply=False)
