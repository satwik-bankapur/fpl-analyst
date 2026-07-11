// Thin fetch layer to the FastAPI backend. No client library — plain fetch (§2).
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Health = {
  last_sync: string | null;
  mode: "PRESEASON" | "IN_SEASON";
  next_gw: number | null;
  next_deadline: string | null;
};

export type PlayerRow = {
  id: number;
  web_name: string;
  team_short: string;
  position: "GK" | "DEF" | "MID" | "FWD";
  price: number;
};

export type Preview = {
  gameweek: number;
  sections: Record<string, string>;
  generated_at: string;
};

export type Brief = {
  brief_id: number | null;
  draft_id: string | null;
  gameweek: number;
  team_name: string | null;
  overall_rank: number | null;
  bank: number | null;
  sections: Record<string, string>;
  locked_sections: string[];
  cached: boolean;
};

export type DraftSubmit = {
  player_ids: number[];
  captain_id: number | null;
  email?: string | null;
};

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body?.detail?.error || body?.error || `HTTP ${res.status}`);
    (err as any).status = res.status;
    (err as any).detail = body?.detail || body;
    throw err;
  }
  return res.json();
}

export function getHealth(): Promise<Health> {
  return fetch(`${API}/api/health`, { cache: "no-store" }).then(json<Health>);
}

export function getPreview(): Promise<Preview> {
  return fetch(`${API}/api/preview/latest`, { cache: "no-store" }).then(json<Preview>);
}

export function getPlayers(): Promise<PlayerRow[]> {
  return fetch(`${API}/api/players`, { cache: "no-store" }).then(json<PlayerRow[]>);
}

export function postDraftBrief(draft: DraftSubmit): Promise<Brief> {
  return fetch(`${API}/api/brief`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ draft: { player_ids: draft.player_ids, captain_id: draft.captain_id }, email: draft.email || null }),
  }).then(json<Brief>);
}

export function getDraftBrief(draftId: string): Promise<Brief> {
  return fetch(`${API}/api/brief/draft/${draftId}`, { cache: "no-store" }).then(json<Brief>);
}

export function postSignup(email: string, teamId?: number | null): Promise<{ ok: boolean }> {
  return fetch(`${API}/api/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, team_id: teamId ?? null, source: "landing" }),
  }).then(json<{ ok: boolean }>);
}

// Canonical section order + display titles (shared by BriefView / preview).
export const SECTION_ORDER = ["verdict", "transfer", "captaincy", "bench_plan", "chips", "watchlist", "fixtures"];
export const SECTION_TITLES: Record<string, string> = {
  verdict: "Verdict",
  transfer: "Transfer",
  captaincy: "Captaincy",
  bench_plan: "Bench order",
  chips: "Chips",
  watchlist: "Watchlist",
  fixtures: "Fixture notes",
};
