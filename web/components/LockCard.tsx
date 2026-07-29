import { SectionIcon } from "@/components/SectionLabel";

// Paid-section placeholder. Rendered for any key in `locked_sections`. In Stage A
// no response ever locks a section (locked_sections is always []), so this is
// exercised only by the /story route until the Stage B paywall flips — proving the
// flip is data-driven with zero component changes.
//
// Design §04: one card standing in for a run of locked sections, naming exactly
// what is behind it. `titles` comes straight from the locked keys, so the card
// always tells the truth about which sections the API withheld.
export default function LockCard({ titles }: { titles: string[] }) {
  return (
    <section className="mt-6 rounded-[11px] border border-line-strong bg-surface-900 p-4 text-center sm:p-5">
      <div className="mb-1.5 flex items-center justify-center gap-[7px] text-body font-semibold text-ink-300">
        <SectionIcon name="locked" />
        <h2 className="m-0 text-body font-semibold">In the full brief</h2>
      </div>
      <p className="m-0 mb-3.5 text-body leading-relaxed text-ink-600">{titles.join(" · ")}</p>
      <button type="button" className="btn-ghost border-line-strong bg-surface-700 px-5 hover:bg-surface-600">
        Unlock weekly briefs · $3.99/mo
      </button>
      <p className="m-0 mt-2.5 text-eyebrow leading-normal tracking-normal text-ink-700">
        or $19 for the whole season
      </p>
    </section>
  );
}
