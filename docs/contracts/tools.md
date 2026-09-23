# Tool Contract

This document defines the Charles MCP **canonical public surface** and the contract of each tool.
`create_server()` exposes exactly the canonical tools declared here.

Installation, environment variables and Claude CLI / Codex CLI / Antigravity configuration examples:

- [README.md](../../README.md)
- [README.en.md](../../README.en.md)
- [docs/README.md](../README.md)
- [AGENTS.md](../../AGENTS.md)
- [agent-workflows.md](../agent-workflows.md)

Defaults match the README:

- `CHARLES_USER=admin`
- `CHARLES_PASS=123456`
- `CHARLES_MANAGE_LIFECYCLE=false`

## General rules

1. Live and history are two independent planes; never mix their source identities.
2. Group or summarize first, then ask for detail.
3. Treat the summary as the primary data source; do not pull full bodies up front.
4. Only `stop_live_capture.status="stopped"` means the capture is fully closed.
5. Output is trimmed at serialization: `header_map`, `parsed_json`, `parsed_form` and `lower_name` are not in the output, and `null` values are stripped.

## Canonical Surface (Parseable)

<!-- CANONICAL_PUBLIC_SURFACE:START -->
```json
{
  "canonical_public_tool_names": [
    "start_live_capture",
    "read_live_capture",
    "peek_live_capture",
    "stop_live_capture",
    "query_live_capture_entries",
    "list_recordings",
    "get_recording_snapshot",
    "query_recorded_traffic",
    "analyze_recorded_traffic",
    "group_capture_analysis",
    "get_capture_analysis_stats",
    "get_traffic_entry_detail",
    "charles_status",
    "throttling",
    "reset_environment",
    "reverse_import_session",
    "reverse_list_captures",
    "reverse_query_entries",
    "reverse_get_entry_detail",
    "reverse_decode_entry_body",
    "reverse_replay_entry",
    "reverse_discover_signature_candidates",
    "reverse_list_findings",
    "reverse_start_live_analysis",
    "reverse_peek_live_entries",
    "reverse_read_live_entries",
    "reverse_stop_live_analysis",
    "reverse_charles_recording_status",
    "reverse_analyze_live_login_flow",
    "reverse_analyze_live_api_flow",
    "reverse_analyze_live_signature_flow",
    "mock_setup_host",
    "mock_create_from_entry",
    "mock_write",
    "mock_list",
    "mock_get",
    "mock_remove",
    "mock_set_enabled",
    "mock_route_setup",
    "mock_discover_variants",
    "mock_rule_create_from_entry",
    "mock_rule_write",
    "mock_rule_list",
    "mock_rule_get",
    "mock_rule_set_enabled",
    "mock_rule_remove",
    "mock_dispatcher",
    "purge_stored_data"
  ]
}
```
<!-- CANONICAL_PUBLIC_SURFACE:END -->

## Recommended tool scope

This document covers the canonical public tools only.

### Live capture tools

| Tool | Contract |
| --- | --- |
| `start_live_capture` | Returns a new or adopted `capture_id` that every other live tool needs; the capture always includes the traffic recorded before the call |
| `read_live_capture` | Reads incrementally by `capture_id + cursor` and advances the cursor |
| `peek_live_capture` | Previews new entries by `capture_id + cursor` without advancing the cursor |
| `stop_live_capture` | Ends the capture and persists it if asked; returns `status`, `recoverable`, `active_capture_preserved` |
| `query_live_capture_entries` | Returns a structured summary of the live capture and a `next_cursor` |

### History tools

| Tool | Contract |
| --- | --- |
| `list_recordings` | Lists the available recordings |
| `get_recording_snapshot` | Returns the raw snapshot of one recording |
| `query_recorded_traffic` | Lightweight filtering of the latest saved recording |
| `analyze_recorded_traffic` | Structured summary of a given recording or the latest one |

### Shared analysis tools

| Tool | Contract |
| --- | --- |
| `group_capture_analysis` | Aggregates by `host`, `path`, `status` and other dimensions; the first look at hotspots |
| `get_capture_analysis_stats` | Returns per-class counts and totals |
| `get_traffic_entry_detail` | Reads the detail of one `entry_id`; not for bulk detail export |

### Status and control tools

| Tool | Contract |
| --- | --- |
| `charles_status` | Returns Charles connectivity and the active capture state |
| `throttling` | Sets a Charles network throttling preset |
| `reset_environment` | Restores the Charles config and cleans the runtime environment |

### Reverse analysis tools

