# ADR-002: Provider Plugin Architecture, Feature Gates, and Entra ID Rich Model

**Date:** 2026-05-15
**Status:** Accepted
**Deciders:** Platform team

---

## Context

v0.2 shipped a tenant-keyed config schema (ADR-001 addendum) that hoisted `tid` out of
individual identity records into the grouping key. That change made multi-tenant configs
clean, but exposed a larger design question: the mock is currently Entra ID–shaped by
assumption — claim names (`tid`, `oid`, `upn`, `azp`), grant types, and token versions
are all baked into the core. If a team testing against Okta or Auth0 wanted to use this
tool, they'd be fighting the claim shape on every request.

Three pressures drove this ADR:

1. **Modularity** — the token-building logic should be extracted from core so a new
   provider profile is an additive change, not a surgery.
2. **Feature complexity** — the richer Entra ID authorization model (per-resource role
   grants, realm roles, service principals as distinct identity types) is useful for
   realistic testing but adds significant config surface. Teams with simpler needs
   shouldn't be forced to adopt it.
3. **Entra ID fidelity** — the current model collapses Entra ID's per-resource role
   assignment into flat lists on each identity. That means roles are the same regardless
   of which resource is being accessed, which is not how Entra ID works and produces
   inaccurate test results.

---

## Decision

### 1. Provider plugin architecture

Extract provider-specific token building into a `providers/` module. Each provider is a
Python module that implements a known interface. The `TenantRecord` gains a `provider`
field (default: `entra_id`). The token endpoint dispatches to the correct provider at
request time.

```
src/mock_idp/
  providers/
    __init__.py       registry + dispatch
    base.py           Protocol / abstract base
    entra_id.py       Entra ID token claim building (v0.3)
```

Adding a new provider (e.g. `okta.py`) is:
1. Write the module implementing the base protocol.
2. Register it in `providers/__init__.py`.
3. Set `provider: okta` on a tenant in config.

**Scope boundary:** providers handle *token claim shape only* — claim names, claim
values, discovery document format. They do not change grant flow logic, key management,
CORS, or admin endpoints. The goal is claim-shape fidelity for testing downstream
consumers, not full provider emulation (see §Alternatives).

### 2. Feature gates

Advanced authorization features are opt-in via the tenant config. A team that only needs
flat roles and simple audience gating can ignore all of it. A team that needs realistic
Entra ID role assignment adopts the richer model by writing the richer config.

Feature adoption is **implicit** — the presence of a `clients:` block with `grants:`
activates the grants model for that tenant. No explicit `features:` flag required. The
config schema expresses what you want; the loader infers the active feature set.

This keeps the simple case simple and the complex case possible.

### 3. Entra ID rich authorization model

The current flat model:

```
identity (user or SP) → roles: [list]   # same roles for every audience
```

The Entra ID model:

```
tenant
  users            human identities (password grant)
  service_principals  machine identities (client_credentials grant)
  clients          resource apps — define what roles exist and who has them
    grants: {identity: [roles]}  # per-resource assignment
```

**Config shape:**

```yaml
tenants:
  22222222-2222-2222-2222-222222222222:
    provider: entra_id

    users:
      alice:
        password: alice-pw
        upn: alice@example.com
        preferred_username: alice@example.com
        oid: 11111111-1111-1111-1111-aaaaaaaaaaaa
        token_version: v2
        token_lifetime_seconds: 300

    service_principals:
      service-a:
        client_id: 01010101-1010-1010-1010-aaaaaaaaaaaa
        secret: serviceA-secret
        label: ServiceA
        token_version: v1
        token_lifetime_seconds: 3600

    clients:
      api://serviceB:
        app_id: 01010101-1010-1010-1010-bbbbbbbbbbbb
        label: Service B API
        roles: [operator, responder, m2m]
        grants:
          alice: [operator, responder]
          service-a: [m2m]

      api://serviceC:
        app_id: 02020202-2020-2020-2020-cccccccccccc
        label: Service C API
        roles: [reader, admin]
        grants:
          alice: [reader]
```

**Token resolution with grants:**

When a token is requested for `api://serviceB`:
1. Resolve the requesting identity (user or SP).
2. Look up the client entry whose key matches the requested audience.
3. Read the `grants` entry for the requesting identity's name.
4. Emit those roles in the `roles` claim.
5. If no grants entry exists: emit empty `roles` (lax mode) or reject (strict mode).

**Fallback for tenants without `clients:` grants:** resolve roles from the flat `roles`
list on the identity, as in v0.2. Backward compatible.

**Groups:** remain on the identity (user or SP). They are tenant-scoped, not
resource-scoped, matching Entra ID behavior where group membership is a directory
property not a per-app assignment.

