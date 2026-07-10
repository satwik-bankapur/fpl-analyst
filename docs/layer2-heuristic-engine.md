# FPL Analyst — Heuristic Scoring Engine Spec (Layer 2)

Consumes: `players`, `teams`, `fixtures`, `gameweeks`, `players_archive` (pre-season) from Layer 1.
Produces: `ep_scores` rows (`ep`, `ep_next4`, `components` jsonb).
Runs inside `sync_fpl()` step 6, for ALL players, once per sync. Never per-user, never at request time.

## 0. Design principles

1. **Deterministic, pure Python.** Same inputs → same outputs, always. No LLM anywhere in this layer. No network calls. No randomness.
2. **Components-first.** The numeric EP is secondary output; the labeled `components` breakdown is the primary product — it is the ONLY raw material the brief writer (Layer 4) gets. If a factor influenced the score, it must appear in components with a human-readable basis.
3. **All constants centralized and versioned** (§7). The formula structure is fixed in code; every weight/table lives in one config object.
4. **Transparent over accurate.** v1 optimizes for explainability and directional correctness, not projection-model accuracy. Do not add opaque factors.

---

## 1. Top-level formula

For each player, over a horizon of fixtures (next GW, and next-4 GWs):

```
EP(horizon) = minutes_prob × Σ over fixtures f in horizon [ fixture_ep(f) ]

fixture_ep(f) = ( base_pts × W_BASE + attack_pts × W_ATTACK )
                × fdr_mult(f) × venue_mult(f)
                + cs_pts(f)
```

- A player with 0 fixtures in a GW (blank) contributes 0 for that GW — see §4.
- A player with 2 fixtures in a GW (double) sums both fixture_ep values — see §4.
- Floor every fixture_ep at 0. Cap nothing else in v1.
- `ep` = horizon of exactly the next gameweek. `ep_next4` = horizon of the next 4 gameweeks.

## 2. Component definitions

### 2.1 `minutes_prob` (0.0–1.0)
```
status_factor:
  'a' → 1.0
  'd' → chance_of_playing / 100   (NULL chance with 'd' → 0.75)
  'i','s','u','n' → chance_of_playing / 100 if present else 0.0

share_factor = player.minutes / (gws_elapsed × 90)   clamped to [0,1]
  gws_elapsed = count of finished gameweeks (min 1)
  Season start exception: gws_elapsed < 4 → share_factor = max(share_factor, 0.7)
  (early-season minutes data is too thin to punish)

minutes_prob = status_factor × (0.5 + 0.5 × share_factor)
```
Rationale: a fully fit nailed starter ≈ 1.0; a fit rotation player ≈ 0.6–0.8; a flagged doubt scales by the flag.
Record in components: the resulting number AND a text basis, e.g. `"status=a, 84% of available minutes"`.

### 2.2 `base_pts` — realized scoring rate
```
base_pts = W_FORM × form + W_PPG × ppg        (defaults: 0.6 / 0.4)
```
`form` is FPL's 30-day average; `ppg` is season rate. The blend damps hot streaks without ignoring them.
Season start exception: before GW4, use archive ppg (if `data_basis = archive`) blended 50/50 with current form once any current data exists.

### 2.3 `attack_pts` — forward-looking attacking threat
```
xgi_per90 = (player.xgi / player.minutes) × 90      (0 if minutes < 270 → fall back to position-band average, flag it)
goal_value  = {GK: 6, DEF: 6, MID: 5, FWD: 4}
assist_value = 3
attack_pts = xgi_per90 × blended_return_value
  blended_return_value = 0.65 × goal_value[pos] + 0.35 × assist_value
```
This intentionally double-counts with base_pts (form already contains attacking returns); the W_BASE/W_ATTACK 50/50 blend is what corrects for that. Do NOT "fix" the double-count by zeroing one side.

### 2.4 `fdr_mult` — fixture difficulty
```
FDR_TABLE = {1: 1.30, 2: 1.20, 3: 1.00, 4: 0.82, 5: 0.68}
```
Uses the fixture's difficulty FOR THIS PLAYER'S TEAM (`team_h_difficulty` if home, `team_a_difficulty` if away).
v2 note (do not build now): replace with opponent attack/defence strength differentials from `teams` split-strength fields.

### 2.5 `venue_mult`
```
home → 1.08 ; away → 0.94
```

### 2.6 `cs_pts` — clean-sheet expectation (added AFTER the multipliers, not scaled by them)
```
cs_prob by fixture difficulty (this team's FDR for the fixture):
  {1: 0.50, 2: 0.42, 3: 0.30, 4: 0.20, 5: 0.12}
venue adjust: home +0.04, away −0.04, clamp [0.05, 0.60]

cs_value = {GK: 4, DEF: 4, MID: 1, FWD: 0}
cs_pts(f) = cs_prob(f) × cs_value[pos]

GK bonus: add flat SAVE_PTS_BASE = 0.6 per fixture (save-points baseline).
```

