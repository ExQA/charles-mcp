# ADR-0001: Data-driven, body-aware mocks

**Status:** Accepted
**Date:** 2026-09-12
**Deciders:** charles-mcp fork owner, mobile QA team, security team (for storage of captured data)

> Ukrainian version: [0001-data-driven-body-aware-mocks.uk.md](0001-data-driven-body-aware-mocks.uk.md)

## Context

The QA flow:
1. Charles records real app traffic.
2. The agent reads the session over MCP.
3. The user says which response or request data to fake.
4. Charles serves the faked response to the app.

What gets in the way:

- **Map Local picks a file by URL only.** It ignores the query string and the method (verified on Charles 5.2.1). Many APIs send every operation as a POST to one URL and select the operation with a body field such as `{"action": "init"}` or `{"action": "get_cards"}`. Map Local can mock all variants of such an endpoint or none.
- **Map Local always answers 200**, so 4xx/5xx paths cannot be tested with it.
- **Charles rules cannot change at runtime.** The web interface only toggles tools, and Charles rewrites its config file from memory on quit (verified 2026-09-10). Any file edit made while Charles runs is lost, even across a "restart".

## Decision

Routes whose response depends on the request body go through a **local dispatcher**, and its rules are **data in the mock directory**, never code.

```
App ──► Charles ──(Map Remote, preserve host header)──► dispatcher 127.0.0.1:18080
                                                          │
                          rule matches, fixture ──────────┼──► stored body (any status)
                          rule matches, patch ────────────┼──► real server ─► response edited
                          no rule ────────────────────────┴──► real server, unchanged
```

Storage, outside the repository (`CHARLES_MOCK_DIR`, default `~/charles-mocks`):

```
_rules/routes.json              routes: host or domain glob, path patterns, scheme, port, TLS verification
_rules/_any/<rule_id>.json      rules for any host (`*`) or a domain glob
_rules/<host>/<rule_id>.json    rules for one host only
<rule_id>.body                  fixture (original captured body) next to its rule, fixture mode only
_archive/                       previous versions; nothing is deleted
```

A rule matches when the method, the path, a subset of the query and headers, and the values at JSON Pointers in the body (JSON or form, e.g. `{"/action": "init"}`) all match. When several rules match, the one with the higher `priority` wins, then the more specific one, then the lower `id`.

Modes:
- **`patch`** (default). The request is forwarded upstream, optionally with request-body edits, and only the named fields of the real response change. Data stays fresh and the mock stays narrow.
- **`fixture`**. The captured body is served with the edits applied and any status, e.g. 500. The real server is never contacted.

**Scale: many paths, actions and hosts.** A real project has dozens of paths and actions, and the same API runs on several hosts (dev, stage, ...). Therefore:
- a route is set up **once per domain**: `https://*.example.com/api/*` to the dispatcher with an empty destination path, so Charles keeps the original path. Charles is not touched again; everything else is data;
- a rule applies **on every host of the route** by default (`host: "*"`). It can be narrowed to a glob or one host, and an exact-host rule wins over a general one;
- the agent starts from an **overview of the whole session** (method × host × path × `action`), not one URL;
- repeated edits of one variant **accumulate in its rule**; an edit of the same field replaces the earlier one.

The agent works like this:
1. `mock_route_setup` — once per domain.
2. `mock_discover_variants` — the whole session: method × host × path × `action`.
3. `mock_rule_create_from_entry` — a rule (or an addition to one) for each variant the user names.
4. `mock_dispatcher` — start the dispatcher.
5. Verify the result with the `X-Charles-MCP-Rule` response header.

Map Local mocks (the non-`rule` `mock_*` tools) remain the tool for plain GETs and unique URLs.

## What Charles allows, per its official documentation

