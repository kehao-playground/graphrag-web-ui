# OAuth2-Proxy Authentication (Optional Deployment Mode) Design

Date: 2026-08-27
Status: approved in chat; revised 2026-08-27 after spec review; pending
implementation plan.

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

Review-round decisions (2026-08-27, added while resolving review
findings — each replaces an under-specified area, not an earlier
decision):

5. Unauthenticated `/api/*` responses stay **401, never a login
   redirect**: oauth2-proxy `api_routes` in compose, a dedicated
   `/api` Ingress without `auth-signin` in helm. Browser navigation
   routes keep the redirect behaviour. (§5.1, §6.2, §7)
6. Logout lands on oauth2-proxy's own sign-in page
   (`/oauth2/sign_out` with **no** `rd`). Redirecting back into the
   app would silently re-authenticate against a live IdP session.
   (§6.1, §9)
7. `PROXY_ADMIN_EMAILS` is **authoritative-upward on every request**,
   not only at row creation: a listed email is promoted to `admin`
   whenever it is seen, and can never be demoted through the UI.
   This is the only break-glass in a deployment with no admin. (§5.2)
8. Email matching is **case-insensitive**, implemented as a
   case-insensitive lookup with lowercased writes — not as a data
   migration of existing rows. (§5.2, §9)

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
  `user`. Existing rows (including ones created in local mode, whose
  stored email may differ in case) are matched case-insensitively and
  keep their role and settings — local→proxy migration is seamless.
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
- Restricting **who** the IdP lets in. That is oauth2-proxy's
  `email_domains` / `authenticated_emails_file`, an operator
  responsibility (§7.1) — but because JIT provisioning turns "the IdP
  let them in" directly into "a row exists", the spec treats it as a
  first-class deployment requirement, not a footnote.

## 4. Configuration

New environment variables (added to the fixed list in AGENTS.md,
`.env.example`, docker-compose api service, and the helm api
deployment):

| Variable | Default | Meaning |
|---|---|---|
| `AUTH_MODE` | `local` | `local` = today's JWT auth; `proxy` = header auth. |
| `PROXY_ADMIN_EMAILS` | *(empty)* | Comma-separated emails held at `role=admin` (§5.2 — applied on every request, not only at creation). |
| `PROXY_AUTH_SECRET` | *(empty)* | Shared secret required on every authenticated request in proxy mode. **Required and ≥ 32 characters** when `AUTH_MODE=proxy` — startup fails fast otherwise. |

`Settings` gains `auth_mode: Literal["local", "proxy"]`,
`proxy_admin_emails: str`, `proxy_auth_secret: str`
(`backend/src/graphrag_ui/config.py`). `PROXY_ADMIN_EMAILS` parses to
a lowercased set once at settings load.

Fail-fast mechanism: a pydantic `model_validator(mode="after")` on
`Settings` raises when `auth_mode == "proxy"` and `proxy_auth_secret`
is shorter than 32 characters. It fires at the first `get_settings()`
call — which `create_app()` reaches through `register_auth_routes`
(§5.3) — so a misconfigured container exits during startup rather than
serving requests with a guessable trust anchor. `get_settings` is
`lru_cache`d, so tests that flip the env must call
`get_settings.cache_clear()`.

The 32-character floor exists because `PROXY_AUTH_SECRET` is the
*entire* trust anchor of this mode: unlike a password it is never
rate-limited and never rotated by a login flow.

oauth2-proxy's own configuration (provider, client id/secret, issuer
URL, cookie secret, redirect URL, **email allowlist**) is
deployment-level and lives in the compose overlay / helm values —
never in the app's `Settings`.

## 5. Backend

### 5.1 Identity resolution (`api/deps.py`)

`get_current_user` and `sse_user_from_request` gain a proxy branch,
extracted into one shared resolver:

```python
async def resolve_proxy_user(request, db) -> User | None
```

Order of checks, every failure a 401 `auth_not_authenticated`
(reusing the existing code; no new information leaked to clients):

1. `request.headers.getlist("X-Proxy-Secret")` has **exactly one**
   entry and it equals `PROXY_AUTH_SECRET` (constant-time compare).
   Zero, several, or different → 401. This is what makes forged
   `X-Forwarded-*` from a direct-to-nginx client worthless.
