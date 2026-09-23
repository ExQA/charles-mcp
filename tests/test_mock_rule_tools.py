"""MCP-level tests for body-aware rules (synthetic data, example.com hosts)."""

from __future__ import annotations

import json
import socket
from copy import deepcopy
from http.client import HTTPConnection
from pathlib import Path

import pytest

import charles_mcp.mocks.rule_service as rule_service_module
import charles_mcp.server as server_module
from charles_mcp.config import Config
from charles_mcp.mocks.rules import RouteConfig, RuleStore
from charles_mcp.server import create_server
from tests.test_mock_dispatcher import FakeUpstream

WALLET = "/api/v1/wallet"
FRESH_CHARLES_CONFIG = (
    "<?xml version='1.0' encoding='UTF-8' ?>\n<?charles serialisation-version='2.0' ?>\n"
    "<configuration><toolConfiguration><configs>"
    "<entry><string>Map Remote</string><map /></entry>"
    "</configs></toolConfiguration></configuration>\n"
)


def _tool_result(call_result):
    payload = call_result[1]
    return payload["result"] if isinstance(payload, dict) and "result" in payload else payload


def _entry(action: str, *, balance: int, second: int, host: str = "api.example.com") -> dict:
    return {
        "scheme": "https",
        "host": host,
        "method": "POST",
        "path": WALLET,
        "status": "COMPLETE",
        "times": {"start": f"2026-09-12T10:00:{second:02d}.000+00:00"},
        # Same shape as a real Charles export-json entry.
        "request": {
            "mimeType": "application/json",
            "header": {
                "firstLine": f"POST {WALLET} HTTP/1.1",
                "headers": [{"name": "Content-Type", "value": "application/json"}],
            },
            "body": {"text": json.dumps({"action": action, "scheme": 1})},
        },
        "response": {
            "status": 200,
            "mimeType": "application/json",
            "header": {
                "firstLine": "HTTP/1.1 200 OK",
                "headers": [{"name": "Content-Type", "value": "application/json"}],
            },
            "body": {"text": json.dumps({"status": "success", "data": {"balance": balance}})},
        },
    }


def _fake_client_class() -> type:
    class FakeClient:
        current_export: list[dict] = []
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

        async def export_session_json(self) -> list[dict]:
            return deepcopy(type(self).current_export)

        async def start_recording(self) -> bool:
            return True

        async def stop_recording(self) -> bool:
            return True

        async def clear_session(self) -> bool:
            return True

        async def set_tool_enabled(self, tool: str, enabled: bool) -> bool:
            type(self).tool_calls.append(f"{tool}:{'on' if enabled else 'off'}")
            return True

    FakeClient.tool_calls = []
    return FakeClient


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake_client = _fake_client_class()
    monkeypatch.setattr(server_module, "CharlesClient", fake_client)
    charles_config = tmp_path / "charles" / "com.xk72.charles.config"
    charles_config.parent.mkdir()
    charles_config.write_text(FRESH_CHARLES_CONFIG, encoding="utf-8")

    config = Config(mock_dir=str(tmp_path / "mocks"))
    # Config.__post_init__ auto-detects the real Charles config; never touch it in tests.
    config.config_path = str(charles_config)
    config.backup_dir = str(tmp_path / "backup")
    config.state_dir = str(tmp_path / "state")
    config.dispatcher_port = _free_port()
    server = create_server(config=config)
    return server, fake_client, config


async def _capture(server, fake_client, entries: list[dict]) -> str:
    started = _tool_result(await server.call_tool("start_live_capture", {}))
    fake_client.current_export = entries
    return started["capture_id"]


async def _entry_id(server, capture_id: str, action_marker: str) -> str:
    result = _tool_result(await server.call_tool(
        "query_live_capture_entries",
        {
            "capture_id": capture_id,
            "preset": "all_http",
            "request_body_contains": action_marker,
            "max_items": 5,
        },
    ))
    assert result["items"], result
    return result["items"][0]["entry_id"]


@pytest.mark.asyncio
async def test_discover_variants_groups_one_url_by_action(env) -> None:
    server, fake_client, _ = env
    capture_id = await _capture(server, fake_client, [
        _entry("init", balance=120, second=1),
        _entry("transactions", balance=120, second=2),
        _entry("init", balance=120, second=3),
    ])

    result = _tool_result(await server.call_tool(
        "mock_discover_variants",
        {"source": "live", "capture_id": capture_id, "host_contains": "api.example.com",
         "path_contains": WALLET},
    ))

    assert result["scanned"] == 3
    assert [(group["path"], group["value_json"], group["count"]) for group in result["groups"]] == [
        (WALLET, '"init"', 2),
        (WALLET, '"transactions"', 1),
    ]
    assert all(group["response_statuses"] == [200] for group in result["groups"])


