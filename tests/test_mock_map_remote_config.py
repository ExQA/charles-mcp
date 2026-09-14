from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from charles_mcp.mocks.charles_config import CharlesConfigError, apply_map_remote_route

PROLOG = "<?xml version='1.0' encoding='UTF-8' ?>\n<?charles serialisation-version='2.0' ?>\n"
FRESH = PROLOG + """<configuration><toolConfiguration><configs>
<entry><string>Map Remote</string><map /></entry>
</configs></toolConfiguration></configuration>
"""


def _apply(path: Path, tmp_path: Path, **overrides: object):
    kwargs: dict = {
        "host": "api.example.com",
        "path": "/api/v1/wallet",
        "backup_dir": tmp_path / "backup",
        "dest_port": 18080,
        **overrides,
    }
    return apply_map_remote_route(path, **kwargs)


def _map_tool(path: Path) -> ET.Element:
    text = path.read_text(encoding="utf-8")
    root = ET.fromstring(text[text.find("<configuration"):])
    tool = root.find("toolConfiguration/configs/entry/map")
    assert tool is not None
    return tool


def test_adds_mapping_with_preserved_host_header(tmp_path: Path) -> None:
    path = tmp_path / "charles.config"
    path.write_text(FRESH, encoding="utf-8")

    change = _apply(path, tmp_path)

    assert change.mapping_added and change.backup_path is not None
    assert Path(change.backup_path).read_text(encoding="utf-8") == FRESH
    tool = _map_tool(path)
    assert tool.findtext("toolEnabled") == "true"
    mapping = tool.find("mappings/mapMapping")
    assert mapping is not None
    assert mapping.findtext("sourceLocation/protocol") == "https"
    assert mapping.findtext("sourceLocation/host") == "api.example.com"
    assert mapping.findtext("sourceLocation/path") == "/api/v1/wallet"
    assert mapping.findtext("destLocation/protocol") == "http"
    assert mapping.findtext("destLocation/host") == "127.0.0.1"
    assert mapping.findtext("destLocation/port") == "18080"
    assert mapping.findtext("destLocation/path") == "/api/v1/wallet"
    assert mapping.findtext("preserveHostHeader") == "true"


def test_second_apply_is_a_no_op_even_after_charles_reserializes(tmp_path: Path) -> None:
    path = tmp_path / "charles.config"
    path.write_text(FRESH, encoding="utf-8")
    _apply(path, tmp_path)
    # Charles drops default values such as <enabled>true</enabled> when it saves.
    path.write_text(path.read_text(encoding="utf-8").replace("<enabled>true</enabled>", ""),
                    encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    change = _apply(path, tmp_path)

    assert not change.mapping_added and change.backup_path is None
    assert path.read_text(encoding="utf-8") == before


def test_existing_mapping_gets_preserve_host_header(tmp_path: Path) -> None:
    path = tmp_path / "charles.config"
    path.write_text(
        FRESH.replace(
            "<map />",
            "<map><toolEnabled>true</toolEnabled><mappings><mapMapping>"
            "<sourceLocation><protocol>https</protocol><host>api.example.com</host>"
            "<port>443</port><path>/api/v1/wallet</path></sourceLocation>"
            "<destLocation><protocol>http</protocol><host>127.0.0.1</host>"
            "<port>18080</port><path>/api/v1/wallet</path></destLocation>"
            "</mapMapping></mappings></map>",
        ),
        encoding="utf-8",
    )

    change = _apply(path, tmp_path)

    assert not change.mapping_added and change.backup_path is not None
    assert _map_tool(path).findtext("mappings/mapMapping/preserveHostHeader") == "true"


def test_conflicting_destination_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "charles.config"
    path.write_text(FRESH, encoding="utf-8")
    _apply(path, tmp_path, dest_port=19999)
    before = path.read_text(encoding="utf-8")

    with pytest.raises(CharlesConfigError, match="elsewhere"):
        _apply(path, tmp_path)
    assert path.read_text(encoding="utf-8") == before


def test_enabling_a_disabled_tool_warns_about_other_mappings(tmp_path: Path) -> None:
    path = tmp_path / "charles.config"
    path.write_text(
        FRESH.replace(
            "<map />",
            "<map><toolEnabled>false</toolEnabled><mappings><mapMapping>"
            "<sourceLocation><host>other.example.com</host></sourceLocation>"
            "<destLocation><host>x</host></destLocation></mapMapping></mappings></map>",
        ),
        encoding="utf-8",
    )

    change = _apply(path, tmp_path)

    assert _map_tool(path).findtext("toolEnabled") == "true"
    assert any("re-activates 1 existing" in warning for warning in change.warnings)
