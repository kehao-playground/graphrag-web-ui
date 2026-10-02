"""Test doubles and read helpers that used to ship in production modules
(R1-40). Importable as `helpers` (pyproject sets pythonpath = ["tests"])."""

import uuid
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import IndexSnapshot
from graphrag_ui.adapters.workspace import _escaped_pattern


class FakeInitializer:
    """Unit tests: create the dir and a minimal settings.yaml, no CLI fork.
    Writes the same $-escaped file_pattern as the real initializer so the
    real CLI can still load the workspace."""

    async def init(self, root: Path, input_file_type: str) -> None:
        (root / "input").mkdir(parents=True, exist_ok=True)
        (root / "settings.yaml").write_text(
            yaml.safe_dump(
                {
                    "input": {
                        "type": input_file_type,
                        "file_pattern": _escaped_pattern(input_file_type),
                    }
                }
            )
        )


async def kinds_of(session: AsyncSession, job_id: uuid.UUID) -> set[str]:
    """The snapshot kinds (start/end) recorded for one job."""
    res = await session.execute(select(IndexSnapshot.kind).where(IndexSnapshot.job_id == job_id))
    return set(res.scalars().all())
