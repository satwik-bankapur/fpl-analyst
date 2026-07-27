// Minimal markdown block renderer for the stub Writer's output (bullets, quotes,
// paragraphs). Deliberately tiny — no markdown dependency for the handful of
// constructs the briefs use.
//
// Typography here is the product (design §04): prose sits at 16/1.7 on mobile
// and 17/1.7 on desktop, in ink-100, capped at a 64ch measure. Nothing in a
// brief is set smaller than this, however dense the surrounding chrome gets.
export default function Md({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: JSX.Element[] = [];
  let list: string[] = [];

  const flushList = () => {
    if (list.length) {
      blocks.push(
        <ul key={`ul-${blocks.length}`} className="my-3 max-w-measure list-disc space-y-2 pl-5 marker:text-ink-700">
          {list.map((li, i) => (
            <li key={i} className="text-prose text-ink-100 sm:text-prose-lg">
              {li}
            </li>
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
    if (/^-{3,}$/.test(line.trim())) {
      blocks.push(<hr key={`hr-${blocks.length}`} className="my-5 max-w-measure border-0 border-t border-line" />);
    } else if (line.startsWith(">")) {
      blocks.push(
        <p
          key={`q-${blocks.length}`}
          className="my-3 max-w-measure border-l-2 border-line-strong pl-4 text-prose italic text-ink-500"
        >
          {line.replace(/^>\s?/, "")}
        </p>
      );
    } else {
      blocks.push(
        <p key={`p-${blocks.length}`} className="my-3 max-w-measure text-prose text-ink-100 sm:text-prose-lg">
          {line}
        </p>
      );
    }
  }
  flushList();
  return <>{blocks}</>;
}
