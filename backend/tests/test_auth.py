async def test_bootstrap_admin_login_and_forced_change(client):
    r = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "admin"
    assert body["user"]["must_change_password"] is True
    # 強制改密碼流程
    hdr = {"Authorization": f"Bearer {body['access_token']}"}
    r2 = await client.post("/api/auth/change-password", headers=hdr, json={
        "current_password": "admin-pass-123", "new_password": "new-pass-456"})
    assert r2.status_code == 204
    r3 = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "new-pass-456"})
    assert r3.json()["user"]["must_change_password"] is False


async def test_login_wrong_password(client):
    r = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "nope"})
    assert r.status_code == 401


async def test_refresh_rotation_invalidates_old(client):
    body = (await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})).json()
    old = body["refresh_token"]
    r1 = await client.post("/api/auth/refresh", json={"refresh_token": old})
    assert r1.status_code == 200
    r2 = await client.post("/api/auth/refresh", json={"refresh_token": old})
    assert r2.status_code == 401  # 舊的已被作廢


async def test_refresh_reuse_revokes_family(client):
    body = (await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})).json()
    old = body["refresh_token"]
    new = (await client.post("/api/auth/refresh", json={"refresh_token": old})).json()["refresh_token"]
    # 拿已消費的舊 token 再試一次 → 視為外洩,連新發的也一併作廢
    assert (await client.post("/api/auth/refresh", json={"refresh_token": old})).status_code == 401
    assert (await client.post("/api/auth/refresh", json={"refresh_token": new})).status_code == 401


async def test_me_returns_current_user(client):
    body = (await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})).json()
    hdr = {"Authorization": f"Bearer {body['access_token']}"}
    # must_change_password 為真時,/me 與 change-password 以外的端點應被擋
    assert (await client.get("/api/auth/me", headers=hdr)).json()["email"] == "admin@test.local"
    assert (await client.get("/api/admin/users", headers=hdr)).status_code == 403


async def test_logout_revokes(client):
    body = (await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})).json()
    await client.post("/api/auth/logout", json={"refresh_token": body["refresh_token"]})
    r = await client.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 401
