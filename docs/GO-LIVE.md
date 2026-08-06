# GO-LIVE

**Written 2026-08-04, against `main` @ "Close accounts + auth".**

This is the ordered runbook for taking this repo from "nothing is deployed" to "a real
customer paid and got a brief". It assumes a browser, a terminal, a credit card, and
**zero memory of this project**. Everything you need to know to execute a step is in
that step.

Read these two facts before you start, because they shape every decision below:

1. **The free brief takes no login and no payment.** Steps 1–9 are enough to have a
   live, working, free product. Steps 10 onward add money. You can stop after step 9
   and still be shipped.
2. **Nothing in this repo has ever spoken to Paddle, Google, or Resend.** The test
   suite is green and proves the code *around* those boundaries. It proves nothing
   about the boundaries themselves. Expect the first contact with each provider to
   surface something this document did not predict.

Environment-variable names below are the **real** names from `.env.example`. The one
that catches everybody: the spec calls the database URL `DATABASE_URL`; the actual
variable is **`FPL_DATABASE_URL`**.

---

## The order, at a glance

| # | Step | Blocks | Can you skip it? |
|---|---|---|---|
| 0 | **`scripts/smoke.py`** — the local pre-deploy gate | Everything. Run it before you spend a penny | **No** |
| 1 | Buy the domain | DNS, Google OAuth, Resend, Paddle live | No |
| 2 | Neon (Postgres) | Render — the API cannot boot without a DB | No |
| 3 | Render (API) | Vercel, GitHub cron, every webhook | No |
| 4 | Migrations + first sync + **backfill-archive** | A brief that isn't all goalkeepers | **No — read step 4** |
| 5 | Vercel (web) | Anything a human looks at | No |
| 6 | DNS: apex → Vercel, `api.` → Render | Cookies, CORS, OAuth, Paddle | No |
| 7 | GitHub Actions cron | Data going stale after launch | No |
| 8 | Resend + SPF/DKIM | Any email at all, incl. magic links | No |
| 9 | Google Cloud OAuth | "Sign in with Google" only | Yes — magic link works alone |
| 10 | Paddle **sandbox** | Testing payments without real money | No (before step 12) |
| 11 | Write the three legal pages | Paddle live review | No |
| 12 | Paddle **live** | Taking real money | No |
| 13 | The first-live-test sequence | Confidence | No |

Steps 1–3 are strictly sequential. Steps 8 and 9 are independent of each other and of
10–12; do them in whatever order suits you, as long as 8 lands before you flip
`SEND_ENABLED` and 11 lands before you submit to Paddle for live review.

---

## Step 0 — The pre-deploy gate (`scripts/smoke.py`)

**Blocks:** everything. Run it on the commit you are about to deploy, before you buy a
domain, and again any time a later step sends you back to the code.

**Do:**

```bash
.venv/Scripts/python -m pytest -q            # 467 passed — the units and the seams
.venv/Scripts/python scripts/smoke.py        # the gate — expect ALL CHECKS PASSED
```

**Cost: zero.** No browser, no Paddle, no Google, no Resend, no LLM, nothing deployed.
The only network call is the FPL public API for the sync; `--offline` swaps in the frozen
snapshot and removes even that. `--keep` keeps the scratch directory and both server logs.

**What it actually does**, in ~15 seconds, all against a throwaway SQLite file:

1. **A database from nothing** — `init-db`, then `backfill-archive`, then `sync`, in that
   order, and asserts the top-20 EP list is a position mix. That last assertion is step 4's
   entire failure mode caught locally: a fresh DB with an empty `players_archive` scores
   every player onto the position/price baseline and recommends a goalkeeper as captain,
   with nothing erroring.
2. **The real HTTP surface** — it starts an actual `uvicorn` process and drives it with a
   real cookie jar. `pytest` never opens a socket and never runs a migration; this is the
   layer between "the tests pass" and "the deploy works".
3. **The free funnel stays open** — anonymous brief generation, the draft path, and an
   anonymous read of a paid team's brief returning **200 with sections locked**, never a
   401 and never a redirect to `/login`.
4. **Magic-link login end to end** — request, consume, session cookie, `/api/auth/me`,
   terms acceptance, logout, account deletion. Plus the failure paths: replay, garbage
   token, malformed address, anonymous writes → 401.
