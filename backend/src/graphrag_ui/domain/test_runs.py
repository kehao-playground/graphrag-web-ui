"""Pure rating arithmetic for test runs (spec 5.3/7.5). No I/O, no ORM.

Ratings are project-shared, one current row per result (spec 5.3). The
matrix window bound is a domain constant like the question curation
bounds — no new environment variables (plan Global Constraints).
"""

from collections.abc import Mapping

# How many of the most recent runs the matrix shows by default (spec 9.2).
MATRIX_DEFAULT_RUNS = 5

RATING_SCORES: tuple[str, ...] = ("good", "fair", "poor")
# Ordered best to worst; a regression is a move to a HIGHER index.
RATING_ORDER: dict[str, int] = {s: i for i, s in enumerate(RATING_SCORES)}


def count_regressions(previous: Mapping[str, str], current: Mapping[str, str]) -> int:
    """Lineages whose newest rating is worse than the previous run's.

    A lineage missing on either side counts as nothing: an unrated or
    not-yet-asked question is not a regression, and treating it as one
    would make the overview cry wolf on every new question.
    """
    return sum(
        1
        for lineage, score in current.items()
        if lineage in previous and RATING_ORDER[score] > RATING_ORDER[previous[lineage]]
    )
