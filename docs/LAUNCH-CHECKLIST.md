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
| Terms of service | `/terms` | `web/app/terms/page.tsx` | **DRAFT written 2026-08-07 — owner must read before launch** |
| Refund policy | `/refunds` | `web/app/refunds/page.tsx` | **DRAFT written 2026-08-07 — owner must read before launch** |

Footer links are live in `web/components/Chrome.tsx` (`SiteFooter`), so routing and
navigation are done.

> **THE DRAFTS ARE NOT SIGNED OFF.** Both pages were written on 2026-08-07 in the same
> plain register as `/privacy` and cover everything the two sections below require, but
> **nobody with a practising certificate has read them**. They are drafts by an author who
> is not a lawyer, in a document that binds you to paying customers and to Paddle.
>
> Before launch, the owner must read both end to end and specifically decide on:
> - **the refund windows** — 14 days on a first purchase, 7 days on a monthly renewal,
>   14 days on the season pass. Each is a promise you have to honour on a bad week, not
>   only a good one. They are the kind of number that is easy to write and expensive to
>   change once customers have bought under it.
> - **the liability limitation** and **governing law (India)** in `/terms` — the two
>   clauses most worth an actual second opinion.
> - **the repeat-refund limit** in `/refunds` (we refund a repeat purchase once), which is
>   a judgement call about your own tolerance, not a legal requirement.

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

All three moved `2026-08-04` → `2026-08-07` when the terms drafts landed. That bump was
deliberate: an account holding `2026-08-04` accepted a page reading "TODO — write the
terms", and carrying that forward as agreement to the real text would be a consent record
for words nobody ever saw. `/privacy` moved with them although **its text did not change**
— one shared version string covers both documents.

`/refunds` now carries a `VERSION` too, and it is **display-only**. It is deliberately not
`TERMS_VERSION`: the refund policy is not part of the acceptance gate, so editing it must
not re-prompt every account. Keep its date honest anyway — a customer arguing about a
refund is entitled to see which version they bought under.

### Also checked by Paddle's review

- **Visible pricing** — a human must be able to see what each plan costs *without*
  starting a checkout.
- A **working contact method**. The footer currently points at the placeholder
  `hello@thescoutreport.fpl`; replace it with a real mailbox at the real domain
  (`web/components/Chrome.tsx`).

---

## TODO — sections to be pasted in

*(Placeholder. The rest of the checklist is being written separately.)*
