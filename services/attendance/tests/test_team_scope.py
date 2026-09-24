"""R8: TEAM scope, answered from a projection of employee's reporting line.

A manager holding `hrms.time.attendance.read:TEAM` sees the attendance of their **current direct
reports** and nobody else's — not their own, not another team's, not an exited report's, not
another tenant's. The reporting line is not this service's fact, so these tests drive the whole
loop the way production does: employee's events (and the backfill) fill `team_membership`, a
TEAM-scoped read joins it, and every rule below is asserted against real PostgreSQL under the
NOBYPASSRLS app role, so a filter that only *looked* right cannot pass.

The negative half is the point. "The manager sees their reports" is the success path exercised by
hand; the assertions that earn the change are the ones that a wrong join would flip — the report of
another manager, a person the projection has never heard of, an exited employee, a late event that
would resurrect an old manager, and a tenant that happens to reuse the same employee ids.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.consumers import (
    on_employee_onboarded,
    on_employee_transitioned,
    on_employee_updated,
)
from app.main import app
from app.models import AttendanceRecord, ProcessedEvent, TeamMembership
from app.schemas import CheckInIn
from app.services import TeamMembershipWrite
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.db import dispose_engine, init_engine
from tests.helpers import ctx_for as _ctx_for
from tests.helpers import employee_event_envelope, principal_for

pytestmark = pytest.mark.integration

READ = "hrms.time.attendance.read"


def ctx_for(org_id, subject_ref, scope, *, locations=None) -> ScopeContext:
    return _ctx_for(org_id, subject_ref, READ, scope, locations=locations)


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumer opens its own `session_scope`; the process engine is what the app lifespan
    initialises in production."""
    init_engine(settings())
    yield
    await dispose_engine()


def _write(
    employee_id: uuid.UUID,
    manager_id: uuid.UUID | None,
    version: int,
    status: str = "active",
) -> TeamMembershipWrite:
    return TeamMembershipWrite(
        employee_id=employee_id,
        manager_employee_id=manager_id,
        status=status,
        source_version=version,
        source_occurred_at=datetime.now(UTC),
    )


async def _punch(session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID) -> uuid.UUID:
    ctx = _ctx_for(org_id, employee_id, "hrms.time.attendance.write", Scope.OWN)
    async with session.begin():
        record = await services.check_in(
            session, ctx, CheckInIn(work_mode="office", source="web", location_code="MUM")
        )
        return record.id


async def _employees_seen(session: AsyncSession, ctx: ScopeContext) -> set[uuid.UUID]:
    async with session.begin():
        rows = (await session.execute(selectors.attendance_stmt(ctx))).scalars().all()
        return {r.employee_id for r in rows}


async def _row(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID
) -> tuple[uuid.UUID | None, str, int] | None:
    async with session.begin():
        found = (
            await session.execute(
                select(
                    TeamMembership.manager_employee_id,
                    TeamMembership.status,
                    TeamMembership.source_version,
                ).where(
                    TeamMembership.organization_id == org_id,
                    TeamMembership.employee_id == employee_id,
                )
            )
        ).one_or_none()
    return None if found is None else (found[0], found[1], found[2])


@pytest.fixture
def people() -> dict[str, uuid.UUID]:
    """Everyone in the org chart these tests need, by role. `loner` is an employee the projection
    has never heard of; `exited` reports to `mgr` on paper but has left."""
    names = ("mgr", "other_mgr", "rep_a", "rep_b", "rep_c", "exited", "loner")
    return {name: uuid.uuid4() for name in names}


@pytest.fixture
async def team(session: AsyncSession, org_id: uuid.UUID, people: dict[str, uuid.UUID]) -> dict:
    """`mgr` has two reports and an exited one; `other_mgr` has one; each punched in today.

    The projection is filled through the real upsert, so a test that reaches the wrong person's row
    fails at the *authorization* check rather than at "no such row".
    """
    async with session.begin():
        await services.upsert_team_membership_bulk(
            session,
            organization_id=org_id,
            rows=[
                _write(people["mgr"], None, 1),
                _write(people["other_mgr"], None, 1),
                _write(people["rep_a"], people["mgr"], 1),
                _write(people["rep_b"], people["mgr"], 1),
                _write(people["rep_c"], people["other_mgr"], 1),
                _write(people["exited"], people["mgr"], 2, status="exited"),
            ],
        )
    record_ids = {name: await _punch(session, org_id, people[name]) for name in people}
    return {**people, **{f"{name}_record": rid for name, rid in record_ids.items()}}


