import { Brief, SECTION_ORDER, SECTION_TITLES } from "@/lib/api";
import Md from "@/components/Md";
import LockCard from "@/components/LockCard";

// Renders the header card + sections. Data-driven: a section key present in
// `sections` renders its content; a key in `locked_sections` renders a LockCard.
// Stage A responses carry locked_sections: [] (all unlocked); Stage B populates
// it and the exact same component shows lock cards — no code change (§amendment).
export default function BriefView({ brief }: { brief: Brief }) {
  const keys = Array.from(
    new Set([...SECTION_ORDER, ...Object.keys(brief.sections), ...brief.locked_sections])
  ).filter((k) => k in brief.sections || brief.locked_sections.includes(k));

  return (
    <article className="mx-auto max-w-2xl">
      <header className="rounded-xl bg-pitch p-6 text-white">
        <h1 className="text-2xl font-bold">{brief.team_name || "Your draft squad"}</h1>
        <p className="mt-1 text-sm text-emerald-100">
          GW{brief.gameweek} scout report
          {brief.overall_rank != null && ` · Rank ${brief.overall_rank.toLocaleString()}`}
          {brief.bank != null && ` · Bank £${brief.bank.toFixed(1)}m`}
        </p>
      </header>

      <div className="mt-4">
        {keys.map((key) =>
          key in brief.sections ? (
            <section key={key} className="my-4 rounded-lg border border-slate-200 bg-white p-5">
              <h2 className="text-lg font-semibold text-ink">{SECTION_TITLES[key] || key}</h2>
              <Md text={brief.sections[key]} />
            </section>
          ) : (
            <LockCard key={key} title={SECTION_TITLES[key] || key} />
          )
        )}
      </div>
    </article>
  );
}
