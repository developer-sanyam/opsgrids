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

from platform_core.auth import _principal_from_gateway, client_ip, get_principal
from platform_core.config import ServiceSettings
from platform_core.errors import UnauthorizedProblem
from platform_core.internal_auth import (
    ADMIN_HOP_NONCE_TTL_SECONDS,
    ADMIN_HOP_TTL_SECONDS,
    GATEWAY_AUDIENCE,
    canonical_query,
    forwarded_headers_digest,
    mint_service_token,
    sign_admin_hop,
    verify_admin_hop,
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
        # The TCP peer a service sees behind a gateway: the gateway's own container address.
        "client": (GATEWAY_PEER, 51000),
    }
    return Request(scope)


GATEWAY_PEER = "172.18.0.5"

IDENTITY = {
    "X-User-Id": USER_ID,
    "X-Org-Id": ORG_ID,
    "X-Perms": "crm.pipeline.deal.read:ORG",
    "X-Entitlements": "crm",
}


def hop_token(
    cfg: ServiceSettings,
    *,
    uid: str = USER_ID,
    org: str = ORG_ID,
    bound: dict[str, str] = IDENTITY,
) -> str:
    """A forwarding token as the gateway mints it: naming uid/org and binding the digest of the
    forwarded headers it travels with (`bound`)."""
    fwd = forwarded_headers_digest(request_with(bound, cfg).headers)
    return mint_service_token(
        cfg,
        audience=GATEWAY_AUDIENCE,
        caller="gateway",
        claims={"uid": uid, "org": org, "fwd": fwd},
    )


def identity_headers(token: str) -> dict[str, str]:
    return {"X-Internal-Token": token, **IDENTITY}


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


class TestForwardedHeadersAreBound:
    """A captured hop token is good for its TTL. Binding only uid/org left every other forwarded
    header free, so the capture could be replayed with `X-Perms: core.*:ORG` or
    `X-Audience: operator` and believed. The token now carries a digest of all of them."""

    @pytest.mark.parametrize(
        ("header", "escalated"),
        [
            ("X-Perms", "core.*:ORG"),
            ("X-Audience", "operator"),
            ("X-Amr", "pwd,otp,stepup"),
            ("X-Roles", "ORG_OWNER"),
            ("X-Entitlements", "crm,hrms,pulseboard"),
            ("X-Locations", "HQ,WH-1"),
            ("X-Subject-Ref", str(uuid.uuid4())),
            ("X-Membership-Id", str(uuid.uuid4())),
            ("X-Permission-Version", "99"),
            ("X-Session-Id", "another-session"),
            ("X-Org-Slug", "another-tenant"),
            ("X-Client-Ip", "203.0.113.50"),
        ],
    )
    def test_a_replayed_token_with_an_altered_header_is_refused(
        self, header: str, escalated: str
    ) -> None:
        cfg = settings()
        headers = {**identity_headers(hop_token(cfg)), header: escalated}
        with pytest.raises(UnauthorizedProblem, match="does not match"):
            _principal_from_gateway(request_with(headers, cfg), cfg)

    def test_a_token_binding_only_uid_and_org_is_refused(self) -> None:
        """The pre-binding token shape vouches for nothing beyond two ids; accepting it would
        keep the replay open."""
        cfg = settings()
        legacy = mint_service_token(
            cfg,
            audience=GATEWAY_AUDIENCE,
            caller="gateway",
            claims={"uid": USER_ID, "org": ORG_ID},
        )
        with pytest.raises(UnauthorizedProblem, match="does not match"):
            _principal_from_gateway(request_with(identity_headers(legacy), cfg), cfg)

    def test_the_headers_the_token_binds_still_forward(self) -> None:
        cfg = settings()
        bound = {**IDENTITY, "X-Audience": "app", "X-Amr": "pwd", "X-Roles": "MEMBER"}
        headers = {"X-Internal-Token": hop_token(cfg, bound=bound), **bound}
        principal = get_principal(request_with(headers, cfg))
        assert principal.audience == "app"
        assert principal.amr == ["pwd"]
        assert principal.roles == ["MEMBER"]


class TestClientIp:
    """Behind a gateway the TCP peer is the gateway, so an address recorded from it says
    nothing about the visitor. The gateway's `X-Client-Ip` is believed only under a hop token
    that binds it."""

    VISITOR = "198.51.100.23"

    def test_a_bound_client_ip_is_the_visitors_address(self) -> None:
        cfg = settings()
        bound = {"X-Client-Ip": self.VISITOR}
        headers = {"X-Internal-Token": hop_token(cfg, bound=bound), **bound}
        assert client_ip(request_with(headers, cfg)) == self.VISITOR

    def test_an_unvouched_client_ip_is_ignored(self) -> None:
        """A caller that reaches the service directly cannot name its own address."""
        cfg = settings()
        assert client_ip(request_with({"X-Client-Ip": self.VISITOR}, cfg)) == GATEWAY_PEER

    def test_a_client_ip_the_token_does_not_bind_is_ignored(self) -> None:
        cfg = settings()
        token = hop_token(cfg, bound={"X-Client-Ip": "203.0.113.1"})
        headers = {"X-Internal-Token": token, "X-Client-Ip": self.VISITOR}
        assert client_ip(request_with(headers, cfg)) == GATEWAY_PEER

    def test_no_request_is_no_address(self) -> None:
        assert client_ip(None) is None


