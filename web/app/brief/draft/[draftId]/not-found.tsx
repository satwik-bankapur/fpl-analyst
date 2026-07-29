import { SiteHeader, SiteFooter } from "@/components/Chrome";
import NotFoundState from "@/components/NotFoundState";

// Rendered when getDraftBrief() 404s — an unknown or expired draft link.
export default function DraftNotFound() {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      <main className="mx-auto flex w-full max-w-[560px] flex-1 flex-col">
        <NotFoundState kicker="No brief · unknown draft" title="That link doesn't point to a brief.">
          <p className="m-0">
            Draft links are minted when a squad is submitted. If you typed or trimmed this one it won&apos;t
            resolve, and drafts from an older gameweek stop resolving once the season moves on.
          </p>
          <p className="m-0 mt-3">Building a new one takes about a minute and costs nothing.</p>
        </NotFoundState>
      </main>
      <SiteFooter />
    </div>
  );
}
