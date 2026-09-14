"""Many hosts, many paths, many actions: host globs, routes, session-wide discovery."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from charles_mcp.mocks.charles_config import apply_map_remote_route
from charles_mcp.mocks.dispatcher import RULE_HEADER, DispatcherServer, DispatcherSettings
from charles_mcp.mocks.rules import (
    IncomingRequest,
    MockRule,
    RouteConfig,
    RuleStore,
    host_matches,
    path_matches,
    select_rule,
)
from charles_mcp.mocks.store import MockPathError, normalize_host_pattern
from tests.test_mock_rule_tools import _capture, _entry_id, _tool_result, env  # noqa: F401

PATH = "/api/v2/gateway"


def _rule(rule_id: str, host: str, action: str, **extra: object) -> MockRule:
    return MockRule.model_validate(
        {
            "id": rule_id,
            "host": host,
            "match": {"method": "POST", "path": PATH, "body": {"/action": action}},
            "response": {"mode": "fixture"},
            **extra,
        }
    )


def _request(host: str, action: str) -> IncomingRequest:
    return IncomingRequest(
        method="POST",
        path=PATH,
        query={},
        headers={},
        body=json.dumps({"action": action}).encode(),
        content_type="application/json",
        host=host,
    )


def test_host_patterns() -> None:
    assert normalize_host_pattern("*") == "*"
    assert normalize_host_pattern("*.Example.COM") == "*.example.com"
    for bad in ["https://*.example.com", "*.example.com:443", "a/*"]:
        with pytest.raises(MockPathError):
            normalize_host_pattern(bad)
    assert host_matches("*", "dev.example.com")
    assert host_matches("*.example.com", "dev.example.com")
    assert not host_matches("*.example.com", "dev.example.org")
    assert path_matches("/*", "/anything/here")
    assert path_matches("/api/*", "/api/v2/gateway") and path_matches("/api/*", "/api")
    assert not path_matches("/api/*", "/apiv2")


def test_any_host_rule_applies_everywhere_and_exact_host_wins() -> None:
    shared = _rule("shared-init", "*", "init")
    domain = _rule("domain-init", "*.example.com", "init")
    stage_only = _rule("stage-init", "stage.example.com", "init")
    rules = [shared, domain, stage_only]

    assert select_rule(rules, _request("stage.example.com", "init")).id == "stage-init"  # type: ignore[union-attr]
    assert select_rule(rules, _request("dev.example.com", "init")).id == "domain-init"  # type: ignore[union-attr]
    assert select_rule(rules, _request("api.other.org", "init")).id == "shared-init"  # type: ignore[union-attr]
    assert select_rule(rules, _request("dev.example.com", "payout")) is None


def test_routes_table_upsert_and_lookup(tmp_path: Path) -> None:
    store = RuleStore(tmp_path)
    store.save_route(RouteConfig(host="*.example.com", paths=["/api/*"]))
    store.save_route(RouteConfig(host="dev.example.com", upstream_port=8443))
    store.save_route(RouteConfig(host="*.example.com", paths=["/api/*", "/auth/*"]))

    assert [route.host for route in store.list_routes()] == ["*.example.com", "dev.example.com"]
    assert store.find_route("dev.example.com").upstream_port == 8443  # type: ignore[union-attr]
    assert store.find_route("stage.example.com", "/auth/login").host == "*.example.com"  # type: ignore[union-attr]
    assert store.find_route("stage.example.com", "/static/app.js") is None
    assert store.find_route("stage.example.org") is None
    assert json.loads((tmp_path / "_rules" / "routes.json").read_text())["version"] == 1


def test_pattern_rules_live_in_any_dir(tmp_path: Path) -> None:
    store = RuleStore(tmp_path)
    store.save_rule(_rule("shared-init", "*", "init"), b"{}")
    store.save_rule(_rule("stage-init", "stage.example.com", "init"), b"{}")

    assert (tmp_path / "_rules" / "_any" / "shared-init.json").is_file()
    assert (tmp_path / "_rules" / "stage.example.com" / "stage-init.json").is_file()
    stage, _ = store.rules_for_host("stage.example.com")
    dev, _ = store.rules_for_host("dev.example.com")
    assert sorted(rule.id for rule in stage) == ["shared-init", "stage-init"]
    assert [rule.id for rule in dev] == ["shared-init"]


def test_dispatcher_serves_one_rule_set_on_several_hosts(tmp_path: Path) -> None:
    store = RuleStore(tmp_path)
    store.save_route(RouteConfig(host="*.example.test", paths=["/api/*"]))
    store.save_rule(_rule("shared-init", "*", "init"), b'{"from": "shared"}')
    store.save_rule(_rule("stage-init", "stage.example.test", "init"), b'{"from": "stage"}')
    server = DispatcherServer(("127.0.0.1", 0), DispatcherSettings(mock_dir=str(tmp_path)))
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def post(host: str, path: str = PATH) -> tuple[int, str | None, bytes]:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("POST", path, body=b'{"action": "init"}', headers={"Host": host})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, response.getheader(RULE_HEADER), data

    try:
        dev = post("dev.example.test")
        stage = post("stage.example.test")
        outside_path = post("dev.example.test", "/static/app.js")
        outside_host = post("dev.example.org")
    finally:
        server.shutdown()
        server.server_close()

    # Fixtures without patches are served byte for byte.
    assert dev == (200, "shared-init", b'{"from": "shared"}')
    assert stage == (200, "stage-init", b'{"from": "stage"}')
    assert outside_path[0] == 421 and outside_host[0] == 421


def test_wildcard_map_remote_keeps_the_path(tmp_path: Path) -> None:
    config = tmp_path / "charles.config"
    config.write_text(
        "<?xml version='1.0' encoding='UTF-8' ?>\n<configuration><toolConfiguration><configs>"
        "<entry><string>Map Remote</string><map /></entry></configs></toolConfiguration>"
        "</configuration>\n",
        encoding="utf-8",
    )

    first = apply_map_remote_route(config, host="*.example.com", path="/*", backup_dir=tmp_path / "b")
    second = apply_map_remote_route(config, host="*.example.com", path="/*", backup_dir=tmp_path / "b")

    text = config.read_text(encoding="utf-8")
    mapping = ET.fromstring(text[text.find("<configuration"):]).find(
        "toolConfiguration/configs/entry/map/mappings/mapMapping"
    )
    assert first.mapping_added and not second.mapping_added and second.backup_path is None
    assert mapping is not None
    assert mapping.findtext("sourceLocation/host") == "*.example.com"
    assert mapping.findtext("sourceLocation/path") == "/*"
    assert mapping.find("destLocation/path") is None  # Charles keeps the original path
    assert mapping.findtext("preserveHostHeader") == "true"


def _entry(host: str, method: str, path: str, action: str | None, second: int) -> dict:
    body = {"action": action, "n": second} if action else None
    return {
        "scheme": "https",
        "host": host,
        "method": method,
        "path": path,
        "status": "COMPLETE",
        "times": {"start": f"2026-09-12T10:00:{second:02d}.000+00:00"},
        # Same shape as a real Charles export-json entry.
        "request": {
            "mimeType": "application/json" if body else None,
            "header": {
                "headers": [{"name": "Content-Type", "value": "application/json"}] if body else []
            },
            "body": {"text": json.dumps(body)} if body else {},
        },
        "response": {
            "status": 200,
            "mimeType": "application/json",
            "header": {"headers": [{"name": "Content-Type", "value": "application/json"}]},
            "body": {"text": json.dumps({"data": {"balance": 10, "limit": 5, "name": "x"}})},
        },
    }


@pytest.mark.asyncio
async def test_session_wide_discovery_groups_hosts_paths_and_actions(env) -> None:  # noqa: F811
    server, fake_client, _ = env
    capture_id = await _capture(server, fake_client, [
        _entry("dev.example.com", "POST", "/api/wallet", "init", 1),
        _entry("dev.example.com", "POST", "/api/wallet", "cards", 2),
        _entry("dev.example.com", "POST", "/api/wallet", "init", 3),
        _entry("dev.example.com", "POST", "/api/profile", "load", 4),
        _entry("stage.example.com", "POST", "/api/wallet", "init", 5),
        _entry("dev.example.com", "GET", "/api/config", None, 6),
    ])

    result = _tool_result(await server.call_tool(
        "mock_discover_variants", {"source": "live", "capture_id": capture_id}
    ))

    rows = [(g["host"], g["method"], g["path"], g["value_json"], g["count"]) for g in result["groups"]]
    assert rows == [
        ("dev.example.com", "GET", "/api/config", "null", 1),
        ("dev.example.com", "POST", "/api/profile", '"load"', 1),
        ("dev.example.com", "POST", "/api/wallet", '"cards"', 1),
        ("dev.example.com", "POST", "/api/wallet", '"init"', 2),
        ("stage.example.com", "POST", "/api/wallet", '"init"', 1),
    ]
    assert result["scanned"] == 6 and result["total_groups"] == 5


@pytest.mark.asyncio
async def test_repeated_requests_merge_into_one_variant_rule(env) -> None:  # noqa: F811
    server, fake_client, _ = env
    capture_id = await _capture(server, fake_client, [
        _entry("dev.example.com", "POST", "/api/wallet", "init", 1),
    ])
    entry_id = await _entry_id(server, capture_id, '"init"')
    base = {
        "source": "live",
        "capture_id": capture_id,
        "entry_id": entry_id,
        "match_body_fields": ["/action"],
    }

    first = _tool_result(await server.call_tool("mock_rule_create_from_entry", {
        **base, "response_patches": [{"op": "set", "path": "/data/balance", "value": 0}],
    }))
    second = _tool_result(await server.call_tool("mock_rule_create_from_entry", {
        **base, "response_patches": [
            {"op": "set", "path": "/data/limit", "value": 999},
            {"op": "set", "path": "/data/balance", "value": 7},
        ],
        "request_patches": [{"op": "set", "path": "/n", "value": 42}],
    }))

    rule = json.loads(second["rule_json"])
    assert rule["host"] == "*"
    assert first["rule"]["id"] == second["rule"]["id"]
    assert rule["response"]["patches"] == [
        {"op": "set", "path": "/data/limit", "value": 999},
        {"op": "set", "path": "/data/balance", "value": 7},
    ]
    assert rule["request"]["patches"] == [{"op": "set", "path": "/n", "value": 42}]
    assert any("merged into existing rule" in warning for warning in second["warnings"])

    exact = _tool_result(await server.call_tool("mock_rule_create_from_entry", {
        **base, "host_scope": "exact",
        "response_patches": [{"op": "set", "path": "/data/name", "value": "dev only"}],
    }))
    assert json.loads(exact["rule_json"])["host"] == "dev.example.com"
    listed = _tool_result(await server.call_tool("mock_rule_list", {"host": "dev.example.com"}))
    assert sorted(item["host"] for item in listed["items"]) == ["*", "dev.example.com"]


@pytest.mark.asyncio
async def test_route_setup_defaults_to_whole_host_glob(env) -> None:  # noqa: F811
    server, _, config = env

    result = _tool_result(await server.call_tool("mock_route_setup", {"host": "*.example.com"}))

    assert result["path"] == "/*"
    assert result["map_remote_rule"]["path_unchanged"] is True
    assert result["map_remote_rule"]["destination"] == f"http://127.0.0.1:{config.dispatcher_port}"
    assert any("WebSocket" in warning for warning in result["warnings"])
    with pytest.raises(Exception, match="hostname or a domain glob"):
        await server.call_tool("mock_route_setup", {"host": "*"})
