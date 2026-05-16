import threading

from cryptography.hazmat.primitives import serialization
from authlib.jose import JsonWebKey


def _new_key(kid: str) -> JsonWebKey:
    return JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": kid})


def key_kid(key: JsonWebKey) -> str:
    return key.as_dict(is_private=False)["kid"]


class _IssuerKeys:
    __slots__ = ("_issuer", "_seq", "signing", "alt", "decoys")

    def __init__(self, issuer: str) -> None:
        self._issuer = issuer
        self._seq = 1
        prefix = f"mock-{issuer}"
        self.signing = _new_key(f"{prefix}-1")
        self.alt = _new_key(f"{prefix}-alt")
        self.decoys = [_new_key(f"{prefix}-d1"), _new_key(f"{prefix}-d2")]

    def jwks_keys(self) -> list[JsonWebKey]:
        """Active signing key first, then decoys — tests the gateway's kid-based selection."""
        return [self.signing] + self.decoys

    def rotate(self) -> JsonWebKey:
        self._seq += 1
        self.signing = _new_key(f"mock-{self._issuer}-{self._seq}")
        return self.signing

    def signing_public_key_pem(self) -> bytes:
        return self.signing.as_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )


_lock = threading.Lock()
_stores: dict[str, _IssuerKeys] = {}


def _get(issuer: str) -> _IssuerKeys:
    try:
        return _stores[issuer]
    except KeyError:
        with _lock:
            if issuer not in _stores:
                _stores[issuer] = _IssuerKeys(issuer)
            return _stores[issuer]


def get_signing_key(issuer: str) -> JsonWebKey:
    return _get(issuer).signing


def get_alt_key(issuer: str) -> JsonWebKey:
    return _get(issuer).alt


def get_jwks_keys(issuer: str) -> list[JsonWebKey]:
    return _get(issuer).jwks_keys()


def get_signing_public_key_pem(issuer: str) -> bytes:
    return _get(issuer).signing_public_key_pem()


def rotate(issuer: str | None = None) -> JsonWebKey | dict[str, str]:
    if issuer is not None:
        return _get(issuer).rotate()
    with _lock:
        snapshot = list(_stores.items())
    return {name: key_kid(store.rotate()) for name, store in snapshot}


def all_signing_kids() -> dict[str, str]:
    """Return {issuer: signing_kid} for all known issuers."""
    with _lock:
        return {name: key_kid(store.signing) for name, store in _stores.items()}


def all_jwks_keys() -> list[JsonWebKey]:
    """All published keys across all known issuers — for debug/decode only."""
    with _lock:
        stores = list(_stores.values())
    keys: list[JsonWebKey] = []
    for store in stores:
        keys.extend(store.jwks_keys())
    return keys
