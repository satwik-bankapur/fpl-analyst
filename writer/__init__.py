"""Layer 4 — Brief Writer. The ONLY layer that calls an LLM in v1.

Pipeline: serialize (BriefState -> labeled text) -> LLM (single call) -> validate
(§7, deterministic) -> persist. On any failure the deterministic stub renderer
(Layer 3 §2.5) is the fallback, so the product never hard-fails.

Import direction: writer/ MAY import graph/ (for the stub fallback); graph/ and
engine/ MUST NEVER import writer/'s LLM side (CLAUDE.md deterministic/LLM boundary).
"""
from __future__ import annotations

from .serializer import serialize_payload
from .validator import validate_brief

__all__ = ["serialize_payload", "validate_brief"]
