# Architecture — Python Mock OIDC

## Stack

| Component | Choice | Why |
|---|---|---|
| HTTP framework | **FastAPI** | Async, type-hinted, path-parameter routing fits multi-issuer cleanly; OpenAPI / Swagger UI comes free at `/docs` |
| ASGI server | **uvicorn** | Fast; hot-reload via `--reload` for the dev loop (file watching only — config changes still need a process restart) |
| JWT / JOSE | **authlib** | Mature crypto, supports RS256/ES256/HS256, handles JWKS export |
| Config format | **YAML** (pyyaml) | Human-readable identity store; mounted from a Kubernetes ConfigMap |
| Container base | **python:3.14-slim** | ~50 MB base, no compiler toolchain |
| Persistence | **none** | Signing keys generated on startup; identity store loaded once from YAML |

Runtime dependencies: `fastapi`, `uvicorn[standard]`, `authlib`,
`python-multipart`, `pyyaml`, `pydantic`.

---

## Endpoints

All endpoints are scoped under an issuer path. Multiple issuers coexist
on one process — distinguished only by the path prefix.

```text
Discovery / OIDC core
─────────────────────
GET  /healthz                                         Kubernetes liveness/readiness
GET  /{issuer}/.well-known/openid-configuration       OIDC discovery (RFC 8414)
GET  /{issuer}/jwks                                   JWKS (RFC 7517, public keys — per-issuer)
POST /{issuer}/token                                  Token endpoint (password / client_credentials /
                                                       token-exchange grants)
POST /{issuer}/introspect                             Token introspection (RFC 7662); SP auth required
GET  /{issuer}/userinfo                               UserInfo endpoint (OIDC Core §5.3)

Negative-case fixtures
──────────────────────
POST /{issuer}/token/wrong-sig                        Signs with unpublished alt key (per-issuer)
POST /{issuer}/token/unsigned                         alg:none, empty signature
POST /{issuer}/token/wrong-alg                        HS256 signed with RSA public key as HMAC secret
GET  /{issuer}/token/malformed                        Returns malformed JWT

Developer ergonomics
────────────────────
GET  /                                                Token playground (HTML)
POST /debug/decode                                    Decode any JWT; validates against all known issuers
GET  /debug/identities                                Loaded identities (secrets redacted)
GET  /debug/config                                    Effective runtime config; signing_kids per issuer

Admin
─────
POST /admin/rotate-jwks[?issuer=<slug>]               Rotate one issuer's signing key, or all if omitted
                                                       (gated by X-Admin-Token header)
POST /admin/reload-config                             Reload identity data from the backing store
                                                       without restarting (gated by X-Admin-Token)
```

The `issuer` path parameter is any URL-safe slug — `default`, `tenant-a`,
`tenant-b`, etc. Each forms a distinct `iss` claim value.

---

## Identity store

Identity records live in a YAML file mounted into the pod from a
ConfigMap. Loaded once at startup; restart the pod to pick up edits.
(Hot reload is a roadmap item.)

```yaml
# Authorization mode. "lax" (default) — resource/scope freeform.
# "strict" — each identity must list allowed_audiences; mismatches reject.
auth_mode: lax

# CORS: which origins may call this mock from a browser. ["*"] for fully permissive.
cors_allow_origins:
  - "*"

# Admin endpoints (e.g., /admin/rotate-jwks) require this header value.
admin_token: change-me-in-real-deployments

users:
  alice:
    password: alice-pw
    upn: alice@example.com
    preferred_username: alice@example.com
    oid: 11111111-1111-1111-1111-aaaaaaaaaaaa
    tid: 22222222-2222-2222-2222-222222222222
    token_version: v2
    token_lifetime_seconds: 300         # short-lived user token
    roles: [technician, noc]
    groups: [support-engineers]
    allowed_audiences:                  # required in strict, ignored in lax
      - api://serviceB
      - api://serviceC
    extra_claims:                       # arbitrary additional claims, merged verbatim
      department: engineering
      cost_center: cc-1234

clients:
  service-a:                            # mnemonic alias
    client_id: 01010101-1010-1010-1010-aaaaaaaaaaaa   # what appears in tokens
    secret: serviceA-secret
    label: ServiceA
    token_version: v1
    token_lifetime_seconds: 3600        # standard service-token lifetime
    roles: [automation]
    groups: [api-callers]
    allowed_audiences: [api://serviceB]
    extra_claims:
      tier: 1

  "00000000-0000-0000-0000-000000000000":   # no alias, UUID-only
    secret: admin-secret
    label: TestAdmin
    override_any_claim: true            # admin escape hatch; also bypasses strict
```

