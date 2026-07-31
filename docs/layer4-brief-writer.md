# FPL Analyst — Brief Writer Spec (Layer 4)

Consumes: completed `BriefState` (Layer 3) — specifically `decisions`, `diagnosis`, `market`, `fixture_outlook`, `squad`, `warnings` — plus two curated tables defined here.
Produces: `brief_md` (personalized brief) and, separately, the weekly generic preview (free tier).
This is the ONLY layer that calls an LLM in v1.

## 0. Design principles

1. **The Writer narrates decisions already made.** It never decides, ranks, or computes. All judgment happened upstream; the Writer's job is voice, emphasis, and honesty.
2. **Closed-world rule.** The Writer may only assert facts present in its payload: the serialized evidence, `players.news` text, curated notes, and flags. If it isn't in the payload, it doesn't exist. This is enforced by validation (§7), not just prompt instruction.
3. **One brief = one LLM call.** No chains, no self-critique loops, no multi-pass polish in v1.
4. **Honesty is voice.** Warnings and fallback flags are surfaced in-prose as candor, not hidden. The brand is "someone who watches the games and tells you straight."

---

## 1. Curated inputs (the moat tables)

### 1.1 `team_notes`
| field | type |
|---|---|
| team_id | int FK→teams |
| note | text (2–3 lines max) |
| updated_at | timestamptz |
Maintained by hand, ~30 min/week (the weekly film-work commitment). Content: tactical identity, set-piece threat, rotation habits, penalty takers, role notes. Example: `"Newcastle: Gordon on pens. Wingers rotate in European weeks; front line doesn't. High line away from home — vulnerable to pace."`
`notes_version` = max(updated_at) across the table → part of the brief cache key (Layer 3 §4).

### 1.2 `european_weeks`
| field | type |
|---|---|
| team_id | int FK→teams |
| gameweek | int |
| competition | text ('UCL','UEL','UECL') |
| tie_importance | smallint 1–3 |
Maintained by hand from published schedules (~10 min/month). Feeds:
- Layer 2 dampener: `minutes_prob × EURO_ROTATION_DAMP[importance]` (config: {1: 0.90, 2: 0.90, 3: 0.85}), applied ONLY where minutes share < 0.85; flag `european_fixture_week:{comp}:{importance}` emitted for the team's players regardless.
- Layer 4: the flag makes rotation hedging MANDATORY in any section discussing that team's players.
- Cache key: include max(updated_at) of this table alongside notes_version.
(This table is an addendum to Layer 2 §7 config and Layer 1 schema; treat as adopted there.)

## 2. Payload serialization (state → prompt)

Compact, labeled, structured text (NOT raw JSON dump — field-name/value lines, grouped). Include ONLY:
- Header facts: team name, rank, bank, free-transfer assumption, gameweek, mode.
- The starting XI + bench as name/position/price/ep/ep_next4 one-liners.
- FULL evidence blocks (components excerpts + flags + news text) for: every player named in `decisions` (transfer out/in, captain, vice, watchlist), every `problems` entry, and any player with a european/injury flag. NOT all 15 — this is the token-budget lever.
- `decisions` verbatim, including reasoning_codes.
- fixture_outlook rows for squad teams + transfer-target teams only.
- team_notes rows for: user's squad teams involved in decisions + transfer-target teams (typically 3–6 notes, not all 20).
- warnings list.
- Serialization must round all numbers (1 decimal) and use web_names.

Token budget: payload ≤ 3,500 input tokens (enforced: if over, drop bench evidence first, then non-decision fixture rows). Output cap 1,200 tokens. Target model: mid-tier (Sonnet-class) via API; temperature 0.7; the stub template renderer (Layer 3 §2.5) is the fallback on failure.

## 3. Output structure (fixed, machine-parseable)

Markdown with EXACT section headers (frontend and email renderer split on these):
```
## The verdict
## This week's transfer      (or "## Roll the transfer" when action=roll)
## Captaincy
## Bench order & the plan    [PAID ONLY]
## Chips                     [PAID ONLY, only when chip_stance ≠ silent hold]
## Watchlist                 [PAID ONLY]
```
- Free-tier personalized brief = first three sections only (generation produces all; gating truncates at render time so one LLM call serves both views of the same team).
- Verdict: 3–5 sentences, the squad's single biggest issue and overall shape. Transfer: the recommendation as an argument (evidence → conclusion), incl. the "why now" fixture logic. Captaincy: pick + one-line case; mention differential_note if present. Bench/plan: bench order + 2-sentence 4-GW outlook. Watchlist: 3 names, one line each.
- No tables, no bullet lists except Watchlist; this is written analysis, not a dashboard.