@pytest.mark.asyncio
async def test_create_patch_rule_from_entry(env) -> None:
    server, fake_client, config = env
    capture_id = await _capture(server, fake_client, [_entry("init", balance=120, second=1)])
    entry_id = await _entry_id(server, capture_id, '"init"')

    result = _tool_result(await server.call_tool(
        "mock_rule_create_from_entry",
        {
            "source": "live",
            "capture_id": capture_id,
            "entry_id": entry_id,
            "match_body_fields": ["/action"],
            "response_patches": [{"op": "set", "path": "/data/balance", "value": 0}],
        },
    ))

    rule = json.loads(result["rule_json"])
    assert rule["match"] == {
        "method": "POST",
        "path": WALLET,
        "query": {},
        "headers": {},
        "body": {"/action": "init"},
    }
    assert rule["response"]["mode"] == "patch"
    assert rule["response"]["patches"] == [{"op": "set", "path": "/data/balance", "value": 0}]
    assert result.get("fixture_file") is None  # null fields are stripped from output
    assert any("mock_route_setup" in warning for warning in result["warnings"])
    assert Path(result["rule"]["file"]).is_relative_to(Path(config.mock_dir) / "_rules")


@pytest.mark.asyncio
async def test_create_fixture_rule_keeps_original_body_and_status_override(env) -> None:
    server, fake_client, config = env
    capture_id = await _capture(server, fake_client, [_entry("payout", balance=5, second=1)])
    entry_id = await _entry_id(server, capture_id, '"payout"')

    result = _tool_result(await server.call_tool(
        "mock_rule_create_from_entry",
        {
            "source": "live",
            "capture_id": capture_id,
            "entry_id": entry_id,
            "match_body_fields": ["/action"],
            "mode": "fixture",
            "status": 500,
            "response_patches": [{"op": "set", "path": "/status", "value": "error"}],
            "rule_id": "wallet-payout-500",
        },
    ))

    fixture = json.loads(Path(result["fixture_file"]).read_text(encoding="utf-8"))
    assert fixture == {"status": "success", "data": {"balance": 5}}  # patches applied at serve time
    rule = json.loads(result["rule_json"])
    assert rule["id"] == "wallet-payout-500"
    assert rule["response"]["status"] == 500


@pytest.mark.asyncio
async def test_match_path_generalizes_a_platform_segment(env) -> None:
    server, fake_client, _ = env
    capture_id = await _capture(server, fake_client, [_entry("init", balance=1, second=1)])
    entry_id = await _entry_id(server, capture_id, '"init"')
    arguments = {
        "source": "live",
        "capture_id": capture_id,
        "entry_id": entry_id,
        "match_body_fields": ["/action"],
    }

    result = _tool_result(await server.call_tool(
        "mock_rule_create_from_entry", {**arguments, "match_path": "/api/*/wallet"}
    ))

    rule = json.loads(result["rule_json"])
    assert rule["match"]["path"] == "/api/*/wallet"
    assert rule["id"].startswith("post-api-wallet-action-init-")
    with pytest.raises(Exception, match="does not cover the entry path"):
        await server.call_tool(
            "mock_rule_create_from_entry", {**arguments, "match_path": "/other/*/wallet"}
        )


@pytest.mark.asyncio
async def test_patches_that_do_not_fit_the_capture_are_rejected(env) -> None:
    server, fake_client, _ = env
    capture_id = await _capture(server, fake_client, [_entry("init", balance=1, second=1)])
    entry_id = await _entry_id(server, capture_id, '"init"')

    with pytest.raises(Exception, match="do not fit"):
        await server.call_tool(
            "mock_rule_create_from_entry",
            {
                "source": "live",
                "capture_id": capture_id,
                "entry_id": entry_id,
                "response_patches": [{"op": "set", "path": "/nope/deeper", "value": 1}],
            },
        )


