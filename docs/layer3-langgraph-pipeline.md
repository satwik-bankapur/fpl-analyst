# FPL Analyst — LangGraph Pipeline Spec (Layer 3)

Consumes: Layer 1 tables (live picks fetch, `ep_scores`, `players`, `fixtures`, `gameweeks`, `draft_squads`), Layer 2 components contract.
Produces: a completed `BriefState` whose final field is `brief_md`, persisted to `briefs`.
Invoked by: `POST /brief` (on cache miss) and the post-deadline pre-generation batch job.

## 0. Design principles

1. **Orchestration graph, not agent committee.** LangGraph is used for typed state, deterministic sequencing, retries, and clean future LLM insertion — NOT for agent-to-agent conversation. No loops, no dynamic routing beyond error paths, no LLM calls in any node except Writer (and later, optionally, Strategist).
2. **State carries derived results and IDs, never bulk data.** Nodes read global tables from the DB directly; the state object stays small, JSON-serializable, and loggable in full.
3. **Every node has a handoff contract** — a typed output shape. Downstream nodes and the eventual LLM prompts depend on contracts, not implementations. Contract tests enforce this.
4. **The graph must run end-to-end with zero LLM calls** (Writer stubbed as a template renderer) so the pipeline is testable, demoable, and cheap during development.

---

## 1. State object

One Pydantic model, `BriefState`, threaded through all nodes:

```
BriefState
├── request: RequestCtx
│     mode: "IN_SEASON" | "PRESEASON"
│     fpl_team_id: int | None        (exactly one of these two set)
│     draft_id: uuid | None
│     gameweek_anchor: int           (0 for pre-season briefs)
│     requested_at: timestamptz
├── squad: SquadCtx | None           (set by SquadAnalyst)
├── diagnosis: Diagnosis | None      (set by SquadAnalyst)
├── market: MarketScan | None        (set by MarketScout)
├── fixture_outlook: FixtureOutlook | None   (set by FixturePlanner)
├── decisions: Decisions | None      (set by Strategist)
├── brief_md: str | None             (set by Writer)
├── warnings: list[str]              (non-fatal: fallbacks used, stale data notes)
└── error: PipelineError | None      (fatal: short-circuits to error responder)
```

## 2. Nodes and handoff contracts

Linear wiring: `SquadAnalyst → MarketScout → FixturePlanner → Strategist → Writer`.
(MarketScout and FixturePlanner are logically parallel; keep sequential in v1 — the whole deterministic chain runs in well under a second, parallelism buys nothing yet.)

### 2.1 SquadAnalyst (deterministic)

Does: resolves the squad, joins it against `ep_scores` + `players`, diagnoses problems.

Input: `request`.
Steps:
1. IN_SEASON: live-fetch `entry/{id}/` (validate; 404 → fatal error `invalid_team_id`) and `entry/{id}/event/{gw}/picks/` (404 with valid entry → fatal `no_picks_yet`, message routes user to draft flow). PRESEASON: load `draft_squads` row.
2. Join all 15 players to current `ep_scores` (anchor GW) and `players` rows.
3. Compute and emit:

`SquadCtx`: team_name, overall_rank (None in pre-season), bank, squad_value, active_chip, picks[15] {player_id, slot 1–15, is_captain, is_vice, multiplier}.

`Diagnosis`:
- `squad_ep_gw`, `squad_ep_next4` — starting XI only (slots 1–11), captain doubled
- `problems`: typed list, each `{type, player_id, severity 1–3, evidence}` where
  - `type ∈ {unavailable, injury_doubt, fixture_cliff, cold_form, dead_bench_spot, blank_exposure}`
  - `evidence` = the relevant components fields / flags copied in (writer-ready strings)
  - detection rules: unavailable = status flag; injury_doubt = flag with chance ≤ 75; fixture_cliff = player's team swing score (§2.3) below −0.8; cold_form = form < 0.6 × ppg with minutes_prob > 0.8; dead_bench_spot = bench player ep_next4 < 4; blank_exposure = ≥ 4 starters with blank_gw flag in next 4
- `free_transfers`: NOT reliably available from the API. v1 rule: assume 1, emit warning `"free_transfers_assumed"`. (v1.1: infer from transfer history endpoint.)
- `captain_current`: player_id

### 2.2 MarketScout (deterministic)

Does: scans the global `ep_scores` table for actionable alternatives.

Input: `diagnosis` (+ squad for ownership/club constraints).
Output `MarketScan`:
- `replacements`: for each problem of type unavailable/injury_doubt/fixture_cliff/cold_form — top 3 same-position candidates affordable within (player sale price + bank), each `{player_id, ep_next4, ep_next4_delta, price_delta, ownership_pct, key_flags}`
- `differentials`: top 5 globally by ep_next4 with ownership < 10%, any position
- `template_missing`: top 5 by ownership > 30% not in the squad
- Hard filters everywhere: exclude status i/s/u/n; exclude candidates that would violate the 3-per-club rule against the CURRENT squad (minus the outgoing player for replacements); exclude the squad's own players from replacement lists.
- Sale-price caveat: true FPL sale price (purchase price + half profit) is not available via public API. v1: use current price as sale price, emit warning `"sale_price_approximated"` when any replacement is within £0.3 of the affordability edge.

