"""HTTP routes (spec §1). Thin: validate, call `service`, shape errors.

Stage A surface only: brief (draft path), preview, players, signup, health, admin.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from fpl.errors import FPLError
from graph.deps import Deps

from . import mail, service
from .config import settings
from .ratelimit import limiter
from .service import get_deps

router = APIRouter(prefix="/api")


def _valid_email(v: str | None) -> str | None:
    # Light trust-boundary guard, no email-validator dependency. Resend does the
    # real deliverability check; we only reject the obviously-malformed here.
    if v is None:
        return None
    v = v.strip()
    if "@" not in v or "." not in v.split("@")[-1] or len(v) > 254:
        raise ValueError("invalid email")
    return v


# --- request models --------------------------------------------------------

class Draft(BaseModel):
    player_ids: list[int] = Field(min_length=15, max_length=15)
    captain_id: int | None = None


class BriefRequest(BaseModel):
    team_id: int | None = None
    draft: Draft | None = None
    email: str | None = None
    _ck_email = field_validator("email")(_valid_email)


class SignupRequest(BaseModel):
    email: str
    team_id: int | None = None
    source: str | None = None
    _ck_email = field_validator("email")(_valid_email)


class NoteRequest(BaseModel):
    team_id: int
    note: str


class EuroWeekRequest(BaseModel):
    team_id: int
    gameweek: int
    competition: str
    tie_importance: int = Field(ge=1, le=3)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _require_admin(authorization: str = Header(default="")) -> None:
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    # Empty ADMIN_TOKEN => admin endpoints are closed, never open-by-misconfig.
    if not settings.admin_token or token != settings.admin_token:
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})


# --- 1.1 / draft brief -----------------------------------------------------

@router.post("/brief")
def post_brief(body: BriefRequest, request: Request, deps: Deps = Depends(get_deps)):
    if body.draft is not None:
        did = service.create_draft(
            deps, body.draft.player_ids, body.draft.captain_id,
            body.email)
        if body.email:  # draft-modal email also joins the free list
            _safe_signup(deps, body.email, None, "draft")
        return service.brief_from_draft(
            deps, did, before_generate=lambda: limiter.enforce(_client_ip(request)))

    if body.team_id is not None:
        if service.mode_of(deps) != "IN_SEASON":
            # pre-season: no live picks — route the user to the draft flow.
            raise HTTPException(status_code=409,
                                detail={"error": "no_picks_yet", "hint": "preseason_draft"})
        raise HTTPException(status_code=503,
                            detail={"error": "fpl_api_down"})  # in-season live path = Stage B

    raise HTTPException(status_code=422, detail={"error": "team_id_or_draft_required"})


@router.get("/brief/draft/{draft_id}")
def get_draft_brief(draft_id: uuid.UUID, deps: Deps = Depends(get_deps)):
    if not service.draft_exists(deps, draft_id):
        raise HTTPException(status_code=404, detail={"error": "invalid_draft_id"})
    return service.brief_from_draft(deps, draft_id)


@router.get("/brief/{team_id}/{gw}")
def get_team_brief(team_id: int, gw: int, token: str | None = None,
                   deps: Deps = Depends(get_deps)):
    # Stage A: no season briefs are generated and no tokens are minted, so this
    # only ever serves an already-persisted brief (free view). Token gating +
    # full-view unlock is Stage B (spec §1.2).
    brief = service.get_team_brief(deps, team_id, gw)
    if brief is None:
        raise HTTPException(status_code=404, detail={"error": "invalid_team_id"})
    return brief


# --- 1.3 preview -----------------------------------------------------------

@router.get("/preview/latest")
def preview_latest(response: Response, deps: Deps = Depends(get_deps)):
    response.headers["Cache-Control"] = "public, max-age=3600"
    return service.preview(deps)


@router.get("/preview/{gw}")
def preview_gw(gw: int, response: Response, deps: Deps = Depends(get_deps)):
    response.headers["Cache-Control"] = "public, max-age=3600"
    return service.preview(deps, gw)


# --- 1.4 players -----------------------------------------------------------

@router.get("/players")
def players(response: Response, deps: Deps = Depends(get_deps)):
    response.headers["Cache-Control"] = "public, max-age=3600"
    return service.players_list(deps)


# --- 1.5 signup ------------------------------------------------------------

def _safe_signup(deps: Deps, email: str, team_id: int | None, source: str | None) -> None:
    service.signup(deps, email, team_id, source)
    mail.send_welcome(email)  # non-fatal (no-op when unkeyed)


@router.post("/signup")
def signup(body: SignupRequest, deps: Deps = Depends(get_deps)):
    _safe_signup(deps, body.email, body.team_id, body.source)
    return {"ok": True}


# --- 1.9 health + admin ----------------------------------------------------

@router.get("/health")
def health(deps: Deps = Depends(get_deps)):
    return service.health(deps)


@router.post("/admin/notes")
def admin_notes(body: NoteRequest, _: None = Depends(_require_admin),
                deps: Deps = Depends(get_deps)):
    service.upsert_note(deps, body.team_id, body.note)
    return {"ok": True}


@router.post("/admin/euro-weeks")
def admin_euro_weeks(body: EuroWeekRequest, _: None = Depends(_require_admin),
                     deps: Deps = Depends(get_deps)):
    service.upsert_euro_week(deps, body.team_id, body.gameweek,
                             body.competition, body.tie_importance)
    return {"ok": True}


@router.post("/admin/sync")
def admin_sync(_: None = Depends(_require_admin), deps: Deps = Depends(get_deps)):
    """External cron entry point for the free-tier topology (no in-process scheduler).

    Runs the sync inline — it is slow (fetch + score ~700 players) by design: the
    caller is a GitHub Actions cron, not a browser. A data-layer failure surfaces
    as 503 so the cron job fails loudly rather than reporting a phantom success.
    """
    try:
        return service.run_sync(deps)
    except FPLError as e:
        raise HTTPException(status_code=503, detail={"error": e.code})
