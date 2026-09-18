"""A whole identity provider, in-process, with a key generated per run.

**No network, and no committed key material.** The suite generates an RSA keypair when it
starts and publishes it as a JWKS the same way Google or Entra publishes theirs, so the
signature check under test is the real one — PyJWT, the real algorithm, a real key it has
never seen before — rather than a mock that returns True.

Every knob the tests need to turn is a parameter of `issue_id_token`, so a malformed token is
built by asking this provider for one rather than by hand-assembling a JWT and hoping it is
wrong in the intended way. `wrong_key` in particular signs with a *second* real key: a forged
token from a plausible issuer is the attack, and a corrupted string is not.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.oidc import TokenResponse

ISSUER = "https://idp.example.test"
CLIENT_ID = "opsgrids-test-client"
#: Long enough that the HS256 forgery test does not trip PyJWT's short-key warning — the
#: point of that test is the algorithm allow-list, not the key length.
CLIENT_SECRET = "idp-test-client-secret-padded-to-thirty-two-bytes"  # pragma: allowlist secret
AUTHORIZE = f"{ISSUER}/authorize"
TOKEN = f"{ISSUER}/oauth2/token"
JWKS_URI = f"{ISSUER}/.well-known/jwks.json"
DISCOVERY = f"{ISSUER}/.well-known/openid-configuration"


def _keypair() -> tuple[rsa.RSAPrivateKey, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, uuid.uuid4().hex[:12]


@dataclass
class FakeIdp:
    """An OIDC provider that signs real tokens and remembers what it was asked.

    `exchanges` is what the PKCE test reads: the verifier the service sent is recorded here,
    and the test hashes it and compares against the challenge in the authorization URL. That
    is the binding PKCE actually provides, checked end to end rather than asserted about a
    helper function.
    """

    #: Set by a test to make the next exchange fail the way a spent or expired code does.
    reject_code: bool = False
    exchanges: list[dict[str, Any]] = field(default_factory=list)
    discoveries: list[str] = field(default_factory=list)
    #: The ID token the next exchange hands back. Tests set this to whatever they want
    #: validated — a good one, or one broken in exactly one way.
    next_id_token: str | None = None

    def __post_init__(self) -> None:
        self._key, self.kid = _keypair()
        self._other_key, self._other_kid = _keypair()

    # ----------------------------------------------------------------- publishing

    def jwks(self) -> dict[str, Any]:
        jwk = json.loads(RSAAlgorithm.to_jwk(self._key.public_key()))
        jwk |= {"kid": self.kid, "use": "sig", "alg": "RS256"}
        return {"keys": [jwk]}

    def discovery_document(self, issuer: str = ISSUER) -> dict[str, Any]:
        return {
            "issuer": issuer,
            "authorization_endpoint": AUTHORIZE,
            "token_endpoint": TOKEN,
            "jwks_uri": JWKS_URI,
        }

    # ----------------------------------------------------------------- minting

    def issue_id_token(
        self,
        *,
        subject: str = "idp-subject-1",
        email: str = "sam@example.com",
        nonce: str,
        issuer: str = ISSUER,
        audience: str = CLIENT_ID,
        email_verified: bool = True,
        expires_in: int = 300,
        issued_at: int | None = None,
        wrong_key: bool = False,
        symmetric: bool = False,
        omit_email_verified: bool = False,
    ) -> str:
        """One ID token, broken in whichever single way the caller asks for.

        `symmetric` signs HS256 with the client secret — the classic algorithm-confusion
        forgery, available to anyone who has ever seen the secret. It must be refused by the
        algorithm allow-list rather than by luck.
        """
        now = issued_at if issued_at is not None else int(time.time())
        claims: dict[str, Any] = {
            "iss": issuer,
            "sub": subject,
            "aud": audience,
            "iat": now,
            "exp": now + expires_in,
            "nonce": nonce,
            "email": email,
        }
        if not omit_email_verified:
            claims["email_verified"] = email_verified

        if symmetric:
            return jwt.encode(claims, CLIENT_SECRET, algorithm="HS256", headers={"kid": self.kid})
        key = self._other_key if wrong_key else self._key
        kid = self._other_kid if wrong_key else self.kid
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})

    # ----------------------------------------------------------------- IdpClient

    async def discover(self, discovery_url: str) -> dict[str, Any]:
        self.discoveries.append(discovery_url)
        return self.discovery_document()

    async def fetch_jwks(self, jwks_uri: str) -> dict[str, Any]:
        return self.jwks()

    async def exchange_code(
        self,
        token_endpoint: str,
        *,
        code: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
        code_verifier: str,
    ) -> TokenResponse:
        from app.oidc import SsoExchangeProblem

        self.exchanges.append(
            {
                "token_endpoint": token_endpoint,
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": code_verifier,
            }
        )
        if self.reject_code:
            # What a provider answers for a code that is expired, already spent, or whose
            # PKCE verifier does not hash to the challenge it was given.
            raise SsoExchangeProblem("That sign-in could not be completed. Please start again.")
        if self.next_id_token is None:  # pragma: no cover - a test forgot to set one
            raise AssertionError("FakeIdp.next_id_token was not set")
        return TokenResponse(id_token=self.next_id_token)
