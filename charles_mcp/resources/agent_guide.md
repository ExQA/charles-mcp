# charles-mcp — agent guide

Everything an agent needs to use this MCP server correctly: the operating model, hard rules, every tool, the main workflows, Charles-specific behaviour and troubleshooting. Served by the server as the MCP resource `charles-mcp://agent-guide`.

## 1. What this server is

- **Charles Proxy is the source of truth.** The app's traffic goes through Charles (a phone, a simulator or this computer). The server reads Charles through its Web Interface (`http://control.charles`, reached via the Charles proxy) and never captures traffic itself.
- **Every live query exports the whole Charles session.** Cost grows with session size. With Charles's macOS proxy on, Charles records *all* traffic of the computer and sessions grow fast.
- **Mocks are data, never code.** Map Local serves files from `~/charles-mocks/<host>/`; the local dispatcher serves rules from `~/charles-mocks/_rules/`. Charles holds only one static rule per host or domain.
- **Output is raw.** Tool results contain real headers, tokens, cookies and bodies, including personal and payment data. Treat everything you read as sensitive.

## 2. Hard rules

1. **Never clear the user's Charles session** unless they explicitly ask. `start_live_capture` adopts the running session by default; pass `reset_session=true` only on an explicit request — a wrong wipe cannot be undone.
2. **Never write the Charles config while Charles runs.** Charles rewrites its config from memory on quit, so the edit is lost. `apply=true` refuses while the proxy port is open; do not work around it.
3. **Never quit or restart Charles without the user's consent.** Quitting loses the unsaved session. Ask the user to save it (File → Save Session) first.
4. **Never call `reset_environment` unless the user asks for exactly that.** It quits Charles, overwrites its config from a backup and deletes saved recordings.
5. **Never replay requests or forward patched requests to production without consent.** `reverse_replay_entry` and dispatcher patch rules send real requests, with real credentials, to real servers.
6. **Never write captured values into source code, tests, commits or shared files,** and never suggest committing `~/charles-mocks`. Use synthetic data when a mock must be shared.
7. **Do not repeat secrets back to the user** (tokens, passwords, full card numbers) unless they are needed for the task; refer to them by field name.
8. **Never restructure a mocked response.** Keep the captured shape: key order, nesting, wrappers, field names, array order and scalar types (`0` is not `0.0`, `"1"` is not `1`). Build the mock from a captured entry and change only the fields the user named; never retype a response from memory. Apps parse more strictly than JSON requires, and a reshaped body fails while still answering 200 (section 6.5).
9. **Remember that Charles loads Map Remote mappings only at startup.** After `apply=true` the user must start Charles; until then the mapping is inert however right the config file looks. When routed traffic never reaches the dispatcher, run `mock_dispatcher(action="verify")` before suspecting the rule.
10. **Leave the environment as you found it:** remove or disable the mocks you added when the scenario ends, stop the dispatcher you started, and say which Charles rules stay configured.

## 3. First checks

1. `charles_status` — Charles reachable? Web Interface credentials right? Active capture? Follow its `recommended_next_action`.
2. HTTPS: Charles must have **SSL Proxying** enabled for the API hosts and the device must trust the Charles certificate. Without it Charles only sees a tunnel, the path is unknown, and path-based rules never fire.
3. Session size: if exports are slow, suggest clearing the session before the scenario and, when only a phone is tested, turning off Proxy → macOS Proxy in Charles.
4. A `charles_records_own_exports` warning means Charles is recording this server's own session exports, and the session will grow on every call. Relay the fix it names to the user — exclude host `control.charles` in Proxy → Recording Settings, then clear the session — rather than carrying on; clearing the session is theirs to do.

## 4. Identities

| Plane | Identity | Created by |
|---|---|---|
| live capture | `capture_id` | `start_live_capture` |
| saved recording (`.chlsj`) | `recording_path` | `list_recordings` / history summaries |
| reverse imported capture | reverse `capture_id` | `reverse_import_session` |
| reverse live session | `live_session_id` | `reverse_start_live_analysis` |
| traffic entry | `entry_id` | any summary; valid only with the identity it came from |
| mock rule | `host` + `rule_id` | `mock_rule_create_from_entry` / `mock_rule_write` |

Keep identities between calls and never mix them across planes.

## 5. Tools

### 5.1 Live capture

