# Changelog

What changed for someone who uses the server or its tools. Changes in behaviour come
first: they are the ones a client or an agent can trip over.

## Unreleased

### Added

- **`charles_config_summary`.** Reads the Charles config file and returns what explains how
  traffic is handled: Map Remote and Map Local mappings, SSL Proxying hosts, recording
  excludes, allowed client IPs, Rewrite set names, the tools that are on and the Web
  Interface state. Each Map Remote mapping names its problems (a URL in the Host field, a
  host SSL Proxying does not cover, a local destination nothing listens on). The license,
  Web Interface users and passwords, Rewrite values and upstream proxy settings are never
  returned, so agents no longer need to open the file, and are told not to.
- **Install without cloning.** `uvx --from git+https://github.com/ExQA/charles-mcp charles-mcp`
  fetches, builds and runs the server; every client config in the README now uses it. The
  source checkout and the offline archive stay for development and machines without network.
- **Claude Code plugin.** `/plugin install charles-mcp --marketplace ExQA/charles-mcp` adds the
  server and asks for the Charles Web Interface credentials (the password goes to the system
  keychain).
- **`charles-proxy-qa` skill.** A `SKILL.md` that tells an agent when to reach for Charles, which
  rules never to break and in which order to call the tools for inspecting, mocking and
  verifying traffic. It ships with the plugin and works in any agent that loads skills.

## 3.1.0rc2 — 2026-11-12

Since `3.1.0rc1`.

### Changed behaviour

- **Recording tools return summaries, not recordings.** `get_recording_snapshot` and
  `query_recorded_traffic` answer with compact summaries and an `entry_id`, a page at a
  time (`offset` / `limit`, `max_items`), instead of raw entries with their bodies. Open
  one entry with `get_traffic_entry_detail(source="history", recording_path, entry_id)`.
- **`entry_id` is stable.** It is built from what identifies a request — host, method,
  path, query, start time, client address and port — and no longer changes when the
  request completes. Ids differ from those of earlier versions.
- **A live capture holds each request once**, in its latest state. `read_live_capture`
  returns a request first seen in flight once more when it finishes; open tunnels are
  no longer returned on every poll.
- **Analysis covers the whole capture.** `query_live_capture_entries`,
  `group_capture_analysis`, `get_capture_analysis_stats` and `mock_discover_variants`
  apply `since_seconds`, the preset's noise classes and the host, path and method
  filters to every entry first; `scan_limit` / `limit` then cap what is analysed,
  newest first, with a warning when older entries were left out.
- **`reset_environment` needs something to restore.** Without a saved Charles config —
  the default, `CHARLES_MANAGE_LIFECYCLE=false` — it changes nothing and says so.
- **Map Remote does not outlive the dispatcher.** On exit the server switches Map
  Remote off if it found it off and switched it on itself; a Map Remote that was already
  on is left on. `mock_dispatcher(action="stop")` switches it off even when no
  dispatcher runs in this server. Map Remote is one switch for all of Charles, so this
  also stops the user's own mappings.
- **Rule merge keeps what the call does not set.** `mock_rule_create_from_entry` with
  `merge=true` leaves a disabled rule disabled and keeps its priority, header conditions
  and response headers; `priority` is optional. Edits of different array elements
  (`[id=1]` and `[id=2]`) are both kept.
- **Reverse live analysis keeps a bounded history.** Snapshots beyond the history limit
  are deleted from the database, together with replay results that refer to their
  entries.

### Added

- Warnings that name a setup problem instead of an empty result: `ssl_proxying_off`,
  `slow_export`, `map_remote_without_dispatcher`. `charles_status` has a `warnings` field.
- `mock_dispatcher(action="verify")` reads the error page Charles answers a refused probe
  with, and tells a mapping whose destination is not a host name from one that did not
  apply.
- `purge_stored_data` with the `reverse` scope removes leftover session exports.
- The quick start covers excluding `control.charles` from recording and SSL Proxying.
- The offline archive also carries wheels for Python 3.15, the current Homebrew `python3`;
  the archive for 3.12–3.14 alone failed in `install.sh` there.

### Fixed

- Live analysis read only the first 500 entries of a capture, so `since_seconds`
  returned nothing and filters missed the rest on any longer session.
- `get_traffic_entry_detail(include_full_body=true)`, `mock_create_from_entry` and
  `mock_rule_create_from_entry` answered "entry not found" for entries past position 500.
- `mock_discover_variants` silently dropped everything past the first 2000 entries of a
  live capture.
- A query result depended on the preset of the call before it.
- The dispatcher replayed one client's `Set-Cookie` as a `Cookie` on other clients'
  requests.
- Detail of a finished request exported the whole session again.
- Reverse live analysis left every session export on disk and stored captures without a
  date, so retention never removed them.

### Security

- `uv.lock`: brotli 1.2.1, certifi 2026.10.23 and pyjwt 2.15.1, past PYSEC-2026-4322,
  PYSEC-2026-4320, PYSEC-2026-4141 and PYSEC-2026-4183. Offline archives built earlier
  carry the old versions; rebuild them with `scripts/build_offline_archive.py`.
- `uv.lock`: cffi 2.0.0 → 2.1.1, pydantic 2.13.0 → 2.14.3 (pydantic-core 2.46.0 → 2.50.4),
  rpds-py 2026.9.1 on Python 3.11+, typing-extensions 4.16.0, typing-inspection 0.4.5 —
  the releases with wheels for Python 3.15.
- zstandard is a dependency only before Python 3.14; from 3.14 the reverse tools decode
  zstd with the standard library (`compression.zstd`). zstandard has no wheels for 3.15.
