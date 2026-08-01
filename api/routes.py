"""HTTP routes (spec §1). Thin: validate, call `service`, shape errors.

Stage A surface only: brief (draft path), preview, players, signup, health, admin.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from fpl.errors import FPLError
from graph.deps import Deps

from . import admin, billing, inspect, mail, send, service, tokens
from .config import settings
from .ratelimit import (
    GLOBAL_BUCKET, admin_limiter, db_limiter, global_limiter, limiter,
    request_limiter,
)
from .service import get_deps

router = APIRouter(prefix="/api")
log = logging.getLogger("api.routes")

# Upper bound for every externally-supplied id (player, FPL team, draft-squad
# member). Python ints are unbounded and pydantic will happily accept 10**400 for
# an `int` field; that value then reaches a Postgres `integer` column and raises a
# DataError, i.e. a 500 on attacker-chosen input. FPL ids live in the low
# millions, so anything past this is not a typo, it is a probe.
MAX_ID = 99_999_999
MAX_GAMEWEEK = 38

PlayerId = Annotated[int, Field(ge=1, le=MAX_ID)]
TeamId = Annotated[int, Field(ge=1, le=MAX_ID)]
GameweekNo = Annotated[int, Field(ge=1, le=MAX_GAMEWEEK)]


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
#
# EVERY field carries a bound. These models are the trust boundary for an
# unauthenticated surface, so an absent bound is not "no opinion", it is an
# accepted value — and pydantic's bare `int`/`str` accept arbitrary magnitude and
# arbitrary length.

class Draft(BaseModel):
    # The exact-15 rule is enforced in `service.validate_draft`, not here, so the
    # caller gets a 400 with a specific `squad_size` code alongside every other
    # squad-rule rejection — rather than one lone 422 from pydantic. max_length
    # stays as a payload bound so a million-id body is refused before any DB work.
    player_ids: list[PlayerId] = Field(min_length=1, max_length=100)
    captain_id: PlayerId | None = None


class BriefRequest(BaseModel):
    team_id: TeamId | None = None
    draft: Draft | None = None
    email: str | None = None
    _ck_email = field_validator("email")(_valid_email)


class SignupRequest(BaseModel):
    email: str
    team_id: TeamId | None = None
    # Acquisition tag, written by our own frontend. Bounded because it is a
    # client-supplied string that lands in a Text column on a public route.
    source: str | None = Field(default=None, max_length=64)
    _ck_email = field_validator("email")(_valid_email)


class NoteRequest(BaseModel):
    team_id: TeamId
    note: str = Field(max_length=4000)


class EuroWeekRequest(BaseModel):
    team_id: TeamId
    gameweek: GameweekNo
    competition: str = Field(max_length=16)
    tie_importance: int = Field(ge=1, le=3)


class CheckoutRequest(BaseModel):
    """§1.6. Note what is ABSENT: no price, no amount, no currency, no price id.

    The client names a PLAN and the server resolves what that costs
    (`billing.price_id_for_plan`). There is no field here a caller could tamper
    with to change what they pay — that is audit §D3's requirement, expressed as a
    schema rather than as a validation rule that someone can forget to run.
    """

    email: str
    team_id: TeamId | None = None
    plan: Literal["monthly", "season"]
    _ck_email = field_validator("email")(_valid_email)


class EuroWeekReplace(BaseModel):
    # The whole grid in one submission. Bounded so a malformed client can't hand
    # the DB an unbounded rewrite; 20 clubs x 38 GWs is the real ceiling.
    rows: list[EuroWeekRequest] = Field(max_length=760)


def _client_ip(request: Request) -> str:
    """The client address the rate limiters key on.

    RIGHTMOST entry of X-Forwarded-For, not leftmost. A reverse proxy APPENDS the
    peer it actually saw, so the last element is the one Render observed and the
    only one the caller cannot forge. Reading the leftmost element — which is
    whatever arbitrary string the client chose to send — made every per-IP limit
    on this surface bypassable by rotating one header value per request.

    # ponytail: assumes exactly one trusted proxy hop (Render's edge). Add a
    # trusted-hop count if a CDN is ever put in front of it.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _require_admin(request: Request, authorization: str = Header(default="")) -> None:
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    # compare_digest, not `!=`: `!=` on str short-circuits at the first differing
    # byte, so its runtime leaks a prefix-match length. Both operands are ASCII by
    # construction (an HTTP header value and an env var), which compare_digest
    # requires.
    ok = bool(settings.admin_token) and secrets.compare_digest(
        token, settings.admin_token)
    if ok:
        return
    # Empty ADMIN_TOKEN => admin endpoints are closed, never open-by-misconfig.
    # Record the failure (successes are never recorded, so the operator's own
    # traffic can never lock them out) and let RateLimited surface as 429.
    admin_limiter.enforce(_client_ip(request))
    raise HTTPException(status_code=401, detail={"error": "unauthorized"})


