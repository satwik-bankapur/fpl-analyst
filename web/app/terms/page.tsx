import Link from "next/link";
import { SiteHeader, SiteFooter } from "@/components/Chrome";

// WRITTEN 2026-08-07, replacing the TODO placeholder that existed only so
// Paddle's live-account review had something to load (docs/LAUNCH-CHECKLIST.md).
//
// DRAFT — THE OWNER MUST READ THIS BEFORE LAUNCH. It is written in the same
// plain register as /privacy and describes what this product actually does, but
// it is not legal advice and nobody with a practising certificate has seen it.
// The clauses most worth a second opinion are the liability limitation and the
// governing-law line.
//
// VERSION must stay equal to `settings.terms_version` (api/config.py,
// TERMS_VERSION) and to the version on /privacy. All three moved from 2026-08-04
// to 2026-08-07 in the same commit, and the bump is deliberate rather than
// cosmetic: the previous text was a TODO, so an account recorded as having
// accepted "2026-08-04" accepted a placeholder. Treating that as agreement to
// these words would be a fabricated consent record.
export const metadata = { title: "Terms — The Scout Report" };

const VERSION = "2026-08-07";

export default function TermsPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      <main className="mx-auto w-full max-w-[840px] flex-1 px-3 py-8 sm:px-6">
        <article className="card p-5 sm:p-8">
          <h1 className="m-0 text-title font-bold sm:text-title-lg">Terms of Service</h1>
          <p className="mt-1 text-eyebrow uppercase text-ink-700">Version {VERSION}</p>

          <div className="mt-5 max-w-measure space-y-4 text-prose text-ink-100 sm:text-prose-lg">
            <p className="m-0">
              These terms cover The Scout Report, a site that reads public Fantasy Premier
              League data and writes you an opinion about your squad. Using the site means
              you accept them. If you create an account we ask you to accept them
              explicitly, and we ask again if they change.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">
              What this is, and what it is not
            </h2>
            <p className="m-0">
              This is analysis and opinion. Every brief is a projection built from public
              data by a scoring model we wrote, and it is regularly wrong. Players get
              injured after we publish, managers rotate without warning, and a heavily
              projected captain blanks several times a season.
            </p>
            <p className="m-0">
              <strong>We do not guarantee points, rank, or any result at all.</strong>{" "}
              Nothing here is financial advice, betting advice, or a tip. Every decision
              about your team is yours, and so is the outcome — if you would not have made
              a transfer without us, it is still your transfer.
            </p>
            <p className="m-0">
              We are not affiliated with, endorsed by, or connected to the Premier League
              or Fantasy Premier League. FPL data is read from their public API and belongs
              to them; club and player names are their owners&apos; property.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">The two plans</h2>
            <p className="m-0">
              One brief a week is free and needs no account. Beyond that there are two paid
              options, and both unlock the same thing: every section of every brief, rather
              than the free subset.
            </p>
            <p className="m-0">
              The <strong>monthly plan</strong> renews each month until you cancel. The{" "}
              <strong>season pass</strong> is a single payment covering the rest of the
              current season, and it does not renew — when the season ends, it ends, and we
              do not charge you again.
            </p>
            <p className="m-0">
              Prices are shown before you pay. If we change the price of the monthly plan,
              we tell you before it affects your subscription, and you can cancel instead.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">Cancelling</h2>
            <p className="m-0">
              You can cancel the monthly plan whenever you like, from your Paddle receipt
              or by asking us. <strong>Cancelling stops the next payment; it does not cut
              off what you have already paid for.</strong> Your paid sections stay unlocked
              until the end of the period you bought, and then stop. We do not refund part
              of a period on cancellation, except where the{" "}
              <Link href="/refunds" className="underline">
                refund policy
              </Link>{" "}
              says otherwise.
            </p>
            <p className="m-0">
              Unsubscribing from the emails is a different thing and never cancels a
              subscription. Deleting your account does not cancel one either — that goes
              through Paddle, and we will do it for you if you ask.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">
              Using the site sensibly
            </h2>
            <p className="m-0">
              Please do not scrape the site, hammer it with automated requests, resell or
              republish the briefs as your own, add other people to our mailing list, or
              try to reach paid sections without paying for them. Do not use the site to
              break the law or to interfere with anyone else&apos;s use of it.
            </p>
            <p className="m-0">
              We can suspend or close an account doing those things. If we close your
              account for any reason other than your own breach, we refund the unused part
              of what you paid.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">Availability</h2>
            <p className="m-0">
              This is a small operation, not an enterprise service. We aim to publish every
              week, but we do not promise the site is always up, always current, or always
              right. The data depends on the public FPL API, and when that is down or
              changes shape, our briefs go stale or stop until we fix it.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">Liability</h2>
            <p className="m-0">
              To the fullest extent the law allows, we are not liable for indirect or
              consequential loss, for lost rank, for mini-league winnings, or for any
              decision you made after reading a brief. Where we are liable, our total
              liability is limited to what you actually paid us in the twelve months before
              the claim — which, for a free reader, is nothing.
            </p>
            <p className="m-0">
              Nothing here limits liability that cannot lawfully be limited, including for
              fraud or for death or personal injury caused by negligence. If you are a
              consumer, you keep every right your local consumer law gives you and nothing
              in these terms removes them.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">Age</h2>
            <p className="m-0">
              You need to be at least 13 to use the site, and if you are under 18 you need
              a parent or guardian&apos;s permission before paying for anything. We do not
              knowingly take money from anyone younger; if we find that we have, we refund
              it and delete the account.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">
              Changes, and the law that applies
            </h2>
            <p className="m-0">
              We may update these terms. When we do, the version date at the top changes
              and account holders are asked to accept the new version before using paid
              features. Continuing to use the free site after a change means you accept it.
            </p>
            <p className="m-0">
              These terms are governed by the laws of India, and the courts of India have
              jurisdiction over any dispute arising from them. If you are a consumer
              elsewhere, this does not deprive you of the protection of your own
              country&apos;s mandatory consumer law.
            </p>
            <p className="m-0">
              Questions, complaints, or anything here that reads wrong to you:{" "}
              <a href="mailto:hello@thescoutreport.fpl" className="underline">
                hello@thescoutreport.fpl
              </a>
              . See also{" "}
              <Link href="/privacy" className="underline">
                privacy
              </Link>{" "}
              and{" "}
              <Link href="/refunds" className="underline">
                refunds
              </Link>
              .
            </p>
          </div>
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}
