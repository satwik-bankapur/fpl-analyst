# Deploy runbook — Layer 5 Stage A

Exact ordered steps to take this repo from `main` to a live pre-season product:
Railway (API + in-process scheduler) + Vercel (Next.js) + Resend (email) + DNS.

**Scope:** Stage A only — no checkout, webhooks, weekly sends, tokens, or paywall
(those are Stage B). The stub Writer serves briefs; no LLM key needed yet.

**How to read this:** plain steps you can execute yourself. Every step that needs a
human decision or a credential is marked **🔴 HUMAN**. Nothing here has been run —
the accounts don't exist yet. Do the steps in order; each section depends on the ones
above it.

---

## 0. Accounts & one-time prerequisites — 🔴 HUMAN

Create these (all have free tiers that cover launch):

1. **GitHub** — push this repo; both platforms deploy from it. Decide the repo/visibility.
2. **Railway** (railway.app) — hosts the API + Postgres.
3. **Vercel** (vercel.com) — hosts the frontend.
4. **Resend** (resend.com) — transactional email. *(Optional for launch: with no
   `RESEND_API_KEY` the welcome email is a silent no-op — you can ship without it and
   enable later.)*
5. **A domain** — 🔴 HUMAN: pick and register it. This runbook uses `example.com`;
   substitute yours everywhere. You need two hostnames: `example.com` (frontend) and
   `api.example.com` (API).

