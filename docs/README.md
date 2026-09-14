# Documentation Hub

This page is the canonical documentation entrypoint for `charles-mcp`.

## Document map

- [README.md](../README.md): project positioning, installation, quick start (Ukrainian, main)
- [README.en.md](../README.en.md): project positioning, installation, quick start (English)
- [Ukrainian documentation hub](./README.uk.md): Ukrainian translations of the docs below
- [AGENTS.md](../AGENTS.md): global agent behavior rules
- [Agent guide](./agent-guide.md): the guide the server hands to agents (MCP instructions + resource `charles-mcp://agent-guide`)
- [Mapping & Charles restarts](./charles-mapping.md): how Map Local / Map Remote rules work, when Charles must be restarted, rollback, troubleshooting
- [ADR-0001](./adr/0001-data-driven-body-aware-mocks.md): why body-aware mocks are data-driven rules served by a local dispatcher
- [Team Installation](./team-installation.md): installing this fork on another computer, security rules, troubleshooting
- [Agent Workflow Guide](./agent-workflows.md): task-oriented workflow playbooks
- [Tool Contract](./contracts/tools.md): canonical public tool surface and per-tool contract
- [How it works](./how-it-works.uk.html): diagrams of traffic interception, the three response sources and rule anatomy, plus Kiro setup (Ukrainian, open it in a browser)

## Responsibility boundaries

- Installation and client setup live in README files.
- Global agent behavior rules live in `AGENTS.md`.
- Workflow sequencing lives in `docs/agent-workflows.md`.
- Public API/tool contract lives in `docs/contracts/tools.md`.
