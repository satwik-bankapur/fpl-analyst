"""Post-generation validation (§7) — deterministic, runs before persist/send.

Enforces the closed-world contract (CLAUDE.md): the brief may only assert facts
present in its payload. Five checks; any failure is actionable by the writer flow
(one evidence-appended retry, then stub fallback). NO LLM calls here — pure text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import LENGTH_MAX_WORDS, LENGTH_MIN_WORDS, REQUIRED_HEADERS, TRANSFER_HEADERS

# £N.N  or  N.N pts  (rounded figures the brief may cite)
_MONEY_RE = re.compile(r"£\d+\.\d")
_PTS_RE = re.compile(r"\d+\.\d\s*pts")


@dataclass
class ValidationResult:
    ok: bool
    failures: list[str] = field(default_factory=list)
    word_count: int = 0

    def as_note(self) -> str:
        """Human-readable violation note appended to the retry prompt."""
        return "; ".join(self.failures)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'£.-]+\b", text))


def validate_brief(output_md: str, payload_text: str, known_names) -> ValidationResult:
    failures: list[str] = []

    # 1. required section headers present AND in order (verdict < transfer < captaincy)
    idx_verdict = output_md.find(REQUIRED_HEADERS[0])
    idx_captain = output_md.find(REQUIRED_HEADERS[1])
    idx_transfer = max(output_md.find(h) for h in TRANSFER_HEADERS)
    if idx_verdict < 0:
        failures.append("missing_header:## The verdict")
    if idx_transfer < 0:
        failures.append("missing_header:## This week's transfer|Roll the transfer")
    if idx_captain < 0:
        failures.append("missing_header:## Captaincy")
    if idx_verdict >= 0 and idx_transfer >= 0 and idx_captain >= 0:
        if not (idx_verdict < idx_transfer < idx_captain):
            failures.append("headers_out_of_order")

    # 2. name whitelist: any KNOWN player name in the output must also be in the payload
    for name in known_names:
        if not name:
            continue
        if re.search(rf"\b{re.escape(name)}\b", output_md) and name not in payload_text:
            failures.append(f"invented_name:{name}")

    # 3. number sanity: every £N.N and N.N pts figure must appear in the payload
    for m in set(_MONEY_RE.findall(output_md)):
        if m not in payload_text:
            failures.append(f"number_not_in_payload:{m}")
    for m in set(_PTS_RE.findall(output_md)):
        rounded = m.replace(" ", "")
        if rounded not in payload_text.replace(" ", ""):
            failures.append(f"number_not_in_payload:{m}")

    # 4. length gate
    wc = _word_count(output_md)
    if wc < LENGTH_MIN_WORDS or wc > LENGTH_MAX_WORDS:
        failures.append(f"length:{wc}")

    return ValidationResult(ok=not failures, failures=failures, word_count=wc)