# --------------------------------------------------------------------- the list


class TestTeamSeesExactlyItsReports:
    async def test_a_manager_sees_their_current_reports_and_no_one_else(
        self, session, org_id, team
    ) -> None:
        ctx = ctx_for(org_id, team["mgr"], Scope.TEAM)
        assert await _employees_seen(session, ctx) == {team["rep_a"], team["rep_b"]}

    async def test_the_other_managers_team_is_disjoint(self, session, org_id, team) -> None:
        ctx = ctx_for(org_id, team["other_mgr"], Scope.TEAM)
        assert await _employees_seen(session, ctx) == {team["rep_c"]}

    async def test_a_manager_does_not_see_their_own_records_at_team_scope(
        self, session, org_id, team
    ) -> None:
        """TEAM is "rows whose manager is me", as it is in employee-service and in
        `ScopeContext.apply` — not the OWN rung plus their reports."""
        ctx = ctx_for(org_id, team["mgr"], Scope.TEAM)
        assert team["mgr"] not in await _employees_seen(session, ctx)

    async def test_an_employee_with_no_reports_sees_nothing(self, session, org_id, team) -> None:
        ctx = ctx_for(org_id, team["rep_a"], Scope.TEAM)
        assert await _employees_seen(session, ctx) == set()

    async def test_a_report_the_projection_has_never_heard_of_is_in_nobodys_team(
        self, session, org_id, team
    ) -> None:
        """`loner` punched in, but no event or backfill row has ever named them. An inner join
        means they match no manager — the safe way to be wrong, never "everyone unmanaged"."""
        for manager in ("mgr", "other_mgr"):
            ctx = ctx_for(org_id, team[manager], Scope.TEAM)
            assert team["loner"] not in await _employees_seen(session, ctx)

    async def test_a_caller_with_no_employee_id_sees_nothing_not_everything(
        self, session, org_id, team
    ) -> None:
        """A token with no `subject_ref` is authenticated but is not an employee. `team_col ==
        NULL` would render `IS NULL` and match every unmanaged row; `ctx.apply` matches nothing."""
        ctx = ctx_for(org_id, None, Scope.TEAM)
        assert await _employees_seen(session, ctx) == set()

    async def test_an_unpopulated_projection_shows_a_manager_nobody(
        self, session, org_id, people
    ) -> None:
        """Before the backfill has run, TEAM answers empty. It answered 403 before this slice."""
        for name in ("rep_a", "rep_b"):
            await _punch(session, org_id, people[name])
        ctx = ctx_for(org_id, people["mgr"], Scope.TEAM)
        assert await _employees_seen(session, ctx) == set()

    async def test_the_employee_id_filter_cannot_widen_the_scope(
        self, session, org_id, team
    ) -> None:
        """`?employee_id=<someone not on my team>` narrows within the scope; it never leaves it."""
        ctx = ctx_for(org_id, team["mgr"], Scope.TEAM)
        async with session.begin():
            rows = (
                (await session.execute(selectors.attendance_stmt(ctx, employee_id=team["rep_c"])))
                .scalars()
                .all()
            )
        assert rows == []

    async def test_org_scope_still_sees_everyone_including_the_exited_and_the_unknown(
        self, session, org_id, team
    ) -> None:
        """The join is added for TEAM only. HR at ORG is unaffected by the projection, so an
        employee the projection lacks does not vanish from their view."""
        ctx = ctx_for(org_id, team["mgr"], Scope.ORG)
        assert await _employees_seen(session, ctx) == {
            team[n] for n in ("mgr", "other_mgr", "rep_a", "rep_b", "rep_c", "exited", "loner")
        }

    async def test_the_count_a_page_reports_does_not_leak_rows_outside_the_scope(
        self, session, org_id, team
    ) -> None:
        """`paginate` counts the same statement it pages, so the total cannot disclose how many
        records exist beyond the caller's team."""
        ctx = ctx_for(org_id, team["mgr"], Scope.TEAM)
        async with session.begin():
            total = (
                await session.execute(
                    select(func.count()).select_from(selectors.attendance_stmt(ctx).subquery())
                )
            ).scalar_one()
        assert total == 2


