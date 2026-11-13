---
name: charles-proxy-qa
description: Inspect, replay and mock the HTTP traffic of a mobile or web app through Charles Proxy with the charles-mcp MCP server. Use when the user asks what an app sent or received, wants to find an API call or an analytics event, or wants the app to see different server data (an empty list, a zero balance, a 500 or 401, a slow response) without touching the backend. Also covers saved .chlsj sessions and replaying or decoding captured requests. Needs Charles Proxy running with its Web Interface enabled.
---

# Charles Proxy QA

Charles sits between the app (phone, simulator or browser) and its servers. The charles-mcp server reads Charles through its Web Interface and can answer instead of the server, so you can test any screen state without a backend change. Charles is the source of truth: the server never captures traffic on its own.

The full reference, with every tool and parameter, is the MCP resource `charles-mcp://agent-guide`. Read it when a step below is not enough.

## Setup

Call `charles_status`. If the tool exists and reports Charles reachable, skip this section.

If there are no `charles_*` tools, the MCP server is not installed. Give the user the command for their agent and ask them to restart the agent afterwards:

```bash
# Claude Code
claude mcp add charles --scope user \
  -e CHARLES_USER=admin -e CHARLES_PASS=123456 -e CHARLES_MANAGE_LIFECYCLE=false \
  -- uvx --from git+https://github.com/ExQA/charles-mcp charles-mcp
```

