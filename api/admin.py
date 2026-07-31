"""Read-only admin queries behind ADMIN_TOKEN (the /admin console).

Nothing here writes. The admin *writes* stay in `api.service` next to the other
mutations, because that is where the skip-if-identical rule that protects the
brief cache key lives — see `service.upsert_note`.

This module reports what is in the database and nothing more. It must never
become a second place where brief content is shaped: no gating, no section keys,
no scoring. If a number here disagrees with a number in a brief, the brief wins
and this module has a bug.
"""
from __future__ import annotations

from sqlalchemy import func, select

from fpl.models import (
    Brief, EmailSignup, EpScore, EuropeanWeek, Fixture, Player, PlayerArchive, TeamNote,
)
from fpl.sync import derive_mode, next_gameweek
from graph.data import load_fixtures, load_teams, resolve_ep_anchor
from graph.deps import Deps
from graph.fixtures_util import swing_score, team_next_fixtures

from .service import iso_utc

_POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

# The engine's honesty vocabulary (engine/scoring.py). A flag qualifies if it
# names a reason the number is softer than it looks: the player may not play
# (`unavailable:*`), the attack term came from a table instead of the player's
# own minutes (`thin_minutes_sample`, `*_fallback`), or a midweek European tie is
# dampening expected minutes (`european_fixture_week:*`).
#
# `injury_doubt:*`, `blank_gw:*`, `double_gw:*` and `early_season_blend` are
# deliberately NOT honesty flags — they are ordinary scoring facts the brief
# already states in prose, and folding them in here would flag most of the league
# and make the dashboard's flagged list useless.
_HONESTY_PREFIXES = ("unavailable", "thin_minutes_sample", "european_fixture_week")


def honesty_flags(components: dict | None) -> list[str]:
    """The subset of a components blob's `flags` that means "trust this less"."""
    flags = (components or {}).get("flags") or []
    return [f for f in flags if f.startswith(_HONESTY_PREFIXES) or f.endswith("_fallback")]


def _anchor(session) -> int | None:
    """The gameweek `ep_scores` is anchored at, else the next GW, else None.

    ep_scores is the truth when it exists — reading fixtures from a different
    anchor than the engine scored at would show a run the projections don't match.
    """
    anchor = resolve_ep_anchor(session)
    if anchor is not None:
        return anchor
    ng = next_gameweek(session)
    return ng.id if ng else None


# --- dashboard -------------------------------------------------------------

def overview(deps: Deps) -> dict:
    """Post-sync sanity check: mode, freshness, row counts, match-method spread."""
    with deps.session_factory() as session:
        mode = derive_mode(session)
        ng = next_gameweek(session)
        counts = {
            "players": session.scalar(select(func.count()).select_from(Player)),
            "fixtures": session.scalar(select(func.count()).select_from(Fixture)),
            "ep_scores": session.scalar(select(func.count()).select_from(EpScore)),
            "briefs": session.scalar(select(func.count()).select_from(Brief)),
            "signups": session.scalar(select(func.count()).select_from(EmailSignup)),
            "team_notes": session.scalar(select(func.count()).select_from(TeamNote)),
            "euro_weeks": session.scalar(select(func.count()).select_from(EuropeanWeek)),
        }
        archive = dict(
            session.execute(
                select(PlayerArchive.season, func.count())
                .group_by(PlayerArchive.season)
                .order_by(PlayerArchive.season)
            ).all()
        )
        last_sync = session.scalar(select(func.max(EpScore.computed_at)))
        # `match_method` lives inside the components JSON. SQLite and Postgres
        # disagree on JSON path syntax, so tally in Python — one column, ~700 rows,
        # on a route only the operator ever calls.
        methods: dict[str, int] = {}
        for comp in session.scalars(select(EpScore.components)):
            m = (comp or {}).get("match_method") or "unknown"
            methods[m] = methods.get(m, 0) + 1
        teams = load_teams(session)

    return {
        "mode": mode,
        "last_sync": iso_utc(last_sync),
        "next_gw": ng.id if ng else None,
        "next_deadline": iso_utc(ng.deadline) if ng else None,
        "counts": counts,
        "archive_by_season": archive,
        "match_method": methods,
        # The club list every other admin page needs (fixture picker, euro-week
        # rows). Twenty rows of three fields — cheaper than a second endpoint.
        "teams": sorted(teams.values(), key=lambda t: t["short_name"]),
    }


