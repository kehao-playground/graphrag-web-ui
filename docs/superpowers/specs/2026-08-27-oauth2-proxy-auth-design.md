# OAuth2-Proxy Authentication (Optional Deployment Mode) Design

Date: 2026-08-27
Status: approved in chat; pending implementation plan.

User decisions (2026-08-27):

1. Integration model: **pure header auth** — no application JWTs in
   proxy mode; every request's identity comes from oauth2-proxy
   injected headers.
2. Provisioning: **JIT auto-create**; `PROXY_ADMIN_EMAILS` grants the
   admin role at first provisioning; roles managed afterwards via the
   existing AdminUsers page.
3. When `AUTH_MODE=proxy`, local email/password login is **fully
   disabled** (routes not registered → 404). Single auth path.
4. Deployment wiring: **both** docker-compose and the helm chart.

## 1. Problem

The console authenticates users with local email/password accounts and
self-issued JWTs (access 15 min + rotating refresh 7 d). Teams that
already run an OIDC provider (Google, GitHub, Azure Entra, Keycloak…)
must maintain a second credential set for this app. We want an optional
deployment mode where an oauth2-proxy in front of the app performs all
authentication and the API derives identity from trusted headers.

The mode must be opt-in: default deployments keep today's behavior
byte-for-byte.

## 2. Goals

- `AUTH_MODE=proxy`: every authenticated API call resolves the user
  from `X-Forwarded-Email` (+ `X-Forwarded-Preferred-Username` for
  display name) injected by oauth2-proxy; no local tokens exist.
- First-seen emails are JIT-provisioned as `User` rows; `role` =
  `admin` when the email is listed in `PROXY_ADMIN_EMAILS`, else
  `user`. Existing rows (including ones created in local mode) keep
  their role and settings — local→proxy migration is seamless.
- Header trust is anchored to a shared secret (`PROXY_AUTH_SECRET`)
  that only oauth2-proxy and the API know; a client that bypasses
  oauth2-proxy (e.g. hitting the web nginx directly with forged
  `X-Forwarded-*`) cannot authenticate.
- Frontend detects the mode at runtime (`GET /api/auth/config`), boots
  without tokens, redirects unauthenticated sessions to
  `/oauth2/start`, and signs out via `/oauth2/sign_out`.
- docker-compose: opt-in overlay file adds an oauth2-proxy service as
  the single published entrypoint; default `docker compose up` is
  unchanged.
- helm: `proxyAuth.enabled` ships an oauth2-proxy instance (official
  chart as dependency, like the vendored postgresql chart) and wires
  ingress external-auth annotations; `proxyAuth.external.url` instead
  reuses a cluster-wide oauth2-proxy via annotations only.
- Full local-mode behavior (login, refresh rotation, SSE `?token=`,
  rate limiting, must-change-password) unchanged; all existing tests
  stay green.

## 3. Non-goals

- Running both auth modes simultaneously (rejected in decisions —
  single auth path per deployment).
- Group/claim-based role mapping beyond the bootstrap admin list
  (AdminUsers remains the role management UI; `X-Forwarded-Groups` is
  ignored).
- Logout from the upstream IdP's own session (oauth2-proxy
  `/oauth2/sign_out` clears the proxy cookie; whether the IdP session
  ends is the operator's `--whitelist-domain`/provider configuration).
- Migrating JIT accounts back to local passwords automatically; after
  a proxy→local switch an admin must reset those passwords (documented
  in §9).
- Authorizing non-browser clients (CLI/API keys). The SPA is the only
  supported client surface in proxy mode.

## 4. Configuration

New environment variables (added to the fixed list in AGENTS.md,
`.env.example`, docker-compose api service, and the helm api
deployment):

| Variable | Default | Meaning |
|---|---|---|
| `AUTH_MODE` | `local` | `local` = today's JWT auth; `proxy` = header auth. |
| `PROXY_ADMIN_EMAILS` | *(empty)* | Comma-separated emails provisioned with `role=admin` at first login. |
| `PROXY_AUTH_SECRET` | *(empty)* | Shared secret required on every authenticated request in proxy mode. **Required** when `AUTH_MODE=proxy` — startup fails fast if missing. |

`Settings` gains `auth_mode: Literal["local", "proxy"]`,
`proxy_admin_emails: str`, `proxy_auth_secret: str`
(`backend/src/graphrag_ui/config.py`). `PROXY_ADMIN_EMAILS` parses to
a lowercased set once at settings load.

oauth2-proxy's own configuration (provider, client id/secret, issuer
URL, cookie secret, redirect URL) is deployment-level and lives in the
compose overlay / helm values — never in the app's `Settings`.

## 5. Backend

### 5.1 Identity resolution (`api/deps.py`)

`get_current_user` and `sse_user_from_request` gain a proxy branch,
extracted into one shared resolver:

