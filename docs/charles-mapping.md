# How mapping works and when Charles is restarted

> Ukrainian version: [charles-mapping.uk.md](charles-mapping.uk.md)

How charles-mcp mocks plug into Charles, which actions need a Charles restart, how to restart safely, and what to check when a rule does not fire. Everything below was verified on Charles 5.2.1 (macOS) unless stated otherwise. The reasoning is in [ADR-0001](adr/0001-data-driven-body-aware-mocks.md).

## 1. Overview

```
App ──► Charles ──┬─ Map Local:  https://<host>/*  ─► file ~/charles-mocks/<host>/<path>
                  │                                   (no file → real server)
                  │
                  └─ Map Remote: https://*.domain/api/* ─► dispatcher 127.0.0.1:18080
                                 (preserve host header,      │
                                  empty destination path)    ├─ fixture rule → stored body, any status
                                                             ├─ patch rule   → real server, request and response edited
                                                             └─ no rule      → real server, unchanged
```

Two mechanisms; pick by endpoint:

| | Map Local | Map Remote + dispatcher |
|---|---|---|
| When | the response does not depend on the request body (usually GET) | one URL answers per body field (`action`), a non-200 status or a request edit is needed, many paths and hosts |
| Rule in Charles | one per host | one per domain (host glob) |
| Mocks | files `~/charles-mocks/<host>/<path>` | rules and fixtures in `~/charles-mocks/_rules/` |
| Response status | always 200 | any |
| Extra process | none | the dispatcher must run |
| Verification | `X-Charles-Map-Local` header | `X-Charles-MCP-Rule` header (rule id or `passthrough`) |

## 2. What Charles can and cannot do

- Map Local, Map Remote and Rewrite rules **cannot be changed at runtime** from outside. The Web Interface (`http://control.charles`) only toggles a whole tool, controls recording and the session, and quits Charles.
- Charles **reads its config at start** and **writes its config from memory on quit**. Editing the file while Charles runs is not just ignored — it is overwritten on quit.
- No Charles tool can condition on the **request body**. Rewrite with regexes can change a field in every response of a URL, but not only for `action=init`, so the dispatcher tells variants apart.
- Therefore Charles gets **one static rule**, and everything that changes is data: Map Local files and dispatcher rules. They can change at any time without a restart; Charles and the dispatcher re-read them on every request.

## 3. When Charles must be restarted

| Action | Restart | How |
|---|---|---|
| Create, edit or remove a Map Local mock file | no | `mock_create_from_entry`, `mock_write`, `mock_remove` |
| Create, edit or disable a dispatcher rule | no | `mock_rule_create_from_entry`, `mock_rule_set_enabled`, `mock_rule_remove` |
| Turn Map Local on or off | no | `mock_set_enabled` (via the Web Interface) |
| Turn Map Remote on or off | no | `mock_dispatcher(action="start" / "stop")` does it |
| Add the Map Local rule for a new host | **yes**, or by hand in the UI | `mock_setup_host(apply=true)` or the steps from `mock_setup_host()` |
| Add the Map Remote rule for a new domain | **yes**, or by hand in the UI | `mock_route_setup(apply=true)` or the steps from `mock_route_setup()` |

The Charles rule is set up **once per host or domain**. No restart is needed afterwards until a new domain appears.

**The restart is what activates the mapping.** Charles reads its config only when it starts, so a Map Remote mapping written by `apply=true` does nothing until the user starts Charles — the file on disk and the Map Remote window can both look right while the mapping is inert. After the start, confirm it with `mock_dispatcher(action="verify")` instead of asking the user to re-test in the app.

If a restart is unwelcome (an important session is running), add the rule by hand in the Charles UI: it works without a restart and applies as soon as you press **Done**.

## 4. How the restart goes

charles-mcp tools **do not quit or start Charles themselves**. `apply=true` only writes the config, and refuses while Charles runs (the proxy port is checked). The order:

1. **Save the session if you need it** (File → Save Session). The unsaved session is lost when Charles quits.
2. **Quit Charles** (Charles → Quit). With your permission the agent can do it through the Web Interface (`http://control.charles/quit`).
3. **Do not reopen Charles** until the agent reports that the rule is written. If Charles is reopened earlier, the write is refused; if it is reopened between the check and the write, the edit is overwritten on the next quit.
4. The agent calls `mock_route_setup(host="*.domain", path="/api/*", apply=true)` or `mock_setup_host(host, apply=true)`. The config is backed up first.
5. **Start Charles.** The rule loads at start.
6. `mock_dispatcher(action="start")` starts the dispatcher and turns Map Remote on.

To confirm Charles accepted the rule: Tools → Map Remote shows it ticked. On its next quit Charles re-saves it in its own form (for example dropping `<enabled>true</enabled>` as a default); that is expected.

### Backups and rollback

Before every write the config is copied to `~/Library/Application Support/charles-mcp/charles-config-backups/<map-remote|mock-setup>/com.xk72.charles.config.<time>` (on Windows and Linux: the charles-mcp state dir, `CHARLES_STATE_DIR`). Backups contain the Charles license key and Web Interface credentials; do not share them.