class TestAdminGatewayHop:
    """BLOCKER 2 of the 2026-09-24 security review: a defence-in-depth signature admin-gateway
    writes on every request it proxies, checked against a key that is deliberately not the
    general `internal_service_token`."""

    KEY = "admin-gateway-hop-key-of-32-bytes!!"

    def test_a_freshly_signed_hop_verifies(self) -> None:
        signed = sign_admin_hop("POST", "/api/v1/auth/operator/login", key=self.KEY)
        assert verify_admin_hop(signed, "POST", "/api/v1/auth/operator/login", key=self.KEY)

    def test_a_different_method_refuses(self) -> None:
        signed = sign_admin_hop("POST", "/api/v1/auth/operator/login", key=self.KEY)
        assert not verify_admin_hop(signed, "GET", "/api/v1/auth/operator/login", key=self.KEY)

    def test_a_different_path_refuses(self) -> None:
        signed = sign_admin_hop("POST", "/api/v1/auth/operator/login", key=self.KEY)
        assert not verify_admin_hop(
            signed, "POST", "/api/v1/auth/operator/mfa/verify", key=self.KEY
        )

    def test_the_wrong_key_refuses(self) -> None:
        signed = sign_admin_hop("POST", "/api/v1/auth/operator/login", key=self.KEY)
        assert not verify_admin_hop(
            signed, "POST", "/api/v1/auth/operator/login", key="a-different-32-byte-key-entirely!!"
        )

    def test_an_expired_signature_refuses(self) -> None:
        signed = sign_admin_hop("POST", "/api/v1/auth/operator/login", key=self.KEY)
        far_future = 10_000_000_000.0
        assert not verify_admin_hop(
            signed, "POST", "/api/v1/auth/operator/login", key=self.KEY, now=far_future
        )

    def test_a_missing_header_refuses(self) -> None:
        assert not verify_admin_hop(None, "POST", "/api/v1/auth/operator/login", key=self.KEY)

    def test_a_malformed_header_refuses_rather_than_raising(self) -> None:
        assert not verify_admin_hop(
            "not-even-shaped-right", "POST", "/api/v1/auth/operator/login", key=self.KEY
        )
        assert not verify_admin_hop("abc.def", "POST", "/api/v1/auth/operator/login", key=self.KEY)

    def test_a_different_body_refuses(self) -> None:
        """m2: the digest of the body is signed, so a captured header cannot be replayed
        against the same path with a payload of the attacker's choosing."""
        path = "/api/v1/auth/operator/login"
        signed = sign_admin_hop("POST", path, key=self.KEY, body=b'{"email": "a"}')
        assert verify_admin_hop(signed, "POST", path, key=self.KEY, body=b'{"email": "a"}')
        assert not verify_admin_hop(signed, "POST", path, key=self.KEY, body=b'{"email": "b"}')
        assert not verify_admin_hop(signed, "POST", path, key=self.KEY)

    def test_a_different_query_refuses(self) -> None:
        path = "/api/v1/auth/operator/login"
        signed = sign_admin_hop("GET", path, key=self.KEY, query=canonical_query([("x", "1")]))
        assert verify_admin_hop(
            signed, "GET", path, key=self.KEY, query=canonical_query([("x", "1")])
        )
        assert not verify_admin_hop(
            signed, "GET", path, key=self.KEY, query=canonical_query([("x", "2")])
        )

    def test_the_canonical_query_ignores_order_but_not_content(self) -> None:
        assert canonical_query([("b", "2"), ("a", "1")]) == canonical_query(
            [("a", "1"), ("b", "2")]
        )
        assert canonical_query([("a", "1")]) != canonical_query([("a", "2")])

    def test_every_signature_carries_a_fresh_nonce(self) -> None:
        """Without this the callee's nonce store would have nothing to tell two legitimate
        identical requests apart from a replay."""
        path = "/api/v1/auth/operator/login"
        first = verify_admin_hop(
            sign_admin_hop("POST", path, key=self.KEY), "POST", path, key=self.KEY
        )
        second = verify_admin_hop(
            sign_admin_hop("POST", path, key=self.KEY), "POST", path, key=self.KEY
        )
        assert first and second and first != second

    def test_verification_returns_the_nonce_the_header_carries(self) -> None:
        path = "/api/v1/auth/operator/login"
        signed = sign_admin_hop("POST", path, key=self.KEY)
        nonce = verify_admin_hop(signed, "POST", path, key=self.KEY)
        assert nonce == signed.split(".")[1]

    def test_a_unicode_digit_timestamp_is_refused_not_a_500(self) -> None:
        """`'²'.isdigit()` is True and `int('²')` raises — the old check let it through to an
        unhandled ValueError. The fullmatch on ASCII digits refuses it as malformed."""
        path = "/api/v1/auth/operator/login"
        signed = sign_admin_hop("POST", path, key=self.KEY)
        _ts, nonce, mac = signed.split(".")
        for forged_ts in ("²", "٣٣", "12²"):
            forged = f"{forged_ts}.{nonce}.{mac}"
            assert verify_admin_hop(forged, "POST", path, key=self.KEY) is None

    def test_an_absurdly_long_timestamp_is_refused_not_an_overflow(self) -> None:
        """Past Python's int-conversion limit a digit string raises ValueError; bounded to
        twelve digits it never reaches `int()`."""
        path = "/api/v1/auth/operator/login"
        signed = sign_admin_hop("POST", path, key=self.KEY)
        _ts, nonce, mac = signed.split(".")
        assert verify_admin_hop(f"{'9' * 5000}.{nonce}.{mac}", "POST", path, key=self.KEY) is None
        assert verify_admin_hop(f"{'1' * 13}.{nonce}.{mac}", "POST", path, key=self.KEY) is None

    def test_the_nonce_outlives_the_whole_acceptance_window_with_margin(self) -> None:
        """A timestamp up to TTL in the past or in the future still verifies, so a nonce must be
        remembered for both windows plus a margin — or it could expire while still replayable."""
        assert ADMIN_HOP_NONCE_TTL_SECONDS == 2 * ADMIN_HOP_TTL_SECONDS + 5