2. `request.headers.getlist("X-Forwarded-Email")` has **exactly one**
   entry, ≤ 320 chars, and parses as an email (same shape `LoginIn`
   enforces) → else 401.
3. `services/auth.get_or_provision_user(session, email, display_name)`
   (§5.2) → user.
4. `user.is_active` false → **403 `auth_user_disabled`** (new error
   code; see §5.6) — distinct from 401 so the frontend can show
   "account disabled" instead of looping into `/oauth2/start`.

The exactly-one rule in steps 1–2 replaces an earlier "compare the
first value" phrasing, which silently accepted a duplicated header
whose first copy happened to match. Different oauth2-proxy versions
and ingress controllers differ on append-vs-replace; a request whose
identity is ambiguous is a failed request, full stop.

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

Every 401 out of this resolver is only useful to the SPA if it
actually reaches the SPA as a 401 rather than as a login redirect —
see decision 5, §6.2 and §7.

### 5.2 JIT provisioning (`services/auth.py`)

```python
async def get_or_provision_user(session, email, display_name) -> User
```

**Lookup is case-insensitive** — `select(User).where(func.lower(
User.email) == email.strip().lower())`. Emails are *not* uniformly
lowercased in the existing data: `create_user` stores the
pydantic-normalized `EmailStr` (which lowercases the domain but keeps
  the local part as typed), and the only `.lower()` applied to an
  email in the codebase today is the login rate-limit key. A
  case-sensitive lookup would give
an existing `Alice@Example.com` a **second** row the moment the IdP
sends `alice@example.com`, silently dropping their role and project
memberships — the exact opposite of the migration goal in §2. New
rows are written lowercased so the data converges going forward; no
migration of existing rows, and no functional index (the users table
is team-sized; recorded here so a later migration can add
`lower(email)` if that ever stops being true).

- Existing row (any origin, any case): returned with its role, password
  and project memberships intact, after the admin reconciliation below.
- New row: `email` (lowercased), `role = "admin" if email in admin set
  else "user"`, `password_hash = "!proxy-no-local-password"` (never
  parses as argon2 → `verify_password` is always False; login
  impossible even if someone flips `AUTH_MODE` back without resetting),
  `is_active=True`, `must_change_password=False`, `display_name`.
- The service commits (services own the transaction boundary) and
  writes an audit entry on creation, following the existing `audit()`
  pattern with the new user as both actor and subject so operators can
  trace who was auto-provisioned when.

**Concurrent first login (must be handled, not merely noted).** The
SPA fires 3–4 requests in parallel on every page load — `stores/auth.ts`
says so in its own single-flight comment. For a first-seen user all of
them miss the SELECT and all of them INSERT; `users.email` is
`unique=True`, so all but one raise `IntegrityError` and the user's
very first page load 500s. The insert is therefore wrapped:
`except IntegrityError: await session.rollback()` then re-run the
case-insensitive SELECT and return the row the winner created. The
rollback is safe here because identity resolution is the first thing
that touches the session in a request — nothing else is pending.
`IntegrityError` is caught, not prevented with a dialect-specific
`ON CONFLICT`, so `services/` stays free of postgres-dialect imports.

**Admin reconciliation (decision 7).** On every resolve, if the email
is in the `PROXY_ADMIN_EMAILS` set and `user.role != "admin"`, promote
it (+ audit entry + commit). Never demote. Applying the list only at
row creation left a real dead end: if the first user signs in before
the variable is set — a typo, an empty value, a forgotten helm value —
they are `user` forever and the deployment has **no admin at all**.
AdminUsers is unreachable, the login routes are 404, and the only
recovery is a hand-written `UPDATE users`. Reconciling on every request
makes "grant an admin" a config change instead. The consequence is
explicit and intended: an email listed in `PROXY_ADMIN_EMAILS` cannot
be demoted through the UI — remove it from the variable first.

