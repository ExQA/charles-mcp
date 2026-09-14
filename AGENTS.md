# AGENTS.md

This file defines **global agent behavior rules** for `charles-mcp`.
Agents that *use* the MCP server from another project get the rules from the server itself: MCP `instructions` and the resource `charles-mcp://agent-guide` ([charles_mcp/resources/agent_guide.md](charles_mcp/resources/agent_guide.md)); keep them in sync with this file.
For task-specific call sequences, see [docs/agent-workflows.md](docs/agent-workflows.md).
For canonical public tool contracts, see [docs/contracts/tools.md](docs/contracts/tools.md).

---

## 1. Global operating model

- **summary first, detail on demand**
- **live and history are separate source planes**
- **reverse analysis extends traffic inspection into replay / decode / signature workflows**
- **state identities must be preserved**: `capture_id`, `cursor`, `recording_path`, `live_session_id`

Do not treat this server as a raw packet dump interface.

---

## 2. Identity and plane rules

1. Never mix source identities:
   - live plane: `capture_id`
   - history plane: `recording_path`
   - reverse imported-analysis plane: reverse `capture_id`
   - reverse live-analysis plane: `live_session_id`
2. Preserve and reuse returned identifiers between calls.
3. Do not infer cross-plane identity fallback in agent logic.
4. **Default plane = live.** When the user asks about ongoing / just-now
   traffic without explicitly naming a saved recording,
   start with `start_live_capture` and stay on the live plane. Only switch to
   `list_recordings` / `query_recorded_traffic` / `analyze_recorded_traffic`
   when the user explicitly references a `.chlsj` file.
5. When unsure which plane to use, call `charles_status` first and follow its
   `recommended_next_action`.
6. **Never clear the user's Charles session unless the user explicitly asks.**
   `start_live_capture` defaults to adopting the user's ongoing session and
   preserves the traffic already captured there; that traffic is always part
   of the capture, so query and read tools see it. Only pass `reset_session=true`
   when the user explicitly asks to clear / wipe the session before
   starting a fresh capture. A wrongly-passed `reset_session=true` destroys
   the user's in-progress data and is not recoverable from this server.

---

## 3. Selection and expansion rules

1. Prefer group/summary tools before detail tools:
   - `group_capture_analysis`
   - `query_live_capture_entries`
   - `analyze_recorded_traffic`
   - `reverse_query_entries`
   - task-oriented reverse live workflow tools
2. Use detail tools only for one confirmed target:
   - `get_traffic_entry_detail`
   - `reverse_get_entry_detail`
3. Do not default to full body expansion:
   - keep `include_full_body=false` unless explicitly needed

---

## 4. Read vs peek semantics

- `read_*` tools consume or advance the current increment/cursor.
- `peek_*` tools preview without consuming or advancing.
- Choose intentionally; do not replace one with the other casually.

---

## 5. Reverse workflow usage principles

1. For reverse imported analysis:
   - narrow with `reverse_query_entries` before detail/decode/replay.
2. For reverse live analysis:
   - preserve `live_session_id`
   - prefer task-oriented tools when goal is known:
     - `reverse_analyze_live_login_flow`
     - `reverse_analyze_live_api_flow`
     - `reverse_analyze_live_signature_flow`
3. For task-oriented outputs:
   - read `summary` and `report` first
   - expand `evidence` only when needed

---

## 6. Recovery and token-budget principles

1. `stop_live_capture`:
   - only `status="stopped"` means fully closed
   - `stop_failed` with `recoverable=true` and `active_capture_preserved=true` means session still exists
2. Prefer status checks over blind retries.
3. Keep token usage low:
   - group first
   - keep `max_items` small
   - avoid bulk detail expansion

---

## 7. Mocking rules

1. Pick the mechanism by the endpoint:
   - a URL whose response does not depend on the request body: Map Local (`mock_setup_host`, `mock_create_from_entry`);
   - API traffic where the response depends on a body field such as `action`, several paths or hosts, a non-200 status or a request edit: dispatcher rules (`mock_discover_variants`, `mock_rule_create_from_entry`, `mock_route_setup`, `mock_dispatcher`).
   - route once per domain (`mock_route_setup(host="*.example.com", path="/api/*")`), not once per URL.
2. Start from `mock_discover_variants` over the whole session, then build each mock from a captured entry of that variant and change only the fields the user named. Keep the default `host_scope="any"` unless the user asks for one host, and replace path segments that vary by platform or app version with `*` via `match_path` (`/api/*/payoneer`); repeated requests for the same variant merge into its rule. Prefer `mode="patch"` (real response, named fields edited) over `mode="fixture"` unless the user needs the server out of the loop or a specific status.
3. Mock data is data, never code: never write captured values into source files or tests, and never suggest committing `CHARLES_MOCK_DIR`. Captured fixtures can hold personal or payment data.
4. `apply=true` on `mock_setup_host` / `mock_route_setup` needs Charles closed; ask the user to save the session and quit Charles (the unsaved session is lost), tell them not to reopen it until the rule is written, then ask them to start it. Never write the Charles config while it runs. Offer manual entry in the Charles UI when a restart is unwelcome. Details: [docs/charles-mapping.md](docs/charles-mapping.md).
5. Verify every mock against fresh traffic: `X-Charles-Map-Local` for Map Local, `X-Charles-MCP-Rule` for dispatcher rules (`passthrough` means no rule matched).
6. Remove or disable mocks when the scenario ends; tell the user which Charles mappings stay active and that the dispatcher must run while they do.
