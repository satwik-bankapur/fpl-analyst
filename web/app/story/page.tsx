import BriefView from "@/components/BriefView";
import LoadingState from "@/components/LoadingState";
import ErrorState from "@/components/ErrorState";
import { Brief } from "@/lib/api";
import { SiteHeader, Eyebrow } from "@/components/Chrome";

// Dev story (not linked from the product). Renders BriefView twice from hardcoded
// payloads to prove the SAME component handles both states with no code change:
//  - Stage A: locked_sections: []  -> everything rendered
//  - Stage B: locked_sections populated -> lock cards for the paid sections
// This is the only thing that exercises LockCard until the Stage B paywall flips.
// The loading and error states are pinned here too, since neither is reachable
// without either a 50-second cold start or a broken backend.
const base: Omit<Brief, "sections" | "locked_sections"> = {
  brief_id: 1, draft_id: "demo", gameweek: 1,
  team_name: "Story FC", overall_rank: 42000, bank: 1.5, cached: true,
};

const stageA: Brief = {
  ...base,
  sections: {
    verdict: "- Projected XI: 62 pts this GW.\n- Problems flagged: 1. Chip stance: hold.",
    transfer: "- ROLL: no move clears the +2.0 bar; bank the transfer.",
    captaincy: "- Captain: Haaland (MCI). Vice: Salah (LIV).",
    bench_plan: "- Sels → Richarlison → Gvardiol → Kluivert",
    watchlist: "- Mbeumo (BRE)",
  },
  locked_sections: [],
};

const stageB: Brief = {
  ...base,
  team_name: "Story FC (paywalled)",
  sections: {
    verdict: stageA.sections.verdict,
    transfer: stageA.sections.transfer,
    captaincy: stageA.sections.captaincy,
  },
  locked_sections: ["bench_plan", "chips", "watchlist"],
};

function Panel({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section>
      <div className="mb-3">
        <Eyebrow>{label}</Eyebrow>
      </div>
      {children}
    </section>
  );
}

export default function StoryPage() {
  return (
    <div className="min-h-screen">
      <SiteHeader right={<span className="text-label text-ink-700">/story · dev</span>} />
      <main className="mx-auto w-full max-w-[840px] space-y-14 px-3 py-8 sm:px-6">
        <Panel label="Brief · Stage A — nothing locked">
          <BriefView brief={stageA} />
        </Panel>
        <Panel label="Brief · Stage B — paid sections locked">
          <BriefView brief={stageB} />
        </Panel>
        <Panel label="Long wait — cold start note appears at 10s">
          <div className="card flex min-h-[520px] flex-col">
            <LoadingState gameweek={1} />
          </div>
        </Panel>
        <Panel label="Error — the brief didn't come back">
          <div className="card flex min-h-[360px] flex-col">
            <ErrorState />
          </div>
        </Panel>
      </main>
    </div>
  );
}
