# FPL Analyst — Delivery Surface Spec (Layer 5)

Consumes: Layers 1–4 (pipeline, cache, writer, generic preview).
Produces: the public product — API, two-page frontend, email delivery, paid tier.
Core simplification that shapes everything here: **no user accounts in v1.** Identity = email + FPL team ID, captured at signup/checkout. Paid access is delivered via signed links in email, not logins.

---

## 1. FastAPI route map

All routes under `/api`. JSON in/out. CORS: allow the frontend origin only.

### 1.1 `POST /api/brief`
Request: `{ "team_id": int }` OR `{ "draft": { "player_ids": [15 ints], "captain_id": int|null }, "email": str|null }` (pre-season path).
Behavior: mode check → cache check (Layer 3 §4) → run graph on miss → persist.
Response 200: `{ "brief_id", "gameweek", "team_name", "sections": { "verdict": md, "transfer": md, "captaincy": md }, "locked_sections": ["bench_plan","chips","watchlist"], "cached": bool }`
— i.e. the FREE view. Full sections are never returned on this route.
Errors: 404 `{"error":"invalid_team_id"}`, 409 `{"error":"no_picks_yet","hint":"preseason_draft"}`, 503 `{"error":"fpl_api_down"}`, 429 on rate limit (below).
Rate limit: 5 brief generations per IP per hour (cache hits don't count). In-memory or Redis-less token bucket is fine at this scale.

### 1.2 `GET /api/brief/{team_id}/{gw}?token={jwt}`
- No/invalid token → free view (same shape as 1.1).
- Valid token → `"sections"` contains ALL sections, `"locked_sections": []`.
Token: JWT signed with `BRIEF_TOKEN_SECRET`, claims `{sub: subscription_id, team_id, exp: current_period_end}`. Minted only by the email send job and checkout success handler. Accepted tradeoff (documented): brief CONTENT isn't secret (FPL squads are public data); the paid product is generation + delivery + full analysis, so token leakage is a non-issue at this scale.

### 1.3 `GET /api/preview/latest` (and `/api/preview/{gw}`)
Returns the generic gameweek preview (Layer 4 §6): `{ "gameweek", "html_sections": {...}, "generated_at" }`. Public, cached hard (CDN-cacheable, `Cache-Control: public, max-age=3600`).

### 1.4 `GET /api/players`
Compact list for the pre-season draft picker: `[ {id, web_name, team_short, position, price} ]`. Served from DB, `max-age=3600`. (~700 rows, ~30KB gzipped — ship it whole; no search endpoint needed.)

### 1.5 `POST /api/signup`
`{ "email", "team_id": int|null, "source": str|null }` → upsert `email_signups` (tier='free'). Response 200 `{ "ok": true }`. Triggers welcome email (§3).

### 1.6 `POST /api/checkout`
`{ "email", "team_id", "plan": "monthly"|"season" }` → creates a Paddle checkout (server-side transaction/checkout link) with `custom_data = { fpl_team_id, email }` → `{ "checkout_url" }`. Frontend redirects.

### 1.7 `POST /api/webhooks/paddle`
See §4. Returns 200 fast; work is idempotent.

### 1.8 `GET /api/email/unsubscribe?token={jwt}`
One-click unsubscribe (required for bulk-sender deliverability). Sets `email_signups.tier='unsubscribed'`; paid users remain subscribed to the PRODUCT (billing) but stop receiving mail only if they confirm on the landing page this shows. Token: signed email claim.

### 1.9 Utility
- `GET /api/health` → sync freshness `{ last_sync, mode, next_deadline }` (also your uptime-monitor target).
- `POST /api/admin/notes` and `POST /api/admin/euro-weeks` — bearer `ADMIN_TOKEN` header; upsert rows in `team_notes` / `european_weeks`. No admin UI in v1: you call these from a local script/HTTP file. (Editing prod SQL by hand is the alternative; the endpoint exists so updates bump `updated_at` correctly for cache keys.)

### 1.10 New table: `subscriptions`
| field | type |
|---|---|
| id | serial PK |
| email | text |
| fpl_team_id | int |
| provider | text ('paddle') |
| provider_subscription_id | text unique |
| provider_customer_id | text |
| plan | text ('monthly','season') |
| status | text ('active','past_due','canceled') |
| current_period_end | timestamptz |
| created_at / updated_at | timestamptz |
Plus `webhook_events (event_id text PK, processed_at)` for idempotency.

## 2. Next.js frontend (two pages + one modal)

### 2.1 `/` — landing
- Hero: one-sentence promise, team-ID input, "Get my free brief" (per agreed mockup). Helper line: "Find your ID in your FPL points page URL."
- Pre-season mode (derived from `/api/health.mode`): input is replaced by "Rate my draft" button → draft picker modal: position-grouped searchable list from `/api/players`, 15 slots, budget counter (£100.0m), captain toggle, optional email field. Submits to `POST /api/brief` (draft path).
- Below fold: latest generic preview (from `/api/preview/latest`) rendered as a teaser + email capture ("The free weekly preview, in your inbox"), and a 3-line pricing blurb (monthly / season pass) that anchors the paid tier without a dedicated pricing page.
- Footer: contact email, privacy note, "not affiliated with the Premier League."

### 2.2 `/brief/[teamId]` (and `/brief/draft/[draftId]`)
- Renders sections from 1.1/1.2 response in the agreed report layout (header card: team name, rank, bank; then sections).
- Locked sections render as the lock card → CTA opens checkout (1.6) with email+team prefilled.
- `?token=` present → full view (this is what email links hit).
- Under the brief: email capture (free) with `team_id` attached, and a "Refresh my brief" button (POST again; cache/input-hash decides whether regeneration happens).
- `<meta>` OG tags: team name + "GW{n} scout report" — share-preview matters; briefs get pasted into group chats.
- Loading state: staged progress text ("Reading your squad… checking fixtures… writing your brief"), driven by optimistic timing, not real events (v1).

Stack: Next.js App Router, server components for preview fetch, plain fetch to the API, Tailwind. No auth libraries, no state management library, no analytics beyond a privacy-light counter (Plausible/Umami optional).

## 3. Email pipeline

**Provider: Resend** (solo-dev-friendly API, React Email templates, free tier covers launch; swapping providers later is a one-module change — isolate in `email/`). Domain setup required before launch: SPF + DKIM records for your domain, sending address like `briefs@yourdomain.com`, `List-Unsubscribe` header (one-click, §1.8) on every bulk send — Gmail/Yahoo bulk-sender rules make this non-optional.

Templates (React Email, rendered to HTML + plaintext alt):
1. **Weekly paid brief** — subject: `"GW{n}: {one-line verdict hook}"` (hook = first sentence of Verdict, truncated 60 chars). Body = full brief sections styled minimally (email-safe HTML, no images required), footer with manage/unsubscribe + link to web view with token.
2. **Weekly free preview** — subject: `"GW{n} preview: {trap-of-the-week player}"`. Body = generic preview + "get this for YOUR team" CTA → landing.
3. **Welcome** (on signup/checkout): what arrives when, one-time link to current brief.

Send job = final stage of the deadline+8h batch (Layer 3 §4), strictly after brief persistence + validation:
1. For each active subscription: mint token → send template 1. 
2. For each free signup (tier='free'): send template 2.
3. Throttle to provider rate limits; per-recipient failures logged to a `send_log` (email, gw, template, status) — retry failures once at +1h; no dead-letter complexity in v1.
Idempotency: `send_log` unique (email, gw, template) checked before send — the batch can be rerun safely.

## 4. Paddle webhooks

Endpoint 1.7. Verify `Paddle-Signature` (HMAC with `PADDLE_WEBHOOK_SECRET`) BEFORE parsing; reject non-verified with 401. Idempotency via `webhook_events`.

Events handled (Paddle Billing):
| event | action |
|---|---|
| `subscription.created` / `subscription.activated` | upsert `subscriptions` from payload: provider ids, plan (map price id → plan), status='active', `current_period_end`; read `custom_data.fpl_team_id` + email; upsert `email_signups` tier='paid'; send welcome email with token |
| `subscription.updated` | update status + `current_period_end` (renewals extend the period — this is what keeps emailed tokens valid) |
| `subscription.past_due` | status='past_due' — keep sending briefs for grace period (current period end still future); Paddle dunning handles retries |
| `subscription.canceled` | status='canceled'; delivery stops after `current_period_end` passes (do NOT cut off mid-period) |
| `transaction.completed` with one-time season-pass price id | upsert subscription-like row: plan='season', status='active', `current_period_end` = June 30 of season end year |
Everything else: log and 200. No refund handling logic — MoR owns refunds; a refund arrives as cancellation.

## 5. Deployment topology

**Railway** — one service, one **single replica** (hard requirement: APScheduler runs in-process; >1 replica = duplicate syncs and duplicate emails. If scale ever demands replicas, extract the scheduler first). Components: uvicorn (FastAPI) + APScheduler in the same process. Postgres: Railway Postgres (or Neon) — either fine; take automated daily backups (Railway setting) since `players_archive`/`briefs` are irreplaceable.
**Vercel** — Next.js frontend. `NEXT_PUBLIC_API_URL` → Railway service. Domain: `yourdomain.com` (Vercel) + `api.yourdomain.com` (Railway custom domain).

Env vars (Railway): `DATABASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `BRIEF_TOKEN_SECRET`, `ADMIN_TOKEN`, `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_PRICE_MONTHLY`, `PADDLE_PRICE_SEASON`, `RESEND_API_KEY`, `SEND_FROM`, `FRONTEND_ORIGIN` (CORS). Vercel: `NEXT_PUBLIC_API_URL`.

Automated: deploys from GitHub main (both platforms); sync schedule; pre-generation + send batch; webhook processing; DB backups.
Manual (the entire human surface, by design): `team_notes` weekly, `european_weeks` monthly, heuristic config tuning, prompt version bumps, Paddle product/price setup (one-time), DNS/domain (one-time).

## 6. Pre-season launch cut (two-stage launch)

**Stage A — pre-season (ship this first, target: this month):**
- Live: landing in pre-season mode, draft picker, `POST /api/brief` draft path, gameweek-0 briefs (FULL content, nothing locked — there is no paywall yet), email capture, generic pre-season preview ("The opening-run report"), health, admin endpoints.
- Stubbed/absent: checkout, webhooks, subscriptions table unused, weekly send job idles (no gameweeks), tokens unminted. Welcome email only.
- Rationale: pre-season briefs are the marketing artifact for Reddit draft threads; giving the full report free costs pennies (cache) and builds the email list the paywall will convert. Rate limit is the only abuse guard needed.
- Stage A definition of done: a stranger can go from Reddit link → draft entry → full gw-0 brief → email signup without you touching anything.

**Stage B — by GW1 deadline (the hard external date):**
- Flip: team-ID input replaces draft picker as primary; paywall gating on (locked sections render); Paddle checkout + webhooks live (Paddle account verification started NOW — §7 note); weekly batch + sends armed; free preview email begins.
- The Stage A email list gets one launch email: "the season's here — your GW1 brief is ready, here's what the full version adds."

**Explicitly LATER (not v1, resist scope creep):** login/accounts, multi-team support, brief history page, chip planner, price-change predictions (that's what `player_history` accrues for), LLM Strategist, mini-league features, Indian-rail payment tier, admin UI.

## 7. Sequencing note for the implementer
Build order: routes 1.1–1.5 + frontend pages against the stub Writer → email module + welcome flow → Stage A ship → Paddle sandbox integration (checkout + webhooks end-to-end in sandbox) → templates 1–2 → Stage B flip behind a single `PAYWALL_ENABLED` env flag. Start Paddle account onboarding/verification in parallel with Stage A — provider verification latency is the only item on the critical path you don't control.

Definition of done (Layer 5 overall): Stage A live on real domains; a sandbox Paddle subscription produces a webhook → subscriptions row → tokenized full-view link → received email, end-to-end; `PAYWALL_ENABLED=true` flips Stage B with no code change.

## 8. Implementation drift notes

Dated corrections/extensions from the Stage A build. The spec above is the intent;
these record where the code diverges and why.

**2026-07-11 — Stage A backend (`api/`) built; Stage B untouched.**

- **Env-name mapping.** Spec §5 lists `DATABASE_URL`; the DB engine is owned by Layer 1 (`fpl/config.py`, env `FPL_DATABASE_URL`) — so **`DATABASE_URL` in this spec = `FPL_DATABASE_URL` in prod**. The delivery-only secrets/knobs (`ADMIN_TOKEN`, `RESEND_API_KEY`, `SEND_FROM`, `FRONTEND_ORIGIN`, `BRIEF_TOKEN_SECRET`) are read under their bare spec names by `api/config.py`.
- **`RUN_SCHEDULER` gate.** §5 puts uvicorn + APScheduler in one process. Startup of the in-process scheduler is gated on a new env flag `RUN_SCHEDULER` (default **off**) so tests and the local demo never spawn it; Railway sets `RUN_SCHEDULER=true`. The single-replica invariant is documented in `railway.toml` (`numReplicas = 1`) and `api/main.py`.
- **New route `GET /api/brief/draft/{draft_id}`** (approved gap in §1.2). §1.2 is season-only (`{team_id}/{gw}`), but the Stage A draft flow routes the frontend on the draft UUID (`/brief/draft/[draftId]`) and needs a GET for refresh / direct-link / OG share. A nonexistent `draft_id` returns **404 `{"error":"invalid_draft_id"}`** (clean user-facing shape, never 500). Same non-secret-content tradeoff as §1.2 team briefs — documented, not gated (FPL squads are public; the product is generation + delivery + full analysis).
- **§1.1 response additions.** Added `draft_id` (frontend routing for the draft path) and `overall_rank` + `bank` (the §2.2 header card needs them; the documented shape omitted them). Additive only.
- **Stage A returns full content, nothing locked.** Per §6 Stage A there is no paywall: `POST /api/brief` / the draft GET return **all** sections with `locked_sections: []`. No `PAYWALL_ENABLED`, no section gating — that is the Stage B flip. The response shape already carries `locked_sections` so Stage B is data-driven (no route change).
- **`email/` → `api/mail.py`.** A top-level `email/` package shadows the stdlib `email`. The provider stays isolated to one module (swap = one file). Stage A ships only the **Welcome** template as inline email-safe HTML; React Email + the weekly paid/free templates are Stage B. An empty `RESEND_API_KEY` makes the send a logged no-op (offline tests / local dev).
- **Preview response.** §1.3 names `html_sections`; the generic preview renders **markdown** (the frontend renders it), so the field is `sections` for parity with the brief routes. `generated_at` is request-time (Stage A renders live behind `Cache-Control: max-age=3600`; no persisted generic singleton yet).
- **`graph.cache.get_or_generate` gained a `before_generate` hook.** A no-arg callback fired **only on a genuine cache miss**, right before the graph runs (never on cache hits or the error path). It is the per-IP rate-limit insertion point, which is how "5 generations/IP/hour, cache hits don't count" (§1.1) is enforced without duplicating the cache logic in the route.
- **Fixed a merged Layer 4 bug.** `writer/generic.py` imported `DIFFERENTIAL_MAX_OWNERSHIP` / `TEMPLATE_MIN_OWNERSHIP` from `writer.config`, where they don't exist (the module was never imported by a test, so the broken import shipped green in f96ad2d). They now come from `graph.config` — the single source shared with `market_scout`.
- **`GET /api/brief/{team_id}/{gw}` is a pure read in Stage A.** No season briefs are generated and no tokens are minted this stage, so it only serves an already-persisted brief (free view); header fields are null until the Stage B send job supplies squad context. Token gating / full-view unlock is Stage B.
