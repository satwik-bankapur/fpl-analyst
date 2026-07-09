"""Typed errors for the data layer.

The two 404s must NEVER be conflated (CLAUDE.md, spec §5.3):
  - entry/{id}/ 404          -> InvalidTeamId  (code "invalid_team_id"): user error.
  - picks 404 on valid entry -> NoPicksYet     (code "no_picks_yet"):    pre-season /
    gameweek not started -> route to the draft flow.
"""
from __future__ import annotations


class FPLError(Exception):
    """Base class for all data-layer errors."""

    code = "fpl_error"


class FPLApiError(FPLError):
    """Network failure or unexpected/malformed response from the FPL API."""

    code = "fpl_api_error"


class SyncAborted(FPLError):
    """Sync could not fetch/validate source data. DB was left untouched (stale beats broken)."""

    code = "sync_aborted"


class InvalidTeamId(FPLError):
    """entry/{id}/ returned 404 — the team ID does not exist. This is user error."""

    code = "invalid_team_id"

    def __init__(self, team_id: int):
        self.team_id = team_id
        super().__init__(f"FPL team id {team_id} does not exist")


class NoPicksYet(FPLError):
    """Picks 404 for a VALID entry — pre-season or the gameweek has not started."""

    code = "no_picks_yet"

    def __init__(self, team_id: int, gameweek: int):
        self.team_id = team_id
        self.gameweek = gameweek
        super().__init__(
            f"No picks for team {team_id} gameweek {gameweek} yet "
            "(pre-season or gameweek not started) — route to draft flow"
        )
