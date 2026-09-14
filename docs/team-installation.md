# Team Installation and Distribution

How to share this `charles-mcp` fork with a team and install it on another computer.

> Ukrainian version: [team-installation.uk.md](team-installation.uk.md)

## Architecture and responsibility boundary

`charles-mcp` is a local stdio MCP server. Every developer runs it on the same computer as their Charles Proxy.

- Charles, its license, Web Interface credentials, proxy port, captures, mocks, backups and runtime state are local to each computer.
- The MCP client starts `charles-mcp` on demand over stdio.
- The body-aware mock dispatcher (see [ADR-0001](adr/0001-data-driven-body-aware-mocks.md)) also runs locally on `127.0.0.1`.
- No shared Charles instance, and no credentials sent to a central service.

Share the repository and a pinned tag. **Never share** Charles configs, captures (`*.chlsj`), cookies, tokens, `.env` files or the mock directory (`~/charles-mocks`): mock fixtures are captured real responses.

## Why not PyPI

The `charles-mcp` package on PyPI belongs to the upstream author and is version 3.0.3. It has no mock tools, and because it does not pin `mcp<2` it resolves `mcp` 2.x and fails at start with `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`. This fork is installed from the team repository instead. Do not publish it to the public PyPI.

## Prerequisites

1. Charles Proxy, licensed according to the team's policy.
2. [`uv`](https://docs.astral.sh/uv/getting-started/installation/); it also provides Python 3.10+ if needed.
3. git.
4. An MCP client with stdio support: Claude Code, Claude Desktop, Cursor, Codex CLI, Kiro or similar.

## Install from the repository

```bash
git clone <TEAM_REPOSITORY_URL> charles-mcp
cd charles-mcp
git checkout <TAG_OR_BRANCH>
uv sync --locked --extra dev
uv run python -m pytest -q
```

Without network access to the repository, a maintainer can hand over a git bundle (`git bundle create charles-mcp.bundle --all`), and the first command becomes `git clone -b <branch> charles-mcp.bundle charles-mcp`.

On macOS, keep the checkout out of iCloud-synced folders such as `~/Documents`; sync interferes with the virtual environment.

### MCP client configuration

Point the client at the repository with an absolute path:

```json
{
  "mcpServers": {
    "charles": {
      "command": "uv",
      "args": ["run", "--project", "/absolute/path/to/charles-mcp", "charles-mcp"],
      "env": {
        "CHARLES_USER": "<local-charles-user>",
        "CHARLES_PASS": "<local-charles-password>",
        "CHARLES_MANAGE_LIFECYCLE": "false"
      }
    }
  }
}
```

Claude Code can add it without editing JSON:

```bash
claude mcp add-json charles '{"type":"stdio","command":"uv","args":["run","--project","/absolute/path/to/charles-mcp","charles-mcp"],"env":{"CHARLES_USER":"<local-charles-user>","CHARLES_PASS":"<local-charles-password>","CHARLES_MANAGE_LIFECYCLE":"false"}}'
```

| Client | Typical configuration file |
| --- | --- |
| Claude Desktop | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`; Windows `%APPDATA%\Claude\claude_desktop_config.json`; Linux `~/.config/Claude/claude_desktop_config.json` |
| Cursor | project `.cursor/mcp.json`, user `~/.cursor/mcp.json` |
| Kiro | project `.kiro/settings/mcp.json`, user `~/.kiro/settings/mcp.json` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| Codex CLI | the equivalent TOML entry |

Merge the `charles` entry into an existing file; do not overwrite other servers. Restart the client, then ask it to call `charles_status`.

A plain virtualenv also works: `python3 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"`, then use `/absolute/path/to/charles-mcp/.venv/bin/charles-mcp` (Windows: `.venv\Scripts\charles-mcp.exe`) as the command.

## Per-computer Charles setup

1. Start Charles and enable `Proxy -> Web Interface Settings` with a non-default username and password; keep "Allow anonymous access" off.
2. Confirm the proxy port, normally `8888`.
3. Enable SSL Proxying for the API hosts and install the Charles root certificate on the test device.
4. Point the device at the computer's LAN address and the proxy port, and allow it in Charles access control.
5. Keep `CHARLES_MANAGE_LIFECYCLE=false`.
6. If config discovery fails, set `CHARLES_CONFIG_PATH`. On macOS with Charles 5 the file is `~/Library/Preferences/com.xk72.charles.config`.

Tools that write the Charles config (`mock_setup_host`, `mock_route_setup` with `apply=true`) only work while Charles is closed: Charles rewrites its config from memory on quit, so edits made while it runs are lost. The tools never quit or start Charles; see [charles-mapping.md](charles-mapping.md) for the restart order, backups, rollback and troubleshooting.

## Mocks on a team

- Map Local mocks: `<CHARLES_MOCK_DIR>/<host>/<path>` files, one Charles rule per host (`mock_setup_host`).
- Dispatcher rules: `<CHARLES_MOCK_DIR>/_rules/` (`routes.json`, `_any/` for rules on any host, `<host>/` for one host), served by the dispatcher (`mock_route_setup` once per domain, `mock_discover_variants`, `mock_rule_create_from_entry`, `mock_dispatcher`).
- The dispatcher must run while a Map Remote mapping points to it. Start it from the agent with `mock_dispatcher(action="start")` (it stops with the MCP server) or run it standalone:

  ```bash
  uv run --project /absolute/path/to/charles-mcp charles-mcp-dispatcher --port 18080
  ```

- To share a scenario, share the rule JSON with synthetic values in the fixture, not the mock directory.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `CHARLES_USER` / `CHARLES_PASS` | Local Charles Web Interface credentials |
| `CHARLES_PROXY_HOST` / `CHARLES_PROXY_PORT` | Charles proxy endpoint, normally `127.0.0.1` and `8888` |
| `CHARLES_CONFIG_PATH` | Explicit Charles config path |
| `CHARLES_MANAGE_LIFECYCLE` | Keep `false` so the server never closes a user's Charles |
| `CHARLES_STATE_DIR` / `CHARLES_REVERSE_STATE_DIR` | Per-user state for captures and reverse analysis |
| `CHARLES_MOCK_DIR` | Per-user mock root, default `~/charles-mocks` |
| `CHARLES_DISPATCHER_PORT` / `CHARLES_DISPATCHER_TIMEOUT` | Dispatcher port (default `18080`) and upstream timeout in seconds (default `20`) |
| `CHARLES_LOG_DIR` | MCP server log directory |

Copy `.env.example` to `.env` for local values; `.env` is git-ignored.

## Releasing a team version

1. Bump `[project].version` in `pyproject.toml`.
2. Run `uv run ruff check charles_mcp tests`, `uv run mypy charles_mcp` and `uv run pytest -q`.
3. Tag the commit and push the tag to the team repository.
4. Announce the tag; everyone runs `git fetch && git checkout <tag> && uv sync --locked --extra dev` and restarts the MCP client.

## Security rules

- Never commit credentials, cookies, authorization headers, tokens, `.env`, Charles configs, `*.chlsj` captures or mock fixtures.
- Charles config backups taken before `apply=true` go to `<CHARLES_STATE_DIR>/charles-config-backups/` (outside the repository): they contain the Charles license key and Web Interface credentials.
- Never put captured data in source code or tests. Tests use synthetic data on `example.com` or `localhost`.
- Traffic data is sensitive: the MCP server passes raw request and response values to the MCP client and its model.
- Use a unique Web Interface password per developer.
- When reporting an issue, attach the version, OS, MCP client, Charles version, a sanitized log and `charles_status` output, not raw captures.

## Troubleshooting

**`ModuleNotFoundError: mcp.server.fastmcp`.** You installed from PyPI or without the lock file. Install from the repository with `uv sync --locked`.

**The client cannot start the server.** Use an absolute `--project` path and restart the client after editing its configuration.

**`charles_status` says Charles is unreachable.** Check that Charles runs, the Web Interface is on, the credentials match, and the proxy host and port are right.

**Requests to a mapped URL fail.** The dispatcher is not running; call `mock_dispatcher(action="status")`, start it, or disable the Map Remote mapping.

**The dispatcher answers 421.** The host has no route; run `mock_route_setup` for it.

**Live tools are slow or the disk fills up.** Every live query exports the whole Charles session through the Web Interface; a long-running session can reach gigabytes (8 GB was observed). Clear the session before a scenario (Charles: Proxy → Clear Session, or `start_live_capture(reset_session=true)` when the user agrees) and restart Charles between long runs.

**Mocks are missing on another computer.** Expected: mocks and state are per computer. Recreate them there.
