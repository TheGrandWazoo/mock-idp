from cryptography.hazmat.primitives import serialization
from authlib.jose import JsonWebKey


def _new_key(kid: str) -> JsonWebKey:
    return JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": kid})


def key_kid(key: JsonWebKey) -> str:
    return key.as_dict(is_private=False)["kid"]


_signing_key = _new_key("mock-py-1")
_alt_key = _new_key("mock-py-alt")      # never published; used by wrong-sig endpoint
_decoy_keys = [_new_key("mock-py-d1"), _new_key("mock-py-d2")]  # published but never sign


def get_signing_key() -> JsonWebKey:
    return _signing_key


def get_alt_key() -> JsonWebKey:
    return _alt_key


def get_jwks_keys() -> list[JsonWebKey]:
    """Active signing key first, then decoys — tests the gateway's kid-based selection."""
    return [_signing_key] + _decoy_keys


def get_signing_public_key_pem() -> bytes:
    """PEM-encoded RSA public key — used as HMAC secret for wrong-alg tokens."""
    return _signing_key.as_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def rotate() -> JsonWebKey:
    global _signing_key
    _signing_key = _new_key("mock-py-rotated")
    return _signing_key
