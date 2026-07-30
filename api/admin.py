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