def top(deps: Deps, n: int = 20) -> list[dict]:
    """Top N by ep_next4 at the scored anchor. Empty list if nothing is scored."""
    with deps.session_factory() as session:
        anchor = resolve_ep_anchor(session)
        if anchor is None:
            return []
        teams = load_teams(session)
        rows = session.execute(
            select(EpScore, Player)
            .join(Player, Player.id == EpScore.player_id)
            .where(EpScore.gameweek == anchor)
            .order_by(EpScore.ep_next4.desc())
            .limit(n)
        ).all()
    return [
        {
            "id": p.id,
            "web_name": p.web_name,
            "team_short": teams.get(p.team_id, {}).get("short_name", "?"),
            "position": _POS.get(p.position, "?"),
            "price": float(p.price),
            "ep": float(e.ep),
            "ep_next4": float(e.ep_next4),
            "flags": honesty_flags(e.components),
        }
        for e, p in rows
    ]


# --- curated tables --------------------------------------------------------

def notes(deps: Deps) -> list[dict]:
    """One row per club whether or not a note exists — the UI shows 20 cards either way."""
    with deps.session_factory() as session:
        teams = load_teams(session)
        rows = {r.team_id: r for r in session.scalars(select(TeamNote))}
    out = [
        {
            "team_id": tid,
            "team_name": t["name"],
            "team_short": t["short_name"],
            "note": rows[tid].note if tid in rows else "",
            "is_sample": bool(rows[tid].is_sample) if tid in rows else False,
            "updated_at": iso_utc(rows[tid].updated_at) if tid in rows else None,
        }
        for tid, t in teams.items()
    ]
    return sorted(out, key=lambda r: r["team_short"])


def euro_weeks(deps: Deps) -> list[dict]:
    with deps.session_factory() as session:
        teams = load_teams(session)
        rows = session.scalars(
            select(EuropeanWeek).order_by(EuropeanWeek.gameweek, EuropeanWeek.team_id)
        ).all()
        return [
            {
                "team_id": r.team_id,
                "team_short": teams.get(r.team_id, {}).get("short_name", "?"),
                "gameweek": r.gameweek,
                "competition": r.competition,
                "tie_importance": r.tie_importance,
                "updated_at": iso_utc(r.updated_at),
            }
            for r in rows
        ]


# --- players ---------------------------------------------------------------

def players(deps: Deps, q: str = "", flagged: bool = False, limit: int = 100) -> dict:
    """Name search, optionally narrowed to players carrying an honesty flag.

    Returns `total` alongside the (capped) rows: a silently truncated list on a
    page whose whole job is making wrongness visible would defeat the page.
    """
    with deps.session_factory() as session:
        teams = load_teams(session)
        stmt = select(Player)
        q = q.strip()
        if q:
            like = f"%{q}%"
            stmt = stmt.where(Player.web_name.ilike(like) | Player.full_name.ilike(like))
        rows = session.scalars(stmt).all()
        anchor = resolve_ep_anchor(session)
        eps = (
            {r.player_id: r for r in session.scalars(
                select(EpScore).where(EpScore.gameweek == anchor))}
            if anchor is not None else {}
        )

    out = []
    for p in rows:
        e = eps.get(p.id)
        flags = honesty_flags(e.components if e else None)
        if flagged and not flags:
            continue
        out.append({
            "id": p.id,
            "web_name": p.web_name,
            "team_short": teams.get(p.team_id, {}).get("short_name", "?"),
            "position": _POS.get(p.position, "?"),
            "price": float(p.price),
            "status": p.status,
            "news": p.news or "",
            "ep": float(e.ep) if e else None,
            "ep_next4": float(e.ep_next4) if e else None,
            "flags": flags,
        })
    out.sort(key=lambda r: (-(r["ep_next4"] or 0.0), r["web_name"]))
    return {"total": len(out), "players": out[:limit]}


