async def test_bootstrap_admin_login_and_forced_change(client):
    r = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "admin"
    assert body["user"]["must_change_password"] is True
    # Forced password-change flow
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
    # 11th attempt: 429 even with the correct password (buckets key on
    # (ip, email) and count failures only)
    r = await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})
    assert r.status_code == 429
    # Another email's bucket is unaffected (still 401, not 429)
    r2 = await client.post("/api/auth/login", json={
        "email": "other@test.local", "password": "nope"})
    assert r2.status_code == 401


async def test_successful_logins_do_not_count_toward_rate_limit(client):
    # Successful logins never fill a bucket: 12 successful logins within a
    # minute must not 429
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
    assert r2.status_code == 401  # the old one is already revoked


async def test_refresh_reuse_revokes_family(client):
    body = (await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})).json()
    old = body["refresh_token"]
    new = (await client.post("/api/auth/refresh", json={"refresh_token": old})).json()["refresh_token"]
    # Replay the consumed old token -> treated as a leak; the newly issued
    # one is revoked too
    assert (await client.post("/api/auth/refresh", json={"refresh_token": old})).status_code == 401
    assert (await client.post("/api/auth/refresh", json={"refresh_token": new})).status_code == 401


async def test_me_returns_current_user(client):
    body = (await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})).json()
    hdr = {"Authorization": f"Bearer {body['access_token']}"}
    # While must_change_password is true, endpoints other than /me and
    # change-password must be blocked
    assert (await client.get("/api/auth/me", headers=hdr)).json()["email"] == "admin@test.local"
    assert (await client.get("/api/admin/users", headers=hdr)).status_code == 403
    # A path with no mounted route is blocked 403 by the global middleware
    # (not 404) — proves the guard is registered
    assert (await client.get("/api/no-such-route", headers=hdr)).status_code == 403


async def test_logout_revokes(client):
    body = (await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-pass-123"})).json()
    await client.post("/api/auth/logout", json={"refresh_token": body["refresh_token"]})
    r = await client.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 401


async def test_bootstrap_admin_warns_when_admin_already_exists(db_session, app, caplog):
    # Bootstrap creates the admin only once; a re-run against a persisted
    # volume (e.g. .env password changed between trials) keeps the OLD
    # password and login just says "invalid email or password". The startup
    # must log why BOOTSTRAP_ADMIN_PASSWORD had no effect.
    import logging

    from graphrag_ui.services.auth import bootstrap_admin

    await bootstrap_admin(db_session)  # first run: creates admin@test.local
    db_session.expire_all()
    with caplog.at_level(logging.WARNING, logger="graphrag_ui.services.auth"):
        await bootstrap_admin(db_session)  # second run: admin exists -> warn
    assert "BOOTSTRAP_ADMIN_PASSWORD" in caplog.text
    assert "admin@test.local" in caplog.text