## 3. The `components` contract (jsonb, exact shape)

Every `ep_scores` row carries:

```json
{
  "config_version": "v1.0",
  "data_basis": "current" | "archive_2025-26" | "baseline_fallback",
  "match_method": "element_id" | "name_position" | "none",
  "minutes_prob": 0.92,
  "minutes_basis": "status=a, 84% of available minutes",
  "base": 4.3,
  "base_inputs": { "form": 4.5, "ppg": 4.0, "weights": [0.6, 0.4] },
  "attack": 3.1,
  "attack_inputs": { "xgi_per90": 0.55, "minutes_sample": 812, "fallback": false },
  "fixtures": [
    {
      "gw": 12, "opponent_short": "BUR", "is_home": true,
      "fdr": 2, "fdr_mult": 1.20, "venue_mult": 1.08,
      "cs_prob": 0.46, "cs_pts": 1.84,
      "fixture_ep": 5.61
    }
  ],
  "flags": [],
  "ep": 5.61,
  "ep_next4": 19.8
}
```

`flags` is a list of human-readable strings the writer surfaces verbatim-ish. Canonical flag values:
- `"blank_gw:{n}"` — no fixture in GW n
- `"double_gw:{n}"` — two fixtures in GW n
- `"injury_doubt:{chance}%"` — status d with a chance value
- `"unavailable:{status}"` — i/s/u/n
- `"thin_minutes_sample"` — attack fallback used (minutes < 270)
- `"promoted_team_fallback"` — §5 baseline used
- `"no_history_fallback"` — new signing, no archive match
- `"early_season_blend"` — pre-GW4 base blending active

Contract rule: the Layer 4 writer may ONLY assert facts present in this object plus the `players.news` text. Anything not in components does not exist. Design every field name to be readable when interpolated into a prompt.

## 4. Blanks and doubles — exact math

Fixture set for player p in GW n = all fixtures where `gameweek = n` and p's team is home or away.
- **|set| = 0 (blank)**: contribution to that GW = 0. Add flag `blank_gw:n`. For `ep` (single-GW horizon) this means ep = 0 — correct and intended; the writer turns it into "no fixture this week."
- **|set| = 1**: standard.
- **|set| = 2 (double)**: `fixture_ep` computed independently per fixture (each with its own FDR/venue/cs) and SUMMED. minutes_prob applies once, outside the sum (already in the top-level formula). Add flag `double_gw:n`.
- Postponed fixtures (`gameweek IS NULL`) are in no GW's set by construction — they simply don't count until rescheduled. No special handling needed beyond Layer 1's nullable field.
- `ep_next4` horizon = the next 4 gameweek IDs (not "next 4 fixtures"): blanks reduce it, doubles inflate it. That asymmetry is the point — it's what makes fixture-run advice real.

## 5. Pre-season fallback scoring (`app_mode = PRESEASON`)

Same formula, different data resolution, applied per player in this order:

1. **Archive match found** (`match_method = element_id` or `name_position`):
   form → archive ppg (form is meaningless pre-season); ppg → archive ppg; xgi_per90 → archive rates; minutes share → archive share. `data_basis = "archive_{season}"`.
   Team change: no penalty/bonus multiplier in v1 (neutral 1.0) — the new team's FIXTURES already differentiate.
2. **No archive match** (new signing to an established PL team): baseline table (below), `data_basis = "baseline_fallback"`, flag `no_history_fallback`.
3. **Promoted-team player**: baseline table × PROMOTED_DISCOUNT (0.85), flag `promoted_team_fallback`.

