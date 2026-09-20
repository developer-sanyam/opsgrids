"""The self-service regularisation request (ADR-0006 §11) — the attendance-side half of the
approval engine's slice 2.

Same two-halves shape `test_authz.py` uses for the rest of this service: object-level checks
(an employee may raise a request against their own record and not another's) and business-rule
checks (no second pending request, no illegal requested transition) are asserted directly against
`services.py`, and the endpoint's authorization is asserted over HTTP.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app import selectors, services
from app.main import app
from app.models import OutboxEvent
from app.schemas import RegularizationRequestIn
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.errors import BusinessRuleProblem, ConflictProblem
from tests.helpers import ctx_for as _ctx_for
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def statements(session: AsyncSession) -> AsyncIterator[list[str]]:
    """The SQL text of every statement the test's session issues, from registration on — the
    same `before_cursor_execute` shape `auth`'s `test_tenancy.py` uses to prove a chokepoint
    ordering rather than asserting on it indirectly."""
    seen: list[str] = []

    def _record(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        seen.append(statement)

    assert isinstance(session.bind, AsyncEngine)
    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", _record)
    yield seen
    event.remove(engine, "before_cursor_execute", _record)


def ctx_for(org_id, subject_ref, permission, scope, *, locations=None) -> ScopeContext:
    return _ctx_for(org_id, subject_ref, permission, scope, locations=locations)


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


# --------------------------------------------------------------------- request-time rules


class TestRequestRegularization:
    async def test_an_employee_may_request_a_correction_on_their_own_record(
        self, session, org_id, records
    ) -> None:
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.request", Scope.OWN)
        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            assert record is not None
            reg = await services.request_regularization(
                session,
                ctx,
                record,
                RegularizationRequestIn(requested_status="half_day", reason="left early"),
            )
        assert reg.status == "pending"
        assert reg.employee_id == records["alice"]
        assert reg.attendance_id == record.id
        assert reg.requested_status == "half_day"
        assert reg.approval_request_id is None
        assert reg.decided_at is None

    async def test_a_second_pending_request_for_the_same_record_is_refused(
        self, session, org_id, records
    ) -> None:
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.request", Scope.OWN)
        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            assert record is not None
            await services.request_regularization(
                session,
                ctx,
                record,
                RegularizationRequestIn(requested_status="half_day", reason="left early"),
            )
        with pytest.raises(ConflictProblem, match="already pending"):
            async with session.begin():
                record = await selectors.get_attendance(session, org_id, records["alice_record"])
                assert record is not None
                await services.request_regularization(
                    session,
                    ctx,
                    record,
                    RegularizationRequestIn(requested_status="absent", reason="mistake"),
                )

    async def test_a_new_request_is_allowed_once_the_first_is_no_longer_pending(
        self, session, org_id, records
    ) -> None:
        """The partial unique index's whole point: `pending` blocks a second request, a decided
        one does not."""
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.request", Scope.OWN)
        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            assert record is not None
            first = await services.request_regularization(
                session,
                ctx,
                record,
                RegularizationRequestIn(requested_status="half_day", reason="left early"),
            )
            first.status = "rejected"
            first.decided_at = record.created_at

        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            assert record is not None
            second = await services.request_regularization(
                session,
                ctx,
                record,
                RegularizationRequestIn(requested_status="absent", reason="second attempt"),
            )
        assert second.id != first.id
        assert second.status == "pending"

    async def test_a_soft_deleted_pending_request_does_not_block_a_new_one(
        self, session, org_id, records
    ) -> None:
        """`uq_regularization_requests_pending_per_attendance` carries the same `deleted_at is
        null` predicate `selectors.get_pending_regularization` filters on. Before that predicate
        was added to the index, a soft-deleted pending row was invisible to the friendly
        `ConflictProblem` check but still collided on insert — turning it into a raw `23505` the
        caller never expects. With the index and the query agreeing, a soft-deleted pending
        request behaves like every other soft-deleted row in this service: gone."""
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.request", Scope.OWN)
        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            assert record is not None
            first = await services.request_regularization(
                session,
                ctx,
                record,
                RegularizationRequestIn(requested_status="half_day", reason="left early"),
            )
            # Soft-deleted while still `pending` — the state the mismatch used to mishandle.
            first.deleted_at = record.created_at

        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            assert record is not None
            second = await services.request_regularization(
                session,
                ctx,
                record,
                RegularizationRequestIn(requested_status="absent", reason="second attempt"),
            )
        assert second.id != first.id
        assert second.status == "pending"

    async def test_an_illegal_requested_transition_is_refused_at_request_time(
        self, session, org_id, records
    ) -> None:
        """`regularized` is terminal — the state machine is checked here, not only when a
        decision eventually lands (ADR-0006 §11)."""
        admin_ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.write", Scope.ORG)
        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            assert record is not None
            await services.regularize_attendance(
                session, admin_ctx, record, "regularized", reason="signed off"
            )

        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.request", Scope.OWN)
        with pytest.raises(BusinessRuleProblem, match="cannot move from 'regularized'"):
            async with session.begin():
                record = await selectors.get_attendance(session, org_id, records["alice_record"])
                assert record is not None
                await services.request_regularization(
                    session,
                    ctx,
                    record,
                    RegularizationRequestIn(requested_status="present", reason="undo"),
                )

    async def test_the_requested_event_carries_exactly_the_fields_approval_expects(
        self, session, org_id, records
    ) -> None:
        """ADR-0006 §12's payload mapping: `regularization_request_id`, `attendance_id`,
        `employee_id`, `requested_status`, `current_status`, `reason`, `date` — plus
        `organization_id`, the field every consumer needs to bind its transaction."""
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.request", Scope.OWN)
        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            assert record is not None
            reg = await services.request_regularization(
                session,
                ctx,
                record,
                RegularizationRequestIn(requested_status="half_day", reason="left early"),
            )
            work_date = record.work_date

        row = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "hrms.time.regularization.requested",
                    OutboxEvent.aggregate_id == reg.id,
                )
            )
        ).scalar_one()
        assert row.payload == {
            "organization_id": str(org_id),
            "regularization_request_id": str(reg.id),
            "attendance_id": str(records["alice_record"]),
            "employee_id": str(records["alice"]),
            "requested_status": "half_day",
            "current_status": "present",
            "reason": "left early",
            "date": work_date.isoformat(),
        }
        assert row.published_at is None


# --------------------------------------------------------------------- endpoint authorization


class TestEndpointAuthorization:
    async def test_an_employee_may_request_a_correction_over_http(
        self, client, org_id, records
    ) -> None:
        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.request:OWN"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/attendance/{records['alice_record']}/regularization-requests",
                json={"requested_status": "half_day", "reason": "left early"},
            )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending"
        assert body["employee_id"] == str(records["alice"])
        assert body["attendance_id"] == str(records["alice_record"])
        # Composed from the punch's own location at read time, not stored on the row (the open
        # contract question this slice settles — see `RegularizationRequestOut.location_code`).
        assert body["location_code"] == "MUM"

    async def test_an_employee_cannot_request_a_correction_on_someone_elses_record(
        self, client, org_id, records
    ) -> None:
        """The named vulnerability again: the permission's OWN ceiling is not itself an
        object-level check, so the route must refuse this by primary key."""
        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.request:OWN"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/attendance/{records['bob_record']}/regularization-requests",
                json={"requested_status": "half_day", "reason": "not mine"},
            )
        assert response.status_code == 403

    async def test_an_org_scoped_grant_still_cannot_request_for_someone_else(
        self, client, org_id, records
    ) -> None:
        """The catalogue's `max_scope` for this permission is OWN, so the platform never installs
        an ORG grant for it — but the route must not rely on that alone. A single over-scoped
        grant string reaching here (a bad install, a forged header) must still be refused: the
        object-level check compares identities, not `assert_can_touch`'s `Scope.ORG` early
        return, which exists for permissions that have a genuine organization-wide reading."""
        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.request:ORG"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/attendance/{records['bob_record']}/regularization-requests",
                json={"requested_status": "half_day", "reason": "not mine"},
            )
        assert response.status_code == 403

    async def test_unauthenticated_is_rejected(self, client, org_id, records) -> None:
        response = await client.post(
            f"/api/v1/attendance/{records['alice_record']}/regularization-requests",
            json={"requested_status": "half_day", "reason": "left early"},
        )
        assert response.status_code == 401

    async def test_missing_the_permission_is_forbidden(self, client, org_id, records) -> None:
        """Holding `attendance.write:OWN` — enough to punch — is not enough to request a
        correction; that is a distinct permission (ADR-0006 §11)."""
        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.write:OWN"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/attendance/{records['alice_record']}/regularization-requests",
                json={"requested_status": "half_day", "reason": "left early"},
            )
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, records
    ) -> None:
        principal = principal_for(
            org_id, records["alice"], ["hrms.time.attendance.request:OWN"], entitlements=[]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/attendance/{records['alice_record']}/regularization-requests",
                json={"requested_status": "half_day", "reason": "left early"},
            )
        assert response.status_code == 402

    async def test_a_blank_reason_fails_validation(self, client, org_id, records) -> None:
        """Validated at the boundary: an empty reason never reaches the domain layer."""
        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.request:OWN"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/attendance/{records['alice_record']}/regularization-requests",
                json={"requested_status": "half_day", "reason": ""},
            )
        assert response.status_code == 422

    async def test_an_unknown_record_is_not_found(self, client, org_id, records) -> None:
        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.request:OWN"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/attendance/{uuid.uuid4()}/regularization-requests",
                json={"requested_status": "half_day", "reason": "left early"},
            )
        assert response.status_code == 404


# --------------------------------------------------------------------- the read model


class TestReadModelExposesCorrectionState:
    async def test_a_pending_request_is_visible_on_the_attendance_record(
        self, client, session, org_id, records
    ) -> None:
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.request", Scope.OWN)
        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            assert record is not None
            await services.request_regularization(
                session,
                ctx,
                record,
                RegularizationRequestIn(requested_status="half_day", reason="left early"),
            )

        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.read:OWN"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/attendance/{records['alice_record']}")
        assert response.status_code == 200
        body = response.json()
        assert body["regularization"]["status"] == "pending"
        assert body["regularization"]["requested_status"] == "half_day"
        assert set(body["allowed_transitions"]) == {"half_day", "absent", "regularized"}

    async def test_a_record_with_no_request_shows_no_correction_state(
        self, client, org_id, records
    ) -> None:
        principal = principal_for(org_id, records["bob"], ["hrms.time.attendance.read:OWN"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/attendance/{records['bob_record']}")
        assert response.status_code == 200
        assert response.json()["regularization"] is None

    async def test_the_list_endpoint_carries_correction_state_without_an_n_plus_one(
        self, client, session, org_id, records, statements
    ) -> None:
        """Both records are on one page; a loop with a query in it would issue one
        `regularization_requests` query per row (two, for this fixture's two records) — this
        counts statements with a `before_cursor_execute` hook to prove
        `selectors.get_latest_regularizations` batches them into exactly one, rather than only
        checking that both rows render correctly, which would pass even for a broken, looping
        implementation."""
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.request", Scope.OWN)
        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            assert record is not None
            await services.request_regularization(
                session,
                ctx,
                record,
                RegularizationRequestIn(requested_status="absent", reason="forgot to punch"),
            )

        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.read:ORG"])
        statements.clear()
        with as_principal(principal):
            response = await client.get("/api/v1/attendance")
        assert response.status_code == 200
        by_employee = {item["employee_id"]: item for item in response.json()["items"]}
        assert by_employee[str(records["alice"])]["regularization"]["status"] == "pending"
        assert by_employee[str(records["bob"])]["regularization"] is None

        regularization_queries = [s for s in statements if "regularization_requests" in s]
        assert len(regularization_queries) == 1, (
            "expected exactly one batched query against regularization_requests for the whole "
            f"page, got {len(regularization_queries)}: {regularization_queries}"
        )

    async def test_approver_and_decider_names_surface_on_the_read_model(
        self, client, session, org_id, records
    ) -> None:
        """`.opened` and `.decided` are consumer-applied, not part of this HTTP round trip — this
        proves the two stored names actually reach `GET /attendance/{id}`'s response, the shape
        the "Waiting on {Name}" / decided-with-comment screens render from. The consumer opens
        its own `session_scope`, which needs the process engine initialised — the app lifespan
        does this in production; here it is set up and torn down around the two consumer calls,
        the same shape `test_consumers.py`'s autouse `_engine` fixture provides for that module."""
        from app.config import settings
        from app.consumers import on_approval_request_decided, on_approval_request_opened
        from platform_core.db import dispose_engine, init_engine
        from tests.helpers import approval_decided_envelope, approval_opened_envelope

        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.request", Scope.OWN)
        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            assert record is not None
            reg = await services.request_regularization(
                session,
                ctx,
                record,
                RegularizationRequestIn(requested_status="half_day", reason="left early"),
            )
            reg_id = reg.id

        init_engine(settings())
        try:
            opened = approval_opened_envelope(
                organization_id=org_id,
                source_ref=reg_id,
                approver_employee_id=uuid.uuid4(),
                approver_name="Priya Sharma",
            )
            await on_approval_request_opened(opened)

            decided = approval_decided_envelope(
                organization_id=org_id,
                source_ref=reg_id,
                outcome="approved",
                decided_by_name="Rahul Verma",
            )
            await on_approval_request_decided(decided)
        finally:
            await dispose_engine()
        session.expire_all()

        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.read:OWN"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/attendance/{records['alice_record']}")
        assert response.status_code == 200
        body = response.json()["regularization"]
        assert body["status"] == "approved"
        assert body["approver_name"] == "Priya Sharma"
        assert body["decided_by_name"] == "Rahul Verma"
