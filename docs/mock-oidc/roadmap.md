# Roadmap & Future Considerations — Python Mock OIDC

What's not yet shipped, what's worth doing next, and what's parked unless a
specific need surfaces.

Current release is **v0.3.2**. The v0.3 surface is documented in ADR-002 and
the commit history. This file exists so design conversations stay grounded —
if someone says "we should add X", you can check whether X is already on the
list and what the thinking was at the time.

---

## Status legend

- 🟢 **v0.3 candidate** — meaningful next step; build when a real test demands it
- 🟡 **Parked** — useful but no concrete demand yet; revisit when one shows up
- 🔴 **Maybe-never** — build only if a concrete and well-scoped use case appears

---

## v0.3 candidates

### 🟢 Secret management integration

Load passwords, client secrets, and the admin token from external secret
stores instead of plain text in the YAML config. Two target surfaces:

- **Environment variables or mounted files** — read secrets from env vars
  or files at startup (e.g., Kubernetes Secret mounted at a path).
  Zero-dependency; works in any environment.
- **Vault / secrets manager** — use `hvac` (HashiCorp Vault) or a
  cloud-provider SDK to pull secrets at startup. Config references a
  path (`vault://secret/mock-idp/clients`) instead of a literal value.

**Why:** plain-text secrets in a ConfigMap are acceptable for a test
fixture but problematic in environments with secret-scanning CI checks
or stricter compliance posture. Even for a test tool, secrets-in-config
is a bad habit.

**Shape:**

```yaml
clients:
  service-a:
    secret:
      from_env: MOCK_IDP_SERVICE_A_SECRET   # reads os.environ
      # or:
      from_file: /var/run/secrets/service-a  # reads file contents
      # or:
      from_vault: secret/data/mock-idp/service-a  # hvac lookup
```

**Effort:** ~50 LOC; add `hvac` as an optional dependency; config
loading layer resolves secret references before populating the identity
store.

### 🟢 OAuth 2.0 Token Exchange (RFC 8693)

Lets an intermediary (e.g., an API gateway) hand in an inbound token
and get back a new token for a different audience, with the original
subject preserved and an `act` claim recording the actor chain.

**Why:** the natural pattern for "the gateway receives a user's token,
needs to mint a service token to call upstream on the user's behalf."
See `docs/architecture.md` §Flows for a diagram.

**Shape:**

```text
POST /{issuer}/token
grant_type=urn:ietf:params:oauth:grant-type:token-exchange
subject_token=<inbound-jwt>
subject_token_type=urn:ietf:params:oauth:token-type:access_token
client_id=<intermediary>
client_secret=<secret>
audience=<destination>
```

Resulting token: `sub` and `preferred_username` preserved from
`subject_token`; `azp` = intermediary; `act = { sub: intermediary }`.

**Effort:** ~50 LOC + tests.

### 🟢 Token introspection (RFC 7662)

```text
POST /{issuer}/introspect
token=<jwt>
client_id=<caller>
client_secret=<secret>
```

Returns `{"active": true, "sub": ..., "scope": ...}` or
`{"active": false}`.

**Why:** for upstream services or gateway plugins that prefer to call
back to the identity provider rather than do local JWT validation. Also
useful for testing revocation scenarios.

**Effort:** ~30 LOC + tests.

### 🟢 Algorithm-failure negative endpoints

Two endpoints for security-test paths:

```text
POST /{issuer}/token/unsigned          Returns a JWT with alg: "none"
POST /{issuer}/token/wrong-alg         Returns a JWT signed HS256 with
                                       the RSA public key as secret
```

**Why:** confirm the gateway rejects both. These are real-world
JWT-validator attacks; a mock that can produce them lets you
regression-test the defense.

**Effort:** ~40 LOC + tests.

### 🟢 Multi-key JWKS

`/jwks` returns 2–3 keys. The current signing key is one; the other
1–2 are dummy keys with distinct `kid`s.

**Why:** test the gateway's "pick the right key by `kid`" path. Currently
JWKS has one key; the gateway can't fail the lookup if there's only one
option.

**Effort:** ~20 LOC + tests.

### 🟢 Slow / failing endpoints

