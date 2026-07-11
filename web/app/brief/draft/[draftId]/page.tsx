import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getDraftBrief, Brief } from "@/lib/api";
import BriefView from "@/components/BriefView";
import EmailCapture from "@/components/EmailCapture";
import RefreshButton from "@/components/RefreshButton";

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
  const title = brief ? `${brief.team_name || "Draft squad"} — GW${brief.gameweek} scout report` : "FPL scout report";
  return { title, openGraph: { title, description: "A personalized FPL scout report." } };
}

export default async function DraftBriefPage({ params }: { params: { draftId: string } }) {
  const brief = await load(params.draftId);
  if (!brief) notFound();

  return (
    <main className="mx-auto max-w-2xl px-6 py-12">
      <BriefView brief={brief} />
      <div className="mx-auto mt-8 max-w-2xl rounded-xl border border-slate-200 bg-white p-6">
        <h2 className="text-lg font-semibold">Get this in your inbox every gameweek</h2>
        <div className="mt-3 flex flex-col gap-3">
          <EmailCapture />
          <RefreshButton />
        </div>
      </div>
    </main>
  );
}
