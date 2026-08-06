"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  getPlayers,
  postDraftBrief,
  PlayerRow,
  MAX_PER_CLUB,
  POSITION_QUOTA,
  SQUAD_BUDGET,
  SQUAD_SIZE,
} from "@/lib/api";
import LoadingState from "@/components/LoadingState";
import ErrorState from "@/components/ErrorState";

// Picker rules are the contract and are unchanged by the re-skin: 15 players,
// caps 2/5/5/3, £100.0m budget, max 3 per club, one captain, optional email,
// POST /api/brief. Every one of those numbers comes from lib/api's mirror of
// graph/config.py — none is re-typed here — and the API re-checks all of them,
// so this UI only has to stop the user before they hit a 400.
const BUDGET = SQUAD_BUDGET;
const POS_ORDER: PlayerRow["position"][] = ["GK", "DEF", "MID", "FWD"];
const POS_CAP = POSITION_QUOTA;
const POS_GROUP: Record<string, string> = { GK: "Goalkeepers", DEF: "Defenders", MID: "Midfielders", FWD: "Forwards" };

// THE ROW CAP IS GONE (2026-08-06). It was 60 per position, and with 572
// players that hid 332 of them — 58% — behind a search box that could not find
// them either (see `fold` below). Sorted price-desc, the cut landed entirely on
// the cheap end, so every budget enabler and bench filler was unreachable. The
// old comment said to swap it for virtualisation "only if the list ever needs
// to show every player at once"; it always did. 572 static rows render fine,
// and correctness beats a DOM budget nobody was measuring.
// ponytail: if the pool ever gets big enough to feel slow, virtualise — do not
// bring the cap back.

// Availability, from `status` on the payload. 'a' and 'd' are pickable without
// comment; the rest get a visible flag. A player is NEVER hidden for being
// unavailable — drafting an injured player for the fixture after next is a real
// strategy, and silently omitting him looks identical to the bug this replaced.
const UNAVAILABLE: Record<string, string> = {
  i: "injured",
  s: "suspended",
  u: "unavailable",
  n: "on loan",
};

// Latin letters that are NOT a base plus a combining mark, so NFD leaves them
// intact and stripping diacritics does nothing for them. Without this map
// Ødegaard stays unfindable as "odegaard" and Groß as "gross". Everything else
// (é, ï, ö, ç, ą…) NFD handles generically.
const LETTERS: Record<string, string> = {
  Ø: "o", ø: "o", ß: "ss", Ł: "l", ł: "l", Đ: "d", đ: "d",
  Ð: "d", ð: "d", ı: "i", Æ: "ae", æ: "ae", Œ: "oe", œ: "oe",
  Þ: "th", þ: "th",
};

/** Lowercase and strip diacritics, so "ait" finds Aït-Nouri.
 *
 * NFD splits a precomposed letter into its base plus a combining mark, and
 * \p{Diacritic} matches exactly that mark — so that half is one line, with no
 * dependency (`web/` has none beyond Next and React, and a transliteration
 * package for this would be absurd). LETTERS above covers the rest.
 *
 * Mirrored by `fold()` in scripts/player_coverage.py — change both together;
 * tests/test_api.py::test_coverage_diagnostic_still_mirrors_the_picker fails if
 * they drift.
 */
