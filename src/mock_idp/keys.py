import threading

from joserfc.jwk import ECKey, RSAKey

_JWK = RSAKey | ECKey


def _new_rsa_key(kid: str) -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": kid})


def _new_ec_key(kid: str) -> ECKey:
    return ECKey.generate_key("P-256", parameters={"kid": kid})


def key_kid(key: _JWK) -> str:
    return key.kid


class _IssuerKeys:
    __slots__ = ("_issuer", "_seq", "signing", "alt", "decoys", "ec_signing", "ec_alt")

    def __init__(self, issuer: str) -> None:
        self._issuer = issuer
        self._seq = 1
        prefix = f"mock-{issuer}"
        self.signing = _new_rsa_key(f"{prefix}-1")
        self.alt = _new_rsa_key(f"{prefix}-alt")
        self.decoys = [_new_rsa_key(f"{prefix}-d1"), _new_rsa_key(f"{prefix}-d2")]
        self.ec_signing = _new_ec_key(f"{prefix}-ec-1")
        self.ec_alt = _new_ec_key(f"{prefix}-ec-alt")

    def jwks_keys(self) -> list[_JWK]:
        """RSA signing key, EC signing key, then RSA decoys — tests kid-based selection."""
        return [self.signing, self.ec_signing] + self.decoys

    def rotate(self) -> RSAKey:
        self._seq += 1
        self.signing = _new_rsa_key(f"mock-{self._issuer}-{self._seq}")
        return self.signing

    def signing_key_for_alg(self, alg: str) -> _JWK:
        return self.ec_signing if alg == "ES256" else self.signing

    def alt_key_for_alg(self, alg: str) -> _JWK:
        return self.ec_alt if alg == "ES256" else self.alt

    def signing_public_key_pem(self) -> bytes:
        return self.signing.as_pem(private=False)


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


def get_signing_key(issuer: str) -> RSAKey:
    return _get(issuer).signing


def get_alt_key(issuer: str) -> RSAKey:
    return _get(issuer).alt


def get_signing_key_for_alg(issuer: str, alg: str) -> _JWK:
    return _get(issuer).signing_key_for_alg(alg)


def get_alt_key_for_alg(issuer: str, alg: str) -> _JWK:
    return _get(issuer).alt_key_for_alg(alg)


def get_jwks_keys(issuer: str) -> list[_JWK]:
    return _get(issuer).jwks_keys()


def get_signing_public_key_pem(issuer: str) -> bytes:
    return _get(issuer).signing_public_key_pem()


def rotate(issuer: str | None = None) -> RSAKey | dict[str, str]:
    if issuer is not None:
        return _get(issuer).rotate()
    with _lock:
        snapshot = list(_stores.items())
    return {name: key_kid(store.rotate()) for name, store in snapshot}


def all_signing_kids() -> dict[str, str]:
    """Return {issuer: signing_kid} for all known issuers."""
    with _lock:
        return {name: key_kid(store.signing) for name, store in _stores.items()}


def all_jwks_keys() -> list[_JWK]:
    """All published keys across all known issuers — for debug/decode only."""
    with _lock:
        stores = list(_stores.values())
    keys: list[JsonWebKey] = []
    for store in stores:
        keys.extend(store.jwks_keys())
    return keys