### 2.3 FixturePlanner (deterministic)

Does: converts raw fixtures into run/swing intelligence for squad teams + shortlisted teams.

Output `FixtureOutlook`:
- per relevant team: `{team_id, next6: [{gw, opponent_short, is_home, fdr}], swing}` where `swing = avg_fdr(next 3) − avg_fdr(following 3)` sign-normalized so positive = improving run
- `entering_good_run`: teams with swing ≥ +0.8; `leaving_good_run`: ≤ −0.8
- `blanks`: [{gw, team_ids}] and `doubles`: [{gw, team_ids}] within next 6 GWs
- Pre-season: identical computation anchored at GW1 (fixtures exist pre-season; this section is the heart of the gameweek-0 brief).

### 2.4 Strategist (RULES in v1 — designated LLM insertion point)

Does: commits to recommendations. Pure function of the three upstream contracts.

Output `Decisions`:
- `transfer`: `{action: "make" | "roll", out_id?, in_id?, reasoning_codes: [str]}`
  v1 rule: take the highest-severity problem that has a replacement with `ep_next4_delta ≥ 2.0`; if none qualifies → roll. Never recommend a points hit (−4) in v1 rules.
- `captain`: `{pick_id, vice_id, differential_note?}` — max `ep` (anchor GW) among starting XI; vice = second. If second is within 0.5 ep AND ownership < 15%, attach differential_note with both ids.
- `bench_order`: bench slots sorted by `ep` desc (bench GK stays in GK slot per FPL rules).
- `chip_stance`: v1 rule stub — `"hold"` unless a double_gw flag exists in the next-4 horizon for ≥ 2 squad players → `"consider_bench_boost_or_tc"` note. No deeper chip logic in v1.
- `watchlist`: 3 entries drawn from differentials ∪ entering_good_run teams' top ep players.
- `reasoning_codes`: machine-readable evidence pointers (e.g. `"delta:+5.8"`, `"flag:injury_doubt:75"`, `"swing:+1.2"`, `"pen_taker"` if in curated notes) — the Writer expands these into prose; they are the audit trail from decision back to data.

**DRIFT NOTE 2026-07-30 — `differential_note` no longer carries ids.** This section said "attach differential_note with both ids". It did, and the stub Writer printed the note verbatim, so readers got `vice (413) is within 0.3 ep of the captain`. `pick_id` and `vice_id` are already fields on the same object, so the ids in the note were pure duplication with a leak attached. The note is now a **predicate about the vice** with no id and no `ep` — "is within 0.3 projected points of the captain and only 8.5% owned — a real differential" — and whoever renders it supplies the name. Guarded by `tests/test_graph.py::test_stub_captaincy_never_prints_a_raw_player_id`.

**Reasoning codes are an AUDIT TRAIL, not copy.** Same date: a code with no plain-English mapping is now *dropped* by the stub Writer rather than printed. An unexplained `flag:no_history_fallback` in a reader's brief is worse than silence. The mapping lives in one place — `_CODE_NOTES` in `graph/nodes/writer.py`.

**LLM insertion contract**: a future LLM Strategist receives exactly `(diagnosis, market, fixture_outlook)` serialized, and must return a valid `Decisions` object (structured output, schema-validated, one retry on validation failure, fall back to rules implementation on second failure). The swap is an implementation change behind an identical contract — enforced by a shared contract test suite that runs against BOTH implementations.

### 2.5 Writer (LLM — full spec is Layer 4; stubbed in this layer)

v1-during-development stub: a deterministic template renderer that interpolates `Decisions` + evidence into the brief skeleton (Verdict / Transfer / Captaincy / Bench / Watchlist sections). Ugly output, correct structure. This makes the whole graph runnable, testable, and demoable before any prompt work, and remains permanently useful as the LLM-outage fallback.

**DRIFT NOTE 2026-07-30 — "ugly output" was a development-only licence, and it expired.** `writer/llm.py` is wired to nothing, so this stub is what every real user reads. It was printing the upstream contracts raw: `[dead_bench_spot · sev1] Zubimendi: ep_next4:0.48`, `codes: delta:+2.45, problem:unavailable, severity:3, flag:no_history_fallback`. Every problem type and every surfaced reasoning code now maps to one short plain-English clause through `_problem_sentence()` / `_CODE_NOTES` in `graph/nodes/writer.py`. **No internal may appear in the output** — no type slug, no severity number, no code, no engine flag, no column name, no player id; `tests/test_graph.py::test_stub_output_leaks_no_internals` holds the ban list. The prose stays deliberately flat: the goal is not to imitate the LLM voice, only to stop reading like a debug log. The honesty footer is unchanged on purpose.

## 3. Error policy

