# Промпт для встановлення через AI-агента

Дайте своєму агенту (Kiro, Claude Code, Cursor Agent, Gemini CLI, ChatGPT з інструментами) текст нижче, замінивши `<PATH>` на абсолютний шлях до цієї теки. Дізнатися шлях: `pwd` у цій теці.

Промпт лишено англійською: агенти виконують його однаково, якою б мовою ви з ними не спілкувалися.

Якщо агента під рукою немає, те саме робиться руками за [docs/team-installation.uk.md](docs/team-installation.uk.md), а найкоротший шлях — просто запустити `./install.sh`.

---

```text
Install the charles-mcp MCP server from the local directory <PATH> and configure
my MCP client to use it. Follow these steps exactly.

Install only from that directory: the same name on PyPI is a different, older
package, so a registry install looks healthy but has no mocking tools. Step 3
checks for them, so do not skip it.

Step 1 - Check the directory:
  Confirm <PATH> exists and contains pyproject.toml, charles_mcp/ and
  charles-mcp-server.py. If it does not, stop and tell me.
  Note whether it also contains install.sh, wheels/ and requirements-lock.txt.

Step 2 - Install:
  a) If install.sh exists, run it and read its output:
       cd <PATH> && ./install.sh
     It creates <PATH>/.venv, installs the bundled wheels without touching any
     registry, and prints the client entry to use. If it fails, report what it
     printed and stop - do not improvise an install from a registry.
  b) If there is no install.sh but wheels/ and requirements-lock.txt exist:
       cd <PATH>
       python3 -m venv .venv
       .venv/bin/pip install --no-index --find-links wheels -r requirements-lock.txt
       .venv/bin/pip install --no-index --no-deps wheels/charles_mcp-*.whl
     On Windows the interpreter is .venv\Scripts\python.exe.
  c) If neither exists, this is a source checkout: run `cd <PATH> && uv sync --locked`
     when `uv --version` works. Otherwise tell me what is missing instead of
     installing anything from the internet.

Step 3 - Verify the build, not just that something starts:
    <PATH>/.venv/bin/charles-mcp --selftest
  (or `uv run --project <PATH> charles-mcp --selftest` if you took path 2c).
  It prints the version and the tool counts and ends with OK. Stop and tell me
  if it prints FAIL, reports 0 mocking tools, or rejects --selftest as an
  unknown option: that build is not this fork, so configuring it is pointless.
  A line about default credentials is expected and fine.

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
      "command": "<PATH>/.venv/bin/charles-mcp",
      "args": [],
      "env": {
        "CHARLES_USER": "<the login I gave you>",
        "CHARLES_PASS": "<the password I gave you>",
        "CHARLES_MANAGE_LIFECYCLE": "false"
      }
    }
  If you took path 2c (uv), use instead:
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

---

Після цього агент має сам уміти покликати `charles_status` — у відповіді буде стан зв'язку з Charles і підказка наступного кроку.
