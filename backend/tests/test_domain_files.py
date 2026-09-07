"""Per-file index state (spec 6.2/6.3), table-driven.

The four states need only the baseline and are always computable; skipped
is a refinement that exists only when title recovery was available when
the artifacts were produced.
"""

import pytest

from graphrag_ui.domain.files import AttributableTitles, FileIndexState, index_state

BASE = {"a.md": "hash-a", "b.md": "hash-b"}
AVAILABLE = AttributableTitles.of({"a.md"})
UNAVAILABLE = AttributableTitles.unavailable()


@pytest.mark.parametrize(
    ("name", "current_sha", "baseline", "attributable", "expected"),
    [
        # not in baseline -> new, regardless of anything else
        ("c.md", "hash-c", BASE, AVAILABLE, FileIndexState.new),
        ("c.md", "hash-c", BASE, UNAVAILABLE, FileIndexState.new),
        ("c.md", "hash-c", {}, AVAILABLE, FileIndexState.new),
        # in baseline, hash differs -> modified
        ("a.md", "other", BASE, AVAILABLE, FileIndexState.modified),
        # in baseline, gone from input/ -> removed (current_sha is None)
        ("a.md", None, BASE, AVAILABLE, FileIndexState.removed),
        ("b.md", None, BASE, UNAVAILABLE, FileIndexState.removed),
        # in baseline, hash matches, attributable -> indexed
        ("a.md", "hash-a", BASE, AVAILABLE, FileIndexState.indexed),
        # in baseline, hash matches, NOT attributable -> skipped
        ("b.md", "hash-b", BASE, AVAILABLE, FileIndexState.skipped),
        # recovery unavailable: skipped is unreachable, falls back to indexed
        ("b.md", "hash-b", BASE, UNAVAILABLE, FileIndexState.indexed),
        # a NULL sha from the backfill path must not read as "gone"
        ("a.md", "", BASE, AVAILABLE, FileIndexState.modified),
    ],
)
def test_index_state_table(name, current_sha, baseline, attributable, expected):
    assert index_state(name, current_sha, baseline, attributable) is expected


def test_removed_outranks_hash_comparison():
    """A removed file has no hash to compare; the rule must not fall through
    to modified just because current_sha is falsy."""
    assert index_state("a.md", None, BASE, UNAVAILABLE) is FileIndexState.removed


def test_skipped_never_emitted_when_recovery_unavailable():
    for name, sha in BASE.items():
        assert index_state(name, sha, BASE, UNAVAILABLE) is not FileIndexState.skipped
