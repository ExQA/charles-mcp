from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from charles_mcp.mocks.charles_config import (
    JSON_CONTENT_TYPE,
    CharlesConfigError,
    apply_mock_host_rules,
)

PROLOG = "<?xml version='1.0' encoding='UTF-8' ?>\n<?charles serialisation-version='2.0' ?>\n"

FRESH_CONFIG = PROLOG + """<configuration>
  <startupConfiguration>
    <acceptedEulaVersion>20240608</acceptedEulaVersion>
  </startupConfiguration>
  <toolConfiguration>
    <configs>
      <entry>
        <string>Rewrite</string>
        <rewrite />
      </entry>
      <entry>
        <string>Map Local</string>
        <mapLocal />
      </entry>
      <entry>
        <string>No Caching</string>
        <selectedHostsTool />
      </entry>
    </configs>
  </toolConfiguration>
</configuration>
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "com.xk72.charles.config"
    path.write_text(text, encoding="utf-8")
    return path


def _apply(path: Path, tmp_path: Path, host: str = "api.example.com"):
    return apply_mock_host_rules(
        path,
        host=host,
        dest_dir=str(tmp_path / "mocks" / host),
        backup_dir=tmp_path / "backup",
    )


def _tool(path: Path, name: str, tag: str) -> ET.Element:
    text = path.read_text(encoding="utf-8")
    root = ET.fromstring(text[text.find("<configuration"):])
    for entry in root.findall("toolConfiguration/configs/entry"):
        if entry.findtext("string") == name:
            element = entry.find(tag)
            assert element is not None
            return element
    raise AssertionError(f"{name} not found")


def test_adds_map_local_mapping_and_rewrite_set(tmp_path: Path) -> None:
    path = _write(tmp_path, FRESH_CONFIG)

    change = _apply(path, tmp_path)

    assert change.map_local_added and change.rewrite_added
    assert change.warnings == []
    assert path.read_text(encoding="utf-8").startswith(PROLOG)

    map_local = _tool(path, "Map Local", "mapLocal")
    assert map_local.findtext("toolEnabled") == "true"
    mapping = map_local.find("mappings/mapLocalMapping")
    assert mapping is not None
    assert mapping.findtext("sourceLocation/protocol") == "https"
    assert mapping.findtext("sourceLocation/host") == "api.example.com"
    assert mapping.findtext("sourceLocation/port") == "443"
    assert mapping.findtext("sourceLocation/path") == "/*"
    assert mapping.findtext("dest") == str(tmp_path / "mocks" / "api.example.com")

    rewrite = _tool(path, "Rewrite", "rewrite")
    assert rewrite.findtext("toolEnabled") == "true"
    rule_set = rewrite.find("sets/rewriteSet")
    assert rule_set is not None
    assert rule_set.findtext("hosts/locationPatterns/locationMatch/location/host") == (
        "api.example.com"
    )
    assert rule_set.findtext("rules/rewriteRule/ruleType") == "3"
    assert rule_set.findtext("rules/rewriteRule/matchValue") == "text/plain"
    assert rule_set.findtext("rules/rewriteRule/newValue") == JSON_CONTENT_TYPE

    # Untouched parts of the config survive.
    assert "<acceptedEulaVersion>20240608</acceptedEulaVersion>" in path.read_text("utf-8")
    assert _tool(path, "No Caching", "selectedHostsTool") is not None


def test_backup_is_taken_before_writing(tmp_path: Path) -> None:
    path = _write(tmp_path, FRESH_CONFIG)

    change = _apply(path, tmp_path)

    assert change.backup_path is not None
    assert Path(change.backup_path).read_text(encoding="utf-8") == FRESH_CONFIG


def test_second_apply_is_a_no_op(tmp_path: Path) -> None:
    path = _write(tmp_path, FRESH_CONFIG)
    _apply(path, tmp_path)
    after_first = path.read_text(encoding="utf-8")

    change = _apply(path, tmp_path)

    assert not change.map_local_added and not change.rewrite_added
    assert change.backup_path is None
    assert path.read_text(encoding="utf-8") == after_first


def test_second_host_is_appended(tmp_path: Path) -> None:
    path = _write(tmp_path, FRESH_CONFIG)
    _apply(path, tmp_path, host="api.example.com")

    _apply(path, tmp_path, host="cdn.example.com")

    hosts = [
        mapping.findtext("sourceLocation/host")
        for mapping in _tool(path, "Map Local", "mapLocal").findall("mappings/mapLocalMapping")
    ]
    assert hosts == ["api.example.com", "cdn.example.com"]
    assert len(_tool(path, "Rewrite", "rewrite").findall("sets/rewriteSet")) == 2


def test_conflicting_mapping_for_same_host_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, FRESH_CONFIG)
    apply_mock_host_rules(
        path, host="api.example.com", dest_dir="/elsewhere", backup_dir=tmp_path / "backup"
    )
    before = path.read_text(encoding="utf-8")

    with pytest.raises(CharlesConfigError, match="already maps"):
        _apply(path, tmp_path)
    assert path.read_text(encoding="utf-8") == before


def test_disabled_rewrite_with_foreign_sets_stays_disabled(tmp_path: Path) -> None:
    config = FRESH_CONFIG.replace(
        "<rewrite />",
        "<rewrite><toolEnabled>false</toolEnabled><sets><rewriteSet>"
        "<active>true</active><name>user set</name></rewriteSet></sets></rewrite>",
    )
    path = _write(tmp_path, config)

    change = _apply(path, tmp_path)

    assert _tool(path, "Rewrite", "rewrite").findtext("toolEnabled") == "false"
    assert any("Rewrite is disabled" in warning for warning in change.warnings)


def test_enabling_map_local_warns_about_existing_mappings(tmp_path: Path) -> None:
    config = FRESH_CONFIG.replace(
        "<mapLocal />",
        "<mapLocal><toolEnabled>false</toolEnabled><mappings><mapLocalMapping>"
        "<sourceLocation><host>other.example.com</host></sourceLocation>"
        "<dest>/x</dest></mapLocalMapping></mappings></mapLocal>",
    )
    path = _write(tmp_path, config)

    change = _apply(path, tmp_path)

    assert _tool(path, "Map Local", "mapLocal").findtext("toolEnabled") == "true"
    assert any("re-activates 1 existing" in warning for warning in change.warnings)


def test_missing_tool_entries_are_created(tmp_path: Path) -> None:
    path = _write(tmp_path, PROLOG + "<configuration></configuration>\n")

    change = _apply(path, tmp_path)

    assert change.map_local_added and change.rewrite_added
    assert _tool(path, "Map Local", "mapLocal").find("mappings/mapLocalMapping") is not None


@pytest.mark.parametrize("text", ["not xml", PROLOG + "<configuration><broken></configuration>"])
def test_unparseable_config_is_refused_untouched(tmp_path: Path, text: str) -> None:
    path = _write(tmp_path, text)

    with pytest.raises(CharlesConfigError):
        _apply(path, tmp_path)
    assert path.read_text(encoding="utf-8") == text
