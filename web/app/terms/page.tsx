import { SiteHeader, SiteFooter } from "@/components/Chrome";

// PLACEHOLDER — routing and the footer link exist so the prose is the only thing
// left to write. Paddle's live-account review requires this page to be real and
// reachable before it will approve the account (docs/LAUNCH-CHECKLIST.md).
//
// VERSION must stay equal to `settings.terms_version` (api/config.py, TERMS_VERSION)
// and to the version on /privacy. Bump all three in the same commit — the server
// prompts every account to re-accept when the config value changes.
export const metadata = { title: "Terms — The Scout Report" };

const VERSION = "2026-08-04";

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
              TODO — write the terms. Cover at minimum: what the service is and what it is
              not (analysis, not advice), the two paid plans and what each grants, that
              the brief is generated from public FPL data and may be wrong, acceptable
              use, account termination, limitation of liability, and governing law.
            </p>
          </div>
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}