| Tool | Use | Notes |
|---|---|---|
| `start_live_capture(reset_session=false, adopt_existing=true, start_recording_if_stopped=true)` | Begin reading the current session | **The capture always includes the traffic recorded before the call**; there is no option to hide it. The first `read_live_capture` returns the existing entries, later reads only new ones. Only one capture can be active |
| `query_live_capture_entries(capture_id, ...)` | Structured, filtered summaries | Read-only, does not move the cursor. Filters: `host_contains`, `path_contains`, `method_in`, `status_in`, header name/value, content types, `request_body_contains` / `response_body_contains`, `request_json_query` / `response_json_query` (JMESPath), size, `since_seconds`. Defaults are small (`max_items=10`, `scan_limit=500`) — raise them for big sessions |
| `read_live_capture(capture_id, cursor, limit)` | Consume new entries (route-level only) | Advances the cursor |
| `peek_live_capture(capture_id, cursor, limit)` | Preview new entries | Does not advance |
| `stop_live_capture(capture_id, persist=true)` | End the capture | `persist=true` saves a `.chlsj` snapshot to the state dir. `status="stop_failed"` with `recoverable=true` means the capture is still alive — call again |

### 5.2 Analysis

| Tool | Use |
|---|---|
| `group_capture_analysis(source, group_by, capture_id / recording_path, ...)` | Cheapest overview: groups by `host`, `path`, `response_status`, `resource_class`, `method`, `host_path`, `host_status` |
| `get_capture_analysis_stats(source, ...)` | Counts by resource class |
| `get_traffic_entry_detail(source, entry_id, capture_id / recording_path, include_full_body=false, max_body_chars=2048)` | One entry in full. Keep `include_full_body=false` unless the body is needed; watch `warnings` for oversized output |

### 5.3 Saved recordings (only when the user mentions a saved file)

`list_recordings`, `get_recording_snapshot(path)`, `analyze_recorded_traffic(recording_path, ...)`, `query_recorded_traffic(host_contains, http_method, keyword_regex)`.

### 5.4 Status and control

| Tool | Notes |
|---|---|
| `charles_status` | Connectivity, active capture, suggested next step |
| `throttling(preset)` | `3G`, `4G`, `5G`, `fibre`, `56k`, `256k`, `off`. Affects all Charles traffic; turn it off afterwards |
| `reset_environment` | **Destructive** (rule 4) |
| `purge_stored_data(older_than_days=30, scopes, dry_run=true)` | Deletes saved captures (`recordings`), reverse-analysis data with bodies (`reverse`) and Charles config backups (`backups`) older than the cut-off. Always run `dry_run=true` first, show the user the list, and delete only after they agree — it cannot be undone. Never touches reset_environment's baseline, the newest backup in each folder, or mocks. `CHARLES_RETENTION_DAYS` runs the same purge at every server start |

### 5.5 Reverse analysis

Import (`reverse_import_session`, list with `reverse_list_captures`), query (`reverse_query_entries`, `reverse_get_entry_detail`), decode incl. protobuf (`reverse_decode_entry_body`), replay with overrides (`reverse_replay_entry` — rule 5), signature hunting (`reverse_discover_signature_candidates`), findings (`reverse_list_findings`), live sessions (`reverse_start_live_analysis`, `reverse_peek_live_entries`, `reverse_read_live_entries`, `reverse_stop_live_analysis`, `reverse_charles_recording_status`) and task workflows (`reverse_analyze_live_login_flow`, `reverse_analyze_live_api_flow`, `reverse_analyze_live_signature_flow`). Read `summary` and `report` before `evidence`.

### 5.6 Map Local mocks (one URL = one response)

| Tool | Notes |
|---|---|
| `mock_setup_host(host, apply=false)` | Once per host. `apply=false` returns manual UI steps (no restart); `apply=true` writes Map Local + a Rewrite rule (text/plain → JSON) into the Charles config — **Charles must be closed** (section 7) |
| `mock_create_from_entry(source, entry_id, capture_id / recording_path, patches)` | Captured response → file at `~/charles-mocks/<host>/<path>`, with JSON Pointer patches |
| `mock_write(host, path, body or body_text)` | Mock from scratch |
| `mock_list`, `mock_get`, `mock_remove` | Remove archives the file; the request goes to the server again |
| `mock_set_enabled(enabled)` | Whole Map Local tool on/off |

Limits: status is always 200; the query string and HTTP method are ignored (all variants get one file); `/users` and `/users/42` cannot both be files. Verify with the `X-Charles-Map-Local` response header.

