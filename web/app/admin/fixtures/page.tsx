"use client";

import { useCallback, useState } from "react";
import { FDR_CLASS, Failed, Loading, PageTitle, Panel, useLoad } from "@/components/AdminBits";
import { adminGet, FixtureRun, Overview } from "@/lib/admin";

const N = 6;

export default function AdminFixtures() {
  const { data: overview, error, loading, reload } = useLoad<Overview>(
    useCallback(() => adminGet<Overview>("/admin/overview"), []),
    []
  );
  const [team, setTeam] = useState("");

  const run = useLoad<FixtureRun | null>(
    useCallback(
      () => (team ? adminGet<FixtureRun>(`/admin/fixtures/${team}?n=${N}`) : Promise.resolve(null)),
      [team]
    ),
    [team]
  );

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !overview) return <Loading what="clubs" />;

  const r = run.data;

  return (
    <>
      <PageTitle>Fixtures</PageTitle>

      <div className="mb-5 flex flex-wrap items-center gap-3">
        <select
          value={team}
          onChange={(e) => setTeam(e.target.value)}
          aria-label="Club"
          className="field w-auto min-w-[220px]"
        >
          <option value="">Pick a club…</option>
          {overview.teams.map((t) => (
            <option key={t.id} value={t.short_name}>
              {t.short_name} — {t.name}
            </option>
          ))}
        </select>
        {r?.anchor != null && <span className="text-body text-ink-600">from GW{r.anchor}</span>}
      </div>

      {run.error && <Failed error={run.error} onRetry={run.reload} />}
      {team && run.loading && <Loading what="fixtures" />}

      {r && !run.loading && (
        <Panel title={`${r.team_name} — next ${N}`}>
          {r.fixtures.length === 0 ? (
            <p className="m-0 text-body text-ink-600">
              No scheduled fixtures in that window{r.anchor == null && " — nothing is scored yet"}.
            </p>
          ) : (
            <>
              <div className="flex flex-wrap gap-2">
                {r.fixtures.map((f, i) => (
                  <div
                    key={`${f.gw}-${i}`}
                    className={`flex min-w-[92px] flex-col items-center rounded px-3 py-2 ${FDR_CLASS[f.fdr] || "bg-surface-700 text-ink-500"}`}
                  >
                    <span className="text-eyebrow font-semibold uppercase opacity-70">GW{f.gw}</span>
                    <span className="mt-1 text-title font-semibold">
                      {f.is_home ? f.opponent_short : f.opponent_short.toLowerCase()}
                    </span>
                    <span className="mt-0.5 text-label opacity-80">
                      {f.is_home ? "H" : "A"} · FDR {f.fdr}
                    </span>
                  </div>
                ))}
              </div>

              <p className="m-0 mt-4 text-body text-ink-500">
                Swing{" "}
                <span
                  className={`font-semibold tabular-nums ${
                    r.swing > 0.3 ? "text-in-green" : r.swing < -0.3 ? "text-out-red" : "text-ink-300"
                  }`}
                >
                  {r.swing > 0 ? "+" : ""}
                  {r.swing.toFixed(2)}
                </span>{" "}
                — average FDR of the first three minus the next three. Positive means the run gets
                easier; below about −0.5 is what raises a fixture-cliff problem.
                {r.fixtures.length < 6 && " Needs six fixtures to mean anything; this run has fewer."}
              </p>
            </>
          )}
        </Panel>
      )}

      {!team && <p className="m-0 text-body text-ink-600">Pick a club to see its next six.</p>}
    </>
  );
}
