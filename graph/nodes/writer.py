"""Writer STUB (§2.5) — deterministic template renderer, NEVER an LLM.

Interpolates Decisions + evidence into the full brief skeleton. Dull but
structurally complete: it makes the whole graph runnable and demoable with zero
LLM spend, and it stays permanently useful as the LLM-outage / validation-failure
fallback (CLAUDE.md).

Since 2026-07-30 it emits the SAME headers as the Layer 4 LLM prompt, both drawn
from `graph.sections`. Two writers producing one section vocabulary is what lets
the paywall gate either output identically — see `graph/sections.py`.

## This stub is the product (2026-07-30)

`writer/llm.py` is wired to nothing, so EVERY brief a real user reads comes from
here. That makes the phrasebook below load-bearing rather than cosmetic: it used
to print the upstream contracts raw, so readers got lines like
`[dead_bench_spot · sev1] Zubimendi: ep_next4:0.48` and
`codes: delta:+2.45, problem:unavailable, severity:3, flag:no_history_fallback`.

The rule now: NOTHING in the output may name an internal — no problem-type slug,
no severity number, no reasoning code, no engine flag, no database field, no
player id. Every one of those maps through `_problem_sentence()` / `_CODE_NOTES` to one
short plain-English clause. Deliberately flat, factual prose: this is NOT trying
to sound like the LLM writer, it is trying not to sound like a debug log.

The honesty footer is unchanged and deliberately so — a reader must always be
able to see which numbers are assumptions.

This module deliberately does NOT import from `writer/` (the Layer-4 LLM side);
it is a self-contained template renderer, which the deterministic/LLM boundary
explicitly permits.
"""
from __future__ import annotations

from ..config import TRANSFER_MIN_DELTA
from ..data import load_players, load_teams, resolve_ep_anchor
from ..deps import Deps
from ..sections import SECTION_TITLES
from ..state import BriefState

STUB_MODEL = "stub-template-v1"

# --- the phrasebook --------------------------------------------------------
# Evidence keys the upstream nodes emit as "key:value" (layer3 §2.1). Anything
# in this set is STRUCTURED DATA to be reworded; anything else in an evidence
# list is free text (the FPL API's own `news` string) and passes through
# verbatim, which is the one place a raw upstream string is wanted.
_EVIDENCE_KEYS = {
    "status", "chance_of_playing", "swing", "team", "form", "ppg",
    "minutes_prob", "ep_next4", "pid",
    # engine flags copied into evidence — restated in words, never printed
    "unavailable", "injury_doubt",
}

# Reasoning codes / engine flags worth telling a reader about, in plain words.
# A code absent from here is dropped, not printed: an unexplained token is worse
# than silence. Keep this the ONLY place a code becomes prose.
_CODE_NOTES = {
    "no_history_fallback":
        "the incoming player has no prior-season record, so his projection comes "
        "from what players at his price and position typically return",
    "promoted_team_fallback":
        "the incoming player is at a promoted club with no top-flight record, so "
        "his projection is discounted from his price band",
    "thin_minutes_sample":
        "he has played too few minutes for a reliable attacking rate, so a "
        "positional average is used instead",
    "early_season_blend":
        "this early in the season the projection still leans on last season's "
        "returns as well as current form",
}


def _split_evidence(evidence: list[str]) -> tuple[dict, list[str], int]:
    """A Problem's evidence list -> (labelled values, free-text notes, pid count)."""
    vals: dict[str, str] = {}
    notes: list[str] = []
    pids = 0
    for item in evidence:
        key, sep, rest = item.partition(":")
        if sep and key in _EVIDENCE_KEYS:
            if key == "pid":
                pids += 1
            else:
                vals[key] = rest
        else:
            notes.append(item)
    return vals, notes, pids


def _num(value: str | None, default: float = 0.0) -> float:
    try:
        return float(str(value).rstrip("%"))
    except (TypeError, ValueError):
        return default


