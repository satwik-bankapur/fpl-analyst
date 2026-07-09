"""APScheduler wiring for `sync_fpl()` (spec §3 frequency table).

The cadence is driven by the next unfinished GW's deadline:

    | window                              | frequency        |
    |-------------------------------------|------------------|
    | Pre-season (no current GW)          | once daily       |
    | Baseline in-season                  | twice daily      |
    | Deadline−48h → deadline             | every 3 hours    |
    | Deadline+8h                         | one-off brief    |
    |                                     | pre-generation   |

Rather than run one fixed job, we run a lightweight *supervisor* every 15 minutes
that (a) always keeps the sync fresh at the current window's cadence and (b) fires
the deadline+8h pre-generation pass once per gameweek. The supervisor derives the
current window from the DB each tick, so the cadence self-adjusts as deadlines pass.

IMPORTANT (Layer 5 invariant): APScheduler runs IN-PROCESS on a single Railway
replica. A second replica would double every sync and every email. Do not scale out
without extracting the scheduler first.

The deadline+8h pre-generation batch itself (fetch picks → pipeline → brief → email)
is a Layer 3/4/5 concern; here we expose a hook (`pregeneration_hook`) that later
layers set. Layer 1 only guarantees the sync cadence and the trigger timing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from .db import SessionLocal
from .sync import next_gameweek, sync_fpl

log = logging.getLogger("fpl.scheduler")

# Window boundaries (spec §3).
_HEIGHTENED_BEFORE = timedelta(hours=48)   # deadline−48h → deadline: every 3h
_PREGEN_AFTER = timedelta(hours=8)          # deadline+8h: one-off pre-generation pass

# Minimum spacing between syncs per window.
_CADENCE_PRESEASON = timedelta(hours=24)
_CADENCE_BASELINE = timedelta(hours=12)
_CADENCE_HEIGHTENED = timedelta(hours=3)

_TICK_MINUTES = 15


class SyncSupervisor:
    """Decides, each tick, whether to sync now and whether to fire pre-generation."""

    def __init__(self, pregeneration_hook=None):
        # Layer 5 sets this to the brief pre-generation + send batch.
        self.pregeneration_hook = pregeneration_hook
        self._last_sync: datetime | None = None
        self._pregen_done_for_gw: int | None = None

    def _cadence(self, deadline: datetime | None, now: datetime) -> timedelta:
        if deadline is None:
            return _CADENCE_PRESEASON
        if deadline - _HEIGHTENED_BEFORE <= now <= deadline:
            return _CADENCE_HEIGHTENED
        return _CADENCE_BASELINE

    def tick(self) -> None:
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            ng = next_gameweek(session)
            deadline = ng.deadline if ng else None
            gw_id = ng.id if ng else None

        cadence = self._cadence(deadline, now)
        if self._last_sync is None or (now - self._last_sync) >= cadence:
            try:
                result = sync_fpl()
                self._last_sync = now
                log.info("supervisor sync: %s (cadence=%s)", result.mode, cadence)
            except Exception:  # a failed sync must not kill the scheduler
                log.exception("supervisor: sync_fpl failed")

        # Deadline+8h one-off pre-generation pass, once per gameweek.
        if (
            deadline is not None
            and gw_id is not None
            and now >= deadline + _PREGEN_AFTER
            and self._pregen_done_for_gw != gw_id
            and self.pregeneration_hook is not None
        ):
            try:
                self.pregeneration_hook(gw_id)
                self._pregen_done_for_gw = gw_id
                log.info("supervisor: ran pre-generation pass for gw=%s", gw_id)
            except Exception:
                log.exception("supervisor: pre-generation hook failed for gw=%s", gw_id)


def build_scheduler(pregeneration_hook=None) -> BackgroundScheduler:
    """Create (but do not start) the in-process scheduler."""
    supervisor = SyncSupervisor(pregeneration_hook=pregeneration_hook)
    scheduler = BackgroundScheduler(timezone=timezone.utc)
    scheduler.add_job(
        supervisor.tick,
        trigger="interval",
        minutes=_TICK_MINUTES,
        id="sync_supervisor",
        next_run_time=datetime.now(timezone.utc),  # run one tick immediately on start
        coalesce=True,
        max_instances=1,
    )
    return scheduler
