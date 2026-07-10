"""Graph wiring + error policy (§3).

Linear chain with a fatal-error short-circuit: after each deterministic node, if
`state.error` is set we route straight to the error responder and END — never a
partial brief. Every completed run logs the full serialized state at DEBUG (the
"why did it tell me to sell Haaland" audit trail).
"""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from .deps import Deps
from .nodes import (
    error_responder, fixture_planner, market_scout, squad_analyst, strategist, writer,
)
from .state import BriefState

log = logging.getLogger("graph.pipeline")

# (node_name, callable, next_node_on_success)
_CHAIN = [
    ("squad_analyst", squad_analyst, "market_scout"),
    ("market_scout", market_scout, "fixture_planner"),
    ("fixture_planner", fixture_planner, "strategist"),
    ("strategist", strategist, "writer"),
]


def _router(next_name: str):
    def route(state: BriefState) -> str:
        return "error_responder" if state.error is not None else next_name
    return route


def build_graph(deps: Deps):
    """Compile the pipeline with I/O handles bound into each node."""
    def bind(fn):
        return lambda s: fn(s, deps)

    b = StateGraph(BriefState)
    for name, fn, _nxt in _CHAIN:
        b.add_node(name, bind(fn))
    b.add_node("writer", bind(writer))
    b.add_node("error_responder", bind(error_responder))

    b.add_edge(START, "squad_analyst")
    for name, _fn, nxt in _CHAIN:
        b.add_conditional_edges(name, _router(nxt),
                                {"error_responder": "error_responder", nxt: nxt})
    b.add_edge("writer", END)
    b.add_edge("error_responder", END)
    return b.compile()


def run_brief(state: BriefState, deps: Deps) -> BriefState:
    """Run the full pipeline once and return the completed BriefState."""
    result = build_graph(deps).invoke(state)
    final = result if isinstance(result, BriefState) else BriefState.model_validate(result)
    log.debug("brief pipeline complete (error=%s): %s",
              final.error.code if final.error else None, final.model_dump_json())
    return final