### Field reference

**Top-level**

| Field | Purpose |
|---|---|
| `auth_mode` | `lax` or `strict`. Default `lax`. |
| `cors_allow_origins` | List of origins allowed by CORS. Default `["*"]`. |
| `admin_token` | Required value of `X-Admin-Token` for admin endpoints. |

**Users**

| Field | Purpose |
|---|---|
| `password` | Strict equality check on `grant_type=password`. |
| `upn`, `preferred_username` | The `upn` (v1) / `preferred_username` (v2) claim. |
| `oid` | Object ID — appears as both `sub` and `oid` in the token. |
| `tid` | Tenant ID. Defaults if omitted. |
| `token_version` | `v1` or `v2`. Default token shape. |
| `token_lifetime_seconds` | Default expiry. Falls back to 3600. |
| `roles`, `groups` | List claims. |
| `allowed_audiences` | Required in strict mode; ignored in lax. |
| `extra_claims` | Free-form dict merged verbatim into the issued token. |

**Clients**

| Field | Purpose |
|---|---|
| `client_id` | If set on an aliased entry, this is what appears in tokens. If omitted, the YAML key is the client_id. |
| `secret` | Strict equality check on `grant_type=client_credentials`. |
| `label` | Human-readable; never appears in tokens. |
| `token_version`, `token_lifetime_seconds` | Same semantics as on users. |
| `roles`, `groups`, `tid`, `allowed_audiences`, `extra_claims` | Same as users. |
| `override_any_claim` | When `true`, form-body fields replace token claims and the strict audience check is bypassed. |

### Mnemonic aliases

A client entry may use a mnemonic key (e.g. `service-a`) with a
separate `client_id` field — that makes test code read better while the
token still shows the realistic UUID-shaped client_id.

```yaml
clients:
  service-a:                            # test code passes: client_id=service-a
    client_id: 01010101-...             # appears in the token's azp/appid
```

The mock looks up by alias first; if no `client_id` field is set, the
alias *is* the client_id.

---

## Grant types

The token endpoint dispatches on `grant_type`. Two grants supported in
v0.2 (Token Exchange is a roadmap item):

### `password` — user identity (Resource Owner Password Credentials, RFC 6749 §4.3)

```text
POST /{issuer}/token
Content-Type: application/x-www-form-urlencoded

grant_type=password
username=<name>
password=<password>
resource=<destination>          # or scope=<destination>/.default
client_id=<oauth-client>        # optional
```

Behavior:

1. Look up `username` in `users`.
2. Compare `password` field strict-equal.
3. If `auth_mode: strict`, verify `resource` ∈ `allowed_audiences`.
4. Resolve token shape (header > suffix > config > v2).
5. Build claims (`sub`/`oid`, `upn`|`preferred_username`, roles, groups,
   `aud`, optional `appid`|`azp`, plus `extra_claims`).
6. Sign with published key, return.

### `client_credentials` — service identity (RFC 6749 §4.4)

```text
POST /{issuer}/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
client_id=<requester>
client_secret=<secret>
resource=<destination>          # or scope=<destination>/.default
```

Behavior:

1. Look up `client_id` (or alias) in `clients`.
2. Compare `secret` strict-equal.
3. If `auth_mode: strict`, verify `resource` ∈ `allowed_audiences`
   (unless `override_any_claim: true`).
4. Resolve token shape.
5. Build claims (`sub`, `appid`|`azp` = client_id, roles, groups, `aud`,
   plus `extra_claims`).
6. If `override_any_claim`, apply form-body fields as claim overrides.
7. Sign with published key, return.

Any other `grant_type` value: `400 unsupported_grant_type`.

---

## Authorization mode (lax / strict)

Top-level `auth_mode`. Default `lax`.

| Mode | Behavior | When to use |
|---|---|---|
| `lax` | `resource` / `scope` freeform; `allowed_audiences` ignored. | Most tests where the mock attests "valid token shape" and authorization lives downstream. |
| `strict` | Requested `aud` must be in identity's `allowed_audiences`. Empty/missing list = deny all. | Testing the identity-provider rejection path; verifying upstream handling of `invalid_target`. |