| Tool | Contract |
| --- | --- |
| `reverse_import_session` | Imports an official Charles XML / native session and returns a `capture_id` for later reverse queries and replay |
| `reverse_list_captures` | Lists the datasets imported into the reverse SQLite store |
| `reverse_query_entries` | Filters imported reverse entries by route-level fields only, without expanding detail |
| `reverse_get_entry_detail` | Reads the canonical detail of one reverse entry: request / response / body blob / decoded artifacts |
| `reverse_decode_entry_body` | Decodes the request or response body of one reverse entry into structure |
| `reverse_replay_entry` | Replays one reverse entry and saves experiment / run / finding when asked |
| `reverse_discover_signature_candidates` | Compares fields across reverse entries and ranks likely signature parameters |
| `reverse_list_findings` | Returns replay and signature findings |
| `reverse_start_live_analysis` | Starts a reverse live session; later reverse live tools need its `live_session_id` |
| `reverse_peek_live_entries` | Reads new traffic of a reverse live session without advancing the cursor |
| `reverse_read_live_entries` | Reads new traffic of a reverse live session and advances the cursor |
| `reverse_stop_live_analysis` | Stops a reverse live session and, depending on a parameter, restores the recording state |
| `reverse_charles_recording_status` | Returns the Charles recording state and the reverse live session state together |
| `reverse_analyze_live_login_flow` | Runs a login / auth workflow on reverse live traffic |
| `reverse_analyze_live_api_flow` | Runs an API workflow on reverse live traffic |
| `reverse_analyze_live_signature_flow` | Runs a signature / dynamic-parameter workflow on reverse live traffic |

### Map Local mock tools

| Tool | Contract |
| --- | --- |
| `mock_setup_host` | Creates `<CHARLES_MOCK_DIR>/<host>/`; `apply=false` returns manual Map Local + Rewrite steps, `apply=true` writes both rules into the Charles config, backs it up first, refuses while Charles runs, and is idempotent per host |
| `mock_create_from_entry` | Reads one entry (`capture_id` for live, `recording_path` for history), applies JSON Pointer `patches` (`set` / `remove`), writes the mock for the entry's host and path; `warnings` flag the method, status, and query that Map Local ignores |
| `mock_write` | Writes exactly one of `body` (JSON value) or `body_text` |
| `mock_list` | Lists active mocks; archived versions are excluded |
| `mock_get` | Returns one mock's content, truncated to `max_chars` |
| `mock_remove` | Moves the mock to `_archive/`; never deletes |
| `mock_set_enabled` | Calls `/tools/map-local/enable` or `/disable`; mock files stay on disk |

A previous mock at the same path is archived before it is overwritten. Responses served from a mock carry the `X-Charles-Map-Local` response header.

### Dispatcher rule tools

| Tool | Contract |
| --- | --- |
| `mock_route_setup` | Normalizes `host` (hostname or glob such as `*.example.com`, not `*`) and `path` (`/*` default, `/prefix/*` or one path) and upserts the route in `<CHARLES_MOCK_DIR>/_rules/routes.json`; `apply=false` returns the Map Remote steps, `apply=true` writes `https://<host>:<port><path>` → `http://127.0.0.1:<CHARLES_DISPATCHER_PORT>` (no destination path for wildcard paths, so Charles keeps the original path) with `preserveHostHeader=true`, backs the config up first, refuses while Charles runs, and is idempotent (a missing default `<enabled>` counts as true) |
| `mock_discover_variants` | Pages through the whole capture (windows of 200, up to `limit`) with the `api_focus` preset and optional `host_contains` / `path_contains` / `methods`; groups by method, host, path and the value at `body_field` (JSON or form; `null` when absent or for bodiless methods); returns `value_json`, `count`, up to 3 `entry_ids` and statuses per group, plus `total_groups` |
| `mock_rule_create_from_entry` | Matches the entry's method and path (or `match_path`, a pattern where `*` stands for one segment or part of one; it must cover the entry's path) plus the values at `match_body_fields` / `match_query_fields`; the rule host is `*` (`host_scope="any"`), the entry's host (`"exact"`) or `host`; `mode="patch"` stores `response_patches` / `request_patches` / `request_headers` (a value sets a header, `null` removes it; `Host` and `Content-Length` are refused), `mode="fixture"` stores the captured body unmodified as `<rule_id>.body` and applies `response_patches` at serve time; with `merge=true` an existing rule of the same variant and mode keeps its patches, a new patch on the same pointer replacing the old one; patches are dry-run against the captured request/response and rejected if they do not fit |
| `mock_rule_write` | Validates a full rule document; fixture rules need `fixture_json` or `fixture_text` unless a fixture already exists |
| `mock_rule_list` | Returns routes, rule summaries (`body_match_json` keeps null/false values) and `errors` for rule files the dispatcher ignores |
| `mock_rule_get` | Returns `rule_json` and, for fixture rules, the fixture truncated to `max_chars` |
| `mock_rule_set_enabled` | Rewrites the rule's `enabled` flag without archiving |
| `mock_rule_remove` | Moves the rule and its fixture to `_archive/_rules/<host>/<timestamp>/`; never deletes |
| `mock_dispatcher` | `start` runs the dispatcher on `127.0.0.1` inside the MCP server (it stops with it) and, with `toggle_map_remote=true` (default), enables Charles Map Remote once something answers on the port; `stop` stops that instance and disables Map Remote; `status` reports whether anything answers on the port; `verify` probes the dispatcher directly and then, through the Charles proxy, one URL per configured route, and reports for each whether Charles routed it to the dispatcher. Rules, fixtures and the stored mappings stay |
| | Charles loads Map Remote mappings only when it starts: a mapping written by `mock_route_setup(apply=true)` is inert until the user starts Charles. `verify` is how that is proven without touching the app |

