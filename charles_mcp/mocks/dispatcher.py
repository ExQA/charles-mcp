"""Body-aware HTTP dispatcher: the Charles Map Remote target for mocked routes.

Charles maps ``https://<host or *.domain><path pattern>`` to
``http://127.0.0.1:<port>`` with "preserve host header" and no destination path,
so the dispatcher sees the original ``Host`` and path. For each request it loads
the host's own rules plus the matching host-pattern rules (see ``rules.py``):

- a matching ``fixture`` rule is answered from the stored body;
- a matching ``patch`` rule is forwarded upstream (optionally with request
  edits) and the real response is edited before it is returned;
- anything else is forwarded upstream unchanged, so unmocked actions keep
  working.

Only requests covered by a route in ``routes.json`` are forwarded, so the
dispatcher cannot be used to reach arbitrary hosts. Every response carries
``X-Charles-MCP-Rule: <rule id | passthrough>`` for verification. Bodies are
never logged.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from charles_mcp.mocks.json_patch import JsonPatchError, apply_patches
from charles_mcp.mocks.rules import (
    IncomingRequest,
    MockRule,
    RouteConfig,
    RuleStore,
    select_rule,
)
from charles_mcp.mocks.store import MockPathError, normalize_host

logger = logging.getLogger(__name__)

RULE_HEADER = "X-Charles-MCP-Rule"
WARNING_HEADER = "X-Charles-MCP-Warning"
DEFAULT_PORT = 18080
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
_DROP_REQUEST_HEADERS = _HOP_BY_HOP | {"host", "content-length"}
# httpx returns decoded content, so the upstream encoding/length no longer apply.
_DROP_RESPONSE_HEADERS = _HOP_BY_HOP | {"content-length", "content-encoding"}
# Answered by the dispatcher itself, never forwarded: proves a request reached it.
HEALTH_SUFFIX = "/__charles-mcp/health"
_MAX_LINE = 65536


@dataclass(frozen=True)
class DispatcherSettings:
    mock_dir: str
    timeout_seconds: float = 20.0
    max_body_bytes: int = 5_000_000


class DispatcherServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], settings: DispatcherSettings) -> None:
        super().__init__(address, _DispatcherHandler)
        # httpx logs every request URL at INFO, query string included, and query
        # strings of mobile APIs often carry tokens. Keep them out of the logs.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        self.settings = settings
        self.store = RuleStore(settings.mock_dir)
        self._clients: dict[bool, httpx.Client] = {}
        self._clients_lock = threading.Lock()

    def upstream_client(self, verify_tls: bool) -> httpx.Client:
        with self._clients_lock:
            client = self._clients.get(verify_tls)
            if client is None:
                # trust_env=False: never route the upstream call back through the
                # system proxy (Charles), which would loop into the dispatcher.
                client = httpx.Client(
                    trust_env=False,
                    verify=verify_tls,
                    timeout=self.settings.timeout_seconds,
                    follow_redirects=False,
                )
                self._clients[verify_tls] = client
            return client

    def server_close(self) -> None:
        super().server_close()
        with self._clients_lock:
            for client in self._clients.values():
                client.close()
            self._clients.clear()


def _patch_json(
    data: bytes, patches: list[dict[str, Any]], content_type: str = ""
) -> tuple[bytes, str | None]:
    """Apply patches to a JSON body, or to a form body when the content type says so."""
    is_form = "application/x-www-form-urlencoded" in content_type.lower()
    try:
        text = data.decode("utf-8")
        document: Any = json.loads(text)
        is_form = False
    except (UnicodeDecodeError, ValueError):
        if not is_form:
            return data, "body-not-json-patches-skipped"
        parsed = parse_qs(text, keep_blank_values=True)
        document = {key: values[0] if len(values) == 1 else values for key, values in parsed.items()}
    try:
        patched = apply_patches(document, patches)
    except JsonPatchError as exc:
        return data, f"patch-failed: {exc}"
    if is_form:
        return urlencode(patched, doseq=True).encode("utf-8"), None
    return json.dumps(patched, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), None


def _header_safe(value: str) -> str:
    return value.encode("ascii", errors="replace").decode("ascii").replace("\r", " ").replace(
        "\n", " "
    )


class _DispatcherHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: DispatcherServer

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = do_GET  # noqa: N815

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    # ---- main flow ------------------------------------------------------------

    def _dispatch(self) -> None:
        body, error = self._read_body()
        if error is not None:
            self.close_connection = True
            self._send_error_json(*error)
            return
        assert body is not None

        split = urlsplit(self.path)
        if is_health_path(split.path):
            # Answered here so the probe proves Charles routed the request to the
            # dispatcher, even for a host that has no route yet.
            self._send_health(split.path)
            return

        try:
            host = normalize_host(self.headers.get("Host", "").rsplit(":", 1)[0])
        except MockPathError:
            self._send_error_json(421, "Host header is missing or invalid")
            return
        route = self.server.store.find_route(host, split.path)
        if route is None:
            self._send_error_json(
                421, f"{host}{split.path} is not routed to the dispatcher; run mock_route_setup"
            )
            return

        request = IncomingRequest(
            method=self.command,
            path=split.path,
            query=parse_qs(split.query, keep_blank_values=True),
            headers={name.lower(): value for name, value in self.headers.items()},
            body=body,
            content_type=self.headers.get("Content-Type", ""),
            host=host,
        )
        rules, errors = self.server.store.rules_for_host(host)
        for problem in errors:
            logger.warning("skipping invalid rule file for %s: %s", host, problem)
        rule = select_rule(rules, request)

        if rule is not None and rule.response.mode == "fixture":
            self._serve_fixture(host, rule)
        else:
            self._forward(route, host, request, rule)

    def _send_health(self, path: str) -> None:
        routes = self.server.store.list_routes()
        payload = {
            "dispatcher": "charles-mcp",
            "probe_path": path,
            "mock_dir": str(self.server.store.root),
            "routes": [f"{route.host}{pattern}" for route in routes for pattern in route.paths],
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = [("Content-Type", "application/json; charset=utf-8")]
        self._send(200, headers, body, "health", [])

    def _serve_fixture(self, host: str, rule: MockRule) -> None:
        try:
            # The fixture lives next to the rule, which may be a host-pattern rule.
            body = self.server.store.read_fixture(rule.host, rule.id)
        except FileNotFoundError as exc:
            self._send_error_json(500, str(exc), rule_id=rule.id)
            return
        warnings: list[str] = []
        if rule.response.patches:
            body, warning = _patch_json(body, rule.response.patches)
            if warning:
                warnings.append(warning)
        headers = [("Content-Type", "application/json; charset=utf-8")]
        headers = _override_headers(headers, rule.response.headers)
        _delay(rule)
        self._send(rule.response.status or 200, headers, body, rule.id, warnings)

    def _forward(
        self,
        route: RouteConfig,
        host: str,
        request: IncomingRequest,
        rule: MockRule | None,
    ) -> None:
        warnings: list[str] = []
        body = request.body
        if rule is not None and rule.request.patches:
            body, warning = _patch_json(
                body, rule.request.patches, self.headers.get("Content-Type", "")
            )
            if warning:
                warnings.append(f"request-{warning}")

        default_port = 443 if route.upstream_scheme == "https" else 80
        port = "" if route.upstream_port == default_port else f":{route.upstream_port}"
        url = f"{route.upstream_scheme}://{host}{port}{self.path}"
        headers = [
            (name, value)
            for name, value in self.headers.items()
            if name.lower() not in _DROP_REQUEST_HEADERS
        ]
        if rule is not None and rule.request.headers:
            headers, header_warning = _edit_request_headers(headers, rule.request.headers)
            if header_warning:
                warnings.append(header_warning)
        try:
            upstream = self.server.upstream_client(route.verify_tls).request(
                self.command, url, headers=headers, content=body
            )
        except httpx.HTTPError as exc:
            logger.warning("upstream %s %s failed: %s", self.command, request.path, exc)
            self._send_error_json(
                502,
                f"upstream request failed: {type(exc).__name__}",
                rule_id=rule.id if rule else None,
            )
            return

        content = upstream.content
        status = upstream.status_code
        response_headers = [
            (name, value)
            for name, value in upstream.headers.multi_items()
            if name.lower() not in _DROP_RESPONSE_HEADERS
        ]
        if rule is not None:
            if rule.response.patches:
                content, warning = _patch_json(content, rule.response.patches)
                if warning:
                    warnings.append(warning)
            if rule.response.status:
                status = rule.response.status
            response_headers = _override_headers(response_headers, rule.response.headers)
            _delay(rule)
        self._send(status, response_headers, content, rule.id if rule else "passthrough", warnings)

    # ---- I/O ------------------------------------------------------------------

    def _read_body(self) -> tuple[bytes | None, tuple[int, str] | None]:
        transfer_encoding = self.headers.get("Transfer-Encoding", "")
        if any(value.strip().lower() == "chunked" for value in transfer_encoding.split(",")):
            return self._read_chunked_body()

        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b"", None
        try:
            length = int(raw_length)
        except ValueError:
            return None, (400, "Invalid Content-Length")
        if length < 0:
            return None, (400, "Invalid Content-Length")
        if length > self.server.settings.max_body_bytes:
            return None, (413, "Request body too large")
        body = self.rfile.read(length)
        if len(body) != length:
            return None, (400, "Incomplete request body")
        return body, None

    def _read_chunked_body(self) -> tuple[bytes | None, tuple[int, str] | None]:
        chunks: list[bytes] = []
        total = 0
        limit = self.server.settings.max_body_bytes
        while True:
            size_line = self.rfile.readline(_MAX_LINE + 1)
            if not size_line or len(size_line) > _MAX_LINE or not size_line.endswith(b"\r\n"):
                return None, (400, "Malformed chunk size")
            try:
                size = int(size_line[:-2].split(b";", 1)[0].strip(), 16)
            except ValueError:
                return None, (400, "Malformed chunk size")
            if size < 0:
                return None, (400, "Malformed chunk size")
            if size == 0:
                while True:
                    trailer = self.rfile.readline(_MAX_LINE + 1)
                    if not trailer or len(trailer) > _MAX_LINE or not trailer.endswith(b"\r\n"):
                        return None, (400, "Malformed chunk trailer")
                    if trailer == b"\r\n":
                        return b"".join(chunks), None
            if total + size > limit:
                return None, (413, "Request body too large")
            chunk = self.rfile.read(size)
            if len(chunk) != size or self.rfile.read(2) != b"\r\n":
                return None, (400, "Malformed chunk data")
            chunks.append(chunk)
            total += size

    def _send_error_json(self, status: int, message: str, *, rule_id: str | None = None) -> None:
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        headers = [("Content-Type", "application/json; charset=utf-8")]
        self._send(status, headers, body, rule_id or "error", [])

    def _send(
        self,
        status: int,
        headers: list[tuple[str, str]],
        body: bytes,
        rule_label: str,
        warnings: list[str],
    ) -> None:
        # send_response_only: BaseHTTPRequestHandler would otherwise add its own
        # Server and Date on top of the upstream ones.
        self.send_response_only(status)
        present = {name.lower() for name, _ in headers}
        if "date" not in present:
            self.send_header("Date", self.date_time_string())
        if "server" not in present:
            self.send_header("Server", "charles-mcp-dispatcher")
        for name, value in headers:
            self.send_header(name, _header_safe(value))
        self.send_header("Content-Length", str(len(body)))
        self.send_header(RULE_HEADER, rule_label)
        for warning in warnings:
            self.send_header(WARNING_HEADER, _header_safe(warning))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        logger.info(
            "dispatch method=%s path=%s rule=%s status=%d bytes=%d",
            self.command,
            urlsplit(self.path).path,
            rule_label,
            status,
            len(body),
        )


def is_health_path(path: str) -> bool:
    """A probe path: the health suffix, optionally under a route prefix."""
    return path.rstrip("/").endswith(HEALTH_SUFFIX)


def _override_headers(
    headers: list[tuple[str, str]], overrides: dict[str, str]
) -> list[tuple[str, str]]:
    if not overrides:
        return headers
    lowered = {name.lower() for name in overrides}
    kept = [(name, value) for name, value in headers if name.lower() not in lowered]
    return kept + list(overrides.items())


def _edit_request_headers(
    headers: list[tuple[str, str]], edits: dict[str, str | None]
) -> tuple[list[tuple[str, str]], str | None]:
    """Apply per-rule request-header edits: a value sets/replaces a header, None
    removes it. Dispatcher-managed headers (Host, Content-Length, hop-by-hop) are
    refused so routing and framing stay intact."""
    refused = sorted(name for name in edits if name.lower() in _DROP_REQUEST_HEADERS)
    applied = {name: value for name, value in edits.items() if name.lower() not in _DROP_REQUEST_HEADERS}
    lowered = {name.lower() for name in applied}
    kept = [(name, value) for name, value in headers if name.lower() not in lowered]
    added = [(name, value) for name, value in applied.items() if value is not None]
    warning = (
        f"request header edits ignored for {', '.join(refused)}: managed by the dispatcher"
        if refused
        else None
    )
    return kept + added, warning


def _delay(rule: MockRule) -> None:
    if rule.response.delay_ms:
        time.sleep(rule.response.delay_ms / 1000)


class DispatcherThread:
    """Runs the dispatcher inside the MCP server process."""

    def __init__(self, settings: DispatcherSettings, host: str, port: int) -> None:
        self.settings = settings
        self.host = host
        self.port = port
        self._server: DispatcherServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def address(self) -> str:
        port = self._server.server_port if self._server else self.port
        return f"http://{self.host}:{port}"

    def start(self) -> None:
        if self.running:
            return
        self._server = DispatcherServer((self.host, self.port), self.settings)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="charles-mcp-dispatcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def main() -> None:
    parser = argparse.ArgumentParser(description="charles-mcp body-aware mock dispatcher")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("CHARLES_DISPATCHER_PORT", DEFAULT_PORT))
    )
    parser.add_argument(
        "--mock-dir",
        default=os.path.expanduser(os.getenv("CHARLES_MOCK_DIR", "~/charles-mocks")),
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not is_loopback(args.host):
        logger.warning(
            "listening on %s exposes fixtures (possibly personal data) to the network",
            args.host,
        )
    settings = DispatcherSettings(mock_dir=args.mock_dir, timeout_seconds=args.timeout)
    server = DispatcherServer((args.host, args.port), settings)
    logger.info("dispatcher on http://%s:%d, rules from %s", args.host, server.server_port,
                settings.mock_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
