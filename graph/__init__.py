"""Layer 3 — LangGraph brief-generation pipeline.

Linear deterministic chain: SquadAnalyst -> MarketScout -> FixturePlanner ->
Strategist -> Writer, with a fatal-error short-circuit to an error responder.

The ONLY node permitted an LLM call is the Writer (Layer 4); in this layer the
Writer is a deterministic template stub. `graph/` must never import `writer/`'s
LLM side (CLAUDE.md deterministic/LLM boundary) — the stub template is fine.
"""
from __future__ import annotations

from .state import BriefState
from .graph import build_graph, run_brief

__all__ = ["BriefState", "build_graph", "run_brief"]
