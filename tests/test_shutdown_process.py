"""The real server process switches Map Remote off however it is ended.

Unit tests cover the cleanup itself. What they cannot show is whether it runs
when a real process stops: an MCP client closes stdin, or just sends SIGTERM.
With SIGTERM turned into an exception the cleanup started but the event loop
was gone before the request reached Charles, and the process then hung on the
thread that reads stdin. This starts the entrypoint for real, against a
stand-in Charles, and ends it both ways.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture
def stand_in_charles():
    """A proxy with a Map Remote switch that remembers what it was asked.

    The switch starts off, like a Charles nobody has touched: the server only
    switches off on exit what it found off and switched on itself.
    """
    asked: list[str] = []
    state = {"enabled": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            page = self.path.replace("http://control.charles", "")
            asked.append(page)
            if page.endswith("/enable"):
                state["enabled"] = True
            elif page.endswith("/disable"):
                state["enabled"] = False
            status = "Enabled" if state["enabled"] else "Disabled"
            body = f"<html>Map Remote Status: {status}</html>".encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server.server_port, asked
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and pipes")
@pytest.mark.parametrize("ending", ["stdin-closed", "sigterm"])
def test_the_process_switches_map_remote_off_when_it_ends(
    tmp_path: Path, stand_in_charles, ending: str
) -> None:
    charles_port, asked = stand_in_charles
    dispatcher_port = _free_port()
    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith("CHARLES_")},
        "CHARLES_PROXY_PORT": str(charles_port),
        "CHARLES_DISPATCHER_PORT": str(dispatcher_port),
        "CHARLES_STATE_DIR": str(tmp_path / "state"),
        "CHARLES_MOCK_DIR": str(tmp_path / "mocks"),
        "CHARLES_LOG_DIR": str(tmp_path / "logs"),
    }
    process = subprocess.Popen(
        [sys.executable, "-c", "from charles_mcp.main import main; main()"],
        cwd=REPO_ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    replies: queue.Queue[dict] = queue.Queue()

    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                replies.put(json.loads(line))
            except ValueError:
                continue

    threading.Thread(target=pump, daemon=True).start()

    def send(message: dict) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def reply(request_id: int) -> dict:
        while True:
            message = replies.get(timeout=30)
            if message.get("id") == request_id:
                return message

    try:
        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "shutdown-test", "version": "0"},
            },
        })
        reply(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "mock_dispatcher", "arguments": {"action": "start"}},
        })
        reply(2)
        assert _port_open(dispatcher_port)
        # It looks at the switch first, then turns it on.
        assert asked == ["/tools/map-remote/", "/tools/map-remote/enable"]

        if ending == "stdin-closed":
            assert process.stdin is not None
            process.stdin.close()
        else:
            process.send_signal(signal.SIGTERM)
        exit_code = process.wait(timeout=20)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert asked[-1] == "/tools/map-remote/disable"
    assert asked.count("/tools/map-remote/disable") == 1
    assert not _port_open(dispatcher_port)
    # A clean exit when the client hangs up; death by the signal it was sent otherwise.
    assert exit_code == (0 if ending == "stdin-closed" else -signal.SIGTERM)
