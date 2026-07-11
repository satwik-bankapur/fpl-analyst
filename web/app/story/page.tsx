import BriefView from "@/components/BriefView";
import { Brief } from "@/lib/api";

// Dev story (not linked from the product). Renders BriefView twice from hardcoded
// payloads to prove the SAME component handles both states with no code change:
//  - Stage A: locked_sections: []  -> everything rendered
//  - Stage B: locked_sections populated -> lock cards for the paid sections
// This is the only thing that exercises LockCard until the Stage B paywall flips.
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

export default function StoryPage() {
  return (
    <main className="mx-auto max-w-2xl space-y-16 px-6 py-12">
      <div>
        <p className="mb-2 text-xs font-semibold uppercase text-slate-400">Stage A — nothing locked</p>
        <BriefView brief={stageA} />
      </div>
      <div>
        <p className="mb-2 text-xs font-semibold uppercase text-slate-400">Stage B — paid sections locked</p>
        <BriefView brief={stageB} />
      </div>
    </main>
  );
}
