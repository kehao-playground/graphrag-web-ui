"""Per-(user, project) sliding-window rate limiter for queries (spec §6.4).

In-memory single-pod design over domain.SlidingWindow: hit timestamps per
key, purged of entries older than one hour on every check. Rejected requests
do not append — a client hammering a full window must not push its own hits
out of the window and unblock itself. Not thread-safe: query handlers run on
the event loop, so check() is effectively serialized.

The key space is bounded by real (user, project) pairs rather than by
anything a client makes up, so the ceiling below is a backstop, not the
primary defence — but an unbounded map is still an unbounded map.
"""

import time
from functools import lru_cache

from graphrag_ui.config import get_settings
from graphrag_ui.domain.sliding_window import SlidingWindow

WINDOW_SECONDS = 3600.0

# Every live user times every project they may query; 50k is far above any
# plausible single-pod deployment and still a hard ceiling.
MAX_TRACKED_KEYS = 50_000

# Module-level clock so tests can drive the window deterministically.
_now = time.monotonic


class QueryRateLimitedError(RuntimeError):
    """(user, project) bucket exceeded QUERY_RATE_LIMIT_PER_HOUR in the last hour."""


class RateLimiter:
    def __init__(self, limit_per_hour: int) -> None:
        self.limit_per_hour = limit_per_hour
        self._window = SlidingWindow(window_seconds=WINDOW_SECONDS, max_keys=MAX_TRACKED_KEYS)

    def check(self, user_id: str, project_id: str) -> None:
        """Count this request; raise QueryRateLimitedError when the bucket is full."""
        key = (str(user_id), str(project_id))
        now = _now()
        if self._window.count(key, now) >= self.limit_per_hour:
            raise QueryRateLimitedError("too many queries — please retry later")
        self._window.add(key, now)


@lru_cache
def get_rate_limiter() -> RateLimiter:
    """Module singleton sized from settings; reset via reset_rate_limiter."""
    return RateLimiter(get_settings().query_rate_limit_per_hour)


def reset_rate_limiter() -> None:
    """Test hygiene: drop the singleton (pair with get_settings.cache_clear)."""
    get_rate_limiter.cache_clear()
