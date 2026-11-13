"""What the Charles config file says, without what must not leave it.

The file holds the Charles license (``registrationConfiguration``) and the Web
Interface passwords, and Rewrite rules or an upstream proxy can carry tokens
and credentials of their own. An agent that opens the file sends all of that to
its model. This module reads it instead and returns only the settings that
explain how traffic is handled: Map Remote and Map Local mappings, SSL Proxying
and recording host lists, the tools that are switched on, and whether the Web
Interface is enabled for the configured user.

Two rules keep secrets in the file:

- **Sections come from an allow list of exact names.** A section this module
  does not name is left out, never copied, so a Charles version that adds a
  secret somewhere new does not leak it.
- **Values are cleaned before they are returned or quoted in a diagnostic.**
  A location (protocol, host, port, path, query) is user input: a mapping for
  ``?access_token=...`` or a whole URL typed into the Host field would carry a
  token or a password into the summary. Query values are replaced with ``…``
  (parameter names stay), a ``user:password@`` part is dropped, and numbers and
  flags are returned only when they parse as such. Paths are returned as
  written: without them a mapping cannot be told from its neighbour.

Charles writes the file from memory when it quits and leaves out values that
are at their defaults, so the summary describes Charles as it was at its last
exit, and a missing element means "default".
"""

from __future__ import annotations

import re
import socket
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element  # nosec B405 - type only; parsing is defusedxml

from charles_mcp.mocks.charles_config import _load_config

# Longest list returned for one setting; the rest is counted, not listed.
MAX_LIST_ITEMS = 100
# Longest single value returned; a longer one is cut.
MAX_VALUE_CHARS = 200

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

# proxyConfiguration values worth showing, each a number or a flag. Anything
# else there (an upstream proxy with its user and password, client
# certificates and their passphrases) stays out.
_PROXY_SCALARS = {
    "port": "port",
    "enableSOCKSProxy": "socks_enabled",
    "socksPort": "socks_port",
    "transparentProxy": "transparent",
    "enableMacOSXProxy": "macos_proxy",
}

_HTTPS_PORT = "443"
_PROTOCOL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{0,15}")
_PORT = re.compile(r"[0-9*?\-]{1,11}")


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

    # Exactly this list and no other child of proxyConfiguration: a section
    # that merely looks like a host list may hold something else.
    ssl_locations = _locations(root.find("proxyConfiguration/sslLocations"))
    recording_excludes = _locations(root.find("recordingConfiguration/ignoreHosts"))
    recording_includes = _locations(root.find("recordingConfiguration/includeHosts"))
    tools = _tools(root)

    map_remote = _map_remote(tools.get("Map Remote"), ssl_locations, warnings)
    map_local = _map_local(tools.get("Map Local"))
    rewrite = _rewrite(tools.get("Rewrite"))
    web_interface = _web_interface(root, charles_user, warnings)

    if not any(location.get("host") == "control.charles" for location in recording_excludes):
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
        "ssl_proxying": {"hosts": _capped([_host_port(item) for item in ssl_locations])},
        "recording": {
            "excluded_hosts": _capped([_host_port(item) for item in recording_excludes]),
            "included_hosts": _capped([_host_port(item) for item in recording_includes]),
        },
        "access_control": _capped(_access_control(root)),
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
            "query values and credentials inside mapping locations",
            "upstream proxy and client certificate settings",
            "window, file and update settings",
        ],
        "warnings": _capped(warnings),
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
        for tag, key in _PROXY_SCALARS.items():
            value = _number_or_flag(proxy.findtext(tag))
            if value is not None:
                values[key] = value
    return values


def _access_control(root: Element) -> list[str]:
    ranges = []
    for ip_range in root.findall("accessControlConfiguration/ipRanges/ipRange"):
        ip = _dotted(ip_range.findall("ip/int"))
        mask = _dotted(ip_range.findall("mask/int"))
        if ip:
            ranges.append(f"{ip}/{mask}" if mask else ip)
    return ranges


