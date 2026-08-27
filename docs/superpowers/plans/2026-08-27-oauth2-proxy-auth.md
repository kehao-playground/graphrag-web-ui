# OAuth2-Proxy Optional Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `AUTH_MODE=proxy` deployment mode where oauth2-proxy performs all authentication and the API derives identity from trusted headers, with compose + helm wiring.

**Architecture:** One auth path per deployment. In proxy mode every authenticated request resolves the user from `X-Forwarded-Email` (validated by a shared `X-Proxy-Secret`), JIT-provisions unknown emails, and reuses every downstream permission check unchanged. Local login routes are not registered at all. The frontend detects the mode at runtime via a public `/api/auth/config` endpoint and redirects unauthenticated sessions to oauth2-proxy's `/oauth2/start`.

**Tech Stack:** FastAPI + pydantic v2 (model_validator), SQLAlchemy async + testcontainers, vitest + jsdom, docker-compose overlay (Compose ≥ 2.24 `!reset`), hand-rolled helm templates, oauth2-proxy v7.15.4 (alpha config, nested `claimSource`/`secretSource` — v7.14.0+ syntax).

**Spec:** `docs/superpowers/specs/2026-08-27-oauth2-proxy-auth-design.md` — the spec travels with this plan; executors read both. Section references below (§4, §5.1…) point at the spec.

## Global Constraints

- **Default mode is byte-identical to today.** `AUTH_MODE` defaults to `local`; every existing test stays green unchanged; `docker compose config` output unchanged without the overlay; `helm template` with default values renders exactly one Ingress, byte-identical.
- Environment variable names are fixed: `AUTH_MODE` (`local`|`proxy`), `PROXY_ADMIN_EMAILS` (comma-separated), `PROXY_AUTH_SECRET` (≥ 32 chars, fail-fast via `model_validator(mode="after")` when `AUTH_MODE=proxy`; tests flipping env must call `get_settings.cache_clear()`).
- `get_or_provision_user` lookup is case-insensitive (`func.lower(User.email)`), new rows written lowercased, NO data migration. Emails in `PROXY_ADMIN_EMAILS` reconcile to `admin` on every resolve (never demote). JIT password hash is the unusable literal `"!proxy-no-local-password"`.
- Resolver rejects with 401 `auth_not_authenticated` unless `X-Proxy-Secret` and `X-Forwarded-Email` each appear via `request.headers.getlist(...)` with **exactly one** value (secret constant-time compare, email ≤ 320 chars + `EmailStr`-shaped). Inactive user → 403 `auth_user_disabled`. must-change gate skipped in proxy mode.
- `/api/*` must answer 401 (never a login redirect) in proxy mode: `OAUTH2_PROXY_API_ROUTES=^/api/` (compose), API Ingress without `auth-signin` (helm). Logout goes to `/oauth2/sign_out` **without** `rd`.
- `openapi.json` + `frontend/src/api/types.generated.ts` regenerate in the SAME commit as any `api/schemas.py` change (`cd backend && uv run python scripts/gen_openapi.py`, then `cd frontend && npm run gen:types`). Generated files are never hand-edited.
- oauth2-proxy image pinned `quay.io/oauth2-proxy/oauth2-proxy:v7.15.4`; alpha config uses the nested `claimSource:`/`secretSource:` form (v7.14.0+ — pin and syntax move together); the alpha file is passed with `--alpha-config` (NOT `--config`); `api-route`/`email-domain` remain legacy env options (not on the alpha removed-options list).
- `OAUTH2_PROXY_EMAIL_DOMAINS` is a required security control in the overlay (`:?` interpolation error when unset); never `*` with a public IdP.
- Comments/docstrings English-only (CI-enforced); UI strings via i18n catalogs (zh-TW + en-US, both locales every key); Conventional Commits; README changes update `docs/zh-TW/README.md` in the same PR.
- Backend tests: `cd backend && uv run pytest -q -m "not slow"` (Docker required for testcontainers). Frontend: `cd frontend && npm test && npx tsc -b --noEmit`. Deploy: `docker compose config`, `helm lint deploy/helm/graphrag-ui`, `helm template deploy/helm/graphrag-ui > /dev/null`.
- graphrag stays pinned `==3.1.0`; no new backend/frontend runtime dependencies.
- Frontend proxy-mode fetches use `redirect: "manual"` and treat 401 / `r.type === "opaqueredirect"` / promise rejection as session-expired → `redirectToProxyLogin()` once per page load; 403 `auth_user_disabled` never redirects.

---

### Task 1: Settings — `AUTH_MODE`, proxy vars, fail-fast validator

**Files:**
- Modify: `backend/src/graphrag_ui/config.py`
- Test: `backend/tests/test_proxy_auth.py` (new file)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `Settings.auth_mode: Literal["local","proxy"]` (default `"local"`), `Settings.proxy_admin_emails: str`, `Settings.proxy_auth_secret: str`, `Settings.proxy_admin_set -> frozenset[str]` property (lowercased, stripped). The validator raises `ValueError` when `auth_mode == "proxy"` and `len(proxy_auth_secret) < 32`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_proxy_auth.py`:

```python
"""Proxy-auth mode (spec 2026-08-27): settings, provisioning, resolver, routes."""

import pytest

from graphrag_ui.config import Settings


def test_proxy_mode_requires_32_char_secret():
    with pytest.raises(ValueError):
        Settings(auth_mode="proxy", proxy_auth_secret="short")


def test_proxy_mode_rejects_empty_secret():
    with pytest.raises(ValueError):
        Settings(auth_mode="proxy", proxy_auth_secret="")


def test_local_mode_allows_empty_secret():
    # Default deployments keep today's behavior: no secret needed (spec §4)
    assert Settings(auth_mode="local", proxy_auth_secret="").auth_mode == "local"


def test_proxy_mode_accepts_32_char_secret():
    s = Settings(auth_mode="proxy", proxy_auth_secret="x" * 32)
    assert s.auth_mode == "proxy"


def test_proxy_admin_set_lowercases_strips_and_dedupes():
    s = Settings(proxy_admin_emails="A@Ex.COM, b@ex.com , ,")
    assert s.proxy_admin_set == frozenset({"a@ex.com", "b@ex.com"})


