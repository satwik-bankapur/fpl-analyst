"use client";

import { Fragment, useCallback, useState } from "react";
import BriefView from "@/components/BriefView";
import { Failed, FlagChips, Loading, PageTitle, Panel, Stat, useLoad } from "@/components/AdminBits";
import { adminGet } from "@/lib/admin";
import type {
  BriefRef, Inspection, InspectSquadRow, PayloadPreview, PlayerComponents, Tier, TraceStage,
} from "@/lib/admin";
import type { Brief } from "@/lib/api";

// The reasoning inspector: raw data -> finished sentence, in pipeline order.
//
// Everything on this page comes from `api/inspect.py`, which runs the real graph
// in trace mode and the real serializer, and calls no model. Nothing is
// re-derived here — if a number on this page is wrong, the pipeline is wrong.
//
// `<details>` does the collapsing. Four stages and a payload do not need a state
// machine, and native disclosure keeps keyboard and find-in-page working.

const STAGE_TITLES: Record<string, string> = {
  squad_analyst: "1 · Squad analyst — what's in the squad and what's wrong with it",
  market_scout: "2 · Market scout — who was considered, and who was rejected",
  fixture_planner: "3 · Fixture planner — swing, blanks and doubles",
  strategist: "4 · Strategist — the decision, and why the alternatives lost",
};

