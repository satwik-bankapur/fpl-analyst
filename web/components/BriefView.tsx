import { Brief, SECTION_ORDER } from "@/lib/api";
import Md from "@/components/Md";
import LockCard from "@/components/LockCard";
import SectionLabel, { sectionTitle } from "@/components/SectionLabel";

// Renders the header card + sections. Data-driven: a section key present in
// `sections` renders its content; a key in `locked_sections` renders a LockCard.
// Stage A responses carry locked_sections: [] (all unlocked); Stage B populates
// it and the exact same component shows lock cards — no code change (§amendment).
//
// Layout follows design §04: everything lives in one card, the chrome stays
// dense, and the prose inside it does not (16/17px at 1.7 on a 64ch measure).

/** Consecutive locked keys collapse into a single lock card, as in the design. */
type Run = { kind: "section"; key: string } | { kind: "locked"; keys: string[] };

function toRuns(keys: string[], locked: string[]): Run[] {
  const runs: Run[] = [];
  for (const key of keys) {
    if (!locked.includes(key)) {
      runs.push({ kind: "section", key });
      continue;
    }
    const last = runs[runs.length - 1];
    if (last?.kind === "locked") last.keys.push(key);
    else runs.push({ kind: "locked", keys: [key] });
  }
  return runs;
}

const words = (s: string) => s.trim().split(/\s+/).length;

export default function BriefView({ brief }: { brief: Brief }) {
  const keys = Array.from(
    new Set([...SECTION_ORDER, ...Object.keys(brief.sections), ...brief.locked_sections])
  ).filter((k) => k in brief.sections || brief.locked_sections.includes(k));

  const runs = toRuns(keys, brief.locked_sections);
  const wordCount = Object.values(brief.sections).reduce((n, s) => n + words(s), 0);
  const minutes = Math.max(1, Math.round(wordCount / 220));

  return (
    <article className="card p-5 sm:p-8">
      <header>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="m-0 text-title font-bold tracking-[-0.01em] text-ink-0 sm:text-title-lg">
              {brief.team_name || "Your draft squad"}
            </h1>
            <p className="mt-1 text-label leading-normal text-ink-600 sm:text-body">
              {/* gameweek 0 is the API's pre-season anchor — "Gameweek 0" is not a thing. */}
              {brief.gameweek > 0 ? `Gameweek ${brief.gameweek} brief` : "Pre-season draft brief"}
              {brief.overall_rank != null && ` · overall rank ${brief.overall_rank.toLocaleString()}`}
            </p>
          </div>
          {brief.bank != null && (
            <span className="shrink-0 rounded-full bg-state-bluebg px-2.5 py-1.5 text-eyebrow font-semibold tracking-normal text-state-blue">
              £{brief.bank.toFixed(1)}m ITB
            </span>
          )}
        </div>
        <div className="mt-3.5 flex flex-wrap gap-x-3.5 gap-y-1 border-t border-line pt-3.5 text-label text-ink-700">
          <span>Reading time {minutes} min</span>
          <span aria-hidden>·</span>
          <span>
            {keys.length} section{keys.length === 1 ? "" : "s"}
          </span>
          {brief.cached && (
            <>
              <span aria-hidden>·</span>
              <span>From cache</span>
            </>
          )}
        </div>
      </header>

      <div className="mt-6">
        {runs.map((run, i) =>
          run.kind === "section" ? (
            <section key={run.key} className={i > 0 ? "mt-6" : undefined}>
              <SectionLabel sectionKey={run.key} />
              <Md text={brief.sections[run.key]} />
            </section>
          ) : (
            <LockCard
              key={`locked-${run.keys.join("-")}`}
              titles={run.keys.map(sectionTitle)}
            />
          )
        )}
      </div>
    </article>
  );
}
