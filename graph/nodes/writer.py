"""Writer STUB (§2.5) — deterministic template renderer, NEVER an LLM.

Interpolates Decisions + evidence into the full brief skeleton. Ugly but
structurally complete: it makes the whole graph runnable and demoable with zero
LLM spend, and it stays permanently useful as the LLM-outage / validation-failure
fallback (CLAUDE.md).

Since 2026-07-30 it emits the SAME headers as the Layer 4 LLM prompt, both drawn
from `graph.sections`. Two writers producing one section vocabulary is what lets
the paywall gate either output identically — see `graph/sections.py`.

This module deliberately does NOT import from `writer/` (the Layer-4 LLM side);
it is a self-contained template renderer, which the deterministic/LLM boundary
explicitly permits.
"""
from __future__ import annotations

from ..data import load_players, load_teams, resolve_ep_anchor
from ..deps import Deps
from ..sections import SECTION_TITLES
from ..state import BriefState

STUB_MODEL = "stub-template-v1"


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
    L.append(f"- Projected XI: {diag.squad_ep_gw} pts this GW, {diag.squad_ep_next4} over the "
             f"next 4 (captain doubled).")
    L.append(f"- Problems flagged: {len(diag.problems)}. Chip stance: {dec.chip_stance}.")
    for p in diag.problems:
        who = nm(p.player_id) if p.player_id else "squad"
        L.append(f"  - [{p.type} · sev{p.severity}] {who}: {'; '.join(p.evidence)}")

    # --- Transfer ---
    # The header varies by action, exactly as the LLM prompt does. Both spellings
    # map to `transfer` in sections.BRIEF_HEADERS.
    t = dec.transfer
    L.append("\n## " + (SECTION_TITLES["transfer"] if t.action == "make"
                        else "Roll the transfer"))
    if t.action == "make":
        L.append(f"- MAKE: out {nm(t.out_id)} ({club(t.out_id)}) → in {nm(t.in_id)} "
                 f"({club(t.in_id)}).")
    else:
        L.append("- ROLL: no move clears the +2.0 ep_next4 bar; bank the transfer.")
    if t.reasoning_codes:
        L.append(f"  - codes: {', '.join(t.reasoning_codes)}")

    # --- Captaincy ---
    L.append(f"\n## {SECTION_TITLES['captaincy']}")
    c = dec.captain
    L.append(f"- Captain: {nm(c.pick_id)} ({club(c.pick_id)}). Vice: {nm(c.vice_id)} "
             f"({club(c.vice_id)}).")
    if c.differential_note:
        L.append(f"  - {c.differential_note}")

    # --- Bench order & the plan ---
    # The fixture outlook folds in HERE as of 2026-07-30 (see the layer4 drift
    # note): "the plan" IS the forward view, which is how the LLM prompt has
    # always structured it. A separate `fixtures` key existed only in the stub and
    # was never producible by the LLM writer, so it could not be gated coherently.
    L.append(f"\n## {SECTION_TITLES['bench_plan']}")
    if dec.bench_order:
        L.append("- " + " → ".join(f"{nm(pid)} ({club(pid)})" for pid in dec.bench_order))
    else:
        L.append("- (no bench resolved)")
    if fx.entering_good_run:
        L.append("- Entering a good run: "
                 + ", ".join(teams.get(t, {}).get("short_name", str(t))
                             for t in fx.entering_good_run))
    if fx.leaving_good_run:
        L.append("- Fixtures turning: "
                 + ", ".join(teams.get(t, {}).get("short_name", str(t))
                             for t in fx.leaving_good_run))
    for d in fx.doubles:
        L.append(f"- Double GW{d.gw}: "
                 + ", ".join(teams.get(t, {}).get("short_name", str(t)) for t in d.team_ids))
    for b in fx.blanks:
        L.append(f"- Blank GW{b.gw}: "
                 + ", ".join(teams.get(t, {}).get("short_name", str(t)) for t in b.team_ids))

    # --- Chips (only when the stance is not a plain hold, per the prompt) ---
    if dec.chip_stance and dec.chip_stance != "hold":
        L.append(f"\n## {SECTION_TITLES['chips']}")
        L.append(f"- Stance: {dec.chip_stance.replace('_', ' ')}.")
        for code in dec.reasoning_codes:
            if code.startswith("double_gw_players:"):
                L.append(f"  - {code.split(':')[1]} squad players have a double in the horizon.")

    # --- Watchlist ---
    L.append(f"\n## {SECTION_TITLES['watchlist']}")
    if dec.watchlist:
        for pid in dec.watchlist:
            L.append(f"- {nm(pid)} ({club(pid)})")
    else:
        L.append("- (nothing actionable this week)")

    # --- Honesty footer (material warnings surfaced) ---
    if state.warnings:
        L.append("\n---")
        notes = {
            "free_transfers_assumed": "Free-transfer count isn't exposed by the API — assumed 1.",
            "sale_price_approximated": "Sale price approximated as current price (no API value).",
        }
        for w in dict.fromkeys(state.warnings):
            L.append(f"> {notes.get(w, w)}")

    return {"brief_md": "\n".join(L)}
