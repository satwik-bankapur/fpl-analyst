"""Rate limiting (§1.1) — in-memory windows plus the DB-backed counter.

TWO IMPLEMENTATIONS, ON PURPOSE, GUARDING DIFFERENT THINGS.

`RateLimiter` (in-memory) is what the spec endorsed at this scale. The 2026-08-01
audit found it does not survive contact with the launch topology: Render free
sleeps after ~15 min idle, so **every counter resets on every wake**, and the
counter is per-process, which stops being single even during a rolling deploy. It
is real protection against a naive script and none at all against a patient one.
See docs/AUDIT.md §C2. It is kept for the cheap, high-frequency checks where a
reset costs nothing (admin auth failures, raw public POSTs).

`DbRateLimiter` is the one that guards money. Stage B puts LLM spend and paid
delivery behind brief generation, so that counter has to survive a sleep. Fixed
window in `rate_hits`, keyed by an arbitrary bucket string:

  - `ip:<addr>`  — per-caller generation budget
  - `global`     — the whole-service circuit breaker (audit §C2 option A). The
                   per-IP limit stops one abuser; the global cap caps the bill
                   when the per-IP limit has a bug.

Only actual generations are recorded; cache hits never call `enforce`, so they
neither count nor get blocked (the "cache hits don't count" rule is unchanged).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from fpl.models import RateHit

from .config import settings

log = logging.getLogger("api.ratelimit")


class RateLimited(Exception):
    """Raised by enforce() when an IP has hit its generation cap this window."""

    def __init__(self, retry_after_s: int):
        self.retry_after_s = retry_after_s
        super().__init__("brief generation rate limit exceeded")


class RateLimiter:
    def __init__(self, limit: int, window_s: int):
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def enforce(self, ip: str, *, now: float | None = None) -> None:
        """Record one generation for `ip`, or raise RateLimited if it's over the cap."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[ip]
            cutoff = now - self.window_s
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                retry = int(hits[0] + self.window_s - now) + 1
                raise RateLimited(retry_after_s=max(retry, 1))
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class DbRateLimiter:
    """Fixed-window counter in `rate_hits`. Survives sleep, restart and replicas.

    Fixed window rather than sliding because the storage cost of a sliding window
    is one row per hit, and this table is written on the hot path of the thing it
    is protecting. The cost is that a caller can burst across a window boundary and
    briefly get 2x the limit — an acceptable overshoot for an abuse ceiling.

    # ponytail: fixed window, one row per (bucket, window). If precise smoothing
    # ever matters more than write volume, move to a sliding log.
    """

    def __init__(self, limit: int, window_s: int):
        self.limit = limit
        self.window_s = window_s

    def _window_start(self, now: datetime) -> datetime:
        epoch = int(now.timestamp())
        return datetime.fromtimestamp(epoch - (epoch % self.window_s), tz=timezone.utc)

    def enforce(self, session_factory, bucket: str, *,
                now: datetime | None = None) -> None:
        """Record one hit for `bucket`, or raise RateLimited if it is over the cap.

        Owns its own short transaction so the counter is durable even if the work
        it guards later fails — a generation that crashed still consumed capacity,
        and not charging for it is a free retry loop.
        """
        now = now or datetime.now(timezone.utc)
        window = self._window_start(now)
        hits = self._bump(session_factory, bucket, window)
        if hits > self.limit:
            retry = int((window + timedelta(seconds=self.window_s) - now).total_seconds())
            raise RateLimited(retry_after_s=max(retry, 1))

    def _bump(self, session_factory, bucket: str, window: datetime) -> int:
        for attempt in (1, 2):
            try:
                with session_factory() as session, session.begin():
                    row = session.get(RateHit, (bucket, window))
                    if row is None:
                        session.add(RateHit(bucket=bucket, window_start=window, hits=1))
                        return 1
                    row.hits += 1
                    return row.hits
            except IntegrityError:
                # Two callers inserted the same fresh window concurrently. The row
                # exists now, so the retry takes the UPDATE branch.
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    def peek(self, session_factory, bucket: str, *,
             now: datetime | None = None) -> int:
        """Hits so far in the current window, without recording one."""
        window = self._window_start(now or datetime.now(timezone.utc))
        with session_factory() as session:
            row = session.get(RateHit, (bucket, window))
            return row.hits if row else 0

    def purge_before(self, session_factory, cutoff: datetime) -> int:
        """Drop windows older than `cutoff`. Called from the sync handler.

        Without this the table grows one row per bucket per window forever — the
        same unbounded-growth finding the audit raised about `draft_squads`, and it
        would be embarrassing to reintroduce it in the fix for it.
        """
        from sqlalchemy import delete

        with session_factory() as session, session.begin():
            result = session.execute(
                delete(RateHit).where(RateHit.window_start < cutoff))
            return result.rowcount or 0


# Brief GENERATIONS. Fired from `before_generate`, so cache hits never touch it.
# In-memory copy kept as the fast first line; `db_limiter` is the durable one.
limiter = RateLimiter(settings.brief_rate_limit, settings.brief_rate_window_s)

# The durable per-IP generation budget (audit §C2 option B).
db_limiter = DbRateLimiter(settings.brief_rate_limit, settings.brief_rate_window_s)

# The whole-service circuit breaker (audit §C2 option A). Deliberately a separate
# instance with its own window so the two can be tuned independently.
global_limiter = DbRateLimiter(settings.global_generation_limit,
                               settings.global_generation_window_s)

GLOBAL_BUCKET = "global"

# Public POST REQUESTS, fired before any DB write. `limiter` cannot bound row
# growth or mail volume because it only sees generations: POST /api/brief writes a
# `draft_squads` row *before* the cache is consulted, and POST /api/signup writes a
# row and sends a welcome email without ever reaching the graph.
request_limiter = RateLimiter(settings.request_rate_limit, settings.request_rate_window_s)

# Failed admin auth attempts. Recorded only on rejection.
admin_limiter = RateLimiter(settings.admin_fail_limit, settings.admin_fail_window_s)
