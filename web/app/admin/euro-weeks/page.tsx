"use client";

import { useCallback, useState } from "react";
import { Failed, Loading, PageTitle, useLoad } from "@/components/AdminBits";
import { adminGet, adminPost, EuroWeekRow, Overview, SaveResult } from "@/lib/admin";

const COMPETITIONS = ["UCL", "UEL", "UECL"];
const IMPORTANCE = [
  { v: 1, label: "1 — dead rubber" },
  { v: 2, label: "2 — normal tie" },
  { v: 3, label: "3 — must-win / knockout" },
];

type Bundle = { rows: EuroWeekRow[]; teams: Overview["teams"] };

// The whole grid saves in one submission (POST /admin/euro-weeks/replace):
// upsert what's here, delete what isn't. The server skips rows that are
// byte-identical, so re-saving an untouched table does NOT bump
// euro_weeks_version — which is half of the brief cache key.
export default function AdminEuroWeeks() {
  const load = useCallback(async (): Promise<Bundle> => {
    const [rows, overview] = await Promise.all([
      adminGet<EuroWeekRow[]>("/admin/euro-weeks"),
      adminGet<Overview>("/admin/overview"),
    ]);
    return { rows, teams: overview.teams };
  }, []);
  const { data, error, loading, reload } = useLoad<Bundle>(load, []);

  const [rows, setRows] = useState<EuroWeekRow[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading what="European weeks" />;

  const current = rows ?? data.rows;
  const dirty = rows !== null && JSON.stringify(rows) !== JSON.stringify(data.rows);
  const teams = data.teams;

  const patch = (i: number, field: keyof EuroWeekRow, v: string | number) =>
    setRows(current.map((r, j) => (j === i ? { ...r, [field]: v } : r)));

  const addRow = () =>
    setRows([
      ...current,
      {
        team_id: teams[0]?.id ?? 1,
        team_short: teams[0]?.short_name,
        gameweek: 1,
        competition: "UCL",
        tie_importance: 2,
      },
    ]);

  const removeRow = (i: number) => setRows(current.filter((_, j) => j !== i));

  // Two rows for the same club+GW would silently overwrite each other; the
  // server rejects it too, but naming the clash here beats a bare 400.
  const clash = (() => {
    const seen = new Set<string>();
    for (const r of current) {
      const k = `${r.team_id}:${r.gameweek}`;
      if (seen.has(k)) return k;
      seen.add(k);
    }
    return null;
  })();

  const save = async () => {
    setSaving(true);
    setFailure(null);
    try {
      const res = await adminPost<SaveResult>("/admin/euro-weeks/replace", {
        rows: current.map((r) => ({
          team_id: Number(r.team_id),
          gameweek: Number(r.gameweek),
          competition: r.competition,
          tie_importance: Number(r.tie_importance),
        })),
      });
      setStatus(res.changed ? `Saved — ${res.changed} row(s) changed` : "No change");
      setRows(null);
      reload();
    } catch (e: any) {
      setFailure(e?.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <PageTitle
        right={
          <div className="flex items-center gap-3">
            {dirty && <span className="text-label text-state-blue">Unsaved changes</span>}
            {!dirty && status && <span className="text-label text-ink-600">{status}</span>}
            {failure && <span className="text-label text-out-red">{failure}</span>}
            <button type="button" onClick={addRow} className="btn-ghost py-2">
              Add row
            </button>
            <button
              type="button"
              onClick={save}
              disabled={!dirty || saving || !!clash}
              className="btn-primary py-2"
            >
              {saving ? "Saving…" : "Save all"}
            </button>
          </div>
        }
      >
        European weeks
      </PageTitle>

      {clash && (
        <p className="mb-4 text-body text-out-red" role="alert">
          Two rows share the same club and gameweek — remove one before saving.
        </p>
      )}

      <div className="card overflow-x-auto p-4">
        <table className="w-full border-collapse text-body">
          <thead>
            <tr className="text-left text-eyebrow uppercase text-ink-700">
              <th className="py-1.5 pr-3 font-semibold">Club</th>
              <th className="py-1.5 pr-3 font-semibold">Gameweek</th>
              <th className="py-1.5 pr-3 font-semibold">Competition</th>
              <th className="py-1.5 pr-3 font-semibold">Tie importance</th>
              <th className="py-1.5 font-semibold" />
            </tr>
          </thead>
          <tbody>
            {current.map((r, i) => (
              <tr key={i} className="border-t border-line-soft">
                <td className="py-1.5 pr-3">
                  <select
                    value={r.team_id}
                    onChange={(e) => patch(i, "team_id", Number(e.target.value))}
                    aria-label="Club"
                    className="field py-1.5"
                  >
                    {teams.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.short_name} — {t.name}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="py-1.5 pr-3">
                  <input
                    type="number"
                    min={1}
                    max={38}
                    value={r.gameweek}
                    onChange={(e) => patch(i, "gameweek", Number(e.target.value))}
                    aria-label="Gameweek"
                    className="field w-[90px] py-1.5 tabular-nums"
                  />
                </td>
                <td className="py-1.5 pr-3">
                  <select
                    value={r.competition}
                    onChange={(e) => patch(i, "competition", e.target.value)}
                    aria-label="Competition"
                    className="field py-1.5"
                  >
                    {COMPETITIONS.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="py-1.5 pr-3">
                  <select
                    value={r.tie_importance}
                    onChange={(e) => patch(i, "tie_importance", Number(e.target.value))}
                    aria-label="Tie importance"
                    className="field py-1.5"
                  >
                    {IMPORTANCE.map((o) => (
                      <option key={o.v} value={o.v}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="py-1.5">
                  <button
                    type="button"
                    onClick={() => removeRow(i)}
                    className="text-label font-semibold text-out-red hover:underline"
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {current.length === 0 && (
          <p className="m-0 pt-3 text-body text-ink-600">
            No European weeks recorded. Empty is valid — the rotation dampener is a no-op.
          </p>
        )}
      </div>
    </>
  );
}