5. **The paywall, both ways** — a second server with `PAYWALL_ENABLED=true`, checking that
   paid sections lock for anonymous readers, unlock for an entitled account, re-lock when
   the terms version goes stale (200, never 403), and stay locked for `past_due` and for an
   unrecognised Paddle status.
6. **Admin auth** — no token, wrong token, right token.
7. **Fail-closed boot** — a third launch with `PAYWALL_ENABLED=true` and an empty
   `BRIEF_TOKEN_SECRET` that **must die on startup**. Only observable as a dead process,
   which is why it is a check rather than a test.

**Two things it deliberately does not prove**, and cannot from here:

- **No provider is contacted.** Paddle's effect on our tables is *seeded* — an entitled
  account and an `active` subscription written directly. That exercises our side of the
  boundary, which is all this repo has ever exercised (`docs/STATUS.md` §10.4). Steps 8–13
  are where the boundaries themselves get tested.
- **Postgres is not exercised.** The gate runs on SQLite; Neon is step 2. Anything
  dialect-specific surfaces there, not here.

**The magic link, locally.** `magic_link_tokens` stores a SHA-256, so a sign-in link that
is never mailed is unrecoverable. With `RESEND_API_KEY` unset, `mail.send_magic_link`
therefore **logs the whole link, token included**, at WARNING — which is the only way to
sign in on localhost, and how the gate gets its token. That branch is unreachable once a
key is set, so it never fires in a deployment. Don't "harden" it away; local login goes
with it.

**Verify:** the last line is `ALL CHECKS PASSED`. Exit code is 0 only if every check
passed; any failure lists the check names and leaves the scratch directory and server logs
in place.

---

## Step 1 — Domain registrar

**Blocks:** DNS (step 6), Resend domain verification (step 8), Google OAuth production
origins (step 9), Paddle live review (step 12). Everything downstream hangs on the
hostname, so buy it first and don't change your mind later.

**Do:**

1. Buy the domain. The repo's placeholder is `thescoutreport.fpl`, which is not a real
   TLD — pick a real one and be ready to find-and-replace the placeholder in
   `web/components/Chrome.tsx` (the `mailto:` in the footer) and `web/app/privacy/page.tsx`.
2. Decide the two hostnames now and write them down:
   - `example.com` — the Next.js site (Vercel)
   - `api.example.com` — the FastAPI service (Render)
   They **must share a registrable domain**. This is not cosmetic: the session cookie
   is `SameSite=lax` with `SESSION_COOKIE_DOMAIN=.example.com`, which only works if both
   hosts sit under the same parent. Split them across two unrelated domains and login
   silently stops working in production while working perfectly on localhost.
3. Set up a working mailbox at that domain (`hello@example.com`). Paddle's review will
   check it, and it's the footer contact address.

**Produces:** nothing that goes in an env var yet — but every hostname below.

**Rollback:** none needed. If you change domains later, you must redo steps 6, 8, 9 and
the Paddle live approval, and update `FRONTEND_ORIGIN`, `PUBLIC_WEB_URL`,
`PUBLIC_API_URL`, `GOOGLE_REDIRECT_URI`, `SESSION_COOKIE_DOMAIN`. Cheap to get right
now, expensive later.

---

## Step 2 — Neon (Postgres)

**Blocks:** Render. The API's start command runs migrations; with no database it will
crash-loop on boot and you will misdiagnose it as a Render problem.

**Do:**

1. Sign up at neon.tech. Create a project; region close to your Render region (step 3
   uses `oregon`, so pick US West / Oregon if offered).
2. Copy the **pooled** connection string. Neon gives you two — pooled and direct. Take
   pooled; Render's free tier gets connection-churned and the pooler absorbs it.
3. **Rewrite the scheme.** Neon hands you `postgres://…`. SQLAlchemy rejects that.
   Change it to `postgresql://…`. This is a five-second edit that costs an hour if you
   miss it, because the error surfaces at Render boot as an opaque dialect error.
4. Leave the connection string somewhere you can paste it in step 3, then close the tab.

**Produces:**

| Neon gives you | Goes in |
|---|---|
| Pooled connection string (`postgresql://…`) | **`FPL_DATABASE_URL`** on Render |

**Rollback:** delete the Neon project and make a new one. Nothing depends on it yet.

---

## Step 3 — Render (the API)

**Blocks:** Vercel (the frontend needs an API URL), the GitHub cron, every Paddle
webhook, every magic link.

**Do:**

