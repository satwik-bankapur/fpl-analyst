import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDraftBrief, Brief } from "@/lib/api";
import BriefView from "@/components/BriefView";
import EmailCapture from "@/components/EmailCapture";
import RefreshButton from "@/components/RefreshButton";
import { SiteHeader, SiteFooter } from "@/components/Chrome";

export const dynamic = "force-dynamic";

async function load(id: string): Promise<Brief | null> {
  try {
    return await getDraftBrief(id);
  } catch {
    return null;
  }
}

// OG tags matter — briefs get pasted into group chats (§2.2).
export async function generateMetadata({ params }: { params: { draftId: string } }): Promise<Metadata> {
  const brief = await load(params.draftId);
  const when = brief && brief.gameweek > 0 ? `GW${brief.gameweek}` : "pre-season";
  const title = brief ? `${brief.team_name || "Draft squad"} — ${when} scout report` : "FPL scout report";
  const description = brief
    ? `A scout report on ${brief.team_name || "this draft"}: the verdict, the transfer, the captain.`
    : "A personalized FPL scout report.";
  return {
    title,
    description,
    openGraph: { title, description, type: "article", siteName: "The Scout Report" },
    twitter: { card: "summary_large_image", title, description },
  };
}

export default async function DraftBriefPage({ params }: { params: { draftId: string } }) {
  const brief = await load(params.draftId);
  if (!brief) notFound();

  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />

      <main className="mx-auto w-full max-w-[840px] flex-1 px-3 py-3 sm:px-6 sm:py-8">
        <BriefView brief={brief} />

        <div className="card mt-3.5 p-5 sm:p-6">
          <div className="mb-2.5 text-[15px] font-semibold">Want this every Friday?</div>
          <EmailCapture source="draft_brief" />
          <div className="mt-2">
            <RefreshButton className="w-full py-2.5" />
          </div>
          <p className="m-0 mt-3 text-label leading-relaxed text-ink-600">
            Written from public FPL data and this week&apos;s fixtures. A considered opinion, not a projection —
            the scout will be wrong sometimes, and will say so next week.
          </p>
        </div>
      </main>

      <SiteFooter />
    </div>
  );
}
