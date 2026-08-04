# Project status — 2026-07-30

Written by reading the repo, running the suites, and tracing contracts end to end.
**Not** by reading the specs. Where the specs and the code disagree, the code wins
and the disagreement is recorded below.

**Five passes are recorded here.** The audit (§1–6) was read-only. A follow-up
pre-hibernation pass then fixed the items marked ✅ **FIXED** — see §7 for exactly
what changed and what deliberately did not. Where a §1–6 finding has been resolved,
the original text is kept (so the reasoning survives) with the fix noted inline.
**§8 records the 2026-08-01 security & quality audit**, whose full findings live in
`docs/AUDIT.md`. **§9 records Stage B** (payments, built and switched off).
**§10 is the 2026-08-04 consolidation truth pass** — everything merged to `main`,
and the explicit list of what has never touched a real external provider.

> **§10 supersedes the numbers in this header block and the branch state in §1.**
> If you read only one section, read §10.

Post-fix state as of 2026-08-01: **275 tests passing**, `npm run build` clean,
`alembic upgrade head` = `c3d4e5f6a7b8`. The §7 pass reported 155 tests; the inspector
and admin console work took it to 252, and the security pass added 23 regressions.
*(Superseded — as of 2026-08-04 the count on `main` is **467** and the head is
`e5f6a7b8c9d0`. See §10.)*

---

## 1. Repo state

### Working tree

**Clean.** `git status --porcelain` returns nothing. No uncommitted work, no untracked
work that isn't deliberately gitignored.

Untracked-but-ignored files present in the tree (intentional, via `.gitignore`):
`check_squad.py`, `scratch_brief.py`, `scratch_decomp.py`, `scratch_fetch.py`,
`scratch_preseason.py`, `scratch_writer.py`, `fpl.db` (1.0 MB local SQLite),
`.venv/`, `.claude/`, `.next/`, `node_modules/`.

Note: `.claude/` is gitignored, which means **`.claude/skills/run-fpl-analyst/driver.py`
is NOT in the repo.** The root `CLAUDE.md` documents it as "the committed driver". It
isn't committed. A fresh clone does not get it. See §5.
> ✅ **FIXED** (§7): `.gitignore` now uses `.claude/*` + `!.claude/skills/`; the driver
> and its SKILL.md are committed, `settings.local.json` stays ignored.

### Branches

| Branch | Head | Merged into main? | What's on it |
|---|---|---|---|
| `main` | `dd7613f` | — | Everything through Stage A free-tier deploy + READMEs |
| `origin/main` | `dd7613f` | in sync | Same as local main |
| `stage-a-reskin` | `2fbfdb3` | **NO — the only unmerged branch** | The Scout Report visual re-skin of `web/` |
| `stage-a-free-tier` | `dd7613f` | yes (fully merged, == main) | Stale pointer, safe to delete |
| `layer1-data-pipeline` | `ba3a10c` | yes | Historical |
| `layer2-heuristic-engine` | `53b0299` | yes | Historical |
| `layer3-langgraph-pipeline` | `4e8b748` | yes | Historical |
| `layer4-brief-writer` | `f96ad2d` | yes | Historical |
| `layer5-delivery` | `7693b3a` | yes | Historical |

**You are currently on `stage-a-reskin`.** It is one commit ahead of main and has never
been pushed (no remote tracking branch). If your laptop dies, the re-skin is gone. Five
merged layer branches are dead weight and can be pruned.

### Latest commit on main

