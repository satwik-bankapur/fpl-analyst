"""Per-IP brief-generation rate limit (§1.1).

In-memory sliding window. Spec-endorsed at this scale: the single-replica
invariant (§5) means one process holds the whole counter — no Redis needed. Only
actual generations are recorded; cache hits never call `enforce`, so they neither
count nor get blocked (the "cache hits don't count" rule).

# ponytail: in-memory per-IP dict; if the scheduler is ever extracted to multiple
# replicas this must move to a shared store (Redis token bucket).
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from .config import settings


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


# Brief GENERATIONS. Fired from `before_generate`, so cache hits never touch it.
limiter = RateLimiter(settings.brief_rate_limit, settings.brief_rate_window_s)

# Public POST REQUESTS, fired before any DB write. `limiter` cannot bound row
# growth or mail volume because it only sees generations: POST /api/brief writes a
# `draft_squads` row *before* the cache is consulted, and POST /api/signup writes a
# row and sends a welcome email without ever reaching the graph.
request_limiter = RateLimiter(settings.request_rate_limit, settings.request_rate_window_s)

# Failed admin auth attempts. Recorded only on rejection.
admin_limiter = RateLimiter(settings.admin_fail_limit, settings.admin_fail_window_s)