Rejection response:

```json
HTTP/1.1 400 Bad Request
{
  "error": "invalid_target",
  "error_description": "Audience 'api://serviceX' is not in allowed_audiences for this identity."
}
```

Admin clients (`override_any_claim: true`) bypass the strict check.

---

## Token shape resolution

For each token request, the shape (v1 vs v2) is resolved by priority:

1. **`X-Token-Shape` header** — if set to `v1` or `v2`, wins
2. **`client_id` suffix** — `-v1` or `-v2`, second
3. **Config `token_version`** — default
4. **Fallback** — `v2`

| Field | v1 | v2 |
|---|---|---|
| Client identity | `appid` | `azp` |
| Username | `upn` (+ `unique_name`) | `preferred_username` |
| Version marker | `ver: "1.0"` | `ver: "2.0"` |

Every other claim (iss, aud, exp, iat, nbf, sub, oid, tid, roles,
groups, extra_claims) is identical across shapes.

---

## Resource → aud mapping

The destination audience is conveyed as:

- **`resource=<value>`** (v1 convention) → `aud = <value>`
- **`scope=<value>/.default`** (v2) → `aud = <value>` (suffix stripped)
- **`scope=<value>`** (no suffix) → `aud = <value>`

If both `resource` and `scope` are provided, `resource` wins. If neither,
`aud` defaults to `api://default`.

---

## Test override headers

| Header | Effect |
|---|---|
| `X-Token-Shape: v1\|v2` | Force token shape |
| `X-Omit-Claims: oid,tid,...` | Drop named claims from the issued token |
| `X-Test-Expired: 1` | Set `exp = now - 60` |
| `X-Test-Expires-In: <int>` | Set `exp = now + <int>` (negative allowed) |

---

## Admin override mechanics

For clients with `override_any_claim: true`, every form-body field that
isn't a reserved OAuth2 field becomes a claim in the issued token,
replacing the default.

Reserved (not treated as overrides): `grant_type`, `client_id`,
`client_secret`, `username`, `password`, `resource`, `scope`.

Coercion: `roles` / `groups` / `amr` split on commas to lists; `exp` /
`iat` / `nbf` parsed as int; everything else string verbatim.

Admin overrides also bypass the strict audience check.

---

## Developer ergonomics

### Token playground at `GET /`

A single HTML page. Pick an issuer, an identity (from the loaded store),
and a destination audience, click "Issue token". Shows:

- The raw JWT
- The decoded header + payload (pretty-printed JSON)
- A copy-to-clipboard `Authorization: Bearer <jwt>` snippet
- A copy-to-clipboard `curl` example matching the choice

Identities come from `GET /debug/identities`. No build step; everything
is rendered HTML so it works in any browser without compilation.

### `POST /debug/decode`

```text
POST /debug/decode
Content-Type: application/json

{"token": "eyJhbGciOiJSUzI1NiJ9...."}
```

Returns:

```json
{
  "header": {"alg": "RS256", "typ": "JWT", "kid": "..."},
  "payload": {"iss": "...", "sub": "...", ...},
  "signature_validated_against_published_key": true
}
```

Decodes any JWT — useful for "what's actually in this token?"
investigations. Validation against the published key is informational
(true/false), not enforced.

### `GET /debug/identities` and `GET /debug/config`

`/debug/identities` returns the loaded user and client store with all
secrets and passwords replaced by `"***"`. Lets you verify the mock has
the config you think it does without exposing credentials.

`/debug/config` returns the effective runtime config — `auth_mode`,
CORS settings, key thumbprints, count of identities loaded, JWKS URL.
No secrets.

### `POST /admin/rotate-jwks`

```text
# Rotate one issuer's signing key:
POST /admin/rotate-jwks?issuer=default
X-Admin-Token: <admin_token from config>
→ {"status": "rotated", "new_signing_kid": "mock-default-2"}

# Rotate all currently-known issuers:
POST /admin/rotate-jwks
X-Admin-Token: <admin_token from config>
→ {"status": "rotated", "issuers": {"default": "mock-default-2", "tenant-a": "mock-tenant-a-2"}}
```

