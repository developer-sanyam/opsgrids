"""Signing keys and the JWKS document.

**This is the only module in the platform that touches a private key.** Every other
service — and every non-Python client — verifies against the public JWKS served here. That
asymmetry is the whole point: a compromised product service can read a token, and cannot
forge one.

Ed25519 rather than RSA: 64-byte signatures instead of 256, no parameter choices to get
wrong, and constant-time by construction.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from platform_core.errors import ProblemError


class SigningKeyUnavailable(ProblemError):
    """The service was asked to mint without a private key.

    Deliberately fatal rather than falling back to a symmetric secret: a service that
    quietly degrades to a weaker algorithm is worse than one that refuses to start.
    """

    status_code = 500
    problem_type = "signing-key-unavailable"
    title = "Signing key unavailable"


def b64url(data: bytes) -> str:
    """Base64url without padding, as JWK requires."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@dataclass(frozen=True, slots=True)
class KeyPair:
    kid: str
    private: Ed25519PrivateKey | None
    public: Ed25519PublicKey

    def public_jwk(self) -> dict[str, Any]:
        raw = self.public.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {
            "kty": "OKP",
            "crv": "Ed25519",
            "use": "sig",
            "alg": "EdDSA",
            "kid": self.kid,
            "x": b64url(raw),
        }


def _thumbprint(public: Ed25519PublicKey) -> str:
    """A `kid` derived from the key itself (RFC 7638).

    Deriving rather than configuring means the identifier cannot drift from the key it
    names — the failure where a rotated key keeps the old `kid` and every verifier
    silently uses the wrong one.
    """
    raw = public.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    canonical = f'{{"crv":"Ed25519","kty":"OKP","x":"{b64url(raw)}"}}'.encode()
    return b64url(hashlib.sha256(canonical).digest())[:16]


def load_private_key(path: str | Path) -> KeyPair:
    """Load the signing key from disk.

    In production this path is mounted from the secret store, never baked into an image.
    """
    pem = Path(path).read_bytes()
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SigningKeyUnavailable(
            "The configured signing key is not Ed25519. Generate one with "
            "`openssl genpkey -algorithm ed25519`."
        )
    public = key.public_key()
    return KeyPair(kid=_thumbprint(public), private=key, public=public)


def generate_keypair() -> KeyPair:
    """A fresh key, for tests and for `make devkeys`."""
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    return KeyPair(kid=_thumbprint(public), private=private, public=public)


def private_pem(pair: KeyPair) -> bytes:
    if pair.private is None:  # pragma: no cover - defensive
        raise SigningKeyUnavailable("No private half to export")
    return pair.private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@lru_cache(maxsize=1)
def _cached_active(path: str) -> KeyPair:
    return load_private_key(path)


def active_key(path: str | None) -> KeyPair:
    """The key this process signs with.

    Cached, because reading and parsing a PEM on every token issue is measurable at
    sign-in rates.
    """
    if not path:
        raise SigningKeyUnavailable(
            "auth-service has no JWT_PRIVATE_KEY_PATH. It is the only service that "
            "should have one — run `make devkeys` for local development."
        )
    return _cached_active(str(path))


def reset_key_cache() -> None:
    """Tests, and the rotation drill."""
    _cached_active.cache_clear()


def jwks(pairs: list[KeyPair]) -> dict[str, list[dict[str, Any]]]:
    """The public document every verifier fetches.

    Serving several keys is not optional: during rotation, tokens signed by the previous
    key are still in flight, and dropping it from this document invalidates them all at
    once.
    """
    return {"keys": [p.public_jwk() for p in pairs]}
