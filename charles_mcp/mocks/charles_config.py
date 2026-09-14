"""Add mock-directory rules to the Charles configuration file.

Charles reads its configuration once at startup and writes its in-memory
state back on exit, so the file may only be edited while Charles is not
running: edits made while it runs are silently overwritten on quit.

For one host this adds:

- a Map Local mapping ``<protocol>://<host>:<port>/*`` -> ``<dest_dir>``;
- a Rewrite set that turns ``Content-Type: text/plain`` into
  ``application/json`` on that host. Charles labels extension-less local
  files (``/api/v1/profile``) as ``text/plain``, which breaks clients that
  only parse JSON for a JSON content type (Dio, Alamofire ``validate()``).
  Rewrite also applies to Map Local responses, so one rule covers every mock.
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET  # nosec B405 - only used to build/serialize
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from defusedxml import ElementTree as SafeET

REWRITE_SET_PREFIX = "charles-mcp mocks: "
JSON_CONTENT_TYPE = "application/json; charset=utf-8"


class CharlesConfigError(RuntimeError):
    """The Charles configuration file cannot be updated safely."""


@dataclass
class ConfigChange:
    config_path: str
    backup_path: str | None = None
    map_local_added: bool = False
    rewrite_added: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class MapRemoteChange:
    config_path: str
    backup_path: str | None = None
    mapping_added: bool = False
    warnings: list[str] = field(default_factory=list)


def _load_config(config_path: str | Path) -> tuple[Path, str, ET.Element]:
    path = Path(config_path)
    if not path.is_file():
        raise CharlesConfigError(f"Charles config file not found: {path}")
    text = path.read_text(encoding="utf-8")
    root_start = text.find("<configuration")
    if root_start < 0:
        raise CharlesConfigError(f"{path} does not look like a Charles config file")
    try:
        root = SafeET.fromstring(text[root_start:])
    except ET.ParseError as exc:
        raise CharlesConfigError(f"cannot parse {path}: {exc}") from exc
    return path, text[:root_start], root


def _write_config(path: Path, prolog: str, root: ET.Element, backup_dir: str | Path) -> str:
    """Back up the current file, then replace it atomically. Returns the backup path."""
    backup_root = Path(backup_dir)
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_root / f"{path.name}.{stamp}"
    shutil.copy2(path, backup)

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(prolog + body + "\n", encoding="utf-8")
    tmp.replace(path)
    return str(backup)


def apply_map_remote_route(
    config_path: str | Path,
    *,
    host: str,
    path: str,
    backup_dir: str | Path,
    dest_host: str = "127.0.0.1",
    dest_port: int = 18080,
    protocol: str = "https",
    port: int = 443,
) -> MapRemoteChange:
    """Map ``<protocol>://<host>:<port><path>`` to the local dispatcher.

    ``host`` and ``path`` may be Charles wildcards (``*.example.com``, ``/*``,
    ``/api/*``). For a wildcard path the destination has no path, which Charles
    documents as "the path part of the URL will not be changed", so one mapping
    covers every path under it.

    "Preserve host header" is required: the dispatcher picks the rules and the
    upstream from the original ``Host``. Charles omits default values when it
    re-serializes (``<enabled>true</enabled>``), so a missing element is read as
    its default to stay idempotent.
    """
    config_file, prolog, root = _load_config(config_path)
    change = MapRemoteChange(config_path=str(config_file))
    tool = _tool_config(_child(_child(root, "toolConfiguration"), "configs"), "Map Remote", "map")
    mappings = _child(tool, "mappings")
    source = {"protocol": protocol, "host": host, "port": str(port), "path": path}
    destination = {"protocol": "http", "host": dest_host, "port": str(dest_port)}
    if "*" not in path:
        destination["path"] = path

    existing = mappings.findall("mapMapping")
    changed = False
    for mapping in existing:
        source_element = mapping.find("sourceLocation")
        if any(_text(source_element, tag) != value for tag, value in source.items()):
            continue
        dest_element = mapping.find("destLocation")
        expected_path = destination.get("path")
        if any(_text(dest_element, tag) != value for tag, value in destination.items()) or (
            expected_path is None and _text(dest_element, "path")
        ):
            raise CharlesConfigError(
                f"Map Remote already maps {protocol}://{host}{path} elsewhere; "
                "remove that mapping in Charles first"
            )
        if not _is_true(mapping, "preserveHostHeader"):
            _set_flag(mapping, "preserveHostHeader")
            changed = True
        if mapping.find("enabled") is not None and not _is_true(mapping, "enabled"):
            _set_flag(mapping, "enabled")
            changed = True
        break
    else:
        mapping = ET.SubElement(mappings, "mapMapping")
        _add_elements(ET.SubElement(mapping, "sourceLocation"), source)
        _add_elements(ET.SubElement(mapping, "destLocation"), destination)
        _add_elements(mapping, {"preserveHostHeader": "true", "enabled": "true"})
        change.mapping_added = True
        changed = True

    if not _is_true(tool, "toolEnabled"):
        others = [item for item in existing if item.find("sourceLocation/host") is not None]
        if others:
            change.warnings.append(
                f"Map Remote was disabled; enabling it also re-activates {len(others)} "
                "existing mapping(s)."
            )
        _set_tool_enabled(tool)
        changed = True

    if changed:
        change.backup_path = _write_config(config_file, prolog, root, backup_dir)
    return change


def apply_mock_host_rules(
    config_path: str | Path,
    *,
    host: str,
    dest_dir: str | Path,
    backup_dir: str | Path,
    protocol: str = "https",
    port: int = 443,
) -> ConfigChange:
    path, prolog, root = _load_config(config_path)
    change = ConfigChange(config_path=str(path))
    configs = _child(_child(root, "toolConfiguration"), "configs")
    change.map_local_added = _add_map_local(
        _tool_config(configs, "Map Local", "mapLocal"),
        protocol=protocol,
        host=host,
        port=port,
        dest_dir=str(dest_dir),
        warnings=change.warnings,
    )
    change.rewrite_added = _add_rewrite(
        _tool_config(configs, "Rewrite", "rewrite"),
        protocol=protocol,
        host=host,
        port=port,
        warnings=change.warnings,
    )

    if change.map_local_added or change.rewrite_added:
        change.backup_path = _write_config(path, prolog, root, backup_dir)
    return change


def _child(parent: ET.Element, tag: str) -> ET.Element:
    element = parent.find(tag)
    if element is None:
        element = ET.SubElement(parent, tag)
    return element


def _tool_config(configs: ET.Element, name: str, tag: str) -> ET.Element:
    for entry in configs.findall("entry"):
        key = entry.find("string")
        if key is not None and key.text == name:
            return _child(entry, tag)
    entry = ET.SubElement(configs, "entry")
    ET.SubElement(entry, "string").text = name
    return ET.SubElement(entry, tag)


def _text(parent: ET.Element | None, tag: str) -> str | None:
    if parent is None:
        return None
    element = parent.find(tag)
    return None if element is None else (element.text or "")


def _is_true(parent: ET.Element, tag: str) -> bool:
    return (_text(parent, tag) or "").strip().lower() == "true"


def _set_tool_enabled(tool: ET.Element) -> None:
    element = tool.find("toolEnabled")
    if element is None:
        element = ET.Element("toolEnabled")
        tool.insert(0, element)
    element.text = "true"


def _set_flag(parent: ET.Element, tag: str) -> None:
    element = parent.find(tag)
    if element is None:
        element = ET.SubElement(parent, tag)
    element.text = "true"


def _add_elements(parent: ET.Element, values: dict[str, str]) -> None:
    for tag, value in values.items():
        ET.SubElement(parent, tag).text = value


def _add_map_local(
    tool: ET.Element,
    *,
    protocol: str,
    host: str,
    port: int,
    dest_dir: str,
    warnings: list[str],
) -> bool:
    mappings = _child(tool, "mappings")
    existing = mappings.findall("mapLocalMapping")
    for mapping in existing:
        source = mapping.find("sourceLocation")
        if _text(source, "host") == host and _text(source, "protocol") == protocol:
            if _text(mapping, "dest") != dest_dir:
                raise CharlesConfigError(
                    f"Map Local already maps {protocol}://{host} to "
                    f"{_text(mapping, 'dest')}; remove that mapping in Charles first"
                )
            if not _is_true(tool, "toolEnabled"):
                _set_tool_enabled(tool)
                return True
            return False

    if existing and not _is_true(tool, "toolEnabled"):
        warnings.append(
            f"Map Local was disabled; enabling it also re-activates {len(existing)} "
            "existing mapping(s)."
        )
    _set_tool_enabled(tool)
    mapping = ET.SubElement(mappings, "mapLocalMapping")
    source = ET.SubElement(mapping, "sourceLocation")
    _add_elements(source, {"protocol": protocol, "host": host, "port": str(port), "path": "/*"})
    _add_elements(mapping, {"dest": dest_dir, "enabled": "true", "caseSensitive": "true"})
    return True


def _add_rewrite(
    tool: ET.Element,
    *,
    protocol: str,
    host: str,
    port: int,
    warnings: list[str],
) -> bool:
    sets = _child(tool, "sets")
    name = f"{REWRITE_SET_PREFIX}{host}"
    existing = sets.findall("rewriteSet")
    others = [rewrite_set for rewrite_set in existing if _text(rewrite_set, "name") != name]
    already_present = len(others) != len(existing)

    changed = False
    if not _is_true(tool, "toolEnabled"):
        if others:
            warnings.append(
                "Rewrite is disabled and holds other rule sets, so it was left disabled; "
                "mocks are served as text/plain until Tools > Rewrite is enabled."
            )
        else:
            _set_tool_enabled(tool)
            changed = True
    if already_present:
        return changed

    rewrite_set = ET.SubElement(sets, "rewriteSet")
    _add_elements(rewrite_set, {"active": "true", "name": name})
    match = ET.SubElement(
        ET.SubElement(ET.SubElement(rewrite_set, "hosts"), "locationPatterns"), "locationMatch"
    )
    _add_elements(
        ET.SubElement(match, "location"),
        {"protocol": protocol, "host": host, "port": str(port)},
    )
    _add_elements(match, {"enabled": "true"})
    rule = ET.SubElement(ET.SubElement(rewrite_set, "rules"), "rewriteRule")
    _add_elements(
        rule,
        {
            "active": "true",
            "ruleType": "3",  # modify header
            "matchHeader": "Content-Type",
            "matchValue": "text/plain",
            "matchHeaderRegex": "false",
            "matchValueRegex": "false",
            "matchRequest": "false",
            "matchResponse": "true",
            "newHeader": "Content-Type",
            "newValue": JSON_CONTENT_TYPE,
            "newHeaderRegex": "false",
            "newValueRegex": "false",
            "matchWholeValue": "true",
            "caseSensitive": "false",
            "replaceType": "2",
        },
    )
    return True