Generate one secret now and keep it safe:
- `ADMIN_TOKEN` — bearer for the curated-notes admin endpoints. 🔴 HUMAN: generate a
  random 32+ char string (e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`).

---

## 1. Railway — API service

1. New Project → **Deploy from GitHub repo** → select this repo, branch `main`.
2. Railway auto-detects `railway.toml`. Confirm it reads:
   - `startCommand = python -m fpl init-db && uvicorn api.main:app --host 0.0.0.0 --port $PORT`
     (runs Alembic migrations to head on every deploy, then serves).
   - `numReplicas = 1` — **do not change this.** 🔴 The in-process APScheduler makes a
     second replica double every sync and every email. If you ever need to scale the web
     tier, extract the scheduler into its own single-instance worker service first.
   - `healthcheckPath = /api/health`.
3. **psycopg must be installed for Postgres.** The `postgres` extra isn't installed by
   `pip install .` alone. In Railway → service → **Settings → Build**, set a custom
   **Install Command**:
   ```
   pip install '.[postgres]'
   ```
   (Without this the app boots but can't open a Postgres connection.)
4. Don't set the domain yet — do env + Postgres first (§2, then §4).

## 2. Postgres

1. In the Railway project: **New → Database → PostgreSQL**. Railway provisions it and
   exposes `DATABASE_URL` (form `postgresql://user:pass@host:port/db`).
2. 🔴 HUMAN — **scheme transform (easy to miss):** this app uses **psycopg 3**, so the
   URL scheme must be `postgresql+psycopg://`, not bare `postgresql://`. Copy Railway's
   `DATABASE_URL` and change only the scheme.
3. Enable **automated daily backups** on the Postgres service — 🔴 HUMAN: `briefs` and
   `players_archive` are irreplaceable (pre-season analysis depends on the archive).

## 3. Environment variables (Railway service → Variables)

Set these. The left column is the spec §5 name; the middle is the **actual env var**
this code reads (see `docs/layer5` §8 drift notes for why they differ).

| spec §5 name | actual env var | value | notes |
|---|---|---|---|
| `DATABASE_URL` | `FPL_DATABASE_URL` | the `postgresql+psycopg://…` URL from §2.2 | **name + scheme both differ** |
| — | `RUN_SCHEDULER` | `true` | starts the in-process sync scheduler (single replica) |
| `FRONTEND_ORIGIN` | `FRONTEND_ORIGIN` | `https://example.com` | CORS allow-list; exact origin, **no trailing slash** — set after §5 |
| `ADMIN_TOKEN` | `ADMIN_TOKEN` | the secret from §0 | bearer for `/api/admin/*` |
| `RESEND_API_KEY` | `RESEND_API_KEY` | from Resend (§6) | omit to disable welcome email (no-op) |
| `SEND_FROM` | `SEND_FROM` | `FPL Analyst <briefs@example.com>` | must be on your Resend-verified domain |

**Not needed in Stage A** (Stage B only): `BRIEF_TOKEN_SECRET`, `PADDLE_*`,
`LLM_API_KEY`, `LLM_MODEL`, `PADDLE_PRICE_*`.

Redeploy the service after setting variables. The deploy runs `init-db` → migrations
apply to the fresh Postgres.

## 4. Vercel — frontend

1. New Project → import the same GitHub repo.
2. 🔴 HUMAN — **Root Directory = `web`** (the Next.js app is in `web/`, not repo root).
   Framework preset auto-detects as Next.js.
3. Environment variable: `NEXT_PUBLIC_API_URL = https://api.example.com`
   (set now; the API domain is wired in §5). For a first smoke test before DNS you may
   temporarily use the Railway-provided public URL.
4. Deploy.

## 5. Domains + DNS — 🔴 HUMAN (registrar + both dashboards)

1. **API domain:** Railway service → Settings → Networking → **Custom Domain** →
   `api.example.com`. Railway shows a CNAME target → add that CNAME at your registrar.
2. **Frontend domain:** Vercel project → Settings → Domains → add `example.com`
   (and `www` if you want) → add the A/CNAME records Vercel shows at your registrar.
3. Wait for DNS to propagate + certs to issue (minutes to a couple hours).
4. **Reconcile the cross-references** (both must be the final HTTPS hostnames):
   - Railway `FRONTEND_ORIGIN` = `https://example.com` → redeploy API.
   - Vercel `NEXT_PUBLIC_API_URL` = `https://api.example.com` → redeploy frontend.
   - 🔴 CORS is exact-match: origin scheme + host, no trailing slash. A mismatch here is
     the most common "the picker won't load" cause.

## 6. Resend — email (optional for launch)

1. Resend → **Domains** → add `example.com` → 🔴 HUMAN: add the SPF + DKIM DNS records
   it gives you at your registrar; wait for "Verified".
2. Create an API key → set `RESEND_API_KEY` on Railway.
3. Set `SEND_FROM` to an address on the verified domain (e.g. `briefs@example.com`).
4. Redeploy the API. (Skip this whole section to launch without email — welcome sends
   become no-ops and everything else works.)

## 7. First production sync

The scheduler runs one tick immediately on startup, so once the API is deployed with
`RUN_SCHEDULER=true` it will `sync_fpl()` against the **live** FPL API and populate the
DB on its own within a minute or two.

- To force it manually instead: Railway service → **Shell** → `python -m fpl sync`.
- 🔴 HUMAN judgement: pre-season, the live FPL API may still be serving last season's
  data until the official reset. `python -m fpl mode` (or `/api/health.mode`) tells you
  what the app derived. `PRESEASON` = landing shows the draft picker (correct for Stage A).

## 8. Smoke checklist (run after §7)

Against the live hostnames:

- [ ] `GET https://api.example.com/api/health` → 200, `mode` set, `last_sync` **not null**
      (proves the first sync landed). Point your uptime monitor here.
- [ ] `GET https://api.example.com/api/players` → JSON array (~hundreds of rows).
- [ ] `GET https://api.example.com/api/preview/latest` → 200 with `sections`.
- [ ] `https://example.com` loads; pre-season shows **"Rate my draft"**.
- [ ] Open the picker → players load (this is the client→API CORS path; if it hangs,
      re-check `FRONTEND_ORIGIN` §5.4).
- [ ] Pick 15 within budget → **Get my brief** → lands on `/brief/draft/…` with all
      sections rendered.
- [ ] `POST /api/admin/notes` with `Authorization: Bearer <ADMIN_TOKEN>` → `{"ok":true}`;
      without the header → 401.
- [ ] (If Resend enabled) submit an email in the picker/landing → welcome email arrives.

## 9. Notes / rollback

- **Rollback:** both platforms deploy from `main` and keep prior deploys — redeploy the
  previous build from the dashboard. DB migrations are additive; no destructive down-step
  is needed for a Stage A rollback.
- **Cost:** stub Writer = zero LLM spend. Briefs are cached (input-hash), so the rate
  limit (5 generations/IP/hr) plus the cache keep compute trivial.
- **Stage B, when you get there:** add the `subscriptions` + `webhook_events` tables and
  Paddle/token/paywall env vars, persist the generic preview singleton (flagged in
  `docs/layer5` §8), and flip behind `PAYWALL_ENABLED`. Start Paddle account verification
  now — provider verification latency is the only critical-path item you don't control.
