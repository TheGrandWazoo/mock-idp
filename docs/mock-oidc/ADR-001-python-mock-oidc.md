# ADR-001: Build a Python Mock OIDC Server for API Gateway Testing

**Date:** 2026-05-13
**Status:** Accepted
**Deciders:** Platform team

---

## Context

An API gateway's OIDC validation plugin is the authentication front door
for every externally-originated request. Testing that plugin thoroughly
requires a token source that can emit tokens with precise, controllable
claim shapes across:

1. v1 tokens (`appid`, `upn` / `unique_name`, `ver: "1.0"`)
2. v2 tokens (`azp`, `preferred_username`, `ver: "2.0"`)
3. Same logical client producing either shape on demand
4. Array claims (`roles`, `groups`)
5. Selective optional claims (`oid`, `tid` present or absent)
6. Expired tokens
7. Multiple issuer URLs on one server
8. Negative cases — malformed JWT, wrong-signature token

Beyond these eight token-shape scenarios, two additional dimensions
emerged during design:

9. **Two grant types** — `password` (user identity) and
   `client_credentials` (service identity) with destination conveyed via
   `resource` / `scope`.
10. **Audience gating** — per-identity allowlist with a lax/strict global
    mode, so tests can exercise both "anything goes" and "the identity
    provider would reject this" paths.

Existing third-party mock OIDC servers (see §Alternatives) handle a
subset of these scenarios natively. The rest require implementation-specific
callback code and a container rebuild per variation — slow, and opaque to
engineers not familiar with the callback framework.

Several alternatives exist (see §Alternatives below). The question is
whether to use a third-party tool, combine multiple tools, or build a
purpose-built Python server.

---

## Decision

**Build a custom Python mock OIDC server using FastAPI + authlib**,
deployed alongside any existing OIDC test infrastructure.

The v0.2 surface:

- **OIDC core:** discovery, JWKS, token, and userinfo endpoints per
  RFC 6749, RFC 7517, and the OIDC Core spec.
- **Two grants:** `password` and `client_credentials`. Optional
  `client_id` on the password grant populates `appid` / `azp` if provided.
- **Destination via `resource` or `scope`:** mapped to the `aud` claim;
  `/.default` suffix on `scope` stripped; default `api://default` if
  neither given.
- **Per-identity config:** `token_version` (v1/v2), `token_lifetime_seconds`,
  `allowed_audiences`, `extra_claims`. Mnemonic aliases with a separate
  `client_id` field.
- **Lax / strict audience gating:** global `auth_mode`; strict mode
  rejects unlisted audiences with `400 invalid_target`. Admin
  (`override_any_claim: true`) bypasses.
- **Admin override:** form-body fields replace token claims for clients
  with the flag set; CSV → list for known list claims; numeric coercion
  for `exp`/`iat`/`nbf`; reserved OAuth2 fields are not overridable.
- **Test override headers:** `X-Token-Shape`, `X-Omit-Claims`,
  `X-Test-Expired`, `X-Test-Expires-In`.
- **Negative-case fixtures:** `/token/wrong-sig` (signs with unpublished
  key), `/token/malformed` (returns garbage JWT).
- **Developer ergonomics:** token playground HTML at `GET /`;
  `POST /debug/decode`; `GET /debug/identities` (redacted);
  `GET /debug/config`.
- **Admin operations:** `POST /admin/rotate-jwks` gated by
  `X-Admin-Token`; rotates the signing key live for JWKS-cache-invalidation
  testing.
- **CORS:** middleware enabled by default; configurable origin allowlist.

The implementation is a modular Python package (`src/mock_idp/`), a
Dockerfile, a Helm chart, and an HTML playground page. No persistence,
no external dependencies at runtime.

Several features came up during design that were deliberately deferred to
v0.3 or later — Token Exchange (RFC 8693) for gateway-as-intermediary
patterns, introspection (RFC 7662) for upstream services that prefer
that style, per-issuer signing keys, configurable signing algorithms,
slow/failing endpoints for timeout testing, and several others. See
[`roadmap.md`](roadmap.md).

---

## Alternatives considered

### A. Third-party static mock OIDC server

Several open-source projects (e.g., `navikt/mock-oauth2-server`) provide
static multi-issuer fixtures and cover a useful subset of OIDC scenarios
natively. They require no Python maintenance.

