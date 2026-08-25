"""The two internal channels: per-callee service tokens, and the gateway forwarding hop.

What these pin, in threat-model terms: the raw signing secret never travels (both channels
carry derived, expiring tokens); a token for one channel opens nothing on the other; and
the forwarding token vouches for exactly one user in one organization, so a capture cannot
be replayed with somebody else's identity headers.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from starlette.requests import Request

from platform_core.auth import _principal_from_gateway, get_principal
from platform_core.config import ServiceSettings
from platform_core.errors import UnauthorizedProblem
from platform_core.internal_auth import (
    GATEWAY_AUDIENCE,
    mint_service_token,
    verify_gateway_token,
    verify_service_token,
)

USER_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())


class _Settings(ServiceSettings):
    service_name: str = "account"
    db_schema: str = "account"


def settings(**kw: Any) -> ServiceSettings:
    return _Settings(**kw)


def request_with(headers: dict[str, str], cfg: ServiceSettings) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/anything",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "app": SimpleNamespace(state=SimpleNamespace(settings=cfg)),
    }
    return Request(scope)


def hop_token(cfg: ServiceSettings, *, uid: str = USER_ID, org: str = ORG_ID) -> str:
    return mint_service_token(
        cfg, audience=GATEWAY_AUDIENCE, caller="gateway", claims={"uid": uid, "org": org}
    )


def identity_headers(token: str) -> dict[str, str]:
    return {
        "X-Internal-Token": token,
        "X-User-Id": USER_ID,
        "X-Org-Id": ORG_ID,
        "X-Perms": "crm.pipeline.deal.read:ORG",
        "X-Entitlements": "crm",
    }


class TestChannelSeparation:
    def test_a_gateway_token_opens_no_internal_endpoint(self) -> None:
        """`verify_service_token` is the `/internal/v1` guard; the forwarding channel's
        audience must never satisfy it."""
        cfg = settings()
        with pytest.raises(jwt.PyJWTError):
            verify_service_token(cfg, hop_token(cfg))

    def test_a_service_token_is_not_a_forwarding_token(self) -> None:
        cfg = settings()
        token = mint_service_token(cfg, audience="account")
        with pytest.raises(jwt.PyJWTError):
            verify_gateway_token(cfg, token)

    def test_the_raw_secret_is_not_a_token_on_either_channel(self) -> None:
        """The failure mode this design replaced: the shared secret presented as a
        credential. It is a signing key, and a signing key opens nothing."""
        cfg = settings()
        raw = cfg.internal_service_token.get_secret_value()
        with pytest.raises(Exception):  # noqa: B017 - any refusal is the property
            verify_gateway_token(cfg, raw)
        with pytest.raises(Exception):  # noqa: B017
            verify_service_token(cfg, raw)


class TestForwardedIdentity:
    def test_a_valid_hop_token_yields_the_forwarded_principal(self) -> None:
        cfg = settings()
        request = request_with(identity_headers(hop_token(cfg)), cfg)
        principal = get_principal(request)
        assert str(principal.sub) == USER_ID
        assert str(principal.organization_id) == ORG_ID
        assert principal.perms == ["crm.pipeline.deal.read:ORG"]

    def test_identity_headers_the_token_does_not_vouch_for_are_refused(self) -> None:
        """A captured hop token replayed with somebody else's X-User-Id is tampering, and
        must not fall back to anything."""
        cfg = settings()
        token = hop_token(cfg, uid=str(uuid.uuid4()))  # vouches for a different user
        with pytest.raises(UnauthorizedProblem, match="does not match"):
            _principal_from_gateway(request_with(identity_headers(token), cfg), cfg)

    def test_a_swapped_organization_is_refused(self) -> None:
        cfg = settings()
        token = hop_token(cfg, org=str(uuid.uuid4()))
        with pytest.raises(UnauthorizedProblem, match="does not match"):
            _principal_from_gateway(request_with(identity_headers(token), cfg), cfg)

    def test_the_raw_secret_in_the_header_no_longer_forwards_identity(self) -> None:
        """Yesterday's wire format is today's spoof: a caller presenting the raw shared
        secret with identity headers gets no principal from the gateway path."""
        cfg = settings()
        headers = identity_headers(cfg.internal_service_token.get_secret_value())
        assert _principal_from_gateway(request_with(headers, cfg), cfg) is None

    def test_an_expired_hop_token_forwards_nothing(self) -> None:
        cfg = settings(service_token_ttl_seconds=-30)
        headers = identity_headers(hop_token(cfg))
        fresh = settings()
        assert _principal_from_gateway(request_with(headers, fresh), fresh) is None

    def test_a_token_signed_with_another_key_forwards_nothing(self) -> None:
        other = settings(internal_service_token="attacker-controlled-key-of-32-bytes!!")
        cfg = settings()
        headers = identity_headers(hop_token(other))
        assert _principal_from_gateway(request_with(headers, cfg), cfg) is None
