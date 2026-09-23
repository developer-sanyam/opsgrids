"""Policy administration (ADR-0006 §8, §10, §11; screens §4.G, slice 4): `GET /policies` and
`PUT /policies/{request_type}` — a value round-trips, a sub-floor `sla_minutes` is refused with a
field-level error rather than a raw integrity failure, a request type nobody has configured reads
as the honest default rather than a fabricated row, disabling turns off escalation and nothing
else, negative-authz on both endpoints, and cross-tenant isolation.

`GET /policies` is read-only and org-wide at **any** scope (ADR-0006 §11: "the one read endpoint
whose scope is not consulted at all") — every test below that only reads uses the narrowest
grant, `request.read:OWN`, to prove that holds.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.main import app
from app.models import ApprovalPolicy, ApprovalStep
from app.services import REQUEST_TYPE_LEAVE, REQUEST_TYPE_REGULARIZATION
from platform_core.auth import Principal, get_principal
from tests.helpers import open_request, principal_for, seed_person

pytestmark = pytest.mark.integration

READ_OWN = "core.approval.request.read:OWN"
WRITE_ORG = "core.approval.policy.write:ORG"
DECIDE_OWN = "core.approval.request.decide:OWN"

POLICIES_URL = "/api/v1/approvals/policies"


def policy_url(request_type: str) -> str:
    return f"{POLICIES_URL}/{request_type}"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


class TestPolicyReadDefaults:
    async def test_a_missing_policy_reads_as_the_default(self, client, org_id: uuid.UUID) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [READ_OWN])
        with as_principal(principal):
            response = await client.get(POLICIES_URL)
        assert response.status_code == 200
        body = response.json()
        assert body == [
            {"request_type": REQUEST_TYPE_REGULARIZATION, "sla_minutes": 1440, "enabled": True},
            {"request_type": REQUEST_TYPE_LEAVE, "sla_minutes": 1440, "enabled": True},
        ]


class TestPolicyRoundTrip:
    async def test_a_policy_round_trips(self, client, org_id: uuid.UUID) -> None:
        writer = principal_for(org_id, uuid.uuid4(), [WRITE_ORG])
        with as_principal(writer):
            put_response = await client.put(
                policy_url(REQUEST_TYPE_REGULARIZATION),
                json={"sla_minutes": 60, "enabled": False},
            )
        assert put_response.status_code == 200
        assert put_response.json() == {
            "request_type": REQUEST_TYPE_REGULARIZATION,
            "sla_minutes": 60,
            "enabled": False,
        }

        reader = principal_for(org_id, uuid.uuid4(), [READ_OWN])
        with as_principal(reader):
            get_response = await client.get(POLICIES_URL)
        assert get_response.status_code == 200
        assert get_response.json() == [
            {"request_type": REQUEST_TYPE_REGULARIZATION, "sla_minutes": 60, "enabled": False},
            {"request_type": REQUEST_TYPE_LEAVE, "sla_minutes": 1440, "enabled": True},
        ]

    async def test_a_second_write_updates_the_same_row_rather_than_creating_another(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        writer = principal_for(org_id, uuid.uuid4(), [WRITE_ORG])
        with as_principal(writer):
            await client.put(
                policy_url(REQUEST_TYPE_REGULARIZATION), json={"sla_minutes": 60, "enabled": True}
            )

        # Plain columns, not the mapped `ApprovalPolicy` entity: `client` and this assertion share
        # one session (`tests/conftest.py`), so an ORM-entity `select()` here would hand back the
        # same identity-mapped, `expire_on_commit=False` instance both times — its Python
        # attributes frozen at whatever the first load saw, since `update_policy`'s Core-level
        # `on_conflict_do_update` never goes through the unit of work and so never expires it. A
        # scalar column selection carries no identity map to go stale.
        row_stmt = select(ApprovalPolicy.sla_minutes, ApprovalPolicy.updated_at).where(
            ApprovalPolicy.organization_id == org_id,
            ApprovalPolicy.request_type == REQUEST_TYPE_REGULARIZATION,
        )
        async with session.begin():
            first_sla_minutes, first_updated_at = (await session.execute(row_stmt)).one()
        assert first_sla_minutes == 60

        with as_principal(writer):
            second = await client.put(
                policy_url(REQUEST_TYPE_REGULARIZATION),
                json={"sla_minutes": 120, "enabled": False},
            )
        assert second.status_code == 200
        assert second.json() == {
            "request_type": REQUEST_TYPE_REGULARIZATION,
            "sla_minutes": 120,
            "enabled": False,
        }

        async with session.begin():
            # `(organization_id, request_type)` is the primary key — `.one()` is itself the proof
            # that the second `PUT` updated the one row a `PUT` before it wrote rather than
            # creating a second one; a `.all()` and a `len(...) == 1` on top of that would only
            # re-assert what the primary key already guarantees can never be otherwise.
            second_sla_minutes, second_updated_at = (await session.execute(row_stmt)).one()
        assert second_sla_minutes == 120
        # Slice-4 review finding 6: `on_conflict_do_update`'s `set_` is taken literally and never
        # consults `TimestampMixin.updated_at`'s ORM-level `onupdate=func.now()` — without an
        # explicit `"updated_at": func.now()` in that `set_`, a second `PUT` would silently freeze
        # this column at the first write's instant forever (the same trap
        # `upsert_hierarchy_nodes_bulk` documents, `app/services.py`).
        assert second_updated_at > first_updated_at


class TestPolicyValidation:
    async def test_a_sub_floor_value_is_refused_with_a_field_level_error(
        self, client, org_id: uuid.UUID
    ) -> None:
        writer = principal_for(org_id, uuid.uuid4(), [WRITE_ORG])
        with as_principal(writer):
            response = await client.put(
                policy_url(REQUEST_TYPE_REGULARIZATION), json={"sla_minutes": 5, "enabled": True}
            )
        assert response.status_code == 422
        body = response.json()
        assert body["errors"]
        assert any(err["field"] == "sla_minutes" for err in body["errors"])
        # Never the constraint's own opaque, non-leaking 409 (`platform_core.errors`'
        # `IntegrityError` handler) — the boundary refuses it before it is ever written.
        assert body["status"] == 422

    async def test_an_over_ceiling_value_is_refused_with_a_field_level_error(
        self, client, org_id: uuid.UUID
    ) -> None:
        writer = principal_for(org_id, uuid.uuid4(), [WRITE_ORG])
        with as_principal(writer):
            response = await client.put(
                policy_url(REQUEST_TYPE_REGULARIZATION),
                json={"sla_minutes": 525601, "enabled": True},
            )
        assert response.status_code == 422
        assert any(err["field"] == "sla_minutes" for err in response.json()["errors"])

    async def test_an_unknown_request_type_is_refused(self, client, org_id: uuid.UUID) -> None:
        writer = principal_for(org_id, uuid.uuid4(), [WRITE_ORG])
        with as_principal(writer):
            response = await client.put(
                policy_url("hrms.time.something_else"),
                json={"sla_minutes": 60, "enabled": True},
            )
        assert response.status_code == 422
        assert any(err["field"] == "request_type" for err in response.json()["errors"])

    async def test_a_malformed_request_type_is_refused_at_the_path(
        self, client, org_id: uuid.UUID
    ) -> None:
        writer = principal_for(org_id, uuid.uuid4(), [WRITE_ORG])
        with as_principal(writer):
            response = await client.put(
                policy_url("not-a-request-type"), json={"sla_minutes": 60, "enabled": True}
            )
        assert response.status_code == 422


class TestDisablingNeverAutoApproves:
    async def test_disabling_stops_escalation_but_approvals_still_work(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """ADR-0006 §10: "disabling turns off escalation for that request type... it never turns
        off approval." Disabled through the real `PUT` endpoint, not seeded directly, so this
        proves the admin path itself, not just `_sla_due_at`'s own reading of a hand-written row
        (`tests/test_routing.py` already covers that one)."""
        writer = principal_for(org_id, uuid.uuid4(), [WRITE_ORG])
        with as_principal(writer):
            disable = await client.put(
                policy_url(REQUEST_TYPE_REGULARIZATION), json={"sla_minutes": 15, "enabled": False}
            )
        assert disable.status_code == 200

        requester, manager = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await seed_person(session, org_id, manager)
            await seed_person(session, org_id, requester, reporting_manager_id=manager)
        request = await open_request(session, org_id, requester_employee_id=requester)
        assert request is not None

        async with session.begin():
            step = (
                (
                    await session.execute(
                        select(ApprovalStep).where(
                            ApprovalStep.organization_id == org_id,
                            ApprovalStep.request_id == request.id,
                            ApprovalStep.outcome == "open",
                        )
                    )
                )
                .scalars()
                .one()
            )
        # Escalation is off: the step opened with no due date at all, so nothing ever becomes due.
        assert step.due_at is None

        # The sweep's own re-check refuses a step with no due date outright — escalation cannot
        # happen even if a stray id reached `escalate_step` directly.
        async with session.begin():
            result = await services.escalate_step(
                session, organization_id=org_id, request_id=request.id
            )
        assert result.escalated is False

        # Nothing about disabling escalation touches the decision path: the manager can still
        # approve it normally.
        decider = principal_for(org_id, manager, [DECIDE_OWN])
        with as_principal(decider):
            decision = await client.post(
                f"/api/v1/approvals/requests/{request.id}/decision", json={"outcome": "approve"}
            )
        assert decision.status_code == 200
        assert decision.json()["status"] == "approved"


class TestPolicyEndpointAuthorization:
    async def test_unauthenticated_cannot_read_policies(self, client, org_id: uuid.UUID) -> None:
        response = await client.get(POLICIES_URL)
        assert response.status_code == 401

    async def test_unauthenticated_cannot_write_a_policy(self, client, org_id: uuid.UUID) -> None:
        response = await client.put(
            policy_url(REQUEST_TYPE_REGULARIZATION), json={"sla_minutes": 60, "enabled": True}
        )
        assert response.status_code == 401

    async def test_reading_policies_requires_the_read_permission(
        self, client, org_id: uuid.UUID
    ) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [])
        with as_principal(principal):
            response = await client.get(POLICIES_URL)
        assert response.status_code == 403

    async def test_writing_a_policy_requires_the_write_permission(
        self, client, org_id: uuid.UUID
    ) -> None:
        principal = principal_for(org_id, uuid.uuid4(), [])
        with as_principal(principal):
            response = await client.put(
                policy_url(REQUEST_TYPE_REGULARIZATION), json={"sla_minutes": 60, "enabled": True}
            )
        assert response.status_code == 403

    async def test_holding_decide_alone_cannot_write_a_policy(
        self, client, org_id: uuid.UUID
    ) -> None:
        """Deciding requests and administering the SLA policy are different authorities — a plain
        member's default grants must not reach this endpoint."""
        principal = principal_for(org_id, uuid.uuid4(), [DECIDE_OWN, READ_OWN])
        with as_principal(principal):
            response = await client.put(
                policy_url(REQUEST_TYPE_REGULARIZATION), json={"sla_minutes": 60, "enabled": True}
            )
        assert response.status_code == 403

    async def test_a_below_org_grant_is_refused_the_write_floor(
        self, client, org_id: uuid.UUID
    ) -> None:
        """`policy.write` is floored at ORG (ADR-0006 §11: "Guarded minimum=Scope.ORG") — a
        below-ORG grant (however it was created) must not reach the write path."""
        principal = principal_for(org_id, uuid.uuid4(), ["core.approval.policy.write:TEAM"])
        with as_principal(principal):
            response = await client.put(
                policy_url(REQUEST_TYPE_REGULARIZATION), json={"sla_minutes": 60, "enabled": True}
            )
        assert response.status_code == 403

    async def test_reading_policies_succeeds_at_the_narrowest_grantable_scope(
        self, client, org_id: uuid.UUID
    ) -> None:
        """The one read endpoint whose scope is never consulted (ADR-0006 §11) — `OWN` sees
        exactly what `ORG` would."""
        principal = principal_for(org_id, uuid.uuid4(), [READ_OWN])
        with as_principal(principal):
            response = await client.get(POLICIES_URL)
        assert response.status_code == 200