### 5.7 Dispatcher rules (many paths, `action` in the body, any status, request edits)

| Tool | Notes |
|---|---|
| `mock_route_setup(host, path="/*", apply=false, port=443, verify_tls=true)` | Once per **domain**: `host="*.example.com"`, `path="/api/*"`. One Charles Map Remote rule covers every host, path and action under it. `apply=true` needs Charles closed (section 7). `verify_tls=false` for servers with an internal CA |
| `mock_discover_variants(source, capture_id / recording_path, host_contains, path_contains, body_field="/action", methods, limit, max_groups)` | Whole-session overview: method × host × path × body-field value (`null` for GETs and bodies without the field), counts, sample `entry_ids`, statuses. One export. For history it returns the `recording_path` to reuse |
| `mock_rule_create_from_entry(source, entry_id, capture_id / recording_path, match_body_fields, match_query_fields, mode="patch", response_patches, request_patches, request_headers, status, delay_ms, priority, host_scope="any", host, match_path, merge=true, rule_id, description)` | The main tool. Details in 6.3 |
| `mock_rule_write(rule, fixture_json or fixture_text)` | Full rule document by hand (advanced) |
| `mock_rule_list(host)`, `mock_rule_get(host, rule_id)` | Review; `errors` lists rule files the dispatcher ignores |
| `mock_rule_set_enabled(host, rule_id, enabled)`, `mock_rule_remove(host, rule_id)` | Pause or archive a rule. `host` is the **rule's** host: `*` for general rules |
| `mock_dispatcher(action="start" / "stop" / "status", port, toggle_map_remote=true)` | Start also turns Charles Map Remote on; stop turns it off. The dispatcher lives inside the MCP server and stops with it; for long runs the user can start `charles-mcp-dispatcher --port 18080` in a terminal instead |
| `mock_dispatcher(action="verify")` | Proves the whole path without the app: it probes the dispatcher directly, then sends one probe per route through the Charles proxy and says for each whether Charles routed it. Use it whenever a rule seems not to fire — most often the answer is that Charles was never restarted after `apply=true` |
| `mock_scenario_save(name, rules=None, description)` | Names a set of rules — a whole QA flow such as "payment fails after login". Without `rules` it saves the rules enabled right now, so the usual order is: build the rules, enable exactly those, save |
| `mock_scenario_apply(name, enabled=true, exclusive=true)` | Switches a flow on in one call; `exclusive` turns every other rule off so mocks from the previous flow cannot linger. `enabled=false` turns only this scenario's rules off |
| `mock_scenario_list()` / `mock_scenario_remove(name)` | Shows which scenarios are fully active and which refer to rules that no longer exist; removing a scenario never touches its rules |

## 6. Main workflow: fake data from a real session

### 6.1 Decide the mechanism

- GET, unique URL, 200 is fine → **Map Local** (5.6).
- POST with an operation field (`action`), several paths or hosts, error statuses, request edits → **dispatcher rules** (5.7).

### 6.2 One-time setup per domain

1. `mock_route_setup(host="*.example.com", path="/api/*")` without `apply` → show the user the Map Remote steps, **or** follow section 7 and call it with `apply=true`.
2. Confirm SSL Proxying covers the domain.

### 6.3 Per session

