"""Document tags: attach, detach and the project's tag catalog.

Tags are metadata, NOT input (spec 8): no freeze check — tagging while an
index runs changes nothing the indexer reads. The project lock is still
taken, because the writes touch project_files-adjacent rows and discovery
may run concurrently.
"""

import asyncio
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import FileTag, FileTagLink, Project, ProjectFile
from graphrag_ui.services.audit import audit
from graphrag_ui.services.file_listing import discovered_row
from graphrag_ui.services.files import input_file
from graphrag_ui.services.input_scan import sha256_file
from graphrag_ui.services.project_lock import input_mutation


async def add_tags(
    session: AsyncSession, project: Project, name: str, tags: list[str], actor_id: uuid.UUID | None
) -> None:
    """Attach tags to input/<name> and audit file.tagged, one transaction."""
    name, target = input_file(project, name)
    unique_tags = sorted(dict.fromkeys(tags))
    by_name = select(ProjectFile).where(
        ProjectFile.project_id == project.id, ProjectFile.name == name
    )
    # A file nobody listed yet has no project_files row; tags attach to the
    # row, so discover it inline (the same shape a listing's discovery
    # produces). Only then is the file hashed — the unbounded read (spec A4)
    # runs off the lock and off the loop; a tracked file's row already
    # carries its hash (R1-69).
    sha: str | None = None
    if (await session.execute(by_name)).scalar_one_or_none() is None:
        sha = await asyncio.to_thread(sha256_file, target)
    async with input_mutation(session, project.id, freeze=False):
        row = (await session.execute(by_name)).scalar_one_or_none()
        if row is None:
            if sha is None:  # its row vanished since the check: hash now
                sha = await asyncio.to_thread(sha256_file, target)
            row = discovered_row(project.id, name, sha, target.stat().st_size)
            session.add(row)
            await session.flush()
        have = set(
            (
                await session.execute(
                    select(FileTag.name).where(
                        FileTag.project_id == project.id, FileTag.name.in_(unique_tags)
                    )
                )
            ).scalars()
        )
        session.add_all(
            FileTag(project_id=project.id, name=t) for t in unique_tags if t not in have
        )
        await session.flush()
        tag_ids = set(
            (
                await session.execute(
                    select(FileTag.id).where(
                        FileTag.project_id == project.id, FileTag.name.in_(unique_tags)
                    )
                )
            ).scalars()
        )
        linked = set(
            (
                await session.execute(
                    select(FileTagLink.tag_id).where(FileTagLink.file_id == row.id)
                )
            ).scalars()
        )
        session.add_all(FileTagLink(file_id=row.id, tag_id=tid) for tid in tag_ids - linked)
        await audit(
            session,
            actor_id,
            "file.tagged",
            "project",
            str(project.id),
            {"name": name, "tags": unique_tags},
        )


async def remove_tags(
    session: AsyncSession, project: Project, name: str, tags: list[str], actor_id: uuid.UUID | None
) -> None:
    """Detach tags from input/<name> and audit file.untagged, one
    transaction. Same boundary as add_tags: no freeze, project lock yes."""
    name, _ = input_file(project, name)
    unique_tags = sorted(dict.fromkeys(tags))
    async with input_mutation(session, project.id, freeze=False):
        # Deleting through subselects keeps it one statement shaped the same
        # regardless of how many tags are detached.
        await session.execute(
            delete(FileTagLink).where(
                FileTagLink.file_id.in_(
                    select(ProjectFile.id).where(
                        ProjectFile.project_id == project.id, ProjectFile.name == name
                    )
                ),
                FileTagLink.tag_id.in_(
                    select(FileTag.id).where(
                        FileTag.project_id == project.id, FileTag.name.in_(unique_tags)
                    )
                ),
            )
        )
        await audit(
            session,
            actor_id,
            "file.untagged",
            "project",
            str(project.id),
            {"name": name, "tags": unique_tags},
        )


async def list_tags(session: AsyncSession, project: Project) -> list[dict]:
    """The project's tag catalog with live link counts — one grouped query.
    A tag with zero links still lists (count 0): it stays the project's
    vocabulary, and suggesting it in a picker costs nothing."""
    rows = (
        await session.execute(
            select(FileTag.name, func.count(FileTagLink.tag_id))
            .outerjoin(FileTagLink, FileTagLink.tag_id == FileTag.id)
            .where(FileTag.project_id == project.id)
            .group_by(FileTag.name)
            .order_by(FileTag.name)
        )
    ).all()
    return [{"name": name, "count": int(count)} for name, count in rows]
