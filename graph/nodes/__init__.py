"""The five pipeline nodes + error responder (spec §2–§3)."""
from __future__ import annotations

from .squad_analyst import squad_analyst
from .market_scout import market_scout
from .fixture_planner import fixture_planner
from .strategist import strategist
from .writer import writer
from .error_responder import error_responder

__all__ = [
    "squad_analyst", "market_scout", "fixture_planner",
    "strategist", "writer", "error_responder",
]