# --- the flagged-player work queue -----------------------------------------
#
# A flat list of flagged players is a fact, not a task. The operator's actual
# question on a Sunday is "where does an hour of my writing buy the most brief
# quality", and the answer is a CLUB, because `team_notes` is a per-club table.
# So this groups by flag, then — for the two flags a note can actually fix — by
# club, ranked by how much that club's briefs stand to improve.

# Every honesty flag, in plain English, with whether writing something changes it.
#   actionable       — a team note materially improves the brief
#   transient        — resolves itself on the next sync; do nothing
#   known_limitation — the engine has no better input; a note won't fix the number
_FLAG_MEANINGS = {
    "no_history_fallback": (
        "actionable",
        "No prior-season record for this player, so the projection falls back to what "
        "players at his price and position typically return.",
    ),
    "thin_minutes_sample": (
        "actionable",
        "Too few minutes played for a reliable attacking rate, so a positional average "
        "stands in for the player's own numbers.",
    ),
    "promoted_team_fallback": (
        "known_limitation",
        "A promoted club with no top-flight record — the projection is discounted from "
        "the price band. Nothing but a season of data fixes this.",
    ),
    "unavailable": (
        "transient",
        "Flagged unavailable by the FPL API (injured, suspended, or out on loan). "
        "Clears on its own when the API updates.",
    ),
    "european_fixture_week": (
        "transient",
        "A midweek European tie is dampening expected minutes for this gameweek only.",
    ),
}

_UNKNOWN_MEANING = (
    "known_limitation",
    "No explanation registered for this flag — add one to `_FLAG_MEANINGS` in api/admin.py.",
)

# The two flags a team note demonstrably improves. Only these get the club
# ranking; ranking clubs by a flag a note cannot fix would send the operator to
# write words that change nothing.
NOTE_IMPROVABLE = ("no_history_fallback", "thin_minutes_sample")

# "Identical EP across a whole club" — measured as COLLAPSE, not literal equality.
#
# Literal equality is the wrong test and was the first thing tried here. A club
# scored entirely on baselines still gets distinct numbers, because the baseline
# is a function of price band and position and those differ across a squad. On the
# 2025-26 data the promoted clubs land at 10-12 distinct ep_next4 values across
# ~29 scored players (ratio ~0.4) while every established club sits at 26-33 of
# 29-36 (ratio ~0.9). So the signal is the RATIO, and a strict-equality rule would
# have quietly never fired.
IDENTICAL_EP_MIN_PLAYERS = 5
IDENTICAL_EP_MAX_DISTINCT_RATIO = 0.5


def _bare(flag: str) -> str:
    """`unavailable:i` -> `unavailable`. Flags carry a suffix; groups do not."""
    return flag.split(":", 1)[0]


def flag_queue(deps: Deps) -> dict:
    """Flagged players as a triage queue: by flag, then by club, ranked by payoff."""
    with deps.session_factory() as session:
        teams = load_teams(session)
        anchor = resolve_ep_anchor(session)
        notes = {
            n.team_id: n for n in session.scalars(select(TeamNote)) if (n.note or "").strip()
        }
        players = {p.id: p for p in session.scalars(select(Player))}
        rows = (
            session.scalars(select(EpScore).where(EpScore.gameweek == anchor)).all()
            if anchor is not None else []
        )

    # club -> (how many scored players, how many DISTINCT ep_next4 values among
    # them). Computed over the whole club, not just the flagged ones: the pattern
    # being detected is "this club was scored on baselines", which flagged-only
    # rows cannot show.
    club_stats: dict[int, dict] = {}
    for r in rows:
        p = players.get(r.player_id)
        if p is None:
            continue
        st = club_stats.setdefault(p.team_id, {"scored": 0, "eps": set()})
        st["scored"] += 1
        st["eps"].add(round(float(r.ep_next4), 2))

    groups: dict[str, list[dict]] = {}
    flagged_ids: set[int] = set()
    for r in rows:
        p = players.get(r.player_id)
        if p is None:
            continue
        for flag in honesty_flags(r.components):
            flagged_ids.add(p.id)
            groups.setdefault(_bare(flag), []).append({
                "id": p.id,
                "web_name": p.web_name,
                "team_id": p.team_id,
                "team_short": teams.get(p.team_id, {}).get("short_name", "?"),
                "position": _POS.get(p.position, "?"),
                "price": float(p.price),
                "news": p.news or "",
                "ep_next4": float(r.ep_next4),
                "flag": flag,
            })

    out_groups = []
    for name, members in groups.items():
        kind, explanation = _FLAG_MEANINGS.get(name, _UNKNOWN_MEANING)
        group = {
            "flag": name,
            "kind": kind,
            "explanation": explanation,
            "count": len(members),
            "note_improvable": name in NOTE_IMPROVABLE,
            "clubs": (_clubs_for(members, teams, notes, club_stats)
                      if name in NOTE_IMPROVABLE else []),
            "players": sorted(members, key=lambda m: -m["ep_next4"])[:12],
        }
        out_groups.append(group)

    # Actionable first — the queue should open on the work, not on the noise.
    order = {"actionable": 0, "known_limitation": 1, "transient": 2}
    out_groups.sort(key=lambda g: (order.get(g["kind"], 3), -g["count"]))

    return {
        "total_flagged_players": len(flagged_ids),
        "groups": out_groups,
        "clubs_total": len(teams),
        "clubs_with_notes": len(notes),
    }


