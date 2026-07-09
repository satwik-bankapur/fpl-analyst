# FPL Analyst — Data Pipeline Spec (Layer 1)

Implementation-ready spec. No code here by design: implement against this document.
Stack context: Python + FastAPI backend, Postgres (SQLite acceptable in dev), APScheduler for jobs.

---

## 1. FPL API endpoints

Base URL: `https://fantasy.premierleague.com/api/`
No auth. JSON responses. Send a browser-like `User-Agent` header (default Python UA is occasionally 403'd).

### 1.1 `GET bootstrap-static/` — primary sync target (~2MB)
Keys used:

**`elements`** (~700 players). Persist per player:
- `id` (int, stable per season), `web_name`, `first_name`, `second_name`
- `team` (team id), `element_type` (1 GK, 2 DEF, 3 MID, 4 FWD)
- `now_cost` (int, price ×10 — 55 means £5.5m; divide by 10 on ingest)
- `total_points`, `minutes`, `goals_scored`, `assists`, `clean_sheets`, `bonus`
- `form` (STRING — avg points/game over last 30 days), `points_per_game` (STRING)
- `status` (char: a=available, d=doubtful, i=injured, s=suspended, u=unavailable, n=not in squad)
- `chance_of_playing_next_round` (int or NULL — NULL means no flag, treat as 100)
- `news` (free-text injury/availability note — persist verbatim; the brief writer uses it)
- `selected_by_percent` (STRING — ownership %)
- `expected_goals`, `expected_assists`, `expected_goal_involvements`, `expected_goals_conceded` (STRINGS)
- `transfers_in_event`, `transfers_out_event`, `cost_change_event`

⚠ Cast string-numerics to numeric ON INGEST, never at query time.

**`teams`** (20 rows): `id`, `name`, `short_name`, `strength`,
`strength_overall_home/away`, `strength_attack_home/away`, `strength_defence_home/away`
(the split strength fields are more granular than FDR — the heuristic layer will use them).

**`events`** (38 gameweeks): `id`, `name`, `deadline_time` (ISO UTC — drives the scheduler),
`finished`, `is_current`, `is_next`, `average_entry_score`, `most_captained`, `chip_plays`.

### 1.2 `GET fixtures/` — all 380 fixtures
Per fixture: `id`, `event` (gameweek id — **NULL if unscheduled/postponed**), `team_h`, `team_a`,
`team_h_difficulty`, `team_a_difficulty` (FDR 1–5), `kickoff_time` (nullable), `finished`,
`started`, `team_h_score`, `team_a_score`.
Pull all; filter locally. (Param `?event={gw}` exists; don't bother.)

### 1.3 `GET entry/{team_id}/` — manager profile
`id`, `name` (team name), `player_first_name`, `player_last_name`,
`summary_overall_points`, `summary_overall_rank`, `current_event`.
**404 = team ID does not exist** → this is the team-ID validation call.

### 1.4 `GET entry/{team_id}/event/{gw}/picks/` — squad for a gameweek
- `picks`: 15 entries — `element` (player id), `position` (1–15; 12–15 = bench in order),
  `is_captain`, `is_vice_captain`, `multiplier`
- `entry_history`: `bank` (×10), `value` (×10), `event_transfers`, `points`
- `active_chip` (string or null)
**404 if the gameweek hasn't started for that entry OR pre-season OR invalid ID** — see §6.

### 1.5 `GET entry/{team_id}/transfers/` — full transfer history
NOT used in v1. Noted for future "season review" feature.

### 1.6 `GET element-summary/{player_id}/` — per-player GW history + past seasons
NOT used in v1 (700 calls for marginal gain). Everything the heuristic needs is in 1.1 + 1.2.

---

## 2. Database schema

Types are Postgres; all compatible with SQLite for dev.

### `teams`
| field | type |
|---|---|
| id | int PK (FPL id) |
| name | text |
| short_name | text |
| strength_overall_home / _away | int |
| strength_attack_home / _away | int |
| strength_defence_home / _away | int |
| updated_at | timestamptz |

### `players` — current-season snapshot, overwritten each sync
| field | type | notes |
|---|---|---|
| id | int PK | FPL element id |
| web_name | text | |
| full_name | text | first + second |
| team_id | int FK→teams | |
| position | smallint | 1–4 |
| price | numeric(4,1) | £m (now_cost / 10) |
| total_points | int | |
| form | numeric(4,1) | cast from string |
| ppg | numeric(4,2) | cast from string |
| minutes | int | |
| status | char(1) | |
| chance_of_playing | int NULL | NULL = no flag = treat as 100 |
| news | text | verbatim |
| ownership_pct | numeric(5,2) | cast from string |
| xg, xa, xgi, xgc | numeric(6,2) | cast from strings |
| transfers_in_event | int | |
| transfers_out_event | int | |
| updated_at | timestamptz | |

### `players_archive` — prior seasons, append-only
Same columns as `players` plus `season` text (e.g. `'2025-26'`).
PK (`season`, `id`). Written ONLY by the season-rollover routine (§6.2).
This table is what powers pre-season analysis.

### `player_history` — daily snapshot log, append-only
| field | type |
|---|---|
| player_id | int |
| snapshot_date | date |
| gameweek | int NULL |
| price | numeric(4,1) |
| form | numeric(4,1) |
| ownership_pct | numeric(5,2) |
| status | char(1) |
| total_points | int |
PK (`player_id`, `snapshot_date`).
**WRITE-ONLY IN v1 — intentional.** No v1 code reads this table. It exists because price/ownership time-series are unrecoverable if not logged from day one; future features (price-change prediction, ownership trends, season review) will consume it. Do not remove as dead code.

### `gameweeks`
| field | type |
|---|---|
| id | int PK |
| name | text |
| deadline | timestamptz |
| finished | bool |
| is_current | bool |
| is_next | bool |
| avg_score | int NULL |
| most_captained | int NULL |

### `fixtures`
| field | type |
|---|---|
| id | int PK |
| gameweek | int NULL FK→gameweeks | NULL = postponed/unscheduled |
| home_team / away_team | int FK→teams |
| home_difficulty / away_difficulty | smallint |
| kickoff | timestamptz NULL |
| finished | bool |
| home_score / away_score | int NULL |

### `ep_scores` — computed output of heuristic layer, rewritten each sync
| field | type | notes |
|---|---|---|
| player_id | int | |
| gameweek | int | horizon anchor (next GW) |
| ep | numeric(5,2) | next-GW expected points |
| ep_next4 | numeric(6,2) | 4-GW horizon |
| components | jsonb | REQUIRED: labeled breakdown (minutes_prob, base, attack, per-fixture multipliers). This is the brief writer's raw material. |
| computed_at | timestamptz | |
PK (`player_id`, `gameweek`).

### `draft_squads` — pre-season manual squads (§6.3)
| field | type |
|---|---|
| id | uuid PK |
| player_ids | jsonb | array of 15 element ids |
| captain_id | int NULL |
| email | text NULL |
| created_at | timestamptz |

### `briefs` — generated output + cost cache
| field | type | notes |
|---|---|---|
| id | serial PK | |
| fpl_team_id | int NULL | NULL in pre-season mode |
| draft_id | uuid NULL FK→draft_squads | NULL in season mode |
| gameweek | int | 0 = pre-season brief |
| brief_md | text | |
| input_hash | text | hash of squad + EP inputs |
| model | text | |
| tokens_in / tokens_out | int | |
| created_at | timestamptz | |
Constraints: unique(`fpl_team_id`, `gameweek`) where fpl_team_id not null;
unique(`draft_id`) where draft_id not null.
CHECK: exactly one of fpl_team_id / draft_id is non-null.
Cache rule: same key + same input_hash → serve stored brief; changed input_hash → regenerate once, overwrite.

### `email_signups`
| field | type |
|---|---|
| email | text PK |
| fpl_team_id | int NULL |
| tier | text | 'free' / 'paid' (paid managed by MoR webhooks later) |
| source | text | acquisition tracking (which post/thread) |
| created_at | timestamptz |

---

## 3. Sync job — `sync_fpl()`

Idempotent; safe to run any time. Steps in order:

1. Fetch `bootstrap-static/`. On request failure OR missing expected keys → **abort without touching DB** (stale beats broken), log, alert.
2. **Season-rollover check** (§6.2). If rollover detected → run archive routine FIRST, then continue.
3. Upsert `teams`, `gameweeks`, `players` (cast string numerics here).
4. Append one row per player to `player_history` for today (skip if today's row exists).
5. Fetch `fixtures/`; upsert all rows, preserving NULL gameweeks.
6. Recompute `ep_scores` for all players (next GW + next-4 horizon) in a single transaction, table rewrite.
7. Log one summary line: players updated, EP rows written, duration, mode (PRESEASON/IN_SEASON).

### Schedule (driven by `gameweeks.deadline` of the next unfinished GW)
| window | frequency |
|---|---|
| Pre-season (no current GW) | once daily |
| Baseline in-season | twice daily (morning + evening IST) |
| Deadline−48h → deadline | every 3 hours (injury flags move here; stale flags kill trust) |
| Deadline+8h | one-off **brief pre-generation pass**: for every paid subscriber, fetch picks → run pipeline → store brief → send email. All LLM spend happens in this one predictable batch. |

---

## 4. Cached vs live

| data | strategy | why |
|---|---|---|
| players, teams, fixtures, gameweeks, ep_scores | cached — DB is source of truth, refreshed by sync | global (identical for all users), slow-moving; makes marginal request cost ~0 |
| `entry/{id}/` | live per request | validation; tiny |
| `entry/{id}/event/{gw}/picks/` | live per request | user-specific and must be fresh — analyzing a stale squad after a transfer = instant churn |
| briefs | cached with input-hash invalidation | LLM cost control; unique constraint enforces one brief per team per GW |

---

## 5. Edge cases (non-negotiable handling)

1. **Null `event` on fixtures** = postponed. A team with a postponed fixture in the horizon has a BLANK (fewer fixtures → lower EP). A team with two fixtures in one GW is a DOUBLE (sum both). Blank/double correctness is the single most scrutinized behavior among FPL-literate users.
2. **`chance_of_playing_next_round` NULL** = no flag = 100%, NOT 0 or unknown.
3. **404 semantics** must be distinguished: `entry/{id}/` 404 = bad ID (tell user); picks 404 with valid entry = pre-season or GW not started (route to pre-season mode / next-GW logic).
4. **Deadline race**: a brief is always "advice for the next unlocked gameweek." Between deadline and pre-generation, requests analyze the locked squad for the NEXT GW's planning horizon.
5. **Rate limits**: none published. Stay polite: throttle any batch work to a few req/s; back off on 429/403. v1 load (~2 global calls/sync + 1–2 per user request) is negligible.
6. **Schema drift**: FPL adds fields mid-season. Ingest by explicit field name; ignore unknowns; never "insert whole object."
7. **All displayed prices**: remember ×10 encoding (`now_cost`, `bank`, `value`).

---

## 6. Pre-season mode (launch-critical — this is the mode the product ships in)

### 6.1 Mode derivation
Derive `app_mode` from the `gameweeks` table on every request/sync:
- `IN_SEASON`: some gameweek has `is_current = true` (or is_next exists and GW1 finished-or-started)
- `PRESEASON`: events exist for the new season, none current, GW1 not started
- Store nothing; compute it. It must never be a manually flipped flag.

### 6.2 Season rollover detection + archive routine
Detection, inside `sync_fpl()` step 2: local DB shows a finished GW38 (or any finished GWs), but fetched bootstrap shows ALL events unfinished AND total_points reset to 0 across players → the API has rolled to a new season.
Archive routine (runs once):
1. Copy `players` → `players_archive` with the outgoing `season` tag.
2. Copy final `ep_scores` → archive (optional but cheap).
3. Truncate `ep_scores`; let sync repopulate `players`/`gameweeks`/`fixtures` from new-season bootstrap.
4. Record rollover in a `meta` key-value row (`season_current = '2026-27'`).
⚠ Without this routine, the new-season sync silently destroys the only data pre-season analysis can use.

### 6.3 Pre-season input path
`entry/{id}/event/1/picks/` 404s for everyone until GW1. Two supported inputs:
- **Manual draft entry**: frontend 15-player picker (or paste-a-list parser) → stored in `draft_squads` → brief keyed on `draft_id`, `gameweek = 0`.
- **Team ID (late pre-season)**: once FPL opens squad creation, `entry/{id}/` resolves even before GW1; picks still 404 → still route to draft flow, optionally prefill nothing.

### 6.4 Pre-season heuristic data
EP in pre-season runs on `players_archive` (last season's per-90 rates, minutes) joined to NEW season prices/teams from current `players`, with:
- **Transferred players**: match on element identity where FPL preserves it; where it doesn't, match on name+position heuristically. If the match is ambiguous or low-confidence (common name, multiple candidates, position mismatch): **auto-fallback to the (position, price band) baseline — no manual review queue in v1.** Record the outcome in `ep_scores.components` (`data_basis` + `match_method` fields, defined in Layer 2) and log it, so odd briefs are traceable.
- **Promoted teams / new signings**: no PL history → fallback baseline EP by (position, price band). Document the fallback in `components` so the writer can say "no top-flight data yet" instead of hallucinating form.
- **Fixture data**: new-season fixtures ARE available in pre-season → opening-run FDR analysis is fully functional, and is the core of the pre-season brief.

### 6.5 Pre-season brief semantics
`gameweek = 0` briefs analyze: opening 4–6 fixture run per squad player, structural squad risks (flagged players, promoted-team density), captaincy for GW1, and one suggested draft change. Same pipeline, different horizon anchor.

---

## 7. Build order for this layer

1. FPL client (endpoints 1.1–1.4) + defensive parsers with string→numeric casts.
2. Schema migration (all tables in §2).
3. `sync_fpl()` steps 1–5, run manually against live API; verify counts.
4. Rollover detection + archive routine (test by faking local finished GWs).
5. Scheduler wiring (APScheduler) with the §3 frequency table.
6. Stub `ep_scores` writer (heuristic layer plugs in next — write placeholder EP = form × fixture multiplier so downstream layers can be built against real rows).

Definition of done: `sync_fpl()` runs clean on a schedule; DB reflects live FPL state; mode derivation returns PRESEASON today; a fake draft squad row can be joined against archive data end-to-end.
