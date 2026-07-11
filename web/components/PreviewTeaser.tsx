import { Preview } from "@/lib/api";
import Md from "@/components/Md";

// Below-the-fold teaser of the latest generic preview (§2.1). Server-rendered.
export default function PreviewTeaser({ preview }: { preview: Preview }) {
  const [firstKey] = Object.keys(preview.sections);
  const first = firstKey ? preview.sections[firstKey] : "";
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
        Free weekly preview · GW{preview.gameweek}
      </p>
      <div className="mt-2 line-clamp-6 text-sm">
        <Md text={first} />
      </div>
    </div>
  );
}