export default function AdminInspect() {
  const [target, setTarget] = useState<string | null>(null);
  const [entry, setEntry] = useState("");

  const { data: briefs, error, loading, reload } = useLoad<BriefRef[]>(
    useCallback(() => adminGet<BriefRef[]>("/admin/inspect/briefs?n=25"), []),
    []
  );

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !briefs) return <Loading what="recent briefs" />;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const v = entry.trim();
    if (v) setTarget(`/admin/inspect/draft/${v}`);
  };

  return (
    <>
      <PageTitle>Reasoning inspector</PageTitle>

      <div className="flex flex-col gap-5">
        <Panel title="Pick a brief">
          {briefs.length === 0 ? (
            <p className="m-0 text-body text-ink-600">
              No briefs generated yet. Submit a draft squad on the public site first.
            </p>
          ) : (
            <ul className="m-0 flex list-none flex-col gap-1 p-0">
              {briefs.map((b) => {
                const path = b.draft_id
                  ? `/admin/inspect/draft/${b.draft_id}`
                  : `/admin/inspect/team/${b.fpl_team_id}/${b.gameweek}`;
                return (
                  <li key={b.brief_id}>
                    <button
                      type="button"
                      onClick={() => setTarget(path)}
                      className={`flex w-full flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded border px-3 py-2 text-left text-body ${
                        target === path
                          ? "border-state-blue bg-state-bluebg/30"
                          : "border-line-soft hover:border-line-strong"
                      }`}
                    >
                      <span className="font-semibold text-ink-0">
                        {b.draft_id ? "Draft" : `Team ${b.fpl_team_id}`}
                      </span>
                      <span className="font-mono text-label text-ink-600">
                        {b.draft_id ? b.draft_id.slice(0, 8) : `GW${b.gameweek}`}
                      </span>
                      <span className="text-label text-ink-700">{b.model}</span>
                      <span className="ml-auto font-mono text-label text-ink-700">
                        {b.input_hash}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          <form onSubmit={submit} className="mt-3 flex flex-wrap gap-2 border-t border-line-soft pt-3">
            <input
              value={entry}
              onChange={(e) => setEntry(e.target.value)}
              placeholder="…or paste a draft_id"
              aria-label="Draft id"
              className="field max-w-[380px] flex-1 font-mono text-label"
            />
            <button type="submit" disabled={!entry.trim()} className="btn-ghost">
              Inspect
            </button>
          </form>
        </Panel>

        {target && <Inspector path={target} key={target} />}
      </div>
    </>
  );
}

function Inspector({ path }: { path: string }) {
  const { data, error, loading, reload } = useLoad<Inspection>(
    useCallback(() => adminGet<Inspection>(path), [path]),
    [path]
  );

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading what="the trace (this re-runs the pipeline)" />;

  if (data.error) {
    return (
      <Failed
        error={`${data.error.code}: ${data.error.message}`}
        onRetry={reload}
      />
    );
  }

  return (
    <>
      {!data.available.trace && data.available.reason && (
        <Panel title="Trace unavailable">
          <p className="m-0 max-w-[80ch] text-body leading-relaxed text-ink-600">
            {data.available.reason}
          </p>
        </Panel>
      )}

      {data.cache && data.cache.matches === false && (
        <Panel title="Cache drift">
          <p className="m-0 max-w-[80ch] text-body leading-relaxed">
            {data.cache.superseded ? (
              <span className="text-ink-600">
                The stored brief was written by <code>{data.cache.model}</code> and the
                current writer is <code>{data.cache.current_writer}</code>. The version is
                part of the cache key, so that row is superseded, not served — this is the
                expected state after a writer bump.
              </span>
            ) : (
              <span className="text-out-red">
                The stored brief was written by the SAME writer version (
                <code>{data.cache.model}</code>) but its text differs from this run. A
                template change landed without bumping <code>STUB_MODEL</code>, so cached
                readers are still being served the old prose. See graph/cache.py.
              </span>
            )}
          </p>
        </Panel>
      )}

      {data.warnings && data.warnings.length > 0 && (
        <Panel title="Pipeline warnings">
          <ul className="m-0 list-none p-0 text-body text-ink-600">
            {data.warnings.map((w) => (
              <li key={w} className="font-mono">{w}</li>
            ))}
          </ul>
        </Panel>
      )}

      {data.trace.map((stage) => (
        <Stage key={stage.node} stage={stage} />
      ))}

      {data.squad && data.squad.length > 0 && <SquadTable rows={data.squad} />}
      {data.payload && <Payload payload={data.payload} />}
      {data.free && data.paid && (
        <TierCompare
          free={data.free}
          paid={data.paid}
          delta={data.paid_delta || []}
          gameweek={data.target.gameweek}
        />
      )}
    </>
  );
}

// --- stages ----------------------------------------------------------------

function Stage({ stage }: { stage: TraceStage }) {
  return (
    <details className="card p-4" open>
      <summary className="cursor-pointer text-body font-semibold text-ink-0">
        {STAGE_TITLES[stage.node] || stage.node}
      </summary>

      <ul className="m-0 mt-3 list-none p-0">
        {stage.summary.map((line, i) => (
          <li
            key={i}
            className={`py-0.5 text-body leading-relaxed ${
              line.startsWith("  ") ? "pl-5 text-ink-600" : "text-ink-100"
            }`}
          >
            {line.trim()}
          </li>
        ))}
      </ul>

      <div className="mt-4">
        {stage.node === "market_scout" && <ScoutDetail data={stage.data} />}
        {stage.node === "fixture_planner" && <FixtureDetail data={stage.data} />}
        {stage.node === "strategist" && <StrategistDetail data={stage.data} />}
        {stage.node === "squad_analyst" && <AnalystDetail data={stage.data} />}
      </div>
    </details>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function AnalystDetail({ data }: { data: Record<string, any> }) {
  if (!data.problems?.length) return null;
  return (
    <Sub title="Problems raised, with the evidence behind each">
      <table className="w-full border-collapse text-body">
        <TH cols={["Type", "Player", "Severity", "Evidence"]} />
        <tbody>
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          {data.problems.map((p: any, i: number) => (
            <tr key={i} className="border-t border-line-soft align-top">
              <td className="py-1.5 pr-3 font-mono text-ink-300">{p.type}</td>
              <td className="py-1.5 pr-3 text-ink-0">{p.name}</td>
              <td className="py-1.5 pr-3 tabular-nums text-ink-500">{p.severity}</td>
              <td className="py-1.5 font-mono text-label text-ink-600">
                {p.evidence.join(" · ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Sub>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function ScoutDetail({ data }: { data: Record<string, any> }) {
  if (!data.problems?.length) {
    return <p className="m-0 text-body text-ink-600">No replaceable problem, so no scan ran.</p>;
  }
  return (
    <>
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      {data.problems.map((p: any) => (
        <Sub
          key={p.problem_player_id}
          title={`Replacing ${p.problem_player} — budget £${p.budget.toFixed(1)}m, ${p.passed_filters} of ${p.considered} passed every filter`}
        >
          <p className="m-0 mb-2 text-label uppercase text-ink-700">Shortlisted</p>
          <table className="w-full border-collapse text-body">
            <TH cols={["Player", "Club", "ep_next4", "Δ", "Δ£", "Own %", "Flags"]} right={[2, 3, 4, 5]} />
            <tbody>
              {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
              {p.shortlisted.map((c: any) => (
                <tr key={c.player_id} className="border-t border-line-soft">
                  <td className="py-1.5 pr-3 text-ink-0">{c.name}</td>
                  <td className="py-1.5 pr-3 text-ink-500">{c.club}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-ink-0">
                    {c.ep_next4.toFixed(2)}
                  </td>
                  <td
                    className={`py-1.5 pr-3 text-right tabular-nums ${
                      c.ep_next4_delta >= 0 ? "text-in-green" : "text-out-red"
                    }`}
                  >
                    {c.ep_next4_delta >= 0 ? "+" : ""}
                    {c.ep_next4_delta.toFixed(2)}
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-ink-500">
                    {c.price_delta.toFixed(1)}
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-ink-500">
                    {c.ownership_pct.toFixed(1)}
                  </td>
                  <td className="py-1.5">
                    <FlagChips flags={c.key_flags} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* The part that is invisible in a normal run: everyone who did NOT
              make the shortlist, and the filter that removed them. */}
          <p className="m-0 mb-2 mt-4 text-label uppercase text-ink-700">Rejected</p>
          <div className="flex flex-col gap-1.5">
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            {p.rejected.map((r: any) => (
              <details key={r.reason} className="rounded border border-line-soft px-3 py-1.5">
                <summary className="cursor-pointer text-body text-ink-300">
                  <span className="font-mono text-ink-500">{r.reason}</span>{" "}
                  <span className="tabular-nums text-ink-700">— {r.count}</span>
                </summary>
                <ul className="m-0 mt-1.5 list-none p-0">
                  {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
                  {r.examples.map((e: any) => (
                    <li key={e.player_id} className="flex flex-wrap gap-x-3 py-0.5 text-label">
                      <span className="text-ink-300">{e.name}</span>
                      <span className="text-ink-700">{e.club}</span>
                      <span className="tabular-nums text-ink-500">£{e.price.toFixed(1)}</span>
                      <span className="tabular-nums text-ink-500">
                        ep4 {e.ep_next4.toFixed(2)}
                      </span>
                      <span className="tabular-nums text-ink-700">
                        Δ {e.ep_next4_delta >= 0 ? "+" : ""}
                        {e.ep_next4_delta.toFixed(2)}
                      </span>
                    </li>
                  ))}
                  {r.count > r.examples.length && (
                    <li className="py-0.5 text-label text-ink-700">
                      +{r.count - r.examples.length} more (highest-EP shown)
                    </li>
                  )}
                </ul>
              </details>
            ))}
          </div>
        </Sub>
      ))}
    </>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function FixtureDetail({ data }: { data: Record<string, any> }) {
  return (
    <Sub title={`Swing by club (anchored GW${data.anchor})`}>
      <table className="w-full border-collapse text-body">
        <TH cols={["Club", "Swing", "Next fixtures (opponent, venue, FDR)"]} right={[1]} />
        <tbody>
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          {data.teams.map((t: any) => (
            <tr key={t.team_id} className="border-t border-line-soft">
              <td className="py-1.5 pr-3 text-ink-0">
                {t.short}
                {t.entering && <span className="ml-1.5 text-label text-in-green">easing</span>}
                {t.leaving && <span className="ml-1.5 text-label text-out-red">hardening</span>}
              </td>
              <td
                className={`py-1.5 pr-3 text-right tabular-nums ${
                  t.swing > 0 ? "text-in-green" : t.swing < 0 ? "text-out-red" : "text-ink-500"
                }`}
              >
                {t.swing > 0 ? "+" : ""}
                {t.swing.toFixed(1)}
              </td>
              <td className="py-1.5">
                <span className="flex flex-wrap gap-1">
                  {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
                  {t.next6.map((f: any, i: number) => (
                    <span
                      key={i}
                      className="rounded bg-surface-700 px-1.5 py-0.5 font-mono text-[11px] text-ink-500"
                      title={`GW${f.gw}`}
                    >
                      {f.opponent_short}
                      {f.is_home ? "(H)" : "(A)"}·{f.fdr}
                    </span>
                  ))}
                  {t.next6.length === 0 && <span className="text-label text-ink-700">none</span>}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Sub>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function StrategistDetail({ data }: { data: Record<string, any> }) {
  return (
    <>
      <Sub title="Transfer — every problem considered, and why it wasn't the one">
        <table className="w-full border-collapse text-body">
          <TH cols={["Problem", "Player", "Sev", "Best Δ", "Outcome"]} right={[3]} />
          <tbody>
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            {data.transfer_alternatives.map((a: any, i: number) => (
              <tr
                key={i}
                className={`border-t border-line-soft align-top ${
                  a.outcome === "chosen" ? "bg-in-greenbg/20" : ""
                }`}
              >
                <td className="py-1.5 pr-3 font-mono text-ink-300">{a.type}</td>
                <td className="py-1.5 pr-3 text-ink-0">{a.name}</td>
                <td className="py-1.5 pr-3 tabular-nums text-ink-500">{a.severity}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-ink-300">
                  {a.best_delta == null
                    ? "—"
                    : `${a.best_delta >= 0 ? "+" : ""}${a.best_delta.toFixed(2)}`}
                </td>
                <td className="py-1.5 text-ink-600">{a.why}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Sub>

      <Sub title="Captaincy — the starting XI ranked on anchor-gameweek projection">
        <table className="w-full border-collapse text-body">
          <TH cols={["#", "Player", "Club", "ep", "ep_next4", "Own %"]} right={[3, 4, 5]} />
          <tbody>
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            {data.ranked_starters.map((s: any, i: number) => (
              <tr key={s.player_id} className="border-t border-line-soft">
                <td className="py-1.5 pr-3 tabular-nums text-ink-700">
                  {i + 1}
                  {i === 0 && <span className="ml-1 text-in-green">C</span>}
                  {i === 1 && <span className="ml-1 text-state-blue">V</span>}
                </td>
                <td className="py-1.5 pr-3 text-ink-0">{s.name}</td>
                <td className="py-1.5 pr-3 text-ink-500">{s.club}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-ink-0">
                  {s.ep.toFixed(2)}
                </td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-ink-500">
                  {s.ep_next4.toFixed(2)}
                </td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-ink-500">
                  {s.ownership_pct.toFixed(1)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Sub>

      <Sub title="Reasoning codes, expanded">
        <ul className="m-0 list-none p-0">
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          {data.codes.map((c: any, i: number) => (
            <li key={i} className="flex flex-wrap gap-x-3 border-b border-line-soft py-1 last:border-0">
              <code className="min-w-[16ch] text-label text-ink-500">{c.code}</code>
              <span className="text-body text-ink-300">{c.plain}</span>
            </li>
          ))}
          {data.codes.length === 0 && (
            <li className="text-body text-ink-600">No reasoning codes emitted.</li>
          )}
        </ul>
      </Sub>
    </>
  );
}

// --- squad + components blob ------------------------------------------------

function SquadTable({ rows }: { rows: InspectSquadRow[] }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <Panel title="Squad — click a player for their full components blob">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-body">
          <TH cols={["", "Player", "Club", "Pos", "£", "ep", "ep_next4", "Flags"]} right={[4, 5, 6]} />
          <tbody>
            {rows.map((r) => (
              <Fragment key={r.player_id}>
                <tr
                  className={`cursor-pointer border-t border-line-soft hover:bg-surface-900 ${
                    r.role === "BENCH" ? "text-ink-600" : ""
                  }`}
                  onClick={() => setOpen(open === r.player_id ? null : r.player_id)}
                >
                  <td className="py-1.5 pr-3 text-eyebrow uppercase text-ink-700">
                    {r.role === "XI" ? "XI" : "BEN"}
                  </td>
                  <td className="py-1.5 pr-3 text-ink-0">
                    {r.name}
                    {r.is_captain && <span className="ml-1.5 text-in-green">(C)</span>}
                  </td>
                  <td className="py-1.5 pr-3 text-ink-500">{r.club}</td>
                  <td className="py-1.5 pr-3 text-ink-500">{r.position}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-ink-500">
                    {r.price.toFixed(1)}
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-ink-300">
                    {r.ep.toFixed(2)}
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-ink-0">
                    {r.ep_next4.toFixed(2)}
                  </td>
                  <td className="py-1.5">
                    <FlagChips flags={r.flags} />
                  </td>
                </tr>
                {open === r.player_id && (
                  <tr>
                    <td colSpan={8} className="border-t border-line-soft bg-surface-900 p-3">
                      <Components playerId={r.player_id} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Components({ playerId }: { playerId: number }) {
  const { data, error, loading } = useLoad<PlayerComponents>(
    useCallback(() => adminGet<PlayerComponents>(`/admin/inspect/player/${playerId}`), [playerId]),
    [playerId]
  );

  if (error) return <p className="m-0 text-body text-out-red">{error}</p>;
  if (loading || !data) return <Loading what="components" />;
  const c = data.components;
  if (!c) return <p className="m-0 text-body text-out-red">No ep_scores row for this player.</p>;

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Data basis" value={<span className="text-body">{c.data_basis}</span>} />
        <Stat label="Match method" value={<span className="text-body">{c.match_method}</span>} />
        <Stat label="Minutes prob" value={c.minutes_prob?.toFixed(3) ?? "—"} />
        <Stat label="Config" value={<span className="text-body">{c.config_version}</span>} />
      </div>

      <p className="m-0 text-body text-ink-600">
        <span className="text-ink-700">Minutes basis:</span> {c.minutes_basis}
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded border border-line px-3 py-2">
          <p className="m-0 text-label uppercase text-ink-700">
            base {c.base?.toFixed(2)}
          </p>
          <p className="m-0 mt-1 font-mono text-label text-ink-500">
            form {c.base_inputs?.form} · ppg {c.base_inputs?.ppg} · weights{" "}
            {JSON.stringify(c.base_inputs?.weights)}
          </p>
        </div>
        <div className="rounded border border-line px-3 py-2">
          <p className="m-0 text-label uppercase text-ink-700">
            attack {c.attack?.toFixed(3)}
          </p>
          <p className="m-0 mt-1 font-mono text-label text-ink-500">
            xgi/90 {c.attack_inputs?.xgi_per90} · minutes {c.attack_inputs?.minutes_sample} ·
            fallback {String(c.attack_inputs?.fallback)}
          </p>
        </div>
      </div>

      {Array.isArray(c.fixtures) && c.fixtures.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-body">
            <TH
              cols={["GW", "Opp", "H/A", "FDR", "FDR ×", "Venue ×", "CS prob", "CS pts", "Fixture EP"]}
              right={[4, 5, 6, 7, 8]}
            />
            <tbody>
              {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
              {c.fixtures.map((f: any) => (
                <tr key={f.gw} className="border-t border-line-soft">
                  <td className="py-1 pr-3 tabular-nums text-ink-700">{f.gw}</td>
                  <td className="py-1 pr-3 text-ink-300">{f.opponent_short}</td>
                  <td className="py-1 pr-3 text-ink-500">{f.is_home ? "H" : "A"}</td>
                  <td className="py-1 pr-3 tabular-nums text-ink-500">{f.fdr}</td>
                  <td className="py-1 pr-3 text-right tabular-nums text-ink-500">{f.fdr_mult}</td>
                  <td className="py-1 pr-3 text-right tabular-nums text-ink-500">{f.venue_mult}</td>
                  <td className="py-1 pr-3 text-right tabular-nums text-ink-500">{f.cs_prob}</td>
                  <td className="py-1 pr-3 text-right tabular-nums text-ink-500">{f.cs_pts}</td>
                  <td className="py-1 text-right tabular-nums text-ink-0">{f.fixture_ep}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* The blob verbatim. Everything above is a reading of it; this is the
          thing itself, so nothing is taken on trust. */}
      <details>
        <summary className="cursor-pointer text-label uppercase text-ink-700">
          Raw components JSON
        </summary>
        <pre className="mt-2 max-h-[400px] overflow-auto rounded bg-surface-950 p-3 font-mono text-[11px] leading-relaxed text-ink-500">
          {JSON.stringify(c, null, 2)}
        </pre>
      </details>
    </div>
  );
}

// --- LLM payload -----------------------------------------------------------

function Payload({ payload: p }: { payload: PayloadPreview }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(p.text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <Panel title="LLM payload preview — generated by the real serializer, never sent to a model">
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        <Stat
          label="Input tokens"
          value={`${p.token_estimate} / ${p.max_input_tokens}`}
          tone={p.over_budget ? "warn" : "ok"}
        />
        <Stat label="Model" value={<span className="text-body">{p.model}</span>} />
        <Stat
          label="Cost at output cap"
          value={p.priced ? `$${p.cost_usd.total_at_cap.toFixed(5)}` : "unpriced"}
          tone={p.priced ? undefined : "warn"}
        />
        <Stat label="Dropped groups" value={p.dropped.length || "none"} tone={p.dropped.length ? "warn" : "ok"} />
      </div>

      <p className="m-0 mt-3 text-label text-ink-700">
        ${p.rate_per_mtok.input}/Mtok in, ${p.rate_per_mtok.output}/Mtok out · input $
        {p.cost_usd.input.toFixed(6)} + output at the {p.output_max_tokens}-token cap $
        {p.cost_usd.output_at_cap.toFixed(6)}. Output is priced at the CAP, not a guess.
      </p>

      <p className="m-0 mb-2 mt-4 text-label uppercase text-ink-700">
        Evidence &amp; fixture blocks — kept vs dropped by the budget rule
      </p>
      <div className="flex flex-wrap gap-1.5">
        {p.blocks.map((b, i) => (
          <span
            key={i}
            title={b.group}
            className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
              b.included
                ? "bg-in-greenbg text-in-green"
                : "bg-out-redbg text-out-red line-through"
            }`}
          >
            {b.label}
          </span>
        ))}
      </div>

      <div className="mt-4 flex items-center gap-3">
        <button type="button" onClick={copy} className="btn-ghost py-2">
          {copied ? "Copied" : "Copy payload"}
        </button>
        <span className="text-label text-ink-700">
          Verbatim — this is the exact string the writer would receive.
        </span>
      </div>
      <pre className="mt-2 max-h-[520px] overflow-auto rounded bg-surface-950 p-3 font-mono text-[11px] leading-relaxed text-ink-300">
        {p.text}
      </pre>
    </Panel>
  );
}

// --- free vs paid ----------------------------------------------------------

function TierCompare({
  free, paid, delta, gameweek,
}: { free: Tier; paid: Tier; delta: string[]; gameweek: number }) {
  // Rendered through the REAL BriefView, from the REAL gate_sections output, so
  // the left column is exactly what a free reader's browser would paint once
  // PAYWALL_ENABLED flips — lock cards and all.
  const asBrief = (t: Tier): Brief => ({
    brief_id: null,
    draft_id: null,
    gameweek,
    team_name: null,
    overall_rank: null,
    bank: null,
    sections: t.sections,
    locked_sections: t.locked_sections,
    cached: false,
  });

  return (
    <Panel title="Free vs paid — the real allow-list, applied twice">
      <p className="m-0 mb-4 max-w-[80ch] text-body leading-relaxed text-ink-600">
        Left is <strong className="text-ink-300">free tier</strong> with the paywall forced
        on; right is <strong className="text-ink-300">paid</strong>. The paid delta is{" "}
        {delta.length ? (
          <span className="text-in-green">{delta.join(", ")}</span>
        ) : (
          <span className="text-out-red">nothing — the writer produced no paid section</span>
        )}
        . Gating runs through <code>graph.sections.gate_sections</code>, the same function
        Stage B will call, so a section missing on the left that should be there is a real
        paywall bug, not a preview artefact.
      </p>

      <div className="grid gap-5 lg:grid-cols-2">
        <div>
          <p className="m-0 mb-2 text-eyebrow font-semibold uppercase text-ink-700">
            Free · {Object.keys(free.sections).length} sections ·{" "}
            {free.locked_sections.length} locked
          </p>
          <BriefView brief={asBrief(free)} />
        </div>
        <div>
          <p className="m-0 mb-2 text-eyebrow font-semibold uppercase text-ink-700">
            Paid · {Object.keys(paid.sections).length} sections
          </p>
          <BriefView brief={asBrief(paid)} />
        </div>
      </div>
    </Panel>
  );
}

// --- small shared bits ------------------------------------------------------

function Sub({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-4 first:mt-0">
      <p className="m-0 mb-2 text-label font-semibold text-ink-300">{title}</p>
      <div className="overflow-x-auto">{children}</div>
    </div>
  );
}

/**
 * `right` lists the column indices whose CELLS are right-aligned, so the header
 * sits over its own numbers. Without it every header is left-aligned above a
 * right-aligned column and the table reads as if the values are shifted a column
 * across — which is exactly how it looked before this argument existed.
 */
function TH({ cols, right = [] }: { cols: string[]; right?: number[] }) {
  return (
    <thead>
      <tr className="text-eyebrow uppercase text-ink-700">
        {cols.map((c, i) => (
          <th
            key={i}
            className={`py-1.5 pr-3 font-semibold ${right.includes(i) ? "text-right" : "text-left"}`}
          >
            {c}
          </th>
        ))}
      </tr>
    </thead>
  );
}
