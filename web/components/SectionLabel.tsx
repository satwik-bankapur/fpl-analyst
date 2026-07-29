import { PREVIEW_TITLES, SECTION_TITLES } from "@/lib/api";

// One 14px icon per brief section, traced from the design's inline SVGs.
// Keyed by the API's section keys — an unknown key falls back to the clipboard,
// so a new backend section renders sensibly without a frontend change.
const PATHS: Record<string, JSX.Element> = {
  verdict: (
    <>
      <rect x="5" y="4" width="14" height="17" rx="2" />
      <path d="M9 4h6v3H9z" />
      <path d="M9 12h6M9 16h4" />
    </>
  ),
  transfer: (
    <>
      <path d="M4 8h14l-3-3" />
      <path d="M20 16H6l3 3" />
    </>
  ),
  captaincy: <path d="M12 3l2.7 5.6 6.1.8-4.5 4.2 1.1 6-5.4-2.9-5.4 2.9 1.1-6L3.2 9.4l6.1-.8z" />,
  bench_plan: (
    <>
      <path d="M3 17h18" />
      <path d="M5 17v-5h14v5" />
      <path d="M8 8h8" />
    </>
  ),
  chips: (
    <>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  watchlist: (
    <>
      <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  // Calendar — serves the preview's fixtures section and any legacy `fixtures`
  // key on a brief persisted before the 2026-07-30 vocabulary unification.
  fixtures: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </>
  ),
  form_and_fixtures: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </>
  ),
  locked: (
    <>
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V7a4 4 0 018 0v4" />
    </>
  ),
};

export function SectionIcon({ name, className }: { name: string; className?: string }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      {PATHS[name] ?? PATHS.verdict}
    </svg>
  );
}

/** Brief titles first, then the preview vocabulary. Anything still unknown reads
 *  as prose rather than as an identifier: "some_new_key" -> "Some new key". */
export function sectionTitle(key: string): string {
  const known = SECTION_TITLES[key] ?? PREVIEW_TITLES[key];
  if (known) return known;
  const words = key.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Sentence case, never uppercase — "labels whisper, prose speaks" (design §05). */
export default function SectionLabel({ sectionKey }: { sectionKey: string }) {
  return (
    <div className="mb-2.5 flex items-center gap-[7px] text-label font-medium text-ink-600">
      <SectionIcon name={sectionKey} />
      <span>{sectionTitle(sectionKey)}</span>
    </div>
  );
}
