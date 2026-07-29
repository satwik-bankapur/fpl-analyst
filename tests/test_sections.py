"""The paywall contract. These tests exist to make fail-OPEN impossible.

Before 2026-07-30 the section vocabulary was defined three times (stub writer
headers, LLM prompt headers, api._SECTION_KEYS) and they did not agree. Gating on
the spec's `["bench_plan","chips","watchlist"]` would have locked NOTHING against
LLM output — every paid section would have been served free — while the lock card
advertised a `chips` section no writer produced.

Anything that weakens these assertions is reintroducing that bug. If a test here
fails, do not adjust the assertion: fix the vocabulary.
"""
from __future__ import annotations

import pytest

from graph.sections import (
    BRIEF_HEADERS, FREE_SECTIONS, PAID_SECTIONS, PREVIEW_HEADERS, PREVIEW_ORDER,
    SECTION_ORDER, SECTION_TITLES, UNKNOWN_PREFIX, gate_sections, split_sections,
)

# --- the two writer formats, as literal output -----------------------------
# Kept as literal markdown ON PURPOSE. Generating these from the same constants
# the code under test uses would make the test vacuous — it would pass even if
# both producer and consumer drifted together. These strings must be updated by
# hand when a writer's headers genuinely change, which is the point.

STUB_MD = """# FPL Brief — Draft Squad (GW1)
_Pre-season draft · Squad value £100.0m_

## The verdict
- Projected XI: 55.0 pts this GW.

## This week's transfer
- MAKE: out Smith (BOU) → in Jones (ARS).

## Captaincy
- Captain: Haaland (MCI). Vice: Salah (LIV).

## Bench order & the plan
- Raya (ARS) → Gordon (NEW)
- Entering a good run: NEW, ARS

## Chips
- Stance: consider bench boost or tc.

## Watchlist
- Mbeumo (BRE)
"""

LLM_MD = """# Gameweek 1

## The verdict
Your squad's problem is the bench, not the XI.

## Roll the transfer
Nothing clears the bar this week. Bank it.

## Captaincy
Haaland, and it isn't close.

## Bench order & the plan
Raya first, then Gordon. The next four are kind to Newcastle.

## Chips
Two doubles in the horizon make the bench boost live.

## Watchlist
Mbeumo, Rogers, Wissa.
"""

BOTH_FORMATS = pytest.mark.parametrize("md", [STUB_MD, LLM_MD], ids=["stub", "llm"])


# --- (a) every canonical key is producible by BOTH writers -----------------

@BOTH_FORMATS
def test_every_canonical_key_is_producible(md):
    """Each key in SECTION_ORDER must be reachable from both writer formats via
    the same splitter. A key no writer can emit is a key the paywall can only
    lie about."""
    keys = set(split_sections(md))
    missing = [k for k in SECTION_ORDER if k not in keys]
    assert not missing, f"canonical keys no writer produced: {missing}"


@BOTH_FORMATS
def test_splitter_emits_no_unknown_keys_for_known_writers(md):
    """Neither writer may produce a header the vocabulary can't map. An unknown
    key means a section silently became unlockable."""
    unknown = [k for k in split_sections(md) if k.startswith(UNKNOWN_PREFIX)]
    assert not unknown, f"unmapped headers from a known writer: {unknown}"


def test_both_writers_agree_on_keys():
    assert set(split_sections(STUB_MD)) == set(split_sections(LLM_MD))


def test_transfer_header_variants_both_map_to_transfer():
    """The header changes with the action ('This week's transfer' / 'Roll the
    transfer'). Both must land on the same key or gating is action-dependent."""
    assert "transfer" in split_sections(STUB_MD)   # make
    assert "transfer" in split_sections(LLM_MD)    # roll


# --- (b) gating actually removes paid content, for BOTH formats ------------

@BOTH_FORMATS
def test_gating_removes_paid_content(md):
    sections = split_sections(md)
    visible, locked = gate_sections(sections, paywall=True)

    for key in PAID_SECTIONS:
        assert key not in visible, f"{key} leaked into the free view"
    assert sorted(locked) == sorted(PAID_SECTIONS)

    # And the actual prose is gone, not merely the key.
    blob = "\n".join(visible.values())
    for key in PAID_SECTIONS:
        for line in sections[key].splitlines():
            if line.strip():
                assert line.strip() not in blob, f"{key} content leaked verbatim"


@BOTH_FORMATS
def test_gating_keeps_free_content(md):
    visible, _ = gate_sections(split_sections(md), paywall=True)
    assert sorted(visible) == sorted(FREE_SECTIONS)
    assert all(visible[k].strip() for k in FREE_SECTIONS)


@BOTH_FORMATS
def test_paywall_off_returns_everything(md):
    sections = split_sections(md)
    visible, locked = gate_sections(sections, paywall=False)
    assert visible == sections
    assert locked == []


