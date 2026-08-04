# FPL Analyst — Delivery Surface Spec (Layer 5)

Consumes: Layers 1–4 (pipeline, cache, writer, generic preview).
Produces: the public product — API, two-page frontend, email delivery, paid tier.
Core simplification that shapes everything here: **no user accounts in v1.** Identity = email + FPL team ID, captured at signup/checkout. Paid access is delivered via signed links in email, not logins.

> **SUPERSEDED 2026-08-04 — accounts and auth are built.** The paragraph above is kept
> because it explains why the rest of this spec is shaped the way it is, but it no longer
> describes the code. Identity is now an `accounts` row; paid access resolves
> session → account → subscription; the emailed link resolves to an account and is
> exchanged for a session rather than granting access on its own. **The free brief is
> still open and takes no login** — that constraint is the one thing the accounts build
> was not allowed to change. See the §8 drift note of 2026-08-04 for the full correction.

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
- `POST /api/admin/notes` and `POST /api/admin/euro-weeks` — bearer `ADMIN_TOKEN` header; upsert rows in `team_notes` / `european_weeks`. ~~No admin UI in v1: you call these from a local script/HTTP file.~~ (Editing prod SQL by hand is the alternative; the endpoint exists so updates bump `updated_at` correctly for cache keys.)

> **DRIFT 2026-07-30 — an admin UI shipped after all; both write endpoints gained a
> skip-if-identical guard.**
>
> The curl-only workflow held for two curated tables edited weekly by hand, and stopped
> holding once the volume was twenty notes a week. `/admin` now exists inside the same
> Next.js app (four pages, `ADMIN_TOKEN` in `localStorage`, never linked, `noindex`), backed
> by a read-only query module `api/admin.py` and these new token-gated reads:
> `GET /api/admin/{overview,notes,euro-weeks,players,fixtures/{team_short},top}`, plus
> `POST /api/admin/euro-weeks/replace` for whole-grid saves. Full description in
> `docs/STATUS.md` §6.
>
> **The parenthetical above had it exactly backwards, and that is the load-bearing part
> of this note.** "The endpoint exists so updates bump `updated_at` correctly for cache
> keys" — but `updated_at` bumping on *every* call is the bug, not the feature, the moment
> a form is doing the calling: a form re-submits its contents whether or not anything was
> typed, so an unchanged save invalidated every cached brief. `upsert_note`,
> `upsert_euro_week` and `replace_euro_weeks` now compare before writing and skip if
> identical, returning `changed` so the caller can tell a save from a no-op. A curl caller
> sending genuinely new content is unaffected.
>
> Still NOT here, still deliberate: no accounts, no sessions, no login page. `/admin` is
> the existing operator token behind one prompt — §5's "no auth" rule is intact.

> **DRIFT 2026-07-31 — the admin surface grew a read-only diagnostic wing.**
>
> Five more Bearer-gated **GET** routes, none of which write anything:
> `GET /api/admin/flag-queue`, `/inspect/briefs`, `/inspect/draft/{draft_id}`,
> `/inspect/team/{team_id}/{gw}`, `/inspect/player/{player_id}`. Queries live in
> `api/inspect.py`; the `/admin/inspect` page renders them. Full description in
> `docs/STATUS.md` §6.
>
> **Three things about this that constrain future edits:**
>
> 1. **It runs the pipeline but writes nothing — not even a cache row.** `inspect_draft`
>    calls `graph.graph.run_brief` directly rather than `graph.cache.get_or_generate`,
>    because the latter persists a `briefs` row on a miss. An inspector that mutated the
>    cache would change the thing it claims to be observing.
>    `tests/test_trace.py::test_inspect_draft_writes_nothing` pins it.
> 2. **It never calls the LLM.** The payload preview goes through the real
>    `writer.serializer` and stops there. `api/inspect.py` imports the serializer and
>    nothing else from the LLM path; a test breaks `AnthropicWriter.generate` to keep the
>    page zero-spend as the §8 prompt work begins.
> 3. **The free/paid comparison is not a mock.** It calls `graph.sections.gate_sections`
>    with `paywall=True` — the same function and the same allow-list Stage B will use —
>    and renders both sides through the real `BriefView`. That makes the page a standing
>    pre-flight check on the paywall: if a section gates wrongly, it is visible here
>    before `PAYWALL_ENABLED` flips, not after a customer finds it. Do not "simplify" this
>    into a static section list; the whole value is that it exercises the real gate.
>
> Stage A has no FPL client, so the `team_id` path cannot re-run the pipeline for a season
> squad. It serves the persisted brief for the free/paid view and says why the trace is
> unavailable, rather than presenting a fabricated one.

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

