# Test Scenarios — Python Mock OIDC

Concrete request/response patterns for the v0.5.2 surface area. Use these
as the basis for a regression suite, ad-hoc curl tests, or gateway
integration tests.

The scenarios assume the default `config.example.yaml` is loaded.

---

## Token shape and v1/v2 normalization

### S1 — v1 shape, user

```bash
curl -X POST http://mock-idp.example.com/default/token \
  -H "X-Token-Shape: v1" \
  -d "grant_type=password&username=alice&password=alice-pw&resource=api://serviceB"
```

Token (decoded):

```json
{
  "iss": "http://mock-idp.example.com/default",
  "aud": "api://serviceB",
  "sub": "11111111-1111-1111-1111-aaaaaaaaaaaa",
  "oid": "11111111-1111-1111-1111-aaaaaaaaaaaa",
  "upn": "alice@example.com",
  "unique_name": "alice@example.com",
  "ver": "1.0",
  "roles": ["operator", "responder"],
  "groups": ["platform-team"],
  "department": "engineering",
  "tid": "22222222-2222-2222-2222-222222222222",
  "iat": ..., "nbf": ..., "exp": ...
}
```

The gateway should map `upn` to the normalized user identity header.

### S2 — v2 shape, user (default for alice)

```bash
curl -X POST http://mock-idp.example.com/default/token \
  -d "grant_type=password&username=alice&password=alice-pw&resource=api://serviceB"
```

Identical payload except: `preferred_username` instead of `upn`/`unique_name`,
`ver: "2.0"`.

The gateway should normalize both shapes to the same downstream header
regardless of `ver`. This is the core claim-normalization scenario.

### S3 — v1 shape, M2M (default for service-a)

```bash
curl -X POST http://mock-idp.example.com/default/token \
  -d "grant_type=client_credentials&client_id=service-a" \
  -d "client_secret=serviceA-secret&resource=api://serviceB"
```

Token:

```json
{
  "iss": "http://mock-idp.example.com/default",
  "aud": "api://serviceB",
  "sub": "01010101-1010-1010-1010-aaaaaaaaaaaa",
  "appid": "01010101-1010-1010-1010-aaaaaaaaaaaa",
  "ver": "1.0",
  "roles": ["m2m"],
  "groups": ["service-accounts"],
  "tier": 1,
  "tid": "22222222-2222-2222-2222-222222222222",
  "iat": ..., "exp": ...
}
```

Note: alias `service-a` was accepted as `client_id`; UUID appears in the token.

### S4 — v2 shape via header override

```bash
curl -X POST http://mock-idp.example.com/default/token \
  -H "X-Token-Shape: v2" \
  -d "grant_type=client_credentials&client_id=service-a" \
  -d "client_secret=serviceA-secret&resource=api://serviceB"
```

Same as S3 but with `azp` instead of `appid` and `ver: "2.0"`.

### S5 — v1 + v2 toggled on the same identity

Repeat S3 with `X-Token-Shape: v1` and `X-Token-Shape: v2`. The
gateway's claim normalization should produce identical downstream headers
despite the different source claim names (`appid` vs `azp`). This is
the core shape-agnosticism scenario.

---

## Audience handling

### S6 — `resource` parameter

```bash
curl ... -d "resource=api://specific-target"
```

`aud` claim is `api://specific-target`.

### S7 — `scope` with `/.default`

```bash
curl ... -d "scope=api://specific-target/.default"
```

`aud` claim is `api://specific-target` (`/.default` suffix stripped).

### S8 — Both `resource` and `scope`

```bash
curl ... -d "resource=api://res-value&scope=api://scope-value/.default"
```

`aud` = `api://res-value` (resource wins).

### S9 — Neither provided

```bash
curl ... -d "grant_type=client_credentials&client_id=service-a&client_secret=serviceA-secret"
```

`aud` defaults to `api://default`.

---

## Authentication failures

### S10 — Wrong password (user)

```bash
curl ... -d "grant_type=password&username=alice&password=wrong&resource=api://serviceB"
```

→ `401 invalid_grant`.

### S11 — Unknown user

```bash
curl ... -d "grant_type=password&username=ghost&password=any&resource=api://serviceB"
```

→ `401 invalid_grant`.

### S12 — Wrong client secret

```bash
curl ... -d "grant_type=client_credentials&client_id=service-a&client_secret=wrong&resource=api://serviceB"
```

→ `401 invalid_client`.

### S13 — Unknown client

```bash
curl ... -d "grant_type=client_credentials&client_id=ghost&client_secret=any&resource=api://serviceB"
```

→ `401 invalid_client`.

### S14 — Unsupported grant type

```bash
curl ... -d "grant_type=authorization_code&code=anything"
```

→ `400 unsupported_grant_type`.

---

## Lax / strict audience gating

The mock must be reloaded with `auth_mode: strict` for the rejection cases.

### S15 — Lax mode allows any audience

`auth_mode: lax`, alice's `allowed_audiences: [api://serviceB, api://serviceC]`.

```bash
curl ... -d "grant_type=password&username=alice&password=alice-pw&resource=api://anywhere"
```