Replaces the active signing key for the specified issuer (or all known
issuers). Previously-issued tokens for those issuers stop validating.
The new public key appears in `/{issuer}/jwks` immediately.

Use for: testing JWKS-cache-invalidation behavior on the gateway. Without
this endpoint, key rotation requires a pod restart.

---

## CORS

`CORSMiddleware` is wired during app startup. The allowed origins come
from `cors_allow_origins` in the config; the default `["*"]` is
sufficient for any test-fixture use case.

Allowed methods: `GET`, `POST`, `OPTIONS`. Allowed headers: any.
Credentials: not allowed (the mock issues bearer tokens, not cookies).

---

## Signature key handling

Each issuer path gets its own set of RSA-2048 keys, created lazily on first
use. There is no shared global key. A request to `/tenant-a/jwks` returns
a completely different key set from `/tenant-b/jwks` — tokens signed by one
issuer cannot be verified against another issuer's JWKS.

Each issuer's key set contains four keys:

| Key | Kid pattern | Published? | Purpose |
|---|---|---|---|
| Signing | `mock-{issuer}-{n}` | Yes (first in JWKS) | Signs all normal tokens |
| Alt | `mock-{issuer}-alt` | No | Signs `/token/wrong-sig` tokens only |
| Decoy 1 | `mock-{issuer}-d1` | Yes | Published but never signs; tests kid-based selection |
| Decoy 2 | `mock-{issuer}-d2` | Yes | Same |

`POST /admin/rotate-jwks?issuer=<slug>` replaces only that issuer's signing
key (incrementing `n`). `POST /admin/rotate-jwks` (no `issuer=`) rotates all
currently-known issuers. Alt and decoy keys are not affected by rotation.

**Implications:**

- No persistence — pod restart regenerates all key stores.
- Single replica per pod — multiple replicas would generate independent key
  stores. `replicas: 1` is enforced.
- Tokens issued by a previous-generation key fail signature validation after
  rotation (intentional — that is the test).
- `/debug/config` returns `signing_kids: {"default": "mock-default-1", ...}`
  (a dict, not a scalar) listing the current signing kid per known issuer.

---

## Data flow (overview)

```text
┌─────────────┐                                 ┌──────────────────┐
│  Test       │   POST /default/token           │  Python Mock     │
│  client     │  ────────────────────────────►  │  (FastAPI)       │
│             │                                 │                  │
│             │  ◄── access_token (signed) ──── │                  │
└─────────────┘                                 └──────────────────┘
       │                                                ▲
       │ access_token                                   │ JWKS fetch
       ▼                                                │
┌─────────────┐                                 ┌──────────────────┐
│  API        │   GET /{issuer}/jwks            │  Python Mock     │
│  Gateway    │  ────────────────────────────►  │                  │
│  OIDC       │  ◄────  JWKS (public key) ────  │                  │
│  plugin     │                                 │                  │
└─────────────┘                                 └──────────────────┘
       │
       │ validate sig, validate claims
       ▼
┌─────────────┐
│  Upstream   │
│  service    │
└─────────────┘
```

JWKS is fetched by the gateway per its OIDC plugin config and cached
per plugin TTL. After a `/admin/rotate-jwks` call or pod restart, the
gateway's JWKS cache is stale until TTL elapses or a refresh is forced.

---

## Flows

### Password grant happy path

```text
┌──────────┐  POST /default/token                       ┌────────────┐
│  Test    │  grant_type=password                       │  Python    │
│  client  │  username=alice                            │  Mock      │
│          │  password=alice-pw                         │            │
│          │  resource=api://serviceB                   │  1. lookup │
│          │ ────────────────────────────────────────►  │     alice  │
│          │                                            │  2. pwd ok │
│          │                                            │  3. check  │
│          │                                            │     aud    │
│          │                                            │     (strict│
│          │                                            │      only) │
│          │                                            │  4. build  │
│          │                                            │     claims │
│          │                                            │  5. merge  │
│          │                                            │     extra_ │
│          │                                            │     claims │
│          │  ◄── { access_token: <jwt>, ... } ────────│  6. sign   │
└──────────┘                                            └────────────┘

Token claims (v2):
  { sub: alice-oid, oid: alice-oid,
    preferred_username: alice@example.com,
    aud: api://serviceB,
    roles: [technician, noc],
    groups: [support-engineers],
    department: engineering,           ← from extra_claims
    cost_center: cc-1234,              ← from extra_claims
    iss: .../default, ver: 2.0, exp: ... }
```

