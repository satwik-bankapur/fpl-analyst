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

// Accounts build (2026-08-04). READ-ONLY: there is no admin write path for an
// account, and no impersonation — a console that can become a customer is a
// token worth stealing for more than a dashboard.
export type AccountRow = {
  id: number;
  email: string;
  auth_provider: string;
  terms_version: string | null;
  terms_accepted_at: string | null;
  terms_current: boolean;
  subscription_status: string | null;
  subscription_plan: string | null;
  entitled: boolean;
  fpl_team_id: number | null;
  active_sessions: number;
  created_at: string | null;
  last_login_at: string | null;
};

export type AccountList = {
  accounts: AccountRow[];
  count: number;
  terms_version: string;
};

// --- flagged-player work queue (mirrors api/admin.flag_queue) ---------------

export type FlagKind = "actionable" | "transient" | "known_limitation";

export type QueuePlayer = {
  id: number;
  web_name: string;
  team_id: number;
  team_short: string;
  position: string;
  price: number;
  news: string;
  ep_next4: number;
  flag: string;
};

export type QueueClub = {
  team_id: number;
  team_short: string;
  team_name: string;
  count: number;
  top_ep_next4: number;
  /** count x highest ep_next4 — the note-writing payoff ranking. */
  score: number;
  has_note: boolean;
  is_sample: boolean;
  scored_players: number;
  distinct_ep_next4: number;
  /** The whole club's EP has collapsed onto a handful of baseline values. */
  identical_ep: boolean;
  players: QueuePlayer[];
};

export type FlagGroup = {
  flag: string;
  kind: FlagKind;
  explanation: string;
  count: number;
  note_improvable: boolean;
  clubs: QueueClub[];
  players: QueuePlayer[];
};

export type FlagQueue = {
  total_flagged_players: number;
  groups: FlagGroup[];
  clubs_total: number;
  clubs_with_notes: number;
};

// --- the reasoning inspector (mirrors api/inspect.py) -----------------------

export type BriefRef = {
  brief_id: number;
  draft_id: string | null;
  fpl_team_id: number | null;
  gameweek: number;
  model: string;
  created_at: string | null;
  input_hash: string;
};

/**
 * One node's working. `data` is deliberately untyped: each stage records a
 * different shape and it is a DIAGNOSTIC, not a contract — see graph/trace.py.
 * The inspector page narrows it per node at the point of render.
 */
export type TraceStage = {
  node: string;
  summary: string[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any>;
};

export type PayloadPreview = {
  text: string;
  token_estimate: number;
  max_input_tokens: number;
  over_budget: boolean;
  dropped: string[];
  blocks: { group: string; label: string; included: boolean }[];
  model: string;
  priced: boolean;
  rate_per_mtok: { input: number; output: number };
  output_max_tokens: number;
  cost_usd: { input: number; output_at_cap: number; total_at_cap: number };
};

export type InspectSquadRow = {
  player_id: number;
  name: string;
  club: string;
  position: string;
  price: number;
  slot: number;
  role: "XI" | "BENCH";
  is_captain: boolean;
  ep: number;
  ep_next4: number;
  flags: string[];
};

export type Tier = { sections: Record<string, string>; locked_sections: string[] };

export type Inspection = {
  target: { kind: "draft" | "team"; draft_id?: string; team_id?: number; gameweek: number };
  error: { code: string; message: string } | null;
  available: { trace: boolean; payload: boolean; reason: string | null };
  trace: TraceStage[];
  warnings: string[];
  brief_md?: string;
  squad?: InspectSquadRow[];
  cache?: {
    matches: boolean | null;
    model: string | null;
    created_at: string | null;
    current_writer: string;
    superseded: boolean;
  } | null;
  free?: Tier;
  paid?: Tier;
  paid_delta?: string[];
  paid_vocabulary?: string[];
  payload?: PayloadPreview | null;
};

export type PlayerComponents = {
  player_id: number;
  web_name: string;
  full_name: string;
  team_short: string;
  position: string;
  price: number;
  status: string;
  news: string;
  chance_of_playing: number | null;
  ownership_pct: number;
  gameweek: number | null;
  ep: number | null;
  ep_next4: number | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  components: Record<string, any> | null;
};