def _enforce_request_budget(request: Request) -> None:
    """Per-IP cap on public POSTs, applied BEFORE anything is written or sent."""
    request_limiter.enforce(_client_ip(request))


def _enforce_generation_budget(request: Request, deps: Deps) -> None:
    """The DURABLE generation cap. Fires only on a real cache miss.

    Three layers, cheapest first, and the order matters:

    1. `limiter` — in-process, free, catches the obvious burst without a query.
    2. `db_limiter` — per-IP in `rate_hits`, survives the Render sleep that resets
       (1). This is the one that actually holds in production (audit §C2).
    3. `global_limiter` — whole-service circuit breaker. (2) stops one abuser;
       this caps the bill when (2) has a bug. Checked LAST so a caller who is
       already over their own limit does not consume global budget on the way to
       being refused.

    Stage B is why (2) and (3) exist at all: this counter now guards LLM spend and
    paid delivery, not just CPU.
    """
    ip = _client_ip(request)
    limiter.enforce(ip)
    db_limiter.enforce(deps.session_factory, f"ip:{ip}")
    global_limiter.enforce(deps.session_factory, GLOBAL_BUCKET)


# --- 1.1 / draft brief -----------------------------------------------------

@router.post("/brief")
def post_brief(body: BriefRequest, request: Request, deps: Deps = Depends(get_deps)):
    # Budget check first: `limiter` below only counts GENERATIONS, and the draft
    # row is written before the cache is consulted — so without this, replaying one
    # valid squad body grows `draft_squads` without bound and never trips a limit.
    _enforce_request_budget(request)
    if body.draft is not None:
        # Validate BEFORE persisting: an illegal squad must not leave a draft row
        # (or an email signup) behind for a brief that will never be generated.
        try:
            service.validate_draft(deps, body.draft.player_ids, body.draft.captain_id)
        except service.InvalidDraft as e:
            raise HTTPException(status_code=400,
                                detail={"error": e.code, "message": e.message})
        did = service.create_draft(
            deps, body.draft.player_ids, body.draft.captain_id,
            body.email)
        if body.email:  # draft-modal email also joins the free list
            _safe_signup(deps, body.email, None, "draft")
        return service.brief_from_draft(
            deps, did,
            before_generate=lambda: _enforce_generation_budget(request, deps))

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
def get_team_brief(team_id: int = Path(ge=1, le=MAX_ID),
                   gw: int = Path(ge=0, le=MAX_GAMEWEEK),
                   token: str | None = None,
                   deps: Deps = Depends(get_deps)):
    """§1.2. No/invalid token -> free view. Valid token + live entitlement -> full.

    `?token=` is BACK (Stage B, 2026-08-01). It was deliberately absent through
    Stage A — nothing minted or verified a JWT, and a signature-shaped parameter
    that silently does nothing reads as implemented auth to the next person. It
    returns now together with `BRIEF_TOKEN_SECRET`, `api/tokens.py` and the send
    job that mints it.

    An invalid token is NOT an error: the contract is "degrade to free", and a 401
    here would tell a forger exactly which of their attempts was closer.
    """
    brief = service.get_team_brief(deps, team_id, gw, token)
    if brief is None:
        raise HTTPException(status_code=404, detail={"error": "invalid_team_id"})
    return brief


# --- 1.3 preview -----------------------------------------------------------

@router.get("/preview/latest")
def preview_latest(response: Response, deps: Deps = Depends(get_deps)):
    response.headers["Cache-Control"] = "public, max-age=3600"
    return service.preview(deps)


@router.get("/preview/{gw}")
def preview_gw(response: Response, gw: int = Path(ge=1, le=MAX_GAMEWEEK),
               deps: Deps = Depends(get_deps)):
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
def signup(body: SignupRequest, request: Request, deps: Deps = Depends(get_deps)):
    # This route SENDS AN EMAIL to an address the caller chose and did not prove
    # they own. Unlimited, that is a mail bomb pointed at a third party and a
    # bounce-rate problem pointed at our sending domain. The cap is the floor, not
    # the fix — double opt-in is the fix (docs/AUDIT.md, Stage B requirements).
    _enforce_request_budget(request)
    _safe_signup(deps, body.email, body.team_id, body.source)
    return {"ok": True}