→ 200, token has `aud: api://anywhere`.

### S16 — Strict mode allows listed audience

`auth_mode: strict`, alice's `allowed_audiences: [api://serviceB]`.

```bash
curl ... -d "resource=api://serviceB"
```

→ 200, token has `aud: api://serviceB`.

### S17 — Strict mode rejects unlisted audience

`auth_mode: strict`, alice's `allowed_audiences: [api://serviceB]`.

```bash
curl ... -d "resource=api://serviceZ"
```

→ 400 with:

```json
{
  "detail": {
    "error": "invalid_target",
    "error_description": "Audience 'api://serviceZ' is not in allowed_audiences for this identity."
  }
}
```

### S18 — Strict mode, missing `allowed_audiences`

Identity with no `allowed_audiences` field at all. `auth_mode: strict`.

→ 400 invalid_target for any audience. (Empty list = deny all; no
silent allow-all fallback.)

### S19 — Admin bypasses strict mode

`auth_mode: strict`, admin client has no `allowed_audiences` but
`override_any_claim: true`.

```bash
curl ... -d "grant_type=client_credentials" \
       -d "client_id=00000000-0000-0000-0000-000000000000" \
       -d "client_secret=admin-secret" \
       -d "resource=api://anywhere"
```

→ 200. Admin can request any audience.

---

## Admin override

### S20 — Replace simple claims

```bash
curl ... -d "grant_type=client_credentials" \
       -d "client_id=00000000-0000-0000-0000-000000000000" \
       -d "client_secret=admin-secret" \
       -d "resource=api://wherever" \
       -d "oid=custom-oid-value" \
       -d "tid=fake-tenant"
```

Token has `oid: custom-oid-value` and `tid: fake-tenant`.

### S21 — Replace list claims via CSV

```bash
curl ... -d "...admin..." -d "roles=admin,superuser,custom-role"
```

Token's `roles` claim is `["admin", "superuser", "custom-role"]`.

### S22 — Override `exp` to test future-dated tokens

```bash
curl ... -d "...admin..." -d "exp=4102444800"
```

`exp` is 2100-01-01 in Unix time. Useful for "what does the gateway do
with a 50-year-old token?"

### S23 — Inject arbitrary custom claim

```bash
curl ... -d "...admin..." -d "test_marker=this-is-a-special-test"
```

Token has `test_marker: "this-is-a-special-test"`. Useful for tests that
key on a known unique value in the payload.

### S24 — Reserved fields aren't overridable

```bash
curl ... -d "...admin..." -d "grant_type=spoofed&password=spoofed"
```

`grant_type` and `password` are reserved — treated as request fields,
not claim overrides. Token built normally; the spoofed values don't
appear in the payload.

---

## Token shape and lifetime per identity

### S25 — User token lifetime is shorter

```bash
curl -i ... -d "grant_type=password&username=alice&password=alice-pw&resource=api://serviceB"
```

