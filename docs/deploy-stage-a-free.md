# Deploy runbook — Layer 5 Stage A, **free-tier topology**

The zero-cost path to a live pre-season product:
Render (API) + Neon (Postgres) + GitHub Actions (sync cron) + Vercel (Next.js) + Resend (email).

**Why this exists:** `docs/deploy-stage-a.md` targets Railway, which now requires a paid
Hobby plan. This runbook is the parallel free-tier version. **The paid runbook is not
obsolete** — it stays as the upgrade path (see §9). Same Stage A scope: no checkout,
webhooks, weekly sends, tokens, or paywall. The stub Writer serves briefs; no LLM key.

**What's different from the paid runbook** (the whole diff in one table):

| | paid (`deploy-stage-a.md`) | free (this file) |
|---|---|---|
| API host | Railway | **Render** free web service (sleeps when idle — §1) |
| Postgres | Railway Postgres | **Neon** free (§2) |
| Sync scheduler | in-process APScheduler, `RUN_SCHEDULER=true` | **GitHub Actions cron** → `POST /api/admin/sync`; `RUN_SCHEDULER` stays **off** (§4) |
| Sync cadence | daily → 2×/day → 3-hourly in deadline−48h | flat **twice daily** (§4) |
| Domain | `example.com` + `api.example.com` | **none** — `*.vercel.app` + `*.onrender.com` (§5) |
| Frontend | Vercel | Vercel — **unchanged** (§5) |
| Email | Resend | Resend — **unchanged** (§6) |

**How to read this:** plain steps you can execute yourself. Every step needing a human
decision or credential is marked **🔴 HUMAN**. Nothing here has been run. Do them in order.

---

## 0. Accounts & one-time prerequisites — 🔴 HUMAN

1. **GitHub** — push this repo. All three platforms deploy from it, *and* it runs the sync
   cron, so the repo isn't optional here the way it is on paid tiers.
2. **Render** (render.com) — hosts the API. Free web service, no card.
3. **Neon** (neon.tech) — hosts Postgres. Free project, no card.
4. **Vercel** (vercel.com) — hosts the frontend. Free Hobby.
5. **Resend** (resend.com) — transactional email. Free tier is sufficient for launch
   (~100 emails/day, 3k/month — plenty for a Stage A list). *Optional:* with no
   `RESEND_API_KEY` the welcome email is a silent no-op; you can ship without it.
6. **Domain — skipped.** Launch on the platform subdomains. §5 wires CORS to those; §9
   covers adding a real domain later.

Generate one secret now and keep it safe — you'll paste it in three places
(Render env, GitHub secret, and your own curl tests):

- `ADMIN_TOKEN` — bearer for the admin endpoints, **including the new sync trigger**.
  🔴 HUMAN: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
  On the free tier this token is what stands between the public internet and your sync
  job — treat it as a production credential, not a dev convenience.

---

## 1. Render — API service

1. **New → Web Service** → connect the GitHub repo → branch `main`.
2. **Runtime:** Python 3. **Region:** anything (pick one near you; free tiers are all slow
   to cold-start anyway).
3. 🔴 HUMAN — **Build Command** (Render's default won't install the Postgres driver;
   `pip install .` alone omits the `postgres` extra and the app boots but can't open a
   connection):
   ```
   pip install '.[postgres]'
   ```
4. 🔴 HUMAN — **Start Command** (identical to the paid runbook — migrations to head on
   every deploy, then serve; Render supplies `$PORT`):
   ```
   python -m fpl init-db && uvicorn api.main:app --host 0.0.0.0 --port $PORT
   ```
5. **Instance type: Free.**
6. **Health Check Path:** `/api/health` (optional but recommended — it makes a broken
   deploy fail visibly instead of serving 502s).
7. Don't deploy successfully yet — it needs `FPL_DATABASE_URL` (§2, §3). A first deploy
   before Postgres exists will fail; that's expected, not a problem.

### 🔴 The free-tier sleep behavior — know this before you launch

**Render free services spin down after ~15 minutes with no inbound traffic.** The next
request wakes them, and that request **waits for a cold start: roughly 30–60 seconds**
(container boot + `init-db` + app import). Consequences you are choosing to accept:

- **Your first visitor after an idle period waits ~a minute for the landing page's API
  calls.** On a Reddit-link launch this hits the *first* person to click, repeatedly,
  throughout a quiet night. This is the single biggest UX cost of the free tier.
