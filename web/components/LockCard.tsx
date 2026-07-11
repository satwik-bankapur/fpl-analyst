// Paid-section placeholder. Rendered for any key in `locked_sections`. In Stage A
// no response ever locks a section (locked_sections is always []), so this is
// exercised only by the /story route until the Stage B paywall flips — proving the
// flip is data-driven with zero component changes.
export default function LockCard({ title }: { title: string }) {
  return (
    <section className="my-4 rounded-lg border border-dashed border-slate-300 bg-slate-100/70 p-5">
      <div className="flex items-center gap-2 text-slate-500">
        <span aria-hidden>🔒</span>
        <h3 className="text-lg font-semibold">{title}</h3>
      </div>
      <p className="mt-1 text-sm text-slate-500">
        Part of the full scout report — bench order, chip plan and watchlist.
      </p>
      <button
        type="button"
        className="mt-3 rounded-md bg-pitch px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        Unlock the full report
      </button>
    </section>
  );
}
