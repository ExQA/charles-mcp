# ADR-0002: Ship the fork as an archive with its own wheelhouse

**Status:** Accepted
**Date:** 2026-09-21
**Deciders:** charles-mcp fork owner, mobile QA team

> Ukrainian version: [0002-offline-archive-distribution.uk.md](0002-offline-archive-distribution.uk.md)

## Context

The team installs this fork as a file, not from a registry: the private
repository is not open to everyone who needs the server, and the current plan is
to hand the fork over as an archive.

Three forces shape how that archive should be built.

- **The name is taken on PyPI.** `charles-mcp` there is the upstream package,
  version 3.0.3, without any mock tools, and it resolves `mcp` 2.x, so it does
  not even start (see [Why not PyPI](../team-installation.md)). Anyone who
  reaches for the habitual `pip install charles-mcp` or `uvx charles-mcp` gets
  that package and a server that is missing half the tools — and nothing in the
  failure points at the cause.
- **Installs are visible and sometimes forbidden.** These machines run Charles
  with the macOS proxy on, so every dependency download shows up in the captured
  session; one such request — `https://pypi.org/simple/charles-mcp/` — is what
  started this decision. Some machines should not reach a public registry at all.
- **`uv` is an extra thing to install.** It solves Python versions and
  dependencies in one command, which is why it stays the documented default, but
  it is itself a download, and not every tester has it.

The dependencies cannot simply be dropped: `mcp`, `httpx` and `pydantic` are the
server, and `jmespath`, `defusedxml`, `brotli`, `zstandard` and `protobuf` parse
what Charles captured. A standard-library-only build is not an option.

## Decision

Keep `uv sync --locked` as the default install, and add a second, self-contained
path: an archive that carries its dependencies as wheels.

`scripts/build_offline_archive.py` exports the pinned runtime closure from
`uv.lock` into `requirements-lock.txt`, downloads those packages as wheels into
`wheels/` for each requested Python version and platform, adds this fork's own
wheel (pure Python, so one file covers every set), and zips everything with the
tracked tree of `HEAD`.

Installing is one command in the unpacked archive, `./install.sh`, which runs
stock Python:

```bash
python3 -m venv .venv
.venv/bin/pip install --no-index --find-links wheels -r requirements-lock.txt
.venv/bin/pip install --no-index --no-deps wheels/charles_mcp-*.whl
.venv/bin/charles-mcp --selftest
```

`--no-index` is the point: pip never contacts a registry, so the upstream package
cannot be pulled in by accident and no install traffic leaves the machine. The
dependencies are hash-checked; the fork is installed as a package, which gives
the `charles-mcp` command and a real version. `--selftest` then fails loudly if
the build has no mocking tools — what an upstream install would look like. The
MCP client runs `<PATH>/.venv/bin/charles-mcp` instead of `uv`, and
`INSTALL-PROMPT.md` hands the same job to an agent.

## Options considered

### Option A: git bundle (what was used before)

| Dimension | Assessment |
|---|---|
| Complexity | Low — one `git bundle create` |
| Network at install | Still needed for every dependency |
| Wrong-package risk | Unchanged |
| Extra tools | git, uv |

**Pros:** carries full history; a real repository after cloning.
**Cons:** does not solve dependencies at all; the bundle also freezes whatever
history it was cut from, which is how the pre-rewrite history outlived the
rewrite.

### Option B: archive of tracked files only (current `git archive` zip)

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Network at install | Needed for every dependency |
| Wrong-package risk | Unchanged |
| Size | ~0.5 MB |

**Pros:** smallest; no history, so nothing deleted comes back.
**Cons:** every machine still downloads from PyPI, and a mistyped install
command still lands on upstream 3.0.3.

### Option C: archive plus wheelhouse (chosen)

| Dimension | Assessment |
|---|---|
| Complexity | Medium — a build script the maintainer runs |
| Network at install | None |
| Wrong-package risk | Removed by `--no-index` |
| Size | 13 MB for the first interpreter/platform set, ~4 MB for each additional one (pure-Python wheels are shared); about 18 MB for the three arm64 sets built by default |

**Pros:** installs with Python alone; exact pinned, hashed versions; works on a
machine cut off from public registries.
**Cons:** the maintainer must rebuild when dependencies change, and binary wheels
are per Python version and architecture, so the archive covers a chosen set.

### Option D: vendor dependencies into the source tree

| Dimension | Assessment |
|---|---|
| Complexity | High |
| Network at install | None |
| Upgrades | Manual and error-prone |

**Pros:** nothing to install at all.
**Cons:** compiled extensions (`pydantic-core`, `brotli`, `zstandard`) cannot be
vendored portably; the tree stops matching `uv.lock`; upgrades become hand work.

## Trade-off analysis

The real trade is **archive size and maintainer effort against install-time
network and the wrong-package failure**. 18 MB and one script run per release buy
an install that cannot reach a registry and therefore cannot pick up upstream
3.0.3 — a failure whose symptom (missing tools) does not name its cause.

The version-and-architecture coupling is the cost that does not go away: binary
wheels are built per Python minor version and per architecture, so the archive
serves the set it was built for and no other. Building several sets is cheap —
pure-Python wheels are shared and pip picks the compatible file — but the set has
to be chosen, and someone outside it gets a clear "no matching distribution"
rather than a silent fallback.

`uv` stays the default because it also supplies the interpreter, which the
wheelhouse cannot.

## Consequences

- A machine with no access to a public registry can run the server.
- The wrong-package failure is structurally impossible on the offline path.
- Dependency upgrades gain a step: rebuild the archive so its wheels match the
  lock again. The release checklist in `docs/team-installation.md` carries it.
- The archive is tied to the Python versions and platforms it was built for, and
  grows by roughly one wheel set per combination.
- Two supported install paths now exist, so install problems must be diagnosed
  against the right one.

## Action items

1. [x] Add `scripts/build_offline_archive.py`.
2. [x] Document the offline path and the client configuration it needs in
       `docs/team-installation.md` and its Ukrainian translation.
3. [x] Add the rebuild step to the release checklist.
4. [x] Confirm what the team runs: Apple Silicon only, so the default set is
       arm64 for Python 3.12-3.14. Intel was dropped when cryptography 50
       stopped publishing Intel macOS wheels; supporting it again would mean
       pinning an older cryptography with open advisories.
