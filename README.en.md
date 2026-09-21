# Charles MCP Server

[![Version](https://img.shields.io/badge/version-3.1.0rc1-0e7c76.svg)](docs/team-installation.md)
[![License](https://img.shields.io/badge/license-MIT-0e7c76.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-0e7c76.svg)](pyproject.toml)
[![Install](https://img.shields.io/badge/install-from%20the%20archive%2C%20not%20PyPI-c75300.svg)](docs/team-installation.md)

[Docs](docs/README.md) | [Team Installation](docs/team-installation.md) | [Agent guide](docs/agent-guide.md) | [Mapping & Charles restarts](docs/charles-mapping.md) | [ADR-0001](docs/adr/0001-data-driven-body-aware-mocks.md) | [Tool Contract](docs/contracts/tools.md) | [AGENTS](AGENTS.md) | [Agent Workflow Guide](docs/agent-workflows.md) | [Ukrainian README](README.md)

Charles MCP Server connects Charles Proxy to MCP clients so an agent can inspect live traffic, analyze saved recordings, and expand individual requests only when needed.

It focuses on three things:

- reading incremental traffic from the current Charles session while recording is still active
- keeping live and history analysis on structured paths instead of exposing raw dump dictionaries first
- using summary-first outputs so the agent can find hotspots before pulling detail

## This Release Direction (v3.0)

The `v3.0` direction is clear: `charles-mcp` is moving from pure traffic inspection toward reverse-engineering workflows.

- On top of existing live/history analysis, it now exposes a reverse-analysis tool surface (import, query, decode, replay, signature candidate discovery, and live reverse sessions).
- The goal is to let an agent go beyond traffic browsing and build an end-to-end reverse workflow around auth flows, signatures, parameter mutation, and replayability.

## Quick Start

### 1. Enable the Charles Web Interface

In Charles, open: `Proxy -> Web Interface Settings`

Make sure:

- `Enable web interface` is checked
- username is `admin`
- password is `123456`

> `admin` / `123456` are the defaults used in every example. For real work set your own
> password and pass it through `CHARLES_USER` / `CHARLES_PASS`.

Menu location:

![Charles Web Interface Menu](docs/images/charles-web-interface-menu.png)

Settings dialog:

![Charles Web Interface Settings](docs/images/charles-web-interface-settings.png)

### 2. Install and configure your MCP client

> **Never install this fork from a registry.** The `charles-mcp` package on PyPI is upstream 3.0.3: it has no mock tools and, without the `mcp<2` pin, fails at start with `ModuleNotFoundError: mcp.server.fastmcp`. So `uvx charles-mcp` and `pip install charles-mcp` give you a server that is missing half the tools, and the failure never names its cause. Install from this repository or from the offline archive — see [docs/team-installation.md](docs/team-installation.md).

Pick one of the two install paths; everything below refers to `<PATH>`, the absolute path of the checkout or of the unpacked archive.

**Path A — with [uv](https://docs.astral.sh/uv/getting-started/installation/):**

```bash
cd <PATH> && uv sync --locked
```

**Path B — offline, from an archive that carries `wheels/`:** needs Python only.

```bash
cd <PATH>
python3 -m venv .venv
.venv/bin/pip install --no-index --find-links wheels -r requirements-lock.txt
```

Use your own Charles Web Interface credentials in `CHARLES_USER` / `CHARLES_PASS` below; the values here are placeholders.

#### Claude Code CLI

```bash
claude mcp add-json charles '{
  "type": "stdio",
  "command": "uv",
  "args": ["run", "--project", "<PATH>", "charles-mcp"],
  "env": {
    "CHARLES_USER": "your-login",
    "CHARLES_PASS": "your-password",
    "CHARLES_MANAGE_LIFECYCLE": "false"
  }
}'
```

#### Claude Desktop / Cursor / Kiro / generic JSON config

```json
{
  "mcpServers": {
    "charles": {
      "command": "uv",
      "args": ["run", "--project", "<PATH>", "charles-mcp"],
      "env": {
        "CHARLES_USER": "your-login",
        "CHARLES_PASS": "your-password",
        "CHARLES_MANAGE_LIFECYCLE": "false"
      }
    }
  }
}
```

On path B, replace those two lines with the virtual environment's own interpreter:

```json
"command": "<PATH>/.venv/bin/python",
"args": ["<PATH>/charles-mcp-server.py"]
```

Kiro reads `.kiro/settings/mcp.json` in the project (it wins) or `~/.kiro/settings/mcp.json`, and also accepts `"disabled": false` and `"autoApprove": [...]`.

#### Codex CLI

```toml
[mcp_servers.charles]
command = "uv"
args = ["run", "--project", "<PATH>", "charles-mcp"]

[mcp_servers.charles.env]
CHARLES_USER = "your-login"
CHARLES_PASS = "your-password"
CHARLES_MANAGE_LIFECYCLE = "false"
```

### Auto-install via AI agent

Hand an agent (Claude Code, Cursor Agent, Kiro, Gemini CLI, ChatGPT with tools) the prompt below, replacing `<PATH>` with the absolute path of the checkout or unpacked archive. The prompt is kept in English: agents follow it the same way whatever language you talk to them in.

<details>
<summary><strong>Click to expand the auto-install prompt</strong></summary>

```text
Install the charles-mcp MCP server from the local directory <PATH> and configure
my MCP client to use it. Follow these steps exactly.

Install only from that directory: the same name on PyPI is a different, older
package, so a registry install looks healthy but has no mocking tools. Step 3
checks for them, so do not skip it.

Step 1 - Check the directory:
  Confirm <PATH> exists and contains pyproject.toml, charles_mcp/ and
  charles-mcp-server.py. If it does not, stop and tell me.
  Note whether it also contains wheels/ and requirements-lock.txt.

Step 2 - Build the environment, preferring the offline path:
  a) If wheels/ and requirements-lock.txt exist, use them (no network):
       cd <PATH>
       python3 -m venv .venv
       .venv/bin/pip install --no-index --find-links wheels -r requirements-lock.txt
     On Windows the interpreter is .venv\Scripts\python.exe.
     If pip reports no matching distribution, the wheels were built for other
     Python versions - report the Python version you used and stop.
  b) Otherwise, if `uv --version` works:
       cd <PATH> && uv sync --locked
  c) Otherwise tell me which of the two is missing instead of improvising.

Step 3 - Verify the build, not just that something starts:
    <PATH>/.venv/bin/python <PATH>/charles-mcp-server.py --selftest
  (or `uv run --project <PATH> charles-mcp --selftest` if you took path 2b).
  It prints the version and the tool counts and ends with OK. Stop and tell me
  if it prints FAIL, reports 0 mocking tools, or rejects --selftest as an
  unknown option: that build is not this fork, so the configuration would be
  pointless. A line about default credentials is expected and fine.

Step 4 - Detect my MCP client, first match wins:
  a) Kiro - .kiro/settings/mcp.json in the current project, else ~/.kiro/settings/mcp.json
  b) Claude Code - `claude --version` succeeds
  c) Cursor - ~/.cursor/mcp.json or .cursor/mcp.json
  d) Claude Desktop - ~/Library/Application Support/Claude/claude_desktop_config.json
     (Windows: %APPDATA%\Claude\claude_desktop_config.json,
      Linux: ~/.config/Claude/claude_desktop_config.json)
  e) Windsurf - ~/.codeium/windsurf/mcp_config.json
  If none is found, ask me.

Step 5 - Ask me for my Charles Web Interface login and password before writing
  them anywhere. Do not invent values and do not reuse the examples from the
  documentation.

Step 6 - Write the config entry, using ABSOLUTE paths:
    "charles": {
      "command": "<PATH>/.venv/bin/python",
      "args": ["<PATH>/charles-mcp-server.py"],
      "env": {
        "CHARLES_USER": "<the login I gave you>",
        "CHARLES_PASS": "<the password I gave you>",
        "CHARLES_MANAGE_LIFECYCLE": "false"
      }
    }
  If you used uv (path 2b) instead:
      "command": "uv",
      "args": ["run", "--project", "<PATH>", "charles-mcp"]

  Read any existing config file first, parse the JSON, add "charles" inside
  "mcpServers" (create "mcpServers" if absent) and write it back. Do not drop or
  overwrite other servers. Kiro also accepts "disabled": false and "autoApprove";
  if you set autoApprove, list only read-only tools: charles_status,
  start_live_capture, query_live_capture_entries, group_capture_analysis. Never
  auto-approve reset_environment, mock_setup_host, mock_route_setup or
  reverse_replay_entry - they close Charles, write its config, or send real
  requests to real servers.

Step 7 - Report which client you configured and which config file you edited,
  and tell me to restart that client. For Kiro, saving mcp.json reconnects the
  server - it should appear in the MCP panel. Remind me that Charles must be
  running with its Web Interface enabled (Proxy -> Web Interface Settings).
```

</details>

## Requirements

- Python 3.10+
- Charles Proxy running locally
- Charles Web Interface enabled
- Charles proxy listening on `127.0.0.1:8888`

`CHARLES_MANAGE_LIFECYCLE=false` is the recommended default. Unless you explicitly want the MCP server to manage Charles lifecycle, do not let it shut down your own Charles process.

## Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `CHARLES_USER` | `admin` | Charles Web Interface username |
| `CHARLES_PASS` | `123456` | Charles Web Interface password |
| `CHARLES_PROXY_HOST` | `127.0.0.1` | Charles proxy host |
| `CHARLES_PROXY_PORT` | `8888` | Charles proxy port |
| `CHARLES_CONFIG_PATH` | auto-detect | Charles config file path |
| `CHARLES_REQUEST_TIMEOUT` | `10` | Control-plane HTTP timeout in seconds |
| `CHARLES_MANAGE_LIFECYCLE` | `false` | Whether the MCP server should manage Charles startup and shutdown |
| `CHARLES_REVERSE_STATE_DIR` | `${CHARLES_STATE_DIR}/reverse` | State root for reverse-analysis artifacts and SQLite data |
| `CHARLES_MOCK_DIR` | `~/charles-mocks` | Root of the Map Local mock store; Charles maps `https://<host>/*` to `<CHARLES_MOCK_DIR>/<host>/`. Dispatcher rules live in `<CHARLES_MOCK_DIR>/_rules/` |
| `CHARLES_DISPATCHER_PORT` | `18080` | Port of the local body-aware dispatcher that Map Remote routes point to |
| `CHARLES_DISPATCHER_TIMEOUT` | `20` | Upstream timeout in seconds for requests the dispatcher forwards |

## Recommended Flows

### Live analysis

1. `start_live_capture`
2. `group_capture_analysis`
3. `query_live_capture_entries`
4. `get_traffic_entry_detail`
5. `stop_live_capture`

This path is optimized for finding hotspots first, then drilling down into one confirmed request.

### History analysis

1. `list_recordings`
2. `analyze_recorded_traffic`
3. `group_capture_analysis(source="history")`
4. `get_traffic_entry_detail`

This path is optimized for browsing saved recordings and then drilling into selected entries.

### Mocking responses with Map Local

> How the Charles rules work, when and how Charles is restarted, rollback and troubleshooting: [docs/charles-mapping.md](docs/charles-mapping.md).

1. `mock_setup_host` once per host: creates `<CHARLES_MOCK_DIR>/<host>/` and the Charles rules. With `apply=true` (Charles must be closed) it writes them into the Charles config; otherwise it returns manual steps.
2. `start_live_capture` + `query_live_capture_entries` to find the real response.
3. `mock_create_from_entry` with JSON Pointer `patches` (or `mock_write` for a response from scratch).
4. The app repeats the request; confirm with `query_live_capture_entries(response_header_name="X-Charles-Map-Local")`.
5. `mock_remove` to go back to the real server (the file is archived, not deleted); `mock_set_enabled(false)` pauses all mocks.

A mock is just a file at `<CHARLES_MOCK_DIR>/<host>/<request path>`: Charles serves it while it exists, re-reads it on every request, and passes the request through when it does not. Map Local limits: the response status is always 200, and the query string and HTTP method are ignored, so every variant of a path gets the same file. `/users` and `/users/42` cannot both be mocked, because `users` cannot be a file and a directory at once. The setup also adds a Rewrite rule that serves mocks as `application/json`, since Charles labels extension-less files `text/plain`.

### Many paths and actions: dispatcher rules

Use this for real API traffic: many paths, POST requests whose `action` field selects the operation, and the same API on several hosts (dev, stage, ...). Map Local cannot tell such requests apart; the dispatcher can. See [ADR-0001](docs/adr/0001-data-driven-body-aware-mocks.md) for the reasoning.

> The Charles rule is set up once per domain and needs a Charles restart (or manual entry in the UI, no restart). The tools never quit or start Charles themselves: the order of steps, what is lost and how to roll back are in [docs/charles-mapping.md](docs/charles-mapping.md).

**Once per domain.** `mock_route_setup(host="*.example.com", path="/api/*")` records the route and returns one Charles Map Remote mapping: `https://*.example.com/api/*` to the local dispatcher with an empty destination path (Charles keeps the original path) and "Preserve host header" on. `apply=true` writes it while Charles is closed. After that, every path, action and host under the route is only data, and requests without a rule go to the real server unchanged.

**Per session.**

1. `start_live_capture`, then let the app run the flow.
2. `mock_discover_variants(source="live", capture_id)` lists the whole session: method × host × path × `action` value (`null` for GETs), with counts, sample `entry_id`s and statuses. Filter with `host_contains` / `path_contains`.
3. For each change the user asks for: `mock_rule_create_from_entry(entry_id, match_body_fields=["/action"], response_patches=[...], request_patches=[...])`.
   - `mode="patch"` (default): the real server answers; only the named fields of the request and response change.
   - `mode="fixture"`: the captured response with patches and any `status`, e.g. 500, without contacting the server.
   - Rules apply on every routed host by default (`host_scope="any"`); `host_scope="exact"` or `host="stage.example.com"` narrows one, and an exact-host rule wins over a general one.
   - Asking again for the same variant adds to its rule (`merge=true`); a patch on the same field replaces the earlier one.
4. `mock_dispatcher(action="start")`, then repeat the actions in the app.
5. Confirm with `query_live_capture_entries(response_header_name="X-Charles-MCP-Rule")`: the header carries the rule id, or `passthrough`.

Storage in `<CHARLES_MOCK_DIR>/_rules/`: `routes.json` (routes), `_any/` (rules for any host or a host glob), `<host>/` (rules for one host), fixtures next to their rules as `<rule_id>.body`. The dispatcher re-reads them on every request, so edits apply immediately. It only forwards requests covered by a route, listens on `127.0.0.1` and must run while the Charles mapping is active; for a long-running instance use the `charles-mcp-dispatcher` command. Routing a whole host (`/*`) also sends its static files and WebSocket upgrades through the dispatcher, which buffers downloads and does not support WebSockets; prefer the API prefix.

> ⚠️ Fixtures are captured real responses and can contain personal or payment data. Keep `CHARLES_MOCK_DIR` outside any repository and never share it; share rule definitions with synthetic data instead.

## Current Version Highlights (v3.0.3)

- Documentation entrypoints now converge on `docs/README.md`.
- Added agent execution docs: root-level `AGENTS.md` and task-oriented `docs/agent-workflows.md`.
- Added agent-doc entry links in README and `docs/contracts/tools.md` using repository-relative paths.
- Added minimal guidance semantics to high-frequency entry-tool descriptions (identity preservation, summary-first, peek/read behavior) with contract tests to prevent drift.
- The product direction now explicitly includes reverse engineering: a reverse-analysis tool surface is available for import, decode, replay, signature-candidate discovery, and live reverse workflows.
- `read_live_capture` and `peek_live_capture` now return route-level summary fields only, such as `host`, `method`, `path`, and `status`, instead of raw Charles entries. This keeps repeated polling from blowing up the context window.
- `query_live_capture_entries` is now a read-only analysis path and does not advance the live cursor. You can reuse the same `capture_id` with different filters without consuming the historical increment.
- `analyze_recorded_traffic` and `query_live_capture_entries` summaries now expose `matched_fields` and `match_reasons`, so an agent can explain why a request was selected.
- `get_traffic_entry_detail` now defaults to `include_full_body=false` and `max_body_chars=2048`. When the estimated detail payload exceeds about 12,000 characters, the tool adds a warning suggesting a narrower request.
- Summary and detail output automatically strip `null` values and hide internal fields such as `header_map`, `parsed_json`, `parsed_form`, and `lower_name`. Use the `headers` list when you need header values.

## Tool Catalog

This README documents the whole public tool surface.

### Live capture tools

| Tool | What it does | Typical use |
| --- | --- | --- |
| `start_live_capture` | Starts or adopts the current live capture and returns `capture_id`; never clears the traffic Charles already recorded and always includes it in the capture | Before realtime inspection begins |
| `read_live_capture` | Reads incremental live entries by cursor and returns compact route summaries only | When consuming new traffic continuously and you only need host/path/status first |
| `peek_live_capture` | Previews new live entries without advancing the cursor and returns compact route summaries only | When you want to inspect new traffic without moving the reader state |
| `stop_live_capture` | Stops the capture and optionally persists a snapshot | When closing or exporting a live session |
| `query_live_capture_entries` | Produces structured summary output for a live capture without advancing the cursor | When repeatedly filtering high-value requests out of current traffic |

### Analysis tools

| Tool | What it does | Typical use |
| --- | --- | --- |
| `group_capture_analysis` | Aggregates live or history traffic by group key | When you want the lowest-token hotspot view |
| `get_capture_analysis_stats` | Returns coarse traffic class counts | When you want a quick distribution view |
| `get_traffic_entry_detail` | Loads detail for one specific entry and warns when the payload is too large | After you already identified a target `entry_id` |
| `analyze_recorded_traffic` | Produces structured summary output for a saved recording with match reasons | When analyzing a `.chlsj` snapshot |

### History tools

| Tool | What it does | Typical use |
| --- | --- | --- |
| `list_recordings` | Lists saved recording files | Before choosing a historical snapshot |
| `get_recording_snapshot` | Loads the raw content of one saved recording | When you need the stored snapshot itself |
| `query_recorded_traffic` | Applies lightweight filtering to the latest saved recording | When you need a quick host, method, or regex query |

### Status and control tools

| Tool | What it does | Typical use |
| --- | --- | --- |
| `charles_status` | Reports Charles connectivity and active capture state | When checking whether Charles is reachable or capture is still active |
| `throttling` | Applies a Charles network throttling preset | When simulating 3G, 4G, 5G, or disabling throttling |
| `reset_environment` | Restores Charles configuration and clears the current environment | When you need to return to a clean baseline |

### Reverse analysis tools

| Tool | What it does | Typical use |
| --- | --- | --- |
| `reverse_import_session` | Imports an official Charles XML or native session into the canonical reverse store | When starting a replay, decode, or signature workflow from saved exports |
| `reverse_list_captures` | Lists imported reverse-analysis captures | When choosing a capture already stored in the reverse SQLite plane |
| `reverse_query_entries` | Filters imported reverse entries by route fields | When narrowing the candidate request set before detail or replay |
| `reverse_get_entry_detail` | Returns canonical detail for one imported reverse entry | When inspecting one baseline request deeply |
| `reverse_decode_entry_body` | Decodes a stored request or response body, including protobuf with descriptors | When you need structured payload understanding |
| `reverse_replay_entry` | Replays one imported request with optional mutations | When validating whether a request can be reproduced or perturbed |
| `reverse_discover_signature_candidates` | Compares multiple imported entries and ranks likely signature-related fields | When searching for dynamic auth or signing parameters |
| `reverse_list_findings` | Lists persisted replay and signature findings | When reviewing prior reverse-analysis evidence |
| `reverse_charles_recording_status` | Reports Charles recording state and reverse live-session state | When checking live reverse-analysis readiness |
| `reverse_start_live_analysis` | Starts a reverse-analysis live session and snapshots Charles via official export pages | When reverse work must track fresh traffic incrementally |
| `reverse_peek_live_entries` | Reads new reverse live entries without advancing the reverse cursor | When previewing new traffic before consuming it |
| `reverse_read_live_entries` | Reads and consumes new reverse live entries | When advancing a reverse live-analysis session |
| `reverse_stop_live_analysis` | Stops a reverse live-analysis session and optionally restores recording | When closing a reverse live session cleanly |
| `reverse_analyze_live_login_flow` | Scores new live traffic for login/auth relevance and summarizes next actions | When tracing login or token bootstrap flows |
| `reverse_analyze_live_api_flow` | Scores new live traffic for API workflow relevance and summarizes next actions | When tracing structured business API traffic |
| `reverse_analyze_live_signature_flow` | Focuses new live traffic on signature-sensitive requests and mutation planning | When targeting signing, nonce, or timestamp defenses |

### Map Local mock tools

| Tool | What it does | Typical use |
| --- | --- | --- |
| `mock_setup_host` | Creates the host's mock directory and the Map Local + Rewrite rules (manual steps, or written to the config with `apply=true` while Charles is closed) | Once per API host |
| `mock_create_from_entry` | Turns a captured response into a mock, applying JSON Pointer edits | When the user wants to change data the app received |
| `mock_write` | Writes a mock from a JSON value or raw text | When no real response exists yet |
| `mock_list` / `mock_get` | Lists active mocks / shows one | When reviewing what is currently mocked |
| `mock_remove` | Archives a mock so the path hits the real server again | When a scenario is finished |
| `mock_set_enabled` | Turns the whole Map Local tool on or off via the web interface | When pausing or resuming all mocks |

### Dispatcher rule tools

| Tool | What it does | Typical use |
| --- | --- | --- |
| `mock_route_setup` | Routes a host or domain glob (default every path) through the dispatcher and returns (or, with `apply=true` while Charles is closed, writes) the one Map Remote mapping | Once per domain |
| `mock_discover_variants` | Groups the whole session by method, host, path and a body field such as `/action` | Right after reading a session |
| `mock_rule_create_from_entry` | Creates or extends the rule for one variant (method + path + body/query values): patch or fixture mode, any status, any host by default | For each change the user names |
| `mock_rule_write` | Writes a rule document directly, optionally with a fixture | When no captured request exists |
| `mock_rule_list` / `mock_rule_get` | Lists routes and rules / shows one rule and its fixture | When reviewing what is mocked |
| `mock_rule_set_enabled` / `mock_rule_remove` | Pauses a rule / archives it | When a scenario is finished |
| `mock_dispatcher` | Starts, stops or checks the local dispatcher; start/stop also switch Charles Map Remote on/off so routed traffic never hits a stopped dispatcher | Before testing, and when done |

## Key Behavior

### 1. Raw values are returned by default

This version no longer redacts request or response content:

- summary, detail, live, and history outputs all return raw values

### 2. Summary comes before detail

Use `group_capture_analysis`, `query_live_capture_entries`, or `analyze_recorded_traffic` first, then call `get_traffic_entry_detail` only for a confirmed target.

Do not default to `include_full_body=true` unless there is a clear reason.

### 3. Output is optimized for token budgets

All summary and detail outputs have been serialized lean:

- Internal fields like `header_map`, `parsed_json`, `parsed_form`, and `lower_name` are excluded from tool output
- `null` values are stripped automatically during serialization
- When `full_text` is present in a detail view, the redundant `preview_text` is removed

Default parameters have been lowered to protect the context window:

| Parameter | Old default | New default |
| --- | --- | --- |
| `max_items` | 20 | 10 |
| `max_preview_chars` | 256 | 128 |
| `max_headers_per_side` | 8 | 6 |
| `max_body_chars` | 4096 | 2048 |

Higher values can still be passed explicitly when a wider view is needed.

### 4. History detail needs stable source identity

History summaries return `recording_path`. Live summaries return `capture_id`.

For `get_traffic_entry_detail`:

- prefer `recording_path` for history
- prefer `capture_id` for live

### 5. `stop_live_capture` failures are recoverable

`stop_live_capture` has two stable end states:

- `status="stopped"` means the capture is actually closed
- `status="stop_failed"` means a short retry also failed but the capture is still preserved

When the result is:

```json
{
  "status": "stop_failed",
  "recoverable": true,
  "active_capture_preserved": true
}
```

the capture is still readable and can be diagnosed or stopped again later.

## Development

Run tests:

```bash
python -m pytest -q
```

Useful local checks:

```bash
python charles-mcp-server.py
python -c "from charles_mcp.main import main; main()"
```

## See Also

- [Docs](docs/README.md)
- [Ukrainian README](README.md)
- [Tool Contract](docs/contracts/tools.md)