# --- 1.6 checkout ----------------------------------------------------------

@router.post("/checkout")
def checkout(body: CheckoutRequest, request: Request, deps: Deps = Depends(get_deps)):
    """Create a Paddle checkout for a PLAN NAME. The server owns the price.

    Nothing is granted here. The checkout-success redirect is a UI event and is
    fully forgeable, so entitlement comes only from the verified webhook
    (audit §D3) — this route's entire job is to hand back a URL.
    """
    _enforce_request_budget(request)
    # The address goes on the free list now, so an abandoned checkout still leaves
    # a lead and still gets the weekly preview.
    _safe_signup(deps, body.email, body.team_id, "checkout")
    try:
        url = billing.create_checkout(body.plan, body.email, body.team_id)
    except billing.CheckoutError as e:
        # 503, not 400: every failure mode here is OUR misconfiguration or the
        # provider's, never the caller's — `plan` was already validated by the
        # Literal on the model.
        log.warning("checkout failed: %s (%s)", e.code, e.message)
        raise HTTPException(status_code=503, detail={"error": e.code})
    return {"checkout_url": url}


# --- 1.7 the Paddle webhook ------------------------------------------------

@router.post("/webhooks/paddle")
async def paddle_webhook(request: Request, deps: Deps = Depends(get_deps)):
    """§4 / audit §D1. Verify, then parse, then apply once.

    NOTE THE SIGNATURE OF THIS FUNCTION: it takes `Request`, not a pydantic model.
    That is the whole verify-before-parse requirement expressed in code — declaring
    a body model would have FastAPI deserialize attacker-controlled JSON before
    this function ever runs, which is exactly what §D1.1 forbids. Read the raw
    bytes, prove they came from Paddle, and only then call `json.loads`.
    """
    raw = await request.body()
    try:
        billing.verify_signature(raw, request.headers.get("paddle-signature"))
        payload = billing.parse_verified(raw)
    except billing.SignatureError as e:
        # 401 with NO detail. The reason goes to our log; telling an attacker
        # whether they got the timestamp or the HMAC wrong is free help.
        log.warning("paddle webhook rejected: %s", e)
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})

    event_id = str(payload.get("event_id") or "")
    with deps.session_factory() as session:
        if event_id and billing.already_processed(session, event_id):
            # Paddle redelivers on any non-2xx, so a duplicate must be a cheerful
            # 200 — a 4xx here would make it retry forever.
            return {"ok": True, "action": "duplicate", "duplicate": True}

    try:
        with deps.session_factory() as session, session.begin():
            handled = billing.handle_event(session, payload)
    except IntegrityError:
        # The race the pre-check above cannot cover: a concurrent redelivery got
        # there between our SELECT and our INSERT. The PK did its job.
        log.info("paddle webhook: concurrent duplicate event_id=%s", event_id)
        return {"ok": True, "action": "duplicate", "duplicate": True}
    except billing.SignatureError as e:
        log.warning("paddle webhook: verified payload was unusable: %s", e)
        raise HTTPException(status_code=400, detail={"error": "bad_payload"})

    return handled.as_dict()


# --- 1.8 unsubscribe + delete ----------------------------------------------
#
# Both live on the API rather than the frontend because the one-click List-
# Unsubscribe header must point at something that acts on a POST with no session,
# no JavaScript and no cookie.

@router.get("/email/unsubscribe")
def email_unsubscribe(e: str = "", t: str = "", deps: Deps = Depends(get_deps)):
    """One-click unsubscribe from a mailed link (GET, per RFC 8058 the landing).

    The token is a keyed HMAC of the address, so the link is unguessable and needs
    no session — and it never expires, because mail sits in inboxes for years and
    an expired unsubscribe link is, for deliverability purposes, no link at all.
    """
    if not e or not t or not tokens.unsubscribe_token_valid(e, t):
        raise HTTPException(status_code=400, detail={"error": "invalid_token"})
    service.unsubscribe(deps, e)
    # Identical response whether or not the address was on the list — this endpoint
    # must not become an oracle for "is this person a customer".
    return {"ok": True, "email": e, "status": "unsubscribed",
            "note": "Billing access, if any, is unaffected."}