class TestPolicyCrossTenantIsolation:
    async def test_a_policy_written_in_one_organization_is_invisible_in_another(
        self,
        client,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        async with admin_session.begin():
            admin_session.add(
                ApprovalPolicy(
                    organization_id=other_org,
                    request_type=REQUEST_TYPE_REGULARIZATION,
                    sla_minutes=999,
                    enabled=False,
                )
            )

        reader = principal_for(org_id, uuid.uuid4(), [READ_OWN])
        with as_principal(reader):
            response = await client.get(POLICIES_URL)
        assert response.status_code == 200
        # `org_id`'s own read sees the honest default, not `other_org`'s written row.
        assert response.json() == [
            {"request_type": REQUEST_TYPE_REGULARIZATION, "sla_minutes": 1440, "enabled": True},
            {"request_type": REQUEST_TYPE_LEAVE, "sla_minutes": 1440, "enabled": True},
        ]

    async def test_writing_in_one_organization_does_not_touch_another(
        self,
        client,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        writer = principal_for(org_id, uuid.uuid4(), [WRITE_ORG])
        with as_principal(writer):
            response = await client.put(
                policy_url(REQUEST_TYPE_REGULARIZATION), json={"sla_minutes": 30, "enabled": False}
            )
        assert response.status_code == 200

        async with admin_session.begin():
            other_rows = (
                (
                    await admin_session.execute(
                        select(ApprovalPolicy).where(
                            ApprovalPolicy.organization_id == other_org,
                            ApprovalPolicy.request_type == REQUEST_TYPE_REGULARIZATION,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert other_rows == []