### M2M (client_credentials) happy path

```text
┌──────────┐  POST /default/token                       ┌────────────┐
│ ServiceA │  grant_type=client_credentials             │  Python    │
│ (caller) │  client_id=service-a   (alias)             │  Mock      │
│          │  client_secret=serviceA-secret             │            │
│          │  resource=api://serviceB                   │  1. lookup │
│          │ ────────────────────────────────────────►  │     by     │
│          │                                            │     alias  │
│          │                                            │  2. secret │
│          │                                            │     ok     │
│          │                                            │  3. check  │
│          │                                            │     aud    │
│          │  ◄── { access_token: <jwt>, ... } ────────│  4. build  │
└──────────┘                                            │     + sign │
                                                        └────────────┘

Token claims (v1):
  { sub: 01010101-...,
    appid: 01010101-...,             ← from client_id (UUID), not alias
    aud: api://serviceB,
    roles: [automation],
    groups: [api-callers],
    tier: 1,                          ← from extra_claims
    iss: .../default, ver: 1.0, exp: ... }
```

### Strict-mode rejection

```text
┌──────────┐  POST /default/token                       ┌────────────┐
│  Test    │  grant_type=password                       │  Python    │
│  client  │  username=alice                            │  Mock      │
│          │  password=alice-pw                         │  (strict)  │
│          │  resource=api://serviceZ  ← not in         │            │
│          │                            alice.allowed_  │  1. auth ok│
│          │                            audiences       │  2. aud    │
│          │ ────────────────────────────────────────►  │     check  │
│          │                                            │     FAILS  │
│          │  ◄── 400 invalid_target ──────────────────│            │
└──────────┘                                            └────────────┘
```

### Token Exchange (gateway as intermediary) — v0.3 roadmap

```text
┌──────────┐                       ┌──────────┐                       ┌────────────┐
│  Alice   │ Bearer <user-jwt>     │  API     │ POST .../token        │  Python    │
│ (caller) │ ────────────────────► │  Gateway │ grant_type=token-     │  Mock      │
│          │                       │          │   exchange            │            │
│          │                       │          │ subject_token=<jwt>   │  1. auth   │
│          │                       │          │ audience=api://       │     gateway│
│          │                       │          │   serviceB            │  2. decode │
│          │                       │          │ client_id=gateway     │     subj   │
│          │                       │          │ client_secret=...     │  3. mint   │
│          │                       │          │ ────────────────────► │     new    │
│          │                       │          │                       │     token  │
│          │                       │          │ ◄── { access_token,   │            │
│          │                       │          │      ... } ────────── │            │
│          │                       │          │ Bearer <new-jwt>      │            │
│          │                       │  Service │ ────────────────────► │  ServiceB  │
└──────────┘                       └──────────┘                       └────────────┘

New token claims (RFC 8693):
  { sub: alice-oid,                  ← preserved from subject_token
    preferred_username: alice@...,   ← preserved
    aud: api://serviceB,             ← new destination
    azp: gateway-client-id,          ← acting party
    act: { sub: gateway-client-id }, ← actor chain (RFC 8693 §4.1)
    iss: .../default, ver: 2.0, exp: ... }
```

The `act` claim makes the intermediary visible. ServiceB receives a
token whose subject is alice but whose acting party was the gateway.

---

## Cluster placement

Same cluster as the gateway under test, namespace `mock-idp`. Service
DNS makes it reachable from within the cluster. Ingress on
`mock-idp.example.com` for external test clients.

ConfigMap (`mock-idp-config`) mounted at `/etc/mock-idp/config.yaml`.

---

## Resource sizing

| Pod | CPU req / limit | Memory req / limit |
|---|---|---|
| mock-idp | 50m / 200m | 64Mi / 128Mi |

Idle: ~25 MB resident, near-zero CPU. Under 100 RPS of token issuance:
~50 MB, ~50 mCPU. Token playground HTML render is one-shot per
operator; negligible impact.

---

## Failure modes

