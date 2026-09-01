from graphrag_ui import main
from graphrag_ui.api import auth_routes
from graphrag_ui.domain.sliding_window import SlidingWindow


async def test_bootstrap_admin_login_and_forced_change(client):
    r = await client.post(
        "/api/auth/login", json={"email": "admin@test.local", "password": "admin-pass-123"}
    )
    assert r.status_code == 200
    body = r.json()
    assert {r["name"] for r in body["user"]["roles"]} == {"user_admin", "ops"}
    assert body["user"]["must_change_password"] is True
    # Forced password-change flow
    hdr = {"Authorization": f"Bearer {body['access_token']}"}
    r2 = await client.post(
        "/api/auth/change-password",
        headers=hdr,
        json={"current_password": "admin-pass-123", "new_password": "new-pass-456"},
    )
    assert r2.status_code == 204
    r3 = await client.post(
        "/api/auth/login", json={"email": "admin@test.local", "password": "new-pass-456"}
    )
    assert r3.json()["user"]["must_change_password"] is False


async def test_login_is_case_insensitive_on_the_email(client):
    """Proxy-mode identity resolution has always lowercased both sides; local
    login compared the raw column. An admin who created `Alice@test.local`
    left a user who could never log in as `alice@test.local` — the error
    being the indistinguishable "invalid email or password".
    """
    r = await client.post(
        "/api/auth/login", json={"email": "ADMIN@test.local", "password": "admin-pass-123"}
    )
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "admin@test.local"


async def test_login_wrong_password(client):
    r = await client.post("/api/auth/login", json={"email": "admin@test.local", "password": "nope"})
    assert r.status_code == 401


async def test_login_rate_limit_blocks_after_ten_failures_per_email(client):
    for _ in range(10):
        r = await client.post(
            "/api/auth/login", json={"email": "admin@test.local", "password": "wrong"}
        )
        assert r.status_code == 401
    # 11th attempt: 429 even with the correct password (buckets key on
    # (ip, email) and count failures only)
    r = await client.post(
        "/api/auth/login", json={"email": "admin@test.local", "password": "admin-pass-123"}
    )
    assert r.status_code == 429
    # Another email's bucket is unaffected (still 401, not 429)
    r2 = await client.post(
        "/api/auth/login", json={"email": "other@test.local", "password": "nope"}
    )
    assert r2.status_code == 401


async def test_login_bucket_is_released_and_reclaimed_after_the_window(client, monkeypatch):
    """A 429'd client is let back in, and its bucket stops occupying memory.

    The limiter map used to keep every (ip, email) it had ever seen: the
    deque was drained but the key stayed. Both halves are request-supplied,
    so failed logins with fresh emails grew the process forever.
    """
    clock = {"t": 1_000.0}
    monkeypatch.setattr(auth_routes, "_now", lambda: clock["t"])

    for _ in range(10):
        await client.post("/api/auth/login", json={"email": "a@test.local", "password": "wrong"})
    r = await client.post("/api/auth/login", json={"email": "a@test.local", "password": "wrong"})
    assert r.status_code == 429
    assert len(auth_routes._LOGIN_FAILURES) == 1

    clock["t"] += auth_routes._LOGIN_WINDOW_SECONDS + 1
    r = await client.post("/api/auth/login", json={"email": "a@test.local", "password": "wrong"})
    assert r.status_code == 401  # window slid; the client is no longer blocked
    # Counting an expired bucket drops it; this failure re-created one.
    assert len(auth_routes._LOGIN_FAILURES) == 1


def test_login_bucket_map_has_a_hard_ceiling():
    # The flood case the API test cannot express in a reasonable runtime:
    # unique keys arriving faster than the 1-minute window retires them.
    window = SlidingWindow(
        window_seconds=auth_routes._LOGIN_WINDOW_SECONDS,
        max_keys=auth_routes._LOGIN_MAX_TRACKED_KEYS,
    )
    for i in range(auth_routes._LOGIN_MAX_TRACKED_KEYS * 2):
        window.add(("10.0.0.1", f"flood-{i}@test.local"), 1_000.0)
    assert len(window) <= auth_routes._LOGIN_MAX_TRACKED_KEYS


