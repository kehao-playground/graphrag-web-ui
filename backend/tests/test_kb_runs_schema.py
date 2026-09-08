"""Schema shape for slice 2 (spec 5.3/5.4).

Two facts a drift check cannot see: the one-result-per-(run, question)
unique key, and that every column the worker fills in is nullable. A
placeholder row must be insertable honestly - a non-null sentinel would be
indistinguishable from a question that genuinely returned nothing.
"""

import sqlalchemy as sa

from graphrag_ui.adapters.db import make_engine


async def _columns(migrated_db, table: str) -> dict[str, dict]:
    engine = make_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            cols = await conn.run_sync(lambda c: sa.inspect(c).get_columns(table))
    finally:
        await engine.dispose()
    return {c["name"]: c for c in cols}


async def test_one_result_per_run_and_question(migrated_db):
    engine = make_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            idx = await conn.run_sync(lambda c: sa.inspect(c).get_indexes("test_results"))
            uq = await conn.run_sync(lambda c: sa.inspect(c).get_unique_constraints("test_results"))
    finally:
        await engine.dispose()
    pairs = [list(i["column_names"]) for i in idx if i["unique"]] + [
        list(u["column_names"]) for u in uq
    ]
    assert ["run_id", "question_id"] in pairs, pairs


async def test_worker_written_columns_are_nullable(migrated_db):
    results = await _columns(migrated_db, "test_results")
    for name in ("answer", "citations", "timings", "error", "completed_at"):
        assert results[name]["nullable"] is True, name
    for name in ("run_id", "question_id", "position", "question_text"):
        assert results[name]["nullable"] is False, name

    runs = await _columns(migrated_db, "test_runs")
    for name in ("index_job_id", "workspace_config_revision", "started_at", "finished_at"):
        assert runs[name]["nullable"] is True, name


async def test_one_rating_per_result(migrated_db):
    engine = make_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            idx = await conn.run_sync(lambda c: sa.inspect(c).get_indexes("result_ratings"))
            uq = await conn.run_sync(
                lambda c: sa.inspect(c).get_unique_constraints("result_ratings")
            )
    finally:
        await engine.dispose()
    cols = [list(i["column_names"]) for i in idx if i["unique"]] + [
        list(u["column_names"]) for u in uq
    ]
    assert ["result_id"] in cols, cols


async def test_jobs_gained_nullable_params_and_progress(migrated_db):
    jobs = await _columns(migrated_db, "jobs")
    assert jobs["params"]["nullable"] is True
    assert jobs["progress"]["nullable"] is True


async def test_questions_carry_a_lineage_and_an_archive_stamp(migrated_db):
    questions = await _columns(migrated_db, "questions")
    assert questions["lineage_id"]["nullable"] is False
    assert questions["archived_at"]["nullable"] is True
    sets = await _columns(migrated_db, "question_sets")
    assert sets["archived_at"]["nullable"] is True
