"use client";

import { useCallback, useEffect, useState } from "react";
import { Unauthorized } from "@/lib/admin";

// The handful of pieces all four admin pages need. Plain on purpose — this is an
// internal console, so it borrows the site's tokens (.card, .field, ink/surface)
// and adds no new visual language.

/** Load-once-with-refresh. Swallows Unauthorized: the shell re-prompts on that. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const run = useCallback(() => {
    setLoading(true);
    fn()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => {
        if (!(e instanceof Unauthorized)) setError(e.message || "Request failed");
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(run, [run]);
  return { data, error, loading, reload: run };
}

export function PageTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-baseline justify-between gap-3">
      <h1 className="m-0 text-title-lg font-bold">{children}</h1>
      {right}
    </div>
  );
}

export function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card p-4">
      <h2 className="m-0 mb-3 text-eyebrow font-semibold uppercase text-ink-600">{title}</h2>
      {children}
    </section>
  );
}

/** One number with its label. `tone="warn"` for values that mean "look at me". */
export function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "warn" | "ok" }) {
  return (
    <div className="rounded border border-line bg-surface-900 px-3 py-2.5">
      <div className="text-eyebrow font-semibold uppercase text-ink-700">{label}</div>
      <div
        className={`mt-1 text-title font-semibold tabular-nums ${
          tone === "warn" ? "text-out-red" : tone === "ok" ? "text-in-green" : "text-ink-0"
        }`}
      >
        {value}
      </div>
    </div>
  );
}

export function Loading({ what }: { what: string }) {
  return <p className="m-0 text-body text-ink-600">Loading {what}…</p>;
}

export function Failed({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="card border-out-red/40 p-4">
      <p className="m-0 text-body text-out-red">{error}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-ghost mt-3">
          Try again
        </button>
      )}
    </div>
  );
}

/** FDR 1–5 as colour. Green = easy, red = hard; the scale FPL itself uses. */
export const FDR_CLASS: Record<number, string> = {
  1: "bg-in-greenbg text-in-green",
  2: "bg-in-greenbg/60 text-in-green",
  3: "bg-surface-700 text-ink-500",
  4: "bg-out-redbg/70 text-out-red",
  5: "bg-out-redbg text-out-red",
};

/** Honesty flags read as warnings; they exist to say "trust this number less". */
export function FlagChips({ flags }: { flags: string[] }) {
  if (!flags.length) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {flags.map((f) => (
        <span
          key={f}
          className="rounded bg-out-redbg px-1.5 py-0.5 text-[11px] font-medium text-out-red"
        >
          {f}
        </span>
      ))}
    </span>
  );
}

export function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.round(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}