# --------------------------------------------------------------------- changes to the line


class TestTheLineChanges:
    async def test_a_manager_change_re_scopes_the_report(self, session, org_id, team) -> None:
        """`rep_a` moves from `mgr` to `other_mgr`: the old manager stops seeing them, the new one
        starts — including the day they already punched. Current state, not history."""
        await on_employee_updated(
            employee_event_envelope(
                organization_id=org_id,
                employee_id=team["rep_a"],
                manager_id=team["other_mgr"],
                version=2,
            )
        )

        assert await _employees_seen(session, ctx_for(org_id, team["mgr"], Scope.TEAM)) == {
            team["rep_b"]
        }
        assert await _employees_seen(session, ctx_for(org_id, team["other_mgr"], Scope.TEAM)) == {
            team["rep_a"],
            team["rep_c"],
        }

    async def test_a_report_with_no_manager_any_more_is_in_nobodys_team(
        self, session, org_id, team
    ) -> None:
        await on_employee_updated(
            employee_event_envelope(
                organization_id=org_id, employee_id=team["rep_a"], manager_id=None, version=2
            )
        )
        assert await _employees_seen(session, ctx_for(org_id, team["mgr"], Scope.TEAM)) == {
            team["rep_b"]
        }

    async def test_a_newly_onboarded_report_appears_without_a_backfill(
        self, session, org_id, team
    ) -> None:
        newcomer = uuid.uuid4()
        await _punch(session, org_id, newcomer)
        await on_employee_onboarded(
            employee_event_envelope(
                organization_id=org_id,
                employee_id=newcomer,
                manager_id=team["mgr"],
                version=0,
                event_type="hrms.people.employee.onboarded",
            )
        )
        assert newcomer in await _employees_seen(session, ctx_for(org_id, team["mgr"], Scope.TEAM))

    async def test_a_two_person_loop_is_untidy_not_dangerous(self, session, org_id, people) -> None:
        """A→B→A. Nothing walks the line — TEAM is *direct* reports — so each simply sees the
        other. The read terminates, returns each person once and reaches nobody else."""
        a, b = people["rep_a"], people["rep_b"]
        async with session.begin():
            await services.upsert_team_membership_bulk(
                session,
                organization_id=org_id,
                rows=[_write(a, b, 1), _write(b, a, 1), _write(people["rep_c"], b, 1)],
            )
        for name in ("rep_a", "rep_b", "rep_c"):
            await _punch(session, org_id, people[name])

        assert await _employees_seen(session, ctx_for(org_id, a, Scope.TEAM)) == {b}
        assert await _employees_seen(session, ctx_for(org_id, b, Scope.TEAM)) == {
            a,
            people["rep_c"],
        }

    async def test_an_employee_who_is_their_own_manager_is_in_no_ones_team(
        self, session, org_id, people
    ) -> None:
        """Employee-service refuses this on write, so it is a corrupt event. It must not put a
        person in their own team, and it must not wedge the consumer on the table's CHECK."""
        me = people["rep_a"]
        await on_employee_updated(
            employee_event_envelope(
                organization_id=org_id, employee_id=me, manager_id=me, version=1
            )
        )
        assert await _row(session, org_id, me) == (None, "active", 1)
        await _punch(session, org_id, me)
        assert await _employees_seen(session, ctx_for(org_id, me, Scope.TEAM)) == set()


