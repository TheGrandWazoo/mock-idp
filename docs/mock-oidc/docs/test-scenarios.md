# Test Scenarios — Python Mock OIDC

Concrete request/response patterns for the v0.3.7 surface area. Use these
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

### S38 — Force-rotate the signing key

```bash
curl -X POST http://mock-idp.example.com/admin/rotate-jwks \
  -H "X-Admin-Token: change-me-in-real-deployments"
```

Response:

```json
{"status": "rotated", "new_signing_kid": "<new-thumbprint>"}
```

Then submit a token issued before the rotation to a protected gateway
route. Gateway behavior depends on its JWKS cache TTL:

- If cache is still warm with the old key → 401
- After TTL elapses → gateway re-fetches JWKS, picks up the new key

Useful for testing JWKS cache invalidation behavior.

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

### S50 — JWKS returns three keys

```bash
curl http://mock-idp.example.com/default/jwks | jq '.keys | length'
```

Returns `3`. One active signing key (`kid: mock-py-1`) followed by two decoys
(`mock-py-d1`, `mock-py-d2`). A gateway that selects by `kid` will correctly use only
the active key; a gateway that blindly tries every key may accept tokens signed by any
of them.

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
| Multi-issuer | S31–S32 |
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

That is the full v0.3.7 surface area. Anything not covered here is either
a v0.4 roadmap item (token introspection, token exchange, config hot-reload, etc.) or out of scope.
