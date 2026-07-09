# CLAUDE.md — FPL Analyst

Guardrails for working in this repo. Full specs live in `docs/layer1` … `docs/layer5` — read the relevant one before touching a layer. This file is only the cross-cutting rules that must never be broken, including several things that look like bugs but are deliberate.

## Repo shape

```
fpl/      FPL API client + sync job          (Layer 1)
engine/   deterministic scoring engine        (Layer 2)
graph/    LangGraph pipeline + node contracts (Layer 3)
writer/   LLM prompts, serialization, validator, email templates (Layer 4)
api/      FastAPI routes, webhooks, scheduler wiring (Layer 5)
web/      Next.js frontend                    (Layer 5)
docs/     layer specs (source of truth)
```

## The deterministic/LLM boundary (most important rule in the repo)

- `engine/` and `graph/` MUST NOT import from `writer/`. Ever. All analysis is fully formed before any LLM sees it.
- The ONLY LLM calls in v1 live in the Writer (`writer/`). The Strategist node is rules-based in v1; it has a designated LLM insertion contract (Layer 3 §2.4) — do not add LLM calls anywhere else "to improve quality."
- The whole graph must run end-to-end with the stub template Writer and zero LLM spend. The stub is permanent: it is the LLM-outage/validation-failure fallback, not scaffolding to delete.

## Intentional non-fixes — DO NOT "correct" these

1. **base/attack double-counting (Layer 2 §2.3).** `form` already contains attacking returns; `attack_pts` measures attacking threat again via xGI. The 50/50 W_BASE/W_ATTACK blend IS the correction. Do not zero one side, deduplicate, or "fix" the overlap.
2. **Clean-sheet points are added AFTER the FDR/venue multipliers, not scaled by them (Layer 2 §1, §2.6).** CS probability already encodes the fixture; running it through fdr_mult would punish the fixture twice.
3. **`player_history` is write-only in v1.** No code reads it. It is not dead code — it accrues unrecoverable time-series for future features. Do not remove it or "clean it up."
4. **The pipeline looks "too simple" relative to typical multi-agent designs on purpose** (Layer 3 §6): batch, linear, one LLM call. Do not add streaming, agent conversation, loops, or dynamic routing.

## Closed-world contract (Writer honesty)

- The Writer may only assert facts present in its payload: serialized evidence/components, `players.news`, curated `team_notes`, flags, warnings. Anything not in the payload does not exist.
- This is ENFORCED by the deterministic post-generation validator (Layer 4 §7: section headers, player-name whitelist, number sanity, length gate), with one retry then stub fallback — never by prompt wording alone. Do not weaken, skip, or "temporarily disable" the validator.
- If a factor influenced an EP score, it must appear in `components` with a human-readable basis. The `components` jsonb contract (Layer 2 §3) and canonical `flags` vocabulary are load-bearing: downstream layers depend on the contract, not the formula behind it.

## Configuration

- Every tunable constant (weights, FDR table, venue multipliers, CS tables, dampeners, thresholds) lives in the single versioned config object (Layer 2 §7). No magic numbers inline, anywhere.
- `config_version` is stamped into every components blob; `prompt_version` into every briefs row. Never generate output that can't be attributed to a config + prompt version.
- Brief cache key = squad + captain + bank + ep `config_version` + gameweek anchor + `team_notes` version + `european_weeks` version. If you add an input that changes brief content, it MUST enter this hash.

## Data-layer invariants (Layer 1)

- **Two 404s, never conflated:** `entry/{id}/` 404 = invalid team ID (user error). Picks 404 with a valid entry = pre-season / gameweek not started (route to draft flow). Distinct error codes: `invalid_team_id` vs `no_picks_yet`.
- **Season rollover:** the archive routine (copy `players` → `players_archive`) runs BEFORE any new-season sync touches `players`. Getting this wrong silently destroys the only data pre-season analysis can use.
- FPL string-numerics (`form`, `ppg`, `xg*`, `selected_by_percent`) are cast on ingest, never at query time. Prices/bank/value arrive ×10.
- `chance_of_playing_next_round = NULL` means NO FLAG = treat as 100%, never 0.
- `fixtures.gameweek = NULL` means postponed: in no gameweek's fixture set. Blanks contribute 0 EP; doubles sum both fixtures.
- Sync failure → abort without touching the DB. Stale beats broken.
- Free transfers and true sale prices are NOT available from the public API: assume 1 FT / current price, emit the corresponding warnings. Never silently fudge these.

## Delivery invariants (Layer 5)

- **Single Railway replica, hard requirement.** APScheduler runs in-process; a second replica = duplicate syncs and duplicate emails to paying customers. If replicas are ever needed, extract the scheduler first.
- **No accounts in v1.** Identity = email + FPL team ID. Paid access = signed JWT links minted by the send job / checkout handler. Do not add auth, sessions, or a login page.
- Webhooks: verify `Paddle-Signature` before parsing; idempotency via `webhook_events`. Cancellations keep delivery alive until `current_period_end`.
- Every bulk email carries one-click `List-Unsubscribe`. Send batch is idempotent via `send_log` unique (email, gw, template).
- Paywall flips via the `PAYWALL_ENABLED` env flag (Stage A → Stage B), not code changes.

## Testing expectations

- Contract tests per graph node; the Strategist contract suite must pass against BOTH implementations (rules now, LLM later).
- Frozen JSON assets (real bootstrap/fixtures snapshots) drive engine and golden-path tests — no live API calls in tests.
- Every scored player's components blob must validate against the Layer 2 §3 schema.