def test_proxy_admin_set_empty_default():
    assert Settings().proxy_admin_set == frozenset()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_proxy_auth.py -v`
Expected: FAIL — `Settings` has no `auth_mode` (pydantic `extra="ignore"` silently drops unknown kwargs, so the attribute access / validator assertions fail).

- [ ] **Step 3: Implement the settings**

In `backend/src/graphrag_ui/config.py`, change the file to:

```python
from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://graphrag:graphrag@localhost:5432/graphrag"
    workspaces_dir: str = "./data/workspaces"
    jwt_secret: str = "dev-secret-change-me"
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    upload_max_file_mb: int = 50
    project_quota_mb: int = 5000
    max_concurrent_jobs: int = 2
    job_log_retention_days: int = 30
    job_log_failed_retention_days: int = 90
    update_output_keep_latest: int = 2
    cache_quota_mb: int = 2048
    disk_watermark_mb: int = 2048
    query_cache_mb: int = 1024
    query_rate_limit_per_hour: int = 30
    auth_mode: Literal["local", "proxy"] = "local"
    proxy_admin_emails: str = ""
    proxy_auth_secret: str = ""

    @property
    def proxy_admin_set(self) -> frozenset[str]:
        """Lowercased PROXY_ADMIN_EMAILS; matching is case-insensitive (spec §9)."""
        return frozenset(
            e.strip().lower() for e in self.proxy_admin_emails.split(",") if e.strip())

    # The shared secret is proxy mode's entire trust anchor (spec §4): unlike
    # a password it is never rate-limited and never rotated, so a weak one is
    # a startup error, not a warning. Fires on the first get_settings() call
    # — reached during create_app() — so a misconfigured container exits
    # before serving a single request with a guessable anchor.
    @model_validator(mode="after")
    def _proxy_mode_needs_strong_secret(self) -> "Settings":
        if self.auth_mode == "proxy" and len(self.proxy_auth_secret) < 32:
            raise ValueError(
                "AUTH_MODE=proxy requires PROXY_AUTH_SECRET >= 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_proxy_auth.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Regression + commit**

Run: `cd backend && uv run pytest -q -m "not slow" && uv run ruff check`
Expected: all green (no behavior changed in local mode).

```bash
git add backend/src/graphrag_ui/config.py backend/tests/test_proxy_auth.py
git commit -m "feat(config): AUTH_MODE/proxy settings with fail-fast secret validator"
```

---

### Task 2: `get_or_provision_user` — case-insensitive JIT + admin reconcile

**Files:**
- Modify: `backend/src/graphrag_ui/services/auth.py`
- Test: `backend/tests/test_proxy_auth.py` (append)

**Interfaces:**
- Consumes: `Settings.proxy_admin_set` (Task 1).
- Produces: `UNUSABLE_PASSWORD_HASH: str` (`"!proxy-no-local-password"`) and
  `async def get_or_provision_user(session: AsyncSession, email: str, display_name: str) -> User` —
  case-insensitive get-or-create; commits on create/promote (services own the transaction boundary); catches `sqlalchemy.exc.IntegrityError` (not `ON CONFLICT` — `services/` stays free of postgres-dialect imports) and re-selects the winner's row; promotes listed emails to `admin` on every call, never demotes; writes audit rows (`user.created` with `payload={"email": ..., "origin": "proxy-jit"}`, `user.role_promoted` with `payload={"via": "proxy_admin_emails"}`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_proxy_auth.py` (after the imports at top add: `import asyncio`, `from sqlalchemy import func, select, text`, and `from graphrag_ui.adapters.db import make_engine, make_session_factory`, `from graphrag_ui.adapters.models import User`, `from graphrag_ui.config import get_settings`, `from graphrag_ui.services.auth import UNUSABLE_PASSWORD_HASH, get_or_provision_user, verify_password`):

```python
# ---- get_or_provision_user (spec §5.2) ----

async def test_provision_new_user_defaults(db_session, monkeypatch):
    monkeypatch.setenv("PROXY_ADMIN_EMAILS", "")
    get_settings.cache_clear()
    user = await get_or_provision_user(db_session, "new@ex.com", "New")
    assert user.email == "new@ex.com"
    assert user.role == "user"
    assert user.is_active and not user.must_change_password
    assert user.display_name == "New"
    # Unusable hash: no password ever verifies against a JIT row (spec §5.2)
    assert user.password_hash == UNUSABLE_PASSWORD_HASH
    assert not verify_password("anything", user.password_hash)


async def test_provision_listed_email_is_admin(db_session, monkeypatch):
    monkeypatch.setenv("PROXY_ADMIN_EMAILS", "root@ex.com")
    get_settings.cache_clear()
    user = await get_or_provision_user(db_session, "Root@EX.com", "Root")
    assert user.role == "admin"
    assert user.email == "root@ex.com"  # stored lowercased


async def test_existing_row_matched_case_insensitively_keeps_role(db_session):
    # create_user stores EmailStr's normalization: local part keeps its case
    db_session.add(User(email="Alice@Example.com", password_hash="x",
                        display_name="Alice", role="admin"))
    await db_session.commit()
    user = await get_or_provision_user(db_session, "alice@example.com", "Alice")
    assert user.role == "admin"  # kept, not duplicated
    assert (await db_session.execute(
        select(func.count()).select_from(User))).scalar_one() == 1


async def test_admin_reconcile_promotes_existing_user(db_session, monkeypatch):
    monkeypatch.setenv("PROXY_ADMIN_EMAILS", "boss@ex.com")
    get_settings.cache_clear()
    db_session.add(User(email="boss@ex.com", password_hash="x", display_name="Boss"))
    await db_session.commit()
    user = await get_or_provision_user(db_session, "boss@ex.com", "Boss")
    assert user.role == "admin"


async def test_admin_reconcile_never_demotes(db_session, monkeypatch):
    monkeypatch.setenv("PROXY_ADMIN_EMAILS", "")
    get_settings.cache_clear()
    db_session.add(User(email="other@ex.com", password_hash="x",
                        display_name="O", role="admin"))
    await db_session.commit()
    user = await get_or_provision_user(db_session, "other@ex.com", "O")
    assert user.role == "admin"


async def test_concurrent_first_provision_single_row(migrated_db, monkeypatch):
    # First page load fires 3-4 parallel requests (spec §5.2): all miss the
    # SELECT, all INSERT, unique index makes all but one raise. The retry
    # must converge on one row with no error surfaced.
    monkeypatch.setenv("PROXY_ADMIN_EMAILS", "")
    get_settings.cache_clear()

    async def one() -> None:
        engine = make_engine(migrated_db)
        factory = make_session_factory(engine)
        async with factory() as s:
            await get_or_provision_user(s, "race@ex.com", "R")
        await engine.dispose()

    await asyncio.gather(*(one() for _ in range(4)))

    engine = make_engine(migrated_db)
    async with engine.begin() as conn:
        n = (await conn.execute(
            text("select count(*) from users where email = 'race@ex.com'"))).scalar_one()
    await engine.dispose()
    assert n == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_proxy_auth.py -v -k provision`
Expected: FAIL — `ImportError: cannot import name 'get_or_provision_user'`.

- [ ] **Step 3: Implement the service**

In `backend/src/graphrag_ui/services/auth.py`:

Add imports (merge with existing):

```python
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from graphrag_ui.services.audit import audit
```

Add after `verify_password` (keep the rest of the module unchanged):

```python
# JIT rows have no usable password: this literal never parses as an argon2
# hash, so verify_password is always False — flipping AUTH_MODE back to
# local cannot let a proxy-provisioned account sign in without a reset
# (spec §5.2).
UNUSABLE_PASSWORD_HASH = "!proxy-no-local-password"


async def get_or_provision_user(
    session: AsyncSession, email: str, display_name: str
) -> User:
    """Case-insensitive get-or-create for proxy-mode identity (spec §5.2).

    Legacy rows may store the local part with its original case (create_user
    keeps EmailStr's normalization, which only lowercases the domain), so the
    lookup lowercases both sides; new rows are written lowercased and the
    data converges without a migration. Concurrent first logins race on the
    users.email unique index; the loser rolls back and returns the winner's
    row — the rollback is safe because identity resolution is the first
    thing touching this request's session.
    """
    addr = email.strip().lower()
    settings = get_settings()

    async def _lookup() -> User | None:
        return (await session.execute(
            select(User).where(func.lower(User.email) == addr))).scalar_one_or_none()

    user = await _lookup()
    if user is None:
        user = User(
            email=addr,
            display_name=display_name,
            password_hash=UNUSABLE_PASSWORD_HASH,
            role="admin" if addr in settings.proxy_admin_set else "user",
            is_active=True,
            must_change_password=False,
        )
        session.add(user)
        try:
            await session.flush()
            await audit(session, user.id, "user.created", "user", str(user.id),
                        payload={"email": addr, "origin": "proxy-jit"})
            await session.commit()
        except IntegrityError:
            # Lost the insert race: the unique-index winner's row is
            # committed (PG blocks our insert until theirs resolves).
            await session.rollback()
            user = await _lookup()
            assert user is not None

    # Authoritative-upward admin reconciliation (spec decision 7): a listed
    # email is promoted whenever it is seen. Only-up means "grant an admin"
    # stays a config change even when the list was set late; the flip side
    # (a listed email cannot be demoted from the UI) is documented intent.
    if user.role != "admin" and addr in settings.proxy_admin_set:
        user.role = "admin"
        await audit(session, user.id, "user.role_promoted", "user", str(user.id),
                    payload={"via": "proxy_admin_emails"})
        await session.commit()
    return user
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_proxy_auth.py -v`
Expected: PASS (all, including Task 1's).

- [ ] **Step 5: Regression + commit**

Run: `cd backend && uv run pytest -q -m "not slow" && uv run ruff check`
Expected: green.

```bash
git add backend/src/graphrag_ui/services/auth.py backend/tests/test_proxy_auth.py
git commit -m "feat(auth): JIT user provisioning with case-insensitive match and admin reconcile"
```

---

### Task 3: `resolve_proxy_user` — trusted-header resolver wired into deps

**Files:**
- Modify: `backend/src/graphrag_ui/api/deps.py`
- Test: `backend/tests/test_proxy_auth.py` (append)

**Interfaces:**
- Consumes: `get_or_provision_user`, `Settings.auth_mode` / `proxy_auth_secret`.
- Produces:
  - `async def resolve_proxy_user(request: Request, db: AsyncSession) -> User` — raises `ApiError` 401 `auth_not_authenticated` / 403 `auth_user_disabled`; **no must-change gate**.
  - `get_current_user(request, creds, db)` and `sse_user_from_request(...)` branch to `resolve_proxy_user` first when `get_settings().auth_mode == "proxy"` (SSE ignores `?token=`/Bearer there).
  - `_test_headers(email, secret)` helper ONLY in tests.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_proxy_auth.py` (add imports: `from starlette.requests import Request`, `from graphrag_ui.api.deps import resolve_proxy_user`):

```python
# ---- resolve_proxy_user (spec §5.1) ----

SECRET = "p" * 40  # >= 32 chars, satisfies the Task 1 validator


def make_request(headers: dict[str, str]) -> Request:
    return Request(scope={
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    })


@pytest.fixture
def proxy_env(monkeypatch):
    """AUTH_MODE=proxy with a valid secret; restores the cached settings after."""
    monkeypatch.setenv("AUTH_MODE", "proxy")
    monkeypatch.setenv("PROXY_AUTH_SECRET", SECRET)
    monkeypatch.setenv("PROXY_ADMIN_EMAILS", "admin@test.local")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_resolver_rejects_missing_secret(db_session, proxy_env):
    from graphrag_ui.api.errors import ApiError
    with pytest.raises(ApiError) as e:
        await resolve_proxy_user(make_request({"X-Forwarded-Email": "a@b.com"}), db_session)
    assert e.value.status_code == 401


async def test_resolver_rejects_wrong_secret(db_session, proxy_env):
    from graphrag_ui.api.errors import ApiError
    with pytest.raises(ApiError):
        await resolve_proxy_user(make_request({
            "X-Proxy-Secret": "nope", "X-Forwarded-Email": "a@b.com"}), db_session)


async def test_resolver_rejects_duplicate_secret(db_session, proxy_env):
    # getlist must see exactly one value; append-vs-replace differences at
    # the edge must fail closed (spec §5.1)
    from graphrag_ui.api.errors import ApiError
    req = Request(scope={
        "type": "http", "method": "GET", "path": "/", "query_string": b"",
        "headers": [(b"x-proxy-secret", SECRET.encode()), (b"x-proxy-secret", SECRET.encode()),
                    (b"x-forwarded-email", b"a@b.com")],
    })
    with pytest.raises(ApiError):
        await resolve_proxy_user(req, db_session)


async def test_resolver_rejects_missing_or_malformed_email(db_session, proxy_env):
    from graphrag_ui.api.errors import ApiError
    with pytest.raises(ApiError):
        await resolve_proxy_user(make_request({"X-Proxy-Secret": SECRET}), db_session)
    with pytest.raises(ApiError):
        await resolve_proxy_user(make_request({
            "X-Proxy-Secret": SECRET, "X-Forwarded-Email": "not-an-email"}), db_session)
    with pytest.raises(ApiError):
        await resolve_proxy_user(make_request({
            "X-Proxy-Secret": SECRET, "X-Forwarded-Email": "x" * 320 + "@ex.com"}), db_session)


async def test_resolver_provisions_and_returns_user(db_session, proxy_env):
    user = await resolve_proxy_user(make_request({
        "X-Proxy-Secret": SECRET,
        "X-Forwarded-Email": "admin@test.local",
        "X-Forwarded-Preferred-Username": "The Admin",
    }), db_session)
    assert user.role == "admin"          # listed in PROXY_ADMIN_EMAILS
    assert user.display_name == "The Admin"


async def test_resolver_display_name_falls_back_to_local_part(db_session, proxy_env):
    user = await resolve_proxy_user(make_request({
        "X-Proxy-Secret": SECRET, "X-Forwarded-Email": "plain@test.local"}), db_session)
    assert user.display_name == "plain"


async def test_resolver_inactive_user_403(db_session, proxy_env):
    from graphrag_ui.api.errors import ApiError
    db_session.add(User(email="gone@test.local", password_hash="x",
                        display_name="G", is_active=False))
    await db_session.commit()
    with pytest.raises(ApiError) as e:
        await resolve_proxy_user(make_request({
            "X-Proxy-Secret": SECRET, "X-Forwarded-Email": "gone@test.local"}), db_session)
    assert e.value.status_code == 403


async def test_resolver_skips_must_change_gate(db_session, proxy_env):
    # A local-mode user stuck at must_change_password must not be locked out
    # after the switch (spec §5.1): the change-password route is gone, so
    # the gate could never be satisfied.
    db_session.add(User(email="stuck@test.local", password_hash="x",
                        display_name="S", must_change_password=True))
    await db_session.commit()
    user = await resolve_proxy_user(make_request({
        "X-Proxy-Secret": SECRET, "X-Forwarded-Email": "stuck@test.local"}), db_session)
    assert user.must_change_password is True  # flag kept, gate skipped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_proxy_auth.py -v -k resolver`
Expected: FAIL — `ImportError: cannot import name 'resolve_proxy_user'`.

- [ ] **Step 3: Implement the resolver**

In `backend/src/graphrag_ui/api/deps.py`:

Add imports:

```python
import hmac
from typing import Annotated

from pydantic import EmailStr, TypeAdapter
```

(merge `Annotated` with the existing `typing` import; `_EMAIL = TypeAdapter(EmailStr)` at module level). Add after `resolve_access_user`:

```python
# Proxy-mode header identity (spec §5.1). The exactly-one rule via getlist
# is deliberate: different oauth2-proxy versions and ingress controllers
# differ on append-vs-replace for injected headers, and a request whose
# identity is ambiguous is a failed request.
_email_adapter = TypeAdapter(EmailStr)


async def resolve_proxy_user(request: Request, db: AsyncSession) -> User:
    """Trusted-header identity for AUTH_MODE=proxy; every failure a 401
    except a disabled account (403, so the SPA shows 'account disabled'
    instead of looping into /oauth2/start)."""
    s = get_settings()
    secrets = request.headers.getlist("X-Proxy-Secret")
    if len(secrets) != 1 or not hmac.compare_digest(secrets[0], s.proxy_auth_secret):
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth_not_authenticated", "Not authenticated")
    emails = request.headers.getlist("X-Forwarded-Email")
    if len(emails) != 1 or len(emails[0]) > 320:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth_not_authenticated", "Not authenticated")
    try:
        email = _email_adapter.validate_python(emails[0])
    except ValidationError:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "auth_not_authenticated", "Not authenticated") from None
    display = (request.headers.get("X-Forwarded-Preferred-Username") or "").strip()[:100]
    user = await get_or_provision_user(db, email, display or email.split("@")[0])
    if not user.is_active:
        raise ApiError(status.HTTP_403_FORBIDDEN, "auth_user_disabled", "account disabled")
    return user
```

Add `from pydantic import ValidationError` and `from graphrag_ui.services.auth import get_or_provision_user` to the imports (mind import cycles: `services.auth` imports config/models only — safe).

Wire the branch into `get_current_user` (first thing in the body, before the Bearer check) and `sse_user_from_request` (before the `?token=` check):

```python
# in get_current_user, at the top:
if get_settings().auth_mode == "proxy":
    return await resolve_proxy_user(request, db)

# in sse_user_from_request, at the top:
# No tokens exist in proxy mode; the EventSource request carries the
# oauth2-proxy cookie, so the injected headers are the only credential.
if get_settings().auth_mode == "proxy":
    return await resolve_proxy_user(request, db)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_proxy_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Regression + commit**

Run: `cd backend && uv run pytest -q -m "not slow" && uv run ruff check`
Expected: green — local mode never enters the new branch (`auth_mode` defaults to `local`).

```bash
git add backend/src/graphrag_ui/api/deps.py backend/tests/test_proxy_auth.py
git commit -m "feat(auth): trusted-header identity resolver for proxy mode"
```

---

### Task 4: Route matrix, `/api/auth/config`, guard/bootstrap skips, contract regen

**Files:**
- Modify: `backend/src/graphrag_ui/api/auth_routes.py`, `backend/src/graphrag_ui/api/schemas.py`, `backend/src/graphrag_ui/api/deps.py` (one line), `backend/src/graphrag_ui/services/auth.py` (bootstrap no-op), `backend/src/graphrag_ui/main.py` (guard registration)
- Modify (generated, same commit): `openapi.json`, `frontend/src/api/types.generated.ts`
- Test: `backend/tests/test_proxy_auth.py` (append), `backend/tests/test_auth.py` (untouched — must stay green)

**Interfaces:**
- Consumes: `resolve_proxy_user` via `CurrentUser`; `Settings.auth_mode`.
- Produces: `GET /api/auth/config` → `{"auth_mode": "local" | "proxy"}` (public, both modes; response model `AuthConfigOut`); in proxy mode ONLY `/api/auth/config` + `/api/auth/me` are registered (login/refresh/logout/change-password → 404); `/api/auth/config` appended to `MUST_CHANGE_ALLOWED_PATHS`; `bootstrap_admin` no-op in proxy mode; must-change middleware not registered in proxy mode.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_proxy_auth.py` (add `from httpx import ASGITransport, AsyncClient`, `from asgi_lifespan import LifespanManager`, `from graphrag_ui.adapters.db import reset_engine`, `from graphrag_ui.main import create_app`):

```python
# ---- route matrix (spec §5.3) ----

@pytest.fixture
async def proxy_client(proxy_env, clean_db, monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "0")
    get_settings.cache_clear()
    await reset_engine()
    app = create_app()
    async with (
        LifespanManager(app) as managed,
        AsyncClient(transport=ASGITransport(app=managed.app), base_url="http://t") as c,
    ):
        yield c
    await reset_engine()


def _hdrs(email="alice@test.local"):
    return {"X-Proxy-Secret": SECRET, "X-Forwarded-Email": email,
            "X-Forwarded-Preferred-Username": "Alice"}


async def test_local_auth_routes_404_in_proxy_mode(proxy_client):
    for path in ("/api/auth/login", "/api/auth/refresh", "/api/auth/logout",
                 "/api/auth/change-password"):
        r = await proxy_client.post(path, json={})
        assert r.status_code == 404, path


async def test_auth_config_reports_mode(proxy_client, client):
    assert (await proxy_client.get("/api/auth/config")).json() == {"auth_mode": "proxy"}
    assert (await client.get("/api/auth/config")).json() == {"auth_mode": "local"}


async def test_me_jits_via_headers(proxy_client, db_session):
    r = await proxy_client.get("/api/auth/me", headers=_hdrs("newbie@test.local"))
    assert r.status_code == 200
    assert r.json()["email"] == "newbie@test.local"


async def test_me_401_without_headers(proxy_client):
    assert (await proxy_client.get("/api/auth/me")).status_code == 401


async def test_sse_route_uses_header_identity(proxy_client):
    # 401 before the 404 job lookup: the auth dependency runs first
    r = await proxy_client.get("/api/jobs/00000000-0000-0000-0000-000000000000/logs")
    assert r.status_code == 401
    r2 = await proxy_client.get("/api/jobs/00000000-0000-0000-0000-000000000000/logs",
                                headers=_hdrs())
    assert r2.status_code == 404  # authenticated, but the job does not exist


async def test_auth_config_reachable_during_must_change(client, db_session):
    # /api/auth/config joins MUST_CHANGE_ALLOWED_PATHS (spec §5.3): a public
    # endpoint answering 403 during the forced-password-change bootstrap is
    # a confusing failure with no diagnostic value.
    from graphrag_ui.adapters.models import User as U
    from graphrag_ui.services.auth import create_access_token
    u = U(email="mc@test.local", password_hash="x", display_name="M",
          must_change_password=True)
    db_session.add(u)
    await db_session.commit()
    h = {"Authorization": f"Bearer {create_access_token(u)}"}
    assert (await client.get("/api/auth/config", headers=h)).status_code == 200
    assert (await client.get("/api/users", headers=h)).status_code == 403  # guard intact
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_proxy_auth.py -v -k "route or auth_config or me_ or sse_route"`
Expected: FAIL — `/api/auth/config` 404s in local mode; login returns 401 (not 404) in proxy mode; must-change blocks config.

- [ ] **Step 3: Implement**

**`api/schemas.py`** — add (near `LoginOut`):

```python
class AuthConfigOut(BaseModel):
    """Runtime auth mode for SPA boot detection (spec §5.3)."""
    auth_mode: Literal["local", "proxy"]
```

**`api/auth_routes.py`** — import `AuthConfigOut` and `get_settings`; restructure `register_auth_routes` so `/config` and `/me` are always registered and the four local-only routes are behind the mode gate:

```python
def register_auth_routes(app):
    # Router built inside the function (like health_routes): create_app() is
    # called repeatedly in tests
    router = APIRouter(prefix="/api/auth")

    @router.get("/config", response_model=AuthConfigOut)
    async def auth_config():
        """Public mode probe: the SPA's single source of truth (spec §5.3)."""
        return AuthConfigOut(auth_mode=get_settings().auth_mode)

    @router.get("/me", response_model=UserOut)
    async def me(user: CurrentUser):
        return UserOut.model_validate(user)

    if get_settings().auth_mode == "proxy":
        # Proxy mode replaces the local login surface entirely (spec §5.3):
        # unregistered routes 404. This is also the first get_settings()
        # call create_app() makes, so the §4 secret validator fires here.
        app.include_router(router)
        return

    @router.post("/login", response_model=LoginOut)
    async def login(body: LoginIn, request: Request, db: DbSession):
        # ... existing body unchanged ...

    # refresh / logout / change-password: existing bodies unchanged
    ...
    app.include_router(router)
```

Keep every existing route body byte-identical — only the registration structure changes.

**`api/deps.py`** — add `"/api/auth/config"` to `MUST_CHANGE_ALLOWED_PATHS` (and update the comment block above it to mention both `main.py`'s guard and `get_current_user` share it — it already says so; just the set changes).

**`services/auth.py`** — first lines of `bootstrap_admin`:

```python
async def bootstrap_admin(session: AsyncSession) -> None:
    if get_settings().auth_mode == "proxy":
        # Proxy mode: the initial admin comes from PROXY_ADMIN_EMAILS JIT
        # (spec §5.2); local login is disabled, so a password-having admin
        # would be unreachable anyway.
        return
    ...existing body...
```

**`main.py`** — in `create_app()`, wrap the `_register_must_change_guard(app)` call:

```python
if get_settings().auth_mode == "local":
    _register_must_change_guard(app)
```

(add `from graphrag_ui.config import get_settings` to main.py imports).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_proxy_auth.py tests/test_auth.py -v`
Expected: PASS — including every pre-existing test in `test_auth.py`, unchanged.

- [ ] **Step 5: Regenerate the contract artifacts (same commit)**

```bash
cd backend && uv run python scripts/gen_openapi.py && git diff --stat ../openapi.json
cd frontend && npm run gen:types && git diff --stat src/api/types.generated.ts
```

Expected: both diffs show exactly the new `/api/auth/config` path + `AuthConfigOut` schema (generation runs without `AUTH_MODE` set → local mode, where login/refresh also appear — spec §5.3).

- [ ] **Step 6: Full regression + commit**

Run: `cd backend && uv run pytest -q -m "not slow" && uv run ruff check`
Expected: green.

```bash
git add backend/src/graphrag_ui/api/auth_routes.py backend/src/graphrag_ui/api/schemas.py \
  backend/src/graphrag_ui/api/deps.py backend/src/graphrag_ui/services/auth.py \
  backend/src/graphrag_ui/main.py backend/tests/test_proxy_auth.py \
  openapi.json frontend/src/api/types.generated.ts
git commit -m "feat(auth): proxy-mode route matrix and public /api/auth/config endpoint"
```

---

### Task 5: Frontend store + client — mode detection, proxy restore, redirect-once

**Files:**
- Modify: `frontend/src/stores/auth.ts`, `frontend/src/api/client.ts`
- Test: `frontend/src/stores/__tests__/auth.test.ts` (append), `frontend/src/api/__tests__/client.test.ts` (new)

**Interfaces:**
- Consumes: `GET /api/auth/config` → `{auth_mode}`; `GET /api/auth/me` (Task 4).
- Produces:
  - `useAuth` state gains `authMode: "local" | "proxy" | null` (null until config resolves; treated as local so local boots are unchanged).
  - `export function redirectToProxyLogin(): void` in `stores/auth.ts` (module-level once-per-page-load guard; `window.location.assign("/oauth2/start?rd=" + encodeURIComponent(location.pathname + location.search))`).
  - `logout()` in proxy mode → `window.location.assign("/oauth2/sign_out")` (no `rd`, no server call).
  - `api()` in client.ts: proxy mode → `fetch(path, { ...init, redirect: "manual" })`, no Authorization, no refresh retry; 401 or `r.type === "opaqueredirect"` or promise rejection → `redirectToProxyLogin()` (rejection re-thrown after).

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/stores/__tests__/auth.test.ts` (match its existing import style; use `vi.resetModules()` + dynamic import so the module-level redirect flag resets between tests):

```typescript
// ---- proxy mode (spec §6.1) ----

function stubLocation() {
  const assign = vi.fn();
  Object.defineProperty(window, "location", {
    value: { ...window.location, assign, pathname: "/projects", search: "?x=1" },
    writable: true,
  });
  return assign;
}

test("proxy restore(): config -> me -> user set, stale refresh token cleared", async () => {
  localStorage.setItem("grui_refresh", "stale");
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
    const url = String(input);
    if (url.includes("/api/auth/config")) {
      return new Response(JSON.stringify({ auth_mode: "proxy" }), { status: 200 });
    }
    if (url.includes("/api/auth/me")) {
      return new Response(JSON.stringify({ email: "a@b.c", role: "user" }), { status: 200 });
    }
    throw new Error("unexpected " + url);
  }));
  vi.resetModules();
  const { useAuth } = await import("../../auth");

  await useAuth.getState().restore();

  expect(useAuth.getState().authMode).toBe("proxy");
  expect(useAuth.getState().user?.email).toBe("a@b.c");
  expect(useAuth.getState().bootstrapping).toBe(false);
  expect(localStorage.getItem("grui_refresh")).toBeNull();
});

test("proxy restore(): 401 from /me redirects to /oauth2/start with rd, once", async () => {
  const assign = stubLocation();
  let meCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
    const url = String(input);
    if (url.includes("/api/auth/config")) {
      return new Response(JSON.stringify({ auth_mode: "proxy" }), { status: 200 });
    }
    meCalls += 1;
    return { ok: false, status: 401, type: "basic", json: async () => ({}) } as unknown as Response;
  }));
  vi.resetModules();
  const { useAuth, redirectToProxyLogin } = await import("../../auth");

  await useAuth.getState().restore();
  redirectToProxyLogin(); // second call: suppressed by the once-guard

  expect(assign).toHaveBeenCalledTimes(1);
  expect(assign).toHaveBeenCalledWith("/oauth2/start?rd=%2Fprojects%3Fx%3D1");
  expect(meCalls).toBe(1);
  expect(useAuth.getState().user).toBeNull();
  expect(useAuth.getState().bootstrapping).toBe(false);
});

test("proxy logout(): navigates to /oauth2/sign_out with no rd and no server call", async () => {
  const assign = stubLocation();
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  vi.resetModules();
  const { useAuth } = await import("../../auth");
  useAuth.setState({ authMode: "proxy", user: { email: "a@b.c" } as never, accessToken: null });

  await useAuth.getState().logout();

  expect(assign).toHaveBeenCalledWith("/oauth2/sign_out");
  expect(fetchMock).not.toHaveBeenCalled();
  expect(localStorage.getItem("grui_refresh")).toBeNull();
});
```

Create `frontend/src/api/__tests__/client.test.ts`:

```typescript
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api } from "../client";
import { useAuth } from "../../stores/auth";

let assign: ReturnType<typeof vi.fn>;
beforeEach(() => {
  assign = vi.fn();
  Object.defineProperty(window, "location", {
    value: { ...window.location, assign, pathname: "/", search: "" },
    writable: true,
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  useAuth.setState({ authMode: "local", accessToken: null, user: null });
});

test("proxy mode: no Authorization header, no refresh, 401 redirects once", async () => {
  const calls: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_p: string, init?: RequestInit) => {
    calls.push(init ?? {});
    return { ok: false, status: 401, type: "basic", json: async () => ({}) } as unknown as Response;
  }));
  useAuth.setState({ authMode: "proxy", accessToken: null });

  const r = await api("/api/projects");

  expect(r.status).toBe(401);
  expect(calls).toHaveLength(1);
  expect(calls[0].headers).toEqual({}); // no Authorization attached
  expect(assign).toHaveBeenCalledTimes(1);
});

test("proxy mode: opaqueredirect response also redirects", async () => {
  vi.stubGlobal("fetch", vi.fn(async () =>
    ({ ok: false, status: 0, type: "opaqueredirect" }) as unknown as Response));
  useAuth.setState({ authMode: "proxy" });

  await api("/api/projects");
  expect(assign).toHaveBeenCalledTimes(1);
});

test("proxy mode: rejected fetch schedules redirect then re-throws", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network down"); }));
  useAuth.setState({ authMode: "proxy" });

  await expect(api("/api/projects")).rejects.toThrow("network down");
  expect(assign).toHaveBeenCalledTimes(1);
});

test("proxy mode: 403 does NOT redirect (account disabled is a normal error)", async () => {
  vi.stubGlobal("fetch", vi.fn(async () =>
    ({ ok: false, status: 403, type: "basic", json: async () => ({}) }) as unknown as Response));
  useAuth.setState({ authMode: "proxy" });

  const r = await api("/api/projects");
  expect(r.status).toBe(403);
  expect(assign).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/stores/__tests__/auth.test.ts src/api/__tests__/client.test.ts`
Expected: FAIL — `authMode` undefined, no redirect behavior, Authorization still attempted.

- [ ] **Step 3: Implement the store**

In `frontend/src/stores/auth.ts`: extend the `AuthState` interface with `authMode: "local" | "proxy" | null`, initialize `authMode: null`, and add:

```typescript
// Proxy-mode sign-in redirect (spec §6.1). Once per page load: a
// valid-but-stale proxy cookie makes /oauth2/start bounce straight back
// and a second redirect would loop start→rd→401→start.
let proxyRedirected = false;
export function redirectToProxyLogin(): void {
  if (proxyRedirected) return;
  proxyRedirected = true;
  window.location.assign(
    "/oauth2/start?rd=" + encodeURIComponent(location.pathname + location.search));
}
```

Rewrite `restore()` (keep the local-mode leg byte-identical including its comments):

```typescript
restore: async () => {
  try {
    const cfgR = await fetch("/api/auth/config", { redirect: "manual" });
    const mode = cfgR.ok
      ? ((await cfgR.json()) as { auth_mode: "local" | "proxy" }).auth_mode
      : "local"; // config unreachable: assume local, existing behavior
    set({ authMode: mode });
    if (mode === "proxy") {
      // No app tokens exist in proxy mode; a stale local-mode refresh token
      // must not linger (spec §6.1)
      localStorage.removeItem(REFRESH_KEY);
      const r = await fetch("/api/auth/me", { redirect: "manual" });
      if (r.status === 401 || r.type === "opaqueredirect") {
        set({ user: null, accessToken: null, bootstrapping: false });
        redirectToProxyLogin();
        return;
      }
      set({ user: r.ok ? await r.json() : null, bootstrapping: false });
      return;
    }
    // ... existing local-mode body unchanged (refreshOnce -> /me) ...
  } catch {
    // ... existing catch unchanged ...
  }
},
```

Add the proxy branch at the top of `logout`:

```typescript
logout: async () => {
  if (useAuth.getState().authMode === "proxy") {
    // Nothing to revoke server-side (no refresh tokens). No `rd`: landing
    // back in the app would silently re-authenticate against the live IdP
    // session (spec decision 6).
    localStorage.removeItem(REFRESH_KEY);
    window.location.assign("/oauth2/sign_out");
    return;
  }
  // ... existing local-mode body unchanged ...
},
```

- [ ] **Step 4: Implement the client**

In `frontend/src/api/client.ts`, add `redirectToProxyLogin` to the existing `../stores/auth` import, then branch at the top of `api()`:

```typescript
if (useAuth.getState().authMode === "proxy") {
  // Proxy mode: no app token. redirect:"manual" keeps an edge login
  // redirect a detectable opaqueredirect signal instead of a CORS
  // TypeError (spec §6.2); a real network rejection also counts as
  // session-expired and is re-thrown after scheduling the redirect.
  try {
    const r = await fetch(path, { ...init, redirect: "manual" });
    if (r.status === 401 || r.type === "opaqueredirect") redirectToProxyLogin();
    return r;
  } catch {
    redirectToProxyLogin();
    throw;
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run && npx tsc -b --noEmit`
Expected: PASS — including the two pre-existing auth tests unchanged.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/stores/auth.ts frontend/src/api/client.ts \
  frontend/src/stores/__tests__/auth.test.ts frontend/src/api/__tests__/client.test.ts
git commit -m "feat(frontend): proxy-mode session boot, redirect-once handling, signout"
```

---

### Task 6: Frontend pages — Login redirect, AdminUsers reset hidden, QueryPanel token, i18n

**Files:**
- Modify: `frontend/src/pages/Login.tsx`, `frontend/src/pages/AdminUsers.tsx`, `frontend/src/components/QueryPanel.tsx`, `frontend/src/i18n/locales/zh-TW.ts`, `frontend/src/i18n/locales/en-US.ts`
- Test: `frontend/src/pages/__tests__/Login.test.tsx` (new), `frontend/src/components/__tests__/QueryPanel.test.tsx` (append), `frontend/src/pages/__tests__/AdminUsers.test.tsx` (new)

**Interfaces:**
- Consumes: `authMode` from `useAuth`, `redirectToProxyLogin` from `stores/auth`.
- Produces: `/login` renders nothing + redirects in proxy mode; AdminUsers hides the reset-password action (`data-testid="reset-password-button"`) in proxy mode; QueryPanel appends `&token=` only when a token exists (local mode); `errors.auth_user_disabled` catalog key in both locales.

- [ ] **Step 1: Write the failing tests**

`frontend/src/pages/__tests__/Login.test.tsx`:

```typescript
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, test, vi } from "vitest";
import Login from "../Login";
import { useAuth } from "../../stores/auth";

test("proxy mode: renders nothing and redirects to /oauth2/start", () => {
  const assign = vi.fn();
  Object.defineProperty(window, "location", {
    value: { ...window.location, assign, pathname: "/login", search: "" },
    writable: true,
  });
  useAuth.setState({ authMode: "proxy" });

  render(<MemoryRouter><Login /></MemoryRouter>);

  expect(screen.queryByRole("button")).toBeNull();
  expect(assign).toHaveBeenCalledWith("/oauth2/start?rd=%2Flogin");
});

test("local mode: renders the password form", () => {
  useAuth.setState({ authMode: "local" });
  render(<MemoryRouter><Login /></MemoryRouter>);
  expect(screen.getByRole("button")).toBeInTheDocument();
});
```

Append to `frontend/src/components/__tests__/QueryPanel.test.tsx` (mirror its existing setup/mocks; the key assertion):

```typescript
test("proxy mode: EventSource URL carries no empty token param", async () => {
  useAuth.setState({ authMode: "proxy", accessToken: null });
  // ... same render/mount preamble as the existing "builds the SSE URL" test ...
  expect(es.url).not.toContain("token=");
});

test("local mode: token still included", async () => {
  useAuth.setState({ authMode: "local", accessToken: "test-token" });
  // ... same preamble ...
  expect(es.url).toContain("token=test-token");
});
```

(Follow the file's existing mock harness — `MockEventSource`, project fetch mocks — the two tests differ only in the store seed.)

`frontend/src/pages/__tests__/AdminUsers.test.tsx`:

```typescript
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import AdminUsers from "../AdminUsers";
import { useAuth } from "../../stores/auth";

const usersBody = JSON.stringify([
  { id: "u1", email: "a@b.c", display_name: "A", role: "user", is_active: true },
]);

function mount() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AdminUsers />
    </QueryClientProvider>,
  );
}