1. Sign up at render.com, connect the GitHub repo.
2. **New → Blueprint**, point it at this repo. `render.yaml` is the blueprint; it
   defines one free-tier web service named `fpl-analyst-api` on branch `main`, with
   `healthCheckPath: /api/health`.
3. Render will prompt for every var marked `sync: false`. Fill in:

| Variable | Value | Notes |
|---|---|---|
| `FPL_DATABASE_URL` | the Neon pooled string from step 2 | must start `postgresql://` |
| `FRONTEND_ORIGIN` | `https://example.com` | **no trailing slash.** Single-origin CORS by design — Vercel *preview* deploys will fail CORS. That is expected, not a bug. |
| `RESEND_API_KEY` | leave empty for now | step 8 fills it. Empty = `api/mail.py` logs and returns, which is the honest default. |
| `ADMIN_TOKEN` | `generateValue: true` — Render mints it | **Copy it out of the dashboard now.** Step 7 needs the same string as a GitHub secret. |
| `BRIEF_TOKEN_SECRET` | `generateValue: true` — Render mints it | 32+ chars. Leave it alone forever. |

4. Leave everything else at its default. Specifically **do not** set `RUN_SCHEDULER=true`
   — the blueprint deliberately omits it. Render's free tier sleeps after ~15 minutes
   idle, so an in-process scheduler cannot survive between requests, and step 7 drives
   sync externally instead. Setting both gives you two sync drivers on one database,
   which double-syncs and, once `SEND_ENABLED` is on, mails every customer twice.
5. Deploy. Wait for the health check to go green.
6. Verify from your terminal:
   ```bash
   curl -sS https://fpl-analyst-api.onrender.com/api/health
   ```
   First call after a sleep takes ~60s. That is the documented free-tier cost.

**Produces:**

| Render gives you | Goes in |
|---|---|
| Service URL (`https://…onrender.com`) | `NEXT_PUBLIC_API_URL` on Vercel (step 5), `API_BASE_URL` GitHub secret (step 7) — replaced by `https://api.example.com` after step 6 |
| Generated `ADMIN_TOKEN` | `ADMIN_TOKEN` GitHub secret (step 7); the Bearer for every `/api/admin/*` call |

**Rollback:** if the deploy fails, read the Render log, not the dashboard status.
The three failures you will actually hit, in order of likelihood: `postgres://` not
rewritten to `postgresql://`; a `FRONTEND_ORIGIN` with a trailing slash (deploys fine,
breaks the browser later); Python version drift (the blueprint pins `3.13`). Fix the
env var and redeploy — the service is stateless, so a failed deploy has changed nothing
except possibly the schema, and migrations are idempotent.

---

## Step 4 — Migrations, first sync, and the backfill

**Blocks:** a brief that is not nonsense. **Do not skip the backfill.** This is the one
step where everything reports success and the product is still broken.

The failure it prevents, stated plainly: a fresh database has an empty `players_archive`.
Pre-season scoring has nothing else to run on, so every expected-points figure collapses
onto the (position, price-band) baseline. The top 20 comes back **all goalkeepers** and
the recommended captain is a keeper. Nothing errors. Nothing logs a warning. Verified on
a fresh clone on 2026-08-01: the top 10 were ten goalkeepers on two distinct EP values.

**Do:**

1. Migrations already ran — Render's start command is
   `python -m fpl init-db && uvicorn …`, and `init-db` applies Alembic migrations
   idempotently on every boot. Confirm the two newest are in: `d4e5f6a7b8c9` (Stage B
   billing) and `e5f6a7b8c9d0` (accounts).
2. Trigger the first sync against production:
   ```bash
   curl -sS -X POST https://api.example.com/api/admin/sync \
     -H "Authorization: Bearer $ADMIN_TOKEN"
   ```
   **Read the summary line in the response body, not just the exit code.** Sync is
   abort-or-nothing: both payloads are fetched and validated up front, then all writes
   land in one transaction. A failed fetch leaves the database exactly as it was —
   "stale beats broken" — and returns 200-shaped output describing that it did nothing.
3. **Backfill the archive.** There is no admin route for this; it is a CLI command, so
   run it against the production database from your own machine:
   ```bash
   FPL_DATABASE_URL='postgresql://…neon pooled…' \
     .venv/Scripts/python -m fpl backfill-archive \
       --season 2025-26 --from tests/assets/bootstrap_2025-26.json
   ```
   (PowerShell: `$env:FPL_DATABASE_URL='postgresql://…'` on its own line first.)
