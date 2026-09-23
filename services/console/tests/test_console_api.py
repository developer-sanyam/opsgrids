"""The staff read API's boundary, and that an override delegates rather than writes.

Three properties (ADR-0003 §2):

1. **Audience boundary** — a product (`app`-audience) token is refused with 404 in-service, the same
   answer an unknown route gives, so the surface is never confirmed to a prober. This is the
   in-service mirror of the gateway's own staff gate (the gateway refuses a product token at
   signature time and 404s a non-staff operator token; it does not forward the staff flag, so all
   the service can check is the audience — exactly what catalog's `require_operator` checks).
2. **Override delegation** — `POST /admin/overrides` calls the *owning* service's internal endpoint
   under a service token addressed to that service by name. Console writes no foreign schema; its
   only local write is an audit row in its own schema, which `/admin/audit` reads back.
3. **Negative-authz** — an unauthenticated caller is refused; a non-operator override attempts no
   delegation at all.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.api.routes import get_delegation_client
from app.config import settings
from app.main import app
from tests.helpers import as_principal, operator_principal

pytestmark = pytest.mark.integration


def _decoded_audience(token: str) -> str:
    """The `aud` of a minted service token — what stops it being replayed elsewhere."""
    claims = jwt.decode(
        token,
        settings().internal_service_token.get_secret_value(),
        algorithms=["HS256"],
        options={"verify_aud": False, "require": ["exp", "iss", "aud", "jti"]},
    )
    return str(claims["aud"])


class _Captured:
    """Records the single outbound delegated call so the test can assert on it."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []


def _mock_delegation(captured: _Captured, *, response: dict) -> None:
    """Override the endpoint's HTTP client with a mock transport recording every request."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.calls.append(request)
        return httpx.Response(200, json=response)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def _override() -> AsyncIterator[httpx.AsyncClient]:
        yield mock_client

    app.dependency_overrides[get_delegation_client] = _override


# --------------------------------------------------------------------- audience boundary


class TestAudienceBoundary:
    async def test_an_operator_token_reaches_a_staff_route(self, client: AsyncClient) -> None:
        with as_principal(operator_principal(staff=True)):
            response = await client.get("/api/v1/admin/organizations")
        assert response.status_code == 200
        assert "organizations" in response.json()

    async def test_a_product_audience_token_gets_404(self, client: AsyncClient) -> None:
        """A stolen product token is inert here — 404, the same answer an unknown path gives, so the
        operator surface is never confirmed. Mirrors the admin-gateway staff-route boundary."""
        with as_principal(operator_principal(staff=False)):
            response = await client.get("/api/v1/admin/organizations")
        assert response.status_code == 404

    async def test_an_unauthenticated_caller_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/admin/organizations")
        assert response.status_code == 401

    async def test_every_staff_route_refuses_a_product_token(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        for path in (
            "/api/v1/admin/organizations",
            f"/api/v1/admin/organizations/{org_id}",
            "/api/v1/admin/metrics/overview",
            "/api/v1/admin/subscriptions",
            "/api/v1/admin/usage",
            "/api/v1/admin/audit",
        ):
            with as_principal(operator_principal(staff=False)):
                response = await client.get(path)
            assert response.status_code == 404, path


# --------------------------------------------------------------------- role matrix


class TestRoleMatrix:
    """The ADR-0003 addendum's role matrix, enforced per route rather than by audience alone."""

    async def test_support_may_read_organizations_but_not_billing_views(
        self, client: AsyncClient
    ) -> None:
        with as_principal(operator_principal(roles=["support"])):
            orgs = await client.get("/api/v1/admin/organizations")
            subs = await client.get("/api/v1/admin/subscriptions")
        assert orgs.status_code == 200
        assert subs.status_code == 403

    async def test_billing_ops_may_read_subscriptions_but_not_the_audit_trail(
        self, client: AsyncClient
    ) -> None:
        with as_principal(operator_principal(roles=["billing_ops"])):
            subs = await client.get("/api/v1/admin/subscriptions")
            audit = await client.get("/api/v1/admin/audit")
        assert subs.status_code == 200
        assert audit.status_code == 403

    async def test_a_role_outside_the_set_is_403_not_404(self, client: AsyncClient) -> None:
        """Right audience — the surface's existence is already conceded — so a role mismatch is
        an ordinary Forbidden, never the audience boundary's 404."""
        with as_principal(operator_principal(roles=["support"])):
            response = await client.get("/api/v1/admin/metrics/overview")
        assert response.status_code == 403

    async def test_support_may_not_read_usage(self, client: AsyncClient) -> None:
        with as_principal(operator_principal(roles=["support"])):
            response = await client.get("/api/v1/admin/usage")
        assert response.status_code == 403

    async def test_support_may_not_create_an_override(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal(roles=["support"])):
            response = await client.post(
                "/api/v1/admin/overrides",
                json={
                    "action": "suspend",
                    "organization_id": str(org_id),
                    "subscription_id": str(uuid.uuid4()),
                },
            )
        assert response.status_code == 403


