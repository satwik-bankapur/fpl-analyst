import Link from "next/link";
import { SiteHeader, SiteFooter } from "@/components/Chrome";

// WRITTEN 2026-08-07, replacing the TODO placeholder. Paddle is the merchant of
// record and will not approve a live account without a reachable refund policy
// (docs/LAUNCH-CHECKLIST.md).
//
// DRAFT — THE OWNER MUST READ THIS BEFORE LAUNCH. Every window stated here is a
// promise to a paying customer and to Paddle, so the numbers need to be ones you
// are willing to honour on a bad week, not just a good one.
//
// This page carries its own VERSION, matching the pattern on /privacy and
// /terms. It is DISPLAY ONLY and is not `settings.terms_version`: the refund
// policy is not part of the terms-acceptance gate, so changing it must not
// prompt every account to re-accept. Keep the date honest anyway — a customer
// arguing about a refund is entitled to see which version they bought under.
export const metadata = { title: "Refunds — The Scout Report" };

const VERSION = "2026-08-07";

export default function RefundsPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      <main className="mx-auto w-full max-w-[840px] flex-1 px-3 py-8 sm:px-6">
        <article className="card p-5 sm:p-8">
          <h1 className="m-0 text-title font-bold sm:text-title-lg">Refunds</h1>
          <p className="mt-1 text-eyebrow uppercase text-ink-700">Version {VERSION}</p>

          <div className="mt-5 max-w-measure space-y-4 text-prose text-ink-100 sm:text-prose-lg">
            <p className="m-0">
              Short version: if you bought this and it is not what you wanted, tell us
              within 14 days and you get your money back. You do not have to explain
              yourself.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">
              Your first purchase — 14 days, no questions
            </h2>
            <p className="m-0">
              The first time you pay us anything, on either plan, you have{" "}
              <strong>14 days to ask for a full refund for any reason or none</strong>. We
              do not ask why, we do not offer you a discount to stay, and we do not count
              how many briefs you read first.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">
              Monthly renewals — 7 days
            </h2>
            <p className="m-0">
              After the first month, each renewal can be refunded if you ask{" "}
              <strong>within 7 days of that charge</strong>. That covers the common case:
              you meant to cancel, the season got away from you, and the payment went
              through anyway.
            </p>
            <p className="m-0">
              Past 7 days we do not refund a renewal you have already used, but you can
              cancel at any moment and you will not be charged again — see{" "}
              <Link href="/terms" className="underline">
                the terms
              </Link>
              .
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">
              The season pass — the same 14 days
            </h2>
            <p className="m-0">
              The season pass is one payment, so it gets the same{" "}
              <strong>14-day window from the day you bought it</strong>, in full. It is a
              larger up-front amount than a month, and a longer window on a bigger payment
              is the fair way round.
            </p>
            <p className="m-0">
              After 14 days the pass is not refundable, including if the season is
              disrupted or if we stop publishing — in that last case we would refund the
              unused part anyway, because we would not have delivered what you bought.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">
              How to ask, and what happens
            </h2>
            <p className="m-0">
              Email{" "}
              <a href="mailto:hello@thescoutreport.fpl" className="underline">
                hello@thescoutreport.fpl
              </a>{" "}
              from the address you paid with, or reply to any brief. Tell us you want a
              refund; that is enough. We aim to reply within two working days.
            </p>
            <p className="m-0">
              <strong>Paddle is the merchant of record for every payment</strong>, which
              means Paddle — not us — actually took the money and Paddle actually issues
              the refund. We never see or hold your card details. Once approved, the money
              goes back to the card or account you paid with, and how long it takes after
              that is your bank&apos;s business, typically a few working days. You can also
              request a refund directly from Paddle using the receipt they emailed you.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">
              What happens to your access
            </h2>
            <p className="m-0">
              <strong>A refund ends paid access immediately.</strong> The paid sections
              lock again as soon as the refund is processed, and any subscription is
              cancelled at the same time so you are not charged again. You do not get to
              keep the rest of the period you were refunded for — that is what being
              refunded means.
            </p>
            <p className="m-0">
              You keep your account, and you keep the free weekly brief, which never
              required paying in the first place. Refunding does not delete anything or
              unsubscribe you from the emails; both of those are separate and are on{" "}
              <Link href="/account" className="underline">
                your account page
              </Link>
              .
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">
              The one limit
            </h2>
            <p className="m-0">
              We will refund a repeat purchase from the same person once. If someone buys,
              refunds, and buys again in a loop, we will decline the third and tell them
              why rather than quietly stopping. Nothing in this paragraph affects your
              statutory rights, and if you are a consumer, your local consumer law wins
              wherever it says something different from this page.
            </p>
          </div>
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}