@router.post("/email/unsubscribe")
def email_unsubscribe_post(e: str = "", t: str = "", deps: Deps = Depends(get_deps)):
    """The `List-Unsubscribe-Post: List-Unsubscribe=One-Click` target (RFC 8058).

    Gmail and Yahoo POST here directly, without a human. Same effect as the GET.
    """
    return email_unsubscribe(e=e, t=t, deps=deps)


@router.post("/email/resubscribe")
def email_resubscribe(e: str = "", t: str = "", deps: Deps = Depends(get_deps)):
    """"Actually, keep sending" — the undo on the unsubscribe landing page."""
    if not e or not t or not tokens.unsubscribe_token_valid(e, t):
        raise HTTPException(status_code=400, detail={"error": "invalid_token"})
    service.resubscribe(deps, e)
    return {"ok": True, "email": e, "status": "subscribed"}


@router.post("/email/delete")
def email_delete(e: str = "", t: str = "", deps: Deps = Depends(get_deps)):
    """Delete my data: removes the mailing-list row outright.

    A DIFFERENT action from unsubscribe, deliberately — see `service.delete_signup`.
    Subscription rows are anonymised rather than deleted: they are the record of a
    financial transaction, which we are required to keep.
    """
    if not e or not t or not tokens.unsubscribe_token_valid(e, t):
        raise HTTPException(status_code=400, detail={"error": "invalid_token"})
    result = service.delete_signup(deps, e)
    return {"ok": True, **result}


# --- 1.9 health + admin ----------------------------------------------------

@router.get("/health")
def health(deps: Deps = Depends(get_deps)):
    return service.health(deps)


@router.post("/admin/notes")
def admin_notes(body: NoteRequest, _: None = Depends(_require_admin),
                deps: Deps = Depends(get_deps)):
    # `changed` is false when the note was already identical — the write was
    # skipped so the brief cache key stays put (service.upsert_note).
    return {"ok": True, "changed": service.upsert_note(deps, body.team_id, body.note)}


@router.post("/admin/euro-weeks")
def admin_euro_weeks(body: EuroWeekRequest, _: None = Depends(_require_admin),
                     deps: Deps = Depends(get_deps)):
    changed = service.upsert_euro_week(deps, body.team_id, body.gameweek,
                                       body.competition, body.tie_importance)
    return {"ok": True, "changed": changed}


@router.post("/admin/euro-weeks/replace")
def admin_euro_weeks_replace(body: EuroWeekReplace, _: None = Depends(_require_admin),
                             deps: Deps = Depends(get_deps)):
    """Whole-grid save for the admin editor: upsert what's sent, delete what isn't.

    POST rather than PUT deliberately — `api.main` allows only GET and POST
    cross-origin, and a verb that needs a CORS change is a worse trade than a
    slightly unRESTful path.
    """
    rows = [r.model_dump() for r in body.rows]
    if len({(r["team_id"], r["gameweek"]) for r in rows}) != len(rows):
        # Two rows for the same club+GW: one would silently overwrite the other.
        raise HTTPException(status_code=400,
                            detail={"error": "duplicate_team_gameweek"})
    return {"ok": True, "changed": service.replace_euro_weeks(deps, rows)}


# --- admin read surface (the /admin console) --------------------------------
#
# Read-only, token-gated, and never linked from a public page. Queries live in
# `api.admin`; these handlers only shape HTTP.

@router.get("/admin/overview")
def admin_overview(_: None = Depends(_require_admin), deps: Deps = Depends(get_deps)):
    return admin.overview(deps)


@router.get("/admin/notes")
def admin_notes_list(_: None = Depends(_require_admin), deps: Deps = Depends(get_deps)):
    return admin.notes(deps)


@router.get("/admin/euro-weeks")
def admin_euro_weeks_list(_: None = Depends(_require_admin),
                          deps: Deps = Depends(get_deps)):
    return admin.euro_weeks(deps)


@router.get("/admin/players")
def admin_players(q: str = "", flagged: bool = False,
                  _: None = Depends(_require_admin), deps: Deps = Depends(get_deps)):
    return admin.players(deps, q=q, flagged=flagged)


@router.get("/admin/fixtures/{team_short}")
def admin_fixtures(team_short: str = Path(max_length=8), n: int = 6,
                   _: None = Depends(_require_admin), deps: Deps = Depends(get_deps)):
    got = admin.team_fixtures(deps, team_short, max(1, min(n, 38)))
    if got is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_team"})
    return got


@router.get("/admin/top")
def admin_top(n: int = 20, _: None = Depends(_require_admin),
              deps: Deps = Depends(get_deps)):
    return admin.top(deps, max(1, min(n, 200)))