async def test_successful_logins_do_not_count_toward_rate_limit(client):
    # Successful logins never fill a bucket: 12 successful logins within a
    # minute must not 429
    for _ in range(12):
        r = await client.post(
            "/api/auth/login", json={"email": "admin@test.local", "password": "admin-pass-123"}
        )
        assert r.status_code == 200


async def test_refresh_rotation_invalidates_old(client):
    body = (
        await client.post(
            "/api/auth/login", json={"email": "admin@test.local", "password": "admin-pass-123"}
        )
    ).json()
    old = body["refresh_token"]
    r1 = await client.post("/api/auth/refresh", json={"refresh_token": old})
    assert r1.status_code == 200
    r2 = await client.post("/api/auth/refresh", json={"refresh_token": old})
    assert r2.status_code == 401  # the old one is already revoked


async def test_refresh_reuse_revokes_family(client):
    body = (
        await client.post(
            "/api/auth/login", json={"email": "admin@test.local", "password": "admin-pass-123"}
        )
    ).json()
    old = body["refresh_token"]
    new = (await client.post("/api/auth/refresh", json={"refresh_token": old})).json()[
        "refresh_token"
    ]
    # Replay the consumed old token -> treated as a leak; the newly issued
    # one is revoked too
    assert (await client.post("/api/auth/refresh", json={"refresh_token": old})).status_code == 401
    assert (await client.post("/api/auth/refresh", json={"refresh_token": new})).status_code == 401


async def test_me_returns_current_user(client):
    body = (
        await client.post(
            "/api/auth/login", json={"email": "admin@test.local", "password": "admin-pass-123"}
        )
    ).json()
    hdr = {"Authorization": f"Bearer {body['access_token']}"}
    # While must_change_password is true, endpoints other than /me and
    # change-password must be blocked
    assert (await client.get("/api/auth/me", headers=hdr)).json()["email"] == "admin@test.local"
    assert (await client.get("/api/admin/users", headers=hdr)).status_code == 403
    # A path with no mounted route is blocked 403 by the global middleware
    # (not 404) — proves the guard is registered
    assert (await client.get("/api/no-such-route", headers=hdr)).status_code == 403


async def test_logout_revokes(client):
    body = (
        await client.post(
            "/api/auth/login", json={"email": "admin@test.local", "password": "admin-pass-123"}
        )
    ).json()
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


async def test_must_change_guard_skips_the_db_for_mounted_routes(client, monkeypatch):
    """The guard exists to turn a 404 into a 403 on paths that have no route.

    For everything that *does* have a route, get_current_user already runs
    the identical check, so the guard was decoding the JWT and opening a
    second session per request purely to reach the same answer — the auth
    query, doubled on every authenticated call.

    Patching main's name only affects the middleware: deps.get_current_user
    resolves through its own module-level reference.
    """
    calls = []
    real = main.resolve_access_user

    async def counting(token, db):
        calls.append(token)
        return await real(token, db)

    monkeypatch.setattr(main, "resolve_access_user", counting)
    body = (
        await client.post(
            "/api/auth/login", json={"email": "admin@test.local", "password": "admin-pass-123"}
        )
    ).json()
    hdr = {"Authorization": f"Bearer {body['access_token']}"}

    assert (await client.get("/api/admin/users", headers=hdr)).status_code == 403
    assert calls == []  # the mounted route's own dependency answered it

    assert (await client.get("/api/no-such-route", headers=hdr)).status_code == 403
    assert len(calls) == 1  # only the unrouted path pays for the lookup


async def test_guard_still_403s_an_unrouted_path_with_a_wrong_method(client):
    # A path that exists but rejects the method must not fall through to the
    # guard's DB check either — 405 is the route's answer, not a leak.
    body = (
        await client.post(
            "/api/auth/login", json={"email": "admin@test.local", "password": "admin-pass-123"}
        )
    ).json()
    hdr = {"Authorization": f"Bearer {body['access_token']}"}
    r = await client.delete("/api/admin/users", headers=hdr)
    assert r.status_code in (403, 405)