- **The sync cron pays it too.** That's why `.github/workflows/sync.yml` pings
  `/api/health` to wake the service before it POSTs the sync (§4).
- **Free tier gives ~750 instance-hours/month.** A calendar month is ~730 hours, so an
  always-awake service *just barely* fits — meaning an uptime pinger every 10 min would
  defeat sleep but consume essentially your whole free allowance, with no headroom if you
  ever run a second free service. 🔴 HUMAN judgement: pinging to stay warm is a real option
  for launch week (better first impression), but it is not free-forever headroom, and it's
  the thing to turn off first if you hit the cap. The paid upgrade (§9) removes sleep entirely.
- **No shell on the free tier.** The paid runbook says "Railway → Shell → `python -m fpl sync`"
  to force a sync; you can't do that here. The equivalent is the workflow's manual
  **Run workflow** button, or a curl (§4, §7).

## 2. Neon — Postgres

1. **New Project** → name it → note the region.
2. Neon shows a **connection string** (Dashboard → Connect, or Connection Details):
   ```
   postgresql://user:password@ep-xxxx-pooler.region.aws.neon.tech/dbname?sslmode=require
   ```
   Copy the **pooled** one (host contains `-pooler`) — the free service is small and
   pooling is the friendlier default.
3. 🔴 HUMAN — **scheme transform (same gotcha as the paid runbook, easy to miss):** this
   app uses **psycopg 3**, so the scheme must be `postgresql+psycopg://`, not bare
   `postgresql://`. Change **only** the scheme; keep `?sslmode=require` — Neon requires TLS:
   ```
   postgresql+psycopg://user:password@ep-xxxx-pooler.region.aws.neon.tech/dbname?sslmode=require
   ```
   This exact string is what goes into `FPL_DATABASE_URL` in §3.
4. **Neon free also sleeps** (compute autosuspends after ~5 min idle). It wakes on connect
   in a second or two — much faster than Render's cold start, and it stacks *behind* it
   rather than adding a separately-visible delay. Nothing to configure; just don't be
   surprised by a slow first query in the Neon SQL editor.
5. 🔴 HUMAN — **backups: this is a real downgrade from the paid runbook.** The paid one says
   "enable automated daily backups" because `briefs` and `players_archive` are
   irreplaceable — the archive is what pre-season analysis *runs on*, and it cannot be
   refetched from the FPL API once the season rolls over. Neon free gives you only a short
   point-in-time restore window (~24h) and no scheduled backups. Mitigation, pick one:
   - Accept the risk for Stage A (the DB is rebuildable from a sync *until* the first
     rollover archives last season — after that, it is not), **or**
   - 🔴 run an occasional manual dump yourself:
     `pg_dump "postgresql://…(bare scheme, sslmode=require)…" > backup.sql`
   Revisit this before the season rollover; it's the strongest single argument for §9.

## 3. Environment variables (Render → service → Environment)

The left column is the spec §5 name; the middle is the **actual env var this code reads**
(see `docs/layer5-delivery-surface.md` §8 for why they differ).

| spec §5 name | actual env var | value | notes |
|---|---|---|---|
| `DATABASE_URL` | `FPL_DATABASE_URL` | the `postgresql+psycopg://…?sslmode=require` string from §2.3 | **name + scheme both differ** |
| — | `RUN_SCHEDULER` | **do not set** (default `false`) | 🔴 the free-tier change: no in-process scheduler — GitHub Actions drives sync (§4) |
| `FRONTEND_ORIGIN` | `FRONTEND_ORIGIN` | `https://<your-project>.vercel.app` | CORS allow-list; exact origin, **no trailing slash** — set after §5 |
| `ADMIN_TOKEN` | `ADMIN_TOKEN` | the secret from §0 | bearer for `/api/admin/*` **incl. `/api/admin/sync`** |
| `RESEND_API_KEY` | `RESEND_API_KEY` | from Resend (§6) | omit to disable welcome email (no-op) |
| `SEND_FROM` | `SEND_FROM` | see §6 | must be on a Resend-verified domain, or use Resend's test sender |

**Not needed in Stage A** (Stage B only): `BRIEF_TOKEN_SECRET`, `PADDLE_*`, `LLM_API_KEY`,
`LLM_MODEL`, `PADDLE_PRICE_*`.

