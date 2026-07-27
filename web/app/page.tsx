import { getHealth, getPreview, Health, Preview } from "@/lib/api";
import HomeHero from "@/components/HomeHero";
import PreviewTeaser from "@/components/PreviewTeaser";
import EmailCapture from "@/components/EmailCapture";
import { SiteHeader, SiteFooter } from "@/components/Chrome";

export const dynamic = "force-dynamic"; // health/preview are live

export default async function Home() {
  const [h, p] = await Promise.allSettled([getHealth(), getPreview()]);
  // Pre-season is the Stage A default if health is unreachable.
  const health = h.status === "fulfilled" ? (h.value as Health) : null;
  const mode = health?.mode ?? "PRESEASON";
  const preview = p.status === "fulfilled" ? (p.value as Preview) : null;

  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />

      <main className="mx-auto w-full max-w-[1180px] flex-1">
        <HomeHero mode={mode} nextGw={health?.next_gw} deadline={health?.next_deadline} />

        {/* Cards below the fold: the preview, then pricing + email capture. */}
        <div className="grid gap-3.5 px-3 pb-10 sm:grid-cols-[minmax(0,7fr)_minmax(0,5fr)] sm:items-stretch sm:px-12">
          {preview ? (
            <PreviewTeaser preview={preview} />
          ) : (
            <div className="card p-5 text-body text-ink-700 sm:p-6">
              This week&apos;s preview will appear here once the first brief of the gameweek is written.
            </div>
          )}

          <div className="card flex flex-col gap-2.5 p-5 sm:p-6">
            <div className="text-label font-medium text-ink-600">Simple pricing</div>
            <p className="m-0 text-[15px] leading-[1.55] text-ink-100">
              <strong className="font-semibold">$3.99/mo</strong> — every section unlocked, cancel any week.
            </p>
            <p className="m-0 text-[15px] leading-[1.55] text-ink-100">
              <strong className="font-semibold">$19 the season</strong> — everything through GW38.
            </p>
            <p className="m-0 text-[15px] leading-[1.55] text-ink-600">One brief a week stays free, forever.</p>
            <div className="mt-auto pt-3">
              <p className="m-0 mb-2 text-label text-ink-600">One email, Friday morning. No fixture tickers.</p>
              <EmailCapture source="landing" />
            </div>
          </div>
        </div>
      </main>

      <SiteFooter />
    </div>
  );
}
