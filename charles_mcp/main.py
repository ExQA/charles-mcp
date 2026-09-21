"""Package entrypoint for the Charles MCP server."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from charles_mcp.server import create_server
from charles_mcp.utils import setup_logging, setup_windows_stdio


def _resolve_log_dir() -> Path:
    """Return the log directory, respecting CHARLES_LOG_DIR env var."""
    env_dir = os.environ.get("CHARLES_LOG_DIR")
    if env_dir:
        p = Path(env_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path(tempfile.gettempdir()) / "charles-mcp"


def selftest() -> int:
    """Report what this build is, and fail if it is not the fork with the mocks.

    The name ``charles-mcp`` also exists on PyPI as an older package with no
    mocking tools, so an install that went to the registry by mistake looks
    healthy: the server starts and answers. This check names the difference —
    run it after installing, instead of trusting that the right thing was
    installed.
    """
    try:
        installed = version("charles-mcp")
    except PackageNotFoundError:
        installed = "unknown (running from a source tree)"

    tools = sorted(tool.name for tool in asyncio.run(create_server().list_tools()))
    mocking = [name for name in tools if name.startswith("mock_")]

    print(f"charles-mcp {installed}")
    print(f"tools: {len(tools)}, mocking tools: {len(mocking)}")
    if not mocking:
        print(
            "FAIL: this build has no mocking tools. It is the upstream package, "
            "not the team fork — install from the repository or the offline archive."
        )
        return 1
    print("OK")
    return 0


def main() -> None:
    """Start the Charles MCP server over stdio."""
    arguments = sys.argv[1:]
    if arguments == ["--selftest"]:
        sys.exit(selftest())
    if arguments:
        # Without this a typo like --self-test starts the stdio server, which
        # waits for a client and reads as a hang.
        print(f"unknown arguments: {' '.join(arguments)}", file=sys.stderr)
        print("usage: charles-mcp [--selftest]", file=sys.stderr)
        sys.exit(2)

    log_dir = _resolve_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(log_file=str(log_dir / "debug.log"))
    logger = logging.getLogger(__name__)
    logger.info(">>> Charles MCP Server starting...")

    setup_windows_stdio()

    try:
        server = create_server()
        logger.info("calling mcp.run()...")
        server.run(transport="stdio")
    except Exception as exc:
        logger.critical("Server Process Crashed: %s", exc, exc_info=True)
        sys.exit(1)