```python
async def resolve_proxy_user(request, db) -> User | None
```

Order of checks, every failure a 401 `auth_not_authenticated`
(reusing the existing code; no new information leaked to clients):

1. `X-Proxy-Secret` header equals `PROXY_AUTH_SECRET` (constant-time
   compare). Missing/different → 401. This is what makes forged
   `X-Forwarded-*` from a direct-to-nginx client worthless.
2. `X-Forwarded-Email` present, ≤ 320 chars, parses as an email
   (same shape `LoginIn` enforces) → else 401.
3. `services/auth.get_or_provision_user(session, email, display_name)`
   (§5.2) → user.
4. `user.is_active` false → **403 `auth_user_disabled`** (new error
   code; see §5.6) — distinct from 401 so the frontend can show
   "account disabled" instead of looping into `/oauth2/start`.

`display_name` = `X-Forwarded-Preferred-Username` (stripped, cut to
100 chars) falling back to the email local-part. The
must-change-password gate is **skipped** in proxy mode: proxy users
have no password, and a local-mode user stuck with
`must_change_password=True` must not be locked out after the switch
(they can never satisfy the gate — the change-password route is gone).

`sse_user_from_request` in proxy mode ignores `?token=` and the
Bearer header entirely (no tokens exist) and delegates to the shared
resolver; EventSource requests carry the oauth2-proxy cookie, so the
headers are present.

### 5.2 JIT provisioning (`services/auth.py`)

```python
async def get_or_provision_user(session, email, display_name) -> User
```

- Existing row (any origin): returned unchanged — role, password, and
  project memberships survive a local→proxy switch.
- New row: `email`, `role = "admin" if email in admin set else "user"`,
  `password_hash = "!proxy-no-local-password"` (never parses as argon2
  → `verify_password` is always False; login impossible even if
  someone flips `AUTH_MODE` back without resetting), `is_active=True`,
  `must_change_password=False`, `display_name`.
- The service commits (services own the transaction boundary) and
  writes an audit entry on creation, following the existing `audit()`
  pattern with the new user as both actor and subject so operators can
  trace who was auto-provisioned when.

`bootstrap_admin` is a no-op in proxy mode: the initial admin comes
from `PROXY_ADMIN_EMAILS` JIT, and a password-having admin in proxy
mode would be unreachable (login disabled).

### 5.3 Route registration (`api/auth_routes.py`, `main.py`)

- `AUTH_MODE=proxy`: the router registers **only** `GET /api/auth/me`
  and `GET /api/auth/config`. `login`, `refresh`, `logout`,
  `change-password` are not registered → 404 (Starlette default).
  Login rate limiting, `_LOGIN_FAILURES`, and the
  must-change-password middleware in `main.py` are all skipped — the
  middleware guard is simply not registered.
- `GET /api/auth/config` (public, both modes): returns
  `{"auth_mode": "local" | "proxy"}`. This is the SPA's single source
  of truth for mode detection; response model `AuthConfigOut` lives in
  `api/schemas.py` and flows into `openapi.json` +
  `types.generated.ts` (regenerate both; the generated artifact is
  produced in local mode, where the login/refresh routes also appear).

### 5.4 Everything downstream is unchanged

`require_admin`, project permissions, rate limits (`QUERY_RATE_LIMIT_
PER_HOUR`), audit, and all domain/services code key off `User`; they
cannot tell the two modes apart. AdminUsers' reset-password API stays
registered (harmless for JIT accounts; useful if the deployment later
switches back to local) — the UI hides it in proxy mode (§6).

### 5.5 Health endpoints

`/api/health`, `/api/ready` remain unauthenticated and must stay
reachable by orchestrators *without* passing oauth2-proxy (compose
healthcheck hits the api container directly; helm probes hit the api
service, not the ingress). No change needed — recorded so the ingress
annotation work in §7.2 doesn't accidentally guard them (the ingress
only fronts user traffic; probes never traverse it).

### 5.6 Error codes and i18n

New code: `auth_user_disabled` (403). Catalog entries in
`frontend/src/i18n/locales/zh-TW.ts` ("此帳號已停用") and `en-US.ts`
("This account is disabled"). `auth_not_authenticated` reuse needs no
new strings.

## 6. Frontend

### 6.1 Mode detection (`stores/auth.ts`)

- New store field `authMode: "local" | "proxy" | null` (null = config
  fetch not yet done; treated as local until known so local deployments
  boot exactly as today).
- `restore()` first `GET /api/auth/config`:
  - `local` → existing path unchanged (refreshOnce → /me).
  - `proxy` → clear any stale `grui_refresh` from localStorage, then
    `GET /api/auth/me` (same-origin; the oauth2-proxy cookie rides
    along). 200 → set user. 401 → `redirectToProxyLogin()`.