## 4. System prompt specification

Core identity block (paraphrase, keep this meaning exactly):
- "You are a football scout writing a private weekly brief for one FPL manager. You watch the games; you are direct, economical, and honest. You write in British English football register."
Hard rules block:
1. Only state facts present in the payload. Never invent statistics, form narratives, injury details, or tactical claims. Curated notes may be used verbatim or lightly rephrased.
2. Every recommendation must carry its reason (expand reasoning_codes into prose; never print the codes).
3. Surface warnings honestly and briefly (e.g. sale-price approximation, assumed free transfer, no-history fallback: "he's new to the league — no top-flight data yet, this is a judgment call").
4. `european_fixture_week` flag present → the relevant section MUST acknowledge rotation risk for that team's non-nailed players.
5. Numbers sparingly: cite at most 4–5 numbers in the whole brief, each in service of a recommendation. No stat-dump sentences.
6. Never hedge everything: commit to the calls made in `decisions`. Hedging is reserved for flagged uncertainty only.
7. Address the manager as "you"; reference their rank/mini-league situation only if present in payload.
8. Length: 350–500 words total.

## 5. Voice examples (include IN the system prompt, one good/bad pair per failure mode)

BAD (stat dump): "Gordon has an xGI of 0.55 per 90, 2 goals and 3 assists in his last 6, and faces BUR (FDR 2), FUL (FDR 2)..."
GOOD: "Gordon's numbers have quietly caught up with his performances, and Newcastle's next month is the kindest run they'll see all season. He's also on penalties now — at 8% owned, he's the move."

BAD (fake insight / invented fact): "Watch for Isak's movement between the lines against a high defensive line" (payload contains no such note).
GOOD (uses curated note): "Newcastle's high line away from home is worth knowing about — but that's their problem, not his."

BAD (hidden uncertainty): "Start Nkunku." (player has european_fixture_week:UCL:3 + minutes share 0.62)
GOOD: "Nkunku is the talent, but Chelsea play a Champions League semi on Tuesday and he's exactly the profile that gets rested. If you have a boring alternative, this is the week for boring."

BAD (hedge-everything): "Haaland could be a good captain option, though Isak may also perform well, depending on how the matches unfold."
GOOD: "Haaland, and it isn't close. Home to the league's worst travellers. Isak is the differential shout, but you're protecting a lead, not chasing — take the ceiling."

## 6. Free-tier generic gameweek preview (separate product, ONE call per GW)

- Generated once in the post-deadline batch, stored (briefs row with fpl_team_id NULL, draft_id NULL — relax Layer 1 CHECK to allow the (NULL, NULL, gw) singleton, unique on gameweek where both NULL), served to all free visitors + free email list.
- Payload: top 8 by ep_next4 (with evidence), top 3 differentials, entering/leaving fixture runs, blanks/doubles ahead, european_weeks for the GW, ALL team_notes (global piece can afford them), most_captained from `gameweeks`.
- Same voice, same rules; structure: `## The week ahead / ## Form and fixtures / ## The differentials / ## Trap of the week` (one popular pick the data is cooler on — this section is the shareable hook).
- 400–600 words. This piece is the marketing surface: written to be screenshotted.

## 7. Post-generation validation (deterministic, before persist/send)

1. Section headers: all required headers present, in order → else one retry, then stub fallback.
2. Name whitelist: every capitalized token matching a `players.web_name` pattern must be a name present in the payload → violation = retry once with violation appended to prompt, then stub fallback. (Catches invented players/transfers.)
3. Number sanity: any "£N.N" or "N.N pts" figure must appear in payload (string match on rounded forms) → same retry policy.
4. Length gate: 250–700 words.
5. Log: model, tokens in/out, validation outcome, retry count → `briefs` row fields (Layer 1).

## 8. Prompt iteration protocol (this is the product-quality work — budget the most human time here)

