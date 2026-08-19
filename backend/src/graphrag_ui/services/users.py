import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from graphrag_ui.adapters.models import User
from graphrag_ui.services.audit import audit
from graphrag_ui.services.auth import hash_password, revoke_all_for_user


async def create_user(session: AsyncSession, email: str, display_name: str,
                      password: str, actor_id: uuid.UUID | None) -> User:
    user = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
        # 管理員設的初始密碼不該長期使用 — 與 reset_password 語意一致
        must_change_password=True,
    )
    session.add(user)
    await session.flush()  # 產生 user.id 供 audit 的 target_id 使用
    await audit(session, actor_id, "user.created", "user", str(user.id),
                payload={"email": email})
    await session.commit()
    return user


async def update_user(session: AsyncSession, user: User, *, display_name: str | None = None,
                      role: str | None = None, is_active: bool | None = None,
                      actor_id: uuid.UUID | None) -> User:
    changed: dict = {}
    if display_name is not None and display_name != user.display_name:
        user.display_name = display_name
        changed["display_name"] = display_name
    if role is not None and role != user.role:
        user.role = role
        changed["role"] = role
    if is_active is not None and is_active != user.is_active:
        user.is_active = is_active
        changed["is_active"] = is_active
    if not changed:  # 空的 PATCH 不是寫入操作,不寫 audit
        return user
    await audit(session, actor_id, "user.updated", "user", str(user.id), payload=changed)
    if changed.get("is_active") is False:
        # 停用即撤銷全部 refresh token;revoke_all_for_user 內部 commit,
        # 會連同上面的 user 變更與 audit 紀錄一併送出。
        await revoke_all_for_user(session, user.id)
    await session.commit()
    return user


async def reset_password(session: AsyncSession, user: User, new_password: str,
                         actor_id: uuid.UUID | None) -> None:
    user.password_hash = hash_password(new_password)
    user.must_change_password = True  # 管理員重設的密碼同樣要求使用者下次登入更換
    await audit(session, actor_id, "user.password_reset", "user", str(user.id))
    await revoke_all_for_user(session, user.id)  # 內部 commit,一併送出密碼變更與 audit
    await session.commit()