@pytest.mark.asyncio
async def test_route_setup_manual_and_apply(env, monkeypatch: pytest.MonkeyPatch) -> None:
    server, _, config = env

    manual = _tool_result(await server.call_tool(
        "mock_route_setup", {"host": "api.example.com", "path": WALLET}
    ))
    assert not manual.get("applied")
    assert any("Preserve host header" in step for step in manual["instructions"])
    assert Path(manual["route_file"]).is_file()

    monkeypatch.setattr(rule_service_module, "_port_open", lambda host, port: True)
    with pytest.raises(Exception, match="Charles is running"):
        await server.call_tool(
            "mock_route_setup", {"host": "api.example.com", "path": WALLET, "apply": True}
        )
    assert Path(config.config_path).read_text(encoding="utf-8") == FRESH_CHARLES_CONFIG

    monkeypatch.setattr(rule_service_module, "_port_open", lambda host, port: False)
    applied = _tool_result(await server.call_tool(
        "mock_route_setup", {"host": "api.example.com", "path": WALLET, "apply": True}
    ))
    assert applied["applied"] and applied["mapping_added"]
    assert "<preserveHostHeader>true</preserveHostHeader>" in Path(config.config_path).read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_rule_list_get_toggle_remove(env) -> None:
    server, fake_client, _ = env
    capture_id = await _capture(server, fake_client, [_entry("init", balance=1, second=1)])
    entry_id = await _entry_id(server, capture_id, '"init"')
    await server.call_tool("mock_route_setup", {"host": "api.example.com", "path": WALLET})
    created = _tool_result(await server.call_tool(
        "mock_rule_create_from_entry",
        {"source": "live", "capture_id": capture_id, "entry_id": entry_id,
         "match_body_fields": ["/action"], "rule_id": "wallet-init"},
    ))
    assert created["warnings"] == []

    listed = _tool_result(await server.call_tool("mock_rule_list", {}))
    assert listed["routes"] == [f"api.example.com{WALLET}"]
    assert [item["id"] for item in listed["items"]] == ["wallet-init"]

    toggled = _tool_result(await server.call_tool(
        "mock_rule_set_enabled", {"host": "*", "rule_id": "wallet-init", "enabled": False}
    ))
    assert toggled["enabled"] is False
    shown = _tool_result(await server.call_tool(
        "mock_rule_get", {"host": "*", "rule_id": "wallet-init"}
    ))
    assert json.loads(shown["rule_json"])["enabled"] is False

    removed = _tool_result(await server.call_tool(
        "mock_rule_remove", {"host": "*", "rule_id": "wallet-init"}
    ))
    assert Path(removed["archived_to"]).is_dir()
    assert _tool_result(await server.call_tool("mock_rule_list", {}))["total"] == 0


@pytest.mark.asyncio
async def test_route_setup_warns_when_tls_verification_is_disabled(env) -> None:
    server, _, _ = env

    result = _tool_result(await server.call_tool(
        "mock_route_setup",
        {"host": "dev.example.com", "path": "/api/*", "verify_tls": False},
    ))
    assert any("TLS verification is OFF" in warning for warning in result["warnings"])

    listed = _tool_result(await server.call_tool("mock_rule_list", {}))
    assert "dev.example.com/api/* (verify_tls=false)" in listed["routes"]


def test_merge_patches_drops_a_patch_that_aliases_a_new_one() -> None:
    existing = [
        {"op": "set", "path": "/data/balanceList/1/balance", "value": "0.00"},
        {"op": "set", "path": "/data/note", "value": "keep"},
    ]
    new = [{"op": "set", "path": "/data/balanceList/[currency=USD]/balance", "value": "1000.00"}]

    merged, aliased = rule_service_module.merge_patches(existing, new)

    paths = [patch["path"] for patch in merged]
    assert "/data/balanceList/1/balance" not in paths  # stale index patch dropped
    assert "/data/note" in paths  # unrelated patch kept
    assert paths[-1] == "/data/balanceList/[currency=USD]/balance"
    assert aliased == ["/data/balanceList/1/balance"]