4. Re-run the sync from step 4.2 so scoring runs again through the now-populated archive.
5. **Verify, and do not take "it returned 200" as verification.** Open
   `https://example.com/admin` (or curl `/api/admin/top` with the Bearer) and check three
   things: roughly **841 archive rows**; `match_method` mostly `name_position`; and the
   top 20 is a **mix of positions**. If it is all goalkeepers, the backfill did not land
   — go back to 4.3.

**Produces:** no env vars. A database that produces real briefs.

**Rollback:** `backfill-archive` is additive and re-runnable. If sync wrote something
wrong, re-run sync — it is a full-replace inside one transaction, not an accumulation.
If the schema itself is wrong, `alembic downgrade` against `FPL_DATABASE_URL` and
redeploy; both new migrations were verified up/down/up with zero drift.

---

## Step 5 — Vercel (the web app)

**Blocks:** anything a human looks at.

**Do:**

1. Sign up at vercel.com, import the repo.
2. **Set the root directory to `web/`.** The Next.js app is not at the repo root; without
   this the build fails immediately and confusingly.
3. Framework preset: Next.js. Build command and output directory: leave as detected.
4. Environment variable (Production scope):

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | `https://api.example.com` (or the `.onrender.com` URL until step 6 lands) |

   Next.js only exposes variables prefixed `NEXT_PUBLIC_`. This is the only one the
   frontend reads.
5. Deploy. Confirm the build produces **15 routes** (9 public, 6 under `/admin`) — plus
   the two new legal pages from this branch, so **17** once `/terms` and `/refunds` are
   in. If the count is lower, a page failed to compile and Vercel will have told you.
6. Visit the deployment URL. Generate a free brief end to end. This works with no login
   and no payment — if it doesn't, the problem is between Vercel and Render (CORS or
   `NEXT_PUBLIC_API_URL`), not in the product.

**Produces:**

| Vercel gives you | Goes in |
|---|---|
| Production deployment URL | `FRONTEND_ORIGIN` on Render, `PUBLIC_WEB_URL` on Render — both replaced with `https://example.com` after step 6 |

**Rollback:** Vercel keeps every deployment. Promote the previous one from the
dashboard; it is instant and needs no rebuild.

---

## Step 6 — DNS

**Blocks:** session cookies (the shared-parent-domain requirement from step 1), CORS,
Google OAuth redirect URIs, the Paddle webhook URL. Do this before steps 9 and 10 so
you register the *final* hostnames with those providers and never have to go back.

**Do, at the registrar's DNS panel:**

| Record | Host | Points at | Why |
|---|---|---|---|
| `A` (or `ALIAS`/`ANAME`) | `@` | Vercel's apex target — take the exact value from Vercel's *Domains* tab | the site |
| `CNAME` | `www` | `cname.vercel-dns.com` | Vercel handles the redirect to apex |
| `CNAME` | `api` | your Render service hostname (`fpl-analyst-api.onrender.com`) | the API |

Then, in each dashboard: add `example.com` under Vercel → Project → Domains, and
`api.example.com` under Render → Settings → Custom Domain. Both will issue TLS
certificates automatically once the records resolve. Propagation is usually minutes,
occasionally an hour.

**Once the hostnames resolve, go back to Render and set the real values:**

| Variable | Value | Why it matters |
|---|---|---|
| `FRONTEND_ORIGIN` | `https://example.com` | CORS allow-list. No trailing slash. |
| `PUBLIC_WEB_URL` | `https://example.com` | links **inside emails** — brief links, the visible unsubscribe link |
| `PUBLIC_API_URL` | `https://api.example.com` | **only** the RFC 8058 one-click `List-Unsubscribe` header, which a mail client POSTs to with no human present. It must be an origin that serves `/api/*`. |
| `SESSION_COOKIE_DOMAIN` | `.example.com` | leading dot. Without it the cookie is host-only and never reaches both hosts. |
| `SESSION_COOKIE_SECURE` | `true` | already the default. Never set false anywhere that is not plain-HTTP local dev. |
| `SESSION_COOKIE_SAMESITE` | `lax` | already the default, and correct for a shared parent domain. Never `strict` — it drops the cookie on the magic-link click, which arrives as a cross-site top-level navigation from a mail client. |

`FRONTEND_ORIGIN` and `PUBLIC_WEB_URL` will usually hold the same string. They are two
variables on purpose: one is a CORS allow-list entry, the other is a link builder, and
changing one must never silently change the other.

