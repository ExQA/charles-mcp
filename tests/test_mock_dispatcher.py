"""Dispatcher tests against a fake upstream on localhost (synthetic data only)."""

from __future__ import annotations

import gzip
import json
import socket
import threading
import time
from collections.abc import Iterator
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from charles_mcp.mocks.dispatcher import (
    RULE_HEADER,
    WARNING_HEADER,
    DispatcherServer,
    DispatcherSettings,
)
from charles_mcp.mocks.rules import MockRule, RouteConfig, RuleStore


class FakeUpstream:
    """Echo server: answers with the action it received and records request bodies."""

    def __init__(self) -> None:
        self.bodies: list[bytes] = []
        self.header_sets: list[dict[str, str]] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                upstream.bodies.append(body)
                upstream.header_sets.append({k.lower(): v for k, v in self.headers.items()})
                try:
                    action = json.loads(body or b"{}").get("action")
                except ValueError:
                    action = parse_qs(body.decode()).get("action", [None])[0]
                payload = {"action": action, "data": {"balance": 120, "currency": "USD"}}
                raw = json.dumps(payload).encode()
                gzip_it = self.path.startswith("/gzip")
                if gzip_it:
                    raw = gzip.compress(raw)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", "a=1")
                self.send_header("Set-Cookie", "b=2")
                if gzip_it:
                    self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_HEAD = do_POST  # noqa: N815

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self) -> FakeUpstream:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def env(tmp_path: Path) -> Iterator[tuple[RuleStore, FakeUpstream, int]]:
    store = RuleStore(tmp_path)
    with FakeUpstream() as upstream:
        store.save_route(
            RouteConfig(
                host="localhost",
                upstream_scheme="http",
                upstream_port=upstream.port,
                paths=["/api/wallet"],
            )
        )
        dispatcher = DispatcherServer(("127.0.0.1", 0), DispatcherSettings(mock_dir=str(tmp_path)))
        thread = threading.Thread(target=dispatcher.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield store, upstream, dispatcher.server_port
        finally:
            dispatcher.shutdown()
            dispatcher.server_close()


def _post(port: int, body: object, *, path: str = "/api/wallet", host: str = "localhost"):
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    raw = json.dumps(body).encode()
    connection.request(
        "POST", path, body=raw, headers={"Host": host, "Content-Type": "application/json"}
    )
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response, data


def _save(store: RuleStore, document: dict, fixture: bytes | None = None) -> None:
    rule = MockRule.model_validate(
        {"host": "localhost", "match": {"method": "POST", "path": "/api/wallet"}, **document}
    )
    store.save_rule(rule, fixture)


def test_unknown_host_is_refused(env) -> None:
    _, upstream, port = env

    response, data = _post(port, {"action": "init"}, host="elsewhere.example.com")

    assert response.status == 421
    assert "not routed" in json.loads(data)["error"]
    assert upstream.bodies == []


def test_no_rule_passes_through_unchanged(env) -> None:
    _, upstream, port = env

    response, data = _post(port, {"action": "init"})

    assert response.status == 200
    assert response.getheader(RULE_HEADER) == "passthrough"
    assert json.loads(data)["data"]["balance"] == 120
    assert response.msg.get_all("Set-Cookie") == ["a=1", "b=2"]
    # Upstream Server/Date pass through once, not doubled by the dispatcher.
    assert len(response.msg.get_all("Server") or []) == 1
    assert "charles-mcp" not in (response.getheader("Server") or "")
    assert len(response.msg.get_all("Date") or []) == 1
    assert upstream.bodies == [b'{"action": "init"}']


def test_patch_rule_edits_only_the_matching_action(env) -> None:
    store, _, port = env
    _save(
        store,
        {
            "id": "wallet-init",
            "match": {"method": "POST", "path": "/api/wallet", "body": {"/action": "init"}},
            "response": {"mode": "patch", "patches": [{"op": "set", "path": "/data/balance", "value": 0}]},
        },
    )

    init, init_body = _post(port, {"action": "init"})
    other, other_body = _post(port, {"action": "transactions"})

    assert init.getheader(RULE_HEADER) == "wallet-init"
    assert json.loads(init_body)["data"] == {"balance": 0, "currency": "USD"}
    assert other.getheader(RULE_HEADER) == "passthrough"
    assert json.loads(other_body)["data"]["balance"] == 120


def test_request_patches_change_what_upstream_receives(env) -> None:
    store, upstream, port = env
    _save(
        store,
        {
            "id": "rewrite-request",
            "match": {"method": "POST", "path": "/api/wallet", "body": {"/action": "init"}},
            "request": {"patches": [{"op": "set", "path": "/currency", "value": "EUR"}]},
            "response": {"mode": "patch", "status": 503},
        },
    )

    response, _ = _post(port, {"action": "init"})

    assert response.status == 503
    assert json.loads(upstream.bodies[-1]) == {"action": "init", "currency": "EUR"}


def test_request_header_edits_set_and_remove_before_forwarding(env) -> None:
    store, upstream, port = env
    _save(
        store,
        {
            "id": "rewrite-headers",
            "match": {"method": "POST", "path": "/api/wallet", "body": {"/action": "init"}},
            "request": {
                "headers": {
                    "X-App-Version": "9.9.9",  # add
                    "Content-Type": "application/json; v=2",  # replace
                    "X-Drop-Me": None,  # remove
                    "Host": "evil.example.com",  # refused (dispatcher-managed)
                }
            },
            "response": {"mode": "patch"},
        },
    )

    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(
        "POST",
        "/api/wallet",
        body=json.dumps({"action": "init"}).encode(),
        headers={"Host": "localhost", "Content-Type": "application/json", "X-Drop-Me": "1"},
    )
    response = connection.getresponse()
    warning = response.getheader("X-Charles-MCP-Warning")
    response.read()
    connection.close()

    sent = upstream.header_sets[-1]
    assert sent["x-app-version"] == "9.9.9"
    assert sent["content-type"] == "application/json; v=2"
    assert "x-drop-me" not in sent
    # The Host override is refused: upstream still sees the real target host.
    assert sent["host"].startswith("localhost")
    assert warning is not None and "Host" in warning


def test_response_delay_is_applied(env) -> None:
    store, _, port = env
    _save(
        store,
        {
            "id": "slow-init",
            "match": {"method": "POST", "path": "/api/wallet", "body": {"/action": "init"}},
            "response": {"mode": "patch", "delay_ms": 300},
        },
    )

    started = time.monotonic()
    response, _ = _post(port, {"action": "init"})
    elapsed = time.monotonic() - started

    assert response.status == 200
    assert elapsed >= 0.3


def test_fixture_rule_never_calls_upstream(env) -> None:
    store, upstream, port = env
    _save(
        store,
        {
            "id": "wallet-error",
            "match": {"method": "POST", "path": "/api/wallet", "body": {"/action": "payout"}},
            "response": {
                "mode": "fixture",
                "status": 500,
                "headers": {"X-Debug": "mock"},
                "patches": [{"op": "set", "path": "/error/code", "value": "LIMIT"}],
            },
        },
        fixture=b'{"error": {"code": "UNKNOWN", "message": "synthetic"}}',
    )

    response, data = _post(port, {"action": "payout"})

    assert response.status == 500
    assert response.getheader("Server") == "charles-mcp-dispatcher"
    assert response.getheader("Date")
    assert response.getheader("X-Debug") == "mock"
    assert response.getheader("Content-Type") == "application/json; charset=utf-8"
    assert json.loads(data) == {"error": {"code": "LIMIT", "message": "synthetic"}}
    assert upstream.bodies == []


def test_patch_failure_is_reported_not_fatal(env) -> None:
    store, _, port = env
    _save(
        store,
        {
            "id": "bad-patch",
            "response": {"mode": "patch", "patches": [{"op": "set", "path": "/missing/x", "value": 1}]},
        },
    )

    response, data = _post(port, {"action": "init"})

    assert response.status == 200
    assert "patch-failed" in (response.getheader(WARNING_HEADER) or "")
    assert json.loads(data)["data"]["balance"] == 120


def test_gzip_upstream_is_decoded_before_patching(env) -> None:
    store, _, port = env
    store.save_route(
        RouteConfig(
            host="localhost",
            upstream_scheme="http",
            upstream_port=store.get_route("localhost").upstream_port,  # type: ignore[union-attr]
            paths=["/api/wallet", "/gzip"],
        )
    )
    rule = MockRule.model_validate(
        {
            "id": "gzip",
            "host": "localhost",
            "match": {"path": "/gzip"},
            "response": {"mode": "patch", "patches": [{"op": "set", "path": "/data/balance", "value": 7}]},
        }
    )
    store.save_rule(rule)

    response, data = _post(port, {"action": "x"}, path="/gzip")

    assert response.getheader("Content-Encoding") is None
    assert json.loads(data)["data"]["balance"] == 7


def test_chunked_request_is_routed_by_action(env) -> None:
    store, _, port = env
    _save(
        store,
        {
            "id": "chunked-init",
            "match": {"path": "/api/wallet", "body": {"/action": "init"}},
            "response": {"mode": "fixture"},
        },
        fixture=b'{"ok": true}',
    )
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    body = b'{"scheme":1,"action":"init"}'
    connection.putrequest("POST", "/api/wallet", skip_host=True)
    connection.putheader("Host", "localhost")
    connection.putheader("Transfer-Encoding", "chunked")
    connection.endheaders()
    for chunk in (body[:10], body[10:]):
        connection.send(f"{len(chunk):X};ext=1\r\n".encode() + chunk + b"\r\n")
    connection.send(b"0\r\nX-Trailer: done\r\n\r\n")
    response = connection.getresponse()

    assert response.getheader(RULE_HEADER) == "chunked-init"
    assert json.loads(response.read()) == {"ok": True}
    connection.close()


def test_keep_alive_survives_an_early_error_response(env) -> None:
    _, _, port = env
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    body = json.dumps({"action": "init"}).encode()

    connection.request("POST", "/api/wallet", body=body, headers={"Host": "unknown.example.com"})
    first = connection.getresponse()
    first.read()
    connection.request("POST", "/api/wallet", body=body, headers={"Host": "localhost"})
    second = connection.getresponse()

    assert first.status == 421
    assert second.status == 200
    assert json.loads(second.read())["action"] == "init"
    connection.close()


def test_get_without_content_length_is_forwarded(env) -> None:
    _, _, port = env
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("HEAD", "/api/wallet", headers={"Host": "localhost"})
    response = connection.getresponse()

    assert response.status == 200
    assert response.read() == b""
    connection.close()


def test_unreachable_upstream_returns_502(tmp_path: Path) -> None:
    store = RuleStore(tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    store.save_route(RouteConfig(host="localhost", upstream_scheme="http", upstream_port=dead_port))
    dispatcher = DispatcherServer(("127.0.0.1", 0), DispatcherSettings(mock_dir=str(tmp_path)))
    thread = threading.Thread(target=dispatcher.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        response, data = _post(dispatcher.server_port, {"action": "init"})
    finally:
        dispatcher.shutdown()
        dispatcher.server_close()

    assert response.status == 502
    assert "upstream request failed" in json.loads(data)["error"]


def test_request_patches_edit_form_bodies(env) -> None:
    store, upstream, port = env
    _save(
        store,
        {
            "id": "form-submit",
            "match": {"method": "POST", "path": "/api/wallet", "body": {"/action": "submit"}},
            "request": {"patches": [{"op": "set", "path": "/lang", "value": "en"}]},
            "response": {"mode": "patch"},
        },
    )
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(
        "POST",
        "/api/wallet",
        body=b"action=submit&lang=uk",
        headers={"Host": "localhost", "Content-Type": "application/x-www-form-urlencoded"},
    )
    response = connection.getresponse()
    response.read()
    connection.close()

    assert response.getheader(RULE_HEADER) == "form-submit"
    assert response.getheader(WARNING_HEADER) is None
    assert parse_qs(upstream.bodies[-1].decode()) == {"action": ["submit"], "lang": ["en"]}


def test_health_probe_is_answered_locally_and_never_forwarded(env) -> None:
    _, upstream, port = env

    for path in ("/__charles-mcp/health", "/api/wallet/__charles-mcp/health"):
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", path, headers={"Host": "localhost"})
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()

        assert response.status == 200
        assert response.getheader("X-Charles-MCP-Rule") == "health"
        assert payload["dispatcher"] == "charles-mcp"
        assert payload["probe_path"] == path

    # A probe for a host without any route still proves the dispatcher was reached.
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", "/__charles-mcp/health", headers={"Host": "elsewhere.example.com"})
    response = connection.getresponse()
    response.read()
    connection.close()

    assert response.status == 200
    assert upstream.bodies == []  # nothing was forwarded upstream
