"""RS256 key management and JWKS publication.

Loads the signing key from settings (PEM from the environment) or generates an
ephemeral one for development. The key id is the RFC 7638 SHA-256 thumbprint of
the public key. Retired public keys can stay published for rotation. Relates to
``iam.tokens`` (signing) and the gateway (JWKS verification).
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

import base64
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings


@dataclass(frozen=True)
class SigningKey:
    kid: str
    private_pem: bytes
    public_jwk: dict[str, str]


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def public_jwk(public_key: rsa.RSAPublicKey) -> dict[str, str]:
    """Return the JWK (with ``kid``, ``use``, ``alg``) of an RSA public key."""
    numbers = public_key.public_numbers()
    core = {"e": _b64(numbers.e), "kty": "RSA", "n": _b64(numbers.n)}
    thumb = hashlib.sha256(json.dumps(core, separators=(",", ":"), sort_keys=True).encode())
    kid = base64.urlsafe_b64encode(thumb.digest()).rstrip(b"=").decode("ascii")
    return {**core, "kid": kid, "use": "sig", "alg": "RS256"}


@lru_cache(maxsize=1)
def _load(pem: str) -> SigningKey:
    key: rsa.RSAPrivateKey
    if pem:
        loaded = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
        if not isinstance(loaded, rsa.RSAPrivateKey):
            raise ValueError("IDENTITY_JWT_PRIVATE_KEY_PEM must be an RSA key")
        key = loaded
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    jwk = public_jwk(key.public_key())
    return SigningKey(kid=jwk["kid"], private_pem=private_pem, public_jwk=jwk)


def signing_key() -> SigningKey:
    """Return the active signing key."""
    return _load(settings.IDENTITY_JWT_PRIVATE_KEY_PEM)


def jwks_document() -> dict[str, list[dict[str, Any]]]:
    """Return the JWKS with the active key first, then retired public keys."""
    keys: list[dict[str, Any]] = [signing_key().public_jwk]
    for pem in settings.IDENTITY_JWT_PREVIOUS_PUBLIC_KEYS_PEM:
        loaded = serialization.load_pem_public_key(pem.encode("ascii"))
        if isinstance(loaded, rsa.RSAPublicKey):
            keys.append(public_jwk(loaded))
    return {"keys": keys}