`display_name` on an existing row is **not** refreshed from the header.
A name changed at the IdP will not propagate; keeping the app's own
value stable is the deliberate choice, since AdminUsers can edit it and
an every-request write would be a write on every request.

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
- `/api/auth/config` is added to `MUST_CHANGE_ALLOWED_PATHS`
  (`api/deps.py`). In **local** mode the global guard in `main.py`
  403s any `/api` path whenever a `must_change_password` user's Bearer
  header is attached; a public config endpoint answering 403 during
  bootstrap is a confusing failure with no diagnostic value.

### 5.4 Everything downstream is unchanged

`require_admin`, project permissions, rate limits (`QUERY_RATE_LIMIT_
PER_HOUR`), audit, and all domain/services code key off `User`; they
cannot tell the two modes apart. AdminUsers' reset-password API stays
registered (harmless for JIT accounts — the login routes that would
consume a password are 404; useful if the deployment later switches
back to local) — the UI hides it in proxy mode (§6).

### 5.5 Health endpoints

`/api/health`, `/api/ready` remain unauthenticated and must stay
reachable by orchestrators *without* passing oauth2-proxy (helm probes
hit the api pod directly, never the ingress; compose has no api
healthcheck today and the overlay does not add one). No change needed
— recorded so the ingress annotation work in §7.2 doesn't accidentally
guard them, and so that adding a compose healthcheck later stays a
direct-to-container check.

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
  `window.location.assign("/oauth2/sign_out")` — **no `rd`**
  (decision 6). Passing `rd=/login` produced a logout that undoes
  itself: the proxy cookie is cleared, the browser lands on `/login`,
  `/login` is behind the proxy, a fresh OIDC flow starts, the IdP
  session is still alive, and the user is silently signed back in and
  bounced to `/oauth2/start` by §6.3. Without `rd` the user stops on
  oauth2-proxy's own sign-in page, which is the honest end state for
  "signed out of this app, still signed in to the IdP" (§3 non-goal).

### 6.2 HTTP client (`api/client.ts`)

- Attach `Authorization` and the 401→refresh→retry loop only in local
  mode.
- Proxy mode: plain fetch with `redirect: "manual"`. The session is
  treated as expired — `redirectToProxyLogin()`, **once per page load**
  (module-level flag set before redirecting, because a valid-but-stale
  proxy cookie could otherwise loop start→rd→401→start) — on any of:
  - `r.status === 401` (the normal case once §7 is configured);
  - `r.type === "opaqueredirect"` (status 0), i.e. the edge answered a
    login redirect instead of a 401;
  - the fetch promise rejecting — a genuine network error here, since
    `redirect: "manual"` never *follows* a redirect; the CORS-blocked
    IdP hop this guards against is real only for plain fetches
    without `manual` (§6.1's restore path must therefore use the same
    `redirect: "manual"` semantics, not a bare fetch). The rejection
    is re-thrown after the redirect is scheduled.
- A 403 `auth_user_disabled` surfaces as a normal localized error; no
  redirect.

The last two branches exist because a 401 is **not** what an
unconfigured edge returns. oauth2-proxy in reverse-proxy mode answers
an expired session with a 302 to the IdP, and nginx-ingress answers
with a 302 to `auth-signin`; `fetch` follows both, the cross-origin hop
fails CORS, and the promise rejects with a TypeError — no `Response`
object with status 401 ever exists, so a 401-only guard is dead code.
§7 configures both edges to return a real 401 for `/api/*` (decision
5); these branches keep a misconfigured deployment showing "please sign
in" rather than an opaque network error.

### 6.3 Pages

- `/login` (`pages/Login.tsx`): in proxy mode, render nothing and
  `redirectToProxyLogin()` in an effect — the local form never shows.
- `AdminUsers.tsx`: hide the "reset password" action when
  `authMode === "proxy"`; role and active toggles remain.
- `ProtectedRoute.tsx`: unchanged — it gates on `user`, which both
  modes populate. `bootstrapping` covers the extra config round-trip.

### 6.4 SSE clients (`JobLogViewer.tsx`, `QueryPanel.tsx`)

Both build an `EventSource` URL with the access token from the store.
In proxy mode `accessToken` is null, and the two components behave
differently today: `JobLogViewer` omits `?token=` when falsy,
`QueryPanel` appends `&token=` unconditionally (`token ?? ""`).