class TestATerminatedEmployee:
    async def test_an_exited_report_leaves_the_managers_team(self, session, org_id, team) -> None:
        """The `transitioned` event carries the full state, so status arrives with the manager
        still set — and the manager's team is their *current* reports."""
        await on_employee_transitioned(
            employee_event_envelope(
                organization_id=org_id,
                employee_id=team["rep_a"],
                manager_id=team["mgr"],
                version=2,
                status="exited",
                event_type="hrms.people.employee.transitioned",
            )
        )
        assert await _employees_seen(session, ctx_for(org_id, team["mgr"], Scope.TEAM)) == {
            team["rep_b"]
        }

    async def test_the_exited_employees_records_stay_visible_to_hr(
        self, session, org_id, team
    ) -> None:
        ctx = ctx_for(org_id, team["mgr"], Scope.ORG)
        assert team["exited"] in await _employees_seen(session, ctx)

    async def test_an_exited_row_is_kept_so_a_stale_event_cannot_resurrect_the_person(
        self, session, org_id, team
    ) -> None:
        """Deleting the row on exit would leave the version gate nothing to compare against: a
        late pre-exit event would find no row and insert the person back into a team."""
        await on_employee_updated(
            employee_event_envelope(
                organization_id=org_id,
                employee_id=team["exited"],
                manager_id=team["mgr"],
                version=1,
                status="active",
            )
        )
        assert await _row(session, org_id, team["exited"]) == (team["mgr"], "exited", 2)
        assert team["exited"] not in await _employees_seen(
            session, ctx_for(org_id, team["mgr"], Scope.TEAM)
        )


class TestOutOfOrderAndReplay:
    async def test_a_stale_event_arriving_late_is_dropped(self, session, org_id, team) -> None:
        """v3 moves `rep_a` to `other_mgr`; v2 (the older move back to `mgr`) is delivered after
        it. The transaction-start `occurred_at` can be stamped in the wrong order, which is why
        the gate is a version and never a timestamp."""
        newer = employee_event_envelope(
            organization_id=org_id,
            employee_id=team["rep_a"],
            manager_id=team["other_mgr"],
            version=3,
            occurred_at="2020-01-01T00:00:00+00:00",
        )
        older = employee_event_envelope(
            organization_id=org_id,
            employee_id=team["rep_a"],
            manager_id=team["mgr"],
            version=2,
            occurred_at="2030-01-01T00:00:00+00:00",
        )
        await on_employee_updated(newer)
        await on_employee_updated(older)

        assert await _row(session, org_id, team["rep_a"]) == (team["other_mgr"], "active", 3)

    async def test_an_equal_version_under_a_fresh_event_id_is_a_no_op(
        self, session, org_id, team
    ) -> None:
        """Idempotence that does not lean on `processed_events`: the same state replayed under a
        new event id (a backfill row, a replay from the outbox) changes nothing."""
        first = employee_event_envelope(
            organization_id=org_id,
            employee_id=team["rep_a"],
            manager_id=team["other_mgr"],
            version=2,
        )
        conflicting = employee_event_envelope(
            organization_id=org_id,
            employee_id=team["rep_a"],
            manager_id=team["mgr"],
            version=2,
        )
        await on_employee_updated(first)
        await on_employee_updated(conflicting)

        assert await _row(session, org_id, team["rep_a"]) == (team["other_mgr"], "active", 2)

    async def test_redelivery_of_the_same_event_is_applied_once(
        self, session, org_id, team
    ) -> None:
        envelope = employee_event_envelope(
            organization_id=org_id,
            employee_id=team["rep_a"],
            manager_id=team["other_mgr"],
            version=2,
        )
        await on_employee_updated(envelope)
        # Move the row on by another route, then redeliver the *old* event id: the ledger
        # short-circuits it before the gate is even consulted.
        async with session.begin():
            await services.upsert_team_membership_bulk(
                session,
                organization_id=org_id,
                rows=[_write(team["rep_a"], team["mgr"], 5)],
            )
        await on_employee_updated(envelope)

        assert await _row(session, org_id, team["rep_a"]) == (team["mgr"], "active", 5)
        async with session.begin():
            ledger = (
                await session.execute(
                    select(func.count())
                    .select_from(ProcessedEvent)
                    .where(ProcessedEvent.event_id == uuid.UUID(envelope["event_id"]))
                )
            ).scalar_one()
        assert ledger == 1

    async def test_the_batch_upsert_keeps_the_highest_version_of_a_repeated_employee(
        self, session, org_id, people
    ) -> None:
        """`ON CONFLICT DO UPDATE` refuses to touch one row twice in a statement; the batch is
        reduced to each employee's newest state first, in whichever order it arrives."""
        me = people["rep_a"]
        async with session.begin():
            written = await services.upsert_team_membership_bulk(
                session,
                organization_id=org_id,
                rows=[_write(me, people["mgr"], 4), _write(me, people["other_mgr"], 2)],
            )
        assert written == 1
        assert await _row(session, org_id, me) == (people["mgr"], "active", 4)


