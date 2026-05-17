# ADR-005: Migrate from authlib.jose to joserfc

**Date:** 2026-05-17
**Status:** Accepted
**Deciders:** Platform team

---

## Context

The project used `authlib` solely for its `authlib.jose` sub-module (`JsonWebKey`
and `jwt`). `authlib` is a broad library covering OAuth 2.0 client/server flows,
OpenID Connect, and JOSE. Only the JOSE portion was used here.

On startup, every container instance printed:

```
/app/mock_idp/keys.py:4: AuthlibDeprecationWarning: authlib.jose module is
deprecated, please use joserfc instead.
It will be compatible before version 2.0.0.
  from authlib.jose import JsonWebKey
```

The authlib maintainer deprecated the `authlib.jose` module in favour of
`joserfc` — a library they created specifically to hold the JOSE/RFC
implementation. The warning appears on every pod start and pollutes operator
logs.

---

## Decision

Replace `authlib` with `joserfc` throughout the codebase. Remove `authlib`
from `pyproject.toml` entirely.

---

## API mapping

| Purpose | authlib | joserfc |
|---|---|---|
| Import | `from authlib.jose import JsonWebKey, jwt` | `from joserfc.jwk import RSAKey, ECKey, KeySet` / `from joserfc import jwt as _jwt` |
| Generate RSA key | `JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": k})` | `RSAKey.generate_key(2048, parameters={"kid": k})` |
| Generate EC key | `JsonWebKey.generate_key("EC", "P-256", is_private=True, options={"kid": k})` | `ECKey.generate_key("P-256", parameters={"kid": k})` |
| Get kid | `key.as_dict(is_private=False)["kid"]` | `key.kid` |
| Public JWK dict | `key.as_dict(is_private=False)` | `key.as_dict(private=False)` |
| PEM export | `key.as_key().public_bytes(Encoding.PEM, SubjectPublicKeyInfo)` | `key.as_pem(private=False)` |
| JWT encode | `jwt.encode(header, claims, key).decode("utf-8")` | `_jwt.encode(header, claims, key)` (returns `str`) |
| JWT decode | `jwt.decode(token, key)` → dict | `_jwt.decode(token, KeySet(keys)).claims` → dict |
| Detect alg | `key.as_dict()["kty"] == "EC"` | `isinstance(key, ECKey)` |

### Key simplification: `verify_token`

`verify_token()` previously sorted keys by matching kid and looped trying each.
`KeySet` handles kid-based selection internally, collapsing ~20 lines to 4:

```python
def verify_token(token_str: str, keys: list[RSAKey | ECKey]) -> dict | None:
    if not token_str:
        return None
    try:
        return _jwt.decode(token_str, KeySet(keys)).claims
    except Exception:
        return None
```

---

## Consequences

**Positive:**
- No deprecation warning in container logs
- `authlib` removed from the dependency tree — smaller image, fewer update surface
- `verify_token` is simpler and more correct (KeySet handles edge cases)
- `cryptography` serialization import removed from `keys.py` (joserfc exposes `as_pem()`)

**Neutral:**
- joserfc is maintained by the same author as authlib; the risk profile is comparable
- All 89 existing tests pass without modification

**Negative:**
- None identified
