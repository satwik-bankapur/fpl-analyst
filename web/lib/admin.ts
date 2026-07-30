// Admin fetch layer. Same plain-fetch approach as lib/api.ts, plus the one thing
// the public API doesn't need: a Bearer token on every call.
//
// The token is the existing ADMIN_TOKEN — there is no login, no session and no
// account (docs/CLAUDE.md, "No accounts in v1"). It lives in localStorage so a
// Sunday pass through /admin costs one paste, and it is cleared the moment the
// server rejects it, so a rotated token re-prompts instead of failing forever.
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const KEY = "fpl_admin_token";

/** Fired on any 401 so the shell can re-prompt from wherever the call happened. */
export const UNAUTHORIZED_EVENT = "admin-unauthorized";

export function getToken(): string {
  return typeof window === "undefined" ? "" : localStorage.getItem(KEY) || "";
}

export function setToken(t: string) {
  localStorage.setItem(KEY, t.trim());
}

export function clearToken() {
  localStorage.removeItem(KEY);
}

export class Unauthorized extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}/api${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      Authorization: `Bearer ${getToken()}`,
    },
  });
  if (res.status === 401) {
    // A wrong or expired token is not an error to render — it is a re-prompt.
    // Drop it first so the retry can't loop on the same bad value.
    clearToken();
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    throw new Unauthorized("unauthorized");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.detail?.error || `HTTP ${res.status}`);
  }
  return res.json();
}

export const adminGet = <T,>(path: string) => call<T>(path);
export const adminPost = <T,>(path: string, body: unknown) =>
  call<T>(path, { method: "POST", body: JSON.stringify(body) });

/**
 * Verifies a token by making the cheapest gated call there is.
 *
 * "unreachable" is a distinct outcome from "rejected" on purpose: a CORS
 * mismatch or a stopped API also fails this call, and telling the operator their
 * token is wrong when the API simply isn't answering sends them to fix the one
 * thing that isn't broken.
 */
export async function checkToken(token: string): Promise<"ok" | "rejected" | "unreachable"> {
  try {
    const res = await fetch(`${API}/api/admin/overview`, {
      cache: "no-store",
      headers: { Authorization: `Bearer ${token.trim()}` },
    });
    return res.ok ? "ok" : res.status === 401 ? "rejected" : "unreachable";
  } catch {
    return "unreachable"; // network / CORS — fetch never reached a response
  }
}

// --- response types (mirror api/admin.py) ----------------------------------

export type TeamRef = { id: number; name: string; short_name: string };

export type Overview = {
  mode: "PRESEASON" | "IN_SEASON";
  last_sync: string | null;
  next_gw: number | null;
  next_deadline: string | null;
  counts: Record<string, number>;
  archive_by_season: Record<string, number>;
  match_method: Record<string, number>;
  teams: TeamRef[];
};

export type TopRow = {
  id: number;
  web_name: string;
  team_short: string;
  position: string;
  price: number;
  ep: number;
  ep_next4: number;
  flags: string[];
};

export type AdminPlayer = TopRow & {
  status: string;
  news: string;
  ep: number | null;
  ep_next4: number | null;
};

export type PlayerSearch = { total: number; players: AdminPlayer[] };

export type NoteRow = {
  team_id: number;
  team_name: string;
  team_short: string;
  note: string;
  is_sample: boolean;
  updated_at: string | null;
};

export type EuroWeekRow = {
  team_id: number;
  team_short?: string;
  gameweek: number;
  competition: string;
  tie_importance: number;
  updated_at?: string;
};

export type FixtureRun = {
  team_id: number;
  team_short: string;
  team_name: string;
  anchor: number | null;
  swing: number;
  fixtures: { gw: number; opponent_short: string; is_home: boolean; fdr: number }[];
};

/** Saves report whether they actually wrote — an unchanged save is a no-op by design. */
export type SaveResult = { ok: boolean; changed: boolean | number };
