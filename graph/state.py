"""BriefState and the per-node handoff contracts (spec §1–§2).

Contracts before implementations: every node's output is a typed Pydantic model
here. Downstream nodes and the eventual LLM prompts depend on these shapes, not
on node internals — the contract tests in tests/test_graph_contracts.py pin them.

State-size discipline (§0.2): carry derived results and IDs, never bulk player
rows. Nodes read the global tables from the DB directly; BriefState stays small,
JSON-serializable, and fully loggable.
"""
from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .trace import TraceStage

Mode = Literal["IN_SEASON", "PRESEASON"]
ProblemType = Literal[
    "unavailable", "injury_doubt", "fixture_cliff",
    "cold_form", "dead_bench_spot", "blank_exposure",
]
ErrorCode = Literal["invalid_team_id", "no_picks_yet", "fpl_api_down", "empty_ep_table"]


# --- request ---------------------------------------------------------------

class RequestCtx(BaseModel):
    mode: Mode
    fpl_team_id: Optional[int] = None      # exactly one of these two is set
    draft_id: Optional[UUID] = None
    gameweek_anchor: int = 0               # 0 for pre-season briefs (§1)
    requested_at: datetime
    # Diagnostic only (the /admin/inspect console). False everywhere else, and
    # nothing downstream of a node may branch on it: a traced run must produce the
    # same brief as an untraced one, or the trace is describing a different
    # pipeline than the one that serves users. See graph/trace.py.
    trace: bool = False


# --- SquadAnalyst outputs (§2.1) -------------------------------------------

class Pick(BaseModel):
    player_id: int
    slot: int                              # 1–15 (1–11 start, 12–15 bench)
    is_captain: bool = False
    is_vice: bool = False
    multiplier: int = 1


class SquadCtx(BaseModel):
    team_name: Optional[str] = None
    overall_rank: Optional[int] = None     # None in pre-season
    bank: float
    squad_value: float
    active_chip: Optional[str] = None
    picks: list[Pick]


class Problem(BaseModel):
    type: ProblemType
    player_id: Optional[int] = None        # None for squad-level (blank_exposure)
    severity: int                          # 1–3
    evidence: list[str]                    # writer-ready strings copied from components


class Diagnosis(BaseModel):
    squad_ep_gw: float                     # starting XI only, captain doubled
    squad_ep_next4: float
    problems: list[Problem]
    free_transfers: int                    # v1: assumed 1 (+ warning)
    captain_current: Optional[int] = None


# --- MarketScout outputs (§2.2) --------------------------------------------

class Replacement(BaseModel):
    player_id: int
    ep_next4: float
    ep_next4_delta: float                  # vs the outgoing player
    price_delta: float
    ownership_pct: float
    key_flags: list[str]


class ReplacementSet(BaseModel):
    """Top-N candidates for one diagnosed problem."""
    problem_player_id: int
    problem_type: ProblemType
    candidates: list[Replacement]


class Candidate(BaseModel):
    player_id: int
    ep_next4: float
    ownership_pct: float
    key_flags: list[str] = Field(default_factory=list)


class MarketScan(BaseModel):
    replacements: list[ReplacementSet]
    differentials: list[Candidate]
    template_missing: list[Candidate]


# --- FixturePlanner outputs (§2.3) -----------------------------------------

class FixtureSlot(BaseModel):
    gw: int
    opponent_short: str
    is_home: bool
    fdr: int


class TeamRun(BaseModel):
    team_id: int
    next6: list[FixtureSlot]
    swing: float                           # +ve => improving run


class GwTeams(BaseModel):
    gw: int
    team_ids: list[int]


class FixtureOutlook(BaseModel):
    teams: list[TeamRun]
    entering_good_run: list[int]           # team_ids, swing >= +0.8
    leaving_good_run: list[int]            # team_ids, swing <= -0.8
    blanks: list[GwTeams]
    doubles: list[GwTeams]


# --- Strategist outputs (§2.4) ---------------------------------------------

class TransferDecision(BaseModel):
    action: Literal["make", "roll"]
    out_id: Optional[int] = None
    in_id: Optional[int] = None
    reasoning_codes: list[str] = Field(default_factory=list)


class CaptainDecision(BaseModel):
    pick_id: int
    vice_id: int
    differential_note: Optional[str] = None


class Decisions(BaseModel):
    transfer: TransferDecision
    captain: CaptainDecision
    bench_order: list[int]                 # bench player_ids, ep desc (bench GK stays GK)
    chip_stance: str
    watchlist: list[int]                   # player_ids
    reasoning_codes: list[str] = Field(default_factory=list)


# --- error -----------------------------------------------------------------

class PipelineError(BaseModel):
    code: ErrorCode
    message: str                           # user-facing


# --- the threaded state ----------------------------------------------------

class BriefState(BaseModel):
    request: RequestCtx
    squad: Optional[SquadCtx] = None
    diagnosis: Optional[Diagnosis] = None
    market: Optional[MarketScan] = None
    fixture_outlook: Optional[FixtureOutlook] = None
    decisions: Optional[Decisions] = None
    brief_md: Optional[str] = None
    # `operator.add` reducer: nodes append warnings; LangGraph concatenates them.
    warnings: Annotated[list[str], operator.add] = Field(default_factory=list)
    error: Optional[PipelineError] = None
    # Same append-reducer as `warnings`, and empty unless `request.trace` is set.
    # It is NOT part of the §2 handoff contract — no node reads it, the writer
    # never sees it, and it is excluded from the brief. See graph/trace.py.
    trace: Annotated[list[TraceStage], operator.add] = Field(default_factory=list)