def _problem_sentence(kind: str, who: str, evidence: list[str]) -> str | None:
    """One reader-facing clause per diagnosed problem. None = say nothing."""
    vals, notes, pids = _split_evidence(evidence)
    news = "; ".join(n for n in notes if n.strip())

    if kind == "unavailable":
        s = f"{who} is not available to play"
        return f"{s} — {news}." if news else f"{s}."
    if kind == "injury_doubt":
        chance = _num(vals.get("chance_of_playing"), 75)
        s = f"{who} is doubtful, rated {chance:.0f}% likely to play"
        return f"{s} — {news}." if news else f"{s}."
    if kind == "fixture_cliff":
        club = vals.get("team", "his club")
        return f"{who}'s fixtures get harder from here ({club} face a difficult run)."
    if kind == "cold_form":
        return (f"{who} is scoring below his own season average right now "
                f"({_num(vals.get('form')):.1f} recently against "
                f"{_num(vals.get('ppg')):.1f} per game).")
    if kind == "blank_exposure":
        n = pids or len([x for x in notes if x])
        return (f"{n} of your starters have a gameweek with no fixture in the "
                "next four.")
    if kind == "dead_bench_spot":
        return (f"{who} is projected to return only "
                f"{_num(vals.get('ep_next4')):.1f} points over the next four "
                "gameweeks, so he adds little from the bench.")
    # An unrecognised problem type: name the player, not the slug.
    return f"{who} needs a look." if who != "your squad" else None


def _code_notes(codes: list[str]) -> list[str]:
    """Plain-English notes for the codes worth surfacing, de-duplicated."""
    out: list[str] = []
    for code in codes:
        bare = code.split(":", 1)[-1] if code.startswith("flag:") else code
        note = _CODE_NOTES.get(bare.split(":")[0])
        if note and note not in out:
            out.append(note)
    return out


def _delta(codes: list[str]) -> float | None:
    for code in codes:
        if code.startswith("delta:"):
            return _num(code.split(":", 1)[1].lstrip("+"), 0.0)
    return None


_CHIP_STANCE = {
    "consider_bench_boost_or_tc":
        "Worth considering the Bench Boost or the Triple Captain.",
}


