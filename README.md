# GraphRAG Web UI

A team web console for [Microsoft GraphRAG](https://github.com/microsoft/graphrag): manage
projects, upload corpora, run indexing jobs, and query the knowledge graph — local / global /
drift / basic search modes, all streaming over SSE with inline citations. Browse the parquet
artifacts (entities, relationships, communities, documents, community reports, text units)
and explore the graph in an interactive WebGL view (圖譜). It replaces GraphRAG CLI
operations for non-technical teammates: everything from `graphrag init` to query runs behind
login, roles, and per-project quotas.

## Architecture

Short sketch (full detail in the [design spec](docs/superpowers/specs/)):

- **Frontend** — React 19 SPA (Ant Design, zh-TW interface text), built with Vite and served
  by nginx, which also reverse-proxies `/api` to the backend (SSE-friendly: buffering off).
- **Backend** — FastAPI, layered `api` / `services` / `domain` / `adapters`.
  **Two graphrag touchpoints, both confined to `adapters/`:**
  - Indexing runs the graphrag CLI as a subprocess — `graphrag init` at project creation,
    then `graphrag index` / `graphrag update` jobs (`adapters/index_runner.py`,
    `adapters/workspace.py`).
  - Query/search calls `graphrag.api` in-process inside a shielded module
    (`adapters/graphrag_search.py` — shielded because graphrag's dependency chain loads
    `.env`/dotenv into `os.environ` on import; the adapter snapshots and restores the
    environment around that import).
- **Database** — PostgreSQL 16 (SQLAlchemy async + asyncpg); Alembic migrations run
  automatically at API startup.
- **Workspaces** — one `graphrag init` workspace per project under `WORKSPACES_DIR`:
  uploads land in `input/`, index output in `output/`, per-project keys (e.g.
  `GRAPHRAG_API_KEY`) in the workspace `.env`.

## Quickstart (15 minutes)

1. **Prerequisites** — Docker + Docker Compose. Node **24** and Python 3.12 + uv are only
   needed for local development (the frontend test stack — jsdom/undici — needs Node ≥ 22;
   CI pins 24).
2. **Configure** — `cp .env.example .env`, then set the three compose-enforced variables:

   - `JWT_SECRET` — a long random string (the JWT signing key; don't keep the dev default)
   - `BOOTSTRAP_ADMIN_EMAIL` — must use a routable domain, **not** `.local`: login
     validation rejects special-use domains
   - `BOOTSTRAP_ADMIN_PASSWORD`

   All 15 variables and their defaults are documented in [`.env.example`](.env.example).
3. **Start** — `docker compose up --build -d`. The UI is at `http://localhost:8080`.
   Postgres starts first; the API waits for PG health and runs the Alembic migrations
   automatically.
4. **First login** — log in as the bootstrap admin; the UI forces a password change before
   anything else.
5. **Create a project** — pick `input_file_type` (`text` / `csv` / `json`). It is fixed at
   creation and decides which file extensions uploads accept.
6. **Upload the corpus** — files go to the project workspace `input/`. Per-file cap
   `UPLOAD_MAX_FILE_MB`, per-project quota `PROJECT_QUOTA_MB`; exceeding either → 413.
7. **Set the LLM key** — Project Settings → Env: set `GRAPHRAG_API_KEY` (per-project,
   stored in the workspace `.env`, read back masked). Without it, indexing jobs fail.
8. **Index** — Jobs → run an index job (method `fast` or `standard`). Caveat from
   real-corpus testing: on tiny corpora the `fast` method can fail ("Graph Pruning failed.
   No entities remain.") — use `standard` for the first run on small test corpora. Follow
   progress in the live log viewer.
9. **Query** — all four modes (`local`, `global`, `drift`, `basic`) stream over SSE with
   inline citations.
10. **Explore** — artifact tables (entities / relationships / communities / documents /
    community_reports / text_units) and the 圖譜 WebGL graph view.

## Known caveats

- graphrag is pinned to `==3.1.0`: newer 3.1.x releases pull `lancedb` versions that have
  no macOS x86_64 wheel (see §13 of the design spec).
- On macOS, the `osxkeychain` credential helper blocks Docker in non-interactive
  sessions (SSH, agent terminals): `error getting credentials … keychain cannot
  be accessed` — even for public-image pulls/builds. Unlock the keychain first
  (`security -v unlock-keychain ~/Library/Keychains/login.keychain-db`), or —
  when only public images are needed — temporarily remove `"credsStore"` from
  `~/.docker/config.json` and restore it afterwards. With OrbStack this also
  affects `docker compose build`: the daemon resolves registry credentials
  through the host's docker config.

## Troubleshooting

- **"invalid email or password" right after re-running `docker compose up`** —
  the bootstrap admin is created **only on the first startup against an empty
  database**. If the Postgres volume already holds an admin (e.g.
  `BOOTSTRAP_ADMIN_PASSWORD` was changed in `.env` between runs), the *old*
  password still applies; the API log names the ignored variable at startup.
  For a clean trial state: `docker compose down -v` (⚠ destroys all data),
  then `up` again.
- **`npm ci` fails with `ERESOLVE` in the web image build** — the frontend
  tolerates the typescript 6 ↔ openapi-typescript peer conflict via
  `frontend/.npmrc` (`legacy-peer-deps=true`), and the Dockerfile must copy it
  into the build stage before `npm ci`. If you touch the copy steps, keep
  `.npmrc` with `package.json`.
- **"LiteLLM:WARNING … could not pre-load bedrock/sagemaker response stream
  shape" topping every index job log** — harmless: graphrag's LLM layer
  (litellm) probes for its optional AWS (botocore) integrations at import.
  Both graphrag touchpoints default `LITELLM_LOG=ERROR` so the noise never
  reaches job logs; export `LITELLM_LOG` explicitly (e.g. `DEBUG`) to
  override when debugging LLM calls.

## Local development

Backend (Docker required — the test suite uses testcontainers):

```
cd backend
uv sync
uv run pytest -m "not slow"
```

Frontend (Node 24):

```
cd frontend
npm ci
npm test
```

The Vite dev server proxies `/api` to `http://localhost:8000` by default; point it at
another front door with `API_PROXY_TARGET` (see `frontend/vite.config.ts`).

## Deployment

- [`deploy/helm/graphrag-ui`](deploy/helm/graphrag-ui) — Helm chart;
  [`values.yaml`](deploy/helm/graphrag-ui/values.yaml) documents every environment variable,
  and `NOTES.txt` prints an install-time quickstart (zh-TW).
- [`docker-compose.yml`](docker-compose.yml) — single-host deployment; same 15 variables.

## OAuth2-Proxy authentication (optional)

Teams that already run an OIDC provider (Google, GitHub, Azure Entra,
Keycloak…) can front the app with
[oauth2-proxy](https://oauth2-proxy.github.io/) instead of maintaining a
second credential set. The mode is opt-in per deployment — default
deployments keep today's behavior byte-for-byte (design:
[spec](docs/superpowers/specs/2026-08-27-oauth2-proxy-auth-design.md)).

`AUTH_MODE=proxy` changes three things:

- **Local login is fully disabled** — `login` / `refresh` / `logout` /
  `change-password` are not registered (404) and no application JWTs
  exist. Identity comes from `X-Forwarded-Email` (display name:
  `X-Forwarded-Preferred-Username`) headers injected by oauth2-proxy on
  every request; the SPA detects the mode via `GET /api/auth/config`.
- **Header trust is anchored to a shared secret** — `PROXY_AUTH_SECRET`
  (required, ≥ 32 chars; the API exits at startup otherwise) travels as
  the `X-Proxy-Secret` header. A request without exactly one matching
  value is rejected, so forged `X-Forwarded-*` headers sent directly at
  nginx or the api are worthless.
- **Users are provisioned just-in-time** — a first-seen email becomes a
  `user` row with an unusable password hash (local login stays
  impossible for it). Emails listed in `PROXY_ADMIN_EMAILS`
  (comma-separated) are held at `role=admin` on every request.

### docker compose (opt-in overlay)

```
docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml up -d
```

The overlay adds the `auth` service (pinned
`quay.io/oauth2-proxy/oauth2-proxy:v7.15.4`) as the single published
entrypoint on `http://localhost:8080`, unpublishes the web port
(belt-and-suspenders: no path to the api that skips the proxy), sets
`AUTH_MODE=proxy` on the api, and configures `/api/*` to answer **401**
instead of a login redirect so the SPA's fetch layer can react. Requires
Compose ≥ 2.24. Minimal `.env` additions:

```dotenv
PROXY_ADMIN_EMAILS=you@example.com
PROXY_AUTH_SECRET=            # >= 32 chars — generate: openssl rand -hex 32
OAUTH2_PROXY_ISSUER_URL=https://idp.example.com/realms/main
OAUTH2_PROXY_CLIENT_ID=graphrag-ui
OAUTH2_PROXY_CLIENT_SECRET=
OAUTH2_PROXY_COOKIE_SECRET=   # base64 of 16/24/32 bytes — openssl rand -base64 32 | tr -d '\n'
OAUTH2_PROXY_REDIRECT_URL=http://localhost:8080/oauth2/callback
OAUTH2_PROXY_EMAIL_DOMAINS=example.com
```

### helm

Set `proxyAuth.enabled: true` (requires `ingress.enabled` and a concrete
`ingress.host`). The chart then splits the ingress in two — an `/api`
Ingress whose failed authentications pass **401** straight through to
`fetch`, and an app Ingress that redirects browsers to the login page.
Either ship the chart-managed oauth2-proxy:

```yaml
proxyAuth:
  enabled: true
  issuerUrl: https://idp.example.com/realms/main
  clientId: graphrag-ui
  clientSecret: "..."        # plaintext, or via existingSecret (below)
  cookieSecret: "..."        # base64 of 16/24/32 bytes
  authSecret: "..."          # PROXY_AUTH_SECRET — >= 32 chars
  adminEmails: ["you@example.com"]
  emailDomains: ["example.com"]   # REQUIRED — see warning below
  # existingSecret: my-secret     # alternative to the three plaintext
  #   secrets; must contain the client-secret, cookie-secret, and
  #   proxy-auth-secret keys
```

…or reuse a cluster-wide oauth2-proxy via annotations only (the chart
ships no oauth2-proxy of its own; the external instance must inject
`X-Forwarded-Email`, `X-Forwarded-Preferred-Username`, and an
`X-Proxy-Secret` equal to `authSecret`):

```yaml
proxyAuth:
  enabled: true
  external:
    url: https://sso.example.com
  authSecret: "..."          # must match the external instance's secret
  adminEmails: ["you@example.com"]
  emailDomains: ["example.com"]
```

### The email-domain allowlist is a security control

> **`OAUTH2_PROXY_EMAIL_DOMAINS` is a security control, not a
> convenience.** oauth2-proxy authorizes emails via `--email-domain`
> (list, `*` = any) or `--authenticated-emails-file` (one per line).
> Because JIT provisioning (§5.2) turns "the IdP authenticated them" into
> "a `User` row exists", a public provider plus `*` means **anyone with a
> Google account self-provisions a `user` account** and can create
> projects and spend LLM budget. `.env.example` ships it uncommented with
> a placeholder domain and an explicit warning; helm mirrors it as
> `proxyAuth.emailDomains` (§7.2). This is the one oauth2-proxy setting
> the app's own threat model depends on (§8).

(§ references are into the design spec linked above.)

### Caveats

- **Switching proxy → local**: JIT accounts have unusable password
  hashes — local login is impossible for them until an admin resets
  their passwords (AdminUsers).
- **Switching local → proxy**: users stuck at `must_change_password` are
  not locked out — the password-change gate is skipped in proxy mode.
- **`PROXY_ADMIN_EMAILS` only promotes, never demotes.** A listed email
  is re-promoted to admin on every request; remove it from the variable
  first, then demote in AdminUsers.
- **An email change at the IdP is a new identity** — the new address is
  provisioned as a fresh row; the old row keeps its project memberships.
  An admin re-adds the new account to its projects and deactivates the
  old row.
- **IdP-issued special-use domains (`.local`, `.internal`) are
  rejected** — email validation refuses them, so the resolver 401s and
  the account is never provisioned (same trap as
  `BOOTSTRAP_ADMIN_EMAIL`).
- **Logout** lands on oauth2-proxy's own sign-in page: it never
  redirects back into the app (a live IdP session would silently
  re-login), and ending the IdP's own session is oauth2-proxy/provider
  configuration, not the app's.

### Manual smoke runbook (requires a real IdP)

Run against the compose overlay once it is up:

1. Anonymous: `curl -i http://localhost:8080/api/auth/me` → **401** (not
   302 — `api_routes` working).
2. Browser `http://localhost:8080/` → IdP login → app boots;
   `/api/auth/me` shows the JIT-provisioned user.
3. Forged bypass: `docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml exec web curl -i -H "X-Forwarded-Email: admin@x" http://api:8000/api/auth/me` → **401** (no secret).
4. Duplicate-header replace semantics: through the front door, send a
   duplicate `X-Forwarded-Email` header → response is still 200 with ONE
   consistent identity (oauth2-proxy replaced, not appended).
5. SSE: run a query stream; frames flow through auth → web → api without
   stalling.
6. UI logout → lands on oauth2-proxy's own sign-in page (no auto
   re-login).

## Contributing & docs

- [CONTRIBUTING.md](CONTRIBUTING.md)
- Traditional Chinese (zh-TW) mirror: [`docs/zh-TW/README.md`](docs/zh-TW/README.md)
- Design specs: [`docs/superpowers/specs/`](docs/superpowers/specs/)
