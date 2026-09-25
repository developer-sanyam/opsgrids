"""`POST /admin/overrides` with `action: "reactivate"` — `suspend`'s inverse.

The override never writes another service's schema: it delegates to subscription-service's
internal reactivate (`/internal/v1/subscriptions/{id}/reactivate`) under a service token addressed
to it, then records console's own audit row bound to the target organization. Pinned here:

1. **Delegation** — the right endpoint, a service token whose audience is `subscription`, and a
   body that names the organization (subscription binds its transaction to it), the accountable
   operator and the reason.
2. **Symmetry with suspend** — same role gate (`owner`/`billing_ops`), same audit shape
   (`override.reactivate`, entity `subscription`), same "no downstream call on a refused caller".
3. **A reason is required** — a missing, blank or over-long one is a 422 that reaches nothing.
4. **Wrong-state 409** — subscription answers 409 for a subscription that is not suspended, and
   the operator gets that 409 (with its detail), not a 503 that reads like an outage; nothing is
   audited for an act that did not happen.
5. **Other failures stay failures** — unreachable or a non-409 refusal is a 503.
6. **The latent suspend bug this exposed** — suspend's delegated body used to omit
   `organization_id`, which subscription's `SuspendIn` requires, so the real service could only
   ever have answered 422. Both actions now send it.
"""

from __future__ import annotations

import uuid

import httpx
import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.config import settings
from tests.helpers import Downstream, as_principal, operator_principal

pytestmark = pytest.mark.integration


def _reactivate_body(org_id: uuid.UUID, subscription_id: uuid.UUID, **overrides: object) -> dict:
    body: dict[str, object] = {
        "action": "reactivate",
        "organization_id": str(org_id),
        "subscription_id": str(subscription_id),
        "reason": "customer paid offline",
    }
    body.update(overrides)
    return body


def _subscription_ok(subscription_id: uuid.UUID, org_id: uuid.UUID) -> httpx.Response:
    return httpx.Response(200, json={"id": str(subscription_id), "state": "active"})


