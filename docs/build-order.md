# build-order.md — FPL Analyst

One-page map of the system and the order to build it. Full detail lives in `docs/`.

## What this is

An AI FPL analyst: paste your team ID (or draft, pre-season), get a personalized scout-report brief — verdict, transfer, captaincy free; bench order, 4-GW plan, chips, watchlist paid ($3.99/mo or season pass via Paddle). Paid subscribers get it by email every gameweek automatically. No accounts; identity = email + team ID.

## The five layers and their docs

| layer | doc | one line |
|---|---|---|
| 1 | `docs/layer1-data-pipeline.md` | FPL API endpoints, full DB schema, sync job + schedule, cache strategy, pre-season mode, season rollover |
| 2 | `docs/layer2-heuristic-engine.md` | Deterministic EP formula + weights, the `components`/flags contract, blanks & doubles math, pre-season fallbacks, config surface |
| 3 | `docs/layer3-langgraph-pipeline.md` | State object, four deterministic nodes + handoff contracts, stub Writer, error policy, cache integration |
| 4 | `docs/layer4-brief-writer.md` | System prompt + voice spec, payload serialization, curated `team_notes` / `european_weeks` tables, post-generation validator, free generic preview |
| 5 | `docs/layer5-delivery-surface.md` | FastAPI route map, two-page Next.js frontend, Resend email pipeline, Paddle webhooks, Railway/Vercel deployment, Stage A/B launch cut |

## Dependency chain

data (1) → scoring (2) → orchestration (3) → writing (4) → delivery (5)

Each layer consumes the previous layer's contract, not its implementation. The two contracts everything hangs on: the `components` blob (Layer 2 §3) and the node handoff shapes (Layer 3 §2).

## Build sequence (do it in this order)

1. **Layer 1** — client, schema, `sync_fpl()`, rollover routine, scheduler. Stub the EP writer (placeholder formula) so downstream work has real rows. *Done when:* sync runs clean on schedule, mode derivation returns PRESEASON.
2. **Layer 2** — the real engine behind the same `ep_scores` contract. *Done when:* all ~700 players scored < 5s, every components blob passes schema test, top-20 eyeball check sane.
3. **Layer 3 with stub Writer** — full graph, template-rendered briefs, zero LLM spend. *Done when:* golden-path test green; a real draft produces a structurally complete (robotic) brief in < 3s.
4. **Stage A of Layer 5** — landing + draft picker + free gw-0 briefs + email capture, live on real domains. Ship it and start posting in draft threads. Start Paddle account verification NOW (it's the only critical-path item with external latency).
5. **Layer 4 prompt iteration** — the human-time sink: frozen payloads, rubric, iterate until the blind-read test passes. Swap stub for LLM Writer behind the validator.
6. **Stage B of Layer 5** — checkout + webhooks (sandbox first), weekly send batch, flip `PAYWALL_ENABLED` before the GW1 deadline. That deadline is external and immovable — the whole schedule backs out from it.

## Standing weekly/monthly human inputs (the moat — never automate away)

- `team_notes`: ~30 min/week of film-informed notes per relevant club (Layer 4 §1.1)
- `european_weeks`: ~10 min/month from published UCL/UEL schedules (Layer 4 §1.2)
- Heuristic calibration glance: EP vs actuals, config tweak + version bump (Layer 2 §7)

## When reality contradicts a spec

The specs are the source of truth until they're wrong. If the FPL API, Paddle, or anything else contradicts a doc mid-build: fix the doc first (or at minimum leave a dated correction note in it), then the code — don't let the specs drift.
