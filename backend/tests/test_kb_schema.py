"""Schema shape for slice 1 (spec 5.1/5.2): the constraints later tasks
rely on, asserted against the migrated database rather than the models.

test_schema_drift.py already proves models and alembic head agree; this
module pins the three facts that a drift check cannot see - the unique
keys and the artifact_epoch default - because a missing unique key turns
a correctness bug into silent duplicate rows.
"""

import sqlalchemy as sa

from graphrag_ui.adapters.db import make_engine


async def _indexes(migrated_db, table: str) -> dict[str, tuple[bool, list[str]]]:
    engine = make_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            rows = await conn.run_sync(lambda c: sa.inspect(c).get_indexes(table))
            uniques = await conn.run_sync(lambda c: sa.inspect(c).get_unique_constraints(table))
    finally:
        await engine.dispose()
    out = {i["name"]: (bool(i["unique"]), list(i["column_names"])) for i in rows}
    out.update({u["name"]: (True, list(u["column_names"])) for u in uniques})
    return out


async def test_project_files_unique_per_project_name(migrated_db):
    idx = await _indexes(migrated_db, "project_files")
    assert any(unique and cols == ["project_id", "name"] for unique, cols in idx.values()), idx


async def test_index_snapshots_unique_per_job_kind(migrated_db):
    idx = await _indexes(migrated_db, "index_snapshots")
    assert any(unique and cols == ["job_id", "kind"] for unique, cols in idx.values()), idx


async def test_projects_artifact_epoch_defaults_to_zero(migrated_db):
    engine = make_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            cols = await conn.run_sync(lambda c: sa.inspect(c).get_columns("projects"))
    finally:
        await engine.dispose()
    epoch = next(c for c in cols if c["name"] == "artifact_epoch")
    assert epoch["nullable"] is False
    assert "0" in str(epoch["default"])
    baseline = next(c for c in cols if c["name"] == "baseline_snapshot_id")
    assert baseline["nullable"] is True