function fold(s: string): string {
  return s
    .replace(/[ØøßŁłĐđÐðıÆæŒœÞþ]/g, (c) => LETTERS[c] || c)
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase();
}

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
  const clubCount = (club: string) => chosen.filter((p) => p.team_short === club).length;
  // Clubs in use, heaviest first — the per-club counter in the rail.
  const clubTally = Object.entries(
    chosen.reduce<Record<string, number>>((acc, p) => {
      acc[p.team_short] = (acc[p.team_short] || 0) + 1;
      return acc;
    }, {})
  ).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));

  // Search key per player, folded once and cached — recomputing fold() for 572
  // players on every keystroke is the one place this gets hot.
  const keyed = useMemo(
    () => players.map((p) => ({ p, key: fold(`${p.web_name} ${p.full_name || ""} ${p.team_short}`) })),
    [players]
  );

  const filtered = useMemo(() => {
    const q = fold(search.trim());
    return keyed.filter(({ key }) => !q || key.includes(q)).map(({ p }) => p);
  }, [keyed, search]);

  // Group the visible pool by position so the list carries the design's headers.
  // Every match is rendered — see the row-cap note at the top of this file.
  // Picked players are always kept in view so they can be captained or removed
  // even when they fall outside the current search.
  //
  // Sorted by PRICE DESCENDING (2026-07-30). /api/players returns rows in
  // element-id order, and FPL assigns ids club-by-club alphabetically — so the
  // old order was effectively "Arsenal first". Price desc is also the
  // FPL-familiar default.
  const groups = useMemo(() => {
    const pickedIds = new Set(Object.keys(picked).map(Number));
    return POS_ORDER.map((pos) => {
      const rows = filtered
        .filter((p) => p.position === pos)
        .sort((a, b) => b.price - a.price || a.web_name.localeCompare(b.web_name));
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
      if (Object.keys(next).length >= SQUAD_SIZE) return cur;
      if (posCount(p.position) >= POS_CAP[p.position]) return cur;
      if (clubCount(p.team_short) >= MAX_PER_CLUB) return cur;
      next[p.id] = p;
      return next;
    });
  }

  const complete = chosen.length === SQUAD_SIZE && remaining >= 0;

  async function submit() {
    if (!complete) {
      setError(`Pick ${SQUAD_SIZE} players within £${BUDGET.toFixed(1)}m.`);
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
      // A 400 is the server's squad-rule validator. The picker prevents all of
      // these, so reaching one means the two disagree — say what the server said
      // rather than blaming the writer for a rejection that never got that far.
      if (e?.status === 400) {
        setError(e?.detail?.message || "That squad isn't legal.");
        return;
      }
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
                  <div className="px-4 py-10 text-center">
                    <p className="m-0 text-body text-ink-700">
                      {players.length ? "No players match that search." : "Loading players…"}
                    </p>
                    {/* The one genuinely-not-ours cause of "where is he?": FPL
                        publishes transfer-window signings on their own schedule,
                        sometimes days after the move. Say so here rather than
                        letting it read as our bug — every OTHER reason a player
                        could be missing was fixed on 2026-08-06. */}
                    {players.length > 0 && (
                      <p className="m-0 mt-2 text-label text-ink-700">
                        Missing a new signing? FPL adds transfers with a delay.
                      </p>
                    )}
                  </div>
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
                      const clubFull = !isPicked && clubCount(p.team_short) >= MAX_PER_CLUB;
                      const overBudget = !isPicked && p.price > remaining;
                      const disabled =
                        !isPicked && (chosen.length >= SQUAD_SIZE || capReached || clubFull);
                      const isCaptain = captainId === p.id;
                      // Flagged, not filtered: he is still pickable.
                      const unavailable = UNAVAILABLE[p.status];
                      return (
                        <div
                          key={p.id}
                          className={`flex items-center gap-3 border-t border-line-soft px-4 py-2.5 sm:px-6 ${
                            isPicked ? "bg-surface-800" : ""
                          } ${disabled || overBudget ? "opacity-[.42]" : ""}`}
                          title={clubFull ? `${MAX_PER_CLUB} max from ${p.team_short}` : undefined}
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
                                {unavailable && (
                                  <span className="ml-1.5 text-[11px] font-semibold uppercase tracking-[.08em] text-out-red">
                                    {unavailable}
                                  </span>
                                )}
                              </span>
                              {/* The reason a disabled row is disabled, in the same
                                  place the over-budget reason already appears. */}
                              <span className="mt-px block text-eyebrow tracking-normal text-ink-600">
                                {p.team_short} · {p.position}
                                {clubFull && ` · ${MAX_PER_CLUB} max from ${p.team_short}`}
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
                {/* Per-club counts — unobtrusive, but it explains why a row went
                    grey before the user has to hunt for the reason. */}
                {clubTally.length > 0 && (
                  <p className="mt-3 m-0 text-eyebrow leading-relaxed tracking-normal text-ink-700">
                    <span className="text-ink-600">Clubs</span>{" "}
                    {clubTally.map(([club, n], i) => (
                      <span key={club} className={n >= MAX_PER_CLUB ? "text-ink-0" : undefined}>
                        {i > 0 && " · "}
                        {club} {n}
                      </span>
                    ))}
                    {clubTally.some(([, n]) => n >= MAX_PER_CLUB) && ` · ${MAX_PER_CLUB} max each`}
                  </p>
                )}

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
                    remaining={SQUAD_SIZE - chosen.length}
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
                  {chosen.length} of {SQUAD_SIZE} picked
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
                  remaining={SQUAD_SIZE - chosen.length}
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