**Explicitly LATER (not v1, resist scope creep):** ~~login/accounts~~ (built 2026-08-04 —
see the §8 drift note; magic link + Google, gating the PAID tier only, with the free brief
still open and login-free), multi-team support, brief history page, chip planner, price-change predictions (that's what `player_history` accrues for), LLM Strategist, mini-league features, Indian-rail payment tier, ~~admin UI~~ (built 2026-07-30 — see the §1.9 drift note; it is an operator console over the endpoints that already existed, not a new product surface, and it added no accounts).

## 7. Sequencing note for the implementer
Build order: routes 1.1–1.5 + frontend pages against the stub Writer → email module + welcome flow → Stage A ship → Paddle sandbox integration (checkout + webhooks end-to-end in sandbox) → templates 1–2 → Stage B flip behind a single `PAYWALL_ENABLED` env flag. Start Paddle account onboarding/verification in parallel with Stage A — provider verification latency is the only item on the critical path you don't control.

Definition of done (Layer 5 overall): Stage A live on real domains; a sandbox Paddle subscription produces a webhook → subscriptions row → tokenized full-view link → received email, end-to-end; `PAYWALL_ENABLED=true` flips Stage B with no code change.

## 8. Implementation drift notes

Dated corrections/extensions from the Stage A build. The spec above is the intent;
these record where the code diverges and why.

**2026-08-04 — ACCOUNTS AND AUTH BUILT. §5's "no user accounts in v1" is SUPERSEDED.**

Read this before §5. The line "Core simplification that shapes everything here: **no
user accounts in v1.** Identity = email + FPL team ID … Paid access is delivered via
signed links in email, not logins" is **no longer what the code does**, and the entry in
§6's "Explicitly LATER" list (`login/accounts`) has moved to *built*. The spec text is
left in place rather than rewritten because it explains why the earlier design looked the
way it did; this note is the correction that overrides it.

**What did NOT change, and was the hard constraint on the whole build: the free brief is
still open.** No login stands in front of `POST /api/brief`, the draft picker,
`/api/preview/*`, `/api/players` or `/api/signup`. Anonymous readers get a 200 with paid
sections locked, exactly as they did on 2026-08-01 — never a 401, never a redirect to
`/login`. `tests/test_accounts.py` §1 asserts this first, and
`test_no_free_route_answers_401_without_a_session` walks the public surface so a future
route is covered the day it lands. A login anywhere upstream of the paywall is a product
kill, not a hardening.

- **Tables (migration `e5f6a7b8c9d0`, additive only).** `accounts`, `sessions`,
  `magic_link_tokens`, plus a nullable `subscriptions.account_id`. No backfill: the
  webhook links a subscription on its next delivery, and an unlinked subscription is
  simply not entitled through any session, which is the direction that fails safe.
  - **`accounts.email` is TEXT + UNIQUE, not `citext`** — a deliberate deviation from the
    build request. Case-insensitivity is normalisation-at-the-boundary
    (`accounts.normalize_email`), which is what the codebase already does with addresses
    everywhere else. `citext` needs a Postgres EXTENSION a managed database can refuse,
    and it does not exist under SQLite, where the whole suite runs — so the guarantee
    would have been untested exactly where it is asserted.
    `test_email_case_is_normalised_on_every_path` and
    `test_one_address_in_any_casing_is_one_account` hold it instead.
  - **Only hashes are stored** for session and magic-link tokens. A dump of either table
    yields nothing presentable. Plain SHA-256, not bcrypt: these hash 32 random bytes, not
    a human-chosen password, and a slow hash on the session-lookup path would be a
    self-inflicted DoS on every request.

- **Entitlement now resolves session -> account -> subscription.** `is_entitled` and
  `ENTITLED_STATUSES` are **untouched** — the allow-list is still the whole rule and an
  unknown Paddle status still degrades to free. What changed is only how the subscription
  is *found*: `billing.entitled_for_account` reads `subscriptions.account_id`, never an
  address match. `tests/test_accounts.py::test_an_unknown_subscription_status_degrades_to_free`
  re-asserts the allow-list through the new path on purpose, because the accounts layer is
  a new place that could have started answering "yes".
  - **Team scoping survived.** An entitled account opens the brief for the team its
    subscription names. Dropping the scope would have been the smaller diff and would have
    quietly turned one subscription into a service for all 11 million FPL teams.

- **The emailed full-view link is no longer a standing credential.** `?token=` still
  works, but it now resolves to the ACCOUNT that owns the subscription in its `sub` claim
  and is **exchanged for a session** on first use. A token whose subscription has no
  linked account opens nothing — which is what stops a forwarded link working for a
  stranger. The claim set is unchanged (`{sub, team_id, exp, iat}`), so
  `api/tokens.py`'s blast-radius docstring still holds; `claims_if_valid` only reads what
  was already there.

- **Terms are a gate on PAID content, never on the free brief.** `routes._paid_account_id`
  withholds the paid view when `terms_accepted_at` is NULL or its version is stale, and
  `GET /api/auth/me` reports `entitled` and `terms_required` separately so the UI can say
  "you have a subscription, accept the terms" rather than showing a paying customer a buy
  button. A NULL acceptance reads as NOT accepted — treating absence as consent is how a
  consent record becomes decorative.
  - **`POST /api/checkout` gained `accept_terms`, and that is load-bearing.** Checkout is
    the third account-creation path alongside magic link and Google. Without it, a customer
    provisioned by the webhook has no recorded consent and **their own emailed brief link
    opens the free view** — which reads as a bug and is the exact failure this field
    prevents. It carries no pricing authority; `CheckoutRequest` still has no price,
    amount, currency or price-id field (audit §D3 unchanged).

- **Session security.** httpOnly + Secure + SameSite=lax, all three, with Secure defaulting
  ON (only plain-http local dev turns it off). `SameSite=strict` is specifically wrong here:
  it drops the cookie on the magic-link click, which arrives as a cross-site top-level
  navigation from a mail client. Fixation is closed structurally — no code path turns a
  client-supplied value into a session row — and `routes._login` additionally deletes the
  arriving cookie's row before minting a new one. Logout deletes the ROW, not just the
  cookie; clearing the cookie alone is a logout that logs nobody out.
  - **CORS gained `allow_credentials=True`.** Safe only because `allow_origins` is a single
    exact origin from config and never `*`. If `FRONTEND_ORIGIN` ever becomes a list or a
    regex, re-derive that decision.
  - **OAuth `state`**: a random nonce in a short-lived httpOnly cookie, compared with
    `compare_digest`, both halves required non-empty (a missing cookie and a missing param
    would otherwise compare equal as `"" == ""`). The post-login target travels in `state`,
    not the cookie, because a cookie value containing `/` gets quoted by the server and
    arrives back quoted — a CSRF check that fails for the wrong reason is one somebody
    eventually "fixes" by loosening.
  - **Open redirect**: `accounts.safe_redirect` is an allow-list of shapes (a path, not
    `//`, not `/\`, no CR/LF), same reasoning as `FREE_SECTIONS`.

- **Rate limits on auth are DB-backed**, all three, because `POST /api/auth/request-link`
  mails an address the caller names. Two axes, and the second is not redundant: per-IP stops
  one host enumerating many addresses, per-address stops many hosts mailbombing one victim
  (a distributed sender is under the per-IP cap on every single request). The per-address
  bucket key is a **hash** — `rate_hits.bucket` is a table column, and a table of who tried
  to sign in is a mailing list.

- **No address reaches a log line** from the auth surface (audit §D4.3 applied without
  exception; `test_no_address_reaches_a_log_line` walks the magic link, the webhook and the
  Google callback together). Addresses ARE shown in full on `/admin/accounts`: the line is
  storage-vs-log, and support cannot answer "why didn't my brief arrive" against hashes.

- **Delete is one cascade behind two doors.** The mailed link (`POST /api/email/delete`,
  by address) and the signed-in button (`POST /api/account/delete`, by session) both end in
  `service._purge_account`, so "delete my data" means the same thing whichever one you
  found. Account, sessions, magic links, signup row and send history go; subscription rows
  survive **unlinked and anonymised**, because they are a financial record. The cascade is
  written out in Python rather than left to `ON DELETE`, because SQLite does not enforce FK
  actions without `PRAGMA foreign_keys=ON` and the whole suite runs on SQLite.
  - **Deleting an account does NOT cancel a Paddle subscription.** Nothing here can reach
    into a payment provider and stop a card being charged. `subscription_cancel_required`
    is returned for exactly this reason and the UI states it *before* the click. Pretending
    otherwise in a return value would be the most expensive lie in this codebase.
  - **Unsubscribing from mail still costs no billing access.** Re-asserted now that a
    session exists (`test_unsubscribing_from_mail_does_not_cost_billing_access`).

- **What is NOT built.** No password auth (there is no password), no X/Twitter provider
  (explicitly out of scope), no account-to-account subscription transfer, no admin write or
  impersonation path — `/admin/accounts` is read-only, and that is a ceiling on what the
  admin token is worth. Google has never been exercised against Google: the token endpoint
  is injected in tests and no `GOOGLE_CLIENT_ID` exists yet, so the same caveat that applies
  to Paddle applies here. **The id_token signature is not verified** — it is read from the
  body of a direct, TLS-and-client-secret-authenticated POST to `oauth2.googleapis.com`,
  which OIDC Core §3.1.3.7 permits for the code flow; `aud`, `iss` and `email_verified` are
  all still checked. If that token ever starts arriving from anything the browser touched,
  `_decode_id_token_payload` becomes a vulnerability and needs real JWKS verification.

**2026-08-01 — Stage B built: billing, tokens, the paywall flip and the weekly send.**

Everything in this note ships **off**: `PAYWALL_ENABLED=false`, `SEND_ENABLED=false`, no
Paddle credentials. The code exists and is tested (`tests/test_stage_b.py`, 104 tests); the
switches are separate from the build on purpose.

- **Tables (§1.10).** `subscriptions`, `webhook_events`, `send_log`, `rate_hits`, plus
  `email_signups.unsubscribed_at`. Migration `d4e5f6a7b8c9`, additive only (CREATE TABLE /
  ADD COLUMN nullable), so it is safe against a populated DB.
  - **`subscriptions.status` is free text, never an enum.** Paddle can invent a status
    tomorrow; a DB enum would make the webhook 500 on it, and a 500 makes Paddle retry
    forever. Storing the unknown string and letting the allow-list refuse it degrades to
    free, which is the correct failure.
  - **`webhook_events.event_id` is the PRIMARY KEY.** That *is* the idempotency mechanism —
    the insert happens in the same transaction as the state change, so a concurrent
    redelivery loses on the PK rather than on a SELECT-then-INSERT race the code would have
    to win.

- **§1.7 webhook — verify, then parse, then apply once (audit §D1).** The route signature is
  `async def paddle_webhook(request: Request, ...)`, **not** a pydantic body model. That is
  the verify-before-parse requirement expressed as code: declaring a model would have FastAPI
  deserialize attacker-controlled JSON before the handler runs.
  `tests/test_stage_b.py::test_signature_is_verified_before_the_body_is_parsed` asserts the
  call ORDER, not the 401, because a handler that parsed first and checked afterwards would
  still return 401.
  - Rejection is **401 with no detail**. Telling a caller whether the timestamp or the HMAC
    was wrong is free help for the next attempt.
  - An empty `PADDLE_WEBHOOK_SECRET` rejects everything (fail closed), the same rule as
    `ADMIN_TOKEN`. `api/main._check_stage_b_config` logs a loud warning when the paywall is on
    and the secret is empty, because in that state nothing can ever unlock.
  - **Plan and price are read only from the verified payload.** The client sends a plan NAME;
    `billing.price_id_for_plan` resolves it server-side. There is no price, amount or price-id
    field on `CheckoutRequest` — the tamper-proofing is a schema, not a validation rule
    someone can forget to run.

- **Entitlement is an ALLOW-LIST**, mirroring the `FREE_SECTIONS` property that
  `docs/CLAUDE.md` already makes load-bearing:
  `ENTITLED_STATUSES = {"active", "trialing"}`. Any other status — `past_due`, `paused`,
  `"ACTIVE"`, `"active "`, or a status Paddle ships next year — degrades to free.
  - **`canceled` is the one exception, and it grants MORE, not less.** A refund or chargeback
    arrives as a cancellation; delivery continues to `current_period_end` and never cuts off
    mid-period. A canceled subscription with no period, or a period already past, is not
    entitled.

- **§1.2 full view.** `?token=` is accepted again (the 2026-07-30 note above, which says it was
  removed, is superseded). Unlocking needs **both** a valid token naming that team **and** a
  live entitlement check against `subscriptions` — the token is not the authority, it is a
  pointer at a row that is re-read on every request. Revoking a subscription therefore takes
  effect immediately, without invalidating anything.

- **`api/tokens.py` is stdlib HS256 with no algorithm dispatch.** There is no `alg` lookup
  table to confuse: the verifier recomputes an HS256 MAC and compares it with
  `hmac.compare_digest`, so `alg: none` and `alg: RS256` are unrepresentable rather than
  merely rejected. `exp` is REQUIRED (a token without one does not verify).
  `BRIEF_TOKEN_SECRET_PREV` gives a rotation overlap so links already sitting in inboxes keep
  working. Minimum 32 characters, enforced at **startup** — with the paywall or the send job
  on, a weak secret makes the app refuse to boot.
  - **Leaked-token blast radius.** A leaked token grants read access to the paid sections of
    **one team's brief**, for that team only, until `exp` — which equals
    `current_period_end`. It is not a session: it carries no identity, cannot be presented to
    any other route, cannot be extended or refreshed, and confers nothing over billing, mail
    consent or the admin surface. It cannot be revoked individually; the containments are its
    short life, its single-team scope, and the live entitlement re-check (cancel the
    subscription and every token for it stops working at once). Rotating
    `BRIEF_TOKEN_SECRET` invalidates every outstanding token — the blunt instrument, for a
    secret compromise rather than a single leak. **Do not add claims to this token.** Its
    safety comes from how little it says.

- **§3 send batch (`api/send.py`).** Per-recipient failure isolation (a broad `except
  Exception` around one delivery IS the boundary — a narrower one would let an unexpected
  type end the batch), idempotent via a unique `(email, gameweek, template)` claim taken
  **before** the provider call and committed on its own, one retry at +1h, a whole-batch
  ceiling, both templates with plaintext alternatives, and RFC 8058 one-click
  `List-Unsubscribe` on every send. **Addresses are hashed in every log line**
  (`mail.mask` → `sha256(email)[:12]@domain`).
  - **Two unsubscribe URLs, deliberately.** The `List-Unsubscribe` **header** points at
    `PUBLIC_API_URL` + `POST /api/email/unsubscribe` because a mail client POSTs it with no
    human, no JavaScript and no cookie; the **visible footer link** points at
    `PUBLIC_WEB_URL` + `/unsubscribe`, a page that acts on load and then offers undo and
    delete. The page is the courtesy path — it could 500 and unsubscribing would still work.

- **§1.8 unsubscribe is a TOMBSTONE (`email_signups.unsubscribed_at`), not `tier`.** The spec
  writes `tier = 'unsubscribed'`, which conflates mail consent with billing state on a
  billing-adjacent column. A paying customer who unsubscribes from email must keep their
  subscription; that is a hard requirement, so consent got its own column. `delete` is a
  separate action that removes the signup row and **anonymises** — not deletes — subscription
  rows, because billing records have to be retained.

- **§6 paywall flip.** `PAYWALL_ENABLED` is read by `service.paywall_on()` and gates the
  existing `graph.sections.gate_sections` allow-list. There is no second gating
  implementation, and `tests/test_stage_b.py::test_the_flip_uses_the_existing_allow_list_and_
  not_a_second_implementation` asserts that structurally.
  The former `service.PAYWALL` constant was **removed** rather than kept as an alias: a
  familiar name that silently reads a stale value is worse than no name.

- **Persisted generic preview.** The Stage A TODO below is closed:
  `service.persist_generic_preview()` writes the singleton the free template mails from
  (unique index `uq_brief_generic`, migration `b2c3d4e5f6a7`), so every free recipient gets
  the *same* artifact and a re-run refreshes rather than duplicates.

- **Rate limiting is now DB-backed for anything that costs money** (audit §C2). The in-process
  dict resets on every Render wake and is per-process; `rate_hits` survives both. Three layers
  on a generation: per-IP in-memory (cheap), per-IP durable, then a **global** circuit-breaker
  bucket (`GLOBAL_GENERATION_LIMIT`) that caps the whole service's bill if the per-IP limit
  has a bug. Old windows are swept on `POST /api/admin/sync`.

- **What is NOT built.** The LLM writer is still wired to nothing (gated on prompt iteration,
  layer4 §8) — every brief a paying customer would receive is still the deterministic stub.
  Double opt-in for signups is still open (audit priority 2). No Paddle account exists, so the
  live checkout URL and a real webhook delivery have never been exercised against Paddle
  itself; the signature scheme is implemented to spec and tested with locally computed
  signatures.

**2026-07-30 — `POST /api/brief` now validates the squad server-side.**

- The draft path accepted whatever squad it was given. Position caps, the £100.0m budget and the 3-per-club rule existed only in the picker, which is a courtesy to the user and not a control — `curl` bypassed all of them, and a nonsense squad produced a nonsense brief on a **public, unauthenticated** endpoint.
- `service.validate_draft()` now re-checks every rule against the real `players` rows and returns **400** with a specific code: `squad_size`, `duplicate_players`, `unknown_players`, `position_counts`, `budget_exceeded`, `club_over_cap`, `captain_not_in_squad`. Detail shape is the existing `{"error": code, "message": "..."}`. Validation runs **before** persistence, so a rejected squad leaves no `draft_squads` row and no email signup behind.
- `captain_id` stays optional — the Strategist picks its own captain, so absent is not invalid. Only a captain *outside* the 15 is rejected.
- The exact-15 check moved out of the pydantic model (`min_length=15`) and into the validator, so squad-size errors arrive as a 400 with a code alongside every other squad rule instead of a lone 422. The model keeps `max_length=100` as a payload bound.
- **The numbers now live in exactly one place**: `MAX_PER_CLUB`, `SQUAD_SIZE`, `POSITION_QUOTA`, `SQUAD_BUDGET` in `graph/config.py`, imported by MarketScout and the validator and mirrored once in `web/lib/api.ts` (asserted by `tests/test_api.py::test_the_club_cap_is_one_shared_constant`). The picker imports them from that mirror; no component re-types a literal.
- **Picker UI**: the `+` button is disabled at the club cap with the reason inline ("3 max from ARS"), matching how the over-budget reason already surfaces, and the desktop rail carries a per-club tally. A 400 from the server is now shown as its own message rather than as "the writing step didn't come back" — reaching one means the picker and the validator disagree.
- **Picker sort order**: within each position group, rows are sorted by **price descending**. `/api/players` returns element-id order and FPL assigns ids club-by-club alphabetically, so the old order was effectively "Arsenal first" and the per-group 60-row cap then truncated mid-alphabet — every recognisable player was out of reach without a search.
- **`cached` is no longer rendered.** `BriefView` showed "From cache" in the meta line. Whether a brief came from the cache is an operational detail; the field stays on the response for logs and DEBUG.

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
- **Stage B TODO — persist the generic preview.** `GET /api/preview` renders the generic stub **live** on each request (behind `Cache-Control`), with no persisted `briefs` singleton row. The Stage B weekly send job (template 2, §3) needs a persisted preview to email — so persisting the generic singleton (the relaxed-CHECK row from migration `b2c3d4e5f6a7`) is Stage B scope, flagged here so it isn't rediscovered mid-batch.

**2026-07-15 — Free-tier deployment topology is the Stage A launch configuration.**

Railway now requires a paid plan, so §5's topology is **deferred, not abandoned**. The launch
config is Render (free) + Neon (free) + GitHub Actions cron + Vercel + Resend, documented
step-by-step in `docs/deploy-stage-a-free.md`. `docs/deploy-stage-a.md` (Railway) is preserved
verbatim as the upgrade path — the free runbook's §9 maps each paid step to the pain it fixes.
The §5 spec above still describes the *intended* topology; this note records what launch
actually runs on.

- **The scheduler moved out of the process — the one architecturally load-bearing change.**
  §5 (and `fpl/scheduler.py`) put APScheduler in-process on a single replica. A Render free
  service **sleeps after ~15 min idle**, so an in-process scheduler cannot survive between
  requests. Sync therefore becomes an external cron: `.github/workflows/sync.yml` calls the
  new `POST /api/admin/sync`. `RUN_SCHEDULER` stays **default-off** in this mode (it is not
  set on Render at all) — so the free topology is `RUN_SCHEDULER` unset + external cron, and
  the paid topology is `RUN_SCHEDULER=true` + no cron. **Never both:** two sync drivers on one
  DB is the duplicate-sync failure the single-replica invariant exists to prevent. The
  invariant itself is *unchanged and still holds* — with the scheduler off, the "one replica"
  rule is no longer what protects against double-syncing; the cron's `concurrency: fpl-sync`
  group and the single-cron-source rule are. Restoring the paid path is config-only
  (`RUN_SCHEDULER=true`, drop the workflow), never a code change.
- **New route `POST /api/admin/sync`** (additive to §1.9's admin surface). Bearer `ADMIN_TOKEN`,
  same `_require_admin` guard as the other admin endpoints; calls the existing `sync_fpl()`
  through `service.run_sync()` and returns `SyncResult` as JSON so the cron log shows what
  landed (`mode`, `players_updated`, `ep_rows`, `rollover`, `duration_s`). It runs the sync
  **inline** — a slow (~minutes) request by design, since the caller is a cron, not a browser.
  `FPLError` (incl. `SyncAborted`) maps to **503 `{"error":"sync_aborted"}`** rather than a
  500, so the workflow fails loudly; abort-or-nothing is untouched (a failed fetch leaves the
  DB exactly as it was). The route is safe to keep in the paid topology as a manual
  "sync now" — it was previously Railway's shell, which the Render free tier doesn't offer.
- **Sync cadence downgraded to flat twice-daily.** Layer 1 §3 wants daily (pre-season) →
  twice-daily (baseline) → **3-hourly inside deadline−48h**; GitHub cron can't derive the
  deadline window from the DB. Twice-daily *exceeds* what pre-season needs (§3 wants daily
  there), so Stage A is unaffected; it becomes wrong at the GW1 deadline, which is the
  Stage B / paid-instance boundary anyway. `fpl/scheduler.py` retains the full deadline-aware
  implementation — nothing was deleted, and flipping `RUN_SCHEDULER=true` restores it.
- **No custom domain at launch.** §5 assumes `yourdomain.com` + `api.yourdomain.com`. Launch
  runs on `*.vercel.app` + `*.onrender.com`; `FRONTEND_ORIGIN` must be the exact Vercel
  production origin. Consequence: Vercel **preview deployments get distinct hostnames and will
  fail CORS** against the live API (single-origin allow-list, §5) — expected, not a bug.
- **Backups are a genuine gap, not a deferral.** §5 says take automated daily backups because
  `players_archive`/`briefs` are irreplaceable. Neon free has no scheduled backups (~24h PITR
  only). Accepted for Stage A because the DB is rebuildable from a sync — **but only until the
  first season rollover archives last season**, after which it is not. Flagged in
  `deploy-stage-a-free.md` §2.5 and §9 as the hard deadline for the paid upgrade.
- **Email is degraded without a domain.** Resend's shared `resend.dev` test sender only
  delivers to your own verified address, so real signups can't receive the welcome mail until
  a domain is verified. The unkeyed no-op path (`RESEND_API_KEY` empty) remains the honest
  free-launch default.

**2026-07-28 — Stage A frontend re-skinned to `docs/Design/fpl-scout-report-design`. No contract changed.**

The `web/` layer was restyled to the "FPL Scout Report v2" design bundle. This was a re-skin,
not a rewrite: every API contract, the section vocabulary, the `locked_sections` mechanism, the
picker rules and the mode-driven hero survive byte-for-byte. **Nothing in this note is a
contract change** — it records where the *design* was not followed literally, and why.

- **Design theme adopted wholesale.** The bundle's §05 ships a `tailwind.config` block; it is
  transcribed into `web/tailwind.config.ts` (surfaces/ink/state/out/in scales, the named type
  scale, `maxWidth.measure = 64ch`). Colours and sizes are theme tokens, not inline utilities.
  Instrument Sans is self-hosted via `next/font/google` — no new runtime dependency, and no
  external font request from the deployed page.
- **Section display titles changed; section KEYS and ORDER did not.** `SECTION_TITLES` now
  reads "The verdict" / "This week's transfer" / "Bench order & the plan" etc. to match the
  design. `SECTION_ORDER` and the keys the renderer splits on
  (`verdict, transfer, captaincy, bench_plan, chips, watchlist, fixtures`) are untouched.
  Unknown keys from the backend now humanise (`the_week_ahead` → "The week ahead") instead of
  rendering as raw identifiers.
- **One lock card per RUN of consecutive locked keys, not one per key.** The design shows a
  single "In the full brief" card naming everything behind it. `BriefView` groups adjacent
  `locked_sections` entries into one `LockCard` and lists their titles. Still driven purely by
  the API response — Stage A's `locked_sections: []` produces zero lock cards, and a Stage B
  response producing non-adjacent locks still yields separate cards. `/story` exercises both.
- **Three design elements were deliberately NOT built, because the data to back them does not exist:**
  - *"4,102 drafts marked · ~55s to build a squad"* stats row — no metric source; would be invented.
  - *"1 free transfer"* in the header pill — free-transfer counts are not exposed by the public
    FPL API (Layer 1 invariant: assume 1, emit a warning, never silently fudge). The pill shows
    only `£X.Xm ITB`, which the response actually carries, and is omitted when `bank` is null.
  - *"Sign in"* nav item — no accounts in v1 (§Delivery invariants). Omitted rather than stubbed.
- **`gameweek: 0` renders as "Pre-season draft brief".** The API's pre-season anchor is 0; the
  old UI printed "GW0", which is not a thing a user recognises. Display-only, in `BriefView`
  and the OG title. The field is unchanged.
- **`postSignup` gained a `source` argument** (default `"landing"`, brief page sends
  `"draft_brief"`). The `source` field already existed in `SignupRequest`; the frontend was
  hardcoding one value, so per-surface attribution was impossible. Additive, backend untouched.
- **Picker row cap is now per position, not global — this was a live bug.** The pool was capped
  at the first 250 filtered players. Once the list was grouped by position (as the design
  requires), that cap provably filled with goalkeepers and defenders and made **forwards
  unreachable without typing a search**. The cap is now `ROWS_PER_POS = 60` per group, and
  picked players are pinned into view even when they fall outside the cap or the active search,
  so they can always be captained or removed.
- **Long-wait loading state is timer-driven, and honestly so.** `POST /api/brief` is a single
  opaque request with no progress to report, so the staged lines advance on a schedule
  (0s / 3s / 14s), the "while you wait" line rotates every 9s, and the cold-start note appears
  at 10s. It is built to hold attention for the ~50s Render free-tier cold start without
  claiming knowledge it does not have. Upgrade path is noted in the component.
- **`/privacy` added.** The design's footer links to it from every page and the product collects
  email addresses; a 404 there would have been a bug, not a re-skin. Static, no data.

**2026-07-30 — Section vocabulary unified; paywall made provably fail-closed. Pre-hibernation pass.**

§1.1 shows `locked_sections: ["bench_plan","chips","watchlist"]`. Shipping that literally
against the code as it stood would have **served the entire paid product for free**. This
note records the fix. Nothing here is a response shape change — the §1.1 JSON is identical.

- **The vocabulary was defined three times and the three disagreed.** `api._SECTION_KEYS`
  mapped the Layer 3 stub's headers (`## Verdict`, `## Transfer`, `## Bench order`); the
  Layer 4 prompt emits different text (`## The verdict`, `## This week's transfer`,
  `## Bench order & the plan`); `web/lib/api.ts` carried a seventh key (`chips`) no backend
  path produced, plus `fixtures`, which the spec never listed. Unmapped headers were slugged
  (`head.lower().replace(" ", "_")`), so against LLM output four of six keys silently
  changed. Gating on the §1.1 list would have matched **nothing** — a fail-OPEN paywall —
  while the lock card advertised a `chips` section that did not exist.
- **One source of truth: `graph/sections.py`.** Keys, titles, header aliases for BOTH writer
  formats, the free/paid split, and the preview's separate vocabulary. `graph/` is the only
  home that respects the import direction (`writer/` may import `graph/`, never the reverse).
  `writer/config`, `writer/generic`, `graph/nodes/writer`, `api/service` and `web/lib/api.ts`
  all derive from it; no `## Header` literal survives in any producer.
- **Gating is an ALLOW-LIST, not a deny-list.** `gate_sections` returns only keys in
  `FREE_SECTIONS`. A deny-list over `PAID_SECTIONS` leaks any header the splitter fails to
  recognise — one LLM typo (`&` → `and`) and paid analysis ships free. The allow-list fails
  the other way: a paying reader might miss a section. Wrong in the recoverable direction.
  Unmappable headers additionally get an `x_` prefix so they cannot impersonate a free key.
- **`locked_sections` now names only sections that actually exist** (present AND non-empty),
  so the lock card can never promise content that is not behind it.
- **`chips` implemented, `fixtures` folded into `bench_plan`** — see the Layer 4 §8 note.
  The canonical list is now exactly §1.1's: `verdict, transfer, captaincy, bench_plan,
  chips, watchlist`. Spec, code, and lock-card copy agree for the first time.
- **`PAYWALL_ENABLED` still does not exist** — this was mechanism-only work. `api.service.PAYWALL`
  is a `False` constant and Stage A responses are unchanged (`locked_sections: []`). The flip
  is one line, and `tests/test_sections.py` (28 tests) proves it cannot fail open first.
- **`?token=` removed from `GET /api/brief/{team_id}/{gw}`.** It was accepted and ignored;
  nothing mints or verifies a JWT. An auth-shaped parameter that does nothing reads as
  implemented security. Stage B adds it back with the send job. FastAPI ignores unknown
  query params, so any early caller still gets the free view.
- **`PreviewTeaser` ordered by the brief vocabulary**, which shares no key with the preview,
  so the lookup never matched and it fell through to whatever key came first. Now uses
  `PREVIEW_ORDER`. Same section rendered, no longer by accident.
- **Deployment config committed** (`render.yaml`, `.env.example`, `web/.env.example`). The
  launch topology previously existed only as runbook prose. `railway.toml` moved to
  `docs/railway.toml.deferred` — it described an abandoned platform from the repo root,
  where it read as active config.
