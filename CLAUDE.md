# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read this first

- **`docs/CLAUDE.md`** — the cross-cutting guardrails and *intentional non-fixes* (things that look like bugs but are deliberate). Read it before changing any layer; several of its rules must never be broken.
- **`docs/build-order.md`** — one-page system map and the layer build sequence.
- **`docs/layerN-*.md`** — the source-of-truth spec for each layer. Read the relevant one before touching that layer. When reality contradicts a spec, fix the doc first (or leave a dated correction note), then the code — don't let specs drift.

## Current implementation state

*Verified against the tree on 2026-08-04. This section was wrong for months — it claimed
Layers 3–5 were spec-only long after they shipped. If you change a layer's status, change
it here in the same commit.*

**Everything is on `main`. There are no unmerged branches.** All five layers, Stage A,
**Stage B (payments)** and **accounts + auth** (magic link, Google OAuth, sessions, terms,
the entitlement rewire) were merged on 2026-08-04 and `main` is the only local branch.

**Everything paid still ships switched OFF** — `PAYWALL_ENABLED=false`,
`SEND_ENABLED=false`, `RUN_SCHEDULER=false`, no Paddle credentials, no Google
credentials, no Resend key, nothing deployed.

**Nothing here has ever spoken to Paddle, Google, or Resend.** The 499-test suite is
offline by design and proves our side of each boundary only. `docs/STATUS.md` §10.4 is
the explicit per-provider list; `docs/GO-LIVE.md` is the ordered runbook that closes it.

**Unmerged work: branch `prelaunch-fixes` (2026-08-07).** Six fixes from a localhost
walkthrough — the draft picker's missing players, a `FREE_BRIEF_ACCESS` config gate,
double opt-in, a stub-writer readability pass, real terms/refunds drafts, and UX polish.
`docs/STATUS.md` §11 is the full account. It is pushed and NOT merged, pending a re-walk.
The rest of this section describes `main`.

### The accounts constraint (2026-08-04) — do not break this

**The FREE brief takes no login.** `POST /api/brief`, the draft picker,
`/api/preview/*`, `/api/players` and `/api/signup` are anonymous, and an anonymous
reader of `GET /api/brief/{team}/{gw}` gets a **200 with paid sections locked** — never
a 401 and never a redirect to `/login`.

> **Amended on branch `prelaunch-fixes` (2026-08-07), not yet on `main`.** The constraint
> above is still the shipped behaviour and still the default, but it became a *configured*
> default: `FREE_BRIEF_ACCESS` can gate brief GENERATION (`open` | `second_gate` |
> `login_first`, defaulting to `open`). Enforcement is one function called from one place,
> there is still no `_require_account`, reads stay anonymous in every mode, and a gated
> request answers 403 `sign_in_required` rather than 401. `tests/test_accounts.py` §1
> passes untouched. See `docs/layer5` §8 for the conversion tradeoff. `api/accounts.py`'s only session dependency is
`routes._current_account`, which is OPTIONAL everywhere; there is deliberately no
`_require_account`. `tests/test_accounts.py` §1 fails if a login appears in the free
funnel. Entitlement now resolves **session → account → subscription**, but
`billing.is_entitled` and `ENTITLED_STATUSES` are unchanged — the allow-list is still
the whole rule, and an unknown Paddle status still degrades to free.

- `fpl/` — Layer 1: FPL API client, SQLAlchemy models (**20 tables**: 13 Layer 1 + `meta`, plus Stage B's `subscriptions`/`webhook_events`/`send_log`/`rate_hits` and accounts' `accounts`/`sessions`/`magic_link_tokens`), Alembic-managed schema, `sync_fpl()` job, mode/rollover derivation, APScheduler wiring.
- `engine/` — Layer 2: deterministic heuristic scoring engine (`score_all` / `score_player`), `config_version` v1.4.
- `graph/` — Layer 3: real LangGraph `StateGraph`, 5 deterministic nodes + a **template stub Writer**. Owns `graph/sections.py`, the canonical section vocabulary.
- `writer/` — Layer 4: LLM writer, §7 validator, payload serializer, generic preview renderer. **Built and tested, but NOT in the serving path** — see below.
- `api/` — Layer 5: FastAPI. Stage B added `api/billing.py`, `api/tokens.py`,
  `api/send.py` and `writer/emails.py`; the accounts build (2026-08-04) added
  `api/accounts.py`, the `/api/auth/*` surface, `POST /api/account/delete` and the
  read-only `GET /api/admin/accounts`.
- `web/` — Layer 5: Next.js frontend, 17 routes (11 public, re-skinned to the Scout
  Report design; 6 under `/admin`, token-gated and `noindex`). On `main`, `/terms` and
  `/refunds` are **placeholder prose** — routing and footer links exist so Paddle's live
  review has something to load; the text is still TODO (`docs/LAUNCH-CHECKLIST.md`).
  Branch `prelaunch-fixes` replaces both with full drafts that **no lawyer has read**;
  the checklist names what the owner must decide before launch.