- `redirectToProxyLogin()`:
  `window.location.assign("/oauth2/start?rd=" + encodeURIComponent(location.pathname + location.search))`.
- `logout()` in proxy mode: no server call (nothing to revoke — no
  refresh tokens exist), then
  `window.location.assign("/oauth2/sign_out?rd=/login")`.

### 6.2 HTTP client (`api/client.ts`)

- Attach `Authorization` and the 401→refresh→retry loop only in local
  mode.
- Proxy mode: plain fetch; a 401 response triggers
  `redirectToProxyLogin()` — but only once per page load (module-level
  flag set before redirecting), because a valid-but-stale proxy cookie
  could otherwise loop start→rd→401→start. A 403 `auth_user_disabled`
  surfaces as a normal localized error; no redirect.

### 6.3 Pages

- `/login` (`pages/Login.tsx`): in proxy mode, render nothing and
  `redirectToProxyLogin()` in an effect — the local form never shows.
- `AdminUsers.tsx`: hide the "reset password" action when
  `authMode === "proxy"`; role and active toggles remain.
- `ProtectedRoute.tsx`: unchanged — it gates on `user`, which both
  modes populate. `bootstrapping` covers the extra config round-trip.

## 7. Deployment

### 7.1 docker-compose (opt-in overlay)

New `docker-compose.proxy-auth.yml` used as
`docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml up`:

- `auth` service: pinned `quay.io/oauth2-proxy/oauth2-proxy:v7.9.0`
  image, publishes `8080:4180`, `depends_on: [web]`, runs with an
  alpha config (`--config`) that:
  - upstream = `http://web:80`, provider/issuer/client IDs come from
    `.env` via compose interpolation of the config `content:`;
  - `injectRequestHeaders`:
    `X-Forwarded-Email` ← `email` claim, `X-Forwarded-Preferred-
    Username` ← `preferred_username` (fallback `name`) claim, and
    `X-Proxy-Secret` ← `secretSource.fromFile` reading a compose
    `secrets` entry backed by the `PROXY_AUTH_SECRET` **environment
    variable** (`secrets: { proxy_auth_secret: { environment:
    PROXY_AUTH_SECRET } }` — one .env var feeds both the api env and
    the oauth2-proxy file; no base64 juggling).
- `web` service: `ports: !reset []` (Compose ≥ 2.24 override tag) —
  the SPA is no longer directly reachable, so the only path to the
  API is through oauth2-proxy. Combined with the secret check this is
  belt-and-suspenders: even a mis-published nginx cannot mint
  identities.
- `api` service: `AUTH_MODE: proxy`,
  `PROXY_ADMIN_EMAILS: ${PROXY_ADMIN_EMAILS:-}`,
  `PROXY_AUTH_SECRET: ${PROXY_AUTH_SECRET:?…}`.
- `nginx.conf` is **unchanged**: it already forwards request headers
  to `api:8000` untouched, and SSE-relevant settings
  (`proxy_buffering off`, 3600 s read timeout) apply to the
  oauth2-proxy→nginx→api chain as-is. oauth2-proxy streams responses
  (no buffering of flushed SSE frames) — verified in §10.
- `.env.example` gains a commented proxy-auth block:
  `PROXY_ADMIN_EMAILS`, `PROXY_AUTH_SECRET`, plus the oauth2-proxy
  provider variables (`OAUTH2_PROXY_PROVIDER`,
  `OAUTH2_PROXY_ISSUER_URL`/client id/secret, `OAUTH2_PROXY_COOKIE_
  SECRET`, `OAUTH2_PROXY_REDIRECT_URL`).
- CI/docs deploy check adds
  `docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml config`
  alongside the existing one.

Default `docker compose config` output must be byte-identical to today
(overlay adds nothing unless included).

### 7.2 helm

`values.yaml`:

```yaml
proxyAuth:
  enabled: false
  external:
    url: ""            # set to reuse a cluster-wide oauth2-proxy; skips shipping our own
  provider: oidc
  issuerUrl: ""
  clientId: ""
  existingSecret: ""   # keys: client-secret, cookie-secret, proxy-auth-secret
  adminEmails: []
```

- `proxyAuth.enabled=true` and no `external.url`: vendor the official
  `oauth2-proxy/oauth2-proxy` chart (same pattern as the vendored
  `postgresql-18.8.12.tgz`), configured with alphaConfig
  `injectResponseHeaders` (email / preferred_username claims +
  `X-Proxy-Secret` from the secret) since the ingress external-auth
  flow copies headers from oauth2-proxy's **auth response**. The
  chart's own ingress is enabled on the same host under path
  `/oauth2` so the `auth-signin` redirect below has a public
  endpoint; the app ingress rules are unchanged apart from the
  annotations.