Also update `NEXT_PUBLIC_API_URL` on Vercel to `https://api.example.com` and redeploy.

**Produces:** the final hostnames every remaining step registers.

**Rollback:** DNS changes are reversible but cached. If you point a record wrong,
fix it and wait out the TTL — set TTLs low (300s) *before* you start, so a mistake
costs five minutes instead of a day.

---

## Step 7 — GitHub Actions cron

**Blocks:** the data going stale a day after launch.

`.github/workflows/sync.yml` already exists and runs twice daily (06:00 and 18:00 UTC).
It needs two repository secrets, and does nothing without them.

**Do:** GitHub → repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|---|---|
| `API_BASE_URL` | `https://api.example.com` |
| `ADMIN_TOKEN` | **the exact string Render generated in step 3** |

Then fire it manually: Actions → `sync` → Run workflow. The job wakes the sleeping
service with a health ping first (cold start ~1 min), then POSTs `/api/admin/sync`.
Any non-200 fails the job on purpose — a silent sync failure means the database quietly
goes stale, which is worse than a red run in the Actions tab.

**Rollback:** disable the workflow from the Actions tab. Data goes stale; nothing breaks.
If the manual run 401s, the `ADMIN_TOKEN` secret does not match Render's — re-copy it,
character for character, and do not retype it by hand.

---

## Step 8 — Resend + SPF/DKIM

**Blocks:** every email the product sends — magic-link logins, welcome mails, and the
weekly brief send. Until this is done, `RESEND_API_KEY` is empty and `api/mail.py` logs
the message and returns. That is a safe default, not a broken one, but it means
**magic-link login does not work in production** until this step lands.

**Do:**

1. Sign up at resend.com.
2. **Domains → Add Domain → `example.com`.** Resend shows you a set of DNS records.
   Add all of them at the registrar:
   - **DKIM** — a `TXT` record at `resend._domainkey` (Resend gives the exact host and
     the public key value). This is what proves the mail is really from you.
   - **SPF** — a `TXT` record, typically at `send.example.com` or the apex, containing
     `v=spf1 include:amazonses.com ~all`. **If you already have an SPF record at that
     host, merge the `include:` into it — never publish two SPF records for one host.**
     Two SPF records is a permanent-error condition and mail providers will fail the
     check outright.
   - **MX** — a record at the same `send.` subdomain, for bounce handling.
   - **DMARC** (optional but do it) — `TXT` at `_dmarc` with
     `v=DMARC1; p=none; rua=mailto:hello@example.com`. Start at `p=none` so you observe
     before you enforce.
3. Wait for Resend to show the domain **Verified**. Do not proceed on "Pending".
4. Create an API key (Full access is fine; sending-only is better).
5. Set on Render:

| Variable | Value |
|---|---|
| `RESEND_API_KEY` | the key from Resend |
| `SEND_FROM` | `The Scout Report <brief@example.com>` — must be at the **verified** domain |

   The `.env.example` default is `FPL Analyst <onboarding@resend.dev>`. Resend's shared
   `resend.dev` sender only delivers to your own verified address, so it is fine for a
   smoke test and useless for customers.
6. Test by requesting a magic link at `https://example.com/login` with your own address.
   The mail should arrive within seconds. Check the headers show `dkim=pass` and
   `spf=pass`.

**Leave `SEND_ENABLED=false`.** That flag controls the weekly *batch*, not transactional
mail — magic links work without it. Flipping it comes at step 13, deliberately last, with
one address on the list.

**Produces:**

| Resend gives you | Goes in |
|---|---|
| API key | `RESEND_API_KEY` |
| Verified sending domain | `SEND_FROM` |

**Rollback:** blank `RESEND_API_KEY` on Render. Mail goes back to being logged and
dropped; nothing else changes. DNS records are additive and safe to leave.

---

## Step 9 — Google Cloud (OAuth credentials)

**Blocks:** the "Sign in with Google" button only. **This step is genuinely optional.**
With `GOOGLE_CLIENT_ID` empty, `/api/auth/google/*` answers 404 and the login page offers
only the magic link, which is a complete working login. Skip it if you want to launch
sooner.

The rule that matters: **both variables or neither.** A half-configured provider fails at
Google's redirect, which is the single worst place to discover a configuration mistake.

**Do:**

