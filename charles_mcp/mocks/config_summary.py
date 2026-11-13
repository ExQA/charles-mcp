"""What the Charles config file says, without what must not leave it.

The file holds the Charles license (``registrationConfiguration``) and the Web
Interface passwords, and Rewrite rules or an upstream proxy can carry tokens
and credentials of their own. An agent that opens the file sends all of that to
its model. This module reads it instead and returns only the settings that
explain how traffic is handled: Map Remote and Map Local mappings, SSL Proxying
and recording host lists, the tools that are switched on, and whether the Web
Interface is enabled for the configured user.

Everything is picked from an allow list. A section this module does not know is
left out, never copied, so a Charles version that adds a secret somewhere new
does not leak it.

Charles writes the file from memory when it quits and leaves out values that
are at their defaults, so the summary describes Charles as it was at its last
exit, and a missing element means "default".
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element  # nosec B405 - type only; parsing is defusedxml

from charles_mcp.mocks.charles_config import _load_config

# Tools reported only as switched on or off and whether they hold anything:
# their entries can hold request data, so the entries themselves stay out.
_COUNTED_TOOLS = {
    "Breakpoints": "breakpoints",
    "Block List": "block_list",
    "Allow List": "allow_list",
    "No Caching": "no_caching",
    "Block Cookies": "block_cookies",
    "DNS Spoofing": "dns_spoofing",
    "Reverse Proxies": "reverse_proxies",
    "Port Forwarding": "port_forwarding",
    "Mirror": "mirror",
    "Auto Save": "auto_save",
    "Client Process": "client_process",
}

# proxyConfiguration scalars worth showing. Anything else there (an upstream
# proxy with its user and password, for one) stays out.
_PROXY_SCALARS = ("port", "enableSOCKSProxy", "socksPort", "transparentProxy", "enableMacOSXProxy")


def summarize_config(
    config_path: str | Path,
    *,
    charles_user: str | None = None,
    proxy_open: bool | None = None,
) -> dict[str, Any]:
    """Summarise the Charles config file, leaving out every secret.

    ``charles_user`` is the Web Interface user this server logs in with; the
    summary says whether the file has such a user without naming any user.
    ``proxy_open`` says whether Charles is running now, which decides how far
    the file can be trusted.
    """
    path, _prolog, root = _load_config(config_path)
    warnings: list[str] = []

    ssl_lists = _ssl_lists(root)
    ssl_hosts = ssl_lists.get("sslLocations", [])
    recording_excludes = _hosts(root.find("recordingConfiguration/ignoreHosts"))
    recording_includes = _hosts(root.find("recordingConfiguration/includeHosts"))
    tools = _tools(root)

    map_remote = _map_remote(tools.get("Map Remote"), ssl_hosts, warnings)
    map_local = _map_local(tools.get("Map Local"))
    rewrite = _rewrite(tools.get("Rewrite"))
    web_interface = _web_interface(root, charles_user, warnings)

    if "control.charles" not in recording_excludes:
        warnings.append(
            "recording_not_excluded: control.charles is not in Proxy > Recording Settings > "
            "Exclude, so Charles records every session export this server makes. Use "
            "charles_recording_exclude."
        )

    summary: dict[str, Any] = {
        "config_path": str(path),
        "file_modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "charles_running": proxy_open,
        "proxy": _proxy(root),
        "ssl_proxying": {
            "hosts": ssl_hosts,
            **{_snake(tag): hosts for tag, hosts in ssl_lists.items() if tag != "sslLocations"},
        },
        "recording": {"excluded_hosts": recording_excludes, "included_hosts": recording_includes},
        "access_control": _access_control(root),
        "web_interface": web_interface,
        "map_remote": map_remote,
        "map_local": map_local,
        "rewrite": rewrite,
        # Only the tools that are on or hold something: the rest are at their defaults.
        "other_tools": {
            key: state
            for name, key in _COUNTED_TOOLS.items()
            if name in tools and any((state := _counted_tool(tools[name])).values())
        },
        "left_out": [
            "Charles license",
            "Web Interface user names and passwords",
            "Rewrite match and replace values",
            "upstream proxy settings",
            "window, file and update settings",
        ],
        "warnings": warnings,
    }
    if proxy_open:
        summary["note"] = (
            "Charles is running. It writes this file when it quits, so changes made in its "
            "windows during this run are not in it yet; the live state can differ."
        )
    return summary


# ------------------------------------------------------------------- sections


def _proxy(root: Element) -> dict[str, Any]:
    proxy = root.find("proxyConfiguration")
    values: dict[str, Any] = {"port": 8888}
    if proxy is not None:
        for tag in _PROXY_SCALARS:
            element = proxy.find(tag)
            if element is not None and element.text is not None:
                values[_snake(tag)] = _scalar(element.text)
    return values


def _ssl_lists(root: Element) -> dict[str, list[str]]:
    """Every host list under proxyConfiguration whose name mentions SSL."""
    proxy = root.find("proxyConfiguration")
    if proxy is None:
        return {}
    return {
        child.tag: _hosts(child)
        for child in proxy
        if "ssl" in child.tag.lower() and child.find("locationPatterns") is not None
    }


def _access_control(root: Element) -> list[str]:
    ranges = []
    for ip_range in root.findall("accessControlConfiguration/ipRanges/ipRange"):
        ip = ".".join(item.text or "" for item in ip_range.findall("ip/int"))
        mask = ".".join(item.text or "" for item in ip_range.findall("mask/int"))
        ranges.append(f"{ip}/{mask}" if mask else ip)
    return ranges


def _web_interface(root: Element, charles_user: str | None, warnings: list[str]) -> dict[str, Any]:
    remote = root.find("remoteControlConfiguration")
    users = [] if remote is None else remote.findall("users/remoteControlUser")
    enabled = remote is not None and _flag(remote, "enabled")
    names = {(user.findtext("username") or "").strip() for user in users}
    result: dict[str, Any] = {"enabled": enabled, "user_count": len(users)}
    if charles_user is not None:
        result["configured_user_present"] = charles_user in names
    if not enabled:
        warnings.append(
            "web_interface_disabled: Proxy > Web Interface Settings is off in the file, and this "
            "server cannot reach Charles without it."
        )
    elif charles_user is not None and charles_user not in names:
        warnings.append(
            "web_interface_user_missing: the file has no Web Interface user named like "
            "CHARLES_USER, so the server's login is refused."
        )
    return result


def _tools(root: Element) -> dict[str, Element]:
    tools: dict[str, Element] = {}
    for entry in root.findall("toolConfiguration/configs/entry"):
        key = entry.find("string")
        body = [child for child in entry if child.tag != "string"]
        if key is not None and key.text and body:
            tools[key.text] = body[0]
    return tools


def _map_remote(tool: Element | None, ssl_hosts: list[str], warnings: list[str]) -> dict[str, Any]:
    if tool is None:
        return {"enabled": False, "mappings": []}
    enabled = _flag(tool, "toolEnabled")
    mappings = []
    for mapping in tool.findall("mappings/mapMapping"):
        source = _location(mapping.find("sourceLocation"))
        dest = _location(mapping.find("destLocation"))
        item: dict[str, Any] = {
            "from": _url(source),
            "to": _url(dest) if dest.get("host") else "(same host)",
            "preserve_host_header": _flag(mapping, "preserveHostHeader"),
            "enabled": _flag(mapping, "enabled", default=True),
        }
        problems = _mapping_problems(
            source, dest, ssl_hosts, mapping_on=enabled and item["enabled"]
        )
        if problems:
            item["problems"] = problems
            warnings.extend(f"map_remote {item['from']}: {problem}" for problem in problems)
        mappings.append(item)
    return {"enabled": enabled, "mappings": mappings}


def _mapping_problems(
    source: dict[str, str], dest: dict[str, str], ssl_hosts: list[str], *, mapping_on: bool
) -> list[str]:
    problems = []
    dest_host = dest.get("host", "")
    if any(mark in dest_host for mark in ("://", "/", ":")):
        problems.append(
            f"destination host {dest_host!r} is not a host name; Charles fails with 'Name "
            "lookup failed'. Put only the host in Host and the port in Port."
        )
    source_host = source.get("host", "")
    if (
        source.get("protocol", "https") == "https"
        and source_host
        and not _covered(source_host, ssl_hosts)
    ):
        problems.append(
            f"SSL Proxying does not cover {source_host}, so Charles sees only a tunnel and "
            "never maps the request."
        )
    if mapping_on and dest_host in {"127.0.0.1", "localhost"} and dest.get("port"):
        port = int(dest["port"]) if dest["port"].isdigit() else None
        if port is not None and not _listening(port):
            problems.append(
                f"nothing listens on {dest_host}:{port}; every request under this mapping "
                "fails until the dispatcher runs (mock_dispatcher action=start)."
            )
    return problems


def _map_local(tool: Element | None) -> dict[str, Any]:
    if tool is None:
        return {"enabled": False, "mappings": []}
    mappings = [
        {
            "from": _url(_location(mapping.find("sourceLocation"))),
            "to": (mapping.findtext("dest") or "").strip(),
            "enabled": _flag(mapping, "enabled", default=True),
        }
        for mapping in tool.findall("mappings/mapLocalMapping")
    ]
    return {"enabled": _flag(tool, "toolEnabled"), "mappings": mappings}


def _rewrite(tool: Element | None) -> dict[str, Any]:
    if tool is None:
        return {"enabled": False, "sets": []}
    sets = [
        {
            "name": (rewrite_set.findtext("name") or "").strip(),
            "active": _flag(rewrite_set, "active", default=True),
            "hosts": [
                _url(_location(match.find("location")))
                for match in rewrite_set.findall("hosts/locationPatterns/locationMatch")
            ],
            "rule_count": len(rewrite_set.findall("rules/rewriteRule")),
        }
        for rewrite_set in tool.findall("sets/rewriteSet")
    ]
    return {"enabled": _flag(tool, "toolEnabled"), "sets": sets}


def _counted_tool(tool: Element) -> dict[str, Any]:
    """Switched on or not, and whether it holds anything; never the entries."""
    has_entries = any(child.tag != "toolEnabled" for child in tool)
    return {"enabled": _flag(tool, "toolEnabled"), "configured": has_entries}


# ------------------------------------------------------------------- helpers


def _hosts(parent: Element | None) -> list[str]:
    if parent is None:
        return []
    hosts = []
    for match in parent.findall("locationPatterns/locationMatch"):
        location = _location(match.find("location"))
        if location.get("host"):
            hosts.append(
                f"{location['host']}:{location['port']}"
                if location.get("port")
                else location["host"]
            )
    return hosts


def _location(element: Element | None) -> dict[str, str]:
    if element is None:
        return {}
    return {
        tag: (element.findtext(tag) or "").strip()
        for tag in ("protocol", "host", "port", "path", "query")
        if element.find(tag) is not None
    }


def _url(location: dict[str, str]) -> str:
    host = location.get("host") or "*"
    url = f"{location['protocol']}://{host}" if location.get("protocol") else host
    if location.get("port"):
        url += f":{location['port']}"
    url += location.get("path") or ""
    if location.get("query"):
        url += f"?{location['query']}"
    return url


def _covered(host: str, ssl_hosts: list[str]) -> bool:
    for entry in ssl_hosts:
        pattern = entry.rsplit(":", 1)[0] if entry.rsplit(":", 1)[-1].isdigit() else entry
        if pattern in {"*", ""} or fnmatchcase(host, pattern):
            return True
    return False


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _flag(parent: Element, tag: str, *, default: bool = False) -> bool:
    text = parent.findtext(tag)
    if text is None:
        return default
    return text.strip().lower() == "true"


def _scalar(text: str) -> Any:
    value = text.strip()
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return int(value) if value.isdigit() else value


def _snake(tag: str) -> str:
    out = ""
    for char in tag:
        out += f"_{char.lower()}" if char.isupper() and out else char.lower()
    return out