Deploy (or redeploy) after setting these. The start command runs `init-db` → Alembic
migrations apply to the fresh Neon database. Watch the deploy log: migrations running to
head is the proof this step worked.

## 4. GitHub Actions — the sync cron (replaces APScheduler)

**Why:** the paid topology runs APScheduler inside the API process. A Render free service
that sleeps after 15 min idle cannot hold an in-process scheduler — it stops existing
between requests. So sync moves out of the process and becomes an external cron call.

The code side already exists on this branch:
- **`POST /api/admin/sync`** (`api/routes.py`) — bearer `ADMIN_TOKEN`, calls the existing
  `sync_fpl()` via `service.run_sync()`. Returns the `SyncResult` as JSON
  (`mode`, `players_updated`, `ep_rows`, `fixtures`, `rollover`, `duration_s`).
  A fetch failure returns **503 `{"error":"sync_aborted"}`** with the DB untouched
  (abort-or-nothing holds: stale beats broken).
- **`.github/workflows/sync.yml`** — twice-daily cron + a manual **Run workflow** button.
  It wakes the service via `/api/health` first (cold start, §1), then POSTs the sync with a
  600s timeout, and **fails the job on any non-200** so a silently-stale DB shows up red in
  the Actions tab instead of going unnoticed.

**🔴 HUMAN — set two repo secrets** (Settings → Secrets and variables → Actions → New
repository secret):

| secret | value |
|---|---|
| `API_BASE_URL` | `https://<your-service>.onrender.com` (no trailing slash) |
| `ADMIN_TOKEN` | the same secret from §0 / §3 |

**Cadence — a deliberate downgrade.** Layer 1 §3 specifies daily (pre-season) → twice daily
(baseline in-season) → **every 3 hours inside the deadline−48h window**. GitHub cron is a
fixed schedule and can't derive the next deadline from the DB, so the free-tier launch runs
a **flat twice daily** (06:00 / 18:00 UTC) in all windows. That is correct-enough for Stage A
— pre-season has no deadline pressure and the spec only wants daily there, so **twice daily
is actually more than pre-season needs**. It becomes wrong at the GW1 deadline, when price
changes and injury news matter within hours. Upgrade paths, cheapest first:

1. Add a second cron block for the deadline window and enable it manually the day before
   (crude, free, works).
2. Move back to the in-process scheduler on a paid instance (§9) — restores the full
   deadline-aware cadence with no new code, since `fpl/scheduler.py` already implements it.

Note GitHub disables scheduled workflows in repos with **no activity for 60 days** (it
emails first). Not a launch concern; a "why did sync stop in the off-season" concern.

## 5. Vercel + CORS on platform subdomains

Vercel is **unchanged from the paid runbook** except the URLs.

1. New Project → import the same GitHub repo.
2. 🔴 HUMAN — **Root Directory = `web`** (the Next.js app lives in `web/`, not repo root).
   Framework preset then auto-detects as **Next.js**. If it shows FastAPI, the root
   directory is still wrong — fix that before deploying.
3. Environment variable: `NEXT_PUBLIC_API_URL = https://<your-service>.onrender.com`
   (your Render URL from §1; no trailing slash).
4. Deploy. Note the production URL: `https://<your-project>.vercel.app`.
5. **Reconcile the cross-reference** — back on Render, set
   `FRONTEND_ORIGIN = https://<your-project>.vercel.app` → redeploy the API.
   - 🔴 CORS is exact-match: scheme + host, no trailing slash. A mismatch here is the most
     common "the picker won't load" cause.
   - 🔴 **Free-tier-specific trap:** Vercel gives every preview deployment its own hostname
     (`<project>-<hash>-<user>.vercel.app`). `FRONTEND_ORIGIN` allows **one** origin, so
     **previews will fail CORS against the live API.** That's acceptable (test locally
     against the Render URL instead); just don't let it convince you production is broken.

## 6. Resend — email (optional for launch)

Free tier is sufficient; **unchanged from the paid runbook** except that you have no domain
to verify yet.

- **Without a domain:** use Resend's shared test sender for `SEND_FROM` (e.g.
  `Acme <onboarding@resend.dev>`). It works for real sends but 🔴 **only to your own
  verified account address** — fine for proving the flow, useless for actual signups. So for
  a genuine free launch: either skip email entirely (leave `RESEND_API_KEY` unset — welcome
  sends become no-ops, everything else works), or buy the domain sooner (§9).
