import Link from "next/link";
import { SiteHeader, SiteFooter } from "@/components/Chrome";

// The footer links here from every page, and the product collects email
// addresses, so this exists to keep that link honest. Nothing more.
//
// REWRITTEN 2026-08-04 (accounts build). The previous version opened with "There
// are no accounts and no logins", which stopped being true the moment this branch
// landed. A privacy policy that describes a product you no longer ship is worse
// than no policy: it is a specific false statement about what you hold.
//
// The version string below is `settings.terms_version` (api/config.py). BUMP BOTH
// TOGETHER — the server prompts every account to re-accept when the config value
// changes, and a bump with no change here asks people to re-agree to the same words.
//
// 2026-08-07: moved to match the terms bump. THIS PAGE'S WORDS DID NOT CHANGE —
// the one shared version string covers both documents, so /terms going from a
// TODO placeholder to real terms moves this number too. Worth knowing before
// hunting for what changed here: nothing did.
export const metadata = { title: "Privacy — The Scout Report" };

const VERSION = "2026-08-07";

export default function PrivacyPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      <main className="mx-auto w-full max-w-[840px] flex-1 px-3 py-8 sm:px-6">
        <article className="card p-5 sm:p-8">
          <h1 className="m-0 text-title font-bold sm:text-title-lg">Privacy</h1>
          <p className="mt-1 text-eyebrow uppercase text-ink-700">Version {VERSION}</p>

          <div className="mt-5 max-w-measure space-y-4 text-prose text-ink-100 sm:text-prose-lg">
            <p className="m-0">
              The weekly brief is free and needs no account. You can read it, submit a
              draft squad and get a report without ever signing in, and nothing on that
              path asks you to.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">
              If you create an account
            </h2>
            <p className="m-0">
              An account exists for one reason: to unlock the sections you pay for. We
              store your email address, which sign-in method you used, when you agreed to
              this policy and which version, when you last signed in, and your active
              sign-in sessions. Sign-in links and session tokens are stored only as
              one-way hashes — a copy of our database contains nothing anyone could sign
              in with.
            </p>
            <p className="m-0">
              We do not store a password, because there isn&apos;t one. You sign in with a
              single-use link mailed to you, or with Google.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">
              Google sign-in
            </h2>
            <p className="m-0">
              If you choose &ldquo;Continue with Google&rdquo;, Google acts as a data
              processor for that sign-in: they confirm to us that you control the address
              and give us a stable account identifier. We ask for the{" "}
              <code className="text-meta">openid email</code> scopes and nothing else — no
              contacts, no calendar, no Drive, and no permission to act on your behalf. We
              store the address and that identifier. Google&apos;s own handling of the
              sign-in is covered by{" "}
              <a
                href="https://policies.google.com/privacy"
                className="underline"
                rel="noopener noreferrer"
                target="_blank"
              >
                their privacy policy
              </a>
              .
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">Payments</h2>
            <p className="m-0">
              Payments are handled by Paddle, who act as merchant of record. We never see
              or store your card details. We keep the subscription record they send us —
              plan, status, billing period and their customer reference — because it is
              what decides whether your paid sections unlock, and because it is a
              financial record we are required to retain.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">Email and data</h2>
            <p className="m-0">
              Addresses are used to send you briefs and sign-in links, and for nothing
              else — no advertising, no resale, no sharing. Squads you submit are stored so
              your brief has a link you can return to. FPL data comes from the public
              Fantasy Premier League API; we never ask for your FPL password. Our
              operational logs never contain your email address in full.
            </p>

            <h2 className="m-0 pt-2 text-body font-semibold text-ink-0">
              Leaving, and deleting
            </h2>
            <p className="m-0">
              Every email carries a one-click unsubscribe, and unsubscribing never costs
              you access you paid for — mail consent and billing are separate on purpose.
              To delete everything, use{" "}
              <Link href="/account" className="underline">
                your account page
              </Link>
              , or mail{" "}
              <a href="mailto:hello@thescoutreport.fpl" className="underline">
                hello@thescoutreport.fpl
              </a>
              . Deleting removes your account, your sessions, your sign-in links and your
              mailing-list entry. Billing records are kept but anonymised — they no longer
              name you. <strong>Deleting your account does not cancel a subscription</strong>
              ; cancel that from your Paddle receipt, or ask us and we&apos;ll do it.
            </p>
          </div>
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}
