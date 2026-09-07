"""Per-file index state (spec 6.2/6.3). Pure: no I/O, no ORM, no graphrag.

Four states need only the baseline and are ALWAYS computable. `skipped` is
a refinement available only when a documents.title could be mapped back to
a filename when the artifacts were produced - so it is expressed as a
separate input rather than folded into the baseline, and its absence
degrades to `indexed` rather than to a screen of false alarms.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum


class FileIndexState(StrEnum):
    new = "new"
    modified = "modified"
    removed = "removed"
    indexed = "indexed"
    skipped = "skipped"


class IngestCheck(StrEnum):
    """Whether `skipped` can be emitted at all, and why not (spec 6.3).
    Lives on the response, not on each file row: it is a property of the
    artifacts, and repeating it per file would invite a per-file rendering."""

    available = "available"
    unavailable_no_baseline = "unavailable_no_baseline"
    unavailable_not_indexed = "unavailable_not_indexed"
    unavailable_title_column = "unavailable_title_column"


@dataclass(frozen=True)
class AttributableTitles:
    """Filenames attributable to the indexed artifacts, or the fact that no
    attribution is possible. An empty set with available=True ("the rule ran
    and matched nothing") is a different fact from available=False ("the rule
    could not run"), and only the first may produce `skipped`."""

    available: bool
    filenames: frozenset[str]

    @classmethod
    def of(cls, filenames: Iterable[str]) -> "AttributableTitles":
        return cls(True, frozenset(filenames))

    @classmethod
    def unavailable(cls) -> "AttributableTitles":
        return cls(False, frozenset())


def index_state(
    name: str,
    current_sha: str | None,
    baseline: Mapping[str, str],
    attributable: AttributableTitles,
) -> FileIndexState:
    """`current_sha` is None exactly when the file is gone from input/."""
    if name not in baseline:
        return FileIndexState.new
    if current_sha is None:
        # Checked before the hash comparison: a removed file has no hash, and
        # falling through would report `modified` for a file that is gone.
        return FileIndexState.removed
    if current_sha != baseline[name]:
        return FileIndexState.modified
    if attributable.available and name not in attributable.filenames:
        return FileIndexState.skipped
    return FileIndexState.indexed