test("proxy mode: reset-password action hidden", async () => {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(usersBody, { status: 200 })));
  useAuth.setState({
    authMode: "proxy", accessToken: null,
    user: { id: "me", email: "me@b.c", role: "admin" } as never,
  });
  mount();
  await waitFor(() => expect(screen.getByText("a@b.c")).toBeInTheDocument());
  expect(screen.queryByTestId("reset-password-button")).toBeNull();
});

test("local mode: reset-password action shown", async () => {
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(usersBody, { status: 200 })));
  useAuth.setState({
    authMode: "local", accessToken: "t",
    user: { id: "me", email: "me@b.c", role: "admin" } as never,
  });
  mount();
  await waitFor(() =>
    expect(screen.getAllByTestId("reset-password-button").length).toBeGreaterThan(0));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/pages/__tests__/Login.test.tsx src/pages/__tests__/AdminUsers.test.tsx src/components/__tests__/QueryPanel.test.tsx`
Expected: FAIL — Login renders the form in proxy mode; reset button has no testid; QueryPanel appends `token=`.

- [ ] **Step 3: Implement**

**`Login.tsx`** — add at the top of the component (imports: `useEffect` from react, `redirectToProxyLogin` from `../stores/auth`):

```tsx
const authMode = useAuth((s) => s.authMode);
useEffect(() => {
  if (authMode === "proxy") redirectToProxyLogin();
}, [authMode]);
if (authMode === "proxy") return null; // the local form never shows (spec §6.3)
```

**`AdminUsers.tsx`** — read the mode next to the existing `const { user: me } = useAuth();` (add `authMode` to that selector or a second `useAuth` call), then change the reset button (currently line ~126) to:

```tsx
{authMode !== "proxy" && (
  <Button size="small" data-testid="reset-password-button"
          onClick={() => setResetTarget(u)}>{t("adminUsers.resetPassword")}</Button>
)}
```

(Role and active toggles stay — proxy mode manages roles normally, spec §5.4.)

**`QueryPanel.tsx`** — change the URL builder's last line from
`` `&token=${encodeURIComponent(token ?? "")}`; `` to JobLogViewer's conditional form (spec §6.4):

```typescript
const url =
  `/api/projects/${pid}/query/stream?...` +
  `&response_type=${encodeURIComponent(RESPONSE_TYPE)}` +
  (token ? `&token=${encodeURIComponent(token)}` : "");