Dispatcher behavior: requests not covered by a route in `routes.json` (exact host first, then the narrowest glob, then the path pattern) get 421; candidate rules are the host's own plus `_any/` rules whose host glob matches; the winner is the enabled matching rule with the higher `priority`, then an exact host over a glob, then an exact path over a path pattern, then more match conditions, then the lower `id`; unmatched requests are forwarded unchanged; upstream calls never use the system proxy; every response carries `X-Charles-MCP-Rule: <rule id | passthrough | error>`, and patch problems are reported in `X-Charles-MCP-Warning` instead of failing the request. Request and response bodies are never logged.

## Recommended call order

### Live

1. `start_live_capture`
2. `group_capture_analysis`
3. `query_live_capture_entries`
4. `get_traffic_entry_detail`
5. `stop_live_capture`

Why:

- `group_capture_analysis` is the cheapest in tokens, so it is the first look at hotspots
- `query_live_capture_entries` returns a structured summary, suited to repeated filtering
- `get_traffic_entry_detail` is used only once the target is confirmed

### History

1. `list_recordings`
2. `analyze_recorded_traffic`
3. `group_capture_analysis(source="history")`
4. `get_traffic_entry_detail`

## Summary-first conventions

### `query_live_capture_entries`

Fields to read:

- `items`
- `matched_count`
- `filtered_out_count`
- `filtered_out_by_class`
- `next_cursor`
- `warnings`

### `analyze_recorded_traffic`

Fields to read:

- `items`
- `matched_count`
- `filtered_out_count`
- `filtered_out_by_class`
- `warnings`

### `group_capture_analysis`

Common groupings:

- `host`
- `path`
- `response_status`
- `resource_class`
- `method`
- `host_path`
- `host_status`

Fields to read:

- `groups`
- `matched_count`
- `filtered_out_count`
- `filtered_out_by_class`
- `warnings`

## Data visibility contract

Raw content is returned by default:

- summary / detail / live / history output is not redacted
- if masking is needed, the MCP client or the agent has to do it

## Detail contract

### `get_traffic_entry_detail`

Rules:

1. Find the `entry_id` through a summary or a grouping first
2. Use `recording_path` for history
3. Use `capture_id` for live
4. Do not set `include_full_body=true` without a clear need

Defaults (tuned for the token budget):

| Parameter | Default | Meaning |
| --- | --- | --- |
| `include_full_body` | `false` | Whether to include the full body |
| `max_body_chars` | `2048` | Maximum characters of `full_text` |

Output serialization rules:

- `header_map` is not in the output (it is used only for internal matching); take headers from the `headers` list
- `parsed_json` and `parsed_form` are not in the output (`full_text` or `preview_text` cover them)
- when `full_text` is present, the redundant `preview_text` is removed
- every field whose value is `null` is stripped
- when the output exceeds 12,000 characters, `warnings` suggests narrowing the request

History detail binding rules:

- a history summary returns `recording_path`
- a live summary returns `capture_id`
- history detail without a source identity must fail
- there is no silent fallback to the latest recording

## `stop_live_capture` contract

### Success

```json
{
  "status": "stopped",
  "recoverable": false,
  "active_capture_preserved": false
}
```

Meaning:

- the stop succeeded
- the active capture is closed

### Recoverable failure

```json
{
  "status": "stop_failed",
  "recoverable": true,
  "active_capture_preserved": true
}
```

Meaning:

- the stop still failed after one short retry
- the capture is kept
- `read_live_capture` keeps working
- `stop_live_capture` can be called again

On `stop_failed` the agent should:

1. keep the `capture_id`
2. not assume the capture is closed
3. read `error` and `warnings`
4. call `charles_status` if needed
5. call `stop_live_capture` again to finish the cleanup

Related warnings:

- `stop_recording_retry_succeeded`
- `stop_recording_failed_after_retry`
