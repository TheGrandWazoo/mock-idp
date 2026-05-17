# Roadmap & Future Considerations — Python Mock OIDC

What's not yet shipped, what's worth doing next, and what's parked unless a
specific need surfaces.

Current release is **v0.5.6**. The v0.3 surface is documented in ADR-002 and
ADR-003. The v0.4 surface is documented in ADR-003 (Postgres backend) and the
commit history. The v0.5 surface is documented in ADR-004 (per-issuer signing
keys) and the commit history.

---

## Status legend

- 🟢 **Milestone** — committed to a version; tracked in GitHub milestone
- 🟡 **Parked** — useful but no concrete demand yet; revisit when one shows up
- 🔴 **Maybe-never** — build only if a concrete and well-scoped use case appears

---

## v0.6.0 — Hosted foundation

GitHub Milestone #2. Lays the database schema and observability groundwork
for the hosted Pro service. All items are community-visible.

### 🟢 Token audit log + multi-org schema

Every `/token` call written to a `token_events` table keyed by `org_id`.
Queryable via `GET /admin/audit?limit=&cursor=`. Requires extending the
Postgres schema with a multi-org layout (shared `tenants`, `users`,
`service_principals` tables with `org_id` foreign key).

**Design constraint:** single-node Postgres today; schema must support
CloudNativePG HA promotion at ~50 paying orgs without a migration.

### 🟢 Multi-provider claim shapes

`provider: okta`, `provider: cognito`, `provider: keycloak` on a
`TenantRecord`. Each provider module under `providers/` emits the
claim shape that product expects. Enables teams switching IdPs to test
both shapes from a single config.

**Priority signal:** second-most-requested feature in OSS mock-OIDC
community threads after hosted endpoint.

### 🟢 Prometheus /metrics endpoint

`GET /metrics` via `prometheus-fastapi-instrumentator`. Exposes request
count, latency histogram, and active connections. Required for hosted
service SLO monitoring (Linode LKE + Prometheus stack).

**Scope note:** the "maybe-never" status on observability was for a test
fixture. For a hosted SaaS, metrics are a hard operational requirement.

---

## v0.7.0 — Hosted endpoint (Pro)

GitHub Milestone #3. Ships the first revenue-generating feature: the
`mock.ksatechnologies.com/{org}/{issuer}/token` hosted endpoint.

### 🟢 Hosted endpoint (slug routing)

Slug-based issuer routing: `/{org}/{issuer}/token` maps to the org's
Postgres-backed config. `iss` claim constructed from `ISS_BASE/{org}/{issuer}`.
Org provisioning via admin API (create org, push config). JWKS URL stable
across CI job isolation — solves the #1 CI pain point.

### 🟢 GitHub Actions marketplace action

`uses: thegrandwazoo/mock-idp-action@v1`. Spins up the hosted endpoint
(or a container for self-hosted runners), outputs `MOCK_IDP_TOKEN_URL`
and `MOCK_IDP_JWKS_URL` as step outputs. Free action is the highest-ROI
growth lever — CI users are the natural Pro upsell.

### 🟢 OBO flow (On-Behalf-Of, Entra-specific)

`grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`. Entra-style
OBO: service A presents its token, receives a token scoped for service B
with the original user's identity threaded through (`scp`, `oid`, `tid`
preserved; `act` chain added). No competitor handles this correctly.

### 🟢 Error / chaos injection admin API

`POST /admin/chaos` sets per-issuer fault modes: `{mode: "error_500",
probability: 0.5}`, `{mode: "delay_ms", value: 2000}`,
`{mode: "invalid_token"}`. Lets CI pipelines test auth failure paths
without modifying config. Reset via `DELETE /admin/chaos/{issuer}`.

### 🟢 Dev Container feature

`.devcontainer/` with `devcontainer.json` that installs mock-idp and
sets `CONFIG_PATH` + `ISS_BASE`. Publishable to the Dev Containers
feature registry. Zero-setup onboarding for new contributors.

---

## v0.8.0 — Web admin UI

GitHub Milestone #4. Depends on Postgres backend (v0.4.0) and multi-org
schema (v0.6.0).

### 🟢 Web admin UI

React (or HTMX) dashboard: manage orgs, tenants, users, service principals,
client apps. View audit log. Rotate JWKS. Trigger chaos modes. Auth via
admin token or SSO (Enterprise). Required for the hosted Pro service
self-service onboarding flow.