- `templates/ingress.yaml` (either variant) gains annotations:
  - `nginx.ingress.kubernetes.io/auth-url` → the oauth2-proxy
    `/oauth2/auth` endpoint (cluster-internal service URL when we ship
    it; `external.url` when not);
  - `...auth-signin` → `https://<host>/oauth2/start?rd=$escaped_request_uri`;
  - `...auth-response-headers: "X-Forwarded-Email,X-Forwarded-Preferred-Username,X-Proxy-Secret"`
    (nginx ingress overwrites these from the auth response, so
    client-forged values never survive).
  - Existing SSE annotations (`proxy-buffering: off`,
    `proxy-read-timeout: 3600`) stay.
- `templates/api-deployment.yaml` injects `AUTH_MODE=proxy`,
  `PROXY_ADMIN_EMAILS` (joined), and `PROXY_AUTH_SECRET` (from the
  secret) only when `proxyAuth.enabled`.
- `helm lint` / `helm template` must pass for: defaults,
  `proxyAuth.enabled=true`, and `external.url` set.

## 8. Security model (summary)

| Threat | Mitigation |
|---|---|
| Client forges `X-Forwarded-Email` directly at nginx/api | API requires `X-Proxy-Secret` (constant-time compare); the secret exists only in oauth2-proxy config and API env. |
| Client sends a forged secret through oauth2-proxy | oauth2-proxy **sets** (replaces) the identity headers it manages from the authenticated session; client-supplied identity headers do not survive, and the secret is never exposed to the browser. |
| Client bypasses oauth2-proxy via web's published port | Overlay unpublishes web (`ports: !reset []`); secret check still backstops. |
| Stolen/leaked secret | Rotate `PROXY_AUTH_SECRET` (api env + oauth2-proxy secret); tokens/sessions unaffected (there are none). |
| OAuth session expiry mid-use | Headers vanish → 401 → SPA redirects to `/oauth2/start`; no orphaned app sessions (the local mode's "refresh forever" failure mode cannot occur in proxy mode). |

## 9. Edge cases

- `X-Forwarded-Email` > 320 chars or malformed → 401 (garbage headers
  must not create rows).
- Local user with `must_change_password=True` when the deployment
  switches to proxy → gate skipped (§5.1); they get in via the proxy.
- Switching proxy→local: JIT accounts have unusable password hashes →
  local login impossible until an admin resets their passwords
  (AdminUsers). Documented in README; no code change.
- `PROXY_ADMIN_EMAILS` matches are case-insensitive (emails stored
  lowercased, matching the existing login keying).
- Duplicate `X-Proxy-Secret` request headers (append vs replace
  differences across oauth2-proxy versions) → treated as mismatch →
  401 (compare first value only; ambiguity is failure).

## 10. Testing

Backend (`uv run pytest`, testcontainers as usual):

- Resolver: missing secret / wrong secret / missing email / malformed
  email → 401; inactive user → 403 `auth_user_disabled`; correct
  headers → 200 with expected identity.
- JIT: new email creates `user`, listed email creates `admin`,
  existing local-mode row keeps role; audit row written; unusable
  password hash never verifies.
- Route matrix: proxy mode `login/refresh/logout/change-password` →
  404; `/api/auth/config` → both modes correct; `/api/auth/me` works
  header-only; SSE route authenticates via headers; must-change guard
  absent in proxy mode.
- Startup: `AUTH_MODE=proxy` without `PROXY_AUTH_SECRET` → fail fast.

Frontend (`npm test`, `tsc -b`):

- `restore()` proxy branch: config → me → user set; 401 → redirect
  called with correct `rd`; stale `grui_refresh` cleared.
- `client.ts`: proxy mode attaches no Authorization, does not refresh,
  401 redirects once.
- Login route proxy redirect; AdminUsers hides reset in proxy mode.

Deploy checks:

- `docker compose config` (unchanged default) and the overlay variant
  both valid; overlay publishes only `auth`.
- `helm lint` + `helm template` for the three values combinations
  (§7.2).
- One manual smoke test against the real oauth2-proxy image (document
  in the plan): SSE streams through the full chain, header injection
  replaces (not appends) client-forged identity headers, and a
  direct-to-nginx request with forged headers gets 401. This is the
  §7.1/§8 assumption made testable.

## 11. Documentation & contract updates

- `AGENTS.md`: append `AUTH_MODE`, `PROXY_ADMIN_EMAILS`,
  `PROXY_AUTH_SECRET` to the fixed environment-variable list.
- README + `docs/zh-TW/README` mirror: proxy-auth deployment section
  (compose overlay usage, helm values, secret generation, mode
  switching caveats from §9). Same PR.
- `openapi.json` regenerated (+ `/api/auth/config`), then
  `npm run gen:types`; both artifacts diffed in CI as usual.
