# FPL Analyst

An AI Fantasy Premier League analyst. You give it a squad (a 15-player draft, or
in-season your live team), it returns a **scout report** — the verdict, one
transfer, the captain call, bench order, and fixture notes — grounded in a
deterministic scoring engine and written up by an LLM.

The pipeline is deliberately split so the numbers never depend on the model:

```
FPL API ──▶ engine ──▶ graph ──▶ writer ──▶ api ──▶ web
 (data)   (scoring)  (assemble)  (1 LLM)   (HTTP)  (Next.js)
 Layer 1   Layer 2    Layer 3    Layer 4   Layer 5
```

**The load-bearing rule:** `engine/` and `graph/` are pure and deterministic —
no network, no LLM, no randomness, same inputs → same outputs. The *only* LLM
call in the whole system lives in `writer/`. `engine/` and `graph/` must never
import from `writer/`. Everything downstream depends on the previous layer's
*contract*, not its implementation.

## Status

All five layers exist; the delivery surface ships as **Stage A** (pre-season /
draft path, stub Writer, no paywall). Stage B — live in-season briefs, checkout,
paywall, weekly email sends, and the real LLM Writer — is scaffolded but not
wired on.

| Layer | Package  | What it does                                              |
|-------|----------|-----------------------------------------------------------|
| 1     | `fpl/`   | FPL API client, SQLAlchemy models, Alembic schema, `sync` job, mode/rollover derivation, scheduler |
| 2     | `engine/`| Deterministic heuristic scoring (`score_all` / `score_player`) |
| 3     | `graph/` | LangGraph pipeline that assembles a brief from scored data + cache |
| 4     | `writer/`| The single LLM call: serialize → generate → validate → retry once → deterministic stub fallback |
| 5     | `api/` + `web/` | FastAPI delivery surface and Next.js frontend |

## Quickstart

Ships with a working `.venv/` (Python 3.13). Use its interpreter explicitly —
`.venv/Scripts/python` on Windows, `.venv/bin/python` on Linux/macOS.

```bash
# Install editable (after a dependency change; idempotent)
.venv/Scripts/python -m pip install -e ".[dev]"

# Tests — fully offline, driven by frozen JSON assets via a FakeClient
.venv/Scripts/python -m pytest -q

# CLI (Layer 1). init-db first, then sync/mode hit the LIVE FPL API.
.venv/Scripts/python -m fpl init-db   # apply Alembic migrations (run once)
.venv/Scripts/python -m fpl sync      # fetch live API, score, write ep_scores
.venv/Scripts/python -m fpl mode      # print derived mode + next GW
.venv/Scripts/python -m fpl run       # start in-process scheduler (blocks)

# API (Layer 5)
.venv/Scripts/python -m uvicorn api.main:app --reload   # http://localhost:8000
```

The frontend lives in `web/` and is deployed separately — see [`web/README.md`](web/README.md).

### Driving the engine offline

`sync`/`mode` hit the **live** FPL API. To exercise Layers 1–2 deterministically
against the frozen 2025-26 snapshot, use the committed driver:

```bash
.venv/Scripts/python .claude/skills/run-fpl-analyst/driver.py engine  # parse -> score_all
.venv/Scripts/python .claude/skills/run-fpl-analyst/driver.py sync    # full Layer 1+2 into in-memory SQLite
```

The snapshot is a *finished* season (all 38 GWs done), so the engine runs
`IN_SEASON` at GW38 with a single-fixture horizon — `ep` and `ep_next4` being
equal there is expected, not a bug.

## Configuration

Two separate config surfaces, on purpose:

- **Layer 1 runtime** (`fpl/config.py`) — env-driven with the `FPL_` prefix
  (e.g. `FPL_DATABASE_URL`, defaults to `sqlite:///fpl.db`).
- **Delivery runtime** (`api/config.py`) — the public-surface secrets/knobs under
  bare env names: `FRONTEND_ORIGIN`, `ADMIN_TOKEN`, `RESEND_API_KEY`, `SEND_FROM`,
  `RUN_SCHEDULER`. Empty defaults are safe (admin endpoints closed, mail a no-op).
- **Heuristic config** (`engine/config.py`) — every tunable weight/table is
  versioned via `config_version`, stamped into every scored player's `components`
  blob. No magic numbers inline.

## Conventions worth knowing before you touch code

- **Schema is Alembic-only.** `python -m fpl init-db` applies migrations.
  `fpl/models.py` is the source of truth; after editing a model run
  `alembic revision --autogenerate` and review it. `Base.metadata.create_all` is
  **test-only** — never in app/sync/scheduler startup.
- **Sync is abort-or-nothing.** Both payloads are fetched and validated up front,
  then all writes happen in one transaction. A failed fetch leaves the DB
  untouched ("stale beats broken") — check the summary line, not just the exit code.
- **Single-replica invariant.** APScheduler runs in-process in the API. A second
  replica doubles every sync and every email. `railway.toml` pins `numReplicas=1`.
- **Tests are offline and deterministic** — frozen `bootstrap.json` / `fixtures.json`
  served through a `FakeClient` (`tests/conftest.py`), never the network.

There is no lint/format tool configured.

## Docs

- [`docs/build-order.md`](docs/build-order.md) — one-page system map and build sequence.
- [`docs/CLAUDE.md`](docs/CLAUDE.md) — cross-cutting guardrails and *intentional non-fixes* (things that look like bugs but are deliberate). Read before changing any layer.
- `docs/layerN-*.md` — source-of-truth spec for each layer. Read the relevant one before touching that layer.
- [`docs/deploy-stage-a.md`](docs/deploy-stage-a.md) — repo → live deployment runbook (Railway API + Postgres, Vercel frontend).
