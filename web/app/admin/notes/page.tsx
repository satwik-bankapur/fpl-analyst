"use client";

import { useCallback, useState } from "react";
import { Failed, Loading, PageTitle, useLoad, timeAgo } from "@/components/AdminBits";
import { adminGet, adminPost, NoteRow, SaveResult } from "@/lib/admin";

// Twenty cards, one per club. The skeleton below is PLACEHOLDER text, never
// pre-filled content: a note that exists but was never written would still bump
// `notes_version` and would still be handed to the writer as fact. Empty means
// empty; the placeholder just stops the blank-page problem by naming the fields
// worth filling.
const SKELETON = `Manager / shape:
Penalties:
Set-pieces:
Rotation risk:
Watch:`;

type Draft = Record<number, string>;

export default function AdminNotes() {
  const { data, error, loading, reload } = useLoad<NoteRow[]>(
    useCallback(() => adminGet<NoteRow[]>("/admin/notes"), []),
    []
  );
  const [draft, setDraft] = useState<Draft>({});
  const [saving, setSaving] = useState<number | null>(null);
  const [saved, setSaved] = useState<Record<number, string>>({});
  const [failed, setFailed] = useState<Record<number, string>>({});

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading what="team notes" />;

  const value = (r: NoteRow) => (r.team_id in draft ? draft[r.team_id] : r.note);
  const dirty = (r: NoteRow) => r.team_id in draft && draft[r.team_id] !== r.note;

  const save = async (r: NoteRow) => {
    setSaving(r.team_id);
    setFailed((f) => ({ ...f, [r.team_id]: "" }));
    try {
      const res = await adminPost<SaveResult>("/admin/notes", {
        team_id: r.team_id,
        note: value(r),
      });
      // The server skips identical writes so the brief cache key doesn't move.
      // Say which happened rather than claiming a save that never occurred.
      setSaved((s) => ({ ...s, [r.team_id]: res.changed ? "Saved" : "No change" }));
      setDraft((d) => {
        const next = { ...d };
        delete next[r.team_id];
        return next;
      });
      reload();
    } catch (e: any) {
      setFailed((f) => ({ ...f, [r.team_id]: e?.message || "Save failed" }));
    } finally {
      setSaving(null);
    }
  };

  const dirtyCount = data.filter(dirty).length;

  return (
    <>
      <PageTitle
        right={
          <span className="text-body text-ink-600">
            {data.filter((r) => r.note.trim()).length} of {data.length} written
            {dirtyCount > 0 && <span className="text-state-blue"> · {dirtyCount} unsaved</span>}
          </span>
        }
      >
        Team notes
      </PageTitle>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {data.map((r) => (
          <section key={r.team_id} className="card flex flex-col p-4">
            <div className="mb-2 flex items-baseline justify-between gap-2">
              <h2 className="m-0 text-title font-semibold">{r.team_short}</h2>
              <span className="text-eyebrow uppercase text-ink-700">
                {r.updated_at ? timeAgo(r.updated_at) : "never"}
                {r.is_sample && " · sample"}
              </span>
            </div>
            <p className="m-0 mb-2 text-label text-ink-700">{r.team_name}</p>

            <textarea
              value={value(r)}
              onChange={(e) => setDraft((d) => ({ ...d, [r.team_id]: e.target.value }))}
              placeholder={SKELETON}
              rows={7}
              aria-label={`Note for ${r.team_name}`}
              className="field min-h-[150px] resize-y font-normal leading-relaxed"
            />

            <div className="mt-3 flex items-center gap-3">
              <button
                type="button"
                onClick={() => save(r)}
                disabled={!dirty(r) || saving === r.team_id}
                className="btn-primary px-3 py-2 text-label"
              >
                {saving === r.team_id ? "Saving…" : "Save"}
              </button>
              {dirty(r) && <span className="text-label text-state-blue">Unsaved changes</span>}
              {!dirty(r) && saved[r.team_id] && (
                <span className="text-label text-ink-600">{saved[r.team_id]}</span>
              )}
              {failed[r.team_id] && <span className="text-label text-out-red">{failed[r.team_id]}</span>}
            </div>
          </section>
        ))}
      </div>
    </>
  );
}
