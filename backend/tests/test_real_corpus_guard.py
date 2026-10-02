# backend/tests/test_real_corpus_guard.py
"""Fast guard against the PR#5 silent-skip failure mode (spec A6): if a
real-corpus module's fixture import is deleted, pytest silently skips
its tests, or (F1-02) errors at setup on key-gated runs only. Object
identity fails loudly instead. No pytest internals."""

import real_corpus_fixtures as helper
import test_real_corpus_explore
import test_real_corpus_jobs
import test_real_corpus_query
import test_real_corpus_titles

MODULES = (
    test_real_corpus_query,
    test_real_corpus_jobs,
    test_real_corpus_explore,
    test_real_corpus_titles,
)


def test_real_corpus_modules_bind_shared_fixtures():
    for mod in MODULES:
        assert mod.query_client is helper.real_corpus_client, mod.__name__
        # The client's own dependencies resolve by name in the test module;
        # a module missing one errors at setup, but only on a key-gated run
        # (F1-02), so pin them here where the fast suite sees it.
        assert mod.real_corpus_app is helper.real_corpus_app, mod.__name__
        assert mod.ws_root is helper.ws_root, mod.__name__
        marks = {m.name for m in getattr(mod, "pytestmark", [])}
        assert "slow" in marks, mod.__name__


def test_modules_share_the_common_pytestmark():
    for mod in MODULES:
        assert mod.pytestmark == helper.pytestmark, mod.__name__
