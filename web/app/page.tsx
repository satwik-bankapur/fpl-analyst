import { getHealth, getPreview, Health, Preview } from "@/lib/api";
import HomeHero from "@/components/HomeHero";
import PreviewTeaser from "@/components/PreviewTeaser";
import EmailCapture from "@/components/EmailCapture";

export const dynamic = "force-dynamic"; // health/preview are live

export default async function Home() {
  const [h, p] = await Promise.allSettled([getHealth(), getPreview()]);
  // Pre-season is the Stage A default if health is unreachable.
  const mode = (h.status === "fulfilled" ? (h.value as Health).mode : "PRESEASON");
  const preview = p.status === "fulfilled" ? (p.value as Preview) : null;

  return (
    <main className="mx-auto max-w-4xl px-6 py-16">
      <HomeHero mode={mode} />

      <section className="mt-16 grid gap-6 md:grid-cols-2">
        {preview ? <PreviewTeaser preview={preview} /> : (
          <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-400">
            The weekly preview will appear here.
          </div>
        )}
        <div className="rounded-xl border border-slate-200 bg-white p-6">
          <h2 className="text-lg font-semibold">The free weekly preview, in your inbox</h2>
          <p className="mb-3 mt-1 text-sm text-slate-500">
            Differentials, traps and fixture swings — every gameweek.
          </p>
          <EmailCapture />
        </div>
      </section>

      <section className="mt-12 rounded-xl bg-pitch p-6 text-emerald-50">
        <h2 className="text-lg font-semibold text-white">Go deeper when the season starts</h2>
        <ul className="mt-2 space-y-1 text-sm">
          <li>· Free every week: verdict, one transfer, captaincy.</li>
          <li>· Full report ($3.99/mo): bench order, 4-GW plan, chips, watchlist.</li>
          <li>· Season pass: the full report every gameweek, one payment.</li>
        </ul>
      </section>

      <footer className="mt-16 border-t border-slate-200 pt-6 text-sm text-slate-400">
        <p>hello@fplanalyst.example · Privacy-light, no accounts.</p>
        <p>Not affiliated with the Premier League.</p>
      </footer>
    </main>
  );
}
