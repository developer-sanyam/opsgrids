"""A token naming a key the platform never published is a forgery, not a server fault.

`PyJWKClientError` is not an `InvalidTokenError`, so before this was caught an anonymous caller
could turn any request into a 500 by sending a token with a made-up `kid`.
"""

from __future__ import annotations

import jwt
import pytest

from platform_core import auth
from platform_core.config import ServiceSettings
from platform_core.errors import UnauthorizedProblem, UpstreamUnavailableProblem


class _KeyClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def get_signing_key_from_jwt(self, token: str) -> None:
        raise self.exc


def test_an_unknown_kid_is_a_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_jwks", lambda _s: _KeyClient(jwt.PyJWKClientError("no key")))
    with pytest.raises(UnauthorizedProblem):
        auth.decode_token(ServiceSettings.model_construct(), "x.y.z", expected_type="access")


@pytest.mark.parametrize(
    "exc",
    [jwt.PyJWKClientConnectionError("down"), jwt.PyJWKSetError("no usable keys")],
    ids=["unreachable", "no-usable-key"],
)
def test_a_jwks_we_cannot_use_is_a_503_not_a_401(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    # Our outage, not a bad credential: a 401 would make every SPA sign its user out.
    monkeypatch.setattr(auth, "_jwks", lambda _s: _KeyClient(exc))
    with pytest.raises(UpstreamUnavailableProblem):
        auth.decode_token(ServiceSettings.model_construct(), "x.y.z", expected_type="access")
