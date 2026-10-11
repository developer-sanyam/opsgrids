"""A token naming a key the platform never published is a forgery, not a server fault.

`PyJWKClientError` is not an `InvalidTokenError`, so before this was caught an anonymous caller
could turn any request into a 500 by sending a token with a made-up `kid`.
"""

from __future__ import annotations

import jwt
import pytest

from platform_core import auth
from platform_core.config import ServiceSettings
from platform_core.errors import UnauthorizedProblem


class _KeyClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def get_signing_key_from_jwt(self, token: str) -> None:
        raise self.exc


def test_an_unknown_kid_is_a_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_jwks", lambda _s: _KeyClient(jwt.PyJWKClientError("no key")))
    with pytest.raises(UnauthorizedProblem):
        auth.decode_token(ServiceSettings.model_construct(), "x.y.z", expected_type="access")


def test_an_unreachable_jwks_endpoint_stays_a_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Logging everyone out because auth-service blipped would be worse than a 5xx.
    exc = jwt.PyJWKClientConnectionError("down")
    monkeypatch.setattr(auth, "_jwks", lambda _s: _KeyClient(exc))
    with pytest.raises(jwt.PyJWKClientConnectionError):
        auth.decode_token(ServiceSettings.model_construct(), "x.y.z", expected_type="access")
