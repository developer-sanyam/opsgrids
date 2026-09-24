"""The employee half of the approval engine (ADR-0006 §2): the version trigger, the cycle
check, full-state hierarchy events, and the internal backfill endpoint.

Written against real PostgreSQL, like the rest of the suite — the version trigger and the
advisory-lock cycle check are database behaviour, not application logic, and the concurrency
tests specifically need two independent connections racing each other, which only a real
server can arbitrate.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import selectors, services
from app.config import settings
from app.models import SCHEMA, AuditLog, Employee, OutboxEvent
from app.schemas import EmployeePatch
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from platform_core.errors import BusinessRuleProblem
from platform_core.internal_auth import mint_service_token
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _org_session(org: uuid.UUID) -> tuple[AsyncSession, AsyncEngine]:
    """A second, independent connection bound to `org` — for the concurrency tests, which need
    two real sessions racing each other rather than the one shared fixture session."""
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    @event.listens_for(session.sync_session, "after_begin")
    def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

    return session, engine


def internal_headers(
    audience: str = "employee",
    *,
    caller: str = "approval",
    org: uuid.UUID | None = None,
) -> dict[str, str]:
    """A service token shaped like the real caller's. `services/approval/app/employee_client.py`
    mints with `caller` left at its default, which is `settings.service_name` — `"approval"` for
    that service — so `caller` defaults to the same value here rather than the generic `"tests"`
    the internal endpoint's `iss` allowlist (`app/api/internal.py::ALLOWED_INTERNAL_ISSUERS`)
    would now refuse.

    `org`, when given, binds `ORG_CLAIM` the way `mint_service_token`'s `claims=` argument
    already supports. Left unset by default because approval's real client does not send it yet
    (`app/api/internal.py`'s module docstring explains why) — most callers here match today's
    production shape.
    """
    claims = {"org": str(org)} if org is not None else None
    return {
        "X-Internal-Token": mint_service_token(
            settings(), audience=audience, caller=caller, claims=claims
        ),
        "X-Calling-Service": caller,
    }


# --------------------------------------------------------------------- the version trigger


class TestVersionTrigger:
    async def test_each_tracked_field_bumps_the_version_by_one(
        self, session: AsyncSession, org_id, people
    ) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        report = people["report"]
        assert report.hierarchy_version == 0

        async with session.begin():
            updated = await services.update_employee(
                session, ctx, report, EmployeePatch(location_code="DEL")
            )
        assert updated.hierarchy_version == 1

        async with session.begin():
            updated = await services.update_employee(
                session, ctx, report, EmployeePatch(full_name="Rhea Renamed")
            )
        assert updated.hierarchy_version == 2

        async with session.begin():
            updated = await services.update_employee(
                session,
                ctx,
                report,
                EmployeePatch(dotted_line_manager_id=people["other_manager"].id),
            )
        assert updated.hierarchy_version == 3

    async def test_an_unrelated_field_does_not_bump_the_version(
        self, session: AsyncSession, org_id, people
    ) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        report = people["report"]
        async with session.begin():
            updated = await services.update_employee(
                session, ctx, report, EmployeePatch(job_title="Staff Engineer")
            )
        assert updated.hierarchy_version == 0

    async def test_a_transition_bumps_the_version_because_status_is_tracked(
        self, session: AsyncSession, org_id, people
    ) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        report = people["report"]
        async with session.begin():
            updated = await services.transition_status(session, ctx, report, "active")
        assert updated.hierarchy_version == 1

    async def test_employee_no_bumps_and_department_id_does_not(
        self, admin_session: AsyncSession, org_id, people
    ) -> None:
        """Neither field is reachable through the API — `employee_no` is immutable after
        onboarding and `department_id` is plain metadata — so this exercises the trigger
        directly to prove both halves of the compared tuple (data model §4.2)."""
        report = people["report"]
        async with admin_session.begin():
            await admin_session.execute(
                text("UPDATE employee.employees SET department_id = :d WHERE id = :id"),
                {"d": uuid.uuid4(), "id": report.id},
            )
        async with admin_session.begin():
            unchanged = (
                await admin_session.execute(
                    text("SELECT hierarchy_version FROM employee.employees WHERE id = :id"),
                    {"id": report.id},
                )
            ).scalar_one()
        assert unchanged == 0

        async with admin_session.begin():
            await admin_session.execute(
                text("UPDATE employee.employees SET employee_no = :n WHERE id = :id"),
                {"n": "EMP0003X", "id": report.id},
            )
        async with admin_session.begin():
            bumped = (
                await admin_session.execute(
                    text("SELECT hierarchy_version FROM employee.employees WHERE id = :id"),
                    {"id": report.id},
                )
            ).scalar_one()
        assert bumped == 1

    async def test_a_client_supplied_version_is_ignored(
        self, admin_session: AsyncSession, org_id, people
    ) -> None:
        report = people["report"]
        async with admin_session.begin():
            await admin_session.execute(
                text("UPDATE employee.employees SET hierarchy_version = 99 WHERE id = :id"),
                {"id": report.id},
            )
        async with admin_session.begin():
            version = (
                await admin_session.execute(
                    text("SELECT hierarchy_version FROM employee.employees WHERE id = :id"),
                    {"id": report.id},
                )
            ).scalar_one()
        # No tracked column changed alongside it, so the trigger holds the version where it was
        # — the explicit `99` never reaches the row.
        assert version == 0


class TestConcurrentVersionBump:
    async def test_two_concurrent_edits_on_the_same_row_both_land(self, org_id, people) -> None:
        """The property the trigger exists for: it reads the row's *current committed* state at
        `UPDATE` time, not a Python attribute read earlier, so Postgres's own row lock — not
        application discipline — is what stops one edit's increment from being lost under the
        other's."""
        report_id = people["report"].id
        s1, e1 = await _org_session(people["report"].organization_id)
        s2, e2 = await _org_session(people["report"].organization_id)
        try:

            async def _edit(session: AsyncSession, code: str) -> None:
                # The fetch happens *inside* the transaction the update runs in — done the
                # other way around, SQLAlchemy autobegins a transaction on the `SELECT` and a
                # later explicit `session.begin()` then raises `InvalidRequestError` rather
                # than opening the one the write needs.
                async with session.begin():
                    employee = await selectors.get_employee(
                        session, people["report"].organization_id, report_id
                    )
                    assert employee is not None
                    ctx = ctx_for(
                        people["report"].organization_id,
                        employee,
                        "hrms.people.employee.write",
                        Scope.ORG,
                    )
                    await services.update_employee(
                        session, ctx, employee, EmployeePatch(location_code=code)
                    )

            await asyncio.gather(_edit(s1, "DEL"), _edit(s2, "BLR"))

            async with s1.begin():
                final = await selectors.get_employee(
                    s1, people["report"].organization_id, report_id
                )
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        assert final is not None
        assert final.hierarchy_version == 2, (
            "one of the two concurrent edits lost its increment — the version must reflect "
            "both writes, not just whichever one happened to be read last"
        )


# --------------------------------------------------------------------- the cycle check


class TestCycleCheck:
    async def test_a_direct_cycle_is_refused(self, session: AsyncSession, org_id, people) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        manager, report = people["manager"], people["report"]
        # `report` already reports to `manager`. Pointing `manager` back at `report` is a
        # two-rung cycle.
        with pytest.raises(BusinessRuleProblem, match="reporting cycle"):
            async with session.begin():
                await services.update_employee(
                    session, ctx, manager, EmployeePatch(reporting_manager_id=report.id)
                )

    async def test_an_indirect_cycle_is_refused(
        self, session: AsyncSession, org_id, people
    ) -> None:
        """`manager` <- `report` <- `outsider`, then `manager` -> `outsider` closes a
        three-rung cycle — not merely the direct, two-rung case."""
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        manager, report, outsider = people["manager"], people["report"], people["outsider"]

        async with session.begin():
            await services.update_employee(
                session, ctx, outsider, EmployeePatch(reporting_manager_id=report.id)
            )

        with pytest.raises(BusinessRuleProblem, match="reporting cycle"):
            async with session.begin():
                await services.update_employee(
                    session, ctx, manager, EmployeePatch(reporting_manager_id=outsider.id)
                )

    async def test_an_unrelated_branch_is_not_refused(
        self, session: AsyncSession, org_id, people
    ) -> None:
        """The negative case: pointing an employee at a manager with no path back to them is
        legal — the check must not over-refuse. `manager` and `other_manager` are two
        unconnected roots in the fixture (neither has a manager of their own), so this is not
        the `outsider -> other_manager` chain the other tests exercise."""
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        manager, other_manager = people["manager"], people["other_manager"]
        async with session.begin():
            updated = await services.update_employee(
                session, ctx, manager, EmployeePatch(reporting_manager_id=other_manager.id)
            )
        assert updated.reporting_manager_id == other_manager.id

    async def test_a_dotted_line_cycle_is_refused_independently(
        self, session: AsyncSession, org_id, people
    ) -> None:
        """The dotted line is never routed on, but it walks the same *reporting* edges upward
        from the proposed manager and is refused the same way (ADR-0006 §2)."""
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        manager, report = people["manager"], people["report"]
        # `report` already reports to `manager`. A dotted line from `manager` to `report` closes
        # manager -[dotted]-> report -[reporting]-> manager.
        with pytest.raises(BusinessRuleProblem, match="reporting cycle"):
            async with session.begin():
                await services.update_employee(
                    session, ctx, manager, EmployeePatch(dotted_line_manager_id=report.id)
                )

    async def test_a_dotted_line_self_reference_is_refused(
        self, session: AsyncSession, org_id, people
    ) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        report = people["report"]
        with pytest.raises(BusinessRuleProblem, match="own dotted-line manager"):
            async with session.begin():
                await services.update_employee(
                    session, ctx, report, EmployeePatch(dotted_line_manager_id=report.id)
                )


# --------------------------------------------------------------------- the depth cap


async def _seed_manager_chain(
    session: AsyncSession, org_id: uuid.UUID, *, length: int, prefix: str
) -> list[uuid.UUID]:
    """`length` employees in a straight reporting line, seeded with direct ORM inserts rather
    than `services.create_employee` purely for speed at this size. `chain[0]` is the root (no
    manager of their own); `chain[-1]` is the far end, the one closest to whatever a test then
    points at it."""
    ids = [uuid.uuid4() for _ in range(length)]
    for i, employee_id in enumerate(ids):
        session.add(
            Employee(
                id=employee_id,
                organization_id=org_id,
                employee_no=f"{prefix}{i:04d}",
                full_name=f"{prefix} Chain {i}",
                employment_type="full_time",
                work_mode="office",
                status="active",
                reporting_manager_id=ids[i - 1] if i > 0 else None,
            )
        )
    await session.flush()
    return ids


class TestDepthCap:
    """`walk_manager_chain`'s `too_deep` boundary (`app/selectors.py::MAX_MANAGER_CHAIN_DEPTH`),
    verified at the exact edge rather than trusted by inspection. `MAX_MANAGER_CHAIN_DEPTH` (32)
    is how many rungs the *proposed manager's own* chain may already have when this walk starts
    counting from the proposed manager, not the employee — one short of that would leave the
    employee-inclusive chain approval later walks at 33 rows, matching approval's own 33-row
    ceiling (32 rungs above the employee, plus the employee's own row)."""

    async def test_a_proposed_managers_chain_exactly_32_deep_is_accepted(
        self, session: AsyncSession, org_id, people
    ) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        async with session.begin():
            chain = await _seed_manager_chain(session, org_id, length=32, prefix="D32")
        async with session.begin():
            updated = await services.update_employee(
                session,
                ctx,
                people["outsider"],
                EmployeePatch(reporting_manager_id=chain[-1]),
            )
        assert updated.reporting_manager_id == chain[-1]

    async def test_a_proposed_managers_chain_33_deep_is_refused_with_the_depth_message(
        self, session: AsyncSession, org_id, people
    ) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        async with session.begin():
            chain = await _seed_manager_chain(session, org_id, length=33, prefix="D33")
        with pytest.raises(BusinessRuleProblem, match="too deep to route") as excinfo:
            async with session.begin():
                await services.update_employee(
                    session,
                    ctx,
                    people["outsider"],
                    EmployeePatch(reporting_manager_id=chain[-1]),
                )
        # Distinct from the cycle message: a chain that is merely too long is not looping, and
        # must never be reported as if it were.
        assert "reporting cycle" not in str(excinfo.value)


class TestConcurrentCycleComposition:
    async def test_disjoint_edits_that_would_compose_a_cycle_cannot_both_land(
        self, session: AsyncSession, org_id, people, admin_session: AsyncSession
    ) -> None:
        """`A.mgr = B` and `C.mgr = D` can each be acyclic at read time and still close a loop
        on commit (`data model §4.3`): row locks on the two edited rows never overlap, so only
        the advisory lock stops it. Seed two disjoint two-rung chains, then race the two edits
        that would close the loop `a -> b -> c -> d -> a`."""
        a, b, c, d = (
            people["manager"],
            people["report"],
            people["other_manager"],
            people["outsider"],
        )
        seed_ctx = ctx_for(org_id, a, "hrms.people.employee.write", Scope.ORG)
        async with session.begin():
            await services.update_employee(
                session, seed_ctx, b, EmployeePatch(reporting_manager_id=c.id)
            )
        async with session.begin():
            await services.update_employee(
                session, seed_ctx, d, EmployeePatch(reporting_manager_id=a.id)
            )

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:

            async def _try(
                worker_session: AsyncSession, employee_id: uuid.UUID, manager_id: uuid.UUID
            ) -> str | None:
                # The fetch happens *inside* the transaction the update runs in — see
                # `TestConcurrentVersionBump` for why the order matters.
                try:
                    async with worker_session.begin():
                        employee = await selectors.get_employee(worker_session, org_id, employee_id)
                        assert employee is not None
                        local_ctx = ctx_for(
                            org_id, employee, "hrms.people.employee.write", Scope.ORG
                        )
                        await services.update_employee(
                            worker_session,
                            local_ctx,
                            employee,
                            EmployeePatch(reporting_manager_id=manager_id),
                        )
                    return None
                except BusinessRuleProblem as exc:
                    return str(exc)

            # a -> b and c -> d together close a -> b -> c -> d -> a.
            r1, r2 = await asyncio.gather(_try(s1, a.id, b.id), _try(s2, c.id, d.id))
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        refused = [r for r in (r1, r2) if r is not None]
        assert len(refused) == 1, (
            "exactly one of the two disjoint edits must be refused — the one that loses the "
            f"race for the advisory lock and sees the other's committed change. Got: {(r1, r2)}"
        )
        assert "reporting cycle" in refused[0]

        # Read the committed rows directly, bypassing `session`'s identity map: `a` and `c`
        # were loaded by the `people` fixture on this same session, and the race committed on
        # two entirely different connections, so the cached ORM objects would otherwise report
        # stale values here rather than what actually landed.
        async with admin_session.begin():
            a_mgr = (
                await admin_session.execute(
                    text("SELECT reporting_manager_id FROM employee.employees WHERE id = :id"),
                    {"id": a.id},
                )
            ).scalar_one()
            c_mgr = (
                await admin_session.execute(
                    text("SELECT reporting_manager_id FROM employee.employees WHERE id = :id"),
                    {"id": c.id},
                )
            ).scalar_one()
        # The cycle never actually closed: at most one of the two composing links exists.
        landed = sum(1 for mgr_id in (a_mgr, c_mgr) if mgr_id in (b.id, d.id))
        assert landed == 1


# --------------------------------------------------------------------- full-state events


class TestFullStateEvents:
    async def test_onboarding_carries_full_hierarchy_state(
        self, session: AsyncSession, org_id, people
    ) -> None:
        report = people["report"]
        row = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "hrms.people.employee.onboarded",
                    OutboxEvent.aggregate_id == report.id,
                )
            )
        ).scalar_one()
        payload = row.payload
        assert payload["employee_id"] == str(report.id)
        assert payload["reporting_manager_id"] == str(people["manager"].id)
        assert payload["dotted_line_manager_id"] is None
        assert payload["status"] == "probation"
        assert payload["location_code"] == "MUM"
        assert payload["display_name"] == "Rhea Report"
        assert payload["employee_no"] == "EMP0003"
        assert payload["hierarchy_version"] == 0

    async def test_an_update_carries_full_state_not_a_diff(
        self, session: AsyncSession, org_id, people
    ) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        report = people["report"]
        async with session.begin():
            await services.update_employee(session, ctx, report, EmployeePatch(location_code="DEL"))
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "hrms.people.employee.updated",
                        OutboxEvent.aggregate_id == report.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        payload = rows[-1].payload
        # Full state, not a diff: every hierarchy field travels, not only the one that changed.
        assert payload["location_code"] == "DEL"
        assert payload["employee_no"] == "EMP0003"
        assert payload["display_name"] == "Rhea Report"
        assert payload["reporting_manager_id"] == str(people["manager"].id)
        assert payload["dotted_line_manager_id"] is None
        assert payload["status"] == "probation"
        assert payload["hierarchy_version"] == 1
        # The existing `changed` list stays, for a consumer that still wants it.
        assert payload["changed"] == ["location_code"]

    async def test_a_transition_carries_full_state(
        self, session: AsyncSession, org_id, people
    ) -> None:
        ctx = ctx_for(org_id, people["manager"], "hrms.people.employee.write", Scope.ORG)
        report = people["report"]
        async with session.begin():
            await services.transition_status(session, ctx, report, "active")
        row = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "hrms.people.employee.transitioned",
                    OutboxEvent.aggregate_id == report.id,
                )
            )
        ).scalar_one()
        payload = row.payload
        assert payload["from"] == "probation"
        assert payload["to"] == "active"
        assert payload["status"] == "active"
        assert payload["employee_no"] == "EMP0003"
        assert payload["display_name"] == "Rhea Report"
        assert payload["reporting_manager_id"] == str(people["manager"].id)
        assert payload["hierarchy_version"] == 1