```

(keep the existing leading segments exactly as they are; only the token segment becomes conditional).

**i18n locales** — in `errors` section of both files add:

```typescript
// zh-TW.ts
auth_user_disabled: "此帳號已停用",
// en-US.ts
auth_user_disabled: "This account is disabled",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run && npx tsc -b --noEmit`
Expected: PASS (the pre-existing QueryPanel test asserting `token=test-token` stays green because it seeds a token).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Login.tsx frontend/src/pages/AdminUsers.tsx \
  frontend/src/components/QueryPanel.tsx frontend/src/i18n/locales/zh-TW.ts \
  frontend/src/i18n/locales/en-US.ts frontend/src/pages/__tests__/Login.test.tsx \
  frontend/src/pages/__tests__/AdminUsers.test.tsx \
  frontend/src/components/__tests__/QueryPanel.test.tsx
git commit -m "feat(frontend): proxy-mode page behaviors and disabled-account error copy"
```

---

### Task 7: docker-compose overlay + .env.example + AGENTS.md + CI

**Files:**
- Create: `docker-compose.proxy-auth.yml`
- Modify: `.env.example`, `AGENTS.md`, `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `AUTH_MODE=proxy` etc. (Tasks 1–4), oauth2-proxy alpha config format.
- Produces: `docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml up` runs oauth2-proxy as the only published service (8080→4180) in front of `web`; `web` unpublished via `ports: !reset []`; api gets `AUTH_MODE=proxy`; alpha config injects `X-Forwarded-Email`, `X-Forwarded-Preferred-Username`, `X-Proxy-Secret`; `/api/*` answers 401 (`OAUTH2_PROXY_API_ROUTES=^/api/`).

- [ ] **Step 1: Write the overlay**

Create `docker-compose.proxy-auth.yml` with EXACTLY this content:

```yaml
# Opt-in OAuth2-Proxy authentication (docs/superpowers/specs/2026-08-27
# -oauth2-proxy-auth-design.md §7.1).
# Usage: docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml up
# Requires Compose >= 2.24 (the !reset tag) and these .env vars:
#   PROXY_AUTH_SECRET (>= 32 chars; openssl rand -hex 32)
#   OAUTH2_PROXY_ISSUER_URL / CLIENT_ID / CLIENT_SECRET / COOKIE_SECRET /
#   REDIRECT_URL / EMAIL_DOMAINS (allowlist — REQUIRED, never "*" with a
#   public IdP: JIT provisioning would let anyone self-register, spec §7.1)
services:
  api:
    environment:
      AUTH_MODE: proxy
      PROXY_ADMIN_EMAILS: ${PROXY_ADMIN_EMAILS:-}
      PROXY_AUTH_SECRET: ${PROXY_AUTH_SECRET:?set in .env (>= 32 chars)}
  web:
    # Only the auth service stays published: direct access to nginx would
    # bypass oauth2-proxy. Belt-and-suspenders — the api still requires the
    # X-Proxy-Secret that path never carries.
    ports: !reset []
  auth:
    image: quay.io/oauth2-proxy/oauth2-proxy:v7.15.4
    # alpha config MUST be passed with --alpha-config (--config is legacy
    # TOML and rejects this YAML); api-route / email-domain are legacy env
    # options and stay valid alongside it (not on the removed-options list)
    command: ["--alpha-config=/etc/oauth2-proxy/alpha-config.yaml"]
    ports: ["8080:4180"]
    depends_on: [web]
    environment:
      OAUTH2_PROXY_HTTP_ADDRESS: 0.0.0.0:4180
      # /api/* answers 401 instead of a login redirect so the SPA's fetch
      # layer can react (spec decision 5); browser routes keep the redirect
      OAUTH2_PROXY_API_ROUTES: ^/api/
      OAUTH2_PROXY_EMAIL_DOMAINS: ${OAUTH2_PROXY_EMAIL_DOMAINS:?allowlist required — never * with a public IdP}
      OAUTH2_PROXY_COOKIE_SECRET: ${OAUTH2_PROXY_COOKIE_SECRET:?set in .env}
      OAUTH2_PROXY_REDIRECT_URL: ${OAUTH2_PROXY_REDIRECT_URL:?set in .env}
      OAUTH2_PROXY_SKIP_PROVIDER_BUTTON: "true"
    configs:
      - source: oauth2proxy_alpha
        target: /etc/oauth2-proxy/alpha-config.yaml
    secrets:
      - source: proxy_auth_secret
        target: proxy_auth_secret
configs:
  oauth2proxy_alpha:
    # Compose interpolates ${...} from .env into this content (single
    # interpolation layer — no ${} sequences are left for oauth2-proxy's
    # own envsubst). Nested claimSource/secretSource form requires
    # oauth2-proxy >= v7.14.0; move it with the image pin.
    content: |
      upstreamConfig:
        upstreams:
          - id: web
            path: /
            uri: http://web:80
      providers:
        - id: primary
          provider: ${OAUTH2_PROXY_PROVIDER:-oidc}
          clientID: ${OAUTH2_PROXY_CLIENT_ID:?set in .env}
          clientSecret: ${OAUTH2_PROXY_CLIENT_SECRET:?set in .env}
          oidcConfig:
            issuerURL: ${OAUTH2_PROXY_ISSUER_URL:?set in .env}
      injectRequestHeaders:
        - name: X-Forwarded-Email
          values: [{ claimSource: { claim: email } }]
        - name: X-Forwarded-Preferred-Username
          values: [{ claimSource: { claim: preferred_username } }]
        - name: X-Proxy-Secret
          values: [{ secretSource: { fromFile: /run/secrets/proxy_auth_secret } }]
secrets:
  proxy_auth_secret:
    environment: PROXY_AUTH_SECRET
```

- [ ] **Step 2: Extend `.env.example`**

Append (note `OAUTH2_PROXY_EMAIL_DOMAINS` ships **uncommented with a placeholder** per spec §7.1):

```bash

# ---- OAuth2-Proxy mode (optional; spec 2026-08-27 §7.1) ----
# Enable with: docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml up
# AUTH_MODE=proxy disables local login entirely; identity comes from
# oauth2-proxy headers.
#AUTH_MODE=proxy
# Comma-separated; emails held at role=admin (spec §5.2) — remove from the
# list before demoting them in the UI.
#PROXY_ADMIN_EMAILS=
# Shared secret injected by oauth2-proxy; >= 32 chars. Generate:
#   openssl rand -hex 32
#PROXY_AUTH_SECRET=
# oauth2-proxy provider settings (used only by the proxy-auth overlay)
#OAUTH2_PROXY_PROVIDER=oidc
#OAUTH2_PROXY_ISSUER_URL=https://idp.example.com/realms/main
#OAUTH2_PROXY_CLIENT_ID=graphrag-ui
#OAUTH2_PROXY_CLIENT_SECRET=
# 16/24/32 bytes, base64: openssl rand -base64 32 | tr -d '\n'
#OAUTH2_PROXY_COOKIE_SECRET=
#OAUTH2_PROXY_REDIRECT_URL=http://localhost:8080/oauth2/callback
# REQUIRED allowlist — never "*" with a public IdP: JIT provisioning would
# let anyone self-register (spec §7.1)
OAUTH2_PROXY_EMAIL_DOMAINS=example.com
```

- [ ] **Step 3: AGENTS.md + CI**

In `AGENTS.md`: append `AUTH_MODE`, `PROXY_ADMIN_EMAILS`, `PROXY_AUTH_SECRET` to the fixed environment-variable list, and add under Commands → deploy checks:

```bash
docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml config   # needs the proxy .env vars
```

In `.github/workflows/ci.yml` (docker job, after the existing `docker compose config` step): the overlay check needs the required vars — set them inline:

```yaml
      - run: docker compose config
      - name: proxy-auth overlay compose config
        env:
          PROXY_AUTH_SECRET: ci-secret-0123456789abcdef0123456789abcdef
          PROXY_ADMIN_EMAILS: admin@example.com
          OAUTH2_PROXY_ISSUER_URL: https://idp.example.com
          OAUTH2_PROXY_CLIENT_ID: graphrag-ui
          OAUTH2_PROXY_CLIENT_SECRET: ci
          OAUTH2_PROXY_COOKIE_SECRET: Y2ktY29va2llLXNlY3JldC0zMg==
          OAUTH2_PROXY_REDIRECT_URL: http://localhost:8080/oauth2/callback
        run: docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml config
```

- [ ] **Step 4: Verify**

```bash
docker compose config > /tmp/default.yml   # must succeed, unchanged vs main (git stash if needed)
export PROXY_AUTH_SECRET=local-secret-0123456789abcdef0123456789abcdef
export OAUTH2_PROXY_ISSUER_URL=https://idp.example.com OAUTH2_PROXY_CLIENT_ID=ci
export OAUTH2_PROXY_CLIENT_SECRET=ci OAUTH2_PROXY_COOKIE_SECRET=Y2ktY29va2llLXNlY3JldC0zMg==
export OAUTH2_PROXY_REDIRECT_URL=http://localhost:8080/oauth2/callback OAUTH2_PROXY_EMAIL_DOMAINS=example.com
docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml config
```

Expected: first config output byte-identical to `main`'s; second succeeds with `auth` publishing 8080, `web` with no ports, `api` env carrying `AUTH_MODE=proxy`.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.proxy-auth.yml .env.example AGENTS.md .github/workflows/ci.yml
git commit -m "feat(deploy): opt-in oauth2-proxy compose overlay for proxy auth"
```

---

### Task 8: helm — proxyAuth values, oauth2-proxy templates, split Ingress, api env

**Files:**
- Create: `deploy/helm/graphrag-ui/templates/oauth2-proxy.yaml`
- Modify: `deploy/helm/graphrag-ui/values.yaml`, `templates/ingress.yaml`, `templates/api-deployment.yaml`, `templates/_helpers.tpl`, `templates/NOTES.txt`

**Interfaces:**
- Consumes: `AUTH_MODE=proxy` api behavior; nginx-ingress external-auth (`auth-url`/`auth-signin`/`auth-response-headers`).
- Produces: `proxyAuth.*` values; helper `graphrag-ui.proxyAuthSecretName`; when `proxyAuth.enabled` and no `proxyAuth.external.url`: Secret + ConfigMap + Deployment + Service `{{ include "graphrag-ui.fullname" . }}-oauth2-proxy` (port 80→4180); three Ingress objects (api `/api` without `auth-signin`, app `/` with it, `/oauth2` unguarded → proxy service); api deployment gains `AUTH_MODE`/`PROXY_ADMIN_EMAILS`/`PROXY_AUTH_SECRET`. Defaults render byte-identical single Ingress.

- [ ] **Step 1: values.yaml**

Append to `deploy/helm/graphrag-ui/values.yaml`:

```yaml
proxyAuth:
  enabled: false        # OAuth2-Proxy in front of the app (spec 2026-08-27 §7.2);
                        # requires ingress.enabled and a concrete ingress.host
  external:
    url: ""             # e.g. https://sso.example.com — reuse a cluster-wide oauth2-proxy; chart ships none of its own
  provider: oidc
  issuerUrl: ""         # OIDC issuer URL
  clientId: ""
  clientSecret: ""      # plaintext fallbacks used only when existingSecret is empty;
  cookieSecret: ""      # all three feed the chart's proxy-auth Secret keys
  authSecret: ""        # PROXY_AUTH_SECRET — >= 32 chars, validated by the api at startup
  existingSecret: ""    # when set, must contain: client-secret, cookie-secret, proxy-auth-secret
  adminEmails: []       # PROXY_ADMIN_EMAILS — held at role=admin (spec §5.2)
  emailDomains: []      # REQUIRED when enabled (spec §7.1); ["*"] = open registration
  image: quay.io/oauth2-proxy/oauth2-proxy:v7.15.4
  resources: {}
```

- [ ] **Step 2: helper**

Append to `templates/_helpers.tpl`:

```yaml
{{/* Secret holding oauth2-proxy material: operator-provided or chart-created (spec §7.2) */}}
{{- define "graphrag-ui.proxyAuthSecretName" -}}
{{- if .Values.proxyAuth.existingSecret -}}{{ .Values.proxyAuth.existingSecret }}{{- else -}}{{ include "graphrag-ui.fullname" . }}-proxy-auth{{- end -}}
{{- end -}}
```

- [ ] **Step 3: oauth2-proxy templates**

Create `templates/oauth2-proxy.yaml`:

```yaml
{{- if and .Values.proxyAuth.enabled (not .Values.proxyAuth.external.url) }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "graphrag-ui.proxyAuthSecretName" . }}
  labels:
    {{- include "graphrag-ui.labels" . | nindent 4 }}
{{- if not .Values.proxyAuth.existingSecret }}
type: Opaque
stringData:
  client-secret: {{ .Values.proxyAuth.clientSecret | quote }}
  cookie-secret: {{ .Values.proxyAuth.cookieSecret | quote }}
  proxy-auth-secret: {{ .Values.proxyAuth.authSecret | quote }}
{{- end }}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "graphrag-ui.fullname" . }}-oauth2-proxy
  labels:
    {{- include "graphrag-ui.labels" . | nindent 4 }}
