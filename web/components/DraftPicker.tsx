"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { getPlayers, postDraftBrief, PlayerRow } from "@/lib/api";
import LoadingState from "@/components/LoadingState";
import ErrorState from "@/components/ErrorState";

// Picker rules are the contract and are unchanged by the re-skin: 15 players,
// caps 2/5/5/3, £100.0m budget, one captain, optional email, POST /api/brief.
const BUDGET = 100.0;
const POS_ORDER: PlayerRow["position"][] = ["GK", "DEF", "MID", "FWD"];
const POS_CAP: Record<string, number> = { GK: 2, DEF: 5, MID: 5, FWD: 3 };
const POS_GROUP: Record<string, string> = { GK: "Goalkeepers", DEF: "Defenders", MID: "Midfielders", FWD: "Forwards" };
// ponytail: fixed per-position row cap keeps the DOM small; swap for
// virtualisation only if the list ever needs to show every player at once.
const ROWS_PER_POS = 60;

// Design §02: mobile is a full-height sheet, desktop a modal with a squad rail.
// One tap adds, position caps enforce, over-budget rows dim, the budget bar sits
// above the thumb on mobile and in the rail on desktop.
export default function DraftPicker({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [players, setPlayers] = useState<PlayerRow[]>([]);
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<Record<number, PlayerRow>>({});
  const [captainId, setCaptainId] = useState<number | null>(null);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPlayers().then(setPlayers).catch(() => setError("Couldn't load players."));
  }, []);

  // Esc closes, as the desktop modal promises.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, busy]);

  const chosen = Object.values(picked);
  const spent = chosen.reduce((s, p) => s + p.price, 0);
  const remaining = BUDGET - spent;
  const posCount = (pos: string) => chosen.filter((p) => p.position === pos).length;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return players.filter((p) => !q || p.web_name.toLowerCase().includes(q) || p.team_short.toLowerCase().includes(q));
  }, [players, search]);

  // Group the visible pool by position so the list carries the design's headers.
  // The row cap is PER GROUP, not global: with ~840 players a flat cap would fill
  // entirely with goalkeepers and defenders and leave forwards unreachable
  // without a search. Picked players are always kept in view so they can be
  // captained or removed even when they fall outside the cap or the search.
  const groups = useMemo(() => {
    const pickedIds = new Set(Object.keys(picked).map(Number));
    return POS_ORDER.map((pos) => {
      const inPos = filtered.filter((p) => p.position === pos);
      const rows = inPos.slice(0, ROWS_PER_POS);
      const shown = new Set(rows.map((p) => p.id));
      const pinned = players.filter((p) => p.position === pos && pickedIds.has(p.id) && !shown.has(p.id));
      return { pos, rows: [...pinned, ...rows] };
    }).filter((g) => g.rows.length);
  }, [filtered, players, picked]);

  function toggle(p: PlayerRow) {
    setError(null);
    setPicked((cur) => {
      const next = { ...cur };
      if (next[p.id]) {
        delete next[p.id];
        if (captainId === p.id) setCaptainId(null);
        return next;
      }
      if (Object.keys(next).length >= 15) return cur;
      if (posCount(p.position) >= POS_CAP[p.position]) return cur;
      next[p.id] = p;
      return next;
    });
  }

  const complete = chosen.length === 15 && remaining >= 0;

  async function submit() {
    if (!complete) {
      setError("Pick 15 players within £100.0m.");
      return;
    }
    setError(null);
    setFailed(null);
    setBusy(true);
    try {
      const ordered = POS_ORDER.flatMap((pos) => chosen.filter((p) => p.position === pos).map((p) => p.id));
      const brief = await postDraftBrief({ player_ids: ordered, captain_id: captainId, email: email || null });
      router.push(`/brief/draft/${brief.draft_id}`);
    } catch (e: any) {
      setBusy(false);
      setFailed(
        e?.status === 429
          ? "You've generated a few briefs already. The limit resets shortly — your squad is still here."
          : "Your squad reached us fine — the writing step didn't come back. This is on us, and it usually works second time."
      );
    }
  }

  const remainingLabel = `£${remaining.toFixed(1)}`;

  return (
    <div
      /* text-left: the picker renders inside the centred hero, and the loading
         and error copy inside it must not inherit that centring. */
      className="fixed inset-0 z-50 flex justify-center bg-black/55 text-left sm:items-start sm:overflow-y-auto sm:p-6"
      onClick={() => !busy && onClose()}
      role="dialog"
      aria-modal="true"
      aria-label="Build your 15"
    >
      <div
        className="flex h-full w-full flex-col overflow-hidden bg-surface-900 sm:my-4 sm:h-auto sm:max-w-[1000px] sm:rounded-frame sm:border sm:border-line sm:bg-surface-850 sm:shadow-[0_24px_60px_rgba(0,0,0,0.5)]"
        onClick={(e) => e.stopPropagation()}
      >
        {busy ? (
          <>
            <div className="border-b border-line-dim px-5 py-3 text-[15px] font-bold">The Scout Report</div>
            <LoadingState />
          </>
        ) : failed ? (
          <>
            <div className="border-b border-line-dim px-5 py-3 text-[15px] font-bold">The Scout Report</div>
            <ErrorState detail={failed} onRetry={submit}>
              <button type="button" onClick={() => setFailed(null)} className="btn-ghost mt-2 w-full">
                Back to my squad
              </button>
            </ErrorState>
          </>
        ) : (
          <>
            {/* Header: title, rules, position counters, search */}
            <div className="shrink-0 border-b border-line bg-surface-850 px-4 py-3.5 sm:px-6 sm:py-4">
              <div className="mb-2.5 flex items-start justify-between gap-4">
                <div>
                  <h2 className="m-0 text-[16px] font-bold sm:text-title">Build your 15</h2>
                  <p className="m-0 mt-0.5 hidden text-label text-ink-600 sm:block">
                    Pick 2 GK · 5 DEF · 5 MID · 3 FWD within £100.0m
                  </p>
                </div>
                <button
                  type="button"
                  onClick={onClose}
                  className="shrink-0 text-label font-medium text-ink-500 hover:text-ink-0"
                >
                  <span className="sm:hidden">Close</span>
                  <span className="hidden sm:inline">Esc to close</span>
                </button>
              </div>
              <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:gap-2.5">
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={`Search ${players.length || ""} players…`}
                  aria-label="Search players"
                  className="field py-2.5 text-body sm:flex-1"
                />
                <div className="flex gap-1.5">
                  {POS_ORDER.map((pos) => {
                    const full = posCount(pos) >= POS_CAP[pos];
                    return (
                      <div
                        key={pos}
                        className={`flex-1 rounded-lg py-1.5 text-center sm:flex-none sm:px-2.5 sm:py-2 ${
                          full ? "bg-ink-0" : "border border-line-strong"
                        }`}
                      >
                        <span
                          className={`block text-eyebrow font-bold tracking-normal sm:inline ${
                            full ? "text-ink-inverse" : "text-ink-0"
                          }`}
                        >
                          {pos}
                        </span>{" "}
                        <span
                          className={`mt-0.5 block text-[11px] font-medium sm:mt-0 sm:inline ${
                            full ? "text-ink-800" : "text-ink-600"
                          }`}
                        >
                          {posCount(pos)}/{POS_CAP[pos]}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>

            <div className="flex min-h-0 flex-1 flex-col sm:grid sm:grid-cols-[minmax(0,1fr)_340px] sm:items-stretch">
              {/* Pool */}
              <div className="min-h-0 flex-1 overflow-y-auto sm:max-h-[60vh] sm:border-r sm:border-line">
                {groups.length === 0 && (
                  <p className="px-4 py-10 text-center text-body text-ink-700">
                    {players.length ? "No players match that search." : "Loading players…"}
                  </p>
                )}
                {groups.map(({ pos, rows }) => (
                  <div key={pos}>
                    <div className="px-4 pb-1.5 pt-3.5 text-[11px] font-semibold uppercase tracking-[.1em] text-ink-700 sm:px-6">
                      {POS_GROUP[pos]} · {posCount(pos)} of {POS_CAP[pos]}
                      {posCount(pos) >= POS_CAP[pos] && " · full"}
                    </div>
                    {rows.map((p) => {
                      const isPicked = !!picked[p.id];
                      const capReached = posCount(p.position) >= POS_CAP[p.position];
                      const overBudget = !isPicked && p.price > remaining;
                      const disabled = !isPicked && (chosen.length >= 15 || capReached);
                      const isCaptain = captainId === p.id;
                      return (
                        <div
                          key={p.id}
                          className={`flex items-center gap-3 border-t border-line-soft px-4 py-2.5 sm:px-6 ${
                            isPicked ? "bg-surface-800" : ""
                          } ${disabled || overBudget ? "opacity-[.42]" : ""}`}
                        >
                          <button
                            type="button"
                            onClick={() => toggle(p)}
                            disabled={disabled}
                            aria-pressed={isPicked}
                            className="flex min-w-0 flex-1 items-center gap-3 text-left disabled:cursor-not-allowed"
                          >
                            <span
                              aria-hidden
                              className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-label font-bold ${
                                isCaptain
                                  ? "bg-ink-0 text-ink-inverse"
                                  : isPicked
                                    ? "bg-in-greenbg text-in-green"
                                    : "border border-line-strong text-[16px] font-normal text-ink-500"
                              }`}
                            >
                              {isCaptain ? "C" : isPicked ? "✓" : "+"}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[15px] font-semibold">
                                {p.web_name}
                                {isCaptain && (
                                  <span className="ml-1.5 text-[11px] font-semibold uppercase tracking-[.08em] text-state-blue">
                                    Captain
                                  </span>
                                )}
                              </span>
                              <span className="mt-px block text-eyebrow tracking-normal text-ink-600">
                                {p.team_short} · {p.position}
                                {overBudget && ` · over budget by £${(p.price - remaining).toFixed(1)}`}
                              </span>
                            </span>
                          </button>
                          {isPicked && !isCaptain && (
                            <button
                              type="button"
                              onClick={() => setCaptainId(p.id)}
                              title={`Captain ${p.web_name}`}
                              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-line-strong text-[11px] font-semibold text-ink-600 hover:border-ink-0 hover:text-ink-0"
                            >
                              C<span className="sr-only"> — make captain</span>
                            </button>
                          )}
                          <span className="shrink-0 text-body font-semibold tabular-nums">
                            £{p.price.toFixed(1)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>

              {/* Squad rail — desktop only; mobile gets the pinned footer below. */}
              <div className="hidden flex-col bg-surface-900 px-5 py-4 sm:flex">
                <div className="mb-3 text-[11px] font-semibold uppercase tracking-[.1em] text-ink-700">Your squad</div>
                <div className="mb-1.5 flex items-baseline justify-between">
                  <span className={`text-[26px] font-bold ${remaining < 0 ? "text-out-red" : ""}`}>
                    {remainingLabel}
                    <span className="text-body font-normal text-ink-600"> left</span>
                  </span>
                  <span className="text-label font-semibold text-ink-500">{chosen.length}/15</span>
                </div>
                <BudgetBar spent={spent} over={remaining < 0} />
                <div className="mt-3.5 flex flex-col gap-2 text-label text-ink-500">
                  {POS_ORDER.map((pos) => {
                    const rows = chosen.filter((p) => p.position === pos);
                    return (
                      <div key={pos} className="flex justify-between gap-3">
                        <span className="truncate">
                          {pos} ·{" "}
                          {rows.length ? rows.map((p) => p.web_name).join(", ") : `none of ${POS_CAP[pos]}`}
                        </span>
                        <span className="shrink-0 tabular-nums">
                          £{rows.reduce((s, p) => s + p.price, 0).toFixed(1)}
                        </span>
                      </div>
                    );
                  })}
                </div>
                <div className="mt-3.5 border-t border-line pt-3.5">
                  <div className="mb-2 text-[11px] font-semibold uppercase tracking-[.1em] text-ink-700">Captain</div>
                  {captainId && picked[captainId] ? (
                    <div className="flex items-center gap-2.5 rounded border border-line bg-surface-800 px-3 py-2.5">
                      <span
                        aria-hidden
                        className="flex h-6 w-6 items-center justify-center rounded-full bg-ink-0 text-[11px] font-bold text-ink-inverse"
                      >
                        C
                      </span>
                      <span className="text-body font-semibold">{picked[captainId].web_name}</span>
                      <button
                        type="button"
                        onClick={() => setCaptainId(null)}
                        className="ml-auto text-eyebrow tracking-normal text-ink-600 hover:text-ink-0"
                      >
                        change
                      </button>
                    </div>
                  ) : (
                    <p className="m-0 text-label text-ink-700">Tap the C beside a picked player.</p>
                  )}
                </div>
                <div className="mt-auto pt-4">
                  <Submit
                    email={email}
                    setEmail={setEmail}
                    onSubmit={submit}
                    disabled={!complete}
                    remaining={15 - chosen.length}
                  />
                </div>
              </div>
            </div>

            {error && (
              <p className="px-4 pb-1 pt-2 text-body text-out-red sm:px-6" role="alert">
                {error}
              </p>
            )}

            {/* Mobile pinned footer: budget above the thumb, then the CTA. */}
            <div className="shrink-0 border-t border-line-dim bg-surface-850 px-4 pb-4 pt-3 sm:hidden">
              <div className="mb-2 flex items-baseline justify-between">
                <span className="text-eyebrow font-semibold uppercase tracking-[.06em] text-ink-500">
                  {chosen.length} of 15 picked
                </span>
                <span className={`text-label font-semibold ${remaining < 0 ? "text-out-red" : ""}`}>
                  £{spent.toFixed(1)} / £100.0
                </span>
              </div>
              <BudgetBar spent={spent} over={remaining < 0} />
              <div className="mt-2.5">
                <Submit
                  email={email}
                  setEmail={setEmail}
                  onSubmit={submit}
                  disabled={!complete}
                  remaining={15 - chosen.length}
                />
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function BudgetBar({ spent, over }: { spent: number; over: boolean }) {
  return (
    <div className="h-1 overflow-hidden rounded-sm bg-line">
      <div
        className={`h-full ${over ? "bg-out-red" : "bg-state-blue"}`}
        style={{ width: `${Math.min(100, (spent / BUDGET) * 100)}%` }}
      />
    </div>
  );
}

function Submit({
  email,
  setEmail,
  onSubmit,
  disabled,
  remaining,
}: {
  email: string;
  setEmail: (v: string) => void;
  onSubmit: () => void;
  disabled: boolean;
  remaining: number;
}) {
  return (
    <>
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="Email me the verdict (optional)"
        aria-label="Email me the verdict (optional)"
        className="field mb-2 py-2.5 text-body"
      />
      <button type="button" onClick={onSubmit} disabled={disabled} className="btn-primary w-full py-3.5">
        Rate my draft{remaining > 0 ? ` · ${remaining} left` : ""}
      </button>
    </>
  );
}
