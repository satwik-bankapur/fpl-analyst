// Minimal markdown block renderer for the stub Writer's output (bullets, quotes,
// paragraphs). Deliberately tiny — no markdown dependency for the handful of
// constructs the briefs use. Nested "  - " become sub-bullets.
export default function Md({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: JSX.Element[] = [];
  let list: string[] = [];

  const flushList = () => {
    if (list.length) {
      blocks.push(
        <ul key={`ul-${blocks.length}`} className="my-2 list-disc space-y-1 pl-5 text-slate-700">
          {list.map((li, i) => (
            <li key={i}>{li}</li>
          ))}
        </ul>
      );
      list = [];
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      flushList();
      continue;
    }
    const bullet = line.match(/^\s*-\s+(.*)$/);
    if (bullet) {
      list.push(bullet[1]);
      continue;
    }
    flushList();
    if (line.startsWith(">")) {
      blocks.push(
        <p key={`q-${blocks.length}`} className="my-2 border-l-2 border-slate-300 pl-3 text-sm italic text-slate-500">
          {line.replace(/^>\s?/, "")}
        </p>
      );
    } else {
      blocks.push(
        <p key={`p-${blocks.length}`} className="my-2 text-slate-700">
          {line}
        </p>
      );
    }
  }
  flushList();
  return <>{blocks}</>;
}
