# LAUNCH CHECKLIST

**Started 2026-08-04. Mostly a stub — the bulk of this document is pasted in separately.**

This is the *what must be true* list. `docs/GO-LIVE.md` is the *what to do, in what
order* runbook. When they disagree, GO-LIVE.md is the executable one.

---

## Legal pages required before Paddle live review

Paddle is the **merchant of record** — it sells to your customer on your behalf, which is
why its account review is stricter than a payment processor's and why these are hard
requirements, not good practice. Paddle staff load the site in a browser and check.

All three must be **real pages at these exact paths**, and all three must be **linked in
the site footer** on every page:

| Page | Route | File | State today |
|---|---|---|---|
| Privacy policy | `/privacy` | `web/app/privacy/page.tsx` | **written** — rewritten 2026-08-04 for the accounts build |
| Terms of service | `/terms` | `web/app/terms/page.tsx` | **placeholder, TODO prose** |
| Refund policy | `/refunds` | `web/app/refunds/page.tsx` | **placeholder, TODO prose** |

Footer links are live in `web/components/Chrome.tsx` (`SiteFooter`), so routing and
navigation are done. **Only the prose is outstanding.**

### What each page must cover

**Terms of service** — what the service is and, explicitly, what it is not (analysis of
public data, not financial or any other kind of advice); the two paid plans and what each
grants; that briefs are generated from public FPL data and may be wrong; acceptable use;
grounds for account termination; limitation of liability; governing law.

**Refund policy** — the refund window as a specific number of days; how a customer
requests one; how the recurring monthly plan differs from the one-off season pass; what
happens to access after a refund is issued; and that Paddle is the merchant of record, so
refunds are processed through Paddle.

**Privacy policy** — already written. Keep it honest as the product changes; it names
what is stored, which sign-in methods exist, and that deleting an account does not cancel
a subscription.

### The version-string rule

`TERMS_VERSION` (Render env / `api/config.py`), the `VERSION` constant in
`web/app/terms/page.tsx`, and the one in `web/app/privacy/page.tsx` must be **equal**, and
must be **bumped in the same commit** as any text change. Every account whose stored
version differs is prompted to re-accept before any paid section is served — so a bump
with no text change asks your customers to re-agree to identical words for nothing.

### Also checked by Paddle's review

- **Visible pricing** — a human must be able to see what each plan costs *without*
  starting a checkout.
- A **working contact method**. The footer currently points at the placeholder
  `hello@thescoutreport.fpl`; replace it with a real mailbox at the real domain
  (`web/components/Chrome.tsx`).

---

## TODO — sections to be pasted in

*(Placeholder. The rest of the checklist is being written separately.)*