Rollback: quit Charles, copy the backup over `~/Library/Preferences/com.xk72.charles.config`, start Charles. Simpler and without a restart: delete the rule in the UI (select, "−", Done).

## 5. The Map Remote rule in detail

`mock_route_setup` writes (or returns for manual entry) this rule:

| Field | Value |
|---|---|
| From: Protocol / Host / Port / Path | `https` / `*.domain` or a host / `443` / `/api/*` (or `/*`, or one path) |
| To: Protocol / Host / Port / Path | `http` / `127.0.0.1` / `CHARLES_DISPATCHER_PORT` (18080) / **empty** for a path pattern |
| Preserve host header | **on** — without it the dispatcher cannot see the original host |

Verified live:

- the host glob `*.example.com` catches `www.example.com`;
- with an empty destination path the path reaches the dispatcher unchanged (`/api/wallet`, `/api/deep/other/path?x=1`);
- requests outside `/api/*` (for example `/index.html`) are not mapped and go to the server as usual;
- with "Preserve host header" the dispatcher receives the original `Host`, finds the route and rules, and forwards requests without a rule to the real host.

Limits of a whole-host route (`/*`): static files and WebSockets also go through the dispatcher; downloads are buffered in full and WebSockets are not supported. Prefer the API prefix.

## 6. Map Local in detail

- The per-host rule maps `https://<host>/*` to `~/charles-mocks/<host>/`. If a file exists at the request path Charles serves it; otherwise the request goes to the server.
- The file is re-read on every request, so edits show up immediately.
- **The status is always 200**, and the query string and HTTP method are **ignored**: every variant of a path gets the same file. `/users` and `/users/42` cannot both be mocked.
- Charles serves extension-less files as `text/plain`; `mock_setup_host` adds a Rewrite rule that turns it into `application/json` for that host (Rewrite also applies to Map Local responses).
- A mocked response carries `X-Charles-Map-Local: <file path>`, and the Charles entry notes say "Mapped to local file".
- If both Map Local and Map Remote cover the same path, remove the Map Local file (`mock_remove`), otherwise the request never reaches the dispatcher.

## 7. Charles requirements

- **Web Interface** enabled (Proxy → Web Interface Settings) with the username and password from `CHARLES_USER` / `CHARLES_PASS`; keep anonymous access off. charles-mcp reads the session and toggles tools through it.
- **SSL Proxying** enabled for the API hosts (Proxy → SSL Proxying Settings) and the Charles certificate installed on the device. Without it Charles sees HTTPS only as a tunnel to the host and does not know the request path, so rules with a path (Map Local, Map Remote with `/api/*`) do not fire.
- **Corporate certificates**: the dispatcher itself calls the real server for requests without a rule and for patch rules. If the server certificate comes from an internal CA, set `"verify_tls": false` for that route in `routes.json`.
- **Session size**: every live query exports the whole Charles session. Clear the session before a scenario; a multi-gigabyte session makes the tools unusable.

## 8. When a rule does not fire

Start with `mock_dispatcher(action="verify")`: it probes the dispatcher directly and then one URL per route through Charles, so it separates "the rule is wrong" from "the request never reached the dispatcher" before you read the table.

| Symptom | Cause and fix |
|---|---|
| Response has no `X-Charles-MCP-Rule` | Charles did not send the request to the dispatcher: Map Remote is off (`mock_dispatcher start` turns it on), the rule was not saved (it is missing in the Map Remote window — Cancel was pressed instead of Done), host, path or protocol do not match, no SSL Proxying for HTTPS |
| Charles answers 503 "Name lookup failed" | Map Remote did not apply and Charles went to the original host; see the row above |
| The mapping is in the Map Remote window but nothing is routed | Charles has not been started since the mapping was written: it loads mappings only at startup. Quit and start Charles, then `mock_dispatcher(action="verify")` |
| `X-Charles-MCP-Rule: passthrough` | The request arrived but no rule matched: check path, method and the `action` value (`mock_discover_variants`) and whether the rule is disabled |
| 421 from the dispatcher | Host or path is not covered by a route in `routes.json` — `mock_route_setup` |
| 502 from the dispatcher | The dispatcher could not reach the server: network/VPN, timeout (`CHARLES_DISPATCHER_TIMEOUT`), certificate (`verify_tls`) |
| Every request to the domain fails | Map Remote is on but the dispatcher is not running — `mock_dispatcher(action="start")`, or turn Map Remote off |
| `X-Charles-MCP-Warning` header | A patch did not apply (body is not JSON, or the patch path was not found); the response was sent without it |
| A config edit "disappeared" | The config was written while Charles ran and Charles overwrote it on quit — repeat per section 4 |
| Import in the Map Remote window fails with `Class.isInstance ... is null` | Importing such a file does not work in Charles 5.2.1; use `apply=true` or manual entry |