- Fatal errors (`invalid_team_id`, `no_picks_yet`, `fpl_api_down`, `empty_ep_table`): set `state.error`, short-circuit all remaining nodes, route to an error responder node that produces a user-facing message string. Never emit a partial brief on fatal errors.
- Live FPL calls: timeout 5s, 2 retries with backoff; on exhaustion → `fpl_api_down`.
- Non-fatal degradations (fallback EP bases, assumed free transfers, approximated sale price) → `warnings`, pipeline continues; Writer surfaces material ones honestly.
- Every completed run (success or error) logs the full serialized state at DEBUG — this is the debugging story for "why did it tell me to sell Haaland."

## 4. Entry points & cache integration

- `POST /brief` handler: compute `input_hash = hash(sorted picks + captain + bank + ep config_version + gameweek_anchor + curated_notes_version)`; check `briefs` (Layer 1 cache rule); on miss run graph, persist, return.
- Pre-generation batch (deadline+8h job): iterate paid subscribers → same path; failures logged and retried once at +30min; batch report line (count, LLM tokens, failures) logged.

## 5. Latency and cost budget

- SquadAnalyst → Strategist: < 1s combined (DB reads + pure computation; the two live FPL calls dominate).
- Writer: the only LLM latency/cost in the system. Everything upstream is free to rerun, which is why the cache key includes config/notes versions — deterministic layers can be rerun liberally; only Writer invocations are guarded.

## 6. Delta vs the Sports Intelligence Engine (orientation note for the implementer)

That system: continuous ingestion, streaming synthesis, LLM-heavy interpretation, WebSocket delivery. This system: batch, request/schedule-triggered, LLM-minimal (one call), REST + email delivery. Reuse the LangGraph mechanics and Pydantic-state habits; do NOT reuse the streaming patterns, multi-LLM synthesis nodes, or WebSocket layer. If a design choice here feels "too simple" relative to that project, that is intentional.

## 7. Tests

1. Golden path: frozen fixture set (real bootstrap + fixtures snapshot as JSON assets, one real squad) → full graph with stub Writer → snapshot-test the Decisions object.
2. Contract tests per node output shape (and the dual-implementation Strategist suite).
3. 3-per-club filter: squad with 3 Arsenal players → no Arsenal replacements suggested.
4. No-problems squad → transfer action = roll, no empty-list crashes.
5. Blank-heavy horizon → blank_exposure problem raised, chip_stance unaffected (v1).
6. Fatal-error paths: bad team id / picks 404 / FPL timeout → correct error object, no partial brief, no LLM call attempted.
7. Cache: identical request twice → one graph run; changed captain → new input_hash → rerun.

Definition of done: golden-path test green end-to-end with stub Writer; full state serializes to < ~50KB; a real team ID produces a structurally complete (if robotically written) brief in < 3s locally with zero LLM spend.

## 8. Implementation drift notes (code is correct; doc trails reality)

- 2026-07-10: `cold_form` detection (§2.1) is restricted to `IN_SEASON` mode. Pre-season the raw `form` field is undefined (Layer 2 §5.1 — the engine itself substitutes archive ppg for form in PRESEASON), so applying the `form < 0.6·ppg` rule against a zero-form pre-season snapshot false-positives the entire squad. The other five problem types are mode-agnostic. Surfaced by the launch-mode (pre-season draft) golden brief; guarded in `graph/nodes/squad_analyst._diagnose`.
- 2026-07-10: the graph is realized with real LangGraph (`StateGraph(BriefState)`, added to `pyproject.toml`) — a linear chain with a conditional error short-circuit after each node, matching §0.1 (typed state + deterministic sequencing, no agent conversation). `MarketScout`/`FixturePlanner` are kept sequential, not parallel (§2 note): the whole deterministic chain runs in <1s, so parallelism buys nothing yet.
- 2026-07-10: the `input_hash` (§4) already mixes `curated_notes_version` + `euro_weeks_version`, but those curated tables are Layer-4 work that does not exist yet; both are placeholder constants in `graph/config.py`. When the real sources land and the constants change, every cached brief invalidates exactly once by design (documented at the constants).
- 2026-07-30 (correction to the note above): the `curated_notes_version` / `euro_weeks_version` placeholder constants are **gone** — they were replaced by real DB-backed sources (`graph.data.curated_notes_version` / `euro_weeks_version`, `max(updated_at)` over `team_notes` / `european_weeks`) when those tables landed. `graph/config.py` no longer defines them. The `graph/cache.py` module docstring still claimed the tables "don't exist yet" until today; it was stale prose, not stale code. The one-time mass cache invalidation on first population of either table still stands as designed.
- 2026-07-30: the stub Writer's section headers now come from `graph/sections.py` (shared with the Layer 4 LLM prompt) rather than being literals in the node body, and it emits `## Chips` when `chip_stance != "hold"`. §2.5's role is unchanged — still deterministic, still never an LLM, still the permanent fallback. See the Layer 5 §8 note dated today for why the vocabulary had to be unified.