**Rejected as the sole solution:** dynamic per-request claim shaping
requires implementation-specific callback code (typically in a different
language) and a container rebuild per variation. Per-request shape
toggling is the most common new requirement; making it expensive
guarantees test coverage stays narrow. Static fixtures remain useful
alongside the Python mock for scenarios where they're a natural fit.

### B. Mock server with JS callback-driven token shaping

Projects such as `axa-group/oauth2-mock-server` expose a
`beforeTokenSigning` JS callback that can reshape tokens per request.
This covers the 8 normalization scenarios without requiring a container
rebuild.

**Considered seriously.** Covers the core scenarios with no Python
maintenance burden. Downsides:

- JS-callback-driven shape definitions still live in container config,
  not in test code — slower iteration than a Python implementation where
  a new scenario is a route handler edit.
- Neither upstream tool provides a token playground, debug-decode
  endpoint, admin key rotation, or per-identity audience allowlisting
  out of the box.
- Two tools to track, two surfaces to onboard, two failure modes if used
  alongside a static server.

Kept as the fallback if the build option doesn't land within ~1 week.

### C. Wrap panva/node-oidc-provider in an Express container

Full OIDC-certified spec compliance, JS callback model for claim shaping.

**Rejected for now:** spec completeness exceeds what is needed today.
PAR / DPoP / advanced flows are not in scope. If those become
requirements later, revisit.

### D. Ory Hydra

Production-grade OAuth2 server.

**Rejected:** designed to be a real identity provider, not a flexible
mock. Postgres dependency, multiple services to run, slow test iteration
via DB-backed config.

### E. dexidp/dex

OIDC federation IdP with static user config.

**Rejected:** wrong tool. Dex federates to upstream identity sources; it
doesn't emit arbitrary token shapes on demand. Custom claim names beyond
the spec-defined set aren't supported.

---

## Consequences

**Good:**

- All 10 scenarios from §Context map to native language constructs (a
  Python `if`, a dict literal, a path parameter). No rebuild for shape
  changes — `uvicorn --reload` reflects edits in ~1 second.
- Tiny footprint: ~80 MB image, ~50 MB resident, no persistence.
- Anyone Python-literate can add a new scenario by writing a new route
  handler. Skill barrier near zero.
- The implementation is small enough to read end-to-end in one sitting.
- Developer ergonomics (token playground, debug-decode, admin
  rotate-jwks) materially improve the test-iteration loop.

**Trade-offs:**

- Net-new code that the maintainer keeps forever. Even a small OAuth2
  implementation has surface area for subtle bugs (clock skew handling,
  JWKS rotation, error response shapes, JWT header `kid` matching).
  Upstream tools have battle-tested these edges; you will rediscover them.
- If a static third-party mock is also in use, two ingresses, two
  deployments, two sets of monitoring.
- Spec drift risk: if the identity provider changes claim semantics or
  the gateway's OIDC plugin tightens validation, you patch the relevant
  route handler.
- Onboarding cost: every new engineer reads the implementation to
  understand it.

**Acceptable because:**

- The surface area is genuinely small. This is a test fixture that emits
  tokens, not a production OAuth2 server.
- Maintenance debt is on the cheap end of the curve in a Python-fluent
  team.
- This tool is not on the production critical path. Operational failures
  degrade test reliability, not customer traffic.

---

## Open questions

| Question | Owner | Status |
|---|---|---|
| Is the signing key pre-provisioned via a Kubernetes Secret instead of generated on startup? More realistic to production OIDC, but loses the "key rotation tests pod restart" property. | Platform team | Lean toward startup-generated for simplicity |
| What is the sunset criterion — is this a permanent fixture, or does it retire when the production identity provider stabilizes? | Architecture | Defer until production token shape is confirmed |
| Should per-issuer signing keys (a v0.3 roadmap item) be promoted — each issuer path gets its own keypair? | Platform team | Open |

Resolved design questions (formerly open, now baked in) are tracked in
[`roadmap.md`](roadmap.md) §Resolved.

---

## Related

- [`roadmap.md`](roadmap.md) — v0.3 and beyond; resolved questions log
- [`docs/architecture.md`](docs/architecture.md) — stack, endpoint shape, flows
- [`docs/implementation-guide.md`](docs/implementation-guide.md) — project layout, manifests, local dev
- [`docs/test-scenarios.md`](docs/test-scenarios.md) — 45 scenarios mapped to concrete invocations
