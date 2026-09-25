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

    async def test_an_operator_token_that_never_completed_mfa_gets_403(
        self, client: AsyncClient
    ) -> None:
        """m8 of the third-round security review: the right audience and an `owner` role are
        not enough without `otp` in `amr` — refused at the route, 403 (not the audience
        boundary's 404: the caller has already proven they are staff)."""
        with as_principal(operator_principal(staff=True, roles=["owner"], amr=["pwd"])):
            response = await client.get("/api/v1/admin/organizations")
        assert response.status_code == 403

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


# --------------------------------------------------------------------- organizations list


class TestListOrganizations:
    """`OrgStateOut`'s list fields (ui/backend-data-2): `lifecycle`, `country`, `created_at` —
    the accounts list's Status/Region/Created filters."""

    async def test_lists_lifecycle_country_and_created_at(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        from datetime import UTC, datetime

        from app.models import OrgCommercialState

        async with admin_session.begin():
            admin_session.add_all(
                [
                    OrgCommercialState(
                        organization_id=org_id,
                        mrr_minor=10000,
                        currency="INR",
                        country="IN",
                        org_created_at=datetime(2025, 6, 1, tzinfo=UTC),
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
            response = await client.get("/api/v1/admin/organizations")
        assert response.status_code == 200, response.text
        rows = {row["organization_id"]: row for row in response.json()["organizations"]}

        active_row = rows[str(org_id)]
        assert active_row["lifecycle"] == "active"
        assert active_row["country"] == "IN"
        created_at = datetime.fromisoformat(active_row["created_at"].replace("Z", "+00:00"))
        assert created_at == datetime(2025, 6, 1, tzinfo=UTC)

        suspended_row = rows[str(org_b)]
        assert suspended_row["lifecycle"] == "suspended"
        assert suspended_row["country"] is None
        assert suspended_row["created_at"] is None

    async def test_an_org_the_fold_has_never_touched_has_no_lifecycle_bucket(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        from app.models import OrgCommercialState

        async with admin_session.begin():
            admin_session.add(OrgCommercialState(organization_id=org_id, mrr_minor=0))

        with as_principal(operator_principal(staff=True)):
            response = await client.get("/api/v1/admin/organizations")
        assert response.status_code == 200, response.text
        row = next(
            r for r in response.json()["organizations"] if r["organization_id"] == str(org_id)
        )
        assert row["lifecycle"] == "none"


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
            response = await client.get(
                f"/api/v1/admin/organizations/{org_id}",
                headers={"Authorization": "Bearer the-operators-own-access-token"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["slug"] == "acme"
        assert body["commercial"]["mrr_minor"] == 50000

        # account-service is handed this request's own operator bearer token, forwarded
        # verbatim, not an assertion about it (MINOR 7 of the security review) — so
        # account-service can verify it independently rather than trust console's word.
        call = captured.calls[0]
        assert call.headers["x-operator-token"] == "the-operators-own-access-token"
        # ... and, separately (m6 of the third-round review), console's own service token:
        # addressed to account, issued by console, and asserting nothing about the operator.
        claims = jwt.decode(
            call.headers["x-internal-token"],
            settings().internal_service_token.get_secret_value(),
            algorithms=["HS256"],
            audience="account",
            options={"require": ["exp", "iss", "aud", "jti"]},
        )
        assert claims["iss"] == "console"
        assert not {"operator_sub", "operator_roles", "operator_amr"} & claims.keys()

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


# --------------------------------------------------------------------- platform metrics series


class TestMetricsSeries:
    async def test_empty_platform_is_null_every_month(self, client: AsyncClient) -> None:
        with as_principal(operator_principal(staff=True)):
            response = await client.get("/api/v1/admin/metrics/series?months=3")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["mrr_minor"]) == 3
        assert all(point["value"] is None for point in body["mrr_minor"])
        assert all(point["value"] is None for point in body["new_organizations"])

    async def test_reports_mrr_new_organizations_trials_and_churn_by_month(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        from datetime import UTC, datetime

        from app.models import OrgCommercialState, RevenueEvent

        def _months_ago(n: int) -> datetime:
            now = datetime.now(UTC)
            year, month = now.year, now.month - n
            while month <= 0:
                month += 12
                year -= 1
            return datetime(year, month, 15, tzinfo=UTC)

        older_month = _months_ago(2).strftime("%Y-%m")
        newer_month = _months_ago(1).strftime("%Y-%m")
        current_month = datetime.now(UTC).strftime("%Y-%m")

        async with admin_session.begin():
            admin_session.add_all(
                [
                    OrgCommercialState(
                        organization_id=org_id,
                        mrr_minor=50000,
                        currency="INR",
                        org_created_at=_months_ago(2),
                        subscription_states={"hrms": {"state": "active", "amount_minor": 50000}},
                    ),
                    OrgCommercialState(organization_id=org_b, mrr_minor=0, currency="INR"),
                    RevenueEvent(
                        organization_id=org_id,
                        kind="commerce.subscription.trial_started",
                        amount_minor=0,
                        currency="INR",
                        occurred_at=_months_ago(2),
                        meta={
                            "from_state": None,
                            "to_state": "trial",
                            "product_key": "hrms",
                            "stale": False,
                        },
                    ),
                    RevenueEvent(
                        organization_id=org_id,
                        kind="commerce.subscription.activated",
                        amount_minor=50000,
                        currency="INR",
                        occurred_at=_months_ago(1),
                        meta={
                            "from_state": "trial",
                            "to_state": "active",
                            "product_key": "hrms",
                            "stale": False,
                        },
                    ),
                ]
            )

        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.get("/api/v1/admin/metrics/series?months=4")
        assert response.status_code == 200, response.text
        body = response.json()
        points = {
            series: {p["period"]: p["value"] for p in body[series]}
            for series in (
                "mrr_minor",
                "new_organizations",
                "trials_started",
                "trial_conversions",
                "churned",
            )
        }
        assert older_month in points["mrr_minor"], "the four requested months trail the current one"

        assert points["mrr_minor"][older_month] == 0, "the trial alone pays nothing"
        assert points["mrr_minor"][newer_month] == 50000
        assert points["mrr_minor"][current_month] == 50000, "MRR carries forward"

        assert points["new_organizations"][older_month] == 1
        assert points["new_organizations"][newer_month] == 0

        assert points["trials_started"][older_month] == 1
        assert points["trials_started"][newer_month] == 0

        assert points["trial_conversions"][older_month] == 0
        assert points["trial_conversions"][newer_month] == 1

        assert points["churned"][newer_month] == 0

    async def test_the_months_query_param_is_bounded(self, client: AsyncClient) -> None:
        with as_principal(operator_principal(staff=True)):
            too_many = await client.get("/api/v1/admin/metrics/series?months=37")
            too_few = await client.get("/api/v1/admin/metrics/series?months=0")
        assert too_many.status_code == 422
        assert too_few.status_code == 422

    async def test_support_may_not_read_the_series(self, client: AsyncClient) -> None:
        with as_principal(operator_principal(roles=["support"])):
            response = await client.get("/api/v1/admin/metrics/series")
        assert response.status_code == 403

    async def test_an_unauthenticated_request_is_refused(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/admin/metrics/series")
        assert response.status_code == 401


# --------------------------------------------------------------------- audit and usage paging


class TestAuditAndUsagePaging:
    async def test_organization_id_filters_audit_to_one_org(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        from app.models import AuditLog

        async with admin_session.begin():
            admin_session.add_all(
                [
                    AuditLog(organization_id=org_id, action="override.suspend", entity="x"),
                    AuditLog(organization_id=org_b, action="override.suspend", entity="x"),
                ]
            )

        with as_principal(operator_principal(roles=["support"])):
            response = await client.get(
                "/api/v1/admin/audit", params={"organization_id": str(org_id)}
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["audit"]) == 1
        assert body["audit"][0]["organization_id"] == str(org_id)

    async def test_organization_id_filters_usage_to_one_org(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        from app.models import UsageSummary

        async with admin_session.begin():
            admin_session.add_all(
                [
                    UsageSummary(
                        organization_id=org_id, meter_key="seats", period="2026-01", quantity=5
                    ),
                    UsageSummary(
                        organization_id=org_b, meter_key="seats", period="2026-01", quantity=9
                    ),
                ]
            )

        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.get(
                "/api/v1/admin/usage", params={"organization_id": str(org_id)}
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["usage"]) == 1
        assert body["usage"][0]["organization_id"] == str(org_id)

    async def test_no_params_returns_the_existing_envelope_shape(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        from app.models import AuditLog

        async with admin_session.begin():
            admin_session.add(
                AuditLog(organization_id=org_id, action="override.suspend", entity="x")
            )

        with as_principal(operator_principal(roles=["support"])):
            response = await client.get("/api/v1/admin/audit")
        assert response.status_code == 200, response.text
        body = response.json()
        assert "audit" in body
        assert body["next_cursor"] is None, "one row never fills a 200-row page"

    async def test_a_cursor_pages_through_every_audit_row_with_no_gaps_or_repeats(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        from datetime import UTC, datetime, timedelta

        from app.models import AuditLog

        base = datetime(2026, 1, 1, tzinfo=UTC)
        async with admin_session.begin():
            admin_session.add_all(
                [
                    AuditLog(
                        organization_id=org_id,
                        action="override.suspend",
                        entity="x",
                        at=base + timedelta(minutes=i),
                    )
                    for i in range(5)
                ]
            )

        seen: list[str] = []
        cursor: str | None = None
        with as_principal(operator_principal(roles=["support"])):
            for _ in range(10):
                params = {"limit": 2}
                if cursor is not None:
                    params["cursor"] = cursor
                response = await client.get("/api/v1/admin/audit", params=params)
                assert response.status_code == 200, response.text
                body = response.json()
                seen.extend(row["id"] for row in body["audit"])
                cursor = body["next_cursor"]
                if cursor is None:
                    break
        assert len(seen) == 5
        assert len(set(seen)) == 5, "a page boundary must never repeat a row"

    async def test_a_cursor_pages_through_every_usage_row(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        from app.models import UsageSummary

        async with admin_session.begin():
            admin_session.add_all(
                [
                    UsageSummary(
                        organization_id=org_id,
                        meter_key=f"meter-{i}",
                        period="2026-01",
                        quantity=i,
                    )
                    for i in range(5)
                ]
            )

        seen: list[str] = []
        cursor: str | None = None
        with as_principal(operator_principal(roles=["billing_ops"])):
            for _ in range(10):
                params = {"limit": 2}
                if cursor is not None:
                    params["cursor"] = cursor
                response = await client.get("/api/v1/admin/usage", params=params)
                assert response.status_code == 200, response.text
                body = response.json()
                seen.extend(row["meter_key"] for row in body["usage"])
                cursor = body["next_cursor"]
                if cursor is None:
                    break
        assert len(seen) == 5
        assert len(set(seen)) == 5

    async def test_a_malformed_audit_cursor_is_422_not_500(self, client: AsyncClient) -> None:
        with as_principal(operator_principal(roles=["support"])):
            response = await client.get(
                "/api/v1/admin/audit", params={"cursor": "not-a-real-cursor"}
            )
        assert response.status_code == 422

    async def test_a_malformed_usage_cursor_is_422_not_500(self, client: AsyncClient) -> None:
        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.get(
                "/api/v1/admin/usage", params={"cursor": "not-a-real-cursor"}
            )
        assert response.status_code == 422
