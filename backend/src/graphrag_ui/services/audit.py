import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import AuditLog


async def audit(session: AsyncSession, actor_id: uuid.UUID | None, action: str,
                target_type: str, target_id: str, payload: dict | None = None) -> None:
    # Only add, **never commit**: the transaction boundary belongs to the caller.
    # Committing here would flush the caller's not-yet-finished changes too
    # (e.g. create_project committed before graphrag init has even run).
    session.add(AuditLog(actor_id=actor_id, action=action, target_type=target_type,
                         target_id=target_id, payload=payload))