class TestReactivateDelegation:
    async def test_reactivate_delegates_to_subscription_and_audits_it(
        self,
        client: AsyncClient,
        directory_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id = uuid.uuid4()
        downstream = Downstream(
            {
                f"/subscriptions/{subscription_id}/reactivate": _subscription_ok(
                    subscription_id, org_id
                )
            }
        )
        downstream.install()
        principal = operator_principal(roles=["billing_ops"])

        with as_principal(principal):
            response = await client.post(
                "/api/v1/admin/overrides", json=_reactivate_body(org_id, subscription_id)
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["action"] == "reactivate"
        assert body["delegated_to"] == "subscription"

        assert len(downstream.calls) == 1
        call = downstream.calls[0]
        assert call.method == "POST"
        assert call.url.path == f"/internal/v1/subscriptions/{subscription_id}/reactivate"
        claims = jwt.decode(
            call.headers["X-Internal-Token"],
            settings().internal_service_token.get_secret_value(),
            algorithms=["HS256"],
            audience="subscription",
            options={"require": ["exp", "iss", "aud", "jti"]},
        )
        assert claims["iss"] == "console"
        sent = httpx.Response(200, content=call.content).json()
        assert sent == {
            "organization_id": str(org_id),
            "operator_id": str(principal.sub),
            "reason": "customer paid offline",
        }

        audit = await selectors.recent_audit(directory_session, organization_id=org_id)
        assert len(audit) == 1
        assert audit[0].action == "override.reactivate"
        assert audit[0].entity == "subscription"
        assert audit[0].entity_id == subscription_id
        assert audit[0].reason == "customer paid offline"
        assert audit[0].organization_id == org_id
        assert audit[0].acting_as == f"operator:{principal.sub}"

    async def test_the_audit_row_is_bound_to_the_target_organization_only(
        self,
        client: AsyncClient,
        directory_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        subscription_id = uuid.uuid4()
        Downstream(
            {
                f"/subscriptions/{subscription_id}/reactivate": _subscription_ok(
                    subscription_id, org_id
                )
            }
        ).install()
        with as_principal(operator_principal(roles=["owner"])):
            await client.post(
                "/api/v1/admin/overrides", json=_reactivate_body(org_id, subscription_id)
            )
        assert await selectors.recent_audit(directory_session, organization_id=org_b) == []


class TestReactivateValidation:
    @pytest.mark.parametrize("reason", [None, "", "   ", "x" * 201])
    async def test_a_missing_blank_or_overlong_reason_is_422_and_reaches_nothing(
        self,
        client: AsyncClient,
        directory_session: AsyncSession,
        org_id: uuid.UUID,
        reason: str | None,
    ) -> None:
        downstream = Downstream({})
        downstream.install()
        body = _reactivate_body(org_id, uuid.uuid4())
        if reason is None:
            del body["reason"]
        else:
            body["reason"] = reason

        with as_principal(operator_principal(roles=["owner"])):
            response = await client.post("/api/v1/admin/overrides", json=body)
        assert response.status_code == 422, response.text
        assert downstream.calls == []
        assert await selectors.recent_audit(directory_session) == []

    async def test_reactivate_without_a_subscription_id_is_422(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        downstream = Downstream({})
        downstream.install()
        body = _reactivate_body(org_id, uuid.uuid4())
        del body["subscription_id"]
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.post("/api/v1/admin/overrides", json=body)
        assert response.status_code == 422
        assert downstream.calls == []

    async def test_an_unknown_action_is_still_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.post(
                "/api/v1/admin/overrides",
                json=_reactivate_body(org_id, uuid.uuid4(), action="unsuspend"),
            )
        assert response.status_code == 422


class TestReactivateFailureModes:
    async def test_a_subscription_that_is_not_suspended_is_a_409_with_its_detail(
        self, client: AsyncClient, directory_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        subscription_id = uuid.uuid4()
        Downstream(
            {
                f"/subscriptions/{subscription_id}/reactivate": httpx.Response(
                    409,
                    json={
                        "detail": (
                            "cannot reactivate a subscription that is active: "
                            "only a suspended one can be reactivated"
                        )
                    },
                )
            }
        ).install()
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.post(
                "/api/v1/admin/overrides", json=_reactivate_body(org_id, subscription_id)
            )
        assert response.status_code == 409, response.text
        assert "only a suspended one can be reactivated" in response.json()["detail"]
        assert await selectors.recent_audit(directory_session) == [], (
            "an act that did not happen must not be audited as if it had"
        )

    async def test_an_unreachable_subscription_service_is_a_503(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        Downstream({}).install()  # 503 everywhere
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.post(
                "/api/v1/admin/overrides", json=_reactivate_body(org_id, uuid.uuid4())
            )
        assert response.status_code == 503

    async def test_a_non_409_refusal_is_a_503_not_a_conflict(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        subscription_id = uuid.uuid4()
        Downstream({f"/subscriptions/{subscription_id}/reactivate": httpx.Response(422)}).install()
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.post(
                "/api/v1/admin/overrides", json=_reactivate_body(org_id, subscription_id)
            )
        assert response.status_code == 503


class TestReactivateAuthz:
    async def test_support_may_not_reactivate(self, client: AsyncClient, org_id: uuid.UUID) -> None:
        downstream = Downstream({})
        downstream.install()
        with as_principal(operator_principal(roles=["support"])):
            response = await client.post(
                "/api/v1/admin/overrides", json=_reactivate_body(org_id, uuid.uuid4())
            )
        assert response.status_code == 403
        assert downstream.calls == []

    async def test_an_operator_token_that_never_completed_mfa_gets_403(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        downstream = Downstream({})
        downstream.install()
        with as_principal(operator_principal(roles=["owner"], amr=["pwd"])):
            response = await client.post(
                "/api/v1/admin/overrides", json=_reactivate_body(org_id, uuid.uuid4())
            )
        assert response.status_code == 403
        assert downstream.calls == []

    async def test_a_product_audience_token_gets_404_and_delegates_nothing(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        downstream = Downstream({})
        downstream.install()
        with as_principal(operator_principal(staff=False)):
            response = await client.post(
                "/api/v1/admin/overrides", json=_reactivate_body(org_id, uuid.uuid4())
            )
        assert response.status_code == 404
        assert downstream.calls == []

    async def test_an_unauthenticated_request_is_401(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        response = await client.post(
            "/api/v1/admin/overrides", json=_reactivate_body(org_id, uuid.uuid4())
        )
        assert response.status_code == 401


class TestSuspendNowSendsTheOrganization:
    async def test_suspends_delegated_body_names_the_organization_and_operator(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        subscription_id = uuid.uuid4()
        downstream = Downstream(
            {
                f"/subscriptions/{subscription_id}/suspend": httpx.Response(
                    200, json={"state": "suspended"}
                )
            }
        )
        downstream.install()
        principal = operator_principal(roles=["owner"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/admin/overrides",
                json={
                    "action": "suspend",
                    "organization_id": str(org_id),
                    "subscription_id": str(subscription_id),
                    "reason": "non-payment",
                },
            )
        assert response.status_code == 200, response.text
        sent = httpx.Response(200, content=downstream.calls[0].content).json()
        assert sent["organization_id"] == str(org_id), (
            "subscription's SuspendIn requires it; without it the real service answers 422"
        )
        assert sent["operator_id"] == str(principal.sub)
        assert sent["reason"] == "non-payment"