`expires_in: 300` (from alice's `token_lifetime_seconds`).

### S26 — Service token lifetime is longer

```bash
curl -i ... -d "grant_type=client_credentials&client_id=service-a&client_secret=serviceA-secret&resource=api://serviceB"
```

`expires_in: 3600` (from service-a's `token_lifetime_seconds`).

### S27 — Override expiry via header

```bash
curl ... -H "X-Test-Expires-In: 60" ... -d "...alice..."
```

`expires_in: 60` regardless of config.

### S28 — Force-expired

```bash
curl ... -H "X-Test-Expired: 1" ... -d "...alice..."
```

Token has `exp = now - 60`. The gateway's OIDC plugin should reject at
signature + expiry validation.

---

## Extra claims

### S29 — User extra_claims included

Alice's config has `extra_claims: {department: engineering, cost_center: cc-1234}`.

```bash
curl ... -d "grant_type=password&username=alice&password=alice-pw&resource=api://serviceB"
```

Token has `department: "engineering"` and `cost_center: "cc-1234"`.

### S30 — Client extra_claims included

Service-a's config has `extra_claims: {tier: 1}`.

```bash
curl ... -d "grant_type=client_credentials&client_id=service-a&client_secret=serviceA-secret&resource=api://serviceB"
```

Token has `tier: 1`.

---

## Multiple issuers

### S31 — Distinct `iss` per issuer path

```bash
curl http://mock-idp.example.com/tenant-a/.well-known/openid-configuration
curl http://mock-idp.example.com/tenant-b/.well-known/openid-configuration
```

Each returns a discovery doc with a distinct `issuer` field. Tokens
minted from each path have matching `iss` claims. Use this to test the
gateway's per-route issuer config.

### S32 — Cross-issuer mismatch

Mint a token from `/tenant-a/token`. Configure a gateway route pointing
at `/tenant-b/.well-known/openid-configuration`. Submit the tenant-a
token to that route.

→ The gateway should reject (issuer claim doesn't match the configured
issuer URL). Confirms the gateway's issuer-validation is wired correctly.

---

## Selective claim omission

### S33 — Omit `oid` and `tid`

```bash
curl ... -H "X-Omit-Claims: oid,tid" ... -d "...alice..."
```

Token has no `oid` and no `tid`. The gateway should handle missing
optional claims without 500ing.

### S34 — Omit `iss` (negative test)

```bash
curl ... -H "X-Omit-Claims: iss" ... -d "...alice..."
```

Token has no `iss`. The gateway should reject at OIDC validation (issuer
validation per RFC 7519 §4.1.1).

### S35 — Omit `aud` (negative test)

```bash
curl ... -H "X-Omit-Claims: aud" ... -d "...alice..."
```

Token has no `aud`. The gateway should reject if configured to validate
audience (RFC 7519 §4.1.3).

---

## Signature failures

### S36 — Wrong-signature token

```bash
curl -X POST http://mock-idp.example.com/default/token/wrong-sig \
  -d "grant_type=client_credentials&client_id=service-a" \
  -d "client_secret=serviceA-secret&resource=api://serviceB"
```

Returns a structurally valid JWT signed by the unpublished alt key. The
token's `kid` references the alt key's thumbprint, which is **not** in
`/jwks`.

The gateway's OIDC plugin fetches JWKS, finds no matching `kid`, rejects
with 401. Confirms JWKS-driven signature validation (RFC 7517).

### S37 — Malformed JWT

```bash
curl http://mock-idp.example.com/default/token/malformed
```

Returns:

```json
{"access_token": "eyJhbGciOiJSUzI1NiJ9.this-is-not-base64-or-json.signature-bytes-garbage"}
```

Submit to the gateway → 401 at the JWT-parse step. Confirms
parsing-error handling.

---

## JWKS rotation

### S38 — Rotate one issuer's signing key

```bash
curl -X POST "http://mock-idp.example.com/admin/rotate-jwks?issuer=default" \
  -H "X-Admin-Token: change-me-in-real-deployments"
```

Response:

```json
{"status": "rotated", "new_signing_kid": "mock-default-2"}
```

Then submit a token issued before the rotation to a protected gateway
route. Gateway behavior depends on its JWKS cache TTL:

- If cache is still warm with the old key → 401
- After TTL elapses → gateway re-fetches JWKS, picks up the new key

Useful for testing JWKS cache invalidation behavior without disrupting
tokens for other issuers.

### S38b — Rotate all known issuers at once

```bash
curl -X POST http://mock-idp.example.com/admin/rotate-jwks \
  -H "X-Admin-Token: change-me-in-real-deployments"
```

Response:

```json
{"status": "rotated", "issuers": {"default": "mock-default-2", "tenant-a": "mock-tenant-a-2"}}
```

All in-flight tokens for all issuers are invalidated simultaneously.
Use at test-suite teardown for a clean slate.

### S39 — Wrong admin token

```bash
curl -X POST .../admin/rotate-jwks -H "X-Admin-Token: wrong"
```

→ 403.

---

## Debug endpoints

### S40 — Decode a token

```bash
TOKEN=$(curl -s -X POST .../default/token -d "..." | jq -r .access_token)

curl -X POST http://mock-idp.example.com/debug/decode \
  -H "Content-Type: application/json" \
  -d "{\"token\": \"$TOKEN\"}"
```

Returns header, payload, and `signature_validated_against_published_key: true`.

### S41 — Decode a wrong-sig token

```bash
TOKEN=$(curl -s -X POST .../default/token/wrong-sig -d "..." | jq -r .access_token)

curl -X POST .../debug/decode -d "{\"token\": \"$TOKEN\"}"
```

Returns header, payload, and `signature_validated_against_published_key: false`
(alt key is not published).

### S42 — Inspect loaded identities

```bash
curl http://mock-idp.example.com/debug/identities | jq .
```

Returns the user and client store with all `password` / `secret` /
`admin_token` values replaced by `"***"`.

### S43 — Inspect runtime config

```bash
curl http://mock-idp.example.com/debug/config | jq .
```

Returns `auth_mode`, CORS origins, ISS_BASE, identity counts, signing kid.

---

## Token playground (manual)

### S44 — End-to-end via the playground

1. Open `https://mock-idp.example.com/` in a browser.
2. Pick alice from the user dropdown.
3. Enter `api://serviceB` as the audience.
4. Click "Issue token".
5. See the JWT, decoded claims, and copy-to-clipboard snippets.
6. Paste the `Authorization: Bearer <jwt>` into your test client.

Useful for: non-developer testers, anyone iterating manually, anyone who
wants to inspect a token without writing curl by hand.

---

## Algorithm-failure negative endpoints (v0.3.7)

These endpoints enforce auth and audience checks normally, then issue an adversarially
crafted token. Use them to confirm the gateway's JWT validator rejects each attack.

### S46 — Unsigned token (alg:none)

```bash
curl -X POST http://mock-idp.example.com/default/token/unsigned \
  -d "grant_type=client_credentials&client_id=service-a" \
  -d "client_secret=serviceA-secret&resource=api://serviceB"
```

Returns a token with header `{"alg": "none", "typ": "JWT"}` and an empty signature.
Any conformant validator must reject `alg: none` tokens (per RFC 7518 §3.6 and the
algorithm confusion attack documented in CVE-2015-9235).

→ Gateway should return 401.

### S47 — Wrong-algorithm token (HS256 with RSA public key)

```bash
curl -X POST http://mock-idp.example.com/default/token/wrong-alg \
  -d "grant_type=client_credentials&client_id=service-a" \
  -d "client_secret=serviceA-secret&resource=api://serviceB"
```

Returns a token with header `{"alg": "HS256"}` signed using the RSA **public** key PEM
as the HMAC secret. A naïve validator that trusts the `alg` header and accepts `HS256`
will verify this successfully — that is the algorithm-confusion attack. A correctly
configured validator locks the allowed algorithms to RS256 and rejects this token.

→ Gateway should return 401.

---

## Slow / failing endpoints (v0.3.4)

### S48 — Simulated delay

```bash
curl -X POST http://mock-idp.example.com/default/token \
  -H "X-Test-Delay-Ms: 5000" \
  -d "grant_type=client_credentials&..."
```

Server sleeps 5 s before responding. Also honored on `GET /jwks` and
`GET /.well-known/openid-configuration`. Use to test gateway timeout and retry behavior.

### S49 — Forced server error

```bash
curl -X POST http://mock-idp.example.com/default/token \
  -H "X-Test-Fail: 1" \
  -d "..."
```

Returns `500 {"error": "server_error"}`. Honored on `/token`, `/jwks`, and discovery.
Use to test gateway circuit-breaker and error-handling paths.

---

## Multi-key JWKS (v0.3.5)

**What this tests:** A real identity provider publishes multiple public keys so that
gateways can validate tokens signed by any generation of key during a rotation window.
Gateways should select the key whose `kid` matches the token header — not blindly try
every key in the JWKS. These scenarios confirm that behavior.

### S50 — JWKS returns four keys

```bash
curl http://mock-idp.example.com/default/jwks | jq '[.keys[] | {kid, kty}]'
```

Returns 4 keys: one RSA signing key (`mock-default-1`, `kty: RSA`), one EC signing key
(`mock-default-ec-1`, `kty: EC`), and two RSA decoys (`mock-default-d1`, `mock-default-d2`).
A gateway that selects by `kid` will correctly use the key matching the token header; a
gateway that blindly tries every key may accept tokens signed by any of them.

---

## Per-issuer auth_mode (v0.3.6)

### S51 — Strict issuer rejects unlisted audience while global mode is lax

Config:

```yaml
auth_mode: lax
issuer_modes:
  strict-tenant: strict
```

```bash
curl -X POST http://mock-idp.example.com/strict-tenant/token \
  -d "grant_type=password&username=alice&password=alice-pw&resource=api://unlisted"
```

→ 400 `invalid_target` — `strict-tenant` issuer path applies strict gating even though
the global mode is lax.

### S52 — Lax issuer allows any audience while global mode is strict

Config:

```yaml
auth_mode: strict
issuer_modes:
  open-tenant: lax
```

```bash
curl -X POST http://mock-idp.example.com/open-tenant/token \
  -d "grant_type=password&username=alice&password=alice-pw&resource=api://anywhere"
```

→ 200 — `open-tenant` overrides to lax.

---

## Admin iss override (v0.3.3)

### S53 — Override `iss` with flag enabled

Config has an admin SP with both `override_any_claim: true` and `override_iss_too: true`.

```bash
curl -X POST http://mock-idp.example.com/default/token \
  -d "grant_type=client_credentials" \
  -d "client_id=00000000-0000-0000-0000-000000000000" \
  -d "client_secret=admin-secret" \
  -d "resource=api://anywhere" \
  -d "iss=https://evil.example.com/fake-issuer"
```

Token has `iss: "https://evil.example.com/fake-issuer"`. The gateway's issuer
validation should reject a token whose `iss` does not match the configured OIDC issuer.

### S54 — `iss` override blocked without flag

Same admin SP but `override_iss_too` is absent or false.

Same request as S53 → Token has the normal `iss` (override silently ignored).

---

## CORS preflight

### S45 — Preflight from a browser-based test client

A JS test runner at `http://localhost:3000` makes a fetch to
`/default/token`. The browser first sends:

```text
OPTIONS /default/token HTTP/1.1
Origin: http://localhost:3000
Access-Control-Request-Method: POST
Access-Control-Request-Headers: X-Token-Shape
```

The CORS middleware responds with `Access-Control-Allow-Origin: *` (or
the configured origin), `Access-Control-Allow-Methods: GET, POST, OPTIONS`,
and `Access-Control-Allow-Headers: *`. The browser then sends the
actual POST.

If you see preflight failures in browser dev tools, check
`cors_allow_origins` in the config and confirm the middleware is wired.

---

## Token introspection (v0.4.1)

**What this is:** RFC 7662 introspection lets a resource server ask the identity
provider "is this token still valid?" rather than validating it locally. This is
useful when you need server-side revocation or when the resource server lacks
public-key crypto libraries. The mock's introspect endpoint requires the caller
to authenticate with a valid service-principal credential — unauthenticated
introspect would let anyone test arbitrary tokens.

**Why you'd use it:** Test that your resource server or API gateway correctly
handles `{"active": false}` responses (expired tokens, wrong-issuer tokens,
malformed tokens) and that it requires re-authentication rather than caching
a "valid" result indefinitely.

### S55 — Active token introspection

```bash
# 1. Issue a token
TOKEN=$(curl -s -X POST http://mock-idp.example.com/default/token \
  -d "grant_type=client_credentials&client_id=service-a" \
  -d "client_secret=serviceA-secret&resource=api://serviceB" \
  | jq -r .access_token)

# 2. Introspect it (caller authenticates as service-a)
curl -X POST http://mock-idp.example.com/default/introspect \
  -d "token=$TOKEN" \
  -d "client_id=service-a&client_secret=serviceA-secret"
```

Response:

```json
{
  "active": true,
  "token_type": "Bearer",
  "sub": "01010101-1010-1010-1010-aaaaaaaaaaaa",
  "iss": "http://mock-idp.example.com/default",
  "aud": "api://serviceB",
  "exp": 1234567890,
  "roles": ["m2m"],
  "azp": "01010101-1010-1010-1010-aaaaaaaaaaaa"
}
```

Only claims in the pass-through allowlist (`sub`, `iss`, `aud`, `exp`, `iat`,
`nbf`, `roles`, `azp`, `tid`, etc.) are returned — arbitrary `extra_claims`
are not forwarded.

### S56 — Expired token returns `active: false`

```bash
EXPIRED=$(curl -s -X POST http://mock-idp.example.com/default/token \
  -H "X-Test-Expired: 1" \
  -d "grant_type=password&username=alice&password=alice-pw&resource=api://serviceB" \
  | jq -r .access_token)

curl -X POST http://mock-idp.example.com/default/introspect \
  -d "token=$EXPIRED" \
  -d "client_id=service-a&client_secret=serviceA-secret"
```

→ `{"active": false}`. The token has a valid signature but `exp` is in the past.

### S57 — Wrong-signature token returns `active: false`

```bash
BAD_SIG=$(curl -s -X POST http://mock-idp.example.com/default/token/wrong-sig \
  -d "grant_type=client_credentials&client_id=service-a" \
  -d "client_secret=serviceA-secret&resource=api://serviceB" \
  | jq -r .access_token)

curl -X POST http://mock-idp.example.com/default/introspect \
  -d "token=$BAD_SIG" \
  -d "client_id=service-a&client_secret=serviceA-secret"
```

→ `{"active": false}`. Signature validation fails; the token was signed by the
unpublished alt key.

### S58 — Unauthenticated introspect caller returns 401

```bash
curl -X POST http://mock-idp.example.com/default/introspect \
  -d "token=$TOKEN" \
  -d "client_id=service-a&client_secret=WRONG"
```

→ `401 invalid_client`. The introspect caller must authenticate; anonymous
introspect is not allowed.

**Troubleshooting:**

- `{"active": false}` for a token you just issued → confirm you're calling
  `/{same-issuer}/introspect`. A token from `/tenant-a` presented to
  `/tenant-b/introspect` always returns `{"active": false}` because the keys
  don't match.
- `401` on the introspect call → the `client_id` / `client_secret` in the form
  body is wrong or missing. This is the caller's credential, not the subject
  token's credential.
- Missing `token` parameter → `400 token parameter required`.

---

## OAuth 2.0 Token Exchange (v0.4.2)

**What this is:** RFC 8693 token exchange lets a service (the "intermediary")
present a token it received from a caller and get a new token scoped to a
downstream service. The new token preserves the original caller's identity
claims (`sub`, `upn`, etc.) while stamping the intermediary in the `act`
(actor) claim. This models API gateway or proxy scenarios where a service acts
on behalf of a user.

**Why you'd use it:** Test that your gateway correctly threads caller identity
through a multi-hop request chain, and that downstream services can distinguish
"this token represents alice" from "this token represents the gateway acting as
alice" via the `act` claim.

### S59 — Basic token exchange

```bash
# 1. Alice gets a token
ALICE_TOKEN=$(curl -s -X POST http://mock-idp.example.com/default/token \
  -d "grant_type=password&username=alice&password=alice-pw&resource=api://serviceB" \
  | jq -r .access_token)

# 2. Gateway exchanges alice's token for one scoped to serviceC
curl -X POST http://mock-idp.example.com/default/token \
  -d "grant_type=urn:ietf:params:oauth:grant-type:token-exchange" \
  -d "client_id=service-a&client_secret=serviceA-secret" \
  -d "subject_token=$ALICE_TOKEN" \
  -d "subject_token_type=urn:ietf:params:oauth:token-type:access_token" \
  -d "audience=api://serviceC"
```

Response:

```json
{
  "access_token": "<new-jwt>",
  "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

The new token's decoded claims:

```json
{
  "sub": "11111111-1111-1111-1111-aaaaaaaaaaaa",
  "preferred_username": "alice@example.com",
  "aud": "api://serviceC",
  "azp": "01010101-1010-1010-1010-aaaaaaaaaaaa",
  "act": {"sub": "01010101-1010-1010-1010-aaaaaaaaaaaa"},
  "iss": "http://mock-idp.example.com/default"
}
```

Alice's identity claims are preserved; `act.sub` identifies the intermediary.

### S60 — Subject identity preserved across exchange

Decode both the original token and the exchanged token. Confirm:
- `sub`, `oid`, `tid`, `preferred_username` are identical.
- `azp` in the new token is the intermediary's client_id (service-a's UUID).
- `act.sub` equals the intermediary's canonical client_id.

Use `POST /debug/decode` on both tokens to compare without writing a parser.

### S61 — Exchange with new audience

Use the `audience` parameter to direct the exchanged token at a different
resource than the original:

```bash
# Original token was for api://serviceB; exchange targets api://serviceC
-d "audience=api://serviceC"
```

The intermediary must have `api://serviceC` in its `allowed_audiences` (or the
issuer must be in lax mode) for the exchange to succeed.

### S62 — Expired subject token is rejected

```bash
EXPIRED=$(curl -s -X POST http://mock-idp.example.com/default/token \
  -H "X-Test-Expired: 1" \
  -d "grant_type=password&username=alice&password=alice-pw&resource=api://serviceB" \
  | jq -r .access_token)

curl -X POST http://mock-idp.example.com/default/token \
  -d "grant_type=urn:ietf:params:oauth:grant-type:token-exchange" \
  -d "client_id=service-a&client_secret=serviceA-secret" \
  -d "subject_token=$EXPIRED" \
  -d "audience=api://serviceC"
```

→ `400 subject_token is expired`. Exchange is rejected; the intermediary cannot
launder an expired token into a fresh one.

**Troubleshooting:**

- `400 subject_token signature invalid` → the subject token was issued by a
  different issuer. The exchange endpoint verifies the subject token against
  `/{issuer}/jwks`. Issue both tokens from the same issuer path.
- `400 subject_token is expired` → the subject token has passed its `exp`.
  Re-issue it before exchanging. Use `X-Test-Expires-In: 3600` if you need a
  longer-lived subject token for the test.
- `401 invalid_client` → the intermediary's `client_id` / `client_secret` is
  wrong or the intermediary is not configured in the identity store.
- Missing `act` claim in the exchanged token → check that the intermediary is
  a service principal (not a user) — `act` is populated from the intermediary's
  canonical client_id.

---

## Per-issuer signing key isolation (v0.5.0)

**What this is:** Each issuer path (`/default`, `/tenant-a`, `/tenant-b`, etc.)
has its own independent RSA keypair. A token minted at `/tenant-a/token` cannot
be verified against `/tenant-b/jwks`, even if both are on the same mock server.

**Why you'd use it:** In a multi-tenant API gateway setup, each tenant's route
is configured against a different issuer URL. This confirms that a misconfigured
gateway route — one pointing at the wrong tenant's JWKS — correctly rejects
tokens from another tenant. Without per-issuer keys, this test would always pass
trivially because all JWKS responses are identical.

### S63 — Different issuers publish different key sets

```bash
curl http://mock-idp.example.com/tenant-a/jwks | jq '[.keys[].kid]'
# ["mock-tenant-a-1", "mock-tenant-a-ec-1", "mock-tenant-a-d1", "mock-tenant-a-d2"]

curl http://mock-idp.example.com/tenant-b/jwks | jq '[.keys[].kid]'
# ["mock-tenant-b-1", "mock-tenant-b-ec-1", "mock-tenant-b-d1", "mock-tenant-b-d2"]
```

No kid overlap between the two sets.

### S64 — Token from one issuer fails validation at another issuer's JWKS

```bash
TOKEN_A=$(curl -s -X POST http://mock-idp.example.com/tenant-a/token \
  -d "grant_type=password&username=alice&password=alice-pw&resource=api://serviceB" \
  | jq -r .access_token)

# Introspect using tenant-b's endpoint — should return inactive
curl -X POST http://mock-idp.example.com/tenant-b/introspect \
  -d "token=$TOKEN_A" \
  -d "client_id=service-a&client_secret=serviceA-secret"
```

→ `{"active": false}`. The token's `kid` is `mock-tenant-a-1`; tenant-b's JWKS
has no such key.

Use this scenario to confirm the gateway rejects a token whose `kid` is not in
the configured issuer's JWKS.

### S65 — Rotating one issuer leaves the other unaffected

```bash
# Check kids before rotation
curl http://mock-idp.example.com/tenant-a/jwks | jq '.keys[0].kid'
# "mock-tenant-a-1"
curl http://mock-idp.example.com/tenant-b/jwks | jq '.keys[0].kid'
# "mock-tenant-b-1"

# Rotate tenant-a only
curl -X POST "http://mock-idp.example.com/admin/rotate-jwks?issuer=tenant-a" \
  -H "X-Admin-Token: change-me-in-real-deployments"
# {"status": "rotated", "new_signing_kid": "mock-tenant-a-2"}

# tenant-a's kid changed; tenant-b's did not
curl http://mock-idp.example.com/tenant-a/jwks | jq '.keys[0].kid'
# "mock-tenant-a-2"
curl http://mock-idp.example.com/tenant-b/jwks | jq '.keys[0].kid'
# "mock-tenant-b-1"   ← unchanged
```

Any tokens issued by tenant-a before the rotation are now invalid; tenant-b
tokens are unaffected.

### S66 — Check all signing kids via debug endpoint

```bash
curl http://mock-idp.example.com/debug/config | jq .signing_kids
```

Returns a dict of all currently-known issuers and their active signing kids:

```json
{
  "default": "mock-default-1",
  "tenant-a": "mock-tenant-a-2",
  "tenant-b": "mock-tenant-b-1"
}
```

Useful for confirming rotation state across a multi-issuer test run.

**Troubleshooting:**

- `signing_kids` is empty → no requests have been made to any `/{issuer}/...`
  endpoint yet. Issue at least one token or hit `/jwks` first; key stores are
  created lazily.
- Token valid at `/debug/decode` (`signature_validated_against_published_key: true`)
  but gateway returns 401 → the gateway is checking the wrong issuer's JWKS. The
  decode endpoint tries all known issuers' keys; the gateway only checks the
  configured issuer's. Confirm the gateway's `issuer_url` matches the `iss` claim
  in the token.
- After pod restart, previously-issued tokens always fail → expected. Each restart
  generates new key stores. Re-acquire tokens after restarting the mock.

---

## Webhook on token issuance (v0.5.2)

**What this is:** After every successful token issuance at `/{issuer}/token`,
the mock POSTs a JSON payload to each configured webhook URL. Negative endpoints
(`/token/wrong-sig`, `/token/unsigned`, `/token/wrong-alg`) do not trigger
webhooks — they are test fixtures, not real issuances.

**Why you'd use it:** Integration tests can assert "a token was issued for this
identity with these claims" without scraping server logs or decoding JWTs. Point
the webhook at a lightweight HTTP recorder in your test harness, then inspect
the captured payloads after the request completes.

### S72 — Configure a webhook receiver

```yaml
# config.yaml
webhooks:
  - url: http://localhost:9090/events
    events: [token_issued]
    timeout_seconds: 5
```

Any POST to `/{issuer}/token` (password, client_credentials, or token-exchange)
now delivers a payload to `http://localhost:9090/events`.

### S73 — Webhook payload

The body POSTed to your receiver:

```json
{
  "event": "token_issued",
  "timestamp": "2026-05-16T12:00:00.000000+00:00",
  "issuer": "default",
  "grant_type": "client_credentials",
  "claims": {
    "sub": "01010101-1010-1010-1010-aaaaaaaaaaaa",
    "aud": "api://serviceB",
    "appid": "01010101-1010-1010-1010-aaaaaaaaaaaa",
    "roles": ["m2m"],
    "iss": "http://mock-idp.example.com/default",
    "exp": 1234567890,
    "iat": 1234564290
  }
}
```

`claims` is the full decoded payload — no JWT parsing needed. Use it to assert
specific claim values directly in your test assertions.

### S74 — Webhook failure does not block token issuance

If the webhook endpoint is down, times out, or returns a non-2xx response, the
token is still issued and the `/token` response returns normally. The failure is
logged at WARNING level on the mock server:

```
WARNING mock_idp.webhooks: Webhook delivery failed (http://localhost:9090/events): ...
```

Your test must not depend on webhook delivery being synchronous with the
`/token` response — treat it as eventually delivered with a short timeout.

### S75 — Event filter

```yaml
webhooks:
  - url: http://localhost:9090/events
    events: [token_issued]   # only this event; future events won't fire here
```

If `events` does not include `token_issued`, the webhook never fires for token
issuances. This allows a single receiver URL to be selectively subscribed to
future event types without receiving everything.

**Troubleshooting:**

- Webhook never arrives → confirm `events` includes `token_issued` (exact
  string, case-sensitive). Check the mock server log for `Webhook delivery
  failed` warnings.
- Webhook payload missing expected claims → the `claims` dict is built before
  `X-Omit-Claims` is applied. If you omit claims via that header, the webhook
  still sees them. This is intentional — the webhook is for test assertion, not
  the consumer's view.
- Token issuance is slow → check `timeout_seconds`. Default is 5 s; if the
  receiver is unreachable and not actively refusing connections, delivery will
  block for up to `timeout_seconds` before logging a warning. Reduce to 1 s for
  faster test teardown.
- Multiple webhooks configured → all matching entries fire concurrently via
  `asyncio.gather`. Order of delivery is not guaranteed.

---

## Configurable signing algorithm per identity (v0.5.1)

**What this is:** Each identity (user or service principal) can specify
`signing_alg: RS256` (default) or `signing_alg: ES256`. The mock issues tokens
signed with the corresponding key — RSA-2048 for RS256, EC P-256 for ES256 — and
publishes both keys in its JWKS. This lets you confirm that your gateway's JWT
validator handles a multi-algorithm JWKS correctly.

**Why you'd use it:** Real identity providers sometimes serve a mix of RSA and EC
keys. A gateway configured to only accept RS256 will reject EC-signed tokens even
if the signature is valid. These scenarios let you catch that misconfiguration
before it hits production.

### S67 — ES256 token has `alg: ES256` and an EC `kid`

Config (`service-b` in `config.example.yaml`):

```yaml
service-b:
  signing_alg: ES256
```

```bash
TOKEN=$(curl -s -X POST http://mock-idp.example.com/default/token \
  -d "grant_type=client_credentials&client_id=service-b" \
  -d "client_secret=serviceB-secret&resource=api://serviceC" \
  | jq -r .access_token)

# Inspect the JWT header
echo $TOKEN | cut -d. -f1 | base64 -d 2>/dev/null | jq .
```

Header:

```json
{"alg": "ES256", "typ": "JWT", "kid": "mock-default-ec-1"}
```

The `kid` matches the EC key in `/jwks`; the gateway must select that key for
verification, not the RSA signing key.

### S68 — JWKS contains both RSA and EC keys

```bash
curl http://mock-idp.example.com/default/jwks | jq '[.keys[] | {kid, kty, crv}]'
```

Response:

```json
[
  {"kid": "mock-default-1",    "kty": "RSA"},
  {"kid": "mock-default-ec-1", "kty": "EC", "crv": "P-256"},
  {"kid": "mock-default-d1",   "kty": "RSA"},
  {"kid": "mock-default-d2",   "kty": "RSA"}
]
```

A gateway that only accepts RSA keys from JWKS (i.e., ignores EC entries) will
fail to verify service-b's ES256 tokens.

### S69 — RS256 identity unaffected by ES256 config

```bash
TOKEN=$(curl -s -X POST http://mock-idp.example.com/default/token \
  -d "grant_type=client_credentials&client_id=service-a" \
  -d "client_secret=serviceA-secret&resource=api://serviceB" \
  | jq -r .access_token)

echo $TOKEN | cut -d. -f1 | base64 -d 2>/dev/null | jq .alg
# "RS256"
```

`service-a` uses the default `RS256`; adding ES256 to `service-b` does not change
other identities.

### S70 — Discovery advertises both algorithms

```bash
curl http://mock-idp.example.com/default/.well-known/openid-configuration \
  | jq .id_token_signing_alg_values_supported
# ["RS256", "ES256"]
```

Gateways that validate the discovery document's `alg_values_supported` list before
accepting tokens must accept both values.

### S71 — ES256 token verifies successfully against published JWKS

Fetch the JWKS and verify the ES256 token:

```bash
TOKEN=$(... service-b token as in S67 ...)

curl -X POST http://mock-idp.example.com/debug/decode \
  -H "Content-Type: application/json" \
  -d "{\"token\": \"$TOKEN\"}"
```

Response includes `"signature_validated_against_published_key": true`.

If the debug endpoint returns `false`, the EC key is missing from the JWKS or the
token was issued before the server was last restarted (new keys on each start).

**Troubleshooting:**

- Gateway returns 401 for an ES256 token but RS256 tokens pass → the gateway's
  JWKS client is either filtering out EC keys or the gateway's allowed-algorithms
  list is restricted to RS256 only. Check both the JWKS-parsing code and the
  algorithm-allowlist configuration.
- `signing_alg` field rejected at config load → must be exactly `"RS256"` or
  `"ES256"` (case-sensitive). Any other value raises a Pydantic validation error and
  the server will refuse to start.
- ES256 token header shows kid `mock-default-ec-1` but gateway can't find it in
  JWKS → confirm the gateway is fetching from the correct issuer path. Each issuer
  has its own EC key; `/tenant-a/jwks` and `/tenant-b/jwks` have different kids.
- EC token fails `introspect` → the introspect endpoint uses the same JWKS for
  verification. If `{"active": false}` is returned for a valid ES256 token, confirm
  the request goes to the same issuer path that minted the token.

---

## Coverage summary

| Area | Scenarios |
|---|---|
| Token shape (v1/v2) | S1–S5 |
| Audience handling | S6–S9 |
| Authentication failures | S10–S14 |
| Lax / strict gating | S15–S19 |
| Admin override | S20–S24 |
| Token lifetime | S25–S28 |
| Extra claims | S29–S30 |
| Multi-issuer (iss isolation) | S31–S32 |
| Claim omission | S33–S35 |
| Signature failures | S36–S37 |
| JWKS rotation | S38–S39 |
| Debug endpoints | S40–S43 |
| Token playground | S44 |
| CORS preflight | S45 |
| Algorithm-failure endpoints | S46–S47 |
| Slow / failing endpoints | S48–S49 |
| Multi-key JWKS | S50 |
| Per-issuer auth_mode | S51–S52 |
| Admin iss override | S53–S54 |
| Token introspection (RFC 7662) | S55–S58 |
| Token Exchange (RFC 8693) | S59–S62 |
| Per-issuer signing key isolation | S63–S66 |
| Webhook on token issuance | S72–S75 |
| Configurable signing algorithm | S67–S71 |

That is the full v0.5.2 surface area. Anything not covered here is either
a v0.4 roadmap item (token introspection, token exchange, config hot-reload, etc.) or out of scope.
