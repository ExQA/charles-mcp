#!/usr/bin/env bash
# Install charles-mcp from this directory, without touching any package registry.
#
# Run it inside the unpacked offline archive:
#
#     ./install.sh
#
# It builds a virtual environment next to this file, installs the bundled
# wheels into it, checks that the result really is this fork, and prints the
# entry to paste into your MCP client's config.
#
# Override the interpreter with PYTHON=/path/to/python3 ./install.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
VENV="$HERE/.venv"
VENV_PYTHON="$VENV/bin/python"

fail() {
    echo "" >&2
    echo "install failed: $1" >&2
    exit 1
}

cd "$HERE"

[ -d "$HERE/wheels" ] && [ -f "$HERE/requirements-lock.txt" ] || fail \
    "this directory has no wheels/ and requirements-lock.txt, so there is nothing to install offline.
  It looks like a source checkout rather than the offline archive — use: uv sync --locked"

command -v "$PYTHON" >/dev/null 2>&1 || fail "no $PYTHON on PATH; install Python 3.10+ or set PYTHON=/path/to/python3"

version="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "Python $version at $(command -v "$PYTHON")"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || fail \
    "charles-mcp needs Python 3.10 or newer, found $version"

echo "creating the virtual environment in .venv"
"$PYTHON" -m venv "$VENV"

echo "installing the bundled dependencies (no network)"
# requirements-lock.txt carries hashes, so pip verifies every file it installs.
"$VENV_PYTHON" -m pip install --quiet --no-index --find-links wheels -r requirements-lock.txt || fail \
    "pip could not install the dependencies. If it reported no matching distribution, this archive
  carries no wheels for Python $version — ask for an archive built for it."

echo "installing charles-mcp itself"
project_wheel="$(ls "$HERE"/wheels/charles_mcp-*.whl 2>/dev/null | tail -1 || true)"
[ -n "$project_wheel" ] || fail "wheels/ has no charles_mcp wheel; rebuild the archive with scripts/build_offline_archive.py"
"$VENV_PYTHON" -m pip install --quiet --no-index --no-deps "$project_wheel"

echo ""
echo "checking the installed build"
"$VENV/bin/charles-mcp" --selftest || fail "the installed build is not this fork — see docs/team-installation.md"

cat <<CONFIG

Done. Start the server with:

  $VENV/bin/charles-mcp

Or give your MCP client this entry, replacing the credentials with your own
Charles Web Interface login and password:

  "charles": {
    "command": "$VENV/bin/charles-mcp",
    "args": [],
    "env": {
      "CHARLES_USER": "your-login",
      "CHARLES_PASS": "your-password",
      "CHARLES_MANAGE_LIFECYCLE": "false"
    }
  }

Kiro reads .kiro/settings/mcp.json in the project, or ~/.kiro/settings/mcp.json.
Charles must be running with Proxy -> Web Interface Settings enabled.
CONFIG