data:
  # ${...} below is expanded by oauth2-proxy's own envsubst from the
  # container env — helm does NOT interpolate ${} (only {{ }}).
  alpha-config.yaml: |
    # external-auth mode never proxies, but the alpha schema requires an
    # upstream; a static 200 is the documented pattern
    upstreamConfig:
      upstreams:
        - id: static
          path: /
          static: true
          staticCode: 200
    providers:
      - id: primary
        provider: {{ .Values.proxyAuth.provider }}
        clientID: {{ .Values.proxyAuth.clientId | quote }}
        clientSecret: ${CLIENT_SECRET}
        oidcConfig:
          issuerURL: {{ .Values.proxyAuth.issuerUrl | quote }}
    # nginx-ingress copies these from oauth2-proxy's auth RESPONSE onto the
    # request to the api (auth-response-headers), overwriting any forged
    # values (spec §7.2)
    injectResponseHeaders:
      - name: X-Forwarded-Email
        values: [{ claimSource: { claim: email } }]
      - name: X-Forwarded-Preferred-Username
        values: [{ claimSource: { claim: preferred_username } }]
      - name: X-Proxy-Secret
        values: [{ secretSource: { fromFile: /etc/oauth2-proxy/proxy-auth-secret } }]
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "graphrag-ui.fullname" . }}-oauth2-proxy
  labels:
    {{- include "graphrag-ui.labels" . | nindent 4 }}
    app.kubernetes.io/component: oauth2-proxy
