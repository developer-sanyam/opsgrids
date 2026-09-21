"""Authorization, scope, validation and events — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth more
than proving one succeeds, because the success path is the one exercised by hand anyway.

The scope tests come in two halves on purpose. `ctx.apply` narrows a *list*; `ctx.assert_can_
touch` guards a single *object*. Filtering the list and forgetting the detail is the named
vulnerability, so both are checked — a manager who can list only their reports must also be
refused the outsider by primary key.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.main import app
from app.models import AuditLog, Employee, OutboxEvent
from app.schemas import EmployeeIn, EmployeePatch
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    NotFoundProblem,
)
from tests.helpers import ctx_for as _ctx_for
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


def ctx_for(org_id, employee, permission, scope, *, locations=None) -> ScopeContext:
    return _ctx_for(org_id, employee, permission, scope, locations=locations)


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


# --------------------------------------------------------------------- list scope


class TestListScope:
    async def _numbers(self, session: AsyncSession, ctx: ScopeContext) -> set[str]:
        rows = (await session.execute(selectors.employees_stmt(ctx))).scalars().all()
        return {e.employee_no for e in rows}

    async def test_org_scope_sees_everyone(self, session, org_id, people) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.read", Scope.ORG)
        assert len(await self._numbers(session, ctx)) == 4

    async def test_team_scope_sees_only_direct_reports(self, session, org_id, people) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.read", Scope.TEAM)
        assert await self._numbers(session, ctx) == {"EMP0003"}

    async def test_own_scope_sees_only_yourself(self, session, org_id, people) -> None:
        ctx = ctx_for(org_id, people["report"], "hrms.people.employee.read", Scope.OWN)
        assert await self._numbers(session, ctx) == {"EMP0003"}

    async def test_location_scope_sees_only_its_own_sites(self, session, org_id, people) -> None:
        ctx = ctx_for(
            org_id,
            people["manager"],
            "hrms.people.employee.read",
            Scope.LOCATION,
            locations=["MUM"],
        )
        assert await self._numbers(session, ctx) == {"EMP0003"}

    async def test_location_scope_with_no_locations_sees_nothing(
        self, session, org_id, people
    ) -> None:
        """An empty location list must mean none, never all."""
        ctx = ctx_for(
            org_id, people["manager"], "hrms.people.employee.read", Scope.LOCATION, locations=[]
        )
        assert await self._numbers(session, ctx) == set()

    async def test_a_search_cannot_widen_scope(self, session, org_id, people) -> None:
        """Filters narrow within scope. They never escape it."""
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.read", Scope.TEAM)
        stmt = selectors.employees_stmt(ctx, q="Otto")
        assert (await session.execute(stmt)).all() == []


# --------------------------------------------------------------------- object-level


class TestObjectLevelChecks:
    async def test_scope_does_not_leak_through_the_detail_route(
        self, client, org_id, people
    ) -> None:
        """The named vulnerability: a correct list filter beside an unguarded detail view.

        The manager holds TEAM scope. The outsider is refused by id; the manager's own report
        is not.
        """
        outsider_id = people["outsider"].id
        report_id = people["report"].id
        principal = principal_for(org_id, people["manager"], ["hrms.people.employee.read:TEAM"])
        with as_principal(principal):
            refused = await client.get(f"/api/v1/employees/{outsider_id}")
            allowed = await client.get(f"/api/v1/employees/{report_id}")

        assert refused.status_code == 403
        assert allowed.status_code == 200

    async def test_reports_route_applies_the_object_check(self, client, org_id, people) -> None:
        """A manager cannot list another manager's reports."""
        other_manager_id = people["other_manager"].id
        principal = principal_for(org_id, people["manager"], ["hrms.people.employee.read:TEAM"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/employees/{other_manager_id}/reports")
        assert response.status_code == 403


# --------------------------------------------------------------------- endpoint authz


class TestEndpointAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id, people) -> None:
        """No principal, no override: `get_principal` raises 401 before anything runs."""
        response = await client.get("/api/v1/employees")
        assert response.status_code == 401

    async def test_missing_the_permission_is_forbidden(self, client, org_id, people) -> None:
        """Entitled to HRMS, but holding no employee permission at all."""
        principal = principal_for(org_id, people["manager"], [])
        with as_principal(principal):
            response = await client.get("/api/v1/employees")
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, people
    ) -> None:
        """The gateway gates the product; the service does too. 402, not 403 — the caller's
        permissions are fine, the subscription is not."""
        principal = principal_for(
            org_id, people["manager"], ["hrms.people.employee.read:ORG"], entitlements=[]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/employees")
        assert response.status_code == 402

    async def test_a_reader_cannot_create_an_employee(self, client, org_id, people) -> None:
        """Everyone holds employee.read at OWN; without the ORG write floor that would let any
        employee onboard people. This is the regression guard for the missing floor."""
        principal = principal_for(org_id, people["report"], ["hrms.people.employee.read:OWN"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/employees",
                json={"employee_no": "EMP9999", "full_name": "Intruder"},
            )
        assert response.status_code == 403

    async def test_an_org_writer_may_create_an_employee(self, client, org_id, people) -> None:
        principal = principal_for(org_id, people["manager"], ["hrms.people.employee.write:ORG"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/employees",
                json={"employee_no": "EMP0100", "full_name": "New Hire"},
            )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "probation"
        assert body["employee_no"] == "EMP0100"

    async def test_an_own_reader_sees_only_their_own_record_over_http(
        self, client, org_id, people
    ) -> None:
        principal = principal_for(org_id, people["report"], ["hrms.people.employee.read:OWN"])
        with as_principal(principal):
            response = await client.get("/api/v1/employees")
        assert response.status_code == 200
        numbers = {item["employee_no"] for item in response.json()["items"]}
        assert numbers == {"EMP0003"}


# --------------------------------------------------------------------- validation


class TestValidation:
    async def test_a_duplicate_employee_number_is_refused(self, session, org_id, people) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        with pytest.raises(ConflictProblem, match="already exists"):
            async with session.begin():
                await services.create_employee(
                    session, ctx, EmployeeIn(employee_no="EMP0001", full_name="Clash")
                )

    async def test_an_unknown_reporting_manager_is_refused(self, session, org_id, people) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        with pytest.raises(NotFoundProblem, match="reporting manager"):
            async with session.begin():
                await services.create_employee(
                    session,
                    ctx,
                    EmployeeIn(
                        employee_no="EMP0200",
                        full_name="Orphan",
                        reporting_manager_id=uuid.uuid4(),
                    ),
                )

    async def test_a_manager_from_another_org_is_not_a_valid_manager(
        self, session, org_id, people, admin_session
    ) -> None:
        """The manager check is tenant-scoped: an employee id from another organization must
        read as 'not found here', never resolve into a cross-tenant reporting line."""
        other_org = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                Employee.__table__.insert().values(
                    id=(stranger := uuid.uuid4()),
                    organization_id=other_org,
                    employee_no="X0001",
                    full_name="Stranger Boss",
                    employment_type="full_time",
                    work_mode="office",
                    status="active",
                )
            )
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        with pytest.raises(NotFoundProblem):
            async with session.begin():
                await services.create_employee(
                    session,
                    ctx,
                    EmployeeIn(
                        employee_no="EMP0300",
                        full_name="Cross Report",
                        reporting_manager_id=stranger,
                    ),
                )

    async def test_somebody_cannot_report_to_themselves(self, session, org_id, people) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        report = people["report"]
        with pytest.raises(BusinessRuleProblem, match="report to themselves"):
            async with session.begin():
                await services.update_employee(
                    session, ctx, report, EmployeePatch(reporting_manager_id=report.id)
                )

    async def test_date_of_exit_cannot_be_corrected_before_the_exit_transition(
        self, session, org_id, people
    ) -> None:
        """`report` starts on probation — correcting an exit date that has not happened yet is
        refused, not silently accepted as a future-dated exit."""
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        report = people["report"]
        with pytest.raises(BusinessRuleProblem, match="only be corrected on an employee"):
            async with session.begin():
                await services.update_employee(
                    session, ctx, report, EmployeePatch(date_of_exit=date(2026, 1, 1))
                )

    async def test_date_of_exit_cannot_precede_date_of_joining(
        self, session, org_id, people
    ) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        report = people["report"]
        async with session.begin():
            await services.update_employee(
                session, ctx, report, EmployeePatch(date_of_joining=date(2024, 1, 1))
            )
            await services.transition_status(session, ctx, report, "exited")
        with pytest.raises(BusinessRuleProblem, match="cannot be before date_of_joining"):
            async with session.begin():
                await services.update_employee(
                    session, ctx, report, EmployeePatch(date_of_exit=date(2023, 12, 31))
                )


# --------------------------------------------------------------------- lifecycle


class TestLifecycle:
    async def test_an_illegal_transition_is_refused(self, session, org_id, people) -> None:
        """probation -> on_notice is not a legal step; the state machine refuses it."""
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        report = people["report"]  # starts on probation
        with pytest.raises(
            BusinessRuleProblem, match="cannot move from 'probation' to 'on_notice'"
        ):
            async with session.begin():
                await services.transition_status(session, ctx, report, "on_notice")

    async def test_a_legal_transition_writes_an_audit_row_and_an_outbox_row(
        self, session, org_id, people
    ) -> None:
        """probation -> active is legal, and the change, its audit row and its event commit
        together."""
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        report = people["report"]
        async with session.begin():
            await services.transition_status(session, ctx, report, "active", reason="Confirmed")

        assert report.status == "active"
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "employee.transitioned",
                    AuditLog.entity_id == report.id,
                )
            )
        ).scalar_one()
        events = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "hrms.people.employee.transitioned",
                    OutboxEvent.aggregate_id == report.id,
                )
            )
        ).scalar_one()
        assert audits == 1
        assert events == 1

    async def test_exiting_stamps_a_date_of_exit(self, session, org_id, people) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        report = people["report"]
        async with session.begin():
            await services.transition_status(session, ctx, report, "exited")
        assert report.status == "exited"
        assert report.date_of_exit is not None

    async def test_date_of_exit_can_be_corrected_once_exited(self, session, org_id, people) -> None:
        """`transition_status`'s own comment calls the auto-stamped date "a correction is a
        later update" — this is that later update, and it must actually stick."""
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        report = people["report"]
        async with session.begin():
            await services.transition_status(session, ctx, report, "exited")
        async with session.begin():
            corrected = await services.update_employee(
                session, ctx, report, EmployeePatch(date_of_exit=date(2026, 3, 15))
            )
        assert corrected.date_of_exit == date(2026, 3, 15)


# --------------------------------------------------------------------- events


class TestEvents:
    async def test_onboarding_stages_an_unpublished_event(self, session, org_id, people) -> None:
        """Nothing publishes inline — that is the whole point of the outbox."""
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "hrms.people.employee.onboarded"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 4  # the four people fixtures
        assert all(r.published_at is None for r in rows)

    async def test_an_update_stages_the_updated_event(self, session, org_id, people) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        async with session.begin():
            await services.update_employee(
                session, ctx, people["report"], EmployeePatch(job_title="Senior Engineer")
            )
        events = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "hrms.people.employee.updated",
                    OutboxEvent.aggregate_id == people["report"].id,
                )
            )
        ).scalar_one()
        assert events == 1