- `QueryPanel` must append the `token` parameter only in local mode,
  matching `JobLogViewer`. The backend ignores it in proxy mode (§5.1),
  so an empty token is harmless *today* — which is exactly why it needs
  to be stated: an empty credential in a URL is the sort of thing a
  later reader "fixes" in the wrong direction.
- Accepted limitation, recorded rather than solved: when the proxy
  session expires **mid-stream**, EventSource receives a redirect it
  cannot follow and fires `onerror` with no data. `QueryPanel` renders
  its generic "query failed" message and `JobLogViewer` stops
  streaming; neither can distinguish this from a network drop, so
  neither triggers `redirectToProxyLogin()`. The next XHR the user
  makes goes through §6.2 and redirects properly.

## 7. Deployment

### 7.1 docker-compose (opt-in overlay)

New `docker-compose.proxy-auth.yml` used as
`docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml up`:

- `auth` service: pinned `quay.io/oauth2-proxy/oauth2-proxy:v7.15.4`
  image, publishes `8080:4180`, `depends_on: [web]`, mounts the
  alpha config and starts with `--alpha-config` (NOT `--config` —
  that flag loads the legacy TOML format and rejects the YAML;
  `api_routes` / `email_domains` stay env/legacy options, which is
  allowed: neither is on the alpha-config removed-options list) that:
  - upstream = `http://web:80`, provider/issuer/client IDs come from
    `.env` via compose interpolation of the config `content:`;
  - `injectRequestHeaders` in the **v7.14.0+ nested form**:
    ```yaml
    injectRequestHeaders:
      - name: X-Forwarded-Email
        values: [{ claimSource: { claim: email } }]
      - name: X-Forwarded-Preferred-Username
        values: [{ claimSource: { claim: preferred_username } }]
      - name: X-Proxy-Secret
        values: [{ secretSource: { fromFile: /run/secrets/proxy_auth_secret } }]
    ```
    The nested `claimSource:` / `secretSource:` keys are **required
    since v7.14.0** and are not valid on older tags (7.9.x used the
    flat `- claim:` / `- fromFile:` form). The image pin and the config
    syntax must move together; ≥ v7.14.0 is the constraint, v7.15.4 is
    the current release.
  - the secret file is a compose `secrets` entry backed by the
    `PROXY_AUTH_SECRET` **environment variable** (`secrets: {
    proxy_auth_secret: { environment: PROXY_AUTH_SECRET } }` — one .env
    var feeds both the api env and the oauth2-proxy file; no base64
    juggling).
  - `OAUTH2_PROXY_API_ROUTES: ^/api/` (flag `--api-route`, toml
    `api_routes`): "No redirect to login will be done. Return 401 if
    not." This is decision 5 for the compose topology — without it the
    SPA's `/api` calls get a 302 to the IdP and §6.2 has nothing to
    react to. Browser navigation (`/`, the SPA shell) keeps the
    redirect behaviour.
  - `OAUTH2_PROXY_EMAIL_DOMAINS`: **required, no default, wildcard
    strongly discouraged** — see the security note below.
  - Alpha config is incompatible with the legacy `pass-user-headers` /
    `set-xauthrequest` flags, and declaring `injectRequestHeaders`
    replaces oauth2-proxy's default header set entirely. Both are fine
    here (we declare every header the API reads), but neither is
    obvious to someone later adding a flag.
- `web` service: `ports: !reset []` (Compose ≥ 2.24 override tag) —
  the SPA is no longer directly reachable, so the only path to the
  API is through oauth2-proxy. Combined with the secret check this is
  belt-and-suspenders: even a mis-published nginx cannot mint
  identities.
- `api` service: `AUTH_MODE: proxy`,
  `PROXY_ADMIN_EMAILS: ${PROXY_ADMIN_EMAILS:-}`,
  `PROXY_AUTH_SECRET: ${PROXY_AUTH_SECRET:?…}`.
- `nginx.conf` is **unchanged**: it already forwards request headers
  to `api:8000` untouched (all three injected headers are
  hyphen-separated, so nginx's `underscores_in_headers off` default
  does not drop them), and SSE-relevant settings (`proxy_buffering
  off`, 3600 s read timeout) apply to the oauth2-proxy→nginx→api chain
  as-is. oauth2-proxy streams responses (no buffering of flushed SSE
  frames) — verified in §10.