@pytest.mark.asyncio
async def test_end_to_end_capture_rule_dispatcher(env) -> None:
    server, fake_client, config = env
    capture_id = await _capture(server, fake_client, [
        _entry("init", balance=120, second=1, host="localhost"),
    ])
    entry_id = await _entry_id(server, capture_id, '"init"')
    await server.call_tool(
        "mock_rule_create_from_entry",
        {"source": "live", "capture_id": capture_id, "entry_id": entry_id,
         "match_body_fields": ["/action"],
         "response_patches": [{"op": "set", "path": "/data/balance", "value": 0}],
         "rule_id": "local-init"},
    )

    with FakeUpstream() as upstream:
        RuleStore(config.mock_dir).save_route(
            RouteConfig(host="localhost", upstream_scheme="http", upstream_port=upstream.port,
                        paths=[WALLET])
        )
        started = _tool_result(await server.call_tool("mock_dispatcher", {"action": "start"}))
        try:
            assert started["running"]

            def post(action: str) -> tuple[str | None, dict]:
                connection = HTTPConnection("127.0.0.1", config.dispatcher_port, timeout=5)
                connection.request("POST", WALLET, body=json.dumps({"action": action}),
                                   headers={"Host": "localhost", "Content-Type": "application/json"})
                response = connection.getresponse()
                payload = json.loads(response.read())
                connection.close()
                return response.getheader("X-Charles-MCP-Rule"), payload

            rule_id, mocked = post("init")
            passthrough_id, real = post("transactions")
        finally:
            stopped = _tool_result(await server.call_tool("mock_dispatcher", {"action": "stop"}))

    assert (rule_id, mocked["data"]["balance"]) == ("local-init", 0)
    assert (passthrough_id, real["data"]["balance"]) == ("passthrough", 120)
    assert stopped["running"] is False
    # Map Remote follows the dispatcher so routed traffic never hits a stopped one.
    assert fake_client.tool_calls == ["map-remote:on", "map-remote:off"]
    assert "Map Remote enabled" in started["message"]
    assert "Map Remote disabled" in stopped["message"]


def test_probe_target_builds_an_unresolvable_host_and_a_covered_path() -> None:
    glob_route = RouteConfig(host="*.example.com", paths=["/api/*"])
    exact_route = RouteConfig(host="api.example.com", paths=["/api/v1/wallet"])

    # A glob becomes a name that cannot resolve, so an unmapped probe never
    # reaches the real server; the path stays under the routed prefix.
    assert rule_service_module.probe_target(glob_route, "/api/*") == (
        "charles-mcp-probe.example.com",
        "/api/__charles-mcp/health",
    )
    assert rule_service_module.probe_target(glob_route, "/*") == (
        "charles-mcp-probe.example.com",
        "/__charles-mcp/health",
    )
    # An exact-path route leaves no room for a probe path.
    assert rule_service_module.probe_target(exact_route, "/api/v1/wallet") is None


@pytest.mark.asyncio
async def test_verify_reports_a_stopped_dispatcher_without_probing_charles(env) -> None:
    server, _, config = env
    RuleStore(config.mock_dir).save_route(RouteConfig(host="*.example.com", paths=["/api/*"]))

    result = _tool_result(await server.call_tool("mock_dispatcher", {"action": "verify"}))

    assert result.get("running") is not True
    assert "does not answer" in result["message"]
    # No dispatcher means no end-to-end probe through the user's real Charles.
    assert "start the dispatcher first" in result["message"]
    assert "example.com" not in result["message"]