### Current serving reality (read before you promise anything)

The gap between what this repo *looks* like it does and what it does:

- **Every brief a user receives comes from the deterministic template stub**
  (`graph/nodes/writer.py`), not an LLM. It reads flat and factual because it is a
  template — but as of 2026-07-30 it no longer leaks internals. Problem types and
  reasoning codes map through `_problem_sentence()` / `_CODE_NOTES` to plain-English clauses,
  and `tests/test_graph.py::test_stub_output_leaks_no_internals` holds the ban list
  (type slugs, severities, codes, engine flags, column names, player ids). Add a new
  problem type or reasoning code → add its sentence there, or it renders as nothing.
- **`writer/llm.py` is wired to nothing.** `api/service.py` imports exactly one symbol
  from `writer/` (`render_generic_stub`). The LLM writer, its prompt, and its
  retry/validate/fallback ladder are complete and unit-tested, but no route calls them.
  This is a pause point pending prompt iteration (layer4 §8), not a bug — but do not
  describe the product as LLM-written until that wiring lands.
  **`/admin/inspect` renders the exact payload the writer would receive**, verbatim and
  costed, without calling a model — so prompt iteration can start against real payloads at
  zero spend. It also traces the pipeline's reasoning and diffs free vs paid; see
  `graph/trace.py`, `api/inspect.py` and `docs/STATUS.md` §6. Trace mode is off by default
  and must never change a brief (`tests/test_trace.py`).
- **The payment machinery exists but has never met Paddle.** Checkout, the signed
  webhook, `subscriptions` / `webhook_events` / `send_log` / `rate_hits`, JWT full-view
  links and the weekly send batch are all built and covered by `tests/test_stage_b.py`.
  There is still **no Paddle account**, so the live checkout URL and a real webhook
  delivery have never been exercised against Paddle itself — every signature in the test
  suite is computed locally to Paddle's documented scheme. Do not describe the product as
  taking money until a sandbox transaction has actually landed.
- **The paywall is switched off, not absent.** `PAYWALL_ENABLED` defaults false, so every
  response still carries `locked_sections: []`. Gating itself is `graph.sections`'
  allow-list and lives nowhere else — `service.paywall_on()` only decides whether it runs.
  (The old `service.PAYWALL` constant is gone; there is no back-compat alias, because a
  familiar name that silently reads a stale value is worse than no name.)
- **`SEND_ENABLED` is off and no scheduler drives the batch.** `api/send.run_batch` is
  reachable only through `POST /api/admin/send` today; wiring it to a cron is a deploy
  decision, and doing it while `RUN_SCHEDULER=true` would mail every customer twice.
- **Nothing is deployed.** `render.yaml` and `.env.example` describe the launch topology;
  no service has been created.

`docs/STATUS.md` is the full audit behind this summary.

### The section vocabulary (a paywall contract — do not fork it)

`graph/sections.py` is the ONE definition of brief section keys. Both writers, the
splitter, the paywall, and `web/lib/api.ts` derive from it. Before 2026-07-30 the
vocabulary was defined three times and disagreed, which would have made the Stage B
paywall **fail open** and serve the paid product free.

Two properties keep that from recurring; `tests/test_sections.py` fails if either weakens:

1. Free-tier gating is an **allow-list** (`FREE_SECTIONS`), never a deny-list over paid
   keys — an unrecognised header is withheld, not leaked.
2. Headers the vocabulary can't map get an `x_` prefix, so they can never impersonate a
   canonical key.

Never hard-code a `## Header` string in a writer, and never add per-route gating logic.

## Commands

The repo ships a working `.venv/` (Python 3.13). Use its interpreter explicitly. On Windows use `.venv/Scripts/python`; on Linux/macOS use `.venv/bin/python`.

```bash
# Install editable (clean-machine / after dependency change; idempotent)
.venv/Scripts/python -m pip install -e ".[dev]"

# Tests — fully offline, driven by frozen JSON assets in tests/assets/ via a FakeClient
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m pytest tests/test_engine.py -q          # one file
.venv/Scripts/python -m pytest tests/test_engine.py::test_name  # one test

# CLI (Layer 1). Needs a migrated DB first: init-db before sync/mode.
.venv/Scripts/python -m fpl init-db   # apply Alembic migrations (run once)
.venv/Scripts/python -m fpl sync      # fetch LIVE API, score, write ep_scores
.venv/Scripts/python -m fpl mode      # print derived mode + next GW
.venv/Scripts/python -m fpl run       # start in-process scheduler (blocks forever)

# A NEW DATABASE NEEDS THIS TOO — pre-season, init-db + sync alone is not enough.
# A fresh DB has an empty players_archive, and pre-season scoring has nothing else
# to run on: every ep collapses onto the (position, price-band) baseline, so the
# top 20 is ALL GOALKEEPERS and the recommended captain is a keeper. Nothing errors.
# Verified on a fresh clone 2026-08-01: top 10 were 10 keepers on two distinct EPs.
.venv/Scripts/python -m fpl backfill-archive \
    --season 2025-26 --from tests/assets/bootstrap_2025-26.json
.venv/Scripts/python -m fpl sync      # re-score through the now-populated archive
# Verify: 841 archive rows, match_method mostly `name_position`, top 20 a position mix.
```

