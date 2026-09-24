"""Authorization, scope, validation, lifecycle and events — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth more
than proving one succeeds, because the success path is the one exercised by hand anyway.

The scope tests come in two halves on purpose. `ctx.apply` narrows a *list*; `ctx.assert_can_
touch` guards a single *object*. Filtering the list and forgetting the detail is the named
vulnerability, so both are checked — a caller who can list only their own record must also be
refused another person's by primary key. TEAM, which reads a projection of employee's reporting
line, has its own suite: `test_team_scope.py`.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import time

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.main import app
from app.models import AuditLog, OutboxEvent, Shift
from app.schemas import CheckInIn, CheckOutIn
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    NotFoundProblem,
)
from tests.helpers import ctx_for as _ctx_for
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


def ctx_for(org_id, subject_ref, permission, scope, *, locations=None) -> ScopeContext:
    return _ctx_for(org_id, subject_ref, permission, scope, locations=locations)


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


# --------------------------------------------------------------------- list scope


class TestListScope:
    async def _employees(self, session: AsyncSession, ctx: ScopeContext) -> set[uuid.UUID]:
        rows = (await session.execute(selectors.attendance_stmt(ctx))).scalars().all()
        return {r.employee_id for r in rows}

    async def test_org_scope_sees_everyone(self, session, org_id, records) -> None:
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.read", Scope.ORG)
        assert await self._employees(session, ctx) == {records["alice"], records["bob"]}

    async def test_own_scope_sees_only_yourself(self, session, org_id, records) -> None:
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.read", Scope.OWN)
        assert await self._employees(session, ctx) == {records["alice"]}

    async def test_location_scope_sees_only_its_own_sites(self, session, org_id, records) -> None:
        ctx = ctx_for(
            org_id,
            records["alice"],
            "hrms.time.attendance.read",
            Scope.LOCATION,
            locations=["MUM"],
        )
        assert await self._employees(session, ctx) == {records["alice"]}

    async def test_location_scope_with_no_locations_sees_nothing(
        self, session, org_id, records
    ) -> None:
        """An empty location list must mean none, never all."""
        ctx = ctx_for(
            org_id, records["alice"], "hrms.time.attendance.read", Scope.LOCATION, locations=[]
        )
        assert await self._employees(session, ctx) == set()

    async def test_team_scope_with_no_reporting_line_known_sees_nobody(
        self, session, org_id, records
    ) -> None:
        """TEAM reads the reporting-line projection (`tests/test_team_scope.py` covers it filled).
        Here it is empty — a tenant not yet backfilled — and the answer is nobody: neither an
        error nor, worse, everyone."""
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.read", Scope.TEAM)
        assert await self._employees(session, ctx) == set()


# --------------------------------------------------------------------- object-level


class TestObjectLevelChecks:
    async def test_scope_does_not_leak_through_the_detail_route(
        self, client, org_id, records
    ) -> None:
        """The named vulnerability: a correct list filter beside an unguarded detail view.

        Alice holds OWN read. Her own record is reachable by id; Bob's is refused.
        """
        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.read:OWN"])
        with as_principal(principal):
            allowed = await client.get(f"/api/v1/attendance/{records['alice_record']}")
            refused = await client.get(f"/api/v1/attendance/{records['bob_record']}")

        assert allowed.status_code == 200
        assert refused.status_code == 403

    async def test_team_scoped_detail_is_refused_when_the_owner_is_in_no_team(
        self, client, org_id, records
    ) -> None:
        """The object-level half: with no reporting line known for the record's owner, a record the
        caller could name by id is out of reach under TEAM scope."""
        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.read:TEAM"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/attendance/{records['alice_record']}")
        assert response.status_code == 403


# --------------------------------------------------------------------- endpoint authz


class TestEndpointAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id, records) -> None:
        """No principal, no override: `get_principal` raises 401 before anything runs."""
        response = await client.get("/api/v1/attendance")
        assert response.status_code == 401

    async def test_missing_the_permission_is_forbidden(self, client, org_id, records) -> None:
        """Entitled to HRMS, but holding no attendance permission at all."""
        principal = principal_for(org_id, records["alice"], [])
        with as_principal(principal):
            response = await client.get("/api/v1/attendance")
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, records
    ) -> None:
        """The gateway gates the product; the service does too. 402, not 403 — the caller's
        permissions are fine, the subscription is not."""
        principal = principal_for(
            org_id, records["alice"], ["hrms.time.attendance.read:ORG"], entitlements=[]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/attendance")
        assert response.status_code == 402

    async def test_an_own_writer_may_punch_in(self, client, org_id, records) -> None:
        """Punching is self-service — OWN write is enough, and the record is attributed to the
        caller's own subject reference."""
        newcomer = uuid.uuid4()
        principal = principal_for(org_id, newcomer, ["hrms.time.attendance.write:OWN"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/attendance/check-in", json={"work_mode": "office", "source": "web"}
            )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "present"
        assert body["employee_id"] == str(newcomer)

    async def test_an_own_writer_cannot_regularize(self, client, org_id, records) -> None:
        """Regularising is an administrative correction: it floors at ORG. An employee holding
        attendance.write at OWN must not be able to rewrite a status. Regression guard."""
        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.write:OWN"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/attendance/{records['alice_record']}/regularize",
                json={"to": "regularized", "reason": "worked from client site"},
            )
        assert response.status_code == 403

    async def test_an_org_writer_may_regularize(self, client, org_id, records) -> None:
        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/attendance/{records['alice_record']}/regularize",
                json={"to": "regularized", "reason": "approved correction"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "regularized"

    async def test_an_own_reader_cannot_define_a_shift(self, client, org_id, records) -> None:
        """Shifts are org definitions: shift.write floors at ORG."""
        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.write:OWN"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/shifts",
                json={"code": "GEN", "name": "General", "start_time": "09:00", "end_time": "18:00"},
            )
        assert response.status_code == 403

    async def test_an_own_reader_cannot_define_a_geofence(self, client, org_id, records) -> None:
        """Geo-fences are org location definitions, guarded by the same shift.write floor."""
        principal = principal_for(org_id, records["alice"], ["hrms.time.shift.read:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/geofences",
                json={
                    "location_code": "SITE1",
                    "name": "Site 1",
                    "center_lat": "19.07",
                    "center_lng": "72.87",
                    "radius_m": 100,
                },
            )
        assert response.status_code == 403

    async def test_an_org_admin_may_define_a_shift(self, client, org_id, records) -> None:
        principal = principal_for(org_id, records["alice"], ["hrms.time.shift.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/shifts",
                json={
                    "code": "NIGHT",
                    "name": "Night",
                    "start_time": "22:00",
                    "end_time": "06:00",
                    "is_night": True,
                },
            )
        assert response.status_code == 201
        assert response.json()["code"] == "NIGHT"

    async def test_an_own_reader_sees_only_their_own_record_over_http(
        self, client, org_id, records
    ) -> None:
        principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.read:OWN"])
        with as_principal(principal):
            response = await client.get("/api/v1/attendance")
        assert response.status_code == 200
        employees = {item["employee_id"] for item in response.json()["items"]}
        assert employees == {str(records["alice"])}


# --------------------------------------------------------------------- retiring shifts/geofences


class TestDeleteShiftAndGeofence:
    async def test_an_org_admin_may_retire_a_shift(self, client, org_id, records) -> None:
        principal = principal_for(
            org_id,
            records["alice"],
            ["hrms.time.shift.write:ORG", "hrms.time.shift.read:ORG"],
        )
        with as_principal(principal):
            created = await client.post(
                "/api/v1/shifts",
                json={
                    "code": "GEN",
                    "name": "General",
                    "start_time": "09:00",
                    "end_time": "18:00",
                },
            )
            assert created.status_code == 201
            shift_id = created.json()["id"]

            deleted = await client.delete(f"/api/v1/shifts/{shift_id}")
            assert deleted.status_code == 204
            assert deleted.content == b""

            listed = await client.get("/api/v1/shifts")
            assert shift_id not in {item["id"] for item in listed.json()["items"]}

            # `uq_shifts_org_code` is a partial index on `deleted_at is null` (migration
            # `f4c1a9b3d2e7`) — the retired code is free to be reused, not burned forever.
            recreated = await client.post(
                "/api/v1/shifts",
                json={
                    "code": "GEN",
                    "name": "General (v2)",
                    "start_time": "10:00",
                    "end_time": "19:00",
                },
            )
        assert recreated.status_code == 201
        assert recreated.json()["id"] != shift_id

    async def test_deleting_an_already_deleted_shift_is_not_found(
        self, client, org_id, records
    ) -> None:
        principal = principal_for(org_id, records["alice"], ["hrms.time.shift.write:ORG"])
        with as_principal(principal):
            created = await client.post(
                "/api/v1/shifts",
                json={
                    "code": "DUP",
                    "name": "Duplicate check",
                    "start_time": "09:00",
                    "end_time": "18:00",
                },
            )
            shift_id = created.json()["id"]
            first = await client.delete(f"/api/v1/shifts/{shift_id}")
            second = await client.delete(f"/api/v1/shifts/{shift_id}")
        assert first.status_code == 204
        assert second.status_code == 404

    async def test_an_own_writer_cannot_retire_a_shift(self, client, org_id, records) -> None:
        principal = principal_for(org_id, records["alice"], ["hrms.time.shift.write:ORG"])
        with as_principal(principal):
            created = await client.post(
                "/api/v1/shifts",
                json={
                    "code": "PROTECTED",
                    "name": "Protected",
                    "start_time": "09:00",
                    "end_time": "18:00",
                },
            )
        shift_id = created.json()["id"]

        weak_principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.write:OWN"])
        with as_principal(weak_principal):
            response = await client.delete(f"/api/v1/shifts/{shift_id}")
        assert response.status_code == 403

    async def test_deleting_a_shift_in_another_organization_is_not_found(
        self, client, org_id, admin_session
    ) -> None:
        """Cross-tenant isolation: naming another org's shift by id gets 404, never 403 — the
        row must not be confirmed to exist anywhere else, and nothing about it changes."""
        other_org = uuid.uuid4()
        other_shift = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                Shift.__table__.insert().values(
                    id=other_shift,
                    organization_id=other_org,
                    code="OTHERORG",
                    name="Other org's shift",
                    start_time=time(9, 0),
                    end_time=time(18, 0),
                    grace_minutes=0,
                    is_night=False,
                )
            )

        principal = principal_for(org_id, uuid.uuid4(), ["hrms.time.shift.write:ORG"])
        with as_principal(principal):
            response = await client.delete(f"/api/v1/shifts/{other_shift}")
        assert response.status_code == 404

        async with admin_session.begin():
            row = (
                await admin_session.execute(select(Shift).where(Shift.id == other_shift))
            ).scalar_one()
            assert row.deleted_at is None

    async def test_an_org_admin_may_retire_a_geofence(self, client, org_id, records) -> None:
        principal = principal_for(
            org_id,
            records["alice"],
            ["hrms.time.shift.write:ORG", "hrms.time.shift.read:ORG"],
        )
        with as_principal(principal):
            created = await client.post(
                "/api/v1/geofences",
                json={
                    "location_code": "SITE9",
                    "name": "Site 9",
                    "center_lat": "19.07",
                    "center_lng": "72.87",
                    "radius_m": 100,
                },
            )
            assert created.status_code == 201
            geofence_id = created.json()["id"]

            deleted = await client.delete(f"/api/v1/geofences/{geofence_id}")
            assert deleted.status_code == 204

            listed = await client.get("/api/v1/geofences")
            assert geofence_id not in {item["id"] for item in listed.json()["items"]}

            # `uq_geofences_org_location` is a partial index on `deleted_at is null`
            # (migration `f4c1a9b3d2e7`) — the retired location code is free to be reused.
            recreated = await client.post(
                "/api/v1/geofences",
                json={
                    "location_code": "SITE9",
                    "name": "Site 9 (v2)",
                    "center_lat": "19.08",
                    "center_lng": "72.88",
                    "radius_m": 150,
                },
            )
        assert recreated.status_code == 201
        assert recreated.json()["id"] != geofence_id

    async def test_an_own_writer_cannot_retire_a_geofence(self, client, org_id, records) -> None:
        principal = principal_for(org_id, records["alice"], ["hrms.time.shift.write:ORG"])
        with as_principal(principal):
            created = await client.post(
                "/api/v1/geofences",
                json={
                    "location_code": "SITE10",
                    "name": "Site 10",
                    "center_lat": "19.07",
                    "center_lng": "72.87",
                    "radius_m": 100,
                },
            )
        geofence_id = created.json()["id"]

        weak_principal = principal_for(org_id, records["alice"], ["hrms.time.attendance.write:OWN"])
        with as_principal(weak_principal):
            response = await client.delete(f"/api/v1/geofences/{geofence_id}")
        assert response.status_code == 403


# --------------------------------------------------------------------- validation


class TestValidation:
    async def test_a_second_check_in_the_same_day_is_a_conflict(
        self, session, org_id, records
    ) -> None:
        """One record per person per day: a double punch is surfaced, not silently re-opened."""
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.write", Scope.OWN)
        with pytest.raises(ConflictProblem, match="already open"):
            async with session.begin():
                await services.check_in(session, ctx, CheckInIn(work_mode="office", source="web"))

    async def test_checking_out_with_no_open_record_is_not_found(
        self, session, org_id, records
    ) -> None:
        stranger = uuid.uuid4()
        ctx = ctx_for(org_id, stranger, "hrms.time.attendance.write", Scope.OWN)
        with pytest.raises(NotFoundProblem, match="No open attendance"):
            async with session.begin():
                await services.check_out(session, ctx, CheckOutIn())

    async def test_a_second_check_out_is_refused(self, session, org_id, records) -> None:
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.write", Scope.OWN)
        async with session.begin():
            await services.check_out(session, ctx, CheckOutIn())
        with pytest.raises(BusinessRuleProblem, match="already closed"):
            async with session.begin():
                await services.check_out(session, ctx, CheckOutIn())

    async def test_a_punch_needs_a_subject(self, session, org_id, records) -> None:
        """An authenticated caller who is not an employee has no own attendance to punch."""
        ctx = ctx_for(org_id, None, "hrms.time.attendance.write", Scope.OWN)
        with pytest.raises(BusinessRuleProblem, match="Only an employee"):
            async with session.begin():
                await services.check_in(session, ctx, CheckInIn(work_mode="office", source="web"))

    async def test_an_unknown_shift_is_refused(self, session, org_id, records) -> None:
        newcomer = uuid.uuid4()
        ctx = ctx_for(org_id, newcomer, "hrms.time.attendance.write", Scope.OWN)
        with pytest.raises(NotFoundProblem, match="shift is not defined"):
            async with session.begin():
                await services.check_in(
                    session,
                    ctx,
                    CheckInIn(work_mode="office", source="web", shift_id=uuid.uuid4()),
                )


# --------------------------------------------------------------------- lifecycle


class TestLifecycle:
    async def test_an_illegal_status_correction_is_refused(self, session, org_id, records) -> None:
        """`regularized` is terminal: a correction of it is refused by the state machine."""
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.write", Scope.ORG)
        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            assert record is not None
            await services.regularize_attendance(
                session, ctx, record, "regularized", reason="signed off"
            )
        with pytest.raises(BusinessRuleProblem, match="cannot move from 'regularized'"):
            async with session.begin():
                record = await selectors.get_attendance(session, org_id, records["alice_record"])
                assert record is not None
                await services.regularize_attendance(session, ctx, record, "present", reason="undo")

    async def test_regularizing_writes_an_audit_row_and_an_outbox_row(
        self, session, org_id, records
    ) -> None:
        """The change, its audit row and its event commit together."""
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.write", Scope.ORG)
        async with session.begin():
            record = await selectors.get_attendance(session, org_id, records["alice_record"])
            assert record is not None
            await services.regularize_attendance(
                session, ctx, record, "half_day", reason="left early"
            )

        assert record.status == "half_day"
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "attendance.regularized",
                    AuditLog.entity_id == record.id,
                )
            )
        ).scalar_one()
        events = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "hrms.time.attendance.regularized",
                    OutboxEvent.aggregate_id == record.id,
                )
            )
        ).scalar_one()
        assert audits == 1
        assert events == 1


# --------------------------------------------------------------------- events


class TestEvents:
    async def test_checking_in_stages_an_unpublished_event(self, session, org_id, records) -> None:
        """Nothing publishes inline — that is the whole point of the outbox."""
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "hrms.time.attendance.checked_in"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2  # the two people the fixture punched in
        assert all(r.published_at is None for r in rows)

    async def test_checking_out_stages_the_checked_out_event(
        self, session, org_id, records
    ) -> None:
        ctx = ctx_for(org_id, records["alice"], "hrms.time.attendance.write", Scope.OWN)
        async with session.begin():
            await services.check_out(session, ctx, CheckOutIn())
        events = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "hrms.time.attendance.checked_out",
                    OutboxEvent.aggregate_id == records["alice_record"],
                )
            )
        ).scalar_one()
        assert events == 1

    async def test_an_audit_row_accompanies_a_check_in(self, session, org_id, records) -> None:
        count = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "attendance.checked_in")
            )
        ).scalar_one()
        assert count == 2  # one per punched-in person
