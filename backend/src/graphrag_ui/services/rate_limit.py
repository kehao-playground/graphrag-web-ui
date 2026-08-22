"""Per-(user, project) sliding-window rate limiter for queries (spec §6.4).

In-memory single-pod design: a deque of monotonic hit timestamps per key,
purged of entries older than one hour on every check. Rejected requests do
not append — a client hammering a full window must not push its own hits
out of the window and unblock itself. Not thread-safe: query handlers run
on the event loop, so check() is effectively serialized.
"""

import time
from collections import deque
from functools import lru_cache

from graphrag_ui.config import get_settings

WINDOW_SECONDS = 3600.0

# Module-level clock so tests can drive the window deterministically.
_now = time.monotonic


class QueryRateLimitedError(RuntimeError):
    """(user, project) bucket exceeded QUERY_RATE_LIMIT_PER_HOUR in the last hour."""


class RateLimiter:
    def __init__(self, limit_per_hour: int) -> None:
        self.limit_per_hour = limit_per_hour
        self._hits: dict[tuple[str, str], deque[float]] = {}

    def check(self, user_id: str, project_id: str) -> None:
        """Count this request; raise QueryRateLimitedError when the bucket is full."""
        key = (str(user_id), str(project_id))
        now = _now()
        window = self._hits.setdefault(key, deque())
        cutoff = now - WINDOW_SECONDS
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= self.limit_per_hour:
            raise QueryRateLimitedError("查詢過於頻繁,請稍後再試")
        window.append(now)


@lru_cache
def get_rate_limiter() -> RateLimiter:
    """Module singleton sized from settings; reset via reset_rate_limiter."""
    return RateLimiter(get_settings().query_rate_limit_per_hour)


def reset_rate_limiter() -> None:
    """Test hygiene: drop the singleton (pair with get_settings.cache_clear)."""
    get_rate_limiter.cache_clear()
