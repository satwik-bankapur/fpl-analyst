"""Payload serialization (§2): completed BriefState -> compact labeled text.

NOT a raw JSON dump — grouped field-name/value lines. Evidence blocks are the
token-budget lever: FULL evidence only for decision-relevant / problem / flagged
players (never all 15). Over budget, drop bench evidence first, then non-decision
fixture rows. All numbers rounded to 1 decimal; web_names throughout.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from graph.data import (
    load_ep_map, load_players, load_teams, resolve_ep_anchor,
)
from sqlalchemy import select

from fpl.models import TeamNote

from .config import CHARS_PER_TOKEN, PAYLOAD_MAX_TOKENS

_POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
_FLAG_EVIDENCE_PREFIXES = ("european_fixture_week", "injury_doubt", "unavailable")


@dataclass
class PayloadResult:
    text: str
    token_estimate: int
    dropped: list[str] = field(default_factory=list)
    # Per-block inventory for the /admin/inspect payload preview: which evidence
    # and fixture blocks the budget rule kept and which it dropped, by name.
    # Additive and defaulted — `dropped` remains the field the writer branches on;
    # nothing in the LLM path reads this. It exists because "dropped:
    # ['bench_evidence']" tells an operator a group went, not WHO went with it.
    blocks: list[dict] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)  # ceil(len/4)


def _r(x) -> str:
    return f"{float(x):.1f}"


def serialize_payload(state, deps, *, max_tokens: int = PAYLOAD_MAX_TOKENS) -> PayloadResult:
    with deps.session_factory() as session:
        anchor = resolve_ep_anchor(session)
        players = load_players(session)
        teams = load_teams(session)
        ep_map = load_ep_map(session, anchor)
        note_rows = {n.team_id: n.note for n in session.scalars(select(TeamNote)).all()}

    sq, diag, mkt, fx, dec = (
        state.squad, state.diagnosis, state.market, state.fixture_outlook, state.decisions,
    )

    def nm(pid): return players.get(pid, {}).get("web_name", f"#{pid}")
    def team_of(pid): return players.get(pid, {}).get("team_id")
    def short(tid): return teams.get(tid, {}).get("short_name", "?")
    def ep(pid): return ep_map.get(pid, {}).get("ep", 0.0)
    def ep4(pid): return ep_map.get(pid, {}).get("ep_next4", 0.0)
    def comp(pid): return ep_map.get(pid, {}).get("components", {}) or {}
    def flags(pid): return comp(pid).get("flags", []) or []

    squad_ids = [pk.player_id for pk in sq.picks]
    slot_of = {pk.player_id: pk.slot for pk in sq.picks}

    # --- which players get FULL evidence blocks --------------------------
    decision_ids = set()
    if dec.transfer.out_id: decision_ids.add(dec.transfer.out_id)
    if dec.transfer.in_id: decision_ids.add(dec.transfer.in_id)
    decision_ids.update({dec.captain.pick_id, dec.captain.vice_id, *dec.watchlist})
    problem_ids = {p.player_id for p in diag.problems if p.player_id}
    flag_ids = {pid for pid in squad_ids
                if any(f.startswith(_FLAG_EVIDENCE_PREFIXES) for f in flags(pid))}
    evidence_ids = decision_ids | problem_ids | flag_ids

    decision_teams = {team_of(pid) for pid in decision_ids} - {None}
    squad_teams = {team_of(pid) for pid in squad_ids} - {None}

    # --- header ----------------------------------------------------------
    header = ["[HEADER]",
              f"team: {sq.team_name or 'Draft Squad'}",
              f"mode: {state.request.mode}",
              f"gameweek_anchor: {state.request.gameweek_anchor or anchor}",
              f"bank: £{_r(sq.bank)}m",
              f"squad_value: £{_r(sq.squad_value)}m",
              f"free_transfers: {diag.free_transfers} (assumed)",
              f"squad_ep_gw: {_r(diag.squad_ep_gw)}",
              f"squad_ep_next4: {_r(diag.squad_ep_next4)}"]
    if sq.overall_rank is not None:
        header.append(f"overall_rank: {sq.overall_rank}")

    # --- squad one-liners ------------------------------------------------
    squad_lines = ["", "[SQUAD]"]
    for pk in sorted(sq.picks, key=lambda p: p.slot):
        pl = players.get(pk.player_id, {})
        role = "XI" if pk.slot <= 11 else "BENCH"
        cap = " (C)" if pk.is_captain else (" (V)" if pk.is_vice else "")
        squad_lines.append(
            f"{role} {nm(pk.player_id)} [{_POS.get(pl.get('position'),'?')} "
            f"{short(pl.get('team_id'))} £{_r(pl.get('price', 0))}] "
            f"ep {_r(ep(pk.player_id))} ep4 {_r(ep4(pk.player_id))}{cap}")

    # --- evidence blocks -------------------------------------------------
    def evidence_block(pid) -> str:
        pl = players.get(pid, {})
        c = comp(pid)
        lines = [f"- {nm(pid)} [{_POS.get(pl.get('position'),'?')} {short(pl.get('team_id'))} "
                 f"£{_r(pl.get('price', 0))}] ep {_r(ep(pid))} ep4 {_r(ep4(pid))}",
                 f"    basis: {c.get('data_basis','?')}  minutes_prob: {_r(c.get('minutes_prob',0))}"
                 f"  base: {_r(c.get('base',0))}  attack: {_r(c.get('attack',0))}"]
        fl = flags(pid)
        if fl:
            lines.append(f"    flags: {', '.join(fl)}")
        news = pl.get("news")
        if news:
            lines.append(f"    news: {news}")
        return "\n".join(lines)

    core_evidence = ["", "[EVIDENCE]"]
    bench_evidence = []
    block_names: list[tuple[str, str]] = []     # (group, label) in emission order
    for pid in sorted(evidence_ids):
        block = evidence_block(pid)
        if slot_of.get(pid, 0) >= 12:           # bench-slot evidence -> droppable first
            bench_evidence.append(block)
            block_names.append(("bench_evidence", nm(pid)))
        else:
            core_evidence.append(block)
            block_names.append(("evidence", nm(pid)))

    # --- problems --------------------------------------------------------
    problem_lines = ["", "[PROBLEMS]"]
    for p in diag.problems:
        who = nm(p.player_id) if p.player_id else "squad"
        problem_lines.append(f"- {p.type} (sev{p.severity}) {who}: {'; '.join(p.evidence)}")
    if len(problem_lines) == 2:
        problem_lines.append("- none")

    # --- decisions verbatim ---------------------------------------------
    dl = ["", "[DECISIONS]"]
    t = dec.transfer
    if t.action == "make":
        dl.append(f"transfer: MAKE out={nm(t.out_id)} in={nm(t.in_id)}")
    else:
        dl.append("transfer: ROLL")
    dl.append(f"transfer_reasoning_codes: {', '.join(t.reasoning_codes)}")
    dl.append(f"captain: {nm(dec.captain.pick_id)}  vice: {nm(dec.captain.vice_id)}")
    if dec.captain.differential_note:
        dl.append(f"captain_differential_note: {dec.captain.differential_note}")
    dl.append(f"bench_order: {', '.join(nm(p) for p in dec.bench_order)}")
    dl.append(f"chip_stance: {dec.chip_stance}")
    dl.append(f"watchlist: {', '.join(nm(p) for p in dec.watchlist)}")
    dl.append(f"reasoning_codes: {', '.join(dec.reasoning_codes)}")

    # --- fixture rows (decision teams = core; other squad teams = droppable) --
    def fixture_row(tr) -> str:
        runs = " ".join(f"{f.opponent_short}({'H' if f.is_home else 'A'},{f.fdr})"
                        for f in tr.next6)
        return f"- {short(tr.team_id)} swing {_r(tr.swing)}: {runs or '(no fixtures)'}"

    core_fixtures = ["", "[FIXTURES]"]
    nondecision_fixtures = []
    for tr in fx.teams:
        if tr.team_id not in squad_teams and tr.team_id not in decision_teams:
            continue
        if tr.team_id in decision_teams:
            core_fixtures.append(fixture_row(tr))
            block_names.append(("fixtures", short(tr.team_id)))
        else:
            nondecision_fixtures.append(fixture_row(tr))
            block_names.append(("nondecision_fixtures", short(tr.team_id)))
    if fx.entering_good_run:
        core_fixtures.append("entering_good_run: "
                             + ", ".join(short(t) for t in fx.entering_good_run))
    if fx.leaving_good_run:
        core_fixtures.append("leaving_good_run: "
                             + ", ".join(short(t) for t in fx.leaving_good_run))

    # --- team notes (decision teams only) --------------------------------
    note_lines = ["", "[TEAM_NOTES]"]
    for tid in sorted(decision_teams):
        if tid in note_rows:
            note_lines.append(f"- {short(tid)}: {note_rows[tid]}")

    # --- warnings --------------------------------------------------------
    warn_lines = ["", "[WARNINGS]"] + ([f"- {w}" for w in dict.fromkeys(state.warnings)]
                                       or ["- none"])

    # --- assemble with drop order ---------------------------------------
    def assemble(bench: bool, nondec_fx: bool) -> str:
        parts = header + squad_lines + core_evidence
        if bench and bench_evidence:
            parts += ["", "[BENCH_EVIDENCE]"] + bench_evidence
        parts += problem_lines + dl + core_fixtures
        if nondec_fx and nondecision_fixtures:
            parts += ["", "[FIXTURES_OTHER]"] + nondecision_fixtures
        parts += note_lines + warn_lines
        return "\n".join(parts)

    dropped: list[str] = []
    text = assemble(True, True)
    if estimate_tokens(text) > max_tokens and bench_evidence:
        dropped.append("bench_evidence")
        text = assemble(False, True)
    if estimate_tokens(text) > max_tokens and nondecision_fixtures:
        dropped.append("nondecision_fixtures")
        text = assemble(False, False)

    blocks = [{"group": group, "label": label, "included": group not in dropped}
              for group, label in block_names]
    return PayloadResult(text=text, token_estimate=estimate_tokens(text),
                         dropped=dropped, blocks=blocks)