def writer(state: BriefState, deps: Deps) -> dict:
    with deps.session_factory() as session:
        players = load_players(session)
        teams = load_teams(session)
        anchor = resolve_ep_anchor(session)

    def nm(pid):
        return players.get(pid, {}).get("web_name", f"#{pid}")

    def club(pid):
        tid = players.get(pid, {}).get("team_id")
        return teams.get(tid, {}).get("short_name", "?")

    def short(tid):
        return teams.get(tid, {}).get("short_name", str(tid))

    sq, diag, mkt, fx, dec = (
        state.squad, state.diagnosis, state.market, state.fixture_outlook, state.decisions,
    )
    L: list[str] = []
    title = sq.team_name or "Draft Squad"
    gw_label = state.request.gameweek_anchor or anchor
    L.append(f"# FPL Brief — {title} (GW{gw_label})")
    if sq.overall_rank is not None:
        L.append(f"_Overall rank: {sq.overall_rank:,} · Squad value £{sq.squad_value}m · "
                 f"Bank £{sq.bank}m_")
    else:
        L.append(f"_Pre-season draft · Squad value £{sq.squad_value}m_")

    # --- Verdict ---
    L.append(f"\n## {SECTION_TITLES['verdict']}")
    L.append(f"- Your starting eleven projects {diag.squad_ep_gw} points this gameweek "
             f"and {diag.squad_ep_next4} over the next four, counting the captain double.")
    if diag.problems:
        L.append(f"- {len(diag.problems)} thing"
                 f"{'' if len(diag.problems) == 1 else 's'} worth your attention:")
        for p in diag.problems:
            who = nm(p.player_id) if p.player_id else "your squad"
            sentence = _problem_sentence(p.type, who, p.evidence)
            if sentence:
                L.append(f"  - {sentence}")
    else:
        L.append("- Nothing in the squad needs attention this week.")

    # --- Transfer ---
    # The header varies by action, exactly as the LLM prompt does. Both spellings
    # map to `transfer` in sections.BRIEF_HEADERS.
    t = dec.transfer
    L.append("\n## " + (SECTION_TITLES["transfer"] if t.action == "make"
                        else "Roll the transfer"))
    if t.action == "make":
        L.append(f"- Move {nm(t.out_id)} ({club(t.out_id)}) out and bring "
                 f"{nm(t.in_id)} ({club(t.in_id)}) in.")
        gain = _delta(t.reasoning_codes)
        if gain is not None:
            L.append(f"- That is worth about {gain:.1f} extra points over the next "
                     "four gameweeks on current projections.")
    else:
        L.append(f"- No move is worth making. Nothing available gains more than "
                 f"{TRANSFER_MIN_DELTA:.1f} projected points over the next four "
                 "gameweeks, so bank the transfer.")
    for note in _code_notes(t.reasoning_codes):
        L.append(f"  - Worth knowing: {note}.")

    # --- Captaincy ---
    L.append(f"\n## {SECTION_TITLES['captaincy']}")
    c = dec.captain
    L.append(f"- Captain {nm(c.pick_id)} ({club(c.pick_id)}), with "
             f"{nm(c.vice_id)} ({club(c.vice_id)}) as vice.")
    if c.differential_note:
        # The note is a PREDICATE about the vice (strategist §2.4) — the name is
        # ours to supply, which is how the raw id stopped reaching readers.
        L.append(f"  - {nm(c.vice_id)} {c.differential_note}.")

    # --- Bench order & the plan ---
    # The fixture outlook folds in HERE as of 2026-07-30 (see the layer4 drift
    # note): "the plan" IS the forward view, which is how the LLM prompt has
    # always structured it. A separate `fixtures` key existed only in the stub and
    # was never producible by the LLM writer, so it could not be gated coherently.
    L.append(f"\n## {SECTION_TITLES['bench_plan']}")
    if dec.bench_order:
        L.append("- Bench in this order: "
                 + " → ".join(f"{nm(pid)} ({club(pid)})" for pid in dec.bench_order))
    else:
        L.append("- No bench order could be resolved from this squad.")
    if fx.entering_good_run:
        L.append("- Fixtures easing for: "
                 + ", ".join(short(t) for t in fx.entering_good_run) + ".")
    if fx.leaving_good_run:
        L.append("- Fixtures getting harder for: "
                 + ", ".join(short(t) for t in fx.leaving_good_run) + ".")
    for d in fx.doubles:
        L.append(f"- Two fixtures in gameweek {d.gw} for: "
                 + ", ".join(short(t) for t in d.team_ids) + ".")
    for b in fx.blanks:
        L.append(f"- No fixture in gameweek {b.gw} for: "
                 + ", ".join(short(t) for t in b.team_ids) + ".")

    # --- Chips (only when the stance is not a plain hold, per the prompt) ---
    if dec.chip_stance and dec.chip_stance != "hold":
        L.append(f"\n## {SECTION_TITLES['chips']}")
        L.append("- " + _CHIP_STANCE.get(
            dec.chip_stance,
            f"Chip stance: {dec.chip_stance.replace('_', ' ')}."))
        for code in dec.reasoning_codes:
            if code.startswith("double_gw_players:"):
                n = code.split(":")[1]
                L.append(f"  - {n} of your players have two fixtures in the "
                         "next four gameweeks.")

    # --- Watchlist ---
    L.append(f"\n## {SECTION_TITLES['watchlist']}")
    if dec.watchlist:
        for pid in dec.watchlist:
            L.append(f"- {nm(pid)} ({club(pid)})")
    else:
        L.append("- Nothing worth adding to a watchlist this week.")

    # --- Honesty footer (material warnings surfaced) ---
    # UNCHANGED ON PURPOSE. Every line here is a place the product is guessing,
    # and a reader is entitled to know which numbers are assumptions.
    if state.warnings:
        L.append("\n---")
        notes = {
            "free_transfers_assumed": "Free-transfer count isn't exposed by the API — assumed 1.",
            "sale_price_approximated": "Sale price approximated as current price (no API value).",
        }
        for w in dict.fromkeys(state.warnings):
            L.append(f"> {notes.get(w, w)}")

    return {"brief_md": "\n".join(L)}