def _clubs_for(members, teams, notes, club_stats) -> list[dict]:
    """Clubs ranked by (count x highest ep_next4) — the note-writing payoff order.

    The product, not either factor alone: twelve flagged fringe players matter less
    than three flagged players who are actually going to be picked, and one flagged
    fringe name matters less than a whole club scored on baselines.
    """
    by_club: dict[int, list[dict]] = {}
    for m in members:
        by_club.setdefault(m["team_id"], []).append(m)

    out = []
    for tid, rows in by_club.items():
        top = max(r["ep_next4"] for r in rows)
        stats = club_stats.get(tid, {"scored": 0, "eps": set()})
        distinct = len(stats["eps"])
        out.append({
            "team_id": tid,
            "team_short": teams.get(tid, {}).get("short_name", "?"),
            "team_name": teams.get(tid, {}).get("name", "?"),
            "count": len(rows),
            "top_ep_next4": round(top, 2),
            "score": round(len(rows) * top, 1),
            "has_note": tid in notes,
            "is_sample": bool(notes[tid].is_sample) if tid in notes else False,
            # The badge: a whole club sharing one or two EP values is a club nobody
            # has data for. The counts are published alongside the boolean so the
            # operator can see the evidence, not just the verdict.
            "scored_players": stats["scored"],
            "distinct_ep_next4": distinct,
            "identical_ep": (
                stats["scored"] >= IDENTICAL_EP_MIN_PLAYERS
                and distinct <= stats["scored"] * IDENTICAL_EP_MAX_DISTINCT_RATIO
            ),
            "players": sorted(rows, key=lambda r: -r["ep_next4"])[:8],
        })
    # Unwritten notes first within the ranking: a club already covered is done work.
    out.sort(key=lambda c: (c["has_note"], -c["score"]))
    return out


# --- fixtures --------------------------------------------------------------

def team_fixtures(deps: Deps, team_short: str, n: int = 6) -> dict | None:
    """A club's next N fixtures + swing. None if `team_short` matches no club."""
    with deps.session_factory() as session:
        teams = load_teams(session)
        match = next(
            (t for t in teams.values() if t["short_name"].lower() == team_short.lower()),
            None,
        )
        if match is None:
            return None
        fixtures = load_fixtures(session)
        anchor = _anchor(session)

    if anchor is None:
        return {"team_id": match["id"], "team_short": match["short_name"],
                "team_name": match["name"], "anchor": None, "swing": 0.0, "fixtures": []}

    runs = team_next_fixtures(fixtures, match["id"], anchor, n)
    return {
        "team_id": match["id"],
        "team_short": match["short_name"],
        "team_name": match["name"],
        "anchor": anchor,
        # graph.fixtures_util is the ONE swing implementation (§2.3) — the number
        # shown here is the number that raises a `fixture_cliff` problem, never a
        # second opinion. It needs 6 fixtures to have both halves; fewer => 0.0.
        "swing": swing_score(runs),
        "fixtures": [
            {
                "gw": gw,
                "opponent_short": teams.get(opp, {}).get("short_name", "?"),
                "is_home": is_home,
                "fdr": fdr,
            }
            for gw, opp, is_home, fdr in runs
        ],
    }
