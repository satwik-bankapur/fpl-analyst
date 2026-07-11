"""FastAPI app: CORS, error mapping, and the single-process scheduler (spec §5).

SINGLE-REPLICA INVARIANT (hard requirement): APScheduler runs in-process here. A
second replica = duplicate syncs and duplicate emails to paying customers. Railway
must pin numReplicas=1 (see railway.toml); if scale ever needs replicas, extract
the scheduler into its own service first. Startup is gated on RUN_SCHEDULER so tests
and local demos never spawn it.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .ratelimit import RateLimited
from .routes import router
from .service import BriefError

log = logging.getLogger("api")

# graph PipelineError codes -> HTTP status (spec §1.1). invalid_draft_id is ours.
_ERROR_STATUS = {
    "invalid_team_id": 404,
    "invalid_draft_id": 404,
    "no_picks_yet": 409,
    "fpl_api_down": 503,
    "empty_ep_table": 503,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    if settings.run_scheduler:
        from fpl.scheduler import build_scheduler  # in-process, single replica only
        scheduler = build_scheduler()
        scheduler.start()
        log.info("in-process sync scheduler started (single replica)")
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


app = FastAPI(title="FPL Analyst API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(BriefError)
async def _brief_error(_: Request, exc: BriefError):
    status = _ERROR_STATUS.get(exc.code, 500)
    body = {"error": exc.code}
    if exc.code == "no_picks_yet":
        body["hint"] = "preseason_draft"
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(RateLimited)
async def _rate_limited(_: Request, exc: RateLimited):
    return JSONResponse(
        status_code=429,
        content={"error": "rate_limited"},
        headers={"Retry-After": str(exc.retry_after_s)},
    )


app.include_router(router)
