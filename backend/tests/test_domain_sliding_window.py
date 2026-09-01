"""Bounded sliding-window counter shared by the login and query limiters.

Both limiters used to be a bare dict that only ever grew: entries were
purged *within* a key's deque but the key itself was never removed, and the
keys are attacker-chosen ((ip, email) for login, so both halves are). These
tests pin the two properties that fixes it — empty keys disappear, and the
key count has a hard ceiling.
"""

from graphrag_ui.domain.sliding_window import SlidingWindow


def test_counts_hits_inside_the_window():
    w = SlidingWindow(window_seconds=60, max_keys=100)
    w.add("k", 1000.0)
    w.add("k", 1010.0)
    assert w.count("k", 1020.0) == 2


def test_hits_older_than_the_window_stop_counting():
    w = SlidingWindow(window_seconds=60, max_keys=100)
    w.add("k", 1000.0)
    w.add("k", 1030.0)
    assert w.count("k", 1061.0) == 1  # the 1000.0 hit aged out


def test_counting_a_fully_expired_key_removes_it():
    w = SlidingWindow(window_seconds=60, max_keys=100)
    w.add("k", 1000.0)
    assert len(w) == 1
    assert w.count("k", 1100.0) == 0
    assert len(w) == 0  # the bucket is gone, not merely empty


def test_counting_an_unknown_key_creates_nothing():
    w = SlidingWindow(window_seconds=60, max_keys=100)
    assert w.count("never-seen", 1000.0) == 0
    assert len(w) == 0


def test_keys_are_independent():
    w = SlidingWindow(window_seconds=60, max_keys=100)
    w.add("a", 1000.0)
    w.add("b", 1000.0)
    w.add("b", 1001.0)
    assert w.count("a", 1002.0) == 1
    assert w.count("b", 1002.0) == 2


def test_expired_keys_are_swept_when_the_ceiling_is_reached():
    w = SlidingWindow(window_seconds=60, max_keys=10)
    for i in range(10):
        w.add(f"old-{i}", 1000.0)
    assert len(w) == 10
    # Every existing key is now expired; adding one more sweeps them.
    w.add("fresh", 2000.0)
    assert len(w) == 1
    assert w.count("fresh", 2000.0) == 1


def test_ceiling_holds_even_when_nothing_has_expired():
    # The flood case: unique keys arriving faster than the window retires
    # them. A sweep frees nothing, so the oldest buckets are evicted and the
    # map still cannot grow without bound.
    w = SlidingWindow(window_seconds=3600, max_keys=10)
    for i in range(500):
        w.add(f"k-{i}", 1000.0 + i)
    assert len(w) <= 10


def test_eviction_under_flood_keeps_the_most_recent_keys():
    w = SlidingWindow(window_seconds=3600, max_keys=3)
    for i in range(20):
        w.add(f"k-{i}", 1000.0 + i)
    # The newest key must still be counted — evicting it would hand an
    # attacker a way to clear their own bucket by flooding other keys.
    assert w.count("k-19", 1020.0) == 1


def test_clear_empties_everything():
    w = SlidingWindow(window_seconds=60, max_keys=100)
    w.add("a", 1000.0)
    w.add("b", 1000.0)
    w.clear()
    assert len(w) == 0