### Baseline table computation (runs once at rollover, from `players_archive`)
- Group last season's players by (position, price_band). Price bands: £0.5m steps, using the NEW season price of the player being scored, mapped onto LAST season's bands (band edges are stable enough; clamp to nearest populated band).
- Only include archive players with ≥ 900 minutes (avoid fringe-player noise).
- Baseline per band = median ppg, median xgi_per90, median minutes share of the band.
- Persist as a small `baseline_ep` table or materialized dict keyed (position, band); recompute only at rollover.
- Empty band (e.g. a price point that didn't exist) → borrow nearest adjacent band.

Minutes_prob in pre-season: status_factor from current flags × 0.5+0.5×(archive or baseline share). New signings to big clubs will look modest — acceptable; the writer's honesty ("no top-flight data yet") is worth more than a guessed number.

## 6. What the horizon anchor is

- IN_SEASON: anchor = the next gameweek with `finished = false` and deadline in the future. `ep` = that GW; `ep_next4` = that GW + 3.
- PRESEASON: anchor = GW1. `ep` = GW1; `ep_next4` = GW1–4. Brief semantics for `gameweek = 0` briefs (Layer 1 §6.5) consume these.

## 7. Configuration & tuning boundary

Single config object (`engine/config.py` or a one-row `heuristic_config` table), version-stamped, containing exactly:
`W_BASE=0.5, W_ATTACK=0.5, W_FORM=0.6, W_PPG=0.4, FDR_TABLE, VENUE_HOME=1.08, VENUE_AWAY=0.94, CS_PROB_TABLE, CS_VENUE_ADJ=0.04, W_CS=0.5, SAVE_PTS_BASE=0.4, GOAL_VALUE, ASSIST_VALUE=3, RETURN_BLEND=[0.65,0.35], PROMOTED_DISCOUNT=0.85, MIN_MINUTES_XGI=270, EARLY_SEASON_GW=4, EARLY_SHARE_FLOOR=0.7`

(`SAVE_PTS_BASE` and `W_CS` reflect the v1.1/v1.3 calibration; see §9. `W_CS` weights the `cs_prob×CS_VALUE` term only — not `SAVE_PTS_BASE`.)

Rules:
- Code never contains a magic number from this list inline.
- `config_version` is written into every components blob → any historical EP is reproducible/attributable.
- Tuning process (post-launch, manual, weekly): compare stored `ep` vs actual GW points across all players; adjust FDR_TABLE / weights; bump version. No automated fitting in v1.
- The deterministic boundary is absolute: if a future version wants an ML model, it replaces §2.2/2.3 internals but MUST still emit the same components contract. Downstream layers depend on the contract, not the formula.

## 8. Structure & tests

- Module: `engine/` — pure functions, zero imports from `writer/`, `graph/`, or `api/`.
- Public surface: `score_player(player, fixtures, teams, config, mode, archive=None) -> EPResult` and `score_all(...)` used by sync step 6.
- Required unit tests (against fixture data from last season, frozen as JSON test assets):
  1. Nailed premium attacker, easy home fixture → ep in a sane band (5–9).
  2. Blank GW → ep = 0 with correct flag.
  3. Double GW → ep ≈ sum of two singles, one flag.
  4. Flagged doubt (d, 75%) → minutes_prob ≈ 0.75 × share term.
  5. NULL chance_of_playing with status 'a' → treated as unflagged.
  6. Pre-season promoted player → baseline path, discount applied, both flags present.
  7. Components blob validates against the §3 shape (schema test) for every scored player.

Definition of done: `score_all()` fills `ep_scores` for all ~700 players in < 5s, every row passes the components schema test, and eyeballing the top-20 by `ep_next4` against your own FPL knowledge produces no absurdities.

## 9. Implementation drift notes (code is correct; doc trails reality)

- 2026-07-09: `score_player(player, ctx)` takes an `EngineContext` built once by `build_context(...)` instead of the §8 six-argument signature `score_player(player, fixtures, teams, config, mode, archive)`. Same contract and output; the context bundles fixtures/teams/config/mode/horizon so they are computed once per sync rather than re-derived for each of ~800 players.
- 2026-07-09: the §5 baseline table is recomputed on demand at the start of PRESEASON scoring (inside `build_context`) rather than persisted to a `baseline_ep` table at rollover. It is deterministic and cheap (~700 archive rows), so the materialized-dict form is used without the persistence/rollover-hook coupling the spec text implies.
- 2026-07-10 (fix, config v1.2): §5 archive resolution never stated that the `MIN_MINUTES_XGI` (270) floor applies to the archive path; only the in-season path guarded it. A tiny archive-minutes sample explodes `xgi_per90` (e.g. a 2-minute player ranked top-3 pre-season). The in-season guard is now applied symmetrically: below the floor, archive `xgi_per90` falls back to the (position, price-band) baseline and emits `thin_minutes_sample`. Well-sampled archive players are unchanged.
- 2026-07-10 (fix, config v1.3): added `W_CS=0.5`, weighting the expected clean-sheet term (`cs_prob×CS_VALUE`) only — `SAVE_PTS_BASE` is left unscaled. This completes the **same double-count correction the base/attack blend performs** (§2.3): a DEF/GK's realized clean-sheet points are already inside `ppg`, so they enter the blended core at weight 0.5; `cs_pts` then re-adds *expected* CS. Before this fix defensive production was counted ~1.5× (0.5 via base + 1.0 via cs_pts) while attacking production is counted 1.0× (0.5 base + 0.5 attack). `W_CS=0.5` makes CS symmetric with attack — 0.5 (base) + 0.5 (cs) = 1.0× — so it is the design-consistent correction, not a new tuning opinion. Diagnosed via top-20 decomposition: mid-core defenders were out-ranking premium attackers whose blended cores were equal or higher, purely on the unscaled cs term (e.g. Saka's core exceeded Senesi's, yet Senesi ranked 21 places higher). This is NOT the §2.6 "CS added after multipliers" positioning rule (untouched); it corrects the CS *magnitude*, which no intentional-non-fix protects.