### 🟢 PKCE / Authorization Code flow

`GET /{issuer}/authorize` → redirect → code exchange at `/token`.
PKCE (RFC 7636) required. Enables testing browser-based OIDC login flows
end-to-end. Needed for teams writing E2E tests with Playwright/Cypress.

### 🟢 Device Authorization Grant (RFC 8628)

`POST /{issuer}/device_authorization` → `device_code` + `user_code` →
polling `/token`. For teams testing CLI tools or IoT clients that use
the device flow. Niche but high-signal: 3+ explicit requests in community
threads.

---

## Pro / Enterprise (backlog)

See [pro-enterprise.md](pro-enterprise.md) and
[business-model.md](business-model.md) for strategy and tier breakdown.
These items land in the private `mock-idp-enterprise` package.

- **mTLS / cert-bound tokens (RFC 8705)** — Enterprise; Large
- **HSM signing key support (PKCS#11)** — Enterprise; Large
- **FIPS 140-2 mode** — Enterprise; Large
- **Terraform provider + Kubernetes CRD operator** — Enterprise; Large
- **LDAP / Active Directory sync** — Enterprise; Medium
- **SAML 2.0 SP federation** — Backlog; Large
- **CloudNativePG HA Postgres** — Enterprise infra; Medium

---

## Parked

### 🟡 ID token issuance (`id_token` alongside `access_token`)

Token endpoint returns both an `access_token` and a separate `id_token`
when scope includes `openid`.

**Why parked:** API gateway OIDC plugins primarily validate access
tokens. ID tokens matter for browser-based OIDC login flows — most
gateway plugin tests don't exercise them.

**When to revisit:** if you start testing browser-based OIDC login flows
where the post-auth redirect carries the ID token.

### 🟡 Authorization Code grant (RFC 6749 §4.1)

`GET /{issuer}/authorize` with redirect handling, PKCE,
`response_type=code`, then code exchange at `/token`.

**Why parked:** complex (state/nonce tracking, PKCE verification,
redirect URI matching). Not needed for service-to-service or ROPC flows.

**When to revisit:** if you start testing interactive web login paths
end-to-end.

### 🟡 Device Code grant (RFC 8628)

CLI-style: device polls, user authorizes in a browser. Niche.

**When to revisit:** if you're explicitly testing device-code clients.

### 🟡 Token revocation (RFC 7009)

`POST /revoke` lets a client tell the identity provider "invalidate this
token." The mock would track revoked tokens and reject them on
introspection / userinfo.

**Why parked:** revocation matters mostly when paired with introspection.
If using JWT-validation-only (which most gateway OIDC plugins do),
revocation doesn't bite — tokens are valid until `exp`.

**When to revisit:** once token introspection lands; revocation without
introspection has limited test value.

### 🟡 OIDC end-session endpoint

`GET /{issuer}/logout` — the OIDC logout flow.

**When to revisit:** if the gateway starts mediating logout flows.

### 🟡 Client assertion auth (private_key_jwt, client_secret_jwt)

Instead of `client_secret`, the client signs a JWT proving its identity.
RFC 7521 / RFC 7523.

**Why parked:** most M2M flows still use `client_secret`. Real test
value is limited.

**When to revisit:** if you start using cert-based client auth in
production.

### 🟡 Smoke test failure opens a GitHub issue automatically

When the CI smoke test fails, automatically open a GitHub issue with:
- Which step failed (healthz / password grant / client_credentials grant)
- Container logs (`docker logs mock-idp-smoke`)
- Commit SHA, branch, run URL
- Label `bug` + `smoke-test-failure` for triage

Implementation: add an `if: failure()` step after the smoke test using
`gh issue create` with the `GITHUB_TOKEN`. Close the issue automatically
if a subsequent run on the same branch passes (query open issues by label
and SHA prefix, close with a comment).

**When to revisit:** once the smoke test has proven stable and the team
starts reacting to failures from the issue tracker rather than watching
CI directly.

### 🟡 Self-hosted CI runner on Proxmox k3s

Deploy a self-hosted GitHub Actions runner onto a k3s cluster running on
Proxmox in the lab. Enables a full cluster smoke test in CI:
deploy manifest → port-forward → hit endpoints → assert token claims.

**Why parked:** requires stable Proxmox k3s setup and GitHub runner registration.
The Docker-based smoke test in the current CI (issue #30) covers the critical path.

**When to revisit:** when the Proxmox k3s setup is operational. See
[pro-enterprise.md](pro-enterprise.md) for the broader infrastructure plan.

### 🟡 Configurable Cache-Control on JWKS and discovery

Lets you simulate "JWKS cached for 1 second" or "JWKS cached for an
hour" to test the gateway's cache behavior independently.

**When to revisit:** if cache-related bugs start showing up in gateway
JWKS handling.

### 🟡 Webhook delivery retries / dead-letter

If webhooks land (see v0.4 candidates), eventually they fail.
Production-grade behavior: retry, then DLQ.

**When to revisit:** when webhook reliability becomes a real concern.

---

## Maybe-never (concrete use case required)

### 🔴 Sample artifact generators

- `GET /debug/postman.json` — Postman collection
- `GET /debug/insomnia.json` — Insomnia workspace
- `GET /debug/curl-examples.sh` — bash script of every example

Cute. But each one is a maintenance liability if the underlying surface
changes. Build only if the onboarding pain is real.

### ~~🔴 Browser-based admin UI for editing identities~~

Promoted to v0.8.0 milestone. Required for hosted Pro self-service onboarding.

### 🔴 Multi-tenant simulation with separate user pools per issuer

Each issuer path has a different `USERS` dict, modeling separate tenants
with non-overlapping identities.

**Why maybe-never:** the current model (one config, all users accessible
at any issuer) is fine for almost every test. If you need strict tenant
isolation, run multiple mock pods with separate configs — that's both
simpler and more realistic.

### ~~🔴 Prometheus / OTel metrics~~

Promoted to v0.6.0 milestone. Required for hosted SaaS SLO monitoring.

### 🔴 Persistent revoked-tokens / persistent issued-tokens

Store issued tokens for replay-attack testing. Track revoked tokens
across pod restarts.

**Why maybe-never:** persistence drags in real ops concerns. For a test
fixture, fresh-start-per-restart is ideal. The Postgres backend (v0.4)
makes this possible if the need ever becomes concrete.

---

## Resolved

### CI/CD hardening (milestone #1, post-v0.5.6)

- ✓ **Version-prefixed sha draft releases** — every push to main creates a
  `<version>-sha-<hash>` GHCR image + GitHub Draft Release. Smoke test gates push.
- ✓ **Cleanup script** — `.github/scripts/cleanup-sha-artifacts.sh` deletes stale
  sha drafts and GHCR images when a new sha build or release tag succeeds.
- ✓ **Branch protection** — `lint-test` + `build` required status checks on main;
  `enforce_admins: false` for owner bypass.
- ✓ **Helm lint + appVersion sync check** — CI enforces `chart/Chart.yaml appVersion`
  matches `pyproject.toml` version (closes #37).
- ✓ **Token exchange smoke test** — RFC 8693 grant type added to the CI smoke test
  (closes #38).
- ✓ **SHA-pinned actions** — all GitHub Actions pinned to commit SHAs; Dependabot
  manages `github-actions` and `uv` ecosystems weekly (closes #39, #40).

### v0.5.6

- ✓ **joserfc migration (issue #31)** — replaced `authlib.jose` with `joserfc`
  (the library authlib itself recommends). Eliminates `AuthlibDeprecationWarning`
  on startup. `verify_token` simplified from manual kid-loop to `KeySet` decode.
  `authlib` removed from the dependency tree entirely.
- ✓ **Playground role selector fix (issue #29)** — all roles defined on a
  client app now appear as checkboxes; granted roles are pre-checked, non-granted
  roles unchecked (dimmed, available for negative testing). Previously only the
  identity's own grants appeared.
- ✓ **k8s manifest updated to current schema** — ConfigMap migrated from
  flat pre-v0.3 layout to current `tenants:` schema with `service_principals:`,
  `clients:`, and `admin_token: {from_env: ...}`.

### CI / infrastructure

- ✓ **Docker smoke test gates image push (issue #30)** — CI builds image
  locally (`load: true, push: false`), runs `/healthz` + password grant +
  client_credentials grant against the container. Image is tagged and pushed
  only if all three pass. Trivy scans the locally built image. Eliminates the
  v0.5.2/v0.5.3 class of bugs where a startup crash reached `:latest`.
- ✓ **Playground bug: all app roles shown in role selector (issue #29)** —
  `renderRoleCheckboxes(availableRoles, checkedRoles)` now renders every role
  defined on the client app, with granted roles pre-checked and non-granted
  roles unchecked (available for negative testing). Previously only the
  identity's own grants appeared.

### v0.5

- ✓ **Realm roles (v0.5.5, issue #20)** — `realm_roles` on `TenantRecord` (applies
  to all identities in the tenant) and on `UserRecord` / `ServicePrincipalRecord`
  (per-identity). `resolve_roles()` merges: `tenant.realm_roles + identity.realm_roles
  + audience_specific_roles`, deduped, first-occurrence wins. Works with
  `X-Override-Roles` override (replaces the full merged list). 4 new tests (S82–S84).
- ✓ **Secret management — from_env / from_file (v0.5.4, issue #27)** —
  `admin_token`, user `password`, and SP `secret` fields accept
  `{from_env: VAR}` or `{from_file: /path}` in addition to plain strings.
  Resolved at startup and on hot-reload; missing var/file exits with a clear
  error at startup, logs and preserves state on reload. 5 new tests (S79–S81).
- ✓ **Role selector in playground + X-Override-Roles header (v0.5.3, issue #26)** —
  `X-Override-Roles: role1,role2` header accepted on all three grant types
  (`password`, `client_credentials`, `token-exchange`). When present, replaces
  `resolve_roles()` output verbatim; empty string → no `roles` claim. Playground
  adds a checkbox per resolved role (all checked by default) for the selected
  identity + audience; unchecking sends the header automatically and includes it
  in the generated curl snippet. 4 new tests (S76–S78).
- ✓ **Webhook on token issuance (v0.5.2)** — top-level `webhooks:` list in config;
  each entry has `url`, `events: [token_issued]`, and `timeout_seconds: 5`.
  The mock POSTs `{event, timestamp, issuer, grant_type, claims}` to each matching URL
  after every successful `/token` call. Delivery is fire-and-forget — failures are
  logged at WARNING and never block token issuance. 5 new tests (happy path for
  password + client_credentials, no-op when unconfigured, failure isolation,
  event filter). See S72–S75 in test-scenarios.md.
- ✓ **Configurable signing algorithm per identity (v0.5.1)** — `signing_alg: RS256`
  (default) or `signing_alg: ES256` on any user or service principal. Each issuer
  now also holds an EC P-256 keypair alongside the RSA-2048 pair; JWKS publishes
  both signing keys (4 keys total: RSA signing, EC signing, 2 RSA decoys). `sign()`
  auto-detects the algorithm from the key type. Discovery advertises
  `["RS256", "ES256"]`. `config.example.yaml` sets `service-b` to ES256. 5 new tests.
  See S67–S71 in test-scenarios.md.
- ✓ **Per-issuer signing keys (v0.5.0)** — each issuer path now has its own RSA-2048
  keypair (signing + unpublished alt + 2 decoys), created lazily on first use.
  `/{issuer}/jwks` returns only that issuer's keys. `POST /admin/rotate-jwks?issuer=<slug>`
  rotates one issuer; omitting `?issuer=` rotates all known issuers. `/debug/config`
  returns `signing_kids: {issuer: kid}` dict. 3 new isolation tests (distinct kids,
  cross-issuer verify fails, single-issuer rotate leaves others untouched). See ADR-004.

### v0.4

- ✓ **v0.4.3 patch** — `verify_token()` helper in `tokens.py` was accidentally left
  unstaged when v0.4.2 was committed; the tagged image failed on startup with
  `ImportError`. OS-level Trivy CVEs resolved by adding `apt-get upgrade` to the
  Dockerfile. Dependabot added for weekly Python and GitHub Actions updates. Trivy SARIF
  results now uploaded to the GitHub Security tab.
- ✓ **Postgres backend (`PostgresIdentityStore`, v0.4.0)** — `asyncpg`-backed store behind
  the `IdentityStore` protocol. `MOCK_IDP_BACKEND=postgres` + `MOCK_IDP_PG_DSN` selects it.
  Schema managed by Alembic (`alembic upgrade head`). `startup()`/`shutdown()` lifecycle
  methods for pool management. `POST /admin/reload-config` triggers reload on all backends.
  `IdentityStore.reload()` is now `async`; `startup`/`shutdown` added to the protocol.
  Optional deps: `asyncpg`, `sqlalchemy[asyncio]`, `alembic` (install with
  `uv sync --extra postgres`). See ADR-003 §Adding a new backend.
- ✓ **Token introspection (RFC 7662, v0.4.1)** — `POST /{issuer}/introspect`. Caller
  authenticates with `client_id` + `client_secret` (service principal). Returns
  `{"active": true, ...claims}` for a valid non-expired token, `{"active": false}` for
  anything else. Discovery doc advertises `introspection_endpoint`. 8 new tests.
- ✓ **OAuth 2.0 Token Exchange (RFC 8693, v0.4.2)** — new `token-exchange` grant type on
  `POST /{issuer}/token`. Intermediary authenticates, inbound `subject_token` is verified
  (signature + expiry). Outbound token preserves `sub`, `oid`, `tid`, `preferred_username`
  from the inbound token; adds `act = {"sub": intermediary}`. Roles resolved for the
  intermediary against the requested audience. Response includes `issued_token_type`. 8 new
  tests. Discovery `grant_types_supported` updated.

### v0.3

- ✓ **Tenant-keyed config schema (v0.2)** — `tid` hoisted from individual identity
  records to the grouping key. `users:` and `clients:` nest under
  `tenants: {<tid>: {...}}`. Eliminates repeated `tid` on every record.
- ✓ **Provider plugin architecture (v0.3)** — `providers/` module; dispatch by `provider:`
  field on `TenantRecord` (default `entra_id`). Claim-shape emulation only. See ADR-002.
- ✓ **Entra ID rich grants model (v0.3)** — `service_principals:` for machine identities,
  `clients:` for resource apps with `grants:` per identity. `resolve_roles()` uses grants
  table when a `ClientAppRecord` exists; falls back to flat `roles` otherwise.
- ✓ **Feature gates are implicit (v0.3)** — presence of `clients:` grants block activates
  grants model; no explicit `features:` flag.
- ✓ **Playground update (v0.3.1)** — audience dropdown from `client_apps`,
  `service_principals` identity group, resolved-roles display per audience.
- ✓ **Playground testing overrides + sig verification (v0.3.2)** — collapsible
  "Testing overrides" panel; signature verification badge via `POST /debug/decode`.
- ✓ **Admin `iss` override (v0.3.3)** — `override_iss_too: true` flag on an admin SP.
- ✓ **Slow / failing endpoints (v0.3.4)** — `X-Test-Delay-Ms` and `X-Test-Fail` headers.
- ✓ **Multi-key JWKS (v0.3.5)** — 3 keys: 1 active + 2 decoys. Tests kid-based selection.
- ✓ **Per-issuer `auth_mode` (v0.3.6)** — `issuer_modes: {slug: lax|strict}` in config.
- ✓ **Algorithm-failure negative endpoints (v0.3.7)** — `token/unsigned` (alg:none) and
  `token/wrong-alg` (HS256 confusion). Playground Token variant selector.
- ✓ **Config pre-lint and better validation errors (v0.3.8)** — structural lint, difflib
  "did you mean?" suggestions, `extra='forbid'` on `AppConfig` and `TenantRecord`.
- ✓ **Pluggable identity store + config hot-reload (v0.3.9)** — `IdentityStore` protocol,
  `YamlIdentityStore`, `create_store()` factory, `watchfiles` file watcher. ConfigMap
  remounts picked up with no pod restart. See ADR-003.

### v0.2 (historical)

- ✓ Password grant `client_id` optional.
- ✓ `resource` and `scope` both accepted; `resource` wins; `/.default` stripped.
- ✓ Default `aud` = `api://default`.
- ✓ Lax / strict audience gating; per-identity `allowed_audiences`.
- ✓ Token shape resolution: header > suffix > config > v2.
- ✓ Mnemonic identity aliases via `client_id` field.
- ✓ Per-identity `token_lifetime_seconds`.
- ✓ `extra_claims` merged verbatim into the token.
- ✓ Admin claim override (`override_any_claim: true`).
- ✓ Admin key rotation (`POST /admin/rotate-jwks`).
- ✓ Token playground (`GET /`).
- ✓ Debug endpoints (`/debug/decode`, `/debug/identities`, `/debug/config`).
- ✓ CORS middleware; `cors_allow_origins` configurable.

---

## Process notes

- Every roadmap item should land with: code, tests, a test-scenario doc
  entry, and a config schema update if applicable.
- Pull items in one at a time based on what tests actually demand.
- If a parked item becomes a candidate, move it up (not delete and re-add)
  so the discussion history is preserved.
- If a maybe-never item gets a concrete use case, write the use case
  down here before reclassifying.
