"""Trace mode — the pipeline's reasoning, recorded for the operator (diagnostic only).

OFF BY DEFAULT AND OFF IN EVERY NORMAL RUN. `RequestCtx.trace` gates every write
in this file's callers; with it false no node builds a `TraceStage`, `BriefState.trace`
stays `[]`, and the brief is byte-identical to a run from before this module existed
(`tests/test_trace.py::test_trace_off_leaves_the_run_untouched` pins that).

## Why a `data` dict and not typed models per node

The four stages record genuinely different things — a rejection tally, a swing
table, a decision and its losers. Typing each one would add four contracts that
only the inspector page consumes, and a contract is a promise to keep. The trace
is a DIAGNOSTIC, not a handoff: no node reads another node's trace, nothing
downstream branches on it, and the writer never sees it. So `data` is a plain
JSON-able dict whose shape is documented per node below and owned by the one page
that renders it.

If something in here ever starts driving a decision, it has stopped being a trace
and needs to move into the real §2 contracts in `state.py`.

## `data` shapes by node

- `squad_analyst`  {squad_ep_gw, squad_ep_next4, xi[], bench[], problems[], warnings[]}
- `market_scout`   {problems[{…, considered, candidates[], rejected[{reason, count, examples[]}]}],
                    differentials[], template_missing[]}
- `fixture_planner`{teams[{team_id, short, swing, next6[]}], entering[], leaving[], blanks[], doubles[]}
- `strategist`     {transfer{}, transfer_alternatives[], captain{}, ranked_starters[],
                    bench_order[], chip{}, watchlist[], codes[{code, plain}]}
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# How many rejected candidates to keep per reason, per problem. The full COUNT is
# always recorded; only the examples are capped. Without a cap a trace over ~560
# players x N problems would be tens of thousands of rows the page cannot render
# and nobody would read — the highest-EP few are the ones worth explaining.
REJECTION_EXAMPLES = 8


class TraceStage(BaseModel):
    """One node's contribution. Nodes append exactly one of these when tracing."""

    node: str
    summary: list[str] = Field(default_factory=list)   # plain-English, reader-first
    data: dict = Field(default_factory=dict)           # the numbers behind the words


# --- reasoning codes -> plain English --------------------------------------
#
# The Strategist emits codes as compact "key:value" tokens (§2.4). The stub writer
# has its own phrasebook (`graph/nodes/writer.py::_CODE_NOTES`) covering the four
# ENGINE FLAGS a reader needs; this one covers the full STRATEGIST vocabulary,
# which readers never see and operators always want.
#
# Deliberately not merged with the writer's: that one is a product-copy decision
# ("what should a customer be told"), this one is a debugging aid ("what did the
# rule actually do"). They answer different questions and drift apart on purpose.

_PREFIX_NOTES = {
    "delta": lambda v: f"the incoming player projects {v} more points over the next four gameweeks",
    "problem": lambda v: f"the problem being solved is: {v.replace('_', ' ')}",
    "severity": lambda v: f"that problem is severity {v} of 3",
    "flag": lambda v: f"the incoming player carries the engine flag `{v}`",
    "captain_ep": lambda v: f"the chosen captain projects {v} points in the anchor gameweek",
    "swing_team": lambda v: f"team {v} is entering a good run, so its best player joins the watchlist",
    "double_gw_players": lambda v: f"{v} squad players have two fixtures in the horizon",
    "transfer": lambda v: f"transfer action: {v}",
}

_EXACT_NOTES = {
    "no_qualifying_delta":
        "no replacement beat the minimum gain threshold, so the transfer is rolled",
    "transfer:roll": "no transfer is being made this week",
}


def explain_code(code: str) -> str:
    """One reasoning code -> a plain-English clause. Never returns empty.

    An unknown code is echoed rather than dropped: on an operator page an
    unexplained token is a lead, whereas in reader-facing copy it is noise. That
    is the opposite of the writer's rule, and intentionally so.
    """
    if code in _EXACT_NOTES:
        return _EXACT_NOTES[code]
    key, sep, value = code.partition(":")
    if sep and key in _PREFIX_NOTES:
        return _PREFIX_NOTES[key](value)
    if key in _PREFIX_NOTES and not sep:
        return f"{key.replace('_', ' ')}"
    return f"unrecognised code `{code}` — no explanation registered"


def explain_codes(codes: list[str]) -> list[dict]:
    """`[{code, plain}]`, order preserved, duplicates kept (a repeat is a fact)."""
    return [{"code": c, "plain": explain_code(c)} for c in codes]


# --- rejection reasons -----------------------------------------------------
#
# MarketScout's filters, named. The strings are the contract between the scout's
# trace and the inspector page's grouping, so they live here rather than as
# literals at the filter site.

REJECTION_REASONS = {
    "already_in_squad": "already in this squad — not an available addition",
    "wrong_position": "plays a different position to the player being replaced",
    "unavailable_status": "flagged unavailable by the FPL API (injured/suspended/loaned)",
    "over_budget": "costs more than the outgoing player's price plus the bank",
    "club_cap": "the squad already holds the maximum allowed from that club",
    "below_top_n": "survived every filter but ranked outside the shortlist on ep_next4",
    "insufficient_ep_delta": "projects no better than the player being replaced",
}
