ADMIN_PW = "admin-new-pass-1"


async def _admin_token(client):
    """bootstrap admin 的 must_change_password=True — 換完密碼才拿得到可用 token。"""
    r = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})
    hdr = {"Authorization": f"Bearer {r.json()['access_token']}"}
    await client.post("/api/auth/change-password", headers=hdr, json={
        "current_password": "admin-pass-123", "new_password": ADMIN_PW})
    r2 = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": ADMIN_PW})
    return {"Authorization": f"Bearer {r2.json()['access_token']}"}


async def test_admin_crud_and_audit(client, db_session):
    hdr = await _admin_token(client)
    r = await client.post("/api/admin/users", headers=hdr, json={
        "email": "u1@test.local", "display_name": "User One", "password": "pass-12345"})
    assert r.status_code == 201
    uid = r.json()["id"]
    r2 = await client.patch(f"/api/admin/users/{uid}", headers=hdr,
                            json={"display_name": "User 1b"})
    assert r2.json()["display_name"] == "User 1b"
    r3 = await client.post(f"/api/admin/users/{uid}/reset-password",
                           headers=hdr, json={"new_password": "reset-12345"})
    assert r3.status_code == 204
    # audit 直接驗表 — 不要用「停用後登不進去」來推論 audit 有寫
    from sqlalchemy import select

    from graphrag_ui.adapters.models import AuditLog
    actions = set((await db_session.execute(select(AuditLog.action))).scalars())
    assert {"user.created", "user.updated", "user.password_reset"} <= actions
    r4 = await client.patch(f"/api/admin/users/{uid}", headers=hdr, json={"is_active": False})
    assert r4.json()["is_active"] is False
    r5 = await client.post("/api/auth/login", json={
        "email": "u1@test.local", "password": "reset-12345"})
    assert r5.status_code == 401


async def test_non_admin_forbidden(client):
    hdr = await _admin_token(client)
    await client.post("/api/admin/users", headers=hdr, json={
        "email": "u2@test.local", "display_name": "U2", "password": "pass-12345"})
    r = await client.post("/api/auth/login", json={
        "email": "u2@test.local", "password": "pass-12345"})
    tok = {"Authorization": f"Bearer {r.json()['access_token']}"}
    # 新建帳號 must_change_password=True → 先換密碼才能用其他端點
    await client.post("/api/auth/change-password", headers=tok, json={
        "current_password": "pass-12345", "new_password": "u2-pass-6789"})
    r2 = await client.post("/api/auth/login", json={
        "email": "u2@test.local", "password": "u2-pass-6789"})
    user_tok = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    r3 = await client.get("/api/admin/users", headers=user_tok)
    assert r3.status_code == 403
