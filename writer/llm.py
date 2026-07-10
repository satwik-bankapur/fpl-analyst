"""Writer LLM module — the single LLM call in the whole system (Layer 4).

Flow: serialize (§2) -> LLM (one call) -> validate (§7) -> one evidence-appended
retry -> deterministic stub fallback. Model + key come from the environment via
the Anthropic SDK; nothing is hard-coded. Every test injects a fake LLM, so the
suite stays zero-spend — the real SDK is imported lazily inside AnthropicWriter.

Import direction: this module imports the Layer 3 stub renderer for the fallback
(writer -> graph is allowed); graph/ never imports back.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from fpl.models import Brief, Player

from graph.nodes.writer import STUB_MODEL
from graph.nodes.writer import writer as _stub_writer

from .config import (
    MAX_RETRIES, MODEL_PRICING, OUTPUT_MAX_TOKENS, SEND_TEMPERATURE, TEMPERATURE,
    model_name,
)
from .serializer import serialize_payload
from .validator import validate_brief

log = logging.getLogger("writer.llm")

PROMPTS_DIR = Path(__file__).parent / "prompts"
DEFAULT_PROMPT_VERSION = "v0.1"


@dataclass
class WriterResult:
    brief_md: str
    model_label: str            # "<model>::<prompt_version>" or "stub-template-v1::fallback"
    tokens_in: int = 0
    tokens_out: int = 0
    retries: int = 0
    validated: bool = False     # did the LLM output pass §7 (False => stub fallback)
    fell_back: bool = False
    failures: list[str] = field(default_factory=list)


def load_system_prompt(version: str = DEFAULT_PROMPT_VERSION) -> str:
    return (PROMPTS_DIR / f"system_{version}.md").read_text(encoding="utf-8")


def all_web_names(session) -> set[str]:
    return {n for (n,) in session.execute(select(Player.web_name)).all() if n}


class AnthropicWriter:
    """Real single-call wrapper. `.generate(system, user) -> (text, usage)`."""

    def generate(self, system: str, user: str) -> tuple[str, dict]:
        import anthropic  # lazy: keeps the offline test suite import-free

        client = anthropic.Anthropic()  # ANTHROPIC_API_KEY / profile resolved by the SDK
        kwargs = dict(
            model=model_name(), max_tokens=OUTPUT_MAX_TOKENS,
            system=system, messages=[{"role": "user", "content": user}],
        )
        if SEND_TEMPERATURE:  # current Sonnet-class model rejects sampling params
            kwargs["temperature"] = TEMPERATURE
        resp = client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        usage = {"input_tokens": resp.usage.input_tokens,
                 "output_tokens": resp.usage.output_tokens}
        return text, usage


def estimate_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    rate_in, rate_out = MODEL_PRICING.get(model, (0.0, 0.0))
    return round(tokens_in / 1e6 * rate_in + tokens_out / 1e6 * rate_out, 5)


def generate_brief(state, deps, *, llm=None, prompt_version: str = DEFAULT_PROMPT_VERSION,
                   persist: bool = False, input_hash: str | None = None) -> WriterResult:
    """Serialize -> LLM -> validate -> retry once -> stub fallback. Optionally persist."""
    llm = llm or AnthropicWriter()
    payload = serialize_payload(state, deps)
    with deps.session_factory() as session:
        known = all_web_names(session)
    system = load_system_prompt(prompt_version)

    user = payload.text
    result: WriterResult | None = None
    for attempt in range(MAX_RETRIES + 1):
        text, usage = llm.generate(system, user)
        v = validate_brief(text, payload.text, known)
        if v.ok:
            result = WriterResult(
                brief_md=text, model_label=f"{model_name()}::{prompt_version}",
                tokens_in=usage.get("input_tokens", 0), tokens_out=usage.get("output_tokens", 0),
                retries=attempt, validated=True)
            break
        log.warning("brief validation failed (attempt %s): %s", attempt, v.failures)
        # append the violation and retry once (§7)
        user = payload.text + "\n\n[VALIDATION FEEDBACK — FIX THESE, REGENERATE]\n" + v.as_note()
        result = WriterResult(
            brief_md=text, model_label=f"{model_name()}::{prompt_version}",
            tokens_in=usage.get("input_tokens", 0), tokens_out=usage.get("output_tokens", 0),
            retries=attempt, validated=False, failures=v.failures)

    if result is None or not result.validated:
        # deterministic stub fallback — never hard-fail (CLAUDE.md permanent fallback)
        stub_md = _stub_writer(state, deps)["brief_md"]
        result = WriterResult(
            brief_md=stub_md, model_label=f"{STUB_MODEL}::fallback",
            tokens_in=0, tokens_out=0, retries=MAX_RETRIES, validated=False, fell_back=True,
            failures=result.failures if result else ["no_output"])

    if persist and input_hash is not None:
        _persist(deps, state, result, input_hash)
    return result


def _persist(deps, state, result: WriterResult, input_hash: str) -> None:
    req = state.request
    with deps.session_factory() as session, session.begin():
        session.add(Brief(
            fpl_team_id=req.fpl_team_id, draft_id=req.draft_id,
            gameweek=req.gameweek_anchor, brief_md=result.brief_md,
            input_hash=input_hash, model=result.model_label,
            tokens_in=result.tokens_in, tokens_out=result.tokens_out))
