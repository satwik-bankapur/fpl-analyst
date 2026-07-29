"""THE canonical brief section vocabulary — one source of truth for section keys.

Every producer (the Layer 3 stub writer, the Layer 4 LLM prompt, the generic
preview) and every consumer (the splitter, the paywall, the frontend mirror in
`web/lib/api.ts`) derives its keys from this module. No layer defines its own
header strings any more.

Why it lives in `graph/`: `graph/` owns the brief skeleton, and the import
direction in CLAUDE.md is `writer/` MAY import `graph/`, never the reverse. So
`graph/` is the only package all three of graph/writer/api can share. This module
imports nothing from the project — it is pure data plus two pure functions.

## The paywall invariant (read before touching `gate_sections`)

Free-tier gating is an **allow-list**: a section reaches a free reader only if
its key is in `FREE_SECTIONS`. It is NOT a deny-list over `PAID_SECTIONS`.

That distinction is the whole safety property. With a deny-list, any header the
splitter fails to recognise (an LLM typo, a prompt revision, a new section)
falls through as "not denied" and leaks paid analysis for free. With an
allow-list, an unrecognised header is withheld by construction — the failure
mode is "a paying reader is missing a section", never "everyone reads the paid
product". Wrong in the recoverable direction.

`split_sections` reinforces this: any header it cannot map is prefixed with
`UNKNOWN_PREFIX`, which can never equal a canonical key, so unmapped content can
never impersonate a free section. Keep both properties or the paywall is
decorative. `tests/test_sections.py` fails loudly if either is weakened.
"""
from __future__ import annotations

# --- canonical keys, in render order --------------------------------------
# This list IS the paywall vocabulary. A key absent from here cannot be locked,
# cannot be shown free, and cannot be named on the lock card.
SECTION_ORDER: list[str] = [
    "verdict",
    "transfer",
    "captaincy",
    "bench_plan",
    "chips",
    "watchlist",
]

# Display titles. `web/lib/api.ts` mirrors these verbatim — change both together.
SECTION_TITLES: dict[str, str] = {
    "verdict": "The verdict",
    "transfer": "This week's transfer",
    "captaincy": "Captaincy",
    "bench_plan": "Bench order & the plan",
    "chips": "Chips",
    "watchlist": "Watchlist",
}

# The Stage A/B split (layer5 §1.1, §6). FREE_SECTIONS is the allow-list.
FREE_SECTIONS: list[str] = ["verdict", "transfer", "captaincy"]
PAID_SECTIONS: list[str] = ["bench_plan", "chips", "watchlist"]

# --- header text -> canonical key -----------------------------------------
# Covers BOTH writer formats. Lookup is on the lowercased, stripped header text.
#
# The legacy stub headers ("verdict", "transfer", "bench order") MUST stay:
# `briefs.brief_md` rows persisted before the 2026-07-30 unification still carry
# them, and `split_sections` re-splits those rows on every cache hit. Deleting a
# legacy alias silently reclassifies old briefs as unknown -> withheld.
BRIEF_HEADERS: dict[str, str] = {
    # --- Layer 4 LLM prompt (writer/prompts/system_v0.1.md §"Output structure")
    # Also what the stub emits since the 2026-07-30 unification.
    "the verdict": "verdict",
    "this week's transfer": "transfer",
    "roll the transfer": "transfer",
    "captaincy": "captaincy",
    "bench order & the plan": "bench_plan",
    "chips": "chips",
    "watchlist": "watchlist",
    # --- legacy stub headers (pre-unification persisted briefs) ---
    "verdict": "verdict",
    "transfer": "transfer",
    "bench order": "bench_plan",
    "fixture notes": "bench_plan",  # folded: see the 2026-07-30 drift note
}

# --- the generic preview -------------------------------------------------
# A PUBLIC teaser, not a personal brief. Its keys are deliberately disjoint from
# SECTION_ORDER so a preview section can never be mistaken for a lockable one.
PREVIEW_ORDER: list[str] = [
    "the_week_ahead",
    "form_and_fixtures",
    "the_differentials",
    "trap_of_the_week",
]

PREVIEW_TITLES: dict[str, str] = {
    "the_week_ahead": "The week ahead",
    "form_and_fixtures": "Form and fixtures",
    "the_differentials": "The differentials",
    "trap_of_the_week": "Trap of the week",
}

PREVIEW_HEADERS: dict[str, str] = {
    "the week ahead": "the_week_ahead",
    "form and fixtures": "form_and_fixtures",
    "the differentials": "the_differentials",
    "trap of the week": "trap_of_the_week",
}

# Prefix for headers no vocabulary maps. Chosen so it can never collide with a
# canonical key (none start with "x_"), which is what stops unmapped content from
# impersonating a free section.
UNKNOWN_PREFIX = "x_"


def _slug(header: str) -> str:
    return header.lower().strip().replace(" ", "_")


def split_sections(brief_md: str, headers: dict[str, str] | None = None) -> dict[str, str]:
    """`brief_md` -> {canonical_key: markdown}.

    Lines before the first `## ` (the title/meta card) are dropped — the response
    carries team/rank/bank as structured fields instead.

    A header absent from `headers` is kept, but under `UNKNOWN_PREFIX + slug`, so
    it survives for debugging while being structurally incapable of passing the
    `FREE_SECTIONS` allow-list. Never map an unknown header to a canonical key.
    """
    vocab = BRIEF_HEADERS if headers is None else headers
    out: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []
    for line in brief_md.splitlines():
        if line.startswith("## "):
            if key is not None:
                out[key] = "\n".join(buf).strip()
            head = line[3:].strip()
            mapped = vocab.get(head.lower())
            key = mapped if mapped else UNKNOWN_PREFIX + _slug(head)
            buf = []
        elif key is not None:
            buf.append(line)
    if key is not None:
        out[key] = "\n".join(buf).strip()
    return out


def gate_sections(
    sections: dict[str, str], *, paywall: bool
) -> tuple[dict[str, str], list[str]]:
    """Apply the free-tier paywall. Returns `(visible_sections, locked_keys)`.

    `paywall=False` (Stage A) returns everything with no locked keys.

    `paywall=True` (Stage B) returns ONLY sections whose key is in
    `FREE_SECTIONS` — an allow-list, so an unrecognised or newly-added key is
    withheld rather than leaked. See the module docstring.

    `locked_keys` names only paid sections that were ACTUALLY present and
    removed. A paid section the writer never produced is not advertised, so the
    lock card can never promise content that does not exist behind it.
    """
    if not paywall:
        return dict(sections), []
    visible = {k: v for k, v in sections.items() if k in FREE_SECTIONS}
    locked = [k for k in PAID_SECTIONS if k in sections and sections[k].strip()]
    return visible, locked
