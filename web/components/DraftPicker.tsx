"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { getPlayers, postDraftBrief, PlayerRow } from "@/lib/api";

const BUDGET = 100.0;
const POS_ORDER: PlayerRow["position"][] = ["GK", "DEF", "MID", "FWD"];
const POS_CAP: Record<string, number> = { GK: 2, DEF: 5, MID: 5, FWD: 3 };
const STAGES = ["Reading your squad…", "Checking fixtures…", "Writing your brief…"];

export default function DraftPicker({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [players, setPlayers] = useState<PlayerRow[]>([]);
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<Record<number, PlayerRow>>({});
  const [captainId, setCaptainId] = useState<number | null>(null);
  const [email, setEmail] = useState("");
  const [stage, setStage] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPlayers().then(setPlayers).catch(() => setError("Couldn't load players."));
  }, []);

  const chosen = Object.values(picked);
  const spent = chosen.reduce((s, p) => s + p.price, 0);
  const remaining = BUDGET - spent;
  const posCount = (pos: string) => chosen.filter((p) => p.position === pos).length;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return players.filter((p) => !q || p.web_name.toLowerCase().includes(q) || p.team_short.toLowerCase().includes(q));
  }, [players, search]);

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
    setStage(0);
    const timer = setInterval(() => setStage((s) => (s === null ? 0 : Math.min(s + 1, STAGES.length - 1))), 1000);
    try {
      const ordered = POS_ORDER.flatMap((pos) => chosen.filter((p) => p.position === pos).map((p) => p.id));
      const brief = await postDraftBrief({ player_ids: ordered, captain_id: captainId, email: email || null });
      router.push(`/brief/draft/${brief.draft_id}`);
    } catch (e: any) {
      clearInterval(timer);
      setStage(null);
      setError(e?.status === 429 ? "You've generated a few briefs already — try again in a bit." : "Something went wrong generating your brief.");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4" onClick={onClose}>
      <div className="my-8 w-full max-w-3xl rounded-xl bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
        {/* header / budget */}
        <div className="flex items-center justify-between border-b border-slate-200 p-4">
          <div>
            <h2 className="text-lg font-bold">Rate my draft</h2>
            <p className="text-sm text-slate-500">Pick your 15. Tap a picked player to captain them.</p>
          </div>
          <div className="text-right text-sm">
            <div className={remaining < 0 ? "font-bold text-red-600" : "font-bold text-pitch"}>£{remaining.toFixed(1)}m left</div>
            <div className="text-slate-500">{chosen.length}/15 picked</div>
          </div>
        </div>

        {stage !== null ? (
          <div className="p-10 text-center">
            <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-pitch" />
            <p className="text-slate-600" aria-live="polite">{STAGES[stage]}</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 p-4 md:grid-cols-2">
            {/* pool */}
            <div>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search player or club…"
                className="mb-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              />
              <div className="h-80 space-y-1 overflow-y-auto rounded-md border border-slate-200 p-2">
                {filtered.slice(0, 250).map((p) => {
                  const isPicked = !!picked[p.id];
                  const capReached = posCount(p.position) >= POS_CAP[p.position];
                  const disabled = !isPicked && (chosen.length >= 15 || capReached);
                  return (
                    <button
                      key={p.id}
                      onClick={() => toggle(p)}
                      disabled={disabled}
                      className={`flex w-full items-center justify-between rounded px-2 py-1 text-left text-sm ${
                        isPicked ? "bg-emerald-100" : disabled ? "opacity-40" : "hover:bg-slate-100"
                      }`}
                    >
                      <span>
                        <span className="font-medium">{p.web_name}</span>{" "}
                        <span className="text-xs text-slate-500">{p.team_short} · {p.position}</span>
                      </span>
                      <span className="tabular-nums text-slate-600">£{p.price.toFixed(1)}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* squad */}
            <div>
              <div className="mb-2 flex gap-2 text-xs text-slate-500">
                {POS_ORDER.map((pos) => (
                  <span key={pos} className="rounded bg-slate-100 px-2 py-1">
                    {pos} {posCount(pos)}/{POS_CAP[pos]}
                  </span>
                ))}
              </div>
              <div className="h-80 space-y-1 overflow-y-auto rounded-md border border-slate-200 p-2">
                {chosen.length === 0 && <p className="p-4 text-center text-sm text-slate-400">No players yet.</p>}
                {chosen.map((p) => (
                  <div key={p.id} className="flex items-center justify-between rounded px-2 py-1 text-sm hover:bg-slate-50">
                    <button onClick={() => setCaptainId(p.id)} className="flex items-center gap-2 text-left">
                      <span className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${captainId === p.id ? "bg-pitch text-white" : "bg-slate-200 text-slate-500"}`}>C</span>
                      <span className="font-medium">{p.web_name}</span>
                      <span className="text-xs text-slate-500">{p.team_short} · {p.position}</span>
                    </button>
                    <button onClick={() => toggle(p)} className="text-slate-400 hover:text-red-500">✕</button>
                  </div>
                ))}
              </div>
              <input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Email (optional — get it in your inbox)"
                className="mt-2 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
          </div>
        )}

        {error && <p className="px-4 text-sm text-red-600">{error}</p>}

        <div className="flex justify-end gap-2 border-t border-slate-200 p-4">
          <button onClick={onClose} className="rounded-md px-4 py-2 text-sm text-slate-600 hover:bg-slate-100">Cancel</button>
          <button
            onClick={submit}
            disabled={!complete || stage !== null}
            className="rounded-md bg-pitch px-5 py-2 text-sm font-semibold text-white disabled:opacity-40"
          >
            Get my brief
          </button>
        </div>
      </div>
    </div>
  );
}
