import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import AuditLog, User


async def audit(
    session: AsyncSession,
    actor_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: str,
    payload: dict | None = None,
) -> None:
    # Only add, **never commit**: the transaction boundary belongs to the caller.
    # Committing here would flush the caller's not-yet-finished changes too
    # (e.g. create_project committed before graphrag init has even run).
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
        )
    )


async def list_audit(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
    action: str | None = None,
    target_type: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> tuple[list[dict], int]:
    """One page of audit rows, newest first, plus the unpaginated total.

    The actor is joined to its email here rather than left as a bare uuid:
    a log that reads "3f2a…-… created user" is not one anybody will use.
    An outer join, because actor_id is null for rows the system wrote
    itself (bootstrap admin creation) and because a deleted user must not
    make its history disappear.
    """
    filters = []
    if action is not None:
        filters.append(AuditLog.action == action)
    if target_type is not None:
        filters.append(AuditLog.target_type == target_type)
    if actor_id is not None:
        filters.append(AuditLog.actor_id == actor_id)

    total = (
        await session.execute(select(func.count()).select_from(AuditLog).where(*filters))
    ).scalar_one()
    rows = (
        await session.execute(
            select(AuditLog, User.email)
            .outerjoin(User, User.id == AuditLog.actor_id)
            .where(*filters)
            .order_by(AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [
        {
            "id": row.id,
            "actor_id": row.actor_id,
            "actor_email": email,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "payload": row.payload,
            "created_at": row.created_at,
        }
        for row, email in rows
    ], int(total)
