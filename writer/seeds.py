"""Sample curated content for TESTING SHAPE ONLY (Layer 4 §1.1).

These are illustrative team_notes marked `is_sample=True` so they can be cleared
before real content is authored. Populating the real moat table (the weekly
film-work commitment) is the maintainer's job, not code's.
"""
from __future__ import annotations

from sqlalchemy import select

from fpl.models import Team, TeamNote

# short_name -> illustrative note (sample shape, not authoritative scouting)
SAMPLE_NOTES = {
    "NEW": "Sample: Gordon on pens. Wingers rotate in European weeks; front line doesn't. "
           "High line away from home — vulnerable to pace.",
    "ARS": "Sample: Set-piece threat is elite — defenders carry real goal upside. Saka is "
           "first-choice pen taker when on the pitch.",
    "MCI": "Sample: Haaland aside, rotation is heavy in cup/European weeks. Full-backs invert, "
           "so attacking returns skew to midfielders.",
}


def seed_sample_notes(session) -> int:
    """Insert sample notes for whichever of the named teams exist. Returns count written."""
    shorts = {t.short_name: t.id for t in session.scalars(select(Team)).all()}
    n = 0
    for short, note in SAMPLE_NOTES.items():
        tid = shorts.get(short)
        if tid is not None and session.get(TeamNote, tid) is None:
            session.add(TeamNote(team_id=tid, note=note, is_sample=True))
            n += 1
    return n