1. **Read the session.** `start_live_capture()` sees everything already recorded in Charles. For a new flow the user runs it after the start; the new entries join the same capture.
2. **Overview.** `mock_discover_variants(source="live", capture_id, host_contains=...)`. Show the user the variants (method, path, `action`, count, status) and ask what to change if they have not said.
3. **For each requested change**, pick a sample `entry_id` of that variant and call `mock_rule_create_from_entry`:
   - `match_body_fields=["/action"]` for POST variants; leave empty for GETs (add `match_query_fields` when a query parameter selects the variant).
   - `mode="patch"` (default): the real server answers; `response_patches` change named response fields, `request_patches` change the request body and `request_headers` its headers before it leaves. Preferred — data stays fresh.
   - `status` overrides the response code in either mode (`500`, `401`, `429`). A code alone keeps the real success body, which an app may not treat as an error; for a realistic failure use `mode="fixture"` with an error body.
   - `delay_ms` waits before answering (0–60000), to test loaders, spinners and client timeouts. It delays only requests this rule matches; `throttling` slows all Charles traffic instead.
   - `request_headers` sets or replaces a header, and `null` removes it: `{"X-App-Version": "9.9.9", "X-Debug": null}`. `Host` and `Content-Length` are managed by the dispatcher and refused with a warning. Ignored in fixture mode, where nothing goes upstream.
   - `mode="fixture"`: the captured response is served (patches applied at serve time) with any `status`, without contacting the server — for errors and offline cases.
   - `host_scope="any"` (default) applies on every host of the route; `"exact"` or `host="stage.example.com"` narrows it. An exact-host rule wins over a general one.
   - Do not pin path segments that vary by platform or app version: `match_path="/api/*/payoneer"` covers `/api/p24-aos2/payoneer` and its iOS twin. `*` stands for one segment or part of one (`p24-*`), never crosses `/` (`**` is not special), and must cover the sample entry's path; ask the user which segments vary when the session shows only one platform.
   - When two rules match one request, the winner is chosen by `priority` (higher first), then an exact host over a glob, then an exact path over a pattern, then more match conditions. So an exact-path rule with no body condition beats a `/api/*/payoneer` rule that keys on `action`; when you want the `action` rule to win for that path, give it a higher `priority`.
   - Asking again for the same variant **merges** into its rule; a patch on the same field replaces the earlier one. `merge=false` replaces the whole rule.
   - Patches are validated against the captured request/response; an error means the path is wrong — fix it, do not force it.
4. `mock_dispatcher(action="start")`.
5. The user repeats the actions. Verify: `query_live_capture_entries(capture_id, path_contains=..., response_header_name="X-Charles-MCP-Rule")` — the header holds the rule id; `passthrough` means no rule matched.
6. Report what is mocked (variant, fields, mode, scope).
7. When done: `mock_rule_set_enabled(false)` or `mock_rule_remove`, then `mock_dispatcher(action="stop")` (turns Map Remote off, traffic goes to real servers).

### 6.4 JSON Pointer patches

| Goal | Patch |
|---|---|
| Replace a value | `{"op": "set", "path": "/data/balance", "value": 0}` |
| Add a key | `{"op": "set", "path": "/data/newFlag", "value": true}` |
| Edit the array item with a given field value | `{"op": "set", "path": "/data/balanceList/[currency=USD]/balance", "value": "1000.00"}` |
| Replace an array item by position | `{"op": "set", "path": "/data/items/0/status", "value": "blocked"}` |
| Append to an array | `{"op": "set", "path": "/data/items/-", "value": {...}}` |
| Remove a key or item | `{"op": "remove", "path": "/data/banner"}` |
| Replace the whole body | `{"op": "set", "path": "", "value": {...}}` |

**Address array items by a field, not by an index**, in patch rules especially: servers often return lists (balances, cards, accounts) in a different order each time, and `/balanceList/1` then edits the wrong item. A `[field=value]` segment selects the one element whose field equals the value; strings compare as they are, numbers and bools by JSON value (`[id=42]`, `[balance=0]` matches `0.0`, `[active=true]`). A `/` inside the value is escaped as `~1` like any JSON Pointer token (`[path=~1api]` for `/api`), and `~` as `~0`. No match, or more than one, fails the patch (`X-Charles-MCP-Warning`) instead of editing a wrong item. Pick a field that identifies one element.

Keys containing `/` or `~` are escaped as `~1` and `~0`. Intermediate objects are never created implicitly. Form bodies (`application/x-www-form-urlencoded`) are patched as flat objects: `{"op": "set", "path": "/lang", "value": "en"}`.

### 6.5 Keep the captured response contract

Some apps parse a response more strictly than a JSON library would: a reordered object, a missing wrapper or a number that turned from `0.0` into `0` can produce a generic "server unavailable" screen even though the mock answered 200. Key order carries no meaning in JSON, yet a fixture should still stay as close to the captured response as the tools allow.