Test override headers:

```text
X-Test-Delay-Ms: 5000      Adds 5s sleep before response
X-Test-Fail: 1             Returns 500 instead of token / JWKS
```

**Why:** test the gateway's timeout and retry behavior against the OIDC
provider. JWKS-fetch and discovery-fetch timeouts are real concerns in
production.

**Effort:** ~15 LOC + tests.

### 🟢 Per-issuer signing keys

Each issuer path gets its own keypair. Currently all issuers share one
signing key.

**Why:** closer to real multi-tenant identity providers (each tenant has
its own keys). Useful for testing JWKS-isolation correctness — confirm
that a token from issuer A doesn't validate against issuer B's JWKS even
if they have the same `kid`.

**Effort:** ~25 LOC; change signing key from module-level to a
per-issuer dict; update `/jwks` and signing helpers.

### 🟢 Per-issuer `auth_mode`

Override the global `auth_mode` per issuer. Lets one mock serve both
lax and strict tests via different paths.

```yaml
issuers:
  default: { auth_mode: lax }
  strict-tenant: { auth_mode: strict }
```

**Effort:** ~20 LOC; config schema addition; resolve mode per-request
by issuer path.

### 🟢 Webhook on token issuance

Configurable URL the mock POSTs to on every successful token issuance,
with the request + token claims. Lets integration tests assert "what
was issued" without scraping logs.

```yaml
webhooks:
  - url: http://test-recorder.example.com/events
    events: [token_issued]
```

**Effort:** ~30 LOC + tests; async HTTP client (httpx).

### 🟢 Configurable signing algorithm per identity

```yaml
clients:
  service-a:
    signing_alg: RS256       # default
  service-b:
    signing_alg: ES256       # different curve for alg-agility testing
```

**Why:** confirm the gateway handles multi-alg JWKS gracefully.

**Effort:** ~15 LOC; add `signing_alg` field; expand the key dict.

### 🟢 Config hot-reload

Watch the config file for changes (via `watchfiles`) and reload the
identity store without a pod restart. Signing keys stay; only the
identity tables refresh.

**Why:** speeds up the test-iteration loop. Edit `config.yaml`, see
new behavior in ~1s.

**Effort:** ~40 LOC; add `watchfiles` dependency; careful with
concurrent read-while-replacing.

**Gating:** opt-in via `enable_hot_reload: true` in config. Off by
default so the operational behavior in production-ish environments stays
predictable.

### 🟢 Realm roles (Keycloak-influenced, optional)

Tenant-level role assignments for directory-scoped roles (e.g. `Global.Reader`)
that appear in every token regardless of audience. Merged alongside resource-scoped
grants from the clients block.

Deferred from v0.3 committed — no concrete test demand yet. When a use case arrives,
the shape and merge logic are documented in ADR-002 §Decision.

### 🟢 Admin overrides include `iss` claim

Currently admin can override almost any claim, but `iss` was
intentionally NOT overridable to prevent accidental footguns. For
serious negative testing of the gateway's issuer validation, allowing it
would be useful.

**Resolution path:** gate the `iss` override behind a stronger flag
(`override_iss_too: true` on the admin client) so it's an explicit
opt-in.

**Effort:** ~5 LOC.

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

**When to revisit:** if introspection lands as v0.3 and you want to
exercise the revocation → introspection path.

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

### 🟡 Configurable Cache-Control on JWKS and discovery

Lets you simulate "JWKS cached for 1 second" or "JWKS cached for an
hour" to test the gateway's cache behavior independently.

**When to revisit:** if cache-related bugs start showing up in gateway
JWKS handling.

### 🟡 Webhook delivery retries / dead-letter

If webhooks land (see v0.3 candidates), eventually they fail.
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

### 🔴 Browser-based admin UI for editing identities

A web form for adding/editing users at runtime without editing YAML.

**Why maybe-never:** YAML editing + pod restart is fast enough for a
test fixture. A UI doubles the surface area and adds an actual
auth-and-authz problem to solve.

### 🔴 Multi-tenant simulation with separate user pools per issuer

Each issuer path has a different `USERS` dict, modeling separate tenants
with non-overlapping identities.

