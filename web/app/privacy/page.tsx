import { SiteHeader, SiteFooter } from "@/components/Chrome";

// The footer links here from every page, and the product collects email
// addresses, so this exists to keep that link honest. Nothing more.
export const metadata = { title: "Privacy — The Scout Report" };

export default function PrivacyPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      <main className="mx-auto w-full max-w-[840px] flex-1 px-3 py-8 sm:px-6">
        <article className="card p-5 sm:p-8">
          <h1 className="m-0 text-title font-bold sm:text-title-lg">Privacy</h1>
          <div className="mt-5 max-w-measure space-y-4 text-prose text-ink-100 sm:text-prose-lg">
            <p className="m-0">
              There are no accounts and no logins. If you give us an email address we store it to send you
              briefs, and for nothing else — no advertising, no resale, no sharing.
            </p>
            <p className="m-0">
              Squads you submit are stored so your brief has a link you can return to. FPL data comes from the
              public Fantasy Premier League API; we never ask for your FPL password.
            </p>
            <p className="m-0">
              Every email carries a one-click unsubscribe. To have your address and drafts deleted, mail{" "}
              <a href="mailto:hello@thescoutreport.fpl" className="underline">
                hello@thescoutreport.fpl
              </a>{" "}
              and it&apos;s done.
            </p>
          </div>
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}