- Assemble 8–10 real squads (yours + friends' + public template teams) as frozen payloads.
- Rubric per output, scored 1–5: (a) would a real FPL player act on this, (b) does every claim trace to payload, (c) does it sound like a person, (d) is flagged uncertainty surfaced, (e) zero filler sentences.
- Iterate system prompt until median ≥ 4 on all axes across the set. Keep the payload frozen while iterating (one variable at a time).
- Store the prompt in `writer/prompts/` under version control; `prompt_version` written to `briefs.model` field alongside model name.

Definition of done: 10 frozen payloads → 10 briefs passing all §7 validations with ≤ 1 retry total; blind-read test (show a brief to one FPL-playing friend without context) produces "who wrote this?" not "is this AI?"; generic preview generates clean from a real gameweek snapshot; cost per personalized brief ≤ $0.02 at chosen model.

## 9. Implementation drift notes (code is correct; doc trails reality)

- 2026-07-10: **`temperature 0.7` (§2) is not sent.** The chosen mid-tier model (`claude-sonnet-5`, from env `FPL_WRITER_MODEL`) rejects sampling parameters (`temperature`/`top_p`/`top_k`) with a 400 — this changed across the model line after the spec was written. The `0.7` value is preserved as `writer/config.TEMPERATURE` behind a `SEND_TEMPERATURE=False` gate for any model that accepts it; voice variety is otherwise steered by the system prompt. Flip `SEND_TEMPERATURE` (and point `FPL_WRITER_MODEL` at a sampling-capable model) to restore the spec-literal behaviour.
- 2026-07-10: the §1.2 European dampener is implemented in **Layer 2** (config → v1.4; `EURO_ROTATION_DAMP`, `EURO_MIN_SHARE`; `european_weeks` fed through `build_context`/`score_all`); see the Layer 2 §9 note. The `european_fixture_week:{comp}:{importance}` flag is now in the canonical Layer 2 §3 flags vocabulary.
- 2026-07-10: the §7 name-whitelist check compares against the global `players.web_name` set — a known player named in the output but absent from the payload fails. A wholly fictional name (matching no real web_name) is not caught by this check; the §4 closed-world prompt rule plus the number/header/length gates are the backstop. Revisit if hallucinated non-players appear in iteration.
- 2026-07-10: the generic preview (§6) and the personalized brief ship as **deterministic template renderers** at this pause point (`writer/generic.render_generic_stub` and the Layer 3 stub Writer). The single LLM call is wired (`writer/llm.generate_brief`) and smoke-tested; prompt iteration to v≥0.2 is the §8 human-in-the-loop work.
- 2026-07-30 (clarification of the note above, for honesty): "wired and smoke-tested" means unit-tested in isolation — **`writer/llm.generate_brief` is not reachable from any route.** `api/service.py` imports exactly one symbol from `writer/` (`render_generic_stub`); briefs are served by `graph.cache.get_or_generate`, which calls the Layer 3 stub. Every brief a real user has ever received is template text. Wiring the LLM into the serving path is a discrete unstarted task, not a side effect of prompt iteration.
- 2026-07-30: **§3's `## Chips` section is now producible by both writers, and `fixtures` is gone.** The §3 header list is unchanged and is now the canonical vocabulary (`graph/sections.py`); `writer/config.REQUIRED_HEADERS` / `TRANSFER_HEADERS` derive from it instead of hard-coding strings. Two corrections to what shipped: (1) the Layer 3 stub emitted `## Fixture notes`, a seventh section §3 never listed and the LLM writer could never produce — its content now renders inside `## Bench order & the plan`, which is where §3 has always put the four-gameweek outlook, so nothing is lost; (2) the stub never emitted `## Chips` at all despite `chip_stance` existing since Layer 3 — it now does, gated on `chip_stance != "hold"` exactly as §3 specifies. Rationale for folding rather than adding a seventh key: a section only one writer can produce cannot be gated coherently, and `locked_sections` naming it would advertise content that does not exist.
- 2026-07-31: **`serialize_payload` (§2) returns one additional field, `PayloadResult.blocks`.** Additive with a default, and nothing in the LLM path reads it — `dropped` is still the field the writer branches on. It exists for the `/admin/inspect` payload preview: `dropped: ["bench_evidence"]` tells an operator that a group went, not *who* went with it, so `blocks` names each evidence and fixture block with its group and whether the budget rule kept it. The drop order and the budget arithmetic are untouched. The same page renders the serialized text verbatim with a costed token estimate **without calling the model**; `tests/test_trace.py::test_inspect_payload_is_generated_but_never_sent` breaks `AnthropicWriter.generate` to keep it that way, so the §8 prompt-iteration work can be done against real payloads at zero spend.
