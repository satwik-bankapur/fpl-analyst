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

# Bumped when the template's OUTPUT changes, because `graph.cache` mixes this into
# the brief input_hash: a writer change must invalidate every cached brief exactly
# once, or readers keep being served the previous template's prose forever.
# v1 -> v2 (2026-07-30): humanised phrasing, then computed draft XI.
# v2 -> v3 (2026-08-06): readability pass — repeated problems of one kind fold
#   into a single sentence, the fixture outlook and watchlist are prose rather
#   than "Label: a, b, c" fragments.
STUB_MODEL = "stub-template-v3"

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


def _problem_sentence(kind: str, who: str, evidence: list[str],
                      *, repeat: bool = False) -> str | None:
    """One reader-facing clause per diagnosed problem. None = say nothing.

    `repeat=True` means this is the second or later problem of its kind, and asks
    for a differently-shaped sentence saying the SAME thing. It exists only for
    the types that cannot be grouped (`unavailable`, `injury_doubt` — see
    `_GROUPABLE`), where two players in a row would otherwise produce the same
    sentence with a name swapped.

    The default (`repeat=False`) wording is unchanged and is pinned verbatim by
    tests/test_graph.py::test_every_problem_type_has_a_plain_english_sentence.
    """
    vals, notes, pids = _split_evidence(evidence)
    news = "; ".join(n for n in notes if n.strip())

    if kind == "unavailable":
        s = (f"{who} also can't play" if repeat
             else f"{who} is not available to play")
        return f"{s} — {news}." if news else f"{s}."
    if kind == "injury_doubt":
        chance = _num(vals.get("chance_of_playing"), 75)
        s = (f"{who} is a doubt too, at {chance:.0f}%" if repeat
             else f"{who} is doubtful, rated {chance:.0f}% likely to play")
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


