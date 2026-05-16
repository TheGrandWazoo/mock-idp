# ADR-004: Per-Issuer Signing Keys

**Date:** 2026-05-16
**Status:** Accepted
**Deciders:** Platform team

---

## Context

Through v0.4.2 the application maintained a single global RSA-2048 signing
keypair shared by every issuer path. All calls to `/{issuer}/token` signed
with the same key and `/{issuer}/jwks` returned the same public key set
regardless of which issuer was requested.

Two test scenarios exposed the gap:

1. **JWKS isolation** — a gateway test needs to confirm that a token minted by
   `/{tenant-a}/token` is rejected by a route configured against
   `/{tenant-b}/.well-known/openid-configuration`. With shared keys the JWKS
   at both paths is identical, so the test can never distinguish "token from
   the wrong issuer" from "token from the right issuer".

2. **Key rotation scope** — rotating `POST /admin/rotate-jwks` invalidates
   every in-flight token across all issuers simultaneously. In a test suite
   that exercises multiple issuers, rotation of one issuer must not discard
   tokens belonging to another.

---

## Decision

### 1. Per-issuer key store (`_IssuerKeys`)

Each issuer path gets its own `_IssuerKeys` instance containing:

- **`signing`** — the RSA-2048 key that signs tokens and appears as the first
  entry in that issuer's JWKS.
- **`alt`** — an unpublished key used only by `/{issuer}/token/wrong-sig`.
- **`decoys`** — two additional published-but-never-signing keys, testing the
  gateway's kid-based key selection.

### 2. Lazy creation

`_IssuerKeys` instances are created on first use inside a `threading.Lock`
guarded dict (`_stores`). No issuer paths are pre-configured; a `GET
/tenant-x/jwks` request automatically creates `tenant-x`'s key store if it
does not exist. This keeps the startup path fast and avoids any coupling
between key management and the identity config.

**Consequence:** a freshly started server has no entries in `_stores`. The first
request to any `/{issuer}/...` endpoint triggers key generation for that issuer.
Pod restart means all key stores are regenerated — any in-flight tokens from
before the restart become invalid, which is the expected behavior for a
stateless test fixture.

### 3. Key naming scheme

Kids are namespaced by issuer: `mock-{issuer}-1` (signing), `mock-{issuer}-alt`
(unpublished), `mock-{issuer}-d1` / `mock-{issuer}-d2` (decoys). After rotation
the signing key is named `mock-{issuer}-{seq}` where `seq` increments each time.

This makes it trivial to read a JWKS response and identify which issuer a key
belongs to — useful when calling `/debug/config` or comparing JWKS across
multiple tenants.

### 4. Per-issuer `rotate()`

`rotate(issuer: str | None)`:

- If `issuer` is given: rotate only that issuer's signing key. Other issuers
  are unaffected.
- If `issuer` is `None` (default): snapshot the current `_stores` dict and
  rotate every known issuer. Useful for "nuke everything" test-teardown.

`POST /admin/rotate-jwks?issuer=default` — rotate one issuer.
`POST /admin/rotate-jwks` — rotate all known issuers.

### 5. Module layout

```
src/mock_idp/
  keys.py
    _IssuerKeys          class — per-issuer key bundle
    _stores              dict[str, _IssuerKeys] — lazy issuer registry
    _get(issuer)         factory / lookup
    get_signing_key(issuer)
    get_alt_key(issuer)
    get_jwks_keys(issuer)
    get_signing_public_key_pem(issuer)
    rotate(issuer=None)
    all_signing_kids()   → dict[str, str] — for /debug/config
    all_jwks_keys()      → list[JsonWebKey] — for /debug/decode
```

All existing callers in `routers/oidc.py` already had `issuer` available from
the route parameter, so the change was additive — no new routing logic was
needed.

---

## Alternatives considered

### A. Pre-create all issuer stores at startup from the config

At startup, enumerate all issuer slugs present in the YAML config and create a
key store for each.

**Rejected.** Issuer paths are not enumerated anywhere in the config today —
they are implicit from the path parameter. Pre-creating them would require
adding an explicit `issuers:` list to the config schema with no other benefit.
Lazy creation achieves the same isolation without schema changes.

### B. A single key ring with per-issuer labels (tags on `kid`)

Use one pool of keys but tag each key with the issuer it belongs to. JWKS for
each issuer filters the pool.

**Rejected.** The pool grows unbounded as issuers are exercised. Rotation
becomes ambiguous (which keys in the pool belong to this issuer?). Offers no
real advantage over separate key stores.

### C. Configurable key material (static kid / PEM in config)

Allow the config to supply a PEM-encoded private key per issuer so that keys
survive pod restarts and are deterministic.

**Deferred.** Useful for long-running shared environments where test clients
cache tokens. For a test fixture that is typically restarted per test run,
ephemeral keys are preferable (each run starts clean). Add when a concrete use
case for stable cross-restart keys appears.

---

## Consequences

**Good:**

- `/{tenant-a}/jwks` and `/{tenant-b}/jwks` return completely disjoint key
  sets. Cross-issuer token validation tests now have a meaningful failure mode.
- Rotating one issuer does not affect other issuers' in-flight tokens.
- No config schema changes required.
- Thread-safe: `_lock` guards only key store creation; reads and signing are
  lock-free.

**Trade-offs:**

- Key stores are created lazily, so a server that has never received a request
  for a given issuer has no entry in `_stores`. `POST /admin/rotate-jwks`
  (no `issuer=`) only rotates issuers that have been exercised. This is
  documented behavior.
- `/debug/config`'s `signing_kids` field is a dict instead of a scalar. Any
  tooling that previously read `signing_kid` (scalar) must be updated.
- Each issuer generates 4 RSA-2048 keypairs (signing + alt + 2 decoys) on first
  use. At ~5 ms per key generation on a modern CPU, this is imperceptible for
  test fixtures but is not suitable for high-concurrency production issuance.

---

## Related

- [`ADR-001`](../ADR-001-python-mock-oidc.md) — original build decision
- [`ADR-002`](../ADR-002-provider-plugin-architecture.md) — provider plugin architecture
- [`ADR-003`](../ADR-003-store-abstraction.md) — pluggable identity store
- [`roadmap.md`](../roadmap.md) — per-issuer signing keys moved to Resolved (v0.5.0)
- [`docs/architecture.md`](architecture.md) — key handling section updated