1. **Start from the real entry**, not from memory: `get_traffic_entry_detail(..., include_full_body=true)`, raising `max_body_chars` until the body is complete. A truncated preview hides exactly the tail where the shape breaks.
2. **Build the fixture from that entry** — `mock_rule_create_from_entry(mode="fixture")` or `mock_create_from_entry` — and change only the named fields with patches. Both tools keep key order, field names, array order and scalar types (`0` stays an int, `0.0` stays a float), but they re-serialize the JSON: indentation changes, a trailing newline is added, `1.50` becomes `1.5`, `\u0421` becomes the character itself, and duplicate keys collapse. When the app needs the original bytes, write them with `mock_rule_write(fixture_text=...)` or edit the generated `<rule_id>.body` file.
3. **Keep the headers the app reads**, above all `Content-Type` with its charset; creating a rule from an entry copies the captured one. The dispatcher owns the transport headers and rewrites them on every response: `Content-Length`, `Content-Encoding` (bodies reach the app already decompressed), `Date`, `Server` and its own `X-Charles-MCP-Rule`. Those are not part of the application contract.
4. **Verify against the real thing.** After the app repeats the request, compare the new entry with the original through `get_traffic_entry_detail(include_full_body=true)`: rule header, status, content type, top-level shape, array order and scalar types. HTTP 200 alone proves only that something answered.

### 6.6 Other QA recipes

- **Error screen**: fixture rule with `status=500` (or 401, 404) for that variant.
- **Empty state**: `{"op": "set", "path": "/data/items", "value": []}`.
- **One slow endpoint**: `delay_ms=5000` on its rule — for loaders and client timeouts.
- **Slow network overall**: `throttling("3G")` — affects all Charles traffic.
- **Analytics check**: `query_live_capture_entries(host_contains=<tracker>, request_body_contains=<event>)`.

## 7. Charles restarts

Only adding the Charles rule for a new host or domain needs a restart; mock files, dispatcher rules and turning tools on/off never do. The tools never quit or start Charles. With the user:

1. Ask them to save the session if needed and quit Charles (or, with explicit consent, quit it via `http://control.charles/quit`).
2. Ask them **not to reopen Charles** until you report the rule is written.
3. Call `mock_route_setup(..., apply=true)` or `mock_setup_host(..., apply=true)`. The config is backed up to the charles-mcp state dir (it contains the Charles license key and Web Interface credentials).
4. Ask them to start Charles; the rule is loaded.

If a restart is unwelcome, give the manual UI steps (`apply=false`): they apply immediately after **Done** — Cancel discards them.

The Map Remote rule that `mock_route_setup` writes or dictates:

| Field | Value |
|---|---|
| From | Protocol `https`, Host `*.example.com` (or one host), Port `443`, Path `/api/*` |
| To | Protocol `http`, Host `127.0.0.1`, Port `18080` (`CHARLES_DISPATCHER_PORT`), Path **empty** — Charles then keeps the original path |
| Preserve host header | **on** — the dispatcher needs the original `Host` to pick routes and rules |

Verified on Charles 5.2.1: a host glob matches its subdomains, the path reaches the dispatcher unchanged, requests outside the path pattern are not mapped. Importing an XML file in the Map Remote window fails in Charles 5.2.1; do not suggest it.

## 8. Troubleshooting

| Symptom | Cause |
|---|---|
| No `X-Charles-MCP-Rule` header | Map Remote off (start the dispatcher), the Charles rule was not saved (Cancel instead of Done), host/path/protocol mismatch, no SSL Proxying |
| Charles 503 "Name lookup failed" | Map Remote did not apply; Charles went to the original host |
| `passthrough` | Request reached the dispatcher but no rule matched: path, method, `action` value, disabled rule |
| 421 from the dispatcher | Host or path not covered by a route — `mock_route_setup` |
| 502 from the dispatcher | Upstream unreachable: network/VPN, timeout, certificate (`verify_tls`) |
| Every request to the domain fails | Map Remote on, dispatcher not running |
| `X-Charles-MCP-Warning` | A patch did not apply (non-JSON body, path not found); response sent without it |
| Mocked value did not change in the app | The app may cache; ask the user to pull-to-refresh or relaunch; check the rule matched |
| Exports are slow / time out | Session too big: clear it, turn off macOS proxy recording, narrow with `host_contains` |
| Config edit "disappeared" | Written while Charles ran; redo per section 7 |

## 9. Limits

- Map Local: always 200; query and method ignored.
- Dispatcher: no WebSockets; downloads are buffered in full; gzip-compressed request bodies are not parsed; a JSON `true` does not equal a form `"true"` in matching; paths match exactly unless the rule uses `*` (`/users/1` ≠ `/users/2`).
- No timeout or dropped-connection simulation: only `delay_ms` and the response code.
- Only one live capture at a time.

## 10. Reporting to the user

State what you looked at (capture, filters), what you changed (variant, fields, mode, scope), how you verified it (header, status), what is still active (rules, dispatcher, Charles rules), and what the user should do next. Do not paste raw tokens or full card numbers.
