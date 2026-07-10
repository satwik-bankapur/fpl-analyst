"""Error responder (§3) — the ONLY node reached on a fatal error.

Turns `state.error` into a user-facing message string in `brief_md`. Never a
partial brief: if we are here, no analysis sections are emitted at all.
"""
from __future__ import annotations

from ..deps import Deps
from ..state import BriefState

_MESSAGES = {
    "invalid_team_id": (
        "We couldn't find that FPL team ID. Double-check the number in your team's "
        "URL and try again."
    ),
    "no_picks_yet": (
        "No squad is available yet for this gameweek — this is normal pre-season or "
        "before the first deadline. Set up a draft squad and we'll brief that instead."
    ),
    "fpl_api_down": (
        "The official FPL service isn't responding right now. Your data is untouched; "
        "please try again shortly."
    ),
    "empty_ep_table": (
        "Player projections aren't ready yet (no EP data). This usually clears after "
        "the next scheduled sync."
    ),
}


def error_responder(state: BriefState, deps: Deps) -> dict:
    err = state.error
    code = err.code if err else "fpl_api_down"
    msg = _MESSAGES.get(code, "Something went wrong generating your brief.")
    return {"brief_md": f"# Brief unavailable\n\n{msg}"}