**Why maybe-never:** the current model (one config, all users accessible
at any issuer) is fine for almost every test. If you need strict tenant
isolation, run multiple mock pods with separate configs — that's both
simpler and more realistic.

### 🔴 Prometheus / OTel metrics

`fastapi-instrumentator` makes this a one-import. Useful for production
observability, less useful for a test fixture without a SLO.

**Why maybe-never:** "scrape access logs" is sufficient. Don't over-build
observability for a test tool.

### 🔴 Persistent revoked-tokens / persistent issued-tokens

Store issued tokens for replay-attack testing. Track revoked tokens
across pod restarts.

**Why maybe-never:** persistence drags in real ops concerns. For a test
fixture, fresh-start-per-restart is ideal.

---

## Resolved (formerly questions)

- ✓ **Tenant-keyed config schema (v0.2)** — `tid` hoisted from individual identity
  records to the grouping key. `users:` and `clients:` nest under
  `tenants: {<tid>: {...}}`. Eliminates repeated `tid` on every record; enables
  multi-tenant configs in a single file.
- ✓ **Provider plugin architecture (v0.3)** — `providers/` module; dispatch by `provider:`
  field on `TenantRecord` (default `entra_id`). Claim-shape emulation only, not full
  flow emulation. See ADR-002.
- ✓ **Entra ID rich grants model (v0.3)** — `service_principals:` for machine identities,
  `clients:` for resource apps with `grants:` per identity. `resolve_roles()` uses grants
  table when a `ClientAppRecord` exists; falls back to flat `roles` otherwise. SP grants
  resolve by original config name, not UUID alias. See ADR-002.
- ✓ **Feature gates are implicit (v0.3)** — presence of `clients:` grants block activates
  grants model; no explicit `features:` flag. Simple config stays simple.
- ✓ **Playground update (v0.3.1)** — audience dropdown from `client_apps`,
  `service_principals` identity group, resolved-roles display per audience.
- ✓ **Playground testing overrides + sig verification (v0.3.2)** — collapsible
  "Testing overrides" panel with `X-Test-Expired` checkbox and `X-Omit-Claims` text
  input; both headers reflected in the generated curl snippet. Signature verification
  badge (`✓`/`✗`) in the JWT card, resolved asynchronously via `POST /debug/decode`.

These were once open questions; resolved during v0.2 design:

- ✓ **Password grant client_id** — optional. Provide it to populate
  `appid`/`azp`; omit it for simpler tests.
- ✓ **Resource parameter name** — accept both `resource` and `scope`;
  `resource` wins; `/.default` suffix stripped from `scope`.
- ✓ **Default `aud` when neither provided** — `api://default`.
- ✓ **Lax / strict audience gating** — global config field;
  per-identity `allowed_audiences`; admin bypasses.
- ✓ **Token shape resolution priority** — header > suffix > config > v2.
- ✓ **Mnemonic identity aliases** — supported via separate `client_id`
  field on the entry.
- ✓ **Per-identity token lifetime** — `token_lifetime_seconds` field.
- ✓ **Extra claims** — `extra_claims` field merged verbatim into the token.
- ✓ **Admin claim override** — `override_any_claim: true` flag;
  form-body fields replace claims; reserved fields enforced; bypasses
  strict audience.
- ✓ **Admin key rotation** — `POST /admin/rotate-jwks` gated by
  `X-Admin-Token`.
- ✓ **Token playground** — `GET /` serves an HTML page.
- ✓ **Debug endpoints** — `/debug/decode`, `/debug/identities`,
  `/debug/config`.
- ✓ **CORS** — middleware enabled by default; `cors_allow_origins`
  configurable.

---

## Process notes

- Every roadmap item should land with: code, tests, a test-scenario doc
  entry, and a config schema update if applicable.
- v0.3 items don't all need to ship together. Pull them in one at a
  time, based on what tests actually demand.
- If a parked item becomes a roadmap candidate, move it up (not delete
  and re-add) so the discussion history is preserved.
- If a maybe-never item gets a concrete use case, write the use case
  down here before reclassifying — that way the next reviewer can see
  *why* the bar moved.
