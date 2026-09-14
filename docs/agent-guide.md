# Agent guide

> Ukrainian version: [agent-guide.uk.md](agent-guide.uk.md)

The full agent guide ships inside the package — [charles_mcp/resources/agent_guide.md](../charles_mcp/resources/agent_guide.md) — so every agent gets it regardless of which project it works in (agents using the MCP server from an app repository never see this repository's files).

How it reaches the agent:

| Channel | What | When |
|---|---|---|
| MCP `instructions` | The hard rules and where to start (`charles_mcp/agent_instructions.py`) | Sent by the server when the client connects; Claude Code and other clients pass it to the model |
| MCP resource `charles-mcp://agent-guide` | The complete guide: every tool, workflows, Charles restarts, JSON Pointer patches, troubleshooting, limits | The agent reads it on demand (`read_resource`) |
| Tool descriptions | Per-tool contract and pitfalls | Always visible to the model |

A contract test (`tests/test_agent_guide.py`) fails if a public tool is missing from the guide, if the instructions lose a hard rule, or if the Charles-specific pitfalls disappear.

Related: [AGENTS.md](../AGENTS.md) (rules for agents working on this repository), [charles-mapping.md](charles-mapping.md) (mapping and Charles restarts for people), [contracts/tools.md](contracts/tools.md) (tool contract).