spec:
  replicas: 1
  selector:
    matchLabels:
      {{- include "graphrag-ui.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: oauth2-proxy
  template:
    metadata:
      labels:
        {{- include "graphrag-ui.selectorLabels" . | nindent 8 }}
        app.kubernetes.io/component: oauth2-proxy
    spec:
      containers:
        - name: oauth2-proxy
          image: {{ .Values.proxyAuth.image | quote }}
          args: ["--alpha-config=/etc/oauth2-proxy/alpha-config.yaml"]
          ports:
            - name: http
              containerPort: 4180
          env:
            - name: CLIENT_SECRET
              valueFrom:
                secretKeyRef: { name: {{ include "graphrag-ui.proxyAuthSecretName" . }}, key: client-secret }
            - name: OAUTH2_PROXY_HTTP_ADDRESS
              value: 0.0.0.0:4180
            - name: OAUTH2_PROXY_COOKIE_SECRET
              valueFrom:
                secretKeyRef: { name: {{ include "graphrag-ui.proxyAuthSecretName" . }}, key: cookie-secret }
            # /api/* must 401, not redirect (spec decision 5)
            - name: OAUTH2_PROXY_API_ROUTES
              value: ^/api/
            - name: OAUTH2_PROXY_EMAIL_DOMAINS
              value: {{ join "," .Values.proxyAuth.emailDomains | quote }}
            - name: OAUTH2_PROXY_SKIP_PROVIDER_BUTTON
              value: "true"
          volumeMounts:
            - name: alpha-config
              mountPath: /etc/oauth2-proxy/alpha-config.yaml
              subPath: alpha-config.yaml
            - name: proxy-auth-secret
              mountPath: /etc/oauth2-proxy/proxy-auth-secret
              subPath: proxy-auth-secret
          resources:
            {{- toYaml .Values.proxyAuth.resources | nindent 12 }}
      volumes:
        - name: alpha-config
          configMap:
            name: {{ include "graphrag-ui.fullname" . }}-oauth2-proxy
        - name: proxy-auth-secret
          secret:
            secretName: {{ include "graphrag-ui.proxyAuthSecretName" . }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ include "graphrag-ui.fullname" . }}-oauth2-proxy
  labels:
    {{- include "graphrag-ui.labels" . | nindent 4 }}
    app.kubernetes.io/component: oauth2-proxy
spec:
  selector:
    {{- include "graphrag-ui.selectorLabels" . | nindent 6 }}
    app.kubernetes.io/component: oauth2-proxy
  ports:
    - name: http
      port: 80
      targetPort: 4180
{{- end }}
```

- [ ] **Step 4: ingress split**

Rewrite `templates/ingress.yaml` so the disabled case renders byte-identical to today (same names, annotations, order), and `proxyAuth.enabled` splits it:

```
{{- if .Values.ingress.enabled }}
{{- $auth := dict "url" "" "signin" "" }}
{{- if .Values.proxyAuth.enabled }}
  {{- if .Values.proxyAuth.external.url }}
    {{- $_ := set $auth "url" (printf "%s/oauth2/auth" .Values.proxyAuth.external.url) }}
    {{- $_ := set $auth "signin" (printf "%s/oauth2/start?rd=$escaped_request_uri" .Values.proxyAuth.external.url) }}
  {{- else }}
    {{- $_ := set $auth "url" (printf "http://%s-oauth2-proxy.%s.svc.cluster.local/oauth2/auth" (include "graphrag-ui.fullname" .) .Release.Namespace) }}
    {{- $_ := set $auth "signin" (printf "https://%s/oauth2/start?rd=$escaped_request_uri" .Values.ingress.host) }}
  {{- end }}
{{- end }}
{{- $sse := dict "nginx.ingress.kubernetes.io/proxy-buffering" "off" "nginx.ingress.kubernetes.io/proxy-read-timeout" "3600" }}
{{- $authAnnotations := dict }}
{{- if .Values.proxyAuth.enabled }}
  {{- $authAnnotations = dict "nginx.ingress.kubernetes.io/auth-url" $auth.url "nginx.ingress.kubernetes.io/auth-response-headers" "X-Forwarded-Email,X-Forwarded-Preferred-Username,X-Proxy-Secret" }}
{{- end }}
```

Then render (a) when NOT `proxyAuth.enabled`: today's single Ingress, unchanged (`{{ $sse }}` + `ingress.annotations` merged as now — keep the existing template body for this branch verbatim); (b) when enabled, THREE objects:

1. `<fullname>-api` — paths: `/api` → `<fullname>-api` service; annotations `$sse` + `$authAnnotations` (NO `auth-signin`; the api answers 401 for fetch clients, spec decision 5).
2. `<fullname>` — path `/` → `<fullname>-web`; annotations `$sse` + `$authAnnotations` + `nginx.ingress.kubernetes.io/auth-signin: {{ $auth.signin | quote }}` (browser navigation still gets the interactive login) + user `ingress.annotations`.
3. `<fullname>-oauth2-proxy` — ONLY when internal (no `external.url`): path `/oauth2` → `<fullname>-oauth2-proxy` service; NO auth annotations (its own login endpoints must stay reachable); `ingress.className` shared.

All three carry `ingressClassName` when set and the standard labels.

- [ ] **Step 5: api deployment env + NOTES**

In `templates/api-deployment.yaml`, after the existing env entries:

```yaml
            {{- if .Values.proxyAuth.enabled }}
            - name: AUTH_MODE
              value: proxy
            - name: PROXY_ADMIN_EMAILS
              value: {{ join "," .Values.proxyAuth.adminEmails | quote }}
            - name: PROXY_AUTH_SECRET
              valueFrom:
                secretKeyRef: { name: {{ include "graphrag-ui.proxyAuthSecretName" . }}, key: proxy-auth-secret }
            {{- end }}
```

In `templates/NOTES.txt`, add a `{{- if .Values.proxyAuth.enabled }}` block noting the oauth2-proxy service, the required `emailDomains` allowlist, and that `PROXY_ADMIN_EMAILS` is the break-glass admin grant.

- [ ] **Step 6: Verify all three renderings**

```bash
helm lint deploy/helm/graphrag-ui && helm template deploy/helm/graphrag-ui > /dev/null
helm template deploy/helm/graphrag-ui \
  --set ingress.enabled=true --set ingress.host=gr.example.com \
  --set proxyAuth.enabled=true \
  --set proxyAuth.issuerUrl=https://idp.example.com \
  --set proxyAuth.clientId=cid --set proxyAuth.clientSecret=cs \
  --set proxyAuth.cookieSecret=Y29va2ll --set proxyAuth.authSecret=a-very-long-proxy-auth-secret-value \
  --set proxyAuth.adminEmails={a@ex.com} --set proxyAuth.emailDomains={example.com} \
  > /tmp/pa.yaml && grep -c "kind: Ingress" /tmp/pa.yaml
helm template deploy/helm/graphrag-ui \
  --set ingress.enabled=true --set ingress.host=gr.example.com \
  --set proxyAuth.enabled=true --set proxyAuth.external.url=https://sso.example.com \
  > /tmp/pax.yaml && grep -c "kind: Ingress" /tmp/pax.yaml
git stash && helm template deploy/helm/graphrag-ui --set ingress.enabled=true > /tmp/before.yaml && git stash pop
helm template deploy/helm/graphrag-ui --set ingress.enabled=true > /tmp/after.yaml && diff /tmp/before.yaml /tmp/after.yaml
```

Expected: lint clean; internal = 3 Ingress objects (and `auth-signin` appears exactly once, on the app Ingress — verify `grep -c auth-signin /tmp/pa.yaml` → 1); external = 2; default diff empty.

- [ ] **Step 7: Commit**

```bash
git add deploy/helm/graphrag-ui
git commit -m "feat(helm): optional oauth2-proxy deployment and split external-auth ingress"
```

---

### Task 9: README + zh-TW mirror, final sweep, manual smoke runbook

**Files:**
- Modify: `README.md`, `docs/zh-TW/README.md` (mirror, same PR)

**Interfaces:**
- Consumes: everything above.
- Produces: operator documentation: enabling proxy auth (compose overlay + helm values), secret generation one-liners, `email_domains` requirement, mode-switching caveats (proxy→local needs password resets; must-change users unlocked on local→proxy), admin-list semantics (only-up, remove-before-demote), IdP email-change handling.

- [ ] **Step 1: README section**

Add an "OAuth2-Proxy authentication (optional)" section covering, in order: what `AUTH_MODE=proxy` changes (local login 404s, header identity, JIT + admin list); compose overlay usage with a minimal `.env` example (including `openssl rand -hex 32` for `PROXY_AUTH_SECRET`, base64 32-byte cookie secret); helm `proxyAuth` values (internal and `external.url` variants); the `OAUTH2_PROXY_EMAIL_DOMAINS` warning verbatim from spec §7.1; caveats from spec §9 (mode switching, admin list, IdP email changes, special-use domains). Add the same content in zh-TW to `docs/zh-TW/README.md`.

- [ ] **Step 2: Manual smoke runbook (document in README, execute if an IdP is available)**

Record these as the verification procedure for the §7/§8 assumptions (run against the overlay with a real IdP):

1. Anonymous: `curl -i http://localhost:8080/api/auth/me` → **401** (not 302 — `api_routes` working).
2. Browser `http://localhost:8080/` → IdP login → app boots; `/api/auth/me` shows the JIT-provisioned user.
3. Forged bypass: `docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml exec web curl -i -H "X-Forwarded-Email: admin@x" http://api:8000/api/auth/me` → **401** (no secret).
4. Duplicate-header replace semantics: through the front door, send a duplicate `X-Forwarded-Email` header → response is still 200 with ONE consistent identity (oauth2-proxy replaced, not appended).
5. SSE: run a query stream; frames flow through auth→web→api without stalling.
6. UI logout → lands on oauth2-proxy's own sign-in page (no auto re-login).

- [ ] **Step 3: Full verification sweep**

```bash
cd backend && uv run pytest -q -m "not slow" && uv run ruff check \
  && uv run python scripts/gen_openapi.py && git diff --exit-code ../openapi.json
cd ../frontend && npm test && npx tsc -b --noEmit && npm run build \
  && npm run gen:types && git diff --exit-code src/api/types.generated.ts
cd .. && docker compose config \
  && docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml config
helm lint deploy/helm/graphrag-ui && helm template deploy/helm/graphrag-ui > /dev/null
```

Expected: every command exits 0.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/zh-TW/README.md
git commit -m "docs: oauth2-proxy optional auth deployment guide (en + zh-TW)"
```

---

## Self-Review

- **Spec coverage:** §4 → Task 1; §5.1 → Task 3; §5.2 → Task 2 (reconcile + concurrency + case) & Task 3 (display_name); §5.3 → Task 4 (route matrix, config endpoint, MUST_CHANGE_ALLOWED_PATHS, bootstrap/guard skips); §5.4 → untouched by design (AdminUsers reset hidden UI-side in Task 6); §5.5 → no-op confirmed (probes never traverse ingress); §5.6 → Task 6 i18n + Task 3 code; §6.1 → Task 5; §6.2 → Task 5; §6.3 → Task 6; §6.4 → Task 6 (QueryPanel; JobLogViewer already conditional; mid-stream limitation recorded, not solved — spec); §7.1 → Task 7; §7.2 → Task 8; §8 → enforced by Tasks 3/7/8 assertions + runbook; §9 → covered by Tasks 2/3/4 tests + Task 9 docs; §10 → test bullets map 1:1 to Tasks 2–8 test steps, helm two-Ingress assertion in Task 8 Step 6, manual smoke in Task 9 Step 2; §11 → Tasks 7 (AGENTS.md), 4 (openapi/types), 9 (README pair).
- **Placeholder scan:** QueryPanel/AdminUsers test steps reference "same preamble as existing test" — the preamble exists verbatim in the named files (QueryPanel.test.tsx already builds `es.url` assertions; AdminUsers mount harness is fully written). No TBDs; every code step has content.
- **Type consistency:** `get_or_provision_user(session, email, display_name)` used identically in Tasks 2/3; `resolve_proxy_user(request, db)` matches Task 3/4 usage; `redirectToProxyLogin()` exported from `stores/auth.ts` and imported by `client.ts`/`Login.tsx`; `AuthConfigOut(auth_mode=...)` matches both route and tests; secret constant `"p" * 40` (tests) ≥ the 32-char floor; `UNUSABLE_PASSWORD_HASH` literal consistent across Task 2 code and tests.