@pytest.mark.asyncio
async def test_verify_probes_every_route_once_the_dispatcher_answers(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, _, config = env
    store = RuleStore(config.mock_dir)
    store.save_route(RouteConfig(host="*.example.com", paths=["/api/*"]))
    store.save_route(RouteConfig(host="stage.example.com", paths=["/v2/*"]))

    async def fake_direct(self, port: int):
        return True, f"the dispatcher answers on {port}"

    probed: list[str] = []

    async def fake_through_charles(self, route, path_pattern: str) -> str:
        probed.append(f"{route.host}{path_pattern}")
        return f"{route.host}{path_pattern}: Charles routes it to the dispatcher"

    monkeypatch.setattr(rule_service_module.RuleService, "_probe_dispatcher", fake_direct)
    monkeypatch.setattr(
        rule_service_module.RuleService, "_probe_through_charles", fake_through_charles
    )

    result = _tool_result(await server.call_tool("mock_dispatcher", {"action": "verify"}))

    assert probed == ["*.example.com/api/*", "stage.example.com/v2/*"]
    assert "Charles routes it to the dispatcher" in result["message"]


@pytest.mark.asyncio
async def test_a_fixture_stores_the_captured_bytes_unchanged(env) -> None:
    """A stored fixture must be the capture, not a tidied copy of it."""
    server, fake_client, config = env
    capture_id = await _capture(server, fake_client, [_entry("init", balance=120, second=1)])
    entry_id = await _entry_id(server, capture_id, '"init"')

    await server.call_tool(
        "mock_rule_create_from_entry",
        {
            "source": "live",
            "capture_id": capture_id,
            "entry_id": entry_id,
            "mode": "fixture",
            "match_body_fields": ["/action"],
            "rule_id": "verbatim",
        },
    )

    captured = json.dumps({"status": "success", "data": {"balance": 120}})
    stored = (Path(config.mock_dir) / "_rules" / "_any" / "verbatim.body").read_bytes()
    assert stored.decode("utf-8") == captured
    # Nothing reformatted: no indentation, no added trailing newline.
    assert b"\n" not in stored


@pytest.mark.asyncio
async def test_a_patch_that_changes_a_number_type_warns(env) -> None:
    server, fake_client, _config = env
    capture_id = await _capture(server, fake_client, [_entry("init", balance=120, second=1)])
    entry_id = await _entry_id(server, capture_id, '"init"')

    created = _tool_result(
        await server.call_tool(
            "mock_rule_create_from_entry",
            {
                "source": "live",
                "capture_id": capture_id,
                "entry_id": entry_id,
                "match_body_fields": ["/action"],
                "response_patches": [{"op": "set", "path": "/data/balance", "value": 1000.5}],
                "rule_id": "typed",
            },
        )
    )
    assert any("keep the type" in warning for warning in created["warnings"])


def _binary_entry(payload: bytes, *, gzipped: bool, second: int = 1) -> dict:
    import base64
    import gzip as gzip_module

    wire = gzip_module.compress(payload) if gzipped else payload
    headers = [{"name": "Content-Type", "value": "application/x-protobuf"}]
    if gzipped:
        headers.append({"name": "Content-Encoding", "value": "gzip"})
    entry = _entry("init", balance=0, second=second)
    entry["response"] = {
        "status": 200,
        "mimeType": "application/x-protobuf",
        "header": {"firstLine": "HTTP/1.1 200 OK", "headers": headers},
        "body": {"text": base64.b64encode(wire).decode(), "encoded": True},
    }
    if gzipped:
        entry["response"]["contentEncoding"] = "gzip"
    return entry


@pytest.mark.asyncio
@pytest.mark.parametrize("gzipped", [False, True])
async def test_a_binary_response_becomes_a_byte_exact_fixture(env, gzipped: bool) -> None:
    server, fake_client, config = env
    payload = bytes(range(256)) * 4  # not valid UTF-8, so no text path can carry it
    capture_id = await _capture(server, fake_client, [_binary_entry(payload, gzipped=gzipped)])
    entry_id = await _entry_id(server, capture_id, '"init"')

    created = _tool_result(
        await server.call_tool(
            "mock_rule_create_from_entry",
            {
                "source": "live",
                "capture_id": capture_id,
                "entry_id": entry_id,
                "mode": "fixture",
                "match_body_fields": ["/action"],
                "rule_id": "proto",
            },
        )
    )
    stored = (Path(config.mock_dir) / "_rules" / "_any" / "proto.body").read_bytes()
    # Decompressed: the dispatcher never sends Content-Encoding, so bytes left
    # gzipped would reach the app undecodable.
    assert stored == payload
    rule = json.loads(
        _tool_result(await server.call_tool("mock_rule_get", {"host": "*", "rule_id": "proto"}))[
            "rule_json"
        ]
    )
    assert rule["response"]["headers"]["Content-Type"] == "application/x-protobuf"
    # The only expected warning is about the route this test does not set up.
    assert all("no route" in warning for warning in created["warnings"])


@pytest.mark.asyncio
async def test_a_binary_response_refuses_patches(env) -> None:
    server, fake_client, _config = env
    capture_id = await _capture(server, fake_client, [_binary_entry(b"\x00\x01", gzipped=False)])
    entry_id = await _entry_id(server, capture_id, '"init"')

    with pytest.raises(Exception, match="binary response"):
        await server.call_tool(
            "mock_rule_create_from_entry",
            {
                "source": "live",
                "capture_id": capture_id,
                "entry_id": entry_id,
                "mode": "fixture",
                "match_body_fields": ["/action"],
                "response_patches": [{"op": "set", "path": "/x", "value": 1}],
                "rule_id": "proto-patched",
            },
        )


@pytest.mark.asyncio
async def test_a_hand_written_binary_fixture_is_stored_from_base64(env) -> None:
    import base64

    server, _fake_client, config = env
    payload = b"\x08\x96\x01"  # a protobuf varint field
    await server.call_tool(
        "mock_rule_write",
        {
            "rule": {
                "id": "hand-proto",
                "host": "*",
                "match": {"method": "POST", "path": "/api/v1/wallet"},
                "response": {"mode": "fixture", "headers": {"Content-Type": "application/x-protobuf"}},
            },
            "fixture_base64": base64.b64encode(payload).decode(),
        },
    )
    stored = (Path(config.mock_dir) / "_rules" / "_any" / "hand-proto.body").read_bytes()
    assert stored == payload
