import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import AuditLog


async def audit(session: AsyncSession, actor_id: uuid.UUID | None, action: str,
                target_type: str, target_id: str, payload: dict | None = None) -> None:
    # 只 add,**不 commit**:交易邊界屬於呼叫端。
    # 若這裡 commit,會把呼叫端尚未完成的變更一起送出
    #(例如 create_project 還沒跑完 graphrag init 就被 commit)。
    session.add(AuditLog(actor_id=actor_id, action=action, target_type=target_type,
                         target_id=target_id, payload=payload))