def _dotted(parts: list[Element]) -> str:
    numbers = [(part.text or "").strip() for part in parts]
    return ".".join(numbers) if numbers and all(n.isdigit() for n in numbers) else ""


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


def _map_remote(
    tool: Element | None, ssl_locations: list[dict[str, str]], warnings: list[str]
) -> dict[str, Any]:
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
            source, dest, ssl_locations, mapping_on=enabled and item["enabled"]
        )
        if problems:
            item["problems"] = problems
            warnings.extend(f"map_remote {item['from']}: {problem}" for problem in problems)
        mappings.append(item)
    return {"enabled": enabled, "mappings": _capped(mappings)}


def _mapping_problems(
    source: dict[str, str],
    dest: dict[str, str],
    ssl_locations: list[dict[str, str]],
    *,
    mapping_on: bool,
) -> list[str]:
    """What stops a Map Remote mapping from working. Quotes cleaned values only."""
    problems = []
    dest_host = dest.get("host", "")
    if any(mark in dest_host for mark in ("://", "/", ":", "?")):
        problems.append(
            f"destination host {dest_host!r} is not a host name; Charles fails with 'Name "
            "lookup failed'. Put only the host in Host and the port in Port."
        )
    source_host = source.get("host", "")
    if source.get("protocol", "https").lower() == "https" and source_host:
        source_port = source.get("port", "")
        if not _ssl_covers(source_host, source_port, ssl_locations):
            where = f"{source_host}:{source_port or _HTTPS_PORT}"
            problems.append(
                f"SSL Proxying does not cover {where}, so Charles sees only a tunnel and "
                "never maps the request."
            )
    if mapping_on and dest_host in {"127.0.0.1", "localhost"} and dest.get("port", "").isdigit():
        port = int(dest["port"])
        if 0 < port < 65536 and not _listening(port):
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
            "to": _cut((mapping.findtext("dest") or "").strip()),
            "enabled": _flag(mapping, "enabled", default=True),
        }
        for mapping in tool.findall("mappings/mapLocalMapping")
    ]
    return {"enabled": _flag(tool, "toolEnabled"), "mappings": _capped(mappings)}


def _rewrite(tool: Element | None) -> dict[str, Any]:
    if tool is None:
        return {"enabled": False, "sets": []}
    sets = [
        {
            "name": _cut((rewrite_set.findtext("name") or "").strip()),
            "active": _flag(rewrite_set, "active", default=True),
            "hosts": _capped(
                [
                    _url(_location(match.find("location")))
                    for match in rewrite_set.findall("hosts/locationPatterns/locationMatch")
                ]
            ),
            "rule_count": len(rewrite_set.findall("rules/rewriteRule")),
        }
        for rewrite_set in tool.findall("sets/rewriteSet")
    ]
    return {"enabled": _flag(tool, "toolEnabled"), "sets": _capped(sets)}


def _counted_tool(tool: Element) -> dict[str, Any]:
    """Switched on or not, and whether it holds anything; never the entries."""
    has_entries = any(child.tag != "toolEnabled" for child in tool)
    return {"enabled": _flag(tool, "toolEnabled"), "configured": has_entries}


# ------------------------------------------------------------------ locations


def _locations(parent: Element | None) -> list[dict[str, str]]:
    """The cleaned locations of a Charles host list (``locationPatterns``)."""
    if parent is None:
        return []
    return [
        location
        for match in parent.findall("locationPatterns/locationMatch")
        if (location := _location(match.find("location"))).get("host")
    ]


def _location(element: Element | None) -> dict[str, str]:
    """A Charles location with every part cleaned; the only way values leave the file.

    Parts that do not look like what they should be are dropped rather than
    passed on: a protocol or a port is a short token, never free text.
    """
    if element is None:
        return {}
    raw = {
        tag: (element.findtext(tag) or "").strip()
        for tag in ("protocol", "host", "port", "path", "query")
    }
    location: dict[str, str] = {}
    if _PROTOCOL.fullmatch(raw["protocol"]):
        location["protocol"] = raw["protocol"]
    if raw["host"]:
        location["host"] = _clean_host(raw["host"])
    if _PORT.fullmatch(raw["port"]):
        location["port"] = raw["port"]
    if raw["path"]:
        location["path"] = _clean_path(raw["path"])
    if raw["query"]:
        location["query"] = _query_names(raw["query"])
    return location


