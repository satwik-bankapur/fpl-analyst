---
name: run-fpl-analyst
description: Run, build, test, or drive the fpl-analyst FPL data pipeline + heuristic scoring engine. Use to launch the `fpl` CLI (init-db / sync / mode), exercise the Layer 2 scoring engine on frozen season data, or smoke-test the whole sync pipeline offline. Triggers on "run fpl", "run the analyst", "score players", "sync FPL", "test the engine".
---

# Run fpl-analyst

`fpl-analyst` is a **Python CLI + library**, not a GUI — there is nothing to
screenshot. It has two surfaces:

- **Layer 1** — the `fpl` CLI (`python -m fpl {init-db,sync,mode,run}`): fetch the
  live FPL API, validate, write to SQL (SQLite in dev, Postgres in prod).
- **Layer 2** — `engine.score_all()` / `score_player()`: a pure, deterministic
  heuristic scoring engine. Given players/teams/fixtures it emits an expected-points
  number **and** a labeled `components` blob (the real product). No network, no LLM.

Drive it with the committed **`driver.py`** — it reaches into both layers using the
**frozen 2025-26 season snapshot** in `tests/assets/`, so runs are deterministic and
offline. A `live` mode hits the real API.

All paths below are relative to the repo root (the `<unit>` dir). Verified on
Windows 11 with the repo's `.venv` (Python 3.13). On Linux/macOS use
`.venv/bin/python` instead of `.venv/Scripts/python`.

## Prerequisites

Python ≥ 3.11 and a virtualenv with the package installed editable:

```bash
.venv/Scripts/python -m pip install -e ".[dev]"
```

(The repo ships a working `.venv/`; the line above is the clean-machine install and
is idempotent.) No OS packages needed — pure Python, SQLite is stdlib.

## Run (agent path) — the driver

```bash
# Layer 2 engine on the frozen snapshot: parse -> score_all -> print stats +
# one player's full labeled components blob. Deterministic, offline.
.venv/Scripts/python .claude/skills/run-fpl-analyst/driver.py engine

# Full Layer 1+2 pipeline: sync_fpl() with a FakeClient serving the frozen
# snapshot into a throwaway in-memory SQLite DB, then read ep_scores back out.
.venv/Scripts/python .claude/skills/run-fpl-analyst/driver.py sync

# The real CLI against the LIVE FPL API: init-db + sync into a temp DB, then mode.
# Needs network; output depends on the real season state (NON-deterministic).
.venv/Scripts/python .claude/skills/run-fpl-analyst/driver.py live
```

`engine` (the default if no arg) is the one to reach for when a change touches the
scoring engine — it prints the `components` dict, which is what Layer 2 exists to
produce. Expected output (frozen snapshot, mode=IN_SEASON, anchor GW38):

```
scored 841 players  mode=IN_SEASON
anchor_gw=38  ep: min=0.00 max=3.38 mean=0.75 median=0.64

top ep_next4: Dúbravka (id=470) ep=3.38 ep_next4=3.38
components:
{ ...config_version, data_basis, minutes_prob, base, attack, fixtures[...], flags, ep... }
```

`sync` proves the pipeline writes rows:

```
sync_fpl -> mode=IN_SEASON players=841 ep_rows=841 fixtures=380 rollover=False 0.29s
ep_scores has 841 rows; top ep_next4: Dúbravka ep=3.38 ep_next4=3.38
```

## Run (human path) — the CLI directly

The CLI needs a `FPL_DATABASE_URL` (defaults to `sqlite:///fpl.db` in the CWD) and the
schema applied once. `sync` fetches the **live** API.

```bash
export FPL_DATABASE_URL="sqlite:///$PWD/scratch.db"   # PowerShell: $env:FPL_DATABASE_URL="sqlite:///.../scratch.db"
.venv/Scripts/python -m fpl init-db     # apply Alembic migrations (run once)
.venv/Scripts/python -m fpl sync        # fetch live API, score, write ep_scores
.venv/Scripts/python -m fpl mode        # print derived mode + next GW
```

`python -m fpl run` starts the in-process APScheduler and **blocks forever** (Ctrl-C to
stop) — not useful for a one-shot check.

## Test

```bash
.venv/Scripts/python -m pytest -q       # 33 passed in ~0.6s
```

Tests are fully offline — they use the small frozen assets (`bootstrap.json`,
`fixtures.json`) via a `FakeClient`, never the network (see `tests/conftest.py`).

## Gotchas

- **`sync`/`mode` need a migrated DB first.** They do **not** call `create_all` — the
  schema is Alembic-only (`fpl/db.py` docstring). Run `init-db` before `sync`/`mode`
  or you get "no such table". The driver's `live` mode does this for you.
- **`sync` aborts rather than half-writes.** Both payloads are fetched *and* parsed
  before any DB write; a fetch/parse failure leaves the DB exactly as it was ("stale
  beats broken"). So a failed `sync` is safe but leaves stale rows — check the summary
  line, not just the exit code.
- **Non-ASCII player names mangle on the Windows console.** The frozen data has names
  like "Dúbravka"; cp1252 stdout turns them into `D�bravka`. Cosmetic only — the JSON
  and DB are correct. Prefix with `PYTHONIOENCODING=utf-8` (PowerShell:
  `$env:PYTHONIOENCODING="utf-8"`) to print them faithfully.
- **The frozen snapshot is a *finished* season** (all 38 GWs finished, current=38), so
  the engine runs IN_SEASON with a single-fixture horizon at GW38. That's why `ep` and
  `ep_next4` are equal in the sample output — don't read that as a bug.
- **`engine/` must never import from `fpl/` writers/graph/api** — it's the
  deterministic boundary. `driver.py engine` imports only `engine.scoring` +
  `fpl.parsers` (pure), which is why it needs no DB.

## Troubleshooting

- `ModuleNotFoundError: fpl` / `engine` → package not installed into the interpreter
  you're running. Re-run the Prerequisites `pip install -e ".[dev]"`, and use
  `.venv/Scripts/python`, not a bare `python`.
- `sqlite3.OperationalError: no such table: gameweeks` → you skipped `init-db`. Apply
  migrations first (see first Gotcha).
- `live` mode hangs or 403s → the FPL API throttles bare clients; the code already
  sends a browser User-Agent and backs off on 429/403 (`fpl/config.py`). A transient
  hang usually clears on retry; there's no published rate limit.

## The driver

[`driver.py`](.claude/skills/run-fpl-analyst/driver.py) — `engine` (default) / `sync` /
`live`. Pure-Python, cross-platform. Lives with the skill; if the project's own e2e
harness ever wants the `FakeClient`-into-SQLite helper, graduate it to `scripts/`.