# --------------------------------------------------------------------- override delegation


class TestOverrideDelegation:
    async def test_suspend_delegates_to_subscription_under_a_service_token(
        self,
        client: AsyncClient,
        directory_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """The headline: a suspend override calls subscription's internal endpoint under a service
        token addressed to subscription, and console writes only its own audit row."""
        captured = _Captured()
        _mock_delegation(captured, response={"status": "suspended"})
        subscription_id = uuid.uuid4()

        with as_principal(operator_principal(staff=True)):
            response = await client.post(
                "/api/v1/admin/overrides",
                json={
                    "action": "suspend",
                    "organization_id": str(org_id),
                    "subscription_id": str(subscription_id),
                    "reason": "non-payment",
                },
            )

        assert response.status_code == 200
        assert response.json()["delegated_to"] == "subscription"

        # The outbound call: exactly one, to subscription's internal suspend endpoint, under a
        # service token whose audience is subscription (so it cannot be replayed against billing).
        assert len(captured.calls) == 1
        call = captured.calls[0]
        assert call.method == "POST"
        assert f"/internal/v1/subscriptions/{subscription_id}/suspend" in str(call.url)
        token = call.headers["X-Internal-Token"]
        assert _decoded_audience(token) == "subscription"

        # Console's only local write is its own audit row, bound to the target organization.
        audit = await selectors.recent_audit(directory_session)
        assert len(audit) == 1
        assert audit[0].action == "override.suspend"
        assert audit[0].organization_id == org_id
        assert audit[0].acting_as is not None, "an impersonated action must name the operator"

    async def test_comp_grant_delegates_to_billing(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        captured = _Captured()
        _mock_delegation(captured, response={"credit_id": str(uuid.uuid4())})

        with as_principal(operator_principal(staff=True)):
            response = await client.post(
                "/api/v1/admin/overrides",
                json={
                    "action": "comp_grant",
                    "organization_id": str(org_id),
                    "amount_minor": 100000,
                    "currency": "INR",
                    "reason": "goodwill",
                },
            )

        assert response.status_code == 200
        assert response.json()["delegated_to"] == "billing"
        call = captured.calls[0]
        assert "/internal/v1/credit" in str(call.url)
        assert _decoded_audience(call.headers["X-Internal-Token"]) == "billing"

    async def test_a_non_operator_override_delegates_nothing(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        """A product token is 404 before any delegation — no outbound call, no audit row."""
        captured = _Captured()
        _mock_delegation(captured, response={})

        with as_principal(operator_principal(staff=False)):
            response = await client.post(
                "/api/v1/admin/overrides",
                json={
                    "action": "suspend",
                    "organization_id": str(org_id),
                    "subscription_id": str(uuid.uuid4()),
                },
            )
        assert response.status_code == 404
        assert captured.calls == [], "a refused override must not reach the owning service"

    async def test_suspend_without_a_subscription_id_is_rejected(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        captured = _Captured()
        _mock_delegation(captured, response={})
        with as_principal(operator_principal(staff=True)):
            response = await client.post(
                "/api/v1/admin/overrides",
                json={"action": "suspend", "organization_id": str(org_id)},
            )
        assert response.status_code == 422
        assert captured.calls == [], "an invalid override must not reach the owning service"


# --------------------------------------------------------------------- org detail composite


class TestOrganizationDetail:
    async def test_composes_directory_identity_with_the_commercial_projection(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        from app.models import OrgCommercialState

        async with admin_session.begin():
            admin_session.add(
                OrgCommercialState(
                    organization_id=org_id,
                    mrr_minor=50000,
                    currency="INR",
                    active_products=["hrms"],
                    subscription_states={"hrms": {"state": "active"}},
                )
            )

        captured = _Captured()
        _mock_delegation(
            captured,
            response={
                "id": str(org_id),
                "slug": "acme",
                "name": "Acme Technologies",
                "status": "active",
                "country": "IN",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        with as_principal(operator_principal(staff=True)):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["slug"] == "acme"
        assert body["commercial"]["mrr_minor"] == 50000

        # account-service was called with an asserted service token, not the caller's own
        # bearer token (MINOR 8 of the security review). Decoded the way account-service
        # would — `verify_service_token` checks `aud == settings.service_name`, which is
        # "account", not console's own name, so this reads the same shared secret directly.
        call = captured.calls[0]
        assert "authorization" not in call.headers
        token = call.headers["x-internal-token"]
        claims = jwt.decode(
            token,
            settings().internal_service_token.get_secret_value(),
            algorithms=["HS256"],
            audience="account",
            options={"require": ["exp", "iss", "aud", "jti"]},
        )
        assert claims["iss"] == "console"
        assert claims["aud"] == "account"
        assert "operator_sub" in claims
        assert claims["operator_roles"]
        assert "otp" in claims["operator_amr"]

    async def test_an_organization_account_service_does_not_know_is_404(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def _override() -> AsyncIterator[httpx.AsyncClient]:
            yield mock_client

        app.dependency_overrides[get_delegation_client] = _override
        with as_principal(operator_principal(staff=True)):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}")
        assert response.status_code == 404

    async def test_account_service_being_unreachable_is_a_503_not_a_404(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def _override() -> AsyncIterator[httpx.AsyncClient]:
            yield mock_client

        app.dependency_overrides[get_delegation_client] = _override
        with as_principal(operator_principal(staff=True)):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}")
        assert response.status_code == 503

    async def test_reading_an_organizations_detail_is_audited(
        self, client: AsyncClient, directory_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        _mock_delegation(
            _Captured(),
            response={
                "id": str(org_id),
                "slug": "acme",
                "name": "Acme",
                "status": "active",
                "country": None,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        with as_principal(operator_principal(staff=True)):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}")
        assert response.status_code == 200
        audit = await selectors.recent_audit(directory_session)
        assert len(audit) == 1
        assert audit[0].action == "organization.viewed"
        assert audit[0].organization_id == org_id
        assert audit[0].acting_as is not None and audit[0].acting_as.startswith("operator:")

    async def test_an_unauthenticated_request_is_refused_before_any_lookup(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        response = await client.get(f"/api/v1/admin/organizations/{org_id}")
        assert response.status_code == 401


# --------------------------------------------------------------------- platform metrics overview


class TestMetricsOverview:
    async def test_reports_org_counts_by_lifecycle(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        from app.models import OrgCommercialState

        async with admin_session.begin():
            admin_session.add_all(
                [
                    OrgCommercialState(
                        organization_id=org_id,
                        mrr_minor=10000,
                        currency="INR",
                        subscription_states={"hrms": {"state": "active"}},
                    ),
                    OrgCommercialState(
                        organization_id=org_b,
                        mrr_minor=0,
                        currency="INR",
                        locked=True,
                        subscription_states={"hrms": {"state": "active"}},
                    ),
                ]
            )

        with as_principal(operator_principal(staff=True)):
            response = await client.get("/api/v1/admin/metrics/overview")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["organization_count"] == 2
        assert body["organizations_by_lifecycle"]["active"] == 1
        assert body["organizations_by_lifecycle"]["suspended"] == 1
        assert body["mrr_minor"] == 10000
