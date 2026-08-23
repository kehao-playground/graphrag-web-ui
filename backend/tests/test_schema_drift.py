"""Schema drift gate (spec A5.1): alembic head must match Base.metadata.

Fail categories: add/remove table, add/remove column, type change,
nullability change. Ignored (known autogenerate noise): server_default,
index/constraint naming and rendering. Table-level drift was already
caught accidentally by conftest's TRUNCATE; this gate adds the
column/type/nullability layer.
"""
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from graphrag_ui.adapters.db import make_engine
from graphrag_ui.adapters.models import Base

_FAIL_KINDS = {"add_table", "remove_table", "add_column", "remove_column",
               "modify_type", "modify_nullable"}


async def test_alembic_head_matches_metadata(migrated_db):
    engine = make_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            diffs = await conn.run_sync(
                lambda c: compare_metadata(
                    MigrationContext.configure(c), Base.metadata))
    finally:
        await engine.dispose()
    real = [d for d in diffs if d[0] in _FAIL_KINDS]
    assert real == [], f"schema drift (migrate or revert the model): {real}"
