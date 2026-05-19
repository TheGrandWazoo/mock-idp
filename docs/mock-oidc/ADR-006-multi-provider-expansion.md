# ADR-006: Multi-Provider Expansion — Okta, Cognito, Keycloak (v0.6.0)

**Date:** 2026-05-19
**Status:** Accepted
**Deciders:** Platform team
**Relates to:** ADR-002 (provider plugin architecture)

---

## Context

ADR-002 (v0.3) established the provider plugin architecture and created the
`providers/` module with `entra_id.py` as the first implementation. The
architecture was designed to make adding new providers additive. This ADR
documents the v0.6.0 decisions made when implementing Okta, Cognito, and
Keycloak providers.

Three concrete decisions arose during implementation:

1. **How to carry the provider name from config load to token issuance** — the
   provider name lives in `TenantRecord.provider`, but `UserRecord` and
   `ServicePrincipalRecord` are the objects passed to the token endpoint. The
   question: how does the token endpoint know which provider to dispatch to?

2. **Entra ID's `resolve_user_aud()` is audience-mapping logic that other
   providers don't need** — Entra ID maps bare audience URIs to UUID app IDs
   (`api://serviceB` → `app_id`). Other providers use the audience URI directly.
   Where should this mapping live?

3. **Provider-specific claim shape contracts** — what must each provider module
   implement to be usable in the registry?

---

## Decision

### 1. Provider name carried via `_provider` PrivateAttr

Each `UserRecord` and `ServicePrincipalRecord` gains a `_provider: str =
PrivateAttr(default="entra_id")`. When the config loader processes a tenant, it
injects the tenant's `provider` value into every identity under that tenant:

```python
# yaml_store.py / pg_store.py _apply()
for user in tenant.users.values():
    user._provider = tenant.provider
for sp in tenant.service_principals.values():
    sp._provider = tenant.provider
```

The token endpoint dispatches via:
```python
provider = get_provider(user._provider)
claims = provider.user_claims(...)
```

**Alternatives considered and rejected:**

- **Add `provider` to the store protocol** — would require `get_provider_for_user()`
  on both YamlIdentityStore and PostgresIdentityStore. Adds protocol surface for
  what is purely a config-read-time concern.

- **Pass provider name through function signatures** — `token()` → `get_provider(issuer)`
  with a per-issuer provider lookup. Breaks the existing per-tenant model where a
  single issuer can host multiple tenants with different providers.

- **Tenant lookup at token issuance time** — resolve the user → look up the tenant →
  read provider. Adds a second config lookup per request and couples the token
  endpoint to the tenant registry.

The PrivateAttr injection is the least-surface approach: the information is stamped
onto the identity object once at load time and requires no new protocol methods or
runtime lookups.

### 2. `resolve_user_aud()` moved inside `entra_id.user_claims()`

`resolve_user_aud()` maps an audience URI to the Entra ID app UUID in the `aud`
claim. This is Entra-specific behavior — other providers use the audience value
as-is. The function was moved from `tokens.py` into `entra_id.user_claims()`:

```python
# entra_id.py
from ..tokens import resolve_user_aud

def user_claims(issuer, user, aud, ...):
    c = _common(issuer, resolve_user_aud(aud), expires_in)
    ...
```

All other providers receive the raw `aud` string and decide for themselves whether
to transform it.

**Consequence:** `oidc.py` no longer imports `resolve_user_aud`. The token endpoint
passes `aud` (the raw audience resolved from the form) directly to `provider.user_claims()`.
Each provider is responsible for its own audience representation.

### 3. Provider module interface (informal Protocol)

Each provider module under `providers/` must export two functions:

```python
def user_claims(issuer: str, user: UserRecord, aud: str, shape: str,
                expires_in: int, roles: list[str],
                client_id: str | None) -> dict: ...

def sp_claims(issuer: str, sp_id: str, sp: ServicePrincipalRecord, aud: str,
              shape: str, expires_in: int, roles: list[str]) -> dict: ...
```

No formal `Protocol` or ABC is enforced. The registry in `providers/__init__.py`
loads the module lazily by relative import path and expects these two names to be
present. A missing function raises `AttributeError` at the first token issuance,
which is an acceptable failure mode for a developer-time config error.

**Why no formal Protocol:** adding `from typing import Protocol` and an abstract
base adds boilerplate for two functions. The informal contract is documented here
and in `providers/__init__.py`. If a third contributor joins and adds a malformed
provider, the `AttributeError` is immediate and obvious. Revisit if the provider
count grows beyond ~6.

---

## Claim shapes shipped

| Provider | `sub` | Audience | Roles | Notable claims |
|---|---|---|---|---|
| `entra_id` | OID UUID | UUID app ID | `roles` array | `tid`, `oid`, `upn`, `azp/appid`, `ver` |
| `okta` | email | raw URI | `groups` array | `uid`, `cid`, `scp` (space-string), no `tid`/`ver` |
| `cognito` | `<pool>/<sub>` | raw URI | `cognito:groups` array | `token_use`, `cognito:username`, `client_id`, `scope` |
| `keycloak` | username | raw URI | `realm_access.roles` + `resource_access.<azp>.roles` | `preferred_username`, `email`, `azp`, no `tid`/`ver` |

---

## Consequences

**Good:**

- New providers remain additive: one file + one registry entry, no core changes.
- Entra-specific aud UUID mapping is isolated; other providers are not affected by
  the Entra ID app registry model.
- 111 tests pass; 7 new provider-specific test cases (S90–S97).

**Trade-offs:**

- `_provider` PrivateAttr on identity objects is invisible to callers unfamiliar
  with Pydantic PrivateAttr semantics — it won't appear in `.model_dump()` or JSON
  serialization. This is intentional (it's a runtime hint, not config data) but
  can surprise readers.
- The informal Protocol means a badly-written provider module fails at request time,
  not at import time. Acceptable for a test tool; revisit for production providers.

---

## Open questions

| Question | Owner | Status |
|---|---|---|
| Should `resolve_user_aud` be renamed to clarify it is Entra-specific? | Platform team | Open |
| Should providers declare a formal `typing.Protocol` as the registry grows? | Architecture | Revisit at ~6 providers |
| Keycloak's `resource_access` key uses `azp` as the client key — is this the right default? | Platform team | Open |

---

## Related

- [`ADR-002`](ADR-002-provider-plugin-architecture.md) — established the provider plugin architecture
- [`ADR-003`](ADR-003-store-abstraction.md) — store abstraction that PrivateAttr injection extends
- [`roadmap.md`](roadmap.md) — v0.6.0 multi-provider resolved entry
- [`docs/test-scenarios.md`](docs/test-scenarios.md) — S90–S97 provider test scenarios