1. console.cloud.google.com → create a project (e.g. "The Scout Report").
2. **APIs & Services → OAuth consent screen.** User type: **External**. Fill in:
   - App name: `The Scout Report`
   - User support email: `hello@example.com`
   - App logo: optional
   - **Application home page:** `https://example.com`
   - **Privacy policy link:** `https://example.com/privacy`
   - **Terms of service link:** `https://example.com/terms`
   - Authorized domain: `example.com`
   - Developer contact email: your address
3. **Scopes:** add exactly two — `openid` and `email`. The code requests the literal
   scope string `"openid email"` (`api/accounts.py:412`) and nothing more. Both are
   non-sensitive, which is why this app **does not require Google's verification review**
   and can stay in Testing or be published without an audit. Do not add `profile` or
   anything else; adding a sensitive scope drags you into a review process you have no
   reason to enter.
4. **Credentials → Create Credentials → OAuth client ID → Web application.**

   **Authorized JavaScript origins** (no paths, no trailing slashes):
   ```
   http://localhost:3000
   https://example.com
   ```

   **Authorized redirect URIs** — these are compared by Google as an **exact string**.
   A trailing slash, `http` vs `https`, or `127.0.0.1` vs `localhost` is a mismatch:
   ```
   http://127.0.0.1:8000/api/auth/google/callback
   https://api.example.com/api/auth/google/callback
   ```
   Note the redirect goes to the **API host**, not the web host — the callback is a
   FastAPI route. And note it is `127.0.0.1`, not `localhost`, for local dev: that is
   what `.env.example` ships as `GOOGLE_REDIRECT_URI`, and it must match byte for byte.
5. Copy the client ID and client secret. Set on Render:

| Variable | Value |
|---|---|
| `GOOGLE_CLIENT_ID` | from the credentials page |
| `GOOGLE_CLIENT_SECRET` | from the credentials page |
| `GOOGLE_REDIRECT_URI` | `https://api.example.com/api/auth/google/callback` — **exactly** the string you registered |

6. Redeploy Render and test the Google button at `https://example.com/login`.

**Rollback:** blank `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` on Render and redeploy.
The route 404s, the button disappears, magic link keeps working. Nothing about existing
accounts changes — identity is address-based, not provider-id-based, so a user who signed
up with Google can log in with a magic link to the same address and land in the same
account.

---

## Step 10 — Paddle **sandbox**

**Blocks:** testing payments without moving real money. Do the whole sandbox loop before
you touch the live account.

**Do:**

1. Sign up at **sandbox-vendors.paddle.com** — this is a separate account from live.
2. **Catalog → Products.** Create one product, "The Scout Report". Under it create
   **two prices**:
   - **Monthly** — recurring, billed monthly, your monthly price.
   - **Season pass** — **one-time**, not recurring. This matters: the code handles the
     season pass through `transaction.completed`, not a subscription lifecycle event,
     because Paddle sends no billing period for a one-off purchase.
3. Copy both **price ids** (they look like `pri_01h…`).
4. **Developer Tools → Notifications → New destination.**
   - URL: `https://api.example.com/api/webhooks/paddle`
   - Subscribe to at least: `subscription.created`, `subscription.updated`,
     `subscription.canceled`, `transaction.completed`.
   - Save, then open the destination and copy its **secret key**.

   **The single most common mistake in this entire document:** the
   **notification destination secret** and the **API key** are two different strings.
   Mixing them up rejects every webhook with a signature failure, and the error message
   will not tell you which one you got wrong. The destination secret is on the
   destination's own page, not under API keys.
5. **Developer Tools → Authentication** → create an API key.
6. Set on Render:

| Variable | Value | Notes |
|---|---|---|
| `PADDLE_API_BASE` | `https://sandbox-api.paddle.com` | this is already the `.env.example` default |
| `PADDLE_API_KEY` | the sandbox API key | |
| `PADDLE_WEBHOOK_SECRET` | the **destination** secret | empty = the webhook rejects everything, fail-closed, same rule as `ADMIN_TOKEN` |
| `PADDLE_PRICE_MONTHLY` | the monthly `pri_…` id | |
| `PADDLE_PRICE_SEASON` | the season `pri_…` id | |
| `SEASON_PASS_END_YEAR` | `2027` | the calendar year the pass expires in, 30 June. Paddle sends no period for a one-off, so the product defines it. **Bump this every summer.** |
| `PADDLE_REPLAY_WINDOW_S` | `300` | leave it. Paddle signs the timestamp, so this is replay protection, not a clock-skew allowance. |
| `PAYWALL_ENABLED` | `true` | flip it now — this is what makes paid sections lock |

   The price ids live on the server on purpose. A client sends a plan **name**
   (`"monthly"` / `"season"`) and the server resolves the id. A price never round-trips
   through the browser.
