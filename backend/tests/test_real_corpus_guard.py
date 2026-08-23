# backend/tests/test_real_corpus_guard.py
"""Fast guard against the PR#5 silent-skip failure mode (spec A6): if a
real-corpus module's fixture import is deleted, pytest silently skips
its tests. Object identity fails loudly instead. No pytest internals."""
import real_corpus_fixtures as helper
import test_real_corpus_explore
import test_real_corpus_jobs
import test_real_corpus_query


def test_real_corpus_modules_bind_shared_fixtures():
    for mod in (test_real_corpus_query, test_real_corpus_jobs,
                test_real_corpus_explore):
        assert mod.query_client is helper.real_corpus_client, mod.__name__
        marks = {m.name for m in getattr(mod, "pytestmark", [])}
        assert "slow" in marks, mod.__name__


def test_modules_share_the_common_pytestmark():
    for mod in (test_real_corpus_query, test_real_corpus_jobs,
                test_real_corpus_explore):
        assert mod.pytestmark == helper.pytestmark, mod.__name__
