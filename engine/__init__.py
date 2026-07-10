"""Layer 2 — deterministic heuristic scoring engine.

Pure Python. Same inputs -> same outputs. No LLM, no network, no randomness.
Zero imports from writer/, graph/, or api/ (the deterministic/LLM boundary).

Public surface (spec §8):
  - score_all(...)   -> list[EPResult], called by sync_fpl() step 6 for all players.
  - score_player(...) -> EPResult,      per-player scoring.
The numeric EP is secondary; the labeled `components` blob (§3) is the primary
product — the only raw material the Layer 4 writer receives.
"""
from .config import DEFAULT_CONFIG, HeuristicConfig
from .scoring import EPResult, EngineContext, build_context, score_all, score_player

__all__ = [
    "DEFAULT_CONFIG",
    "HeuristicConfig",
    "EPResult",
    "EngineContext",
    "build_context",
    "score_all",
    "score_player",
]