7. Redeploy. The app performs a startup check: with `PAYWALL_ENABLED` or `SEND_ENABLED`
   true and a weak or empty `BRIEF_TOKEN_SECRET`, it **refuses to boot**. That is
   deliberate — a guessable signing key means anyone can forge a full-view token for any
   team, which is the paid product given away. Render generated a strong one in step 3,
   so this should pass; if it doesn't, that is the check working.

**Rollback:** set `PAYWALL_ENABLED=false` and redeploy. Every response goes back to
`locked_sections: []` and the whole site is free again — instantly, with no data change.
This is the cheapest rollback in the entire runbook; use it without hesitation if
anything about payments looks wrong.

---

## Step 11 — The three legal pages

**Blocks:** Paddle live review (step 12). **Paddle will not approve a live account
without them.**

Paddle is the **merchant of record** — it is legally selling to your customer on your
behalf, which is why its review is stricter than a payment processor's. It checks, by
loading your site in a browser:

- a **privacy policy** at a real, reachable URL
- **terms of service** at a real, reachable URL
- a **refund policy** at a real, reachable URL
- all three **linked from the site footer**, not buried
- **visible pricing** — a human must be able to see what each plan costs without
  starting a checkout
- a working **contact method**

**Current state in this repo:** the routes and the footer links exist. `/privacy` has
real prose. **`/terms` and `/refunds` are placeholders with TODO text** —
`web/app/terms/page.tsx` and `web/app/refunds/page.tsx`. They will render, and they will
fail review as written.

**Do:** write the prose into those two files. `docs/LAUNCH-CHECKLIST.md` §"Legal pages
required before Paddle live review" lists what each must cover. Then:

- Keep `TERMS_VERSION` (Render env), the `VERSION` constant in `web/app/terms/page.tsx`,
  and the one in `web/app/privacy/page.tsx` **equal, and bump all three in one commit**.
  Every account whose stored version differs is prompted to re-accept before any paid
  section is served — so a bump with no text change asks your customers to re-agree to
  the same words for no reason.
- Replace the `thescoutreport.fpl` placeholder mailto in `web/components/Chrome.tsx`
  with your real address.
- Make sure the pricing is visible on a public page.

**Rollback:** none. This is writing, not configuration.

---

## Step 12 — Paddle **live**

**Blocks:** taking real money.

**Do:**

1. Sign up at **vendors.paddle.com** (live, separate account and separate credentials
   from sandbox).
2. Submit for verification. Expect a business-details form, and expect them to actually
   load your site and check step 11's list. Approval typically takes a few business days
   and can bounce back with specific complaints — the common ones are a missing refund
   policy and pricing that is not visible without a checkout.
3. **Once approved**, recreate the product and both prices in the live catalog. **The
   price ids are different from sandbox.** Copy the new ones.
4. Create a live notification destination at the same URL
   (`https://api.example.com/api/webhooks/paddle`), same events. Copy its **destination
   secret** — again, not the API key.
5. Create a live API key.
6. Swap on Render, all five together:

| Variable | Live value |
|---|---|
| `PADDLE_API_BASE` | `https://api.paddle.com` (drop `sandbox-`) |
| `PADDLE_API_KEY` | live API key |
| `PADDLE_WEBHOOK_SECRET` | live **destination** secret |
| `PADDLE_PRICE_MONTHLY` | live monthly `pri_…` |
| `PADDLE_PRICE_SEASON` | live season `pri_…` |

   Swap all five in one edit and redeploy once. A half-swapped set — live API base with
   sandbox price ids, say — fails in a way that looks like a code bug.

**Rollback:** revert all five variables to their sandbox values and redeploy. Or set
`PAYWALL_ENABLED=false` to take the whole paywall down while you think. Existing
`subscriptions` rows are untouched by either.

---

## Step 13 — The first live test, in this exact order

This expands the sequence in `docs/STATUS.md` §9. Do not reorder it; each step's
failure is diagnosable only because the previous one succeeded.