def _clean_host(host: str) -> str:
    """A host as typed into Charles, without credentials or query values.

    A host is meant to be a name, but a whole URL typed into the field is the
    very mistake the summary reports, and such a URL can carry a password
    (``https://user:pass@host``) or a token (``host/path?key=...``).
    """
    scheme, separator, rest = host.partition("://")
    if not separator:
        scheme, rest = "", host
    rest, _, fragment = rest.partition("#")
    rest, question, query = rest.partition("?")
    authority, slash, path = rest.partition("/")
    authority = authority.rsplit("@", 1)[-1]
    cleaned = authority + slash + path
    if question:
        cleaned += f"?{_query_names(query)}"
    if fragment:
        cleaned += "#…"
    return _cut(f"{scheme}://{cleaned}" if scheme else cleaned)


def _clean_path(path: str) -> str:
    path, _, fragment = path.partition("#")
    path, question, query = path.partition("?")
    if question:
        path += f"?{_query_names(query)}"
    return _cut(path + ("#…" if fragment else ""))


def _query_names(query: str) -> str:
    """Parameter names of a query pattern, never their values: ``a=…&b=…``.

    A part without ``=`` cannot be told from a bare value, so it becomes ``…``
    unless it is only wildcards.
    """
    parts = []
    for item in query.split("&"):
        name, equals, _value = item.partition("=")
        if equals:
            parts.append(f"{name}=…")
        else:
            parts.append(item if item and not item.strip("*?") else "…")
    return _cut("&".join(parts))


def _url(location: dict[str, str]) -> str:
    host = location.get("host") or "*"
    url = f"{location['protocol']}://{host}" if location.get("protocol") else host
    if location.get("port"):
        url += f":{location['port']}"
    url += location.get("path", "")
    if location.get("query"):
        url += f"?{location['query']}"
    return url


def _host_port(location: dict[str, str]) -> str:
    return f"{location['host']}:{location['port']}" if location.get("port") else location["host"]


def _ssl_covers(host: str, port: str, ssl_locations: list[dict[str, str]]) -> bool:
    """Whether an SSL Proxying entry covers a mapping's source ``host:port``.

    Charles reads an empty field as "any": an entry without a port covers every
    port. A mapping without a port is checked against 443, where HTTPS traffic
    arrives unless the URL says otherwise. Hosts compare without case, and the
    entry's ``*`` and ``?`` are wildcards.
    """
    host = host.lower()
    port = port if port and port != "*" else _HTTPS_PORT
    for entry in ssl_locations:
        entry_host = entry.get("host", "").lower()
        entry_port = entry.get("port", "") or "*"
        if fnmatchcase(host, entry_host) and fnmatchcase(port, entry_port):
            return True
    return False


# -------------------------------------------------------------------- helpers


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


def _number_or_flag(text: str | None) -> bool | int | None:
    """A config value that is a number or a flag; anything else is not returned."""
    value = (text or "").strip().lower()
    if value in {"true", "false"}:
        return value == "true"
    return int(value) if value.isdigit() and len(value) <= 9 else None


def _cut(value: str) -> str:
    return value if len(value) <= MAX_VALUE_CHARS else value[:MAX_VALUE_CHARS] + "…"


def _capped(items: list[Any]) -> list[Any]:
    """At most MAX_LIST_ITEMS items; a last string says how many were left out."""
    if len(items) <= MAX_LIST_ITEMS:
        return items
    return [*items[:MAX_LIST_ITEMS], f"… and {len(items) - MAX_LIST_ITEMS} more, not listed"]