class TestMalformedEvents:
    async def test_an_event_with_no_version_is_dropped_never_read_as_version_zero(
        self, session, org_id, team
    ) -> None:
        envelope = employee_event_envelope(
            organization_id=org_id,
            employee_id=team["rep_a"],
            manager_id=team["other_mgr"],
            version=None,
        )
        await on_employee_updated(envelope)

        assert await _row(session, org_id, team["rep_a"]) == (team["mgr"], "active", 1)
        async with session.begin():
            assert await session.get(ProcessedEvent, uuid.UUID(envelope["event_id"])) is not None

    async def test_an_unknown_status_is_dropped_not_dead_lettered(
        self, session, org_id, team
    ) -> None:
        await on_employee_updated(
            employee_event_envelope(
                organization_id=org_id,
                employee_id=team["rep_a"],
                manager_id=team["other_mgr"],
                version=9,
                status="teleported",
            )
        )
        assert await _row(session, org_id, team["rep_a"]) == (team["mgr"], "active", 1)

    async def test_an_envelope_and_payload_naming_different_tenants_apply_under_neither(
        self, session, org_id, team, admin_session
    ) -> None:
        other_org = uuid.uuid4()
        await on_employee_updated(
            employee_event_envelope(
                organization_id=org_id,
                payload_organization_id=other_org,
                employee_id=team["rep_a"],
                manager_id=team["other_mgr"],
                version=9,
            )
        )
        assert await _row(session, org_id, team["rep_a"]) == (team["mgr"], "active", 1)
        async with admin_session.begin():
            leaked = (
                await admin_session.execute(
                    select(func.count())
                    .select_from(TeamMembership)
                    .where(TeamMembership.organization_id == other_org)
                )
            ).scalar_one()
        assert leaked == 0


# --------------------------------------------------------------------- one record