**1. Replay one sandbox webhook.**
In the Paddle sandbox dashboard, use the notification destination's replay/test button
to send one `subscription.created`.
*Expect:* HTTP 200 from your endpoint.
*If it 401s:* you used the API key as `PADDLE_WEBHOOK_SECRET`. That is the mistake, ~90%
of the time. Go back to step 10.4.
*If it 200s but nothing happens:* check `PADDLE_PRICE_MONTHLY` matches the price id in
the payload — an unrecognised price id is ignored by design, not errored.

**2. Confirm the row appears.**
```bash
curl -sS https://api.example.com/api/admin/subscriptions \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```
*Expect:* one `subscriptions` row with the right plan and a `current_period_end`.
This is the moment the provider boundary is first actually proven. Everything before it
was tests computing signatures locally to Paddle's documented scheme, which proves the
verifier, not the format.
*If the row is missing:* re-send the webhook — delivery is idempotent, `webhook_events`
dedupes, so replaying is safe and costs nothing.

**3. Magic-link login.**
Go to `https://example.com/login`, enter your own address, click the link in the mail.
*Expect:* you land signed in; `/account` shows your address and sign-in method.
*If no mail arrives:* Resend's dashboard shows every attempt with a reason. If it shows
nothing at all, `RESEND_API_KEY` is empty on Render.
*If the mail arrives but the link logs you out again:* `SESSION_COOKIE_DOMAIN` is wrong
(needs the leading dot) or `SESSION_COOKIE_SAMESITE` got set to `strict`, which drops the
cookie on exactly this navigation.

**4. Google login.**
Sign out, then sign in with Google **using the same address**.
*Expect:* you land in the **same account**, not a second one. Identity is address-based,
so this is the real assertion here.
*If Google shows `redirect_uri_mismatch`:* the string in `GOOGLE_REDIRECT_URI` and the
one in the Google console differ. Compare them character by character — it is almost
always a trailing slash or `http` vs `https`.

**5. A real checkout with your own card.**
Only now, with live Paddle credentials in place. Buy the monthly plan from your own site,
with your own card, as a customer would.
*Expect:* the checkout completes, the live webhook fires, a `subscriptions` row appears,
and — the actual point — **the paid sections unlock for you in the browser**. That last
part is the end-to-end assertion; a row in the database is not the product.
*If it unlocks for nobody:* `PAYWALL_ENABLED` is true but the webhook is 401ing, so no
one can ever be granted access. Check the Paddle destination's delivery log.

**6. Refund it.**
Refund your own transaction from the Paddle dashboard.
*Expect:* the refund processes and access reflects it. Note the entitlement rule here:
`billing.is_entitled` runs off an **allow-list** (`ENTITLED_STATUSES`), so a status
Paddle sends that the code does not recognise **degrades to free**, never to entitled.
That is the intended failure direction — verify it degrades rather than assuming it does.

**7. Only then, the weekly send.**
Set `SEND_ENABLED=true` and `SEND_MAX_RECIPIENTS` low (say `5`) with **one address — your
own — on the list**. Trigger it manually:
```bash
curl -sS -X POST https://api.example.com/api/admin/send \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```
*Expect:* one mail, to you, with a working brief link and a working unsubscribe link.
*Two things to know before you flip this:* with `RESEND_API_KEY` empty the batch runs,
claims `send_log` rows and delivers nothing — so check both together. And **nothing
schedules this batch**; `run_batch` is reachable only through
`POST /api/admin/send` today. Wiring it to the Actions cron is a separate decision, and
doing it while `RUN_SCHEDULER=true` would mail every customer twice.

**Rollback at any point in step 13:** `PAYWALL_ENABLED=false` + `SEND_ENABLED=false`,
redeploy. You are back to a free site that works, in about 90 seconds, having lost
nothing.

---

## What is still true after all of this

Finishing every step above gives you a deployed, paid, working product. It does **not**
give you the product the repo looks like it describes. Two things remain:

- **Every brief is written by a deterministic template**, not an LLM.
  `writer/llm.py` is complete and unit-tested and **nothing calls it** — `api/service.py`
  imports exactly one symbol from `writer/`. A paying customer receives the template stub.
  This is a pause point pending prompt iteration, not a bug, and `/admin/inspect` renders
  real payloads at zero spend so that iteration can start whenever you want. Do not
  describe the product as LLM-written until that wiring lands.
- **Double opt-in for signups is not built.** The rate limit bounds the mail cannon;
  it does not close it.