| Failure | Impact | Recovery |
|---|---|---|
| Pod crash | All per-issuer key stores lost; previously-issued tokens fail | Restart → new keys → tests re-acquire |
| OOM (unlikely at this size) | Same as crash | Same |
| ConfigMap update without pod restart | New identities invisible until restart or hot-reload | `POST /admin/reload-config` or `kubectl rollout restart deployment/mock-idp` |
| Malformed config YAML on startup | Pod fails to start | Fix YAML, re-apply, restart |
| JWKS endpoint unreachable from gateway | Gateway fails token validation → 503/401 | Verify Service / Ingress; check NetworkPolicy |
| `/admin/rotate-jwks` called inadvertently | Existing in-flight tokens for affected issuers reject until clients reacquire | Document the test pattern; gate the endpoint behind `admin_token` |
| Token playground accidentally exposed publicly | Anyone with the URL can mint tokens | Internal-only ingress, not internet-facing |

No data persistence, so no data-loss failure modes.

---

## Troubleshooting

### Gateway returns 401 for a token that was just issued

1. **JWKS cache stale** — the gateway cached the old key set before a pod
   restart or `/admin/rotate-jwks` call. Force a JWKS cache flush in the
   gateway plugin config, or wait for the TTL to expire.

2. **Wrong issuer path** — the gateway's `issuer_url` points at
   `/tenant-a/...` but the token was minted at `/tenant-b/token`. The `iss`
   claim won't match. Confirm with `POST /debug/decode` — if
   `signature_validated_against_published_key` is `false` the keys don't
   match; check that both the token endpoint and the gateway's discovery URL
   use the same issuer slug.

3. **Token expired** — short `token_lifetime_seconds` or `X-Test-Expired: 1`
   was set. Re-issue.

### `POST /token` returns `401 invalid_grant` or `invalid_client`

- **Password grant:** username or password is wrong. Check the `users:` block
  in your config; passwords are compared as plain strings.
- **Client credentials:** `client_id` or `client_secret` is wrong. The
  `client_id` can be either the YAML key (mnemonic alias) or the `client_id`
  UUID field value — the mock accepts both.

### `POST /token` returns `400 invalid_target`

The issuer is in strict mode (`auth_mode: strict` or `issuer_modes:
{<slug>: strict}`) and the requested `resource` / `scope` is not in the
identity's `allowed_audiences`. Either add the audience to the config or
switch the issuer to `lax`.

### Pod starts but `/healthz` returns 500 or the pod never becomes Ready

The config file failed validation. Look at the pod logs:

```bash
kubectl logs -n mock-idp deploy/mock-idp
```

The YAML store logs a structured Pydantic validation error and exits with
`sys.exit(1)`. Common causes: numeric password (quote it in YAML), unknown
field name (check for typos — `extra='forbid'` is set on all models), or a
service principal nested under `users:`.

### `POST /introspect` returns `{"active": false}` for a valid token

1. **Wrong issuer's introspect endpoint** — introspect at `/{issuer}/introspect`
   uses that issuer's keys. A token from `/tenant-a/token` presented to
   `/tenant-b/introspect` will always return `{"active": false}`.

2. **Token is expired** — introspect checks `exp` after signature verification.
   Re-issue a fresh token.

3. **Caller not authenticated** — introspect requires a valid `client_id` +
   `client_secret` in the form body (any service principal). A missing or wrong
   credential returns `401`, not `{"active": false}`.

### Token Exchange returns `subject_token signature invalid`

The `subject_token` was issued by a different issuer than the one handling the
exchange request. The exchange endpoint uses `/{issuer}/jwks` to verify the
subject token. Issue the subject token from the same issuer path you're
exchanging at, or use the admin `override_any_claim` identity to bypass
verification for testing purposes.

### `/debug/config` shows empty `signing_kids`

No requests have been made to any `/{issuer}/...` endpoint yet — key stores
are created lazily. Issue at least one token or JWKS request first.

### Hot-reload didn't pick up my ConfigMap change

1. Confirm the file was actually remounted: `kubectl exec -n mock-idp deploy/mock-idp -- cat /etc/mock-idp/config.yaml`
2. The file watcher uses inotify. Check the pod logs for `Reloading config` messages.
3. Force a reload: `POST /admin/reload-config` with the correct `X-Admin-Token`.
4. CORS origins are **not** hot-reloadable — they require a pod restart.
