import { Eyebrow } from "@/components/Chrome";

// Design §03: errors say exactly what happened and cost nothing. Two facts do
// the reassuring — the squad step succeeded, and the free brief is still unused.
export default function ErrorState({
  kicker = "Didn't come back",
  title = "The brief didn't finish writing.",
  detail = "Your squad loaded fine — the writing step timed out. This is on us, and it usually works second time.",
  onRetry,
  retryLabel = "Try again",
  children,
}: {
  kicker?: string;
  title?: string;
  detail?: string;
  onRetry?: () => void;
  retryLabel?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-1 flex-col px-5 py-7 sm:px-8" role="alert">
      <Eyebrow tone="error">{kicker}</Eyebrow>
      <h2 className="mb-2.5 mt-3 text-[23px] font-bold leading-tight tracking-[-0.015em]">{title}</h2>
      <p className="m-0 mb-1.5 max-w-measure text-[15px] leading-relaxed text-ink-500">{detail}</p>
      <p className="m-0 mb-5 max-w-measure text-body leading-relaxed text-ink-700">
        Nothing was charged and your free brief this week is still unused.
      </p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-primary w-full">
          {retryLabel}
        </button>
      )}
      {children}
    </div>
  );
}