There is no lint/format tool configured — do not invent one.

```bash
# Frontend (separate terminal)
npm --prefix web install
cp web/.env.example web/.env.local          # points at 127.0.0.1:8000
npm --prefix web run dev                    # http://localhost:3000
npm --prefix web run build                  # must stay clean before any commit
```

### Driving the engine offline

`sync`/`mode` hit the **live** FPL API. To exercise Layers 1–2 deterministically against
the frozen 2025-26 snapshot, use the committed driver (also the `/run-fpl-analyst` skill).
It IS committed — `.gitignore` ignores `.claude/*` but re-includes `.claude/skills/`
specifically so a fresh clone can follow these instructions:

```bash
.venv/Scripts/python .claude/skills/run-fpl-analyst/driver.py engine  # parse -> score_all, prints a components blob
.venv/Scripts/python .claude/skills/run-fpl-analyst/driver.py sync    # full Layer 1+2 into in-memory SQLite
```

The frozen snapshot is a *finished* season (all 38 GWs done, current=38), so the engine runs `IN_SEASON` at GW38 with a single-fixture horizon — `ep` and `ep_next4` being equal there is expected, not a bug.

## Architecture essentials

- **Deterministic/LLM boundary (the load-bearing rule):** `engine/` and `graph/` must never import from `writer/`; the only LLM calls live in the Writer. `engine/` is pure — no network, no LLM, no randomness, same inputs → same outputs. `driver.py engine` imports only `engine.scoring` + `fpl.parsers`, which is why it needs no DB.
- **Two contracts everything hangs on:** the Layer 2 `components` jsonb blob (§3) and the Layer 3 node handoff shapes (§2). Each layer consumes the previous layer's *contract*, not its implementation. Downstream code depends on the contract, not the formula behind it.
- **Schema is Alembic-only.** `python -m fpl init-db` applies migrations. `fpl/models.py` is the source of truth an autogenerated migration is diffed against — after editing a model, run `alembic revision --autogenerate` and review it. `Base.metadata.create_all` is **test-only** (throwaway in-memory schema in `tests/conftest.py`); never add it to app/sync/scheduler startup.
- **Sync is abort-or-nothing:** both payloads (bootstrap + fixtures) are fetched and validated up front, then all writes happen in a single transaction. A failed fetch leaves the DB exactly as it was ("stale beats broken") — so check the summary line, not just the exit code.
- **Config:** Layer-1 runtime settings (`fpl/config.py`) are env-driven with the `FPL_` prefix (e.g. `FPL_DATABASE_URL`, defaults to `sqlite:///fpl.db`). Layer-5 delivery settings (`api/config.py`) use the spec's **bare** names (`ADMIN_TOKEN`, `FRONTEND_ORIGIN`, …) — so spec `DATABASE_URL` is really `FPL_DATABASE_URL`. The versioned *heuristic* config is separate and lives in `engine/config.py` — every tunable weight/table there is versioned via `config_version`, which is stamped into every components blob. No magic numbers inline. **`.env.example` lists every variable the project reads**; keep it current.
- **Section keys are a contract, not strings.** `graph/sections.py` only. It lives in `graph/` because `writer/` may import `graph/` but never the reverse — that is the only package all of graph/writer/api can share without breaking the boundary rule above.
- **Deployment:** the launch topology is Render + Neon + GitHub Actions cron (`render.yaml`, `.github/workflows/sync.yml`, runbook `docs/deploy-stage-a-free.md`). `docs/railway.toml.deferred` is the paid-tier upgrade path, **not** active config. `RUN_SCHEDULER=true` and the Actions cron are mutually exclusive — two sync drivers on one DB double every sync and, in Stage B, every customer email.

## Testing expectations

Tests are offline and deterministic — frozen `bootstrap.json` / `fixtures.json` assets served through a `FakeClient` (`tests/conftest.py`), never the network. Every scored player's `components` blob must validate against the Layer 2 §3 schema.

`tests/test_sections.py` is the paywall contract suite. Its literal markdown fixtures are
hand-written **on purpose** — deriving them from the constants under test would make them
vacuous. `tests/test_graph.py::test_real_stub_output_splits_to_canonical_keys` is the link
that proves the live writer still matches those fixtures. If an assertion there fails, fix
the vocabulary, not the assertion.