class TestTeamScopedDetail:
    async def test_a_report_s_record_is_readable_by_their_manager(
        self, client, org_id, team
    ) -> None:
        principal = principal_for(org_id, team["mgr"], [f"{READ}:TEAM"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/attendance/{team['rep_a_record']}")
        assert response.status_code == 200
        assert response.json()["employee_id"] == str(team["rep_a"])

    async def test_someone_elses_report_is_refused_by_id(self, client, org_id, team) -> None:
        """The named vulnerability: a correct list beside an unguarded detail view. `mgr` can name
        `rep_c`'s record by primary key and is refused."""
        principal = principal_for(org_id, team["mgr"], [f"{READ}:TEAM"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/attendance/{team['rep_c_record']}")
        assert response.status_code == 403

    async def test_their_own_record_is_refused_at_team_scope(self, client, org_id, team) -> None:
        principal = principal_for(org_id, team["mgr"], [f"{READ}:TEAM"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/attendance/{team['mgr_record']}")
        assert response.status_code == 403

    async def test_an_unprojected_employee_s_record_is_refused(self, client, org_id, team) -> None:
        principal = principal_for(org_id, team["mgr"], [f"{READ}:TEAM"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/attendance/{team['loner_record']}")
        assert response.status_code == 403

    async def test_an_exited_reports_record_is_refused(self, client, org_id, team) -> None:
        principal = principal_for(org_id, team["mgr"], [f"{READ}:TEAM"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/attendance/{team['exited_record']}")
        assert response.status_code == 403

    async def test_a_caller_with_no_employee_id_is_refused(self, client, org_id, team) -> None:
        principal = principal_for(org_id, None, [f"{READ}:TEAM"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/attendance/{team['rep_a_record']}")
        assert response.status_code == 403

    async def test_the_list_route_returns_exactly_the_team(self, client, org_id, team) -> None:
        principal = principal_for(org_id, team["mgr"], [f"{READ}:TEAM"])
        with as_principal(principal):
            response = await client.get("/api/v1/attendance")
        assert response.status_code == 200
        body = response.json()
        assert {i["employee_id"] for i in body["items"]} == {
            str(team["rep_a"]),
            str(team["rep_b"]),
        }
        assert body["total"] == 2


# --------------------------------------------------------------------- another tenant


class TestAnotherTenant:
    async def test_a_tenant_reusing_the_same_ids_does_not_leak_into_this_teams_reads(
        self, session, admin_session, org_id, team
    ) -> None:
        """Org B contains the *same manager id* with a report of its own and a punched-in day.
        Org A's manager must see only org A's reports: the join names the tenant on both sides, and
        RLS is the net under it."""
        other_org = uuid.uuid4()
        b_report = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                TeamMembership.__table__.insert().values(
                    organization_id=other_org,
                    employee_id=b_report,
                    manager_employee_id=team["mgr"],
                    status="active",
                    source_version=1,
                    source_occurred_at=datetime.now(UTC),
                )
            )
            await admin_session.execute(
                AttendanceRecord.__table__.insert().values(
                    id=uuid.uuid4(),
                    organization_id=other_org,
                    employee_id=b_report,
                    work_date=datetime.now(UTC).date(),
                    check_in_at=datetime.now(UTC),
                    source="web",
                    work_mode="office",
                    status="present",
                )
            )

        assert b_report not in await _employees_seen(
            session, ctx_for(org_id, team["mgr"], Scope.TEAM)
        )
        # ... and from the other side: org B's own manager reads org B's team.
        assert await _employees_seen(session, ctx_for(other_org, team["mgr"], Scope.TEAM)) == set()

    async def test_the_app_role_cannot_write_a_projection_row_for_another_tenant(
        self, session, org_id, team
    ) -> None:
        """The RLS `WITH CHECK` is the net under the explicit tenant on every write."""
        with pytest.raises(DBAPIError):
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO attendance.team_membership "
                        "(organization_id, employee_id, status, source_version, "
                        "source_occurred_at) "
                        "VALUES (:org, :emp, 'active', 1, now())"
                    ),
                    {"org": uuid.uuid4(), "emp": uuid.uuid4()},
                )

    async def test_an_event_for_another_tenant_never_touches_this_tenants_rows(
        self, session, admin_session, org_id, team
    ) -> None:
        other_org = uuid.uuid4()
        await on_employee_updated(
            employee_event_envelope(
                organization_id=other_org,
                employee_id=team["rep_a"],
                manager_id=team["other_mgr"],
                version=99,
            )
        )
        # Org A's row for the same employee id is untouched.
        assert await _row(session, org_id, team["rep_a"]) == (team["mgr"], "active", 1)
        async with admin_session.begin():
            theirs = (
                await admin_session.execute(
                    select(TeamMembership.source_version).where(
                        TeamMembership.organization_id == other_org,
                        TeamMembership.employee_id == team["rep_a"],
                    )
                )
            ).scalar_one()
        assert theirs == 99

    async def test_the_projection_is_invisible_across_tenants_to_the_app_role(
        self, session, admin_session, org_id, team
    ) -> None:
        other_org = uuid.uuid4()
        async with admin_session.begin():
            await admin_session.execute(
                TeamMembership.__table__.insert().values(
                    organization_id=other_org,
                    employee_id=uuid.uuid4(),
                    manager_employee_id=None,
                    status="active",
                    source_version=1,
                    source_occurred_at=datetime.now(UTC),
                )
            )
        async with session.begin():
            visible = (
                await session.execute(select(func.count()).select_from(TeamMembership))
            ).scalar_one()
        # The six rows `team` seeded for this tenant — the other tenant's row is filtered out.
        assert visible == 6
