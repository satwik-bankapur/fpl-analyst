import Link from "next/link";
import { Eyebrow } from "@/components/Chrome";

// Design §03 "not found": name the thing that wasn't found, say the likeliest
// reason in one sentence, then give the one route that still works.
export default function NotFoundState({
  kicker,
  title,
  children,
  hint,
  cta = { href: "/", label: "Build a draft instead" },
}: {
  kicker: string;
  title: string;
  children?: React.ReactNode;
  /** Monospace-ish example line, e.g. the FPL URL a team ID is taken from. */
  hint?: React.ReactNode;
  cta?: { href: string; label: string };
}) {
  return (
    <div className="flex flex-1 flex-col px-5 py-7 sm:px-8">
      <Eyebrow tone="error">{kicker}</Eyebrow>
      <h1 className="mb-2.5 mt-3 text-[23px] font-bold leading-tight tracking-[-0.015em] sm:text-[28px]">
        {title}
      </h1>
      <div className="m-0 mb-4 max-w-measure text-[15px] leading-relaxed text-ink-500">{children}</div>
      {hint && (
        <div className="mb-5 break-all rounded border border-line bg-surface-800 px-3.5 py-3 text-label leading-relaxed text-ink-500">
          {hint}
        </div>
      )}
      <Link href={cta.href} className="btn-primary w-full text-center no-underline hover:no-underline">
        {cta.label}
      </Link>
    </div>
  );
}
