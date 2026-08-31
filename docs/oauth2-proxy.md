# OAuth2-Proxy authentication (optional)

Operational guide for `AUTH_MODE=proxy`. For the overview and request-flow
diagram see the [OAuth2-Proxy section in the README](../README.md#oauth2-proxy-authentication-optional);
the design rationale lives in the
[design spec](../superpowers/specs/2026-08-27-oauth2-proxy-auth-design.md).

## docker compose (opt-in overlay)

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
# Plain-http deployment only (browsers refuse the Secure cookie over http,
# login then fails silently): OAUTH2_PROXY_COOKIE_SECURE=false
```

## helm

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

## The email-domain allowlist is a security control

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

## Caveats

- **Switching proxy → local**: JIT accounts have unusable password
  hashes — local login is impossible for them until an admin resets
  their passwords (AdminUsers).
- **Switching local → proxy**: users stuck at `must_change_password` are
  not locked out — the password-change gate is skipped in proxy mode.
- **`PROXY_ADMIN_EMAILS` only grants, never revokes.** A listed email
  is re-granted the `user_admin` + `ops` pair on every request; remove it
  from the variable first, then change the roles in AdminUsers.
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

## Manual smoke runbook (requires a real IdP)

Run against the compose overlay once it is up:

1. Anonymous: `curl -i http://localhost:8080/api/auth/me` → **401** (not
   302 — `api_routes` working).
2. Browser `http://localhost:8080/` → IdP login → app boots;
   `/api/auth/me` shows the JIT-provisioned user.
3. Forged bypass: `docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml exec web curl -i -H "X-Forwarded-Email: admin@x" http://api:8000/api/auth/me` → **401** (no secret).
4. Duplicate-header replace semantics: through the front door, send a
   duplicate `X-Forwarded-Email` header → response is still 200 with ONE
   consistent identity (oauth2-proxy replaced, not appended).
5. SSE: enqueue an index job and open its live log (or run a query once
   indexed); frames flow through auth → web → api without stalling.
6. UI logout → lands on oauth2-proxy's own sign-in page (no auto
   re-login).