- `.env.example` gains a commented proxy-auth block:
  `PROXY_ADMIN_EMAILS`, `PROXY_AUTH_SECRET` (with a generation
  one-liner and the 32-char floor), plus the oauth2-proxy provider
  variables (`OAUTH2_PROXY_PROVIDER`, `OAUTH2_PROXY_ISSUER_URL`/client
  id/secret, `OAUTH2_PROXY_COOKIE_SECRET`, `OAUTH2_PROXY_REDIRECT_URL`,
  `OAUTH2_PROXY_EMAIL_DOMAINS`).
- CI/docs deploy check adds
  `docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml config`
  alongside the existing one.

**`OAUTH2_PROXY_EMAIL_DOMAINS` is a security control, not a
convenience.** oauth2-proxy authorizes emails via `--email-domain`
(list, `*` = any) or `--authenticated-emails-file` (one per line).
Because JIT provisioning (§5.2) turns "the IdP authenticated them" into
"a `User` row exists", a public provider plus `*` means **anyone with a
Google account self-provisions a `user` account** and can create
projects and spend LLM budget. `.env.example` ships it uncommented with
a placeholder domain and an explicit warning; helm mirrors it as
`proxyAuth.emailDomains` (§7.2). This is the one oauth2-proxy setting
the app's own threat model depends on (§8).

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
  clientSecret: ""     # plaintext fallbacks used only when existingSecret is empty;
  cookieSecret: ""     # all three feed the chart Secret's extra keys
  authSecret: ""       # PROXY_AUTH_SECRET — >= 32 chars, validated by the api at startup
  existingSecret: ""   # when set, must contain: client-secret, cookie-secret, proxy-auth-secret
  adminEmails: []
  emailDomains: []     # REQUIRED when enabled (see §7.1); ["*"] = open registration
```

- `proxyAuth.enabled=true` and no `external.url`: the chart ships its
  own **hand-rolled** oauth2-proxy Deployment + Service + ConfigMap
  (`templates/oauth2-proxy.yaml`), like the hand-rolled api/web
  templates — NOT the official chart as a dependency. Reason: Helm
  sub-chart values are static and cannot reference parent values, so
  threading `proxyAuth.issuerUrl`/`ingress.host` into a sub-chart's
  alphaConfig/ingress would force operators to declare every value
  twice. The ConfigMap carries the alpha config with `${ENV}`
  placeholders (oauth2-proxy's own envsubst) fed by container env:
  provider/client/cookie secrets from the chart Secret,
  `OAUTH2_PROXY_EMAIL_DOMAINS` from `proxyAuth.emailDomains`,
  `OAUTH2_PROXY_API_ROUTES=^/api/`. Because nginx-ingress
  external-auth copies headers from the **auth response**, the alpha
  config declares `injectResponseHeaders` (email /
  preferred_username claims + `X-Proxy-Secret` via a secret-file
  volume mount), not `injectRequestHeaders`.
- A **third parent-rendered Ingress** routes `/oauth2` → the
  oauth2-proxy Service on the same `ingress.host`/`className`, so the
  `auth-signin` redirect below has a public endpoint and the
  longest-prefix match that puts `/oauth2` ahead of our `/` rule
  happens on the same controller.
- `templates/ingress.yaml` **splits into two Ingress objects** when
  `proxyAuth.enabled` (decision 5). Today's single object routes
  `/api` → api service and `/` → web service; nginx-ingress applies
  `auth-signin` per Ingress, not per path, so one object cannot answer
  401 for `/api` and 302 for `/`.
  - **API Ingress** (`/api` → api service): `auth-url`,
    `auth-response-headers`, **no `auth-signin`** → a failed auth
    subrequest passes 401 straight through to `fetch` (§6.2).
  - **App Ingress** (`/` → web service): `auth-url`,
    `auth-response-headers`, **and** `auth-signin` →
    `https://<host>/oauth2/start?rd=$escaped_request_uri`, so browser
    navigation still gets the interactive login.
  - Both carry
    `...auth-response-headers: "X-Forwarded-Email,X-Forwarded-Preferred-Username,X-Proxy-Secret"`
    (nginx ingress overwrites these from the auth response, so
    client-forged values never survive), and
    `...auth-url` → the oauth2-proxy `/oauth2/auth` endpoint
    (cluster-internal service URL when we ship it; `external.url` when
    not).
  - Existing SSE annotations (`proxy-buffering: off`,
    `proxy-read-timeout: 3600`) stay on both.
  - When `proxyAuth.enabled=false` the template renders exactly one
    Ingress, byte-identical to today.
