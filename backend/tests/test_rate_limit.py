"""Rate limiter (Task 3): sliding 1h window per (user, project) key —
window purge, rejected hits don't consume slots, per-key isolation,
settings-driven singleton with reset."""

import pytest

from graphrag_ui.config import get_settings
from graphrag_ui.services import rate_limit
from graphrag_ui.services.rate_limit import (
    QueryRateLimitedError,
    RateLimiter,
    get_rate_limiter,
    reset_rate_limiter,
)


class FakeClock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(rate_limit, "_now", fake)
    return fake


def test_allows_up_to_limit_then_raises(clock):
    limiter = RateLimiter(2)
    limiter.check("u1", "p1")
    limiter.check("u1", "p1")
    with pytest.raises(QueryRateLimitedError):
        limiter.check("u1", "p1")


def test_window_purges_hits_older_than_one_hour(clock):
    limiter = RateLimiter(2)
    limiter.check("u1", "p1")
    limiter.check("u1", "p1")
    with pytest.raises(QueryRateLimitedError):
        limiter.check("u1", "p1")
    clock.now += 3601  # both hits fell out of the sliding window
    limiter.check("u1", "p1")
    limiter.check("u1", "p1")  # two fresh slots were available again
    with pytest.raises(QueryRateLimitedError):
        limiter.check("u1", "p1")


def test_rejected_request_does_not_consume_or_extend_window(clock):
    limiter = RateLimiter(1)
    limiter.check("u1", "p1")
    with pytest.raises(QueryRateLimitedError):
        limiter.check("u1", "p1")  # rejected — must NOT append a hit
    with pytest.raises(QueryRateLimitedError):
        limiter.check("u1", "p1")  # window still holds exactly one hit
    clock.now += 3600  # the single hit ages out
    limiter.check("u1", "p1")


def test_per_key_isolation():
    limiter = RateLimiter(1)
    limiter.check("u1", "p1")
    limiter.check("u1", "p2")  # same user, other project
    limiter.check("u2", "p1")  # other user, same project
    with pytest.raises(QueryRateLimitedError):
        limiter.check("u1", "p1")


def test_singleton_reads_settings_and_reset_rebuilds(monkeypatch):
    try:
        monkeypatch.setenv("QUERY_RATE_LIMIT_PER_HOUR", "7")
        get_settings.cache_clear()
        reset_rate_limiter()
        limiter = get_rate_limiter()
        assert limiter is get_rate_limiter()
        assert limiter.limit_per_hour == 7
        monkeypatch.delenv("QUERY_RATE_LIMIT_PER_HOUR")
        get_settings.cache_clear()
        reset_rate_limiter()
        assert get_rate_limiter().limit_per_hour == 30
    finally:
        get_settings.cache_clear()
        reset_rate_limiter()
