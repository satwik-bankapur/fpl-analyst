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


limiter = RateLimiter(settings.brief_rate_limit, settings.brief_rate_window_s)
