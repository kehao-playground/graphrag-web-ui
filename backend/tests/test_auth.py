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


async def test_login_rate_limit_blocks_after_ten_failures_per_email(client):
    for _ in range(10):
        r = await client.post("/api/auth/login", json={
            "email": "admin@test.local", "password": "wrong"})
        assert r.status_code == 401
    # 第 11 次:即使密碼正確也 429(桶以 (ip, email) 計,只累積失敗)
    r = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})
    assert r.status_code == 429
    # 其他 email 的桶不受影響(仍是 401 而非 429)
    r2 = await client.post("/api/auth/login", json={
        "email": "other@test.local", "password": "nope"})
    assert r2.status_code == 401


async def test_successful_logins_do_not_count_toward_rate_limit(client):
    # 成功登入不佔桶:一分鐘內超過 10 次成功登入也不得 429
    for _ in range(12):
        r = await client.post("/api/auth/login", json={
            "email": "admin@test.local", "password": "admin-pass-123"})
        assert r.status_code == 200


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
    # 未掛路由的路徑由全域 middleware 擋 403(而非 404)— 證明 guard 有註冊
    assert (await client.get("/api/no-such-route", headers=hdr)).status_code == 403


async def test_logout_revokes(client):
    body = (await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})).json()
    await client.post("/api/auth/logout", json={"refresh_token": body["refresh_token"]})
    r = await client.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 401