def _join(names: list[str]) -> str:
    """"A", "A and B", "A, B and C" — an Oxford-comma-free serial list."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


# Longest serial list before it stops being a sentence and becomes a data dump.
# Grouping ten cold-form players into one sentence fixes the repetition and
# replaces it with something nobody can read; four names plus a count is still a
# sentence, and the count keeps the claim complete.
_LIST_LIMIT = 4


def _listed(items: list[str], noun: str = "others") -> str:
    """A serial list, truncated with an honest remainder.

    The truncated form joins with commas only — `_join` would put an "and"
    before the fourth name and the remainder adds another, giving "A, B, C and D
    and 6 others".
    """
    if len(items) <= _LIST_LIMIT:
        return _join(items)
    rest = len(items) - _LIST_LIMIT
    return f"{', '.join(items[:_LIST_LIMIT])} and {rest} {noun}"


# Problem types whose evidence is entirely NUMBERS we re-render ourselves, so
# several of them can be folded into one sentence without dropping anything.
#
# `unavailable` and `injury_doubt` are deliberately NOT here: their evidence
# carries `players.news`, FPL's own free text ("Knee injury - expected back
# 12 Apr"), which is the most useful thing in the whole problem and is different
# for every player. Grouping them would mean either repeating the news inside a
# list — worse than the repetition being fixed — or silently dropping it.
_GROUPABLE = {"dead_bench_spot", "cold_form", "fixture_cliff"}


def _grouped_sentence(kind: str, entries: list[tuple[str, dict]]) -> str | None:
    """One sentence covering SEVERAL problems of the same kind.

    `entries` is [(player name, parsed evidence values)], in squad order. Only
    called with len(entries) > 1; the single case stays in `_problem_sentence`,
    whose exact wording is pinned by tests and reads better on its own.

    Every number here still comes from that player's own evidence. The only thing
    added is the count, which is arithmetic on facts we already have.
    """
    names = [name for name, _ in entries]
    n = len(entries)

    if kind == "dead_bench_spot":
        # THE ONE THIS EXISTS FOR. Previously each bench player got the identical
        # "X is projected to return only N points over the next four gameweeks"
        # sentence, so a squad with four weak bench spots read as four near-copies
        # of the same line — which is what made the stub feel machine-written.
        #
        # NOTE THE CLAIM. It says these spots return "little", which is the
        # engine's own judgement — it raised `dead_bench_spot` for each of them,
        # and the single-player sentence has always said "adds little from the
        # bench". It does NOT assert a threshold: a first draft of this said
        # "each projected under two points" and the demo brief immediately
        # printed it above a 3.0, because the engine's cutoff is not two points
        # and was never ours to restate. Every number shown is the player's own,
        # so a reader who disagrees can see exactly what we based it on.
        listed = _listed([f"{name} ({_num(v.get('ep_next4')):.1f})"
                          for name, v in entries])
        return (f"{n} bench spots are projected to return little over the next "
                f"four gameweeks: {listed}.")

    if kind == "cold_form":
        listed = _listed([f"{name} ({_num(v.get('form')):.1f} against "
                          f"{_num(v.get('ppg')):.1f} per game)" for name, v in entries])
        return (f"{n} of your players are scoring below their own season "
                f"averages right now: {listed}.")

    if kind == "fixture_cliff":
        clubs = _join(sorted({v.get("team", "") for _, v in entries} - {""}))
        who = _listed(names)
        return (f"{who} face a harder run from here"
                + (f" — {clubs} all have a difficult set of fixtures." if clubs
                   else "."))
    return None


def _by_team_set(events) -> list[tuple[tuple, list[int]]]:
    """Collapse fixture events that name the SAME clubs into one entry.

    `events` is a list of things carrying `.gw` and `.team_ids` (doubles or
    blanks). Returns [(team_ids, [gameweeks])], first-appearance order preserved
    so the output stays deterministic.
    """
    order: list[tuple] = []
    gws: dict[tuple, list[int]] = {}
    for e in events:
        key = tuple(e.team_ids)
        if key not in gws:
            order.append(key)
            gws[key] = []
        gws[key].append(e.gw)
    return [(k, gws[k]) for k in order]


def _gw_phrase(gws: list[int]) -> str:
    """"gameweek 7" / "gameweeks 7 and 9" / "gameweeks 39-43" for a run."""
    if len(gws) == 1:
        return f"gameweek {gws[0]}"
    ordered = sorted(gws)
    if ordered == list(range(ordered[0], ordered[-1] + 1)):
        return f"gameweeks {ordered[0]}-{ordered[-1]}"
    return f"gameweeks {_join([str(g) for g in ordered])}"


def _problem_lines(problems, name_of) -> list[str]:
    """The verdict's problem bullets: one sentence per problem, or per GROUP.

    Groups by type while preserving the order each type first appears, so the
    output stays deterministic — the whole engine is, and a brief whose sentences
    reorder between identical runs would break the cache's reason for existing.

    Returns rendered sentences. The caller must count THESE rather than the raw
    problem list: after grouping, "4 things worth your attention" above two
    bullets is a brief arguing with itself.
    """
    order: list[str] = []
    by_kind: dict[str, list] = {}
    for p in problems:
        if p.type not in by_kind:
            order.append(p.type)
            by_kind[p.type] = []
        by_kind[p.type].append(p)

    lines: list[str] = []
    for kind in order:
        group = by_kind[kind]
        if len(group) > 1 and kind in _GROUPABLE:
            entries = [(name_of(p.player_id) if p.player_id else "your squad",
                        _split_evidence(p.evidence)[0]) for p in group]
            sentence = _grouped_sentence(kind, entries)
            if sentence:
                lines.append(sentence)
                continue
        for i, p in enumerate(group):
            who = name_of(p.player_id) if p.player_id else "your squad"
            sentence = _problem_sentence(p.type, who, p.evidence, repeat=i > 0)
            if sentence:
                lines.append(sentence)
    return lines


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
    if state.request.draft_id is not None:
        # A draft has no lineup, so the eleven above is one we chose. Say so —
        # the projection means nothing if the reader assumes it is their own XI.
        L.append("- Best XI picked from your 15: a draft has no set lineup, so the "
                 "eleven above were chosen on projected points.")
    problem_lines = _problem_lines(diag.problems, nm)
    if problem_lines:
        # Count the RENDERED lines, not diag.problems. Grouping means four bench
        # problems become one sentence, and "4 things worth your attention" above
        # two bullets is the brief contradicting itself in the reader's face.
        n = len(problem_lines)
        L.append(f"- {n} thing{'' if n == 1 else 's'} worth your attention:")
        for sentence in problem_lines:
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
    # The forward view, as sentences rather than "Label: a, b, c" fragments
    # (2026-08-06). Same facts, same order, same club codes — the colon-list form
    # read like a database dump, which is what the readability pass was for.
    if fx.entering_good_run:
        teams_ = _join([short(t) for t in fx.entering_good_run])
        L.append(f"- Fixtures ease from here for {teams_}.")
    if fx.leaving_good_run:
        teams_ = _join([short(t) for t in fx.leaving_good_run])
        L.append(f"- They get harder for {teams_}.")
    # Doubles and blanks are collapsed by TEAM SET (2026-08-06): the same fifteen
    # clubs blanking in five consecutive gameweeks previously printed five
    # near-identical lines listing all fifteen each time. Same facts, same
    # gameweek numbers, said once.
    for team_ids, gws in _by_team_set(fx.doubles):
        L.append(f"- {_listed([short(t) for t in team_ids], 'more')} play twice "
                 f"in {_gw_phrase(gws)}.")
    for team_ids, gws in _by_team_set(fx.blanks):
        names_ = _listed([short(t) for t in team_ids], "more")
        plural = "have" if len(team_ids) > 1 else "has"
        L.append(f"- {names_} {plural} no fixture at all in {_gw_phrase(gws)}.")

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
        # One sentence, not a column of bare "Name (CLU)" fragments (2026-08-06).
        # Nothing is added or implied — it is the same names in the same order,
        # in a form a person would actually say out loud.
        L.append("- Worth keeping an eye on: "
                 + _join([f"{nm(pid)} ({club(pid)})" for pid in dec.watchlist]) + ".")
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