- `templates/api-deployment.yaml` injects `AUTH_MODE=proxy`,
  `PROXY_ADMIN_EMAILS` (joined), and `PROXY_AUTH_SECRET` (from the
  secret) only when `proxyAuth.enabled`.
- `helm lint` / `helm template` must pass for: defaults,
  `proxyAuth.enabled=true`, and `external.url` set.

## 8. Security model (summary)

| Threat | Mitigation |
|---|---|
| Client forges `X-Forwarded-Email` directly at nginx/api | API requires exactly one `X-Proxy-Secret` matching `PROXY_AUTH_SECRET` (constant-time compare); the secret exists only in oauth2-proxy config and API env. |
| Client sends a forged secret through oauth2-proxy | oauth2-proxy **sets** (replaces) the identity headers it manages from the authenticated session; client-supplied identity headers do not survive, and the secret is never exposed to the browser. |
| Client sends duplicate identity headers to exploit append-vs-replace differences | Resolver requires exactly one value per header; any ambiguity is a 401 (§5.1). |
| Client bypasses oauth2-proxy via web's published port | Overlay unpublishes web (`ports: !reset []`); secret check still backstops. |
| Stolen/leaked secret | Rotate `PROXY_AUTH_SECRET` (api env + oauth2-proxy secret); tokens/sessions unaffected (there are none). ≥ 32 chars (§4) keeps it out of guessing range in the first place. |
| **Anyone with an account at a public IdP self-provisions** | oauth2-proxy `email_domains` / `authenticated_emails_file` must be set to a real allowlist; `*` plus JIT (§5.2) is open registration. Required (not optional) in `.env.example` and `proxyAuth.emailDomains` (§7.1). |
| **Deployment ends up with no admin** (`PROXY_ADMIN_EMAILS` unset at first login) | Admin reconciliation on every resolve (§5.2): fixing the variable and reloading restores access, with no manual DB edit. |
| OAuth session expiry mid-use | Headers vanish → 401 → SPA redirects to `/oauth2/start`; no orphaned app sessions (the local mode's "refresh forever" failure mode cannot occur in proxy mode). Requires the `/api`-returns-401 wiring of decision 5, else the SPA only sees a CORS-blocked redirect (§6.2). |

## 9. Edge cases

- `X-Forwarded-Email` > 320 chars, malformed, absent, or duplicated →
  401 (garbage headers must not create rows). Same rule for
  `X-Proxy-Secret` (§5.1).
- Email matching is **case-insensitive** (§5.2). Existing rows are not
  rewritten; new rows are stored lowercased. Note the corollary: the
  claim "emails are already stored lowercased" is *false* for this
  codebase — only the login rate-limit key lowercases — which is why
  the lookup, not the data, carries the normalization.
- `PROXY_ADMIN_EMAILS` matching is likewise case-insensitive (the set
  is lowercased at settings load, the header is lowercased before
  comparison).
- An email listed in `PROXY_ADMIN_EMAILS` **cannot be demoted** in
  AdminUsers — the next request re-promotes it (§5.2, decision 7).
  Remove it from the variable and restart first. Documented in README
  next to the variable.
- Local user with `must_change_password=True` when the deployment
  switches to proxy → gate skipped (§5.1); they get in via the proxy.
- Switching proxy→local: JIT accounts have unusable password hashes →
  local login impossible until an admin resets their passwords
  (AdminUsers). Documented in README; no code change.
- **User's email changes at the IdP**: the new address is a first-seen
  email → a new row, and the old row keeps the project memberships.
  Not automated; an admin re-adds the new account to the projects and
  deactivates the old row. Documented in README.
- **IdP issues emails on a special-use domain** (`.local`, `.internal`):
  `EmailStr` rejects them, so the resolver 401s and the account can
  never be provisioned. Same trap `.env.example` already warns about
  for `BOOTSTRAP_ADMIN_EMAIL`; the warning now applies to IdP-issued
  addresses too.
- **Logout**: `/oauth2/sign_out` with no `rd` (decision 6). Any `rd`
  pointing back into the app re-authenticates against a live IdP
  session and undoes the logout.
- **First page load of a first-seen user** races 3–4 concurrent
  provisioning attempts; the `IntegrityError` retry in §5.2 is what
  keeps that from being a 500.

## 10. Testing

Backend (`uv run pytest`, testcontainers as usual):

- Resolver: missing secret / wrong secret / **duplicate secret header**
  / missing email / **duplicate email header** / malformed email → 401;
  inactive user → 403 `auth_user_disabled`; correct headers → 200 with
  expected identity.
- JIT: new email creates `user`, listed email creates `admin`,
  existing local-mode row keeps role; audit row written; unusable
  password hash never verifies.
- JIT case-insensitivity: a row created in local mode as
  `Alice@Example.com` is **returned, not duplicated**, when the header
  says `alice@example.com`; the row keeps its role and project
  memberships. Assert the users table still has one row.
- JIT concurrency: N parallel `get_or_provision_user` calls for the
  same first-seen email yield one row and no error (drives the
  `IntegrityError` retry).
- Admin reconciliation: an existing `user` row whose email is in
  `PROXY_ADMIN_EMAILS` is promoted on the next resolve and audited; an
  admin **not** in the list is never demoted.
- Route matrix: proxy mode `login/refresh/logout/change-password` →
  404; `/api/auth/config` → both modes correct; `/api/auth/me` works
  header-only; SSE route authenticates via headers; must-change guard
  absent in proxy mode; `/api/auth/config` reachable in local mode with
  a `must_change_password` user's Bearer header attached.
- Startup: `AUTH_MODE=proxy` without `PROXY_AUTH_SECRET`, and with a
  secret shorter than 32 chars → fail fast (remember
  `get_settings.cache_clear()`).

Frontend (`npm test`, `tsc -b`):

- `restore()` proxy branch: config → me → user set; 401 → redirect
  called with correct `rd`; stale `grui_refresh` cleared.
- `client.ts`: proxy mode attaches no Authorization, does not refresh,
  and redirects **once** for each of the three expiry shapes — 401,
  `opaqueredirect`, and a rejected fetch (§6.2).
- `logout()` in proxy mode navigates to `/oauth2/sign_out` with no `rd`.
- Login route proxy redirect; AdminUsers hides reset in proxy mode.
- `QueryPanel` omits the `token` query parameter in proxy mode and
  still includes it in local mode.

Deploy checks:

- `docker compose config` (unchanged default) and the overlay variant
  both valid; overlay publishes only `auth`.
- `helm lint` + `helm template` for the three values combinations
  (§7.2), asserting two Ingress objects when `proxyAuth.enabled` and
  exactly one (unchanged) otherwise, and that only the app Ingress
  carries `auth-signin`.
- One manual smoke test against the real oauth2-proxy image (document
  in the plan): SSE streams through the full chain; header injection
  replaces (not appends) client-forged identity headers; a
  direct-to-nginx request with forged headers gets 401; an expired
  session on `/api/*` returns a real **401** (not a 302) thanks to
  `api_routes`. This is the §7.1/§8 assumption made testable.

## 11. Documentation & contract updates

- `AGENTS.md`: append `AUTH_MODE`, `PROXY_ADMIN_EMAILS`,
  `PROXY_AUTH_SECRET` to the fixed environment-variable list.
- README + `docs/zh-TW/README` mirror: proxy-auth deployment section
  (compose overlay usage, helm values, secret generation, the
  `email_domains` requirement from §7.1, and the mode-switching and
  admin-list caveats from §9). Same PR.
- `openapi.json` regenerated (+ `/api/auth/config`), then
  `npm run gen:types`; both artifacts diffed in CI as usual.