- **With a domain (later):** Resend → Domains → add it → 🔴 add SPF + DKIM records at your
  registrar → wait for "Verified" → set `SEND_FROM` to e.g. `FPL Analyst <briefs@yourdomain.com>`.
- Either way: create an API key → set `RESEND_API_KEY` on Render → redeploy.

## 7. First production sync

Unlike the paid runbook, **nothing syncs on its own at startup** — there's no in-process
scheduler. The DB is empty until you trigger the first sync.

🔴 HUMAN — trigger it: GitHub → **Actions → sync → Run workflow**. Watch the run; the
"Trigger sync" step prints the HTTP code and the `SyncResult` JSON. Or curl it yourself:

```bash
curl -sS -X POST "https://<your-service>.onrender.com/api/admin/sync" \
  -H "Authorization: Bearer $ADMIN_TOKEN" --max-time 600
```

Expect it to take a while on the first call (cold start + full fetch + scoring ~700 players).

- 🔴 HUMAN judgement: pre-season, the live FPL API may still serve **last season's** data
  until the official reset. `/api/health` → `mode` tells you what the app derived.
  `PRESEASON` = landing shows the draft picker (correct for Stage A).

## 8. Smoke checklist (run after §7)

Against the live platform subdomains:

- [ ] `GET https://<svc>.onrender.com/api/health` → 200, `mode` set, `last_sync` **not null**
      (proves the first sync landed). Expect the first hit after idle to take ~a minute.
- [ ] `GET …/api/players` → JSON array (~hundreds of rows).
- [ ] `GET …/api/preview/latest` → 200 with `sections`.
- [ ] `POST …/api/admin/sync` **without** the bearer header → 401. **With** it → 200 + JSON.
- [ ] `POST …/api/admin/notes` with `Authorization: Bearer <ADMIN_TOKEN>` → `{"ok":true}`;
      without → 401.
- [ ] GitHub → Actions → **sync** → Run workflow → green.
- [ ] `https://<project>.vercel.app` loads; pre-season shows **"Rate my draft"**.
- [ ] Open the picker → players load (the client→API CORS path; if it hangs, re-check
      `FRONTEND_ORIGIN` §5.5 — and give it a minute first, the API may be cold-starting).
- [ ] Pick 15 within budget → **Get my brief** → lands on `/brief/draft/…`, all sections render.
- [ ] (If Resend enabled) submit an email → welcome email arrives (only to your own address
      on the `resend.dev` test sender — see §6).

## 9. Upgrade path + rollback

**This topology is the launch configuration, not the destination.** What each paid step buys,
in the order the pain will actually arrive:

| upgrade | fixes | when |
|---|---|---|
| Buy a domain | real sender address (§6), real URLs; then follow §5 of `deploy-stage-a.md` for DNS on either host | as soon as you want email signups to *work* |
| Render paid instance (or Railway per `deploy-stage-a.md`) | **no sleep** → no cold-start on your first visitor; **shell access**; and lets you set `RUN_SCHEDULER=true` to restore the deadline-aware cadence — delete the workflow, no code change | when cold starts cost you users, or at the GW1 deadline when 3-hourly matters |
| Neon paid / managed backups | real backups for `players_archive` (§2.5) | 🔴 **before the first season rollover** — that's when the data becomes unrecoverable |

**Going back to Railway** (`deploy-stage-a.md`) is a config change, not a code change: set
`RUN_SCHEDULER=true`, delete `.github/workflows/sync.yml` (or leave it — it's harmless if
the secrets are removed, and the route stays useful for manual "sync now"). `/api/admin/sync`
is additive and safe to keep in either topology.

**Rollback:** both platforms deploy from `main` and keep prior deploys — redeploy the previous
build from the dashboard. DB migrations are additive; no destructive down-step needed for a
Stage A rollback.

**Cost:** stub Writer = zero LLM spend. Briefs are cached (input-hash), so the rate limit
(5 generations/IP/hr) plus the cache keep compute trivial — well inside free-tier CPU.

**Stage B, when you get there:** see `deploy-stage-a.md` §9. Stage B (checkout, webhooks,
weekly sends) assumes a service that doesn't sleep and a real domain — **plan to be off the
free tier by then**. Start Paddle account verification early regardless; provider
verification latency is the only critical-path item you don't control.