Checked against the [Charles documentation](https://www.charlesproxy.com/documentation/) and the classes of the installed Charles 5.2.1 (2026-09-12).

| Surface | What it can do | Changes rules at runtime? |
|---|---|---|
| [Web Interface](https://www.charlesproxy.com/documentation/using-charles/web-interface/) (`http://control.charles`) | Toggle tools, throttling presets and recording; clear and export the session; quit. The docs suggest automating it as a web service | No: it only toggles a whole tool |
| UI: Tools → Map Remote / Map Local / Rewrite | Any rule; applies as soon as you press **Done** | Yes, by hand |
| UI: Import in a tool window, Tools → Import/Export Settings | Load rules from an XML file | Yes, by hand (two clicks) |
| Config file (`com.xk72.charles.config`) | Any rule | No: read only at start and overwritten from memory on quit |
| [Command line](https://www.charlesproxy.com/documentation/using-charles/command-line-options/): `-config`, `-headless`, `-throttling`, `--debug` | Start with another config, without a GUI, throttled | No: start time only |
| [CLI tools](https://www.charlesproxy.com/documentation/tools/command-line-tools/): `convert`, `ssl`, `filter` (5.2) | Work with saved sessions and certificates | No |
| [Breakpoints](https://www.charlesproxy.com/documentation/proxying/breakpoints/) | Edit a request or response by hand | Yes, but it blocks the request until a person acts; no automation |

What this means for faking data:

- **[Map Remote](https://www.charlesproxy.com/documentation/tools/map-remote/)** matches protocol, host, port and path with wildcards. "If you don't specify a path in the destination mapping then the path part of the URL will not be changed." It maps HTTP↔HTTPS, offers "Preserve host header", and 5.2.1 adds "Preserve base path". It has no request-body condition.
- **[Rewrite](https://www.charlesproxy.com/documentation/tools/rewrite/)** has header, URL, query, body and response-status rules, Perl-style regexes with `$1`, `$2` groups, and can act on the request, the response or both. Its only condition is the location (protocol, host, port, path), **not the request body**. A regex can change a field in every response of a URL, but not only for `action=init`.
- **No Charles tool can condition on the request body**, and there is no API or scripting. Telling `action` values apart has to happen outside Charles, hence the dispatcher.
- **The only thing that changes at runtime without a person** is the content a static rule points to: Map Local files, and dispatcher answers behind Map Remote. Both mechanisms in this fork are built on that.

Charles 5.2.1 serializes Map Remote as `MapTool.MapConfiguration.MapMapping` (`sourceLocation`, `destLocation`, `enabled`, `preserveHostHeader`, `preserveBasePath`, element `mapMapping`), which matches what `apply_map_remote_route` writes.

## Options considered

### Option A: Map Local only (status quo)

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Cost | None |
| Scalability | Good for unique URLs, **none** for one URL with many actions |
| Team familiarity | High |

**Pros:** works without the agent or an extra process; one file is one mock.
**Cons:** blind to the request body; always 200; cannot edit the request.

### Option B: Charles Rewrite rules matching the response body

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Cost | None |
| Scalability | Poor |
| Team familiarity | Medium |

**Pros:** no extra process.
**Cons:** Rewrite cannot condition on the *request* body, so it cannot tell actions apart. Regexes over JSON are fragile, a rule applies to the whole host, and rules can only change while Charles is closed.

### Option C: generic dispatcher with rules as data (chosen)

| Dimension | Assessment |
|---|---|
| Complexity | Medium: about 600 lines of code and 39 tests |
| Cost | One local process on `127.0.0.1` |
| Scalability | Any host or endpoint without code changes |
| Team familiarity | Medium: JSON Pointer and JSON files |

**Pros:** matches on body, query and headers; edits both request and response; any status; rules change on the fly because they are read from disk on every request; data never enters the repository.
**Cons:** the dispatcher must run while a route is mapped, otherwise requests to that URL fail. The Map Remote mapping is set up once per URL: by hand in the Charles UI at any time, or automatically (`apply=true`) only while Charles is closed. Upstream HTTPS goes out from the dispatcher, so internal CAs may need `verify_tls=false`.

### Option D: replace Charles with mitmproxy addons

| Dimension | Assessment |
|---|---|
| Complexity | High: a tool change |
| Cost | Retraining the team, new device and certificate setup |
| Scalability | Excellent |
| Team familiarity | Low |

**Pros:** a programmable proxy with body conditions built in.
**Cons:** out of scope: the team, its licenses and its existing setup all rely on Charles.

## Trade-off analysis

The deciding question is **where mock data lives**. In code it would travel through git and releases; in the Charles config (B) it cannot change while Charles runs. C moves the data to a per-user directory that is never committed and leaves one static mapping per URL in Charles.

The second question is **what happens to a request no rule covers**. C forwards it to the real server unchanged, so only what the user asked for is mocked and the rest of the app keeps working.

The price of C is an extra process. That is acceptable: the agent starts it with one call (`mock_dispatcher`) or it runs standalone (`charles-mcp-dispatcher`), and if it stops, disabling the Map Remote mapping restores the real traffic.

## Consequences

**Easier:**
- mocking variants of one URL by a body field, query parameter or header;
- testing error paths (any status) and edge values (one patched field in a real response);
- adding endpoints without code or a release;
- verifying that a response came from a mock: `X-Charles-MCP-Rule`.

**Harder:**
- keeping the dispatcher running while a route is mapped;
- a whole-host route (`/*`) also sends static files and WebSocket upgrades through the dispatcher, which buffers downloads and does not support WebSockets, so routes should be limited to the API prefix (`/api/*`);
- configuring Map Remote: once per URL, by hand in the UI or with `apply=true` while Charles is closed;
- handling `~/charles-mocks`: it holds real captured responses and must never be committed or shared.

**To revisit:**
- ~~verify on a live Charles a mapping with a host glob and an empty destination path~~ — **verified 2026-09-12 on Charles 5.2.1**: `http://*.example.com/api/*` caught `www.example.com`, `/api/wallet` and `/api/deep/other/path` reached the dispatcher unchanged, `/index.html` outside the prefix was not mapped, and Charles kept the glob mapping when it saved its config on quit;
- ~~verify on a live Charles that Map Remote with "preserve host header" passes the original `Host`~~ — **verified 2026-09-12 on Charles 5.2.1**: a mapping written by `apply_map_remote_route` while Charles was closed loaded at start; through the Charles proxy `action=init` (JSON and form) got the rule's answer and `action=transactions` reached the real `example.com` with `X-Charles-MCP-Rule: passthrough`. Still open: a phone over HTTPS (needs SSL Proxying and the certificate);
- a `restart_charles` mode: quit Charles via `/quit`, write the rules and start it again, with explicit user consent because the unsaved session is lost;
- masking personal data in fixtures when a rule is created.

## Action items

1. [x] Rule model, store and matching (`charles_mcp/mocks/rules.py`).
2. [x] Dispatcher with forwarding, edits, and SSRF and loop protection (`charles_mcp/mocks/dispatcher.py`).
3. [x] Idempotent Map Remote writer with "preserve host header" (`charles_config.py`).
4. [x] MCP tools and the `charles-mcp-dispatcher` CLI.
5. [x] Tests: 39 new, 232 in total.
6. [x] Verify on a live Charles 5.2.1 through its proxy. [ ] The same from a phone over HTTPS.