`dd7613f` — "Add docs and web readme". The layer it *closed* was **Layer 5 Stage A**:
the real closing work was `05db78b` ("Stage A: free-tier deployment topology — Render +
Neon + Actions cron"), with `dd7613f` a docs-only follow-up. Main is therefore
"Stage A complete, deployable, not deployed."

The one unmerged commit `2fbfdb3` is the frontend re-skin, which per its own drift note
changed no contracts.

---

## 2. What actually works right now

### Verified runs

```
.venv/Scripts/python -m pytest -q     →  124 passed, 1 warning in 9.76s
npm --prefix web run build            →  ✓ Compiled successfully, 6 routes, no type errors
```

Both green, on the current `stage-a-reskin` checkout. The one warning is a Starlette
deprecation notice about httpx in FastAPI's TestClient — cosmetic, not ours.

Test distribution (124 total):

| File | Tests |
|---|---|
| `test_engine.py` | 18 |
| `test_api.py` | 17 |
| `test_graph.py` | 11 |
| `test_writer.py` | 9 |
| `test_parsers.py` | 8 |
| `test_sync.py` | 7 |
| `test_client.py` | 4 |
| `test_rollover.py` | 4 |
| `test_imports.py` | 1 |

Next.js build output: `/` and `/brief/draft/[draftId]` are dynamic (ƒ), `/privacy`,
`/story`, `/_not-found` static (○), `/brief/[teamId]` dynamic. First Load JS 87–102 kB.

### Per-layer reality

**The root `CLAUDE.md` is materially wrong.** It states: *"only Layers 1–2 exist as code
today… `graph/`, `writer/`, `api/`, `web/` — spec-only (Layer 3–5 docs exist, no code
yet)."* All five layers have shipped code and are merged into main. This is the single
most misleading document in the repo for a returning developer. Fix it first.

| Layer | Status | Evidence checked |
|---|---|---|
| **1 — `fpl/`** | **Built** | 11 modules, 1,185 LOC. `client.py`, `models.py` (13 tables), `sync.py`, `parsers.py`, `scheduler.py`, `migrate.py`, `__main__.py` CLI. 3 Alembic migrations. 23 tests across sync/parsers/client/rollover. |
| **2 — `engine/`** | **Built** | 4 modules, 569 LOC. `scoring.py` + `baseline.py` + versioned `config.py` at `config_version = "v1.4"`. 18 tests. |
| **3 — `graph/`** | **Built, on a stub Writer** | 15 modules, 1,229 LOC. Real LangGraph `StateGraph`. 5 real nodes + `nodes/writer.py`, which is a deterministic template renderer, not an LLM. 11 tests incl. a golden-path brief. |
| **4 — `writer/`** | **Built but NOT WIRED IN** | 8 modules, 661 LOC. `llm.py` (real Anthropic call), `validator.py` (5 gates), `serializer.py`, `generic.py`, `seeds.py`, prompt `system_v0.1.md`, 10 frozen payload fixtures. 9 tests. **Dead code in the serving path — see below.** |
| **5 — `api/`** | **Stage A built** | 7 modules, 683 LOC. 12 routes. 17 tests. Stage B (checkout/webhooks/sends/paywall) absent. |
| **5 — `web/`** | **Stage A built + re-skinned** | 1,601 LOC TS/TSX. 6 routes, 13 components. Builds clean. Re-skin unmerged. |

### Running on stubs — named explicitly

**1. The brief writer that users actually see is the Layer 3 template stub, not the LLM.**

This is the biggest gap between the docs' impression and reality. Traced:

```
api/service.py:139   generate_brief → graph.cache.get_or_generate
graph/cache.py:28    imports STUB_MODEL from .nodes.writer
graph/nodes/writer.py:1  "Writer STUB (§2.5) — deterministic template renderer, NEVER an LLM"
```

`grep -rn "writer.llm" api/ graph/` returns **nothing**. `api/service.py` imports exactly
one thing from `writer/`: `render_generic_stub`. So the entire Layer 4 LLM writer
(`writer/llm.py`, the prompt, the validator, the retry/fallback ladder) is built, tested
in isolation, and **never called by any route**. Every brief a real user receives today
is robotic interpolated template text.

Layer 4's own drift note (2026-07-10) admits the pause point — but it reads as "prompt
iteration is pending", not "the LLM is not connected to the product at all". The
honest statement is the latter.

**2. The generic preview is a deterministic stub** — `writer/generic.render_generic_stub`,
rendered live per request behind `Cache-Control: max-age=3600`, no persisted row.

**3. `GET /api/brief/{team_id}/{gw}` is a read-only shell** — Stage A never generates
season briefs, so it serves only already-persisted rows, and `team_name` / `overall_rank`
/ `bank` are hardcoded `None`. The frontend page for it (`web/app/brief/[teamId]/page.tsx`)
is an explicit placeholder.

**4. `locked_sections` is hardcoded `[]`** at `api/service.py:119` and `:168`. The paywall
mechanism exists as a data shape only.

**5. Email is a no-op unless keyed** — empty `RESEND_API_KEY` makes `api/mail.py` log and
return. Only the Welcome template exists.

**6. ~~`graph/config.py` ships placeholder version constants~~ — WRONG, my error.**
`curated_notes_version` / `euro_weeks_version` have been real DB-backed sources
(`graph/data.py:80,86`, `max(updated_at)` over the curated tables) since 2026-07-10. I
matched on the word "placeholder" in a *historical* comment and reported stale prose as
stale code. The `graph/cache.py` docstring genuinely was wrong and has been corrected.

---

## 3. What's missing between here and a paying customer

Verified against code, not copied from spec. Every item below was confirmed absent by grep.

| # | Item | Evidence it's missing | Effort |
|---|---|---|---|
| 1 | **`subscriptions` table** | `fpl/models.py` has 13 tables; no `Subscription` class. Not in any of the 3 migrations. | 0.5 d (model + migration + tests) |
| 2 | **`webhook_events` table** (idempotency) | Same — absent. | 0.25 d (with #1) |
| 3 | **`POST /api/checkout`** (spec §1.6) | Not in `api/routes.py`. Zero `stripe`/`paddle` references in any `.py`. | 1 d incl. Paddle SDK wiring |
| 4 | **`POST /api/webhooks/paddle`** (spec §1.7) | Absent. Needs `Paddle-Signature` HMAC verify *before* parse, per `docs/CLAUDE.md:62`. | 1.5 d — 6 event types to map |
| 5 | **`PAYWALL_ENABLED` flag + section gating** | Not in `api/config.py` (which has 8 settings, none of them this). `locked_sections` hardcoded `[]`. | 0.5 d — but see §5, the key vocabulary is broken first |
| 6 | **JWT brief tokens** | `brief_token_secret` exists in config but is unused; no mint, no verify, no `jose`/`pyjwt` import. `GET /api/brief/{team_id}/{gw}` accepts a `token` param it does nothing with. | 1 d |
| 7 | **Weekly send job** | No batch runner anywhere. `fpl/scheduler.py` schedules sync only. | 2 d — batching, per-sub token mint, failure isolation |
| 8 | **Email templates 1 & 2** (paid weekly, free preview) | `api/mail.py` has Welcome only. | 1 d |
| 9 | **Persisted generic preview singleton** | `service.preview()` renders live every call. Migration `b2c3d4e5f6a7` already relaxed the `briefs` CHECK to allow the row — the row is just never written. Send job (#7) needs it. | 0.5 d |
| 10 | **Paddle account + verification** | No account referenced anywhere. **This is the critical-path item you don't control** — provider verification latency. | Calendar weeks, ~0 dev time |
| 11 | **Real LLM briefs in the serving path** | §2 above. Not a Stage B spec item — it was silently skipped. | 1 d wiring + open-ended prompt iteration |
| 12 | **Section-key vocabulary fix** | See §5. Blocks #5 correctness. | 0.5 d |

**Rough total: ~11 developer-days**, plus Paddle verification calendar time running in
parallel. Start #10 the day you pick this up — it's the only item with external latency.

### Specced but silently skipped

- **The LLM writer never reached the serving path** (#11). No drift note says "the product
  ships stub briefs". Layer 4's note frames it as a pause, which undersells it.
- **`GET /api/brief/{team_id}/{gw}` accepts an unused `token` parameter.** Signature
  suggests gating; body ignores it. Looks implemented, isn't.
- **`fpl/scheduler.py` is fully built and entirely unused in the launch topology.**
  `RUN_SCHEDULER` defaults off and is not set on Render. Correct decision (documented in
  the 2026-07-15 note), but ~100 LOC of live-looking dead code.
- **`railway.toml` is committed but describes an abandoned topology.** Railway went paid;
  launch is Render. The file is preserved as the upgrade path — but nothing in the filename
  says "not in use", and it's the only deploy config at repo root.
- **No `render.yaml` / `vercel.json`.** The actual launch topology has **zero committed
  infrastructure config**. Everything is dashboard-clicked per `docs/deploy-stage-a-free.md`.
  Reproducing the deploy is a manual runbook, not a file.

---

## 4. Deployment state

### Is anything deployed?

**No.** Nothing is live. Evidence:
- `web/.env.local` points at `http://127.0.0.1:8000` — local only.
- No `render.yaml`, no `vercel.json`, no deployment metadata anywhere in the tree.
- `.github/workflows/sync.yml` exists and is valid, but depends on two repo secrets
  (`API_BASE_URL`, `ADMIN_TOKEN`) that only have meaning once a service exists.
- The only remote is the GitHub origin; main is pushed, nothing else.

### Cold start to live, per `docs/deploy-stage-a-free.md`

The runbook is genuinely complete — 9 sections, sequenced, with a smoke checklist. Path:

0. Accounts & prerequisites (marked 🔴 HUMAN)
1. Render — API web service (free)
2. Neon — Postgres (free)
3. Environment variables on Render
4. GitHub Actions sync cron — set `API_BASE_URL` + `ADMIN_TOKEN` repo secrets
5. Vercel + CORS on platform subdomains
6. Resend — email (optional for launch)
7. First production sync
8. Smoke checklist
9. Upgrade path + rollback

Realistically **half a day** if accounts already exist, most of it waiting on
provisioning and the first sync.

**Do this before deploying:** merge `stage-a-reskin` into main. Both platforms deploy from
main, so deploying today ships the pre-re-skin UI.

### External accounts required

| Account | Needed for | Config in repo? |
|---|---|---|
| Render | API hosting | ❌ No `render.yaml`. Dashboard-only. |
| Neon | Postgres | ❌ Consumed as `FPL_DATABASE_URL` env var only. |
| Vercel | Frontend | ❌ No `vercel.json`. `NEXT_PUBLIC_API_URL` set in dashboard. |
| GitHub Actions | Sync cron | ✅ `.github/workflows/sync.yml` committed. Needs 2 secrets. |
| Resend | Email | ✅ `api/mail.py` + `RESEND_API_KEY`/`SEND_FROM` in `api/config.py`. Degraded without a verified domain. |
| Anthropic | LLM writer | ⚠️ `writer/config.py` reads model/key from env — but nothing calls it (§2). |
| Paddle | **Payments** | ❌ **Nothing. No account, no config, no code.** |

So: 5 of 7 accounts have no committed configuration, and the payments provider has no
presence in the repo at all.

### Free-tier constraints you accepted

- Render sleeps after ~15 min idle → first request pays ~1 min cold start. The sync
  workflow pings `/api/health` first to absorb it. **A real user's first brief request
  hits that same cold start with no warm-up.**
- Neon free has no scheduled backups (~24h PITR only). Documented as an **accepted gap
  that expires at the first season rollover**, after which `players_archive` is
  irreplaceable. That's a hard deadline for the paid upgrade, not a nice-to-have.
- Vercel preview deploys get distinct hostnames and **will fail CORS** against the single
  allowed `FRONTEND_ORIGIN`. Expected, documented, still surprising in the moment.
- Sync cadence is flat twice-daily. Layer 1 §3 wants 3-hourly inside deadline−48h. Fine
  pre-season; **wrong from the GW1 deadline onward.**

---

## 5. Known issues and open questions

### The section-key question — resolved, and it's worse than "fixtures vs chips"

You asked whether the re-skin rendered `fixtures` where the spec lists `chips`. Traced the
full chain. Three findings.

**(a) `chips` is never produced by any backend code path today.**

```
graph/nodes/writer.py emits:  Verdict, Transfer, Captaincy, Bench order, Watchlist, Fixture notes
api/service.py:31 _SECTION_KEYS maps exactly those six → verdict, transfer, captaincy,
                  bench_plan, watchlist, fixtures
```

`grep -rn "chips" --include=*.py` across the whole repo returns **one hit**, and it's a
comment in `writer/config.py:36`. There is no `"Chips": "chips"` entry in `_SECTION_KEYS`.

Meanwhile the frontend `SECTION_ORDER` (`web/lib/api.ts`) carries seven keys including
`chips`, with a title ("Chips") and an icon. So the frontend is a superset of what the
backend can emit. The re-skin didn't substitute `fixtures` for `chips` — **the backend
grew a `fixtures` section the spec never listed, and never grew the `chips` one it did.**

**(b) Does `chips` render when present? Yes, correctly.**

`BriefView.tsx:34-36` builds keys as the union of `SECTION_ORDER` + response section keys +
`locked_sections`, then filters to keys actually present in `sections` or `locked_sections`.
So `chips` in `SECTION_ORDER` but absent from the response is **filtered out — no empty
section renders.** If the backend ever emits it, it renders in the right ordinal position
(after `bench_plan`) with the right title and icon. Forward-compatible. This part is fine.

**(c) The lock card's section list does NOT match real keys, and it fails open. This is
a live paywall defect waiting for Stage B.**

`LockCard` takes `titles={run.keys.map(sectionTitle)}` — it renders whatever keys the API
puts in `locked_sections`. Spec §1.1 says Stage B will send
`locked_sections: ["bench_plan", "chips", "watchlist"]`.

If you ship that literally against today's writer:
- `bench_plan` ✅ exists → gets locked correctly
- `watchlist` ✅ exists → gets locked correctly
- `chips` ❌ never in `sections` — but it **is** in `locked_sections`, so it passes
  BriefView's filter and **the lock card advertises "Chips" content that does not exist
  behind the paywall.** You'd be charging for a section the writer never writes.

**And the deeper problem — swapping in the real LLM writer breaks the key vocabulary
entirely.** `_SECTION_KEYS` was built against the *stub's* headers. The LLM prompt
(`writer/prompts/system_v0.1.md:16-21`) emits different header text, and
`split_sections` slugs anything unmapped via `head.lower().replace(" ", "_")`:

| LLM prompt header | `_SECTION_KEYS` match? | Resulting key |
|---|---|---|
| `## The verdict` | ❌ (map has "Verdict") | `the_verdict` |
| `## This week's transfer` | ❌ (map has "Transfer") | `this_week's_transfer` |
| `## Captaincy` | ✅ | `captaincy` |
| `## Bench order & the plan` | ❌ (map has "Bench order") | `bench_order_&_the_plan` |
| `## Chips` | — (slug coincidence) | `chips` |
| `## Watchlist` | ✅ | `watchlist` |

Four of six keys change. Consequence: gating on `["bench_plan", "chips", "watchlist"]`
would lock **nothing** (`bench_plan` no longer exists as a key) — the paywall **fails open
and gives the paid product away free**, while ordering and icons silently degrade to
the prose fallback.

**Verdict:** the section-key vocabulary is a genuine unresolved paywall contract bug.
It is invisible today because the stub writer happens to emit headers matching
`_SECTION_KEYS`, and because `locked_sections` is hardcoded `[]`. It becomes a
revenue-losing bug the moment either changes. **Fix `_SECTION_KEYS` to cover both writers'
headers, and add a test asserting every `SECTION_ORDER` key is producible, before touching
`PAYWALL_ENABLED`.**

Related, minor: the generic preview emits entirely different headers again —
`the_week_ahead`, `form_and_fixtures`, `the_differentials`, `trap_of_the_week`. None are in
`SECTION_ORDER`, so `PreviewTeaser.tsx:8`'s `SECTION_ORDER.find(...)` **never matches** and
always falls through to `Object.keys(preview.sections)[0]`. It works, but by accident —
that lookup is dead code.

### Every dated drift note, collected

**Layer 2 — `docs/layer2-heuristic-engine.md`**
- `2026-07-09` — `score_player` takes an `EngineContext` from `build_context(...)`, not the §8 six-arg signature. Same contract; context computed once per sync instead of per-player.
- `2026-07-09` — §5 baseline table recomputed on demand in `build_context`, not persisted to a `baseline_ep` table at rollover.
- `2026-07-10` (fix, v1.2) — `MIN_MINUTES_XGI` (270) floor now applied symmetrically on the archive path; below it, `xgi_per90` falls back to the (position, price-band) baseline and emits `thin_minutes_sample`.
- `2026-07-10` (fix, v1.3) — added `W_CS=0.5` weighting the expected clean-sheet term. Corrects a ~1.5× double-count of defensive production vs 1.0× attacking. Not the §2.6 positioning rule.
- `2026-07-10` (feature, v1.4) — European-week rotation dampener adopted from Layer 4 §1.2. `EURO_ROTATION_DAMP={1:0.90,2:0.90,3:0.85}`, `EURO_MIN_SHARE=0.85`. No-op when `euro_weeks` empty.

**Layer 3 — `docs/layer3-langgraph-pipeline.md`**
- `2026-07-10` — `cold_form` detection restricted to `IN_SEASON`; pre-season it false-positived the entire squad.
- `2026-07-10` — realized with real LangGraph `StateGraph`; `MarketScout`/`FixturePlanner` kept sequential, not parallel (whole chain <1s).
- `2026-07-10` — `input_hash` mixes `curated_notes_version` + `euro_weeks_version`, both placeholder constants in `graph/config.py`. Changing them invalidates every cached brief exactly once, by design.

**Layer 4 — `docs/layer4-brief-writer.md`**
- `2026-07-10` — `temperature 0.7` **is not sent**; `claude-sonnet-5` rejects sampling params with a 400. Preserved behind `SEND_TEMPERATURE=False`.
- `2026-07-10` — §1.2 European dampener implemented in **Layer 2** (v1.4), not Layer 4.
- `2026-07-10` — §7 name whitelist compares against global `players.web_name`. A wholly fictional name matching no real player is **not caught**; closed-world prompt + number/header/length gates are the only backstop.
- `2026-07-10` — generic preview and personalized brief both ship as deterministic template renderers. LLM call wired and smoke-tested; prompt iteration to v≥0.2 is §8 human-in-the-loop work.

**Layer 5 — `docs/layer5-delivery-surface.md`**
- `2026-07-11` (Stage A backend) — 10 sub-notes: `DATABASE_URL`→`FPL_DATABASE_URL` mapping; `RUN_SCHEDULER` gate default-off; new `GET /api/brief/draft/{draft_id}`; §1.1 response gained `draft_id`/`overall_rank`/`bank`; Stage A returns everything with `locked_sections: []`; `email/`→`api/mail.py` (stdlib shadow); preview field is `sections` not `html_sections`; `get_or_generate` gained a `before_generate` rate-limit hook; **fixed a merged Layer 4 bug** — `writer/generic.py` imported two constants from `writer.config` where they don't exist, shipped green in `f96ad2d` because no test imported the module; `GET /api/brief/{team_id}/{gw}` is a pure read.
- `2026-07-11` — **Stage B TODO: persist the generic preview.** Currently rendered live per request; the weekly send job needs a persisted singleton.
- `2026-07-15` (free-tier topology) — 6 sub-notes: scheduler moved out of process (the one architecturally load-bearing change — free Render sleeps); new `POST /api/admin/sync`; sync cadence downgraded to flat twice-daily; no custom domain at launch (Vercel previews will fail CORS); **backups are a genuine gap, not a deferral** — hard deadline at first season rollover; email degraded without a verified domain.
- `2026-07-28` (re-skin) — design theme transcribed to `tailwind.config.ts`; **section display titles changed, keys and order did not**; one lock card per *run* of consecutive locked keys.

### TODO / FIXME / placeholder inventory

No literal `TODO:` or `FIXME:` markers exist in the codebase. What exists instead:

- `api/service.py:119`, `:168` — `"locked_sections": []` with `# Stage B populates this`
- `web/app/brief/[teamId]/page.tsx:7` — explicit pre-season placeholder page
- `web/components/LockCard.tsx:3` — "Paid-section placeholder", exercised only by `/story`
- `graph/config.py:6` — placeholder version constants for tables that don't exist
- `graph/cache.py:5` — same, curated/euro-week versions
- `fpl/models.py:161` — comment still says `ep_scores` is populated by the Layer 1 **stub** writer; Layer 2 replaced it. Stale comment.
- `fpl/__init__.py:5` — same stale "(placeholder) stub writer" reference
- `docs/layer5-delivery-surface.md:156` — the one flagged "Stage B TODO"

### Things a returning developer will trip over

1. **The root `CLAUDE.md` says Layers 3–5 don't exist.** They do, all merged. Highest-value
   fix in the repo.
2. **`CLAUDE.md` points at `.claude/skills/run-fpl-analyst/driver.py` as "the committed
   driver". `.claude/` is gitignored — it is not committed.** A fresh clone has no offline
   driver, and the documented "drive the engine offline" workflow is impossible.
3. **The re-skin is unmerged and unpushed.** One local commit; no remote copy.
4. **`railway.toml` is the only root-level deploy config and describes an abandoned
   platform.** Actual topology (Render) has none.
5. **Stub vs LLM.** `graph/nodes/writer.py` and `writer/llm.py` both exist. Only the stub
   runs. The `writer/` package's docstrings describe an LLM pipeline that nothing invokes.
6. **`fpl.db` (1 MB) sits in the working tree, gitignored.** State from a July 28 E2E
   session. Unknown freshness — re-seed rather than trust it.
7. **The frozen test snapshot is a *finished* season** (all 38 GWs, current=38), so the
   engine runs `IN_SEASON` at GW38 with a single-fixture horizon. `ep == ep_next4` there
   is correct, not a bug. Documented, but it looks alarming.
8. **Two sync drivers must never both be on.** `RUN_SCHEDULER=true` (paid) XOR the Actions
   cron (free). Both = double sync and, in Stage B, double emails to paying customers.
9. **`brief_token_secret` and the `token` query param exist but do nothing.** They read as
   implemented auth. They are not.
10. **`_SECTION_KEYS` is coupled to the stub writer's exact header strings.** See the
    paywall analysis above.

---

## 6. Cold-start guide for future me

### From a fresh clone

```bash
git clone https://github.com/satwik-bankapur/fpl-analyst.git
cd fpl-analyst

# Backend — Python 3.13
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # Windows
# .venv/bin/python -m pip install -e ".[dev]"        # Linux/macOS

.venv/Scripts/python -m pytest -q                    # expect 124 passed
.venv/Scripts/python -m fpl init-db                  # Alembic migrations, once
.venv/Scripts/python -m fpl sync                     # LIVE FPL API → scores → ep_scores
.venv/Scripts/python -m fpl mode                     # derived mode + next GW

# API
.venv/Scripts/python -m uvicorn api.main:app --reload --port 8000

# Frontend (second terminal)
npm --prefix web install
echo "NEXT_PUBLIC_API_URL=http://127.0.0.1:8000" > web/.env.local
npm --prefix web run dev                             # http://localhost:3000
```

Useful pages: `/` (landing + draft picker), `/story` (renders every component state
including the **only** exercise of `LockCard`), `/brief/draft/<uuid>`.

⚠️ **The `.claude/skills/run-fpl-analyst/driver.py` offline driver is gitignored and will
NOT be in a fresh clone.** Without it there is no offline path to exercise Layers 1–2
against the frozen snapshot — `sync`/`mode` hit the live API. If you want that workflow
back, un-ignore and commit the driver.

### Recurring human inputs

| Input | Where it lives | How it's fed |
|---|---|---|
| **`team_notes`** | `team_notes` table (`fpl/models.py:242`) | **`/admin` → Team notes** (below), or `POST /api/admin/notes` — Bearer `ADMIN_TOKEN`, `{team_id, note}`. |
| **`european_weeks`** | `european_weeks` table (`fpl/models.py:259`) | **`/admin` → European weeks** (below), or `POST /api/admin/euro-weeks`. Drives the v1.4 rotation dampener. Empty = no-op. |
| **Engine calibration** | `engine/config.py`, `config_version = "v1.4"` | Every weight/table is versioned; bumping `config_version` stamps into every components blob. **Never inline a magic number.** |
| **Graph tunables** | `graph/config.py` | Incl. the placeholder version constants — changing them invalidates the whole brief cache once. |
| **Writer tunables** | `writer/config.py` | Length gates (250–700 words), `MAX_RETRIES=1`, `REQUIRED_HEADERS`, model default, `SEND_TEMPERATURE` gate. |
| **Prompt** | `writer/prompts/system_v0.1.md` | Still v0.1. Iteration to v≥0.2 is the flagged human-in-the-loop work. |
| **`players_archive`** | `players_archive` table (`fpl/models.py:96`) | Normally written ONCE per season by the rollover routine inside `sync_fpl()`. When that is missed, `python -m fpl backfill-archive` is the recovery — see below. |

#### The `/admin` console (added 2026-07-30)

Both curated tables used to be curl-only. They now have a UI at **`/admin`** inside the same
Next.js app, built to make the Sunday pass a fifteen-minute click-through rather than a
sequence of hand-written `curl` bodies. The curl endpoints still work unchanged.

**Auth.** One prompt for `ADMIN_TOKEN`, verified against `GET /api/admin/overview` before it
is stored, kept in `localStorage`, sent as `Bearer` on every admin call. A 401 from anywhere
clears the token and re-prompts (`lib/admin.ts` dispatches `admin-unauthorized`;
`AdminShell` listens). A token that is merely *unreachable* — API down, `FRONTEND_ORIGIN`
mismatch — reports that instead of blaming the token. This is the existing operator token,
not user auth: **no accounts, no sessions, no login page** (docs/CLAUDE.md still holds).

**Not linked, not indexed.** Nothing on any public page links to `/admin`; the layout sets
`robots: noindex, nofollow, nocache`. `tests/test_api.py::test_admin_is_never_linked_from_a_public_page`
greps every public `app/**` and `components/*` file and fails if a link appears.

**Pages.**

| Page | What it's for |
|---|---|
| `/admin` | Post-sync sanity check: mode, sync freshness, row counts, archive-by-season, `match_method` spread, top 20 by `ep_next4`, and the flagged-player **triage queue** (below). Zeroes, an empty archive and a `match_method` that has collapsed onto `none` render red — the missed-rollover signature (above) is meant to be visible without reading carefully. |
| `/admin/notes` | Twenty cards, one per club. Empty notes show a manager / penalties / set-pieces / rotation / shape / watch skeleton as **placeholder** text — never pre-filled content, since a note that exists but was never written would be handed to the writer as fact. Per-card Save with an unsaved-changes indicator. Each card carries `id="club-<team_id>"` so the triage queue can link straight to it. |
| `/admin/euro-weeks` | Editable grid (club, GW, competition, importance) with add/remove, saved in one submission via `POST /api/admin/euro-weeks/replace` — upsert what's sent, delete what isn't. |
| `/admin/fixtures` | Club picker → next 6 with FDR colour-coding and the swing, computed by `graph.fixtures_util.swing_score` — the same function that raises a `fixture_cliff` problem, never a second implementation. |
| `/admin/inspect` | The **reasoning inspector** (below): the pipeline's working for one brief, the exact LLM payload it would send, and free-vs-paid side by side. |

**Read endpoints** (all `GET`, all Bearer-gated, all in `api/admin.py` / `api/inspect.py`,
neither of which writes): `/api/admin/overview`, `/notes`, `/euro-weeks`,
`/players?q=&flagged=`, `/fixtures/{team_short}?n=`, `/top?n=`, `/flag-queue`,
`/inspect/briefs?n=`, `/inspect/draft/{draft_id}`, `/inspect/team/{team_id}/{gw}`,
`/inspect/player/{player_id}`. `overview` also returns the 20-club list, so the picker and
the grid don't need a second round trip.

##### The flagged-player triage queue (added 2026-07-31)

The dashboard's flagged list used to be a flat table of ~260 players sorted by EP. True, and
useless on a Sunday: it answered "which players are flagged" when the question that spends an
hour well is "which club should I write a note about next". `api.admin.flag_queue` now groups by
flag, states in plain English whether each flag is **actionable / transient / a known
limitation**, and for the two a note actually fixes (`no_history_fallback`,
`thin_minutes_sample`) ranks clubs by `count × highest ep_next4` — unwritten notes first, each
row linking to `/admin/notes#club-<id>`. Clubs already covered sink down the list, so the queue
shrinks as it is worked.

A club whose scored players share very few distinct `ep_next4` values gets a **near-identical
EP** badge: the signature of a club scored entirely on baselines rather than its own data. It is
measured as a RATIO, not literal equality — the first implementation tested for equality and
would have never fired. On the 2025-26 data the three promoted clubs sit at 10–12 distinct
values across ~29 players (~0.4) and every established club at 26–33 of 29–36 (~0.9). Badged:
IPS, HUL, COV, and nothing else.

##### The reasoning inspector, `/admin/inspect` (added 2026-07-31)

Read-only, token-gated, **zero LLM spend**. Four things the serving path throws away:

1. **The trace.** `RequestCtx.trace` (default `False`) makes each node append a `TraceStage` —
   see `graph/trace.py`. Rendered as collapsible stages in pipeline order. MarketScout's is the
   one that did not exist before: it retains every REJECTED candidate with the filter that
   removed them (`already_in_squad`, `wrong_position`, `unavailable_status`, `over_budget`,
   `club_cap`, `below_top_n`, `insufficient_ep_delta`), counted and exampled. Strategist's
   records why each alternative lost, with the margin.
2. **The components blob.** Click any squad row for its full `ep_scores.components` — minutes
   basis, base/attack inputs, the per-fixture breakdown with FDR and CS, flags, and the raw
   JSON. That blob IS the engine's reasoning; reading it should not require SQL.
3. **The LLM payload.** The exact string `writer/serializer.py` would hand the model, verbatim
   and copy-pasteable, with the token estimate, which evidence blocks the budget rule kept vs
   dropped, and the cost at the configured model's rate (output priced at the generation cap).
   **No model is called** — `tests/test_trace.py` breaks `AnthropicWriter.generate` to prove it.
4. **Free vs paid.** The same brief through `gate_sections` twice, rendered through the real
   `BriefView` — so it doubles as a standing check that the Stage B paywall gates correctly
   before `PAYWALL_ENABLED` ever flips.

**Tracing must never change a brief.** `tests/test_trace.py::test_trace_off_leaves_the_run_untouched`
asserts a traced run and a normal run produce identical `brief_md`, `decisions`, `market`,
`diagnosis` and `warnings`, and that a normal run's `trace` is `[]`. If that ever fails, the
inspector is describing a different pipeline than the one users get and is worse than useless.

The inspector calls `run_brief` directly, not `graph.cache.get_or_generate` — so it writes no
`briefs` row and never pollutes the cache. It reports whether the persisted row still matches
what the code produces, alongside the writer version that wrote it: a mismatch where the
versions DIFFER is an orphaned row (expected after a `STUB_MODEL` bump), a mismatch where they
are the SAME means cached readers are being served prose the template no longer produces.

Stage A has no FPL client, so a `team_id` cannot be re-traced. That path serves the persisted
brief for the free/paid comparison and says so, rather than faking a trace.

##### An unchanged save must not bump `updated_at` — the rule this UI is built around

`max(team_notes.updated_at)` and `max(european_weeks.updated_at)` **are** the
`curated_notes_version` / `euro_weeks_version` halves of the brief cache key. A form re-sends
its contents on every click, including clicks where nothing was typed — so writing
unconditionally would invalidate **every cached brief in the database** on a no-op save.

`service.upsert_note`, `upsert_euro_week` and `replace_euro_weeks` therefore compare before
writing and skip if identical, returning `changed` so the UI can say "No change" rather than
claim a save that never happened. The UI additionally disables Save when nothing is dirty,
but that is a convenience — the server-side skip is the control. Covered by
`test_saving_an_unchanged_note_does_not_bump_updated_at`,
`test_saving_a_changed_note_does_bump_updated_at` and
`test_unchanged_euro_week_grid_does_not_bump`.

##### Pointing it at production

`/admin` is a client-side page against whatever `NEXT_PUBLIC_API_URL` names, so the same
build administers a deployed API:

```bash
NEXT_PUBLIC_API_URL=https://<your-render-service>.onrender.com npm --prefix web run dev
# then http://localhost:3000/admin, paste the PROD ADMIN_TOKEN
```

Two things must line up or the token prompt reports "API unreachable": `ADMIN_TOKEN` must be
set on that API (empty = admin closed, never open), and `FRONTEND_ORIGIN` must match the
origin you are browsing from — CORS is single-origin, and `localhost` and `127.0.0.1` are
different origins.

##### Timestamps carry an offset (fixed 2026-07-30)

Found by driving the dashboard: it reported an 8-hour-old sync that was 2 hours old. SQLite
returns naive datetimes, and a naive ISO string is parsed as **local** time by `new Date()`,
shifting every rendered timestamp by the viewer's UTC offset. `service.iso_utc()` now stamps
UTC at the serialization boundary for `/api/health` and every admin timestamp;
`test_every_served_timestamp_carries_a_utc_offset` holds the line.

#### Missed-rollover recovery — `backfill-archive` (added 2026-07-30)

`players_archive` is the only input pre-season analysis has, and it is written exactly once
per season by a rollover the sync has to *witness* (layer1 §6.2). If no sync runs between
the last finished gameweek and the API's season reset, the rollover is never detected and
the archive stays empty — which is what happened on the local DB before 2026-07-30.

It does not look like a bug. Every `ep` collapses onto the (position, price-band) baseline,
so projections are all near-identical, and the only surviving differentiator is clean-sheet
+ save points — goalkeepers top the table and the recommended captain is a keeper.

```bash
# 1. rebuild the archive from any saved end-of-season bootstrap payload
python -m fpl backfill-archive --season 2025-26 --from tests/assets/bootstrap_2025-26.json
# 2. recompute ep_scores through the (now populated) archive path
python -m fpl sync
```

Idempotent: the season's rows are deleted and rewritten in one transaction. Refuses to
overwrite an existing season's rows without `--force`. Offline and deterministic — it does
not sync for you, deliberately, so a failure in either half is unambiguous.
`fpl/backfill.py`, covered by `tests/test_backfill.py` (whose load-bearing assertion is
that the post-backfill top-20 by `ep_next4` genuinely spreads).

### The three things to do first

**1. Fix the docs that will actively mislead you — 30 minutes.**
Root `CLAUDE.md` claims Layers 3–5 are spec-only; they're built and merged. Same file
points at a gitignored driver as "committed". Everything else you decide will be built on
these two false premises. Also correct the stale stub-writer comments in `fpl/models.py:161`
and `fpl/__init__.py:5`.

**2. Get the re-skin out of local-only limbo — 30 minutes.**
`stage-a-reskin` is one unpushed commit holding all the frontend work. Tests pass, build
passes, its drift note says no contract changed. Merge to main and push, or at minimum
push the branch. Right now a disk failure loses it, and any deploy from main ships the
old UI.

**3. Resolve the section-key vocabulary before any paywall work — half a day.**
Detailed in §5. `_SECTION_KEYS` is coupled to the stub writer's header strings; the LLM
writer emits four different ones; `chips` is in the frontend contract but no backend path
produces it. Shipping `PAYWALL_ENABLED` on top of this either advertises a section that
doesn't exist or fails open and gives the paid tier away free. Add a test asserting every
`SECTION_ORDER` key is producible by every writer, then build Stage B on solid ground.

**And in parallel, on day one:** start Paddle account verification. It's the only item on
the critical path whose latency you don't control, and it gates every revenue item in §3.

### Honest assessment

The engineering is in better shape than the documentation. 124 passing offline tests, a
clean build, careful layer separation, and unusually disciplined drift notes — the specs
record their own divergences better than most production codebases.

The weak spot is the gap between what the repo *looks* like it does and what it does.
It reads as a five-layer AI FPL analyst with an LLM writer and a paywall-ready delivery
surface. It is actually a well-tested deterministic pipeline that renders **template**
briefs, with an LLM writer that is built, tested, and never called, and a paywall that
exists as an empty list literal in two places. Nothing is deployed. There is no payments
integration of any kind.

Distance to a paying customer is roughly **11 developer-days plus Paddle verification** —
achievable, but not "nearly there", and the section-key defect means one of those days
has to come before the rest.

---

## 7. Pre-hibernation fix pass — 2026-07-30 (post-audit)

Everything in this section is done, tested, and on `main`. **No Stage B work**: the
goal was a repo that is safe, truthful, and deployable across a two-month gap — not
one that takes money.

### ✅ Re-skin preserved (audit §1)

`stage-a-reskin` pushed to origin, merged into `main` with `--no-ff`, main pushed.
Six stale merged branch pointers deleted (`stage-a-free-tier`, `layer1`…`layer5`).
The one-local-commit risk is gone.

### ✅ Section-key unification — the paywall-fails-open fix (audit §5)

The defect: three disagreeing vocabularies meant gating on the spec's
`["bench_plan","chips","watchlist"]` would have matched nothing against LLM output and
**served the paid product free**, while the lock card advertised a `chips` section no
writer produced.

- **`graph/sections.py` is now the single vocabulary** — keys, titles, header aliases
  for both writer formats, the free/paid split, and the preview's separate key set. It
  lives in `graph/` because `writer/` may import `graph/` and never the reverse, so it
  is the only package graph/writer/api can share without breaking the boundary rule.
- **Every producer derives from it.** No `## Header` literal survives in
  `graph/nodes/writer.py`, `writer/generic.py`, or `writer/config.py`. `web/lib/api.ts`
  mirrors it, `api/service.py` consumes it.
- **Gating is an allow-list.** `gate_sections` returns only `FREE_SECTIONS`. A deny-list
  over paid keys leaks anything the splitter fails to recognise — one LLM typo (`&` to
  `and`) and paid analysis ships free. The allow-list fails the other way: a paying
  reader might be missing a section. Wrong in the recoverable direction. Unmappable
  headers also get an `x_` prefix so they cannot impersonate a canonical key.
- **`locked_sections` names only sections that exist** (present and non-empty), so the
  lock card cannot promise content that isn't behind it.
- **`chips` implemented** in the stub, gated on `chip_stance != "hold"` exactly as
  layer4 §3 specifies. **`fixtures` folded into `bench_plan`** — it existed only in the
  stub, the LLM writer could never produce it, and a section only one writer can emit
  cannot be gated coherently. Its content now renders under "Bench order & the plan",
  which is where the prompt has always put the four-gameweek outlook. Canonical list is
  now exactly the spec's six keys; spec, code, and lock-card copy agree for the first
  time. Dated drift notes added to layer3/layer4/layer5.
- **`PreviewTeaser` fall-through fixed** — it searched the brief vocabulary, which shares
  no key with the preview, so it always fell through to whatever key came first. Now
  ordered by `PREVIEW_ORDER`. Same section rendered, no longer by accident.
- **`/story`'s Stage B fixture rebuilt.** It hardcoded the exact bug being fixed. It now
  derives from `PAID_SECTIONS` and includes a real `chips` section, so it demos a state
  the API can actually produce.

**Test coverage: 155 total, up from 124.** `tests/test_sections.py` (28) is the contract
suite; `tests/test_graph.py::test_real_stub_output_splits_to_canonical_keys` and
`::test_chips_section_appears_only_when_stance_is_not_hold` run the **real** graph so the
literal fixtures cannot drift from the live writer. The three required properties:

| Property | Tests |
|---|---|
| (a) every canonical key producible by both writers, one splitter | 5 |
| (b) gating removes paid content for both writer formats | 6 |
| (c) unknown header can never leak a lockable section | 7 (5 parametrized drifts) |

`PAYWALL_ENABLED` still does not exist — mechanism only. `api.service.PAYWALL` is a
`False` constant; Stage A responses are byte-identical. The flip is one line, and it is
now provably impossible for it to fail open.

### ✅ Documentation truth pass (audit §5 trip hazards)

- **Root `CLAUDE.md` rewritten.** The "only Layers 1–2 exist" claim is gone; all five
  layers documented with real status. Added a **"Current serving reality"** section
  stating plainly: briefs are template text, `writer/llm.py` is wired to nothing, no
  payments exist, the paywall is off, nothing is deployed. Added a section-vocabulary
  rule and a testing note explaining why the fixtures are hand-written.
- **The driver claim is now true.** `.gitignore` uses `.claude/*` plus `!.claude/skills/`
  (git cannot re-include a path whose *parent* is excluded, so `.claude/` would have made
  the negation a no-op). `driver.py` + `SKILL.md` are committed; `settings.local.json`
  stays ignored. A fresh clone can now follow its own instructions.
- **`railway.toml` moved to `docs/railway.toml.deferred`** with an unmissable header. It
  described an abandoned platform from the repo root, where it read as active config.
  Kept, not deleted — it is the documented upgrade path.
- **Stale comments corrected:** `graph/cache.py` (claimed the curated tables don't exist),
  `fpl/models.py:161` and `fpl/__init__.py:5` (both still credited the Layer 1 stub writer
  that Layer 2 replaced).

### ✅ Deployment readiness — config only, no accounts (audit §4)

September-you deploys by filling in secrets, not by reverse-engineering prose:

- **`render.yaml`** — Render Blueprint for the launch topology. Secrets are `sync: false`
  (prompted, never stored in git); `ADMIN_TOKEN` / `BRIEF_TOKEN_SECRET` use
  `generateValue`. Documents the free-tier sleep and why `RUN_SCHEDULER` must stay unset.
- **`.env.example`** — every variable the project reads, grouped by layer, each with a
  comment. Includes the `DATABASE_URL` to `FPL_DATABASE_URL` trap and a clearly-marked
  Stage B block (Paddle, `PAYWALL_ENABLED`, `BRIEF_TOKEN_SECRET`) noting nothing reads
  them yet.
- **`web/.env.example`** — frontend config plus the CORS-must-match warning.
- **No `vercel.json`** — a standard Next.js app needs none; the root-directory setting is
  a dashboard step the runbook already covers.

### ✅ Loose ends (audit §3, §5)

- **`fpl/scheduler.py` quarantined in place** with a header explaining it is not running,
  why (free Render sleeps), that it is the paid-tier alternative and the only
  deadline-aware implementation, and that running it alongside the Actions cron
  double-syncs. Kept because deleting it would delete the upgrade path.
- **`?token=` removed** from `GET /api/brief/{team_id}/{gw}`. It was accepted and ignored;
  an auth-shaped parameter that does nothing reads as implemented security. FastAPI
  ignores unknown query params, so no caller breaks. Noted in code, the layer5 spec, and
  the frontend placeholder page.

### Deliberately NOT done

- **Everything in §3** — checkout, webhooks, `subscriptions`/`webhook_events`, JWT minting,
  the send job, email templates 1–2, the persisted preview singleton, `PAYWALL_ENABLED`.
  Stage B, out of scope for this pass. The §3 estimates stand, minus the section-key item
  (now done), so **~10.5 developer-days** plus Paddle verification.
- **Wiring `writer/llm.py` into the serving path.** Still the largest gap between
  perception and reality. Left alone deliberately: it needs prompt iteration and real LLM
  spend to evaluate, which is not pre-hibernation work. It is now stated plainly in
  `CLAUDE.md` and the layer4 drift note instead of being implied away.
- **Backups.** Neon free still has no scheduled backups. Unchanged, still a real gap with
  a hard deadline at the first season rollover.

### Revised "three things first"

Items 1 and 2 from §6 are done. Replacements:

1. **Start Paddle account verification** — day one. Still the only critical-path item
   whose latency you don't control, and it gates every revenue item in §3.
2. **Deploy Stage A from cold** using `render.yaml` + `docs/deploy-stage-a-free.md`.
   Config exists now, so it is an accounts-and-secrets exercise. Half a day. Nothing
   about the product is real until it is reachable.
3. **Decide the LLM question.** Either wire `writer/llm.py` into the serving path and
   iterate the prompt to v0.2+, or commit to shipping template briefs and stop describing
   the product as AI-written. Both are defensible; the current in-between is what makes
   the repo hard to read honestly.

---

## 8. Security & quality audit — 2026-08-01

Full findings, evidence and effort estimates: **`docs/AUDIT.md`**. This section records
only what changed about the *stated state of the app*, so §1–7 above stay readable.

30 findings: 2 CRITICAL, 12 HIGH, 11 MEDIUM, 5 LOW. 13 fixed on branch
`audit-2026-08-01`; 17 deferred by instruction (design decision, Stage B, or
known-deferred). Suite 252 → **275 passing**; every fix carries a regression test.

### What this changes about claims made earlier in this document

- **"Rate limiting works" is now true; it was not before.** `_client_ip` read the
  *leftmost* `X-Forwarded-For` entry — a value the caller supplies — so every per-IP
  limit on the public surface was bypassable with one rotating header. Fixed (reads the
  rightmost, proxy-appended entry). **But see the standing caveat below.**
- **The admin surface is no longer the single-control surface §5 implied.** The token is
  now compared with `secrets.compare_digest` (it was `!=`, a timing oracle), failed
  attempts are throttled, and a router-derived test asserts all **15** admin operations
  reject an anonymous caller — so a new `/api/admin/*` route added without
  `_require_admin` now fails the suite rather than shipping open.
- **`POST /api/signup` was an unauthenticated mail cannon.** No rate limit, and a welcome
  email sent on every call to any address. Now rate-limited. The real fix — double opt-in
  — is a Stage B requirement (AUDIT §D5), not done.
- **`email_signups` was losing data on every re-signup.** `service.signup` used
  `session.merge` with the request's values, so the draft-modal path (`team_id=None,
  source="draft"`) NULLed a homepage signup's team id and rewrote its source — and set
  `tier="free"` unconditionally, which in Stage B **demotes a paying subscriber**. Now
  fills blanks only; `tier` is not written here at all.
- **`/api/docs` and `/openapi.json` were public** — a full map of the admin surface. Now
  off by default behind `DOCS_ENABLED`.
- **The free-tier deployment would have 500'd its first visitor after every sleep.**
  `fpl/db.py` had no `pool_pre_ping`; Render sleeps and Neon autosuspends, both leaving
  dead sockets in the pool. Fixed.
- **The brief cache lookup was a table scan.** `briefs.input_hash` had no index despite
  being read on every request. Fixed + migration `c3d4e5f6a7b8`.

### Standing caveat — the rate limiter is still not production-grade

Worth stating here because §2 and §4 read as if it is. `api/ratelimit.py` is an
in-process dict on a service that sleeps every 15 minutes: **every counter resets on
every wake**, and it is per-process. Post-fix it is real protection against a naive
script and none against a patient one. It must be replaced with a DB-backed counter
(and/or a global daily cap) **before `PAYWALL_ENABLED` or any LLM wiring**, because
that is the moment it starts guarding money instead of CPU. Three costed options in
AUDIT §C2.

### Confirmed clean — do not re-audit these

- **No committed secrets** in the full git history. The two `postgresql://user:pass@…`
  hits are placeholders in the two deploy runbooks. Nothing to rotate.
- **No hardcoded credential or fallback secret.** Every secret defaults to empty and
  empty fails *closed* (admin shut, mail a logged no-op) — the opposite of the
  "if no secret use 'dev'" pattern.
- **`pip-audit`: no known vulnerabilities.**
- **No SQL injection surface.** Everything is ORM/Core; the only `text()` calls carry
  literal partial-index predicates.
- **No XSS vector in the admin UI.** Zero `dangerouslySetInnerHTML`/`innerHTML` anywhere
  in `web/`; `players.news` and `team_notes.note` render as escaped React text children.
- **`sync_fpl` is genuinely abort-or-nothing**, and every other multi-write path is
  inside a single `session.begin()`.
- **Timezones are correct end to end** — aware on ingest, `timestamptz` in storage,
  `iso_utc` on the way out. One latent naive-vs-aware bug in the *quarantined* scheduler
  (AUDIT M4).
- **`alembic upgrade head` is safe against a populated production DB** — every migration
  audited; the only destructive operations are in `downgrade()`.

### Two open questions this audit raises for you

1. **Retention.** `draft_squads`, `briefs` and `email_signups` grow without bound and
   nothing would tell you before Neon's 0.5 GB runs out. AUDIT §H5 proposes a policy and
   the cheapest place to run it (inside the existing sync handler). Needs your numbers.
2. **`next@14.2.35`** carries several high-severity advisories and the remedy is
   `next@16` — two majors, roughly half a day including re-verifying the re-skin. It gets
   more expensive the longer Stage B builds on 14.

---

## 9. Stage B — payments, built and switched off (2026-08-01)

Branch `stage-b`. Full design rationale: `docs/layer5-delivery-surface.md` §8, the
2026-08-01 note. Requirements it was built against: `docs/AUDIT.md` §D.

Suite 275 → **383 passing** (`tests/test_stage_b.py`, 104 new tests). `npm run build`
clean, 12 routes.

### What this changes about claims made earlier in this document

- **§3 "What's missing between here and a paying customer" is now mostly built.** Checkout
  (`POST /api/checkout`), the signed webhook (`POST /api/webhooks/paddle`), the four new
  tables, tokenized full-view links, the weekly send batch, unsubscribe/delete and the
  paywall flip all exist. What remains between here and revenue is **an account and a
  deploy**, not code.
- **§8's standing caveat is discharged.** Anything that costs money now goes through a
  DB-backed counter (`rate_hits`) plus a global circuit-breaker bucket, so a Render sleep
  no longer resets the budget. The in-process limiter is kept as the cheap first layer.
- **"Nothing is deployed" is still true**, and it is now the binding constraint.

### The switches, and what each one costs to flip

Everything ships off. In order:

| Switch | Needs | Blast radius if wrong |
|---|---|---|
| `BRIEF_TOKEN_SECRET` | 32+ random chars | App refuses to boot without it once the two below are on — deliberate |
| `PAYWALL_ENABLED` | nothing else | Paid sections lock. With no `PADDLE_WEBHOOK_SECRET` nothing can ever unlock — the startup check warns |
| `PADDLE_*` | a Paddle account, a verified webhook destination, two price ids | Wrong secret = every webhook 401s = no customer is ever granted access |
| `SEND_ENABLED` | `RESEND_API_KEY` + a verified sending domain | On without the key: the batch claims `send_log` rows and delivers nothing |

### What has never been exercised, and cannot be from here

**There is no Paddle account and nothing is deployed**, so no sandbox transaction has ever
been created, no webhook has ever been delivered by Paddle, and no email has ever arrived
in a real inbox. Everything below the provider boundary is proven; the boundary itself is
not. The signature scheme is implemented to Paddle's documented spec
(`HMAC-SHA256` over `f"{ts}:{raw_body}"`, header `ts=…;h1=…`) and every test computes its
signatures locally to that scheme — which proves the verifier, not the format.

**The first live test to run, in this order:** create the sandbox account → set the
notification destination and copy the *destination* secret (not the API key) → replay one
sandbox `subscription.created` → confirm a `subscriptions` row → then, and only then,
`SEND_ENABLED=true` with a single address on the list.

### Still open after Stage B

- **The LLM writer is still wired to nothing.** A paying customer would receive the
  deterministic stub. This is the last product gap, and it is gated on prompt iteration,
  not on engineering (layer4 §8; `/admin/inspect` renders real payloads at zero spend).
- **Double opt-in** for signups (AUDIT §D5 / priority 2) is not built. The rate limit
  bounds the mail cannon; it does not close it.
- **Nothing schedules the send batch.** `run_batch` is reachable only via
  `POST /api/admin/send`. Wiring it to the GitHub Actions cron is a deploy decision — and
  doing it while `RUN_SCHEDULER=true` would mail every customer twice.
- **`next@14.2.35`** — unchanged from §8, and now one major further behind whatever ships
  next.

---

## 10. Consolidation truth pass — 2026-08-04

*Written after merging `stage-b` and `accounts` into `main`, immediately before creating
any external account. This section supersedes every count and every branch claim earlier
in this document. It exists for one reader: the version of me who comes back in September
having forgotten all of this, sees a green test suite, and concludes the integrations
work. **They do not.** Read §10.4 before you conclude anything.*

### 10.1 Branch state — everything is merged, nothing is outstanding

`main` @ **`972f2b3`** — "Close accounts + auth". In sync with `origin/main`, working
tree clean.

The merge was done in dependency order (`accounts` forks off `stage-b`, which forks off
`main`), both with `--no-ff` so the two bodies of work stay legible in the history:

```
972f2b3  Close accounts + auth              (merge)
bd4f028  Close Stage B: payment machinery   (merge)
67a7472  Accounts + auth: magic link, Google, sessions, and the entitlement rewire
f9a3dee  Stage B: payments, tokens, the paywall flip and the weekly send
351514c  Merge audit-2026-08-01
```

Both merges were clean — **zero conflicts**.

**There are no unmerged branches.** Every local pointer that was fully merged has been
deleted; `main` is the only local branch. The remote still carries the historical feature
branches (`stage-b`, `accounts`, `audit-2026-08-01`, `inspector-2026-07-31`,
`admin-ui-2026-07-30`, `stage-a-reskin`) as archived pointers — they contain nothing that
is not in `main`. §1's "Branches" table is now historical; ignore it.

### 10.2 Verified on `main` after the merge — not on the branches

Both of these were run **after** the second merge, on `main`, because a suite that was
green on two branches separately proves nothing about their merge:

| Check | Result |
|---|---|
| `.venv/Scripts/python -m pytest -q` | **467 passed**, 1 warning, ~70s |
| `npm --prefix web run build` | clean, **17 routes** |
| `alembic heads` | `e5f6a7b8c9d0` (accounts) |
| `git status` | clean, `main` in sync with `origin/main` |

**Test count by file — 467 total:**

| File | Tests | | File | Tests |
|---|---:|---|---|---:|
| `test_stage_b.py` | 104 | | `test_engine.py` | 18 |
| `test_accounts.py` | 83 | | `test_parsers.py` | 15 |
| `test_api.py` | 81 | | `test_backfill.py` | 11 |
| `test_imports.py` | 54 | | `test_writer.py` | 9 |
| `test_graph.py` | 30 | | `test_sync.py` | 7 |
| `test_sections.py` | 28 | | `test_rollover.py` | 4 |
| `test_trace.py` | 19 | | `test_client.py` | 4 |

The trajectory, for calibration: 155 (§7) → 252 (inspector + admin) → 275 (§8 security)
→ 383 (Stage B) → **467** (accounts).

### 10.3 Route and surface counts

**API — 41 endpoints across 38 unique paths** (three paths carry two methods:
`/admin/euro-weeks`, `/admin/notes`, `/email/unsubscribe`).

| Group | Count | Auth |
|---|---:|---|
| Public / anonymous | 9 | none — `/health`, `/brief`, `/brief/{team}/{gw}`, `/brief/draft/{id}`, `/players`, `/signup`, `/preview/latest`, `/preview/{gw}`, `/checkout` |
| Auth (`/auth/*`) | 6 | session-optional by design |
| Account | 1 | session — `/account/delete` |
| Email lifecycle (`/email/*`) | 4 | token in link |
| Paddle webhook | 1 | HMAC signature |
| Admin (`/admin/*`) | 20 | `ADMIN_TOKEN` Bearer |

**Web — 17 routes** (up from 15; `/terms` and `/refunds` added 2026-08-04):

- **11 public:** `/`, `/story`, `/login`, `/account`, `/privacy`, `/terms`, `/refunds`,
  `/unsubscribe`, `/brief/[teamId]`, `/brief/draft/[draftId]`, `/_not-found`
- **6 admin** (token-gated, `noindex`): `/admin`, `/admin/accounts`, `/admin/euro-weeks`,
  `/admin/fixtures`, `/admin/inspect`, `/admin/notes`

**Database — 20 tables.** (CLAUDE.md's "13 tables" described Layer 1 only and predates
both later builds: Stage B added `subscriptions`, `webhook_events`, `send_log`,
`rate_hits`; accounts added `accounts`, `sessions`, `magic_link_tokens`.)

### 10.4 What has never touched a real external provider

**This is the section that matters. A green suite is not a working integration.**

Every test in this repo is offline and deterministic — frozen JSON assets served through
a `FakeClient`, and every signature computed locally to the provider's *documented*
scheme. That design is correct and deliberate. It also means the suite proves the code on
**our** side of each boundary and says **nothing** about the boundary itself.

| Provider | Ever contacted? | What is proven | What is NOT proven |
|---|---|---|---|
| **Paddle** | **Never.** No account exists — neither sandbox nor live. | The webhook verifier, idempotency via `webhook_events`, the entitlement allow-list, the price-id→plan map, season-pass expiry, checkout request construction. 104 tests. | That Paddle's real signature header matches our parse. That a real `subscription.created` payload has the fields we read. That checkout returns a URL that opens. **No transaction has ever been created, by anyone, in any environment.** |
| **Google OAuth** | **Never.** No Google Cloud project exists. | State/nonce CSRF handling, the callback flow, ID-token issuer checks, verified-email enforcement, cross-provider account unification. | That the registered redirect URI matches (Google compares exact strings). That the consent screen passes. That a real ID token parses. |
| **Resend** | **Never.** No account, no verified domain, no API key. `RESEND_API_KEY` is empty, so `api/mail.py` logs and returns. | Message construction, the RFC 8058 one-click unsubscribe header, `send_log` claiming, the batch cap. | **That any email has ever arrived in any inbox.** Not one has. SPF/DKIM have never been evaluated by a receiving server. |
| **Neon / Postgres** | **Never.** Every test runs on in-memory SQLite. | Schema migrations up/down/up with zero drift. | That the migrations apply against real Postgres. Dialect differences are exactly why `citext` was rejected in favour of normalise-at-the-boundary. |
| **Render** | **Never.** No service created. | `render.yaml` is syntactically a blueprint. | That it deploys. That the start command's `init-db && uvicorn` chain works there. |
| **Vercel** | **Never.** No project created. | `npm run build` is clean locally. | That the `web/` root-directory setting and env wiring work. |
| **GitHub Actions cron** | **Never run.** The workflow exists; the two required secrets do not. | The YAML is valid. | That it authenticates against a live `/api/admin/sync`. |
| **FPL API** | **Yes — this one is real.** `python -m fpl sync` hits the live API and has been run. | The client, parsers, and abort-or-nothing sync against real upstream payloads. | Nothing outstanding. |

**The single sentence to carry into September:** the only external system this project has
ever actually spoken to is the FPL API. Payments, login-with-Google, and email are all
code that has never met the thing it is written against.

`docs/GO-LIVE.md` is the ordered runbook that closes this list, and its step 13 is the
first-live-test sequence that converts each row above from "not proven" to "proven".

### 10.5 Still open — unchanged by this pass

Nothing in the merge fixed any of these. They were open before and they are open now:

- **The LLM writer is still wired to nothing.** `api/service.py` imports exactly one
  symbol from `writer/` (`render_generic_stub`). Every brief a customer would receive
  comes from the deterministic template stub. This is the last product gap, gated on
  prompt iteration, not engineering. Do not describe the product as LLM-written.
- **The three legal pages are two-thirds placeholder.** `/privacy` is real prose;
  `/terms` and `/refunds` are TODO stubs with the routing and footer links in place.
  Paddle's live review blocks on all three. See `docs/LAUNCH-CHECKLIST.md`.
- **Double opt-in for signups** (AUDIT §D5) is not built.
- **Nothing schedules the send batch.** `run_batch` is reachable only via
  `POST /api/admin/send`.
- **`next@14.2.35`** — still behind.
- **Everything ships off:** `PAYWALL_ENABLED=false`, `SEND_ENABLED=false`,
  `RUN_SCHEDULER=false`, no Paddle credentials, no Google credentials, no Resend key.
