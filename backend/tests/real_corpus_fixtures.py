"""Shared fixtures/marks for the three real-corpus slow modules (spec A6):
test_real_corpus_query, test_real_corpus_jobs, test_real_corpus_explore.

Each module binds these under its local names (query_app/runner_app style
aliases) so existing test bodies stay untouched; test_real_corpus_guard.py
asserts the identity of the bound objects, so a deleted import fails loudly
instead of pytest silently skipping the module's tests.

The key gate (skipif without GRAPHRAG_API_KEY) and the slow mark live ONLY
here — modules share this exact pytestmark object rather than re-declaring
it. DOCS is the query/explore micro-corpus; jobs keeps its own corpus in
module scope because its incremental-update content is test-specific.
"""

import os
import shutil

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from graphrag_ui.adapters.db import reset_engine
from graphrag_ui.api import auth_routes
from graphrag_ui.config import get_settings
from graphrag_ui.main import create_app

# Single source of the slow mark + real-LLM key gate; every real-corpus
# module binds this exact object (the guard test asserts equality).
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("GRAPHRAG_API_KEY"), reason="needs real LLM key (GRAPHRAG_API_KEY)"
    ),
]

# Factual micro-corpus, 2-3 sentences per document: enough text for the
# standard pipeline's entity extraction to find something on every doc, and
# a question basic search can answer from the text units.
DOCS = {
    "babbage.txt": (
        "Charles Babbage, an English mathematician and inventor, designed the "
        "Analytical Engine between 1834 and 1846. He was Lucasian Professor of "
        "Mathematics at Cambridge from 1828 to 1839. Babbage funded much of the "
        "Engine's development from his own fortune after the British government "
        "withdrew its support."
    ),
    "lovelace.txt": (
        "Ada Lovelace, daughter of the poet Lord Byron, translated Menabrea's "
        "memoir on the Analytical Engine from French and appended her Notes, "
        "published in 1843. Her Note G described an algorithm for computing "
        "Bernoulli numbers, often considered the first published computer "
        "program. She worked closely with Charles Babbage on the Engine."
    ),
    "engine.txt": (
        "The Analytical Engine was a proposed mechanical general-purpose "
        "computer designed around an arithmetic mill, a store for one thousand "
        "numbers, and punch-card control flow borrowed from the Jacquard loom. "
        "It was never completed during Babbage's lifetime, yet its architecture "
        "anticipated the modern CPU."
    ),
}


@pytest.fixture
def ws_root(tmp_path):
    """Workspace root shared with the app; removed afterwards — the corpus
    output/cache artifacts are the test's biggest footprint on disk."""
    root = tmp_path / "ws"
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
async def real_corpus_app(clean_db, monkeypatch, ws_root):
    """conftest's app fixture disables the runner loop (MAX_CONCURRENT_JOBS=0)
    so queued jobs are never auto-executed; this variant enables it with cap 1
    so POSTing a job actually runs it, exactly like a single-replica deploy."""
    monkeypatch.setenv("WORKSPACES_DIR", str(ws_root))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@test.local")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "admin-pass-123")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-0123456789abcdef0123456789abcd")
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "1")
    get_settings.cache_clear()
    await reset_engine()  # env changed → shared engine must be rebuilt
    auth_routes._LOGIN_FAILURES.clear()
    return create_app()


@pytest.fixture
async def real_corpus_client(real_corpus_app):
    async with (
        LifespanManager(real_corpus_app) as managed,
        AsyncClient(transport=ASGITransport(app=managed.app), base_url="http://t") as c,
    ):
        yield c
    await reset_engine()
