"""Route → error code assertions (i18n spec §4.2), one place for all
routes. detail strings are pinned elsewhere; here we pin the code field."""


async def test_login_failure_carries_code(client):
    r = await client.post("/api/auth/login",
                          json={"email": "admin@test.local", "password": "wrong"})
    assert r.status_code == 401
    assert r.json()["code"] == "auth_invalid_credentials"
    assert r.json()["detail"] == "invalid email or password"


async def test_must_change_guard_carries_code(client):
    # Fresh bootstrap admin starts must_change_password=True; the global
    # guard 403s any /api path outside the allowlist (main.py §4.4 exit).
    # The guard only fires when Authorization starts with "Bearer " —
    # a bare request would 401 in get_current_user instead.
    login = await client.post(
        "/api/auth/login",
        json={"email": "admin@test.local", "password": "admin-pass-123"})
    token = login.json()["access_token"]
    r = await client.get("/api/projects",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    body = r.json()
    assert body["detail"] == "password change required"
    assert body["code"] == "auth_must_change_password"
