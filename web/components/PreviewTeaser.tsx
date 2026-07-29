import { Preview, PREVIEW_ORDER } from "@/lib/api";
import Md from "@/components/Md";
import SectionLabel from "@/components/SectionLabel";

// Below-the-fold teaser of the latest generic preview (§2.1). Server-rendered.
// Design §01: the fade at the foot is the tell that there is more to read.
//
// Ordered by PREVIEW_ORDER, not SECTION_ORDER. Until 2026-07-30 this searched the
// BRIEF vocabulary, which shares no key with the preview — so the lookup never
// matched and it silently fell through to whatever key happened to be first.
export default function PreviewTeaser({ preview }: { preview: Preview }) {
  const key = PREVIEW_ORDER.find((k) => k in preview.sections) ?? Object.keys(preview.sections)[0];
  const text = key ? preview.sections[key] : "";

  return (
    <div className="card p-5 sm:p-6">
      <div className="mb-3.5 flex items-start justify-between gap-3">
        <div>
          <div className="text-[16px] font-bold sm:text-[17px]">This week&apos;s preview</div>
          <div className="mt-0.5 text-label text-ink-600">Gameweek {preview.gameweek} · free, for everyone</div>
        </div>
      </div>
      {key && <SectionLabel sectionKey={key} />}
      <div className="relative max-h-[168px] overflow-hidden">
        <Md text={text} />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-14 bg-gradient-to-b from-transparent to-surface-800" />
      </div>
    </div>
  );
}