def test_free_and_paid_partition_the_vocabulary():
    """No key may be both free and paid, and none may be neither — an unclassified
    canonical key is exactly the hole the allow-list exists to close."""
    assert set(FREE_SECTIONS).isdisjoint(PAID_SECTIONS)
    assert set(FREE_SECTIONS) | set(PAID_SECTIONS) == set(SECTION_ORDER)


def test_lock_card_never_advertises_an_absent_section():
    """The lock card is built from `locked`. A paid section the writer didn't
    produce must not be named, or we promise content that isn't there."""
    md = STUB_MD.replace("## Chips\n- Stance: consider bench boost or tc.\n\n", "")
    sections = split_sections(md)
    assert "chips" not in sections
    _, locked = gate_sections(sections, paywall=True)
    assert "chips" not in locked
    assert sorted(locked) == ["bench_plan", "watchlist"]


def test_empty_paid_section_is_not_advertised():
    md = STUB_MD.replace("- Stance: consider bench boost or tc.", "")
    _, locked = gate_sections(split_sections(md), paywall=True)
    assert "chips" not in locked


# --- (c) an unknown header can never leak a lockable section ---------------

@pytest.mark.parametrize("header", [
    "Bench order and the plan",   # '&' -> 'and': a plausible LLM drift
    "Bench Order & The Plan!",    # punctuation drift
    "The Plan",                   # renamed section
    "Chips and strategy",         # near-miss on a paid key
    "Watchlist notes",
])
def test_unknown_header_is_withheld_not_leaked(header):
    """THE fail-closed property. A header the vocabulary doesn't recognise must
    never reach a free reader, because we cannot prove it isn't paid analysis.

    A deny-list would serve this content free (it isn't in PAID_SECTIONS). The
    allow-list withholds it. Losing this test means losing the paywall."""
    md = f"## The verdict\nfree stuff\n\n## {header}\nPAID ANALYSIS HERE\n"
    sections = split_sections(md)

    key = next(k for k in sections if k.startswith(UNKNOWN_PREFIX))
    assert key not in SECTION_ORDER
    assert sections[key] == "PAID ANALYSIS HERE"

    visible, locked = gate_sections(sections, paywall=True)
    assert "PAID ANALYSIS HERE" not in "\n".join(visible.values())
    assert key not in visible
    assert key not in locked          # unknown != advertised as paid


def test_unknown_prefix_cannot_collide_with_a_canonical_key():
    assert not any(k.startswith(UNKNOWN_PREFIX) for k in SECTION_ORDER)
    assert not any(k.startswith(UNKNOWN_PREFIX) for k in PREVIEW_ORDER)


def test_no_header_alias_maps_outside_the_canonical_vocabulary():
    """Every alias must land on a real canonical key — a typo'd target would
    create a key that is neither free nor paid, i.e. silently unreachable."""
    bad = {h: k for h, k in BRIEF_HEADERS.items() if k not in SECTION_ORDER}
    assert not bad, f"aliases pointing outside SECTION_ORDER: {bad}"


def test_header_aliases_are_lowercased():
    """Lookup is on `header.lower()`; a capitalised alias key is dead weight that
    silently never matches."""
    wrong = [h for h in BRIEF_HEADERS if h != h.lower()]
    assert not wrong, f"aliases that can never match: {wrong}"


def test_legacy_stub_headers_still_split():
    """`briefs.brief_md` rows persisted before the unification carry the old
    headers and are re-split on every cache hit. Dropping a legacy alias
    reclassifies old briefs as unknown -> withheld from paying readers."""
    legacy = ("## Verdict\na\n\n## Transfer\nb\n\n## Captaincy\nc\n\n"
              "## Bench order\nd\n\n## Watchlist\ne\n\n## Fixture notes\nf\n")
    keys = split_sections(legacy)
    assert not [k for k in keys if k.startswith(UNKNOWN_PREFIX)]
    assert {"verdict", "transfer", "captaincy", "bench_plan", "watchlist"} <= set(keys)


# --- the preview vocabulary is disjoint and never lockable ----------------

def test_preview_vocabulary_is_disjoint_from_the_brief():
    """If a preview key ever equalled a brief key, the public teaser could be
    gated — or worse, satisfy a paid key and leak the real section's slot."""
    assert set(PREVIEW_ORDER).isdisjoint(SECTION_ORDER)
    assert set(PREVIEW_HEADERS.values()).isdisjoint(SECTION_ORDER)


def test_preview_headers_map_explicitly():
    md = ("## The week ahead\na\n\n## Form and fixtures\nb\n\n"
          "## The differentials\nc\n\n## Trap of the week\nd\n")
    keys = split_sections(md, PREVIEW_HEADERS)
    assert sorted(keys) == sorted(PREVIEW_ORDER)
    assert not [k for k in keys if k.startswith(UNKNOWN_PREFIX)]


def test_titles_cover_every_key():
    assert sorted(SECTION_TITLES) == sorted(SECTION_ORDER)


def test_splitter_drops_the_title_card():
    out = split_sections("# Title\n_meta_\n\n## The verdict\nbody\n")
    assert sorted(out) == ["verdict"]
    assert out["verdict"] == "body"