# --------------------------------------------------------------------- the internal endpoint


class TestInternalHierarchyEndpoint:
    async def test_it_refuses_a_request_with_no_service_token(self, client, org_id, people) -> None:
        response = await client.get(
            "/internal/v1/hierarchy", params={"organization_id": str(org_id)}
        )
        assert response.status_code == 401

    async def test_a_token_for_another_service_is_refused(self, client, org_id, people) -> None:
        response = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(org_id)},
            headers=internal_headers(audience="billing"),
        )
        assert response.status_code == 401

    async def test_a_token_minted_by_a_service_other_than_approval_is_refused(
        self, client, org_id, people
    ) -> None:
        """A token that is otherwise perfectly valid — right audience, right signature, not
        expired — but names a caller outside `ALLOWED_INTERNAL_ISSUERS`. Distinct from the
        previous test: that one has the wrong `aud`; this one has the right `aud` and the wrong
        `iss` (`app/api/internal.py::_verify_internal_caller`)."""
        response = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(org_id)},
            headers=internal_headers(caller="billing"),
        )
        assert response.status_code == 401

    async def test_a_token_whose_org_claim_does_not_match_the_query_is_refused(
        self, client, org_id, people
    ) -> None:
        """A caller that *does* bind an authorized organization into its token (`ORG_CLAIM`)
        must not be able to ask for a different one in the query string — the whole point of
        binding it (`app/api/internal.py`)."""
        response = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(org_id)},
            headers=internal_headers(org=uuid.uuid4()),
        )
        assert response.status_code == 401

    async def test_a_legitimate_call_succeeds_and_leaves_an_audit_row(
        self, client, org_id, people, session: AsyncSession
    ) -> None:
        """The positive case for both new checks together: `iss="approval"` and an `org` claim
        that matches the organization asked for. Also proves every page writes an audit row
        (`app/api/internal.py::hierarchy`), naming the caller and the page it read."""
        response = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(org_id)},
            headers=internal_headers(org=org_id),
        )
        assert response.status_code == 200

        row = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == org_id,
                    AuditLog.action == "employee.hierarchy.exported",
                )
            )
        ).scalar_one()
        assert row.entity == "employee_hierarchy_page"
        assert row.entity_id is None
        assert row.after is not None
        assert row.after["caller_iss"] == "approval"
        assert row.after["count"] == len(people)

    async def test_attendance_may_read_it_when_it_binds_its_organization(
        self, client, org_id, people, session: AsyncSession
    ) -> None:
        """R8: attendance's TEAM backfill is the second legitimate caller. Its page is the one
        approval reads, and every page it takes leaves an audit row naming it."""
        response = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(org_id)},
            headers=internal_headers(caller="attendance", org=org_id),
        )
        assert response.status_code == 200
        assert len(response.json()["items"]) == len(people)

        row = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.organization_id == org_id,
                    AuditLog.action == "employee.hierarchy.exported",
                )
            )
        ).scalar_one()
        assert row.after is not None
        assert row.after["caller_iss"] == "attendance"

    async def test_attendance_without_an_org_claim_is_refused(self, client, org_id, people) -> None:
        """A caller added after the gap was understood starts strict: attendance must bind the
        organization, where approval's older posture still lets the query string name it."""
        response = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(org_id)},
            headers=internal_headers(caller="attendance"),
        )
        assert response.status_code == 401

    async def test_attendance_cannot_name_an_organization_its_token_does_not_carry(
        self, client, org_id, people, admin_session: AsyncSession
    ) -> None:
        other_org = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                Employee.__table__.insert().values(
                    id=uuid.uuid4(),
                    organization_id=other_org,
                    employee_no="Z0001",
                    full_name="Zoe Zeta",
                    employment_type="full_time",
                    work_mode="office",
                    status="active",
                )
            )
        response = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(other_org)},
            headers=internal_headers(caller="attendance", org=org_id),
        )
        assert response.status_code == 401
        assert "Zoe Zeta" not in response.text

    async def test_admitting_attendance_here_does_not_admit_it_to_the_links_endpoint(
        self, client, org_id, people
    ) -> None:
        """The per-route allowlists stay disjoint: a legitimate caller of one endpoint must not
        become a caller of the other by being on the router-level union."""
        response = await client.get(
            "/internal/v1/employee-links",
            params={"organization_id": str(org_id)},
            headers=internal_headers(caller="attendance", org=org_id),
        )
        assert response.status_code == 401

    async def test_approval_keeps_its_existing_posture(self, client, org_id, people) -> None:
        """No org claim, `iss="approval"`: unchanged, so a deployed approval is not broken by
        this slice."""
        response = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(org_id)},
            headers=internal_headers(),
        )
        assert response.status_code == 200

    async def test_it_is_absent_from_the_public_schema(self, client, org_id, people) -> None:
        schema = (await client.get("/openapi.json")).json()
        assert not [p for p in schema["paths"] if p.startswith("/internal")]

    async def test_it_returns_the_same_shape_the_events_carry(self, client, org_id, people) -> None:
        response = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(org_id)},
            headers=internal_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["next_cursor"] is None  # one page for four employees, default limit 500
        by_no = {item["employee_no"]: item for item in body["items"]}
        report = by_no["EMP0003"]
        assert report["employee_id"] == str(people["report"].id)
        assert report["reporting_manager_id"] == str(people["manager"].id)
        assert report["dotted_line_manager_id"] is None
        assert report["display_name"] == "Rhea Report"
        assert report["location_code"] == "MUM"
        assert report["status"] == "probation"
        assert report["hierarchy_version"] == 0

    async def test_it_paginates_by_keyset_not_offset(self, client, org_id, people) -> None:
        first = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(org_id), "limit": 2},
            headers=internal_headers(),
        )
        assert first.status_code == 200
        first_body = first.json()
        assert len(first_body["items"]) == 2
        assert first_body["next_cursor"] is not None

        second = await client.get(
            "/internal/v1/hierarchy",
            params={
                "organization_id": str(org_id),
                "limit": 2,
                "cursor": first_body["next_cursor"],
            },
            headers=internal_headers(),
        )
        assert second.status_code == 200
        second_body = second.json()
        assert len(second_body["items"]) == 2
        # Four employees, page size two: this page lands exactly on the boundary, so it still
        # carries a `next_cursor` — the caller cannot yet tell it was the last page with data.
        assert second_body["next_cursor"] is not None

        third = await client.get(
            "/internal/v1/hierarchy",
            params={
                "organization_id": str(org_id),
                "limit": 2,
                "cursor": second_body["next_cursor"],
            },
            headers=internal_headers(),
        )
        assert third.status_code == 200
        third_body = third.json()
        # One page past the boundary comes back empty, and only then is `next_cursor` null —
        # that emptiness is what actually terminates the walk (`HierarchyPage`).
        assert third_body["items"] == []
        assert third_body["next_cursor"] is None

        seen = {i["employee_id"] for i in first_body["items"]} | {
            i["employee_id"] for i in second_body["items"]
        }
        assert seen == {str(e.id) for e in people.values()}

    async def test_the_page_size_is_capped(self, client, org_id, people) -> None:
        response = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(org_id), "limit": 5000},
            headers=internal_headers(),
        )
        assert response.status_code == 422

    async def test_it_is_tenant_bound_not_a_cross_tenant_listing(
        self, client, org_id, people, admin_session: AsyncSession
    ) -> None:
        """RLS binds each call to exactly one organization: a response never mixes rows from two
        organizations, whichever one the call names. This is **not** the same guarantee as
        refusing to name the "wrong" organization in the first place — a caller with an
        `iss="approval"` token and no `org` claim (today's real shape, see
        `internal_headers`/`app/api/internal.py`'s module docstring) can still ask for either
        organization here and get exactly that one back. That residual gap is why every page is
        now audited, and it closes once approval's client binds `ORG_CLAIM` and this check can
        become mandatory rather than best-effort."""
        other_org = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                Employee.__table__.insert().values(
                    id=uuid.uuid4(),
                    organization_id=other_org,
                    employee_no="Z0001",
                    full_name="Zoe Zeta",
                    employment_type="full_time",
                    work_mode="office",
                    status="active",
                )
            )

        response = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(org_id)},
            headers=internal_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert {i["employee_no"] for i in body["items"]} == {
            "EMP0001",
            "EMP0002",
            "EMP0003",
            "EMP0004",
        }
        assert "Zoe Zeta" not in response.text

        other_response = await client.get(
            "/internal/v1/hierarchy",
            params={"organization_id": str(other_org)},
            headers=internal_headers(),
        )
        assert other_response.status_code == 200
        other_body = other_response.json()
        assert {i["employee_no"] for i in other_body["items"]} == {"Z0001"}
