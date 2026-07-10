"""Runtime dependencies injected into nodes (kept out of BriefState).

BriefState carries data; Deps carries handles (DB session factory, FPL client).
Nodes are wrapped as `lambda s: node(s, deps)` when the graph is compiled, so the
graph topology stays pure and the I/O handles are supplied at build time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Deps:
    session_factory: Any               # sessionmaker / callable -> Session context manager
    client: Any = None                 # FPLClient for IN_SEASON live squad fetch (None in PRESEASON)