Other agents take the same command in their MCP config: command `uvx`, args `["--from", "git+https://github.com/ExQA/charles-mcp", "charles-mcp"]`, and the three environment variables. It needs [uv](https://docs.astral.sh/uv/). Never suggest `uvx charles-mcp` or `pip install charles-mcp`: that PyPI package is an older project without the mocking tools.

If Charles is not reachable, the user has to do this in Charles once (you cannot):

1. `Proxy -> Web Interface Settings`: enable the Web Interface and a user. `admin` / `123456` are the server's defaults; other values go into `CHARLES_USER` / `CHARLES_PASS`.
2. `Proxy -> Recording Settings -> Exclude`: add host `control.charles`. Without it every call makes the session bigger and slower.
3. `Proxy -> SSL Proxying Settings`: add the app's API hosts, and make the device trust the Charles root certificate. Without it HTTPS is one opaque tunnel.

## Rules

1. Never clear the user's Charles session unless they ask. `start_live_capture()` keeps what is already recorded; `reset_session=true` only on an explicit request.
2. Never quit or restart Charles yourself, and never call `apply=true` while Charles runs. Ask the user to save the session and quit first.
3. Never open or edit the Charles config file (`com.xk72.charles.config`, `charles.config`) yourself: it holds the license key and the Web Interface password. Call `charles_config_summary` instead: it returns the mappings, SSL Proxying hosts and recording excludes without the secrets, and names what breaks each Map Remote mapping.
4. Never call `reset_environment` unless the user asks for exactly that.
5. Never replay a request or send a patched request to a real server without the user's consent: it goes out with real credentials.
6. Tool output holds real tokens, cookies and personal data. Do not repeat secrets, and never write captured values into source code, tests or commits.
7. Build every mock from a captured response and change only the fields the user named. Keep key order, nesting, array order and number types: apps parse more strictly than JSON requires.
8. Summary before detail: group and query first, open one entry at a time, and keep `include_full_body=false` unless you need the body.
9. Keep the ids you are given (`capture_id`, `entry_id`, `recording_path`, `live_session_id`) and never mix them between live, history and reverse tools.
10. Leave things as you found them: disable the mocks you added, stop the dispatcher, and say which Charles rules stay on.

## First call of every task

`charles_status`, then follow its `recommended_next_action`. Relay its warnings instead of working around them:

- `map_remote_without_dispatcher`: every request under a mocked route fails in the app. Start the dispatcher or run `mock_dispatcher(action="stop")`.
- `charles_records_own_exports`: setup step 2 is missing.
- `ssl_proxying_off` (on a query): Charles saw only tunnels for that host. Say so; do not report that the app sent nothing.
- `ssl_proxying_off` or a mapping that never fires: `charles_config_summary` shows which hosts SSL Proxying and Map Remote cover.
- `slow_export`: the session is large. Suggest clearing it before the scenario, which is the user's call.

## See what the app does

1. `start_live_capture()` and keep the `capture_id`. Ask the user to repeat the action in the app.
2. Overview: `group_capture_analysis(source="live", capture_id, group_by="host_path")`.
3. Narrow: `query_live_capture_entries(capture_id, host_contains=..., path_contains=..., method_in=[...], response_body_contains=...)`. Keep `max_items` small.
4. One entry: `get_traffic_entry_detail(source="live", capture_id, entry_id)`; add `include_full_body=true` only when the body matters.
5. For only the new requests, use `read_live_capture` (moves the cursor) or `peek_live_capture` (does not).
6. Analytics check: `query_live_capture_entries(host_contains=<tracker>, request_body_contains=<event name>)`.
7. Finish with `stop_live_capture(capture_id)`. Only `status="stopped"` means closed; `stop_failed` with `recoverable=true` means call it again.

## Change what the server returns

Pick the mechanism by the endpoint:

- A GET whose response does not depend on the request body, where 200 is fine: Map Local (`mock_setup_host`, `mock_create_from_entry`). Verify with the `X-Charles-Map-Local` header.
- Anything else (an `action` field in the body, several paths or hosts, error codes, delays, request edits): dispatcher rules, as follows.

1. **Once per domain:** `mock_route_setup(host="*.example.com", path="/api/*")`. Without `apply` it returns the Map Remote steps to do in Charles, which work at once. With `apply=true` it writes them itself, but only while Charles is closed, and Charles loads them on its next start.
2. **Overview of the session:** `mock_discover_variants(source="live", capture_id, host_contains=...)` lists method, path, `action` value, count and status. Show it to the user and ask what to change if they have not said.
3. **One rule per variant:** `mock_rule_create_from_entry(source="live", capture_id, entry_id, ...)` with one sample entry of that variant:
   - `match_body_fields=["/action"]` for POST variants; `match_query_fields` when a query parameter selects the variant.
   - `mode="patch"` (default): the real server answers and `response_patches` edit named fields. Prefer this.
   - `mode="fixture"` with `status=500` (or 401, 404): the captured response is served without the server, for error and offline cases. A status alone keeps the captured success body, which many apps still render as success; for a real error screen also replace the body with the app's error shape, taken from a captured error response when there is one: `response_patches=[{"op": "set", "path": "", "value": {...}}]`.
   - `delay_ms=5000` for loaders and client timeouts on this endpoint only.
   - `match_path="/api/*/wallet"` when a path segment differs by platform or app version.
   - Asking again for the same variant merges into its rule.
4. **Patches** are JSON Pointer: `{"op": "set", "path": "/data/balance", "value": 0}`, `{"op": "set", "path": "/data/items", "value": []}`, `{"op": "remove", "path": "/data/banner"}`. Select array items by a field, never by position, because servers reorder lists: `/data/accounts/[currency=USD]/balance`.
5. `mock_dispatcher(action="start")`. The user repeats the action in the app. The dispatcher lives inside the MCP server and stops with it; for a long run the user can keep one in a terminal: `uvx --from git+https://github.com/ExQA/charles-mcp charles-mcp-dispatcher --port 18080`.
6. **Verify:** `query_live_capture_entries(capture_id, path_contains=..., response_header_name="X-Charles-MCP-Rule")`. The header holds the rule id; `passthrough` means no rule matched. If nothing arrives at all, run `mock_dispatcher(action="verify")`; most often Charles was not restarted after `apply=true`.
7. **Whole flows:** `mock_scenario_save(name)` stores the rules enabled now; `mock_scenario_apply(name)` switches a flow on and the others off.
8. **Clean up:** `mock_rule_set_enabled(..., enabled=false)` or `mock_rule_remove`, then `mock_dispatcher(action="stop")`.

`throttling("3G")` slows all Charles traffic; turn it `off` afterwards.

## Saved sessions and reverse analysis

Use these only when the user names a `.chlsj` file or asks to replay or decode:

- Saved files: `list_recordings`, `analyze_recorded_traffic(recording_path)`, `query_recorded_traffic(...)`, then `get_traffic_entry_detail(source="history", recording_path, entry_id)`.
- Replay, decode and signatures: `reverse_import_session`, `reverse_query_entries`, `reverse_decode_entry_body` (protobuf too), `reverse_replay_entry` (rule 5), `reverse_discover_signature_candidates`. For live flows with a known goal: `reverse_analyze_live_login_flow`, `reverse_analyze_live_api_flow`, `reverse_analyze_live_signature_flow`. Read `summary` and `report` before `evidence`.

## When something is off

| Symptom | Likely cause |
|---|---|
| Charles answers 503 "Name lookup failed" for a mocked host | The Map Remote destination is not a host name (a whole URL typed into Host). `mock_dispatcher(action="verify")` confirms it; the user fixes Map To in `Tools -> Map Remote`: protocol `http`, host `127.0.0.1`, port `18080`, empty path |
| Rule never fires, no `X-Charles-MCP-Rule` header | Check `charles_config_summary`: each mapping lists its `problems`. Otherwise Map Remote off or not loaded yet (restart Charles after `apply=true`), host or path mismatch, no SSL Proxying |
| `passthrough` | Request reached the dispatcher, no rule matched: path, method, `action` value, or rule disabled |
| 421 from the dispatcher | Host or path not covered by `mock_route_setup` |
| 502 from the dispatcher | Real server unreachable: network, VPN, certificate (`verify_tls=false` for an internal CA) |
| Value did not change in the app | App cache: pull to refresh or relaunch |
| Every call is slow | Session too big: clear it, turn off `Proxy -> macOS Proxy` when testing a phone |

## Report back

Say what you looked at (capture, filters), what you changed (variant, fields, mode), how you verified it (header, status), what is still active (rules, dispatcher, Charles mappings) and what the user should do next. Refer to secrets by field name.
