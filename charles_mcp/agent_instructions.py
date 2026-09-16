"""What the MCP client tells the model about this server, and the full agent guide."""

from __future__ import annotations

from importlib.resources import files

AGENT_GUIDE_URI = "charles-mcp://agent-guide"

SERVER_INSTRUCTIONS = f"""\
charles-mcp reads Charles Proxy through its Web Interface and mocks app traffic.
Read the full guide before mocking or changing Charles: resource {AGENT_GUIDE_URI}.

Hard rules:
1. Never clear the user's Charles session unless asked (reset_session=true wipes it).
2. Never write the Charles config while Charles runs; apply=true needs Charles closed.
   The tools never quit or start Charles: ask the user to save the session, quit,
   not reopen until the write is done, then start it. Manual UI steps need no restart.
3. Never call reset_environment unless the user asks for exactly that.
4. Replays and dispatcher patch rules send real requests with real credentials;
   do not aim them at production without consent.
5. Output is raw traffic (tokens, personal and payment data): never copy captured
   values into code, tests or shared files; do not repeat secrets back.
6. Never restructure a mocked response. Keep the captured shape: key order, nesting,
   wrappers, field names, array order and scalar types (0 is not 0.0, "1" is not 1).
   Build the mock from a captured entry and change only the fields the user named;
   never retype a response from memory or reorder it to look tidier. Apps parse more
   strictly than JSON requires, and a reshaped body fails while still answering 200.
7. Charles loads Map Remote mappings only when it starts. After apply=true the user
   must start Charles, or the mapping is inert however right the config file looks.
   Prove the path with mock_dispatcher(action="verify") before blaming a rule.
8. Clean up: disable or remove the mocks you added and stop the dispatcher.

Start: charles_status, then start_live_capture() — the capture always includes
the traffic already recorded in Charles. Overview: group_capture_analysis or
mock_discover_variants. Detail: get_traffic_entry_detail for one entry_id.
Mocking: Map Local (mock_*) for one URL = one response; dispatcher rules
(mock_route_setup once per domain, mock_rule_create_from_entry, mock_dispatcher)
for many paths, an `action` field in the body, any status or request edits.
Verify with the X-Charles-Map-Local / X-Charles-MCP-Rule response headers, and
mock_dispatcher(action="verify") when routed traffic never reaches the dispatcher.
"""


def agent_guide() -> str:
    return files("charles_mcp.resources").joinpath("agent_guide.md").read_text(encoding="utf-8")