**Realm roles (optional, Keycloak-influenced):** a tenant-level `realm_roles` block
may assign directory-scoped roles (e.g. `Global.Reader`) to identities. These are
merged into the token alongside resource-scoped grants. Deferred to v0.4 pending a
concrete test demand.

### 4. `service_principals` as a distinct identity type

Currently clients (SPAs) and resource apps share the `clients:` block. The rich model
separates them:

- `service_principals:` — machine identities that *request* tokens
  (`client_credentials` grant). Equivalent to Entra ID service principals.
- `clients:` — resource apps that *receive* tokens. Define what roles exist and who
  has grants.

An identity that is both (calls other APIs and is called) gets an entry in both blocks.

---

## Alternatives considered

### A. Full provider flow emulation

Build complete Okta, Auth0, and Entra ID auth flows — authorization code, device flow,
PKCE, error response formats, etc.

**Rejected as current scope.** The primary value of this mock is testing downstream
JWT consumers, not auth flows. Full emulation requires ongoing maintenance as providers
change their behavior. `navikt/mock-oauth2-server` already does multi-provider flow
emulation and is the right tool for that problem. The provider plugin architecture
leaves the door open if a concrete flow-level need emerges.

### B. Explicit feature flags (`features: {grants: true}`)

A top-level `features:` block enabling each optional behavior by name.

**Rejected in favor of implicit detection.** Explicit flags create two ways to be wrong
(flag on but config absent; config present but flag off). Implicit activation from
config structure removes the inconsistency.

### C. Keep flat roles, add `audience_roles` override map

Add `audience_roles: {api://serviceB: [operator]}` to the identity record. Simpler
schema than full grants model; no `clients:` block required.

**Considered seriously.** Covers the per-audience role variation use case at lower
complexity. Rejected because it doesn't model who *defines* the roles — there's no
resource app concept, so you can't validate that a role being granted actually exists
on the target app. The grants model catches that misconfiguration at config load time.
Teams that want the lighter approach can get equivalent behavior through the fallback
flat model.

### D. One ADR per feature

Write ADR-002 for provider architecture and ADR-003 for the grants model separately.

**Rejected for now.** The two decisions are coupled — the grants model is the motivating
case for the provider abstraction. A single ADR captures that relationship. If providers
diverge significantly in their authorization models, split into separate ADRs at that
point.

---

## Consequences

**Good:**

- Adding a new provider profile doesn't touch core routing, key management, or config
  loading — it's an additive file plus a one-line registry entry.
- Teams testing Entra ID–protected APIs get a mock that produces tokens that actually
  reflect how Entra ID assigns roles. Tests that were passing because roles were always
  present will correctly fail if the grant isn't configured.
- Teams with simple needs see no additional complexity — the flat model still works.
- The `service_principals` / `clients` split makes the config self-documenting: reading
  the YAML tells you who calls and who is called.

**Trade-offs:**

- Config for the rich model is meaningfully more verbose. An identity store that
  previously fit in 20 lines may need 50.
- The grants model adds a second lookup at token issuance time (resolve identity →
  resolve client → resolve grant). Negligible for a test fixture, but worth noting.
- Implicit feature detection means the loader needs branching logic for which model
  is active. Must be clearly documented and tested.
- `service_principals` as a distinct key is a breaking change for anyone who had
  machine identities in `clients:` under the v0.2 schema. Migration: rename the block.

**Acceptable because:**

- Verbosity is a config file; it doesn't make runtime behavior harder to reason about.
- The double lookup is microseconds in a test fixture.
- Implicit detection is constrained to the config loader, which is already the most
  complex module.
- Breaking changes before v1.0 and before significant adoption are low-cost.

---

## Open questions

| Question | Owner | Status |
|---|---|---|
| Should `allowed_audiences` on an identity be removed entirely (grants replace it) or kept as a fast-path filter? | Platform team | Lean toward keeping as optional early gate in strict mode |
| Should a client grant entry support a wildcard (`*: [reader]`) for "any authenticated identity gets this role"? | Platform team | Open |
| Realm roles scope and shape — defer until a concrete use case exists? | Architecture | Defer to v0.4 |
| Should the provider protocol be a Python `Protocol` (structural) or `ABC` (nominal)? | Platform team | Lean toward `Protocol` for lighter coupling |

---

## Related

- [`ADR-001`](ADR-001-python-mock-oidc.md) — original build decision; v0.2 surface
- [`roadmap.md`](roadmap.md) — per-resource grants moved from v0.3 candidate to v0.3 committed
- [`docs/architecture.md`](docs/architecture.md) — endpoint shape and flows
