"""Bounded sliding-window hit counter (pure; no I/O, no external imports).

Shared by the login-failure limiter (api/auth_routes.py) and the query
limiter (services/rate_limit.py). Both previously kept a bare dict that grew
forever: a key's deque was purged of stale timestamps on read, but the key
itself was never deleted, and both key spaces are caller-supplied — the login
key is (client ip, email), so a flood of failed logins with fresh emails, or
with spoofed X-Forwarded-For values, grew the process's memory with nothing
to reclaim it.

Two guarantees, in that order:

* a key whose hits have all aged out is deleted, not left as an empty deque;
* the number of live keys never exceeds ``max_keys`` — when a sweep of
  expired keys cannot get under the ceiling (a genuine flood of distinct
  keys inside one window), the least recently hit keys are evicted.

Eviction is deliberately least-recently-hit rather than random or oldest-
inserted: evicting the *newest* key would let an attacker clear their own
bucket by flooding other keys, which is the limiter failing open.

Timestamps are plain floats and always supplied by the caller, so callers
pick their own clock (both use a monotonic one) and tests can drive the
window deterministically.
"""

from collections import OrderedDict, deque
from collections.abc import Hashable


class SlidingWindow:
    def __init__(self, *, window_seconds: float, max_keys: int) -> None:
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        # Insertion-ordered by *last hit*: add() moves a key to the end, so
        # the front is always the least recently hit — the eviction order.
        self._hits: OrderedDict[Hashable, deque[float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._hits)

    def count(self, key: Hashable, now: float) -> int:
        """Live hits for `key`, after dropping the ones that aged out.

        Never creates a bucket: an unknown key stays unknown, so a bare
        membership check cannot be used to grow the map.
        """
        window = self._hits.get(key)
        if window is None:
            return 0
        self._purge(window, now)
        if not window:
            del self._hits[key]
            return 0
        return len(window)

    def add(self, key: Hashable, now: float) -> None:
        """Record one hit, enforcing the ceiling."""
        window = self._hits.get(key)
        if window is None:
            window = deque()
            self._hits[key] = window
        self._purge(window, now)
        window.append(now)
        self._hits.move_to_end(key)
        if len(self._hits) > self.max_keys:
            self._evict(now)

    def clear(self) -> None:
        self._hits.clear()

    def _purge(self, window: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while window and window[0] <= cutoff:
            window.popleft()

    def _evict(self, now: float) -> None:
        """Sweep expired keys first; evict least-recently-hit if still over.

        The sweep is O(live keys) but only runs when the ceiling is crossed,
        so the per-hit cost stays amortized constant.
        """
        for key in [
            k for k, w in self._hits.items() if not w or w[-1] <= now - self.window_seconds
        ]:
            del self._hits[key]
        while len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)
