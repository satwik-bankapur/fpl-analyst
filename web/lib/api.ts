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

// `token` is the JWT minted by the send job. Absent or expired, the API answers
// with the free sections and populates locked_sections — it never 401s, so the
// page renders either way and the lock cards do the explaining.
export function getTeamBrief(teamId: number, gw: number, token?: string | null): Promise<Brief> {
  const q = token ? `?token=${encodeURIComponent(token)}` : "";
  return fetch(`${API}/api/brief/${teamId}/${gw}${q}`, { cache: "no-store" }).then(json<Brief>);
}

// Sends a PLAN NAME. There is deliberately no price field to send — the server
// resolves what a plan costs (api/billing.price_id_for_plan), so nothing the
// browser can edit changes what the customer pays.
export function postCheckout(
  plan: "monthly" | "season",
  email: string,
  teamId?: number | null
): Promise<{ checkout_url: string }> {
  return fetch(`${API}/api/checkout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan, email, team_id: teamId ?? null }),
  }).then(json<{ checkout_url: string }>);
}

// The mail-consent routes take `e`/`t` as QUERY params, not a body: the same
// URLs have to work as a bare link in an email client with no JavaScript.
export function postMailConsent(
  action: "unsubscribe" | "resubscribe" | "delete",
  e: string,
  t: string
): Promise<{ ok: boolean; status?: string }> {
  const q = `?e=${encodeURIComponent(e)}&t=${encodeURIComponent(t)}`;
  return fetch(`${API}/api/email/${action}${q}`, { method: "POST" }).then(
    json<{ ok: boolean; status?: string }>
  );
}

// `source` tags where the address was captured (landing hero, brief footer, …)
// so signup volume can be attributed per surface. The backend takes any string.
export function postSignup(email: string, teamId?: number | null, source = "landing"): Promise<{ ok: boolean }> {
  return fetch(`${API}/api/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, team_id: teamId ?? null, source }),
  }).then(json<{ ok: boolean }>);
}

// MIRROR OF graph/config.py's FPL squad rules — that module is the source of
// truth and POST /api/brief re-checks every one of these server-side. These
// exist so the picker can PREVENT an illegal squad; they do not enforce it.
// Mirrored ONCE, here — never re-typed as a literal in a component.
export const MAX_PER_CLUB = 3;
export const SQUAD_SIZE = 15;
export const SQUAD_BUDGET = 100.0;
export const POSITION_QUOTA: Record<string, number> = { GK: 2, DEF: 5, MID: 5, FWD: 3 };

// MIRROR OF graph/sections.py — keep the two in sync, that module is the source
// of truth. The KEYS and their ORDER are the API contract; the backend splitter
// maps every writer's headers onto exactly these keys.
//
// `fixtures` was removed on 2026-07-30: it existed only in the stub writer, was
// never producible by the LLM writer, and so could not be gated coherently. Its
// content now renders inside `bench_plan` ("the plan" IS the forward view).
export const SECTION_ORDER = ["verdict", "transfer", "captaincy", "bench_plan", "chips", "watchlist"];
export const SECTION_TITLES: Record<string, string> = {
  verdict: "The verdict",
  transfer: "This week's transfer",
  captaincy: "Captaincy",
  bench_plan: "Bench order & the plan",
  chips: "Chips",
  watchlist: "Watchlist",
};

// Sections the Stage B paywall withholds. Mirrors graph.sections.PAID_SECTIONS —
// used for copy only; the backend allow-list is what actually gates content.
export const PAID_SECTIONS = ["bench_plan", "chips", "watchlist"];

// The generic preview is a public teaser with its own vocabulary, deliberately
// disjoint from SECTION_ORDER so it can never be mistaken for a lockable section.
export const PREVIEW_ORDER = ["the_week_ahead", "form_and_fixtures", "the_differentials", "trap_of_the_week"];
export const PREVIEW_TITLES: Record<string, string> = {
  the_week_ahead: "The week ahead",
  form_and_fixtures: "Form and fixtures",
  the_differentials: "The differentials",
  trap_of_the_week: "Trap of the week",
};