@router.get("/admin/flag-queue")
def admin_flag_queue(_: None = Depends(_require_admin), deps: Deps = Depends(get_deps)):
    return admin.flag_queue(deps)


# --- the reasoning inspector (/admin/inspect) -------------------------------
#
# Read-only and zero-spend: `api.inspect` runs the deterministic graph in trace
# mode and serializes the Layer 4 payload WITHOUT calling any model. Nothing here
# writes a row — not even a `briefs` cache entry (see api/inspect.py).

@router.get("/admin/inspect/briefs")
def admin_inspect_briefs(n: int = 25, _: None = Depends(_require_admin),
                         deps: Deps = Depends(get_deps)):
    return inspect.recent_briefs(deps, max(1, min(n, 100)))


@router.get("/admin/inspect/player/{player_id}")
def admin_inspect_player(player_id: int = Path(ge=1, le=MAX_ID),
                         _: None = Depends(_require_admin),
                         deps: Deps = Depends(get_deps)):
    got = inspect.player_components(deps, player_id)
    if got is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_player"})
    return got


@router.get("/admin/inspect/draft/{draft_id}")
def admin_inspect_draft(draft_id: uuid.UUID, _: None = Depends(_require_admin),
                        deps: Deps = Depends(get_deps)):
    got = inspect.inspect_draft(deps, draft_id)
    if got is None:
        raise HTTPException(status_code=404, detail={"error": "invalid_draft_id"})
    return got


@router.get("/admin/inspect/team/{team_id}/{gw}")
def admin_inspect_team(team_id: int = Path(ge=1, le=MAX_ID),
                       gw: int = Path(ge=0, le=MAX_GAMEWEEK),
                       _: None = Depends(_require_admin),
                       deps: Deps = Depends(get_deps)):
    got = inspect.inspect_team(deps, team_id, gw)
    if got is None:
        raise HTTPException(status_code=404, detail={"error": "no_persisted_brief"})
    return got


@router.post("/admin/sync")
def admin_sync(_: None = Depends(_require_admin), deps: Deps = Depends(get_deps)):
    """External cron entry point for the free-tier topology (no in-process scheduler).

    Runs the sync inline — it is slow (fetch + score ~700 players) by design: the
    caller is a GitHub Actions cron, not a browser. A data-layer failure surfaces
    as 503 so the cron job fails loudly rather than reporting a phantom success.
    """
    try:
        result = service.run_sync(deps)
    except FPLError as e:
        raise HTTPException(status_code=503, detail={"error": e.code})
    # Retention sweep for the limiter's own table, on the one route that already
    # runs on a schedule. Without it `rate_hits` grows one row per bucket per
    # window forever — reintroducing, inside the fix for unbounded growth, exactly
    # the unbounded growth the audit flagged (§H5).
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    result["rate_hits_purged"] = global_limiter.purge_before(deps.session_factory, cutoff)
    return result


@router.post("/admin/preview")
def admin_preview(gw: int | None = None, _: None = Depends(_require_admin),
                  deps: Deps = Depends(get_deps)):
    """Generate + persist THE generic weekly preview (Layer 4 §6).

    The send job's free template reads this persisted row and has no other content
    source, so this runs before a batch. Singleton per gameweek — a rerun refreshes
    rather than duplicating.
    """
    return service.persist_generic_preview(deps, gw)


@router.post("/admin/send")
def admin_send(gw: int, retry: bool = False, _: None = Depends(_require_admin),
               deps: Deps = Depends(get_deps)):
    """Run the weekly send batch for `gw` (spec §3), or its one retry pass.

    Behind ADMIN_TOKEN and driven by the same external cron as sync, for the same
    reason: a free Render service cannot hold a scheduler. Idempotent — `send_log`
    makes a rerun a no-op for anyone already mailed, so a cron that fires twice
    costs nothing.
    """
    if retry:
        return send.retry_failed(deps, gw).as_dict()
    return send.run_batch(deps, gw).as_dict()


@router.get("/admin/subscriptions")
def admin_subscriptions(_: None = Depends(_require_admin),
                        deps: Deps = Depends(get_deps)):
    """Billing state as the gate actually sees it — `entitled` is `is_entitled`.

    Shows the verdict next to the raw status so an operator can see WHY someone is
    or is not being served, rather than inferring it from a status string whose
    meaning lives in another file.
    """
    return admin.subscriptions(deps)
