"""Authorization, scope and validation — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth more than
proving one succeeds. `ctx.apply` narrows the *list* and the write path's `assert_can_touch`
guards the *object*, because filtering the list and forgetting the object is the named
vulnerability. A user who can read only their own weeks must also be refused writing another's.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.main import app
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.errors import ForbiddenProblem, ValidationProblem
from tests.conftest import WEEK_START
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


# --------------------------------------------------------------------- list scope


class TestListScope:
    async def _refs(self, session: AsyncSession, ctx: ScopeContext) -> set[str]:
        rows = (
            (await session.execute(selectors.timesheets_stmt(ctx, week_start=WEEK_START)))
            .scalars()
            .all()
        )
        return {t.person_ref for t in rows}

    async def test_org_scope_sees_the_whole_grid(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "pulseboard.time.timesheet.read", Scope.ORG)
        assert await self._refs(session, ctx) == {"p01", "p02"}

    async def test_own_scope_sees_only_your_own_week(self, session, org_id, data) -> None:
        ctx = ctx_for(
            org_id, "pulseboard.time.timesheet.read", Scope.OWN, subject_ref=data["subject_a"]
        )
        assert await self._refs(session, ctx) == {"p01"}

    async def test_own_scope_with_no_subject_sees_nothing(self, session, org_id, data) -> None:
        """OWN with nothing to own must match nothing, never everything."""
        ctx = ctx_for(org_id, "pulseboard.time.timesheet.read", Scope.OWN, subject_ref=None)
        assert await self._refs(session, ctx) == set()

    async def test_team_scope_is_refused_rather_than_widened(self, session, org_id, data) -> None:
        ctx = ctx_for(
            org_id, "pulseboard.time.timesheet.read", Scope.TEAM, subject_ref=data["subject_a"]
        )
        with pytest.raises(ForbiddenProblem, match="TEAM"):
            selectors.timesheets_stmt(ctx, week_start=WEEK_START)


# --------------------------------------------------------------------- endpoint authz


READ_ENDPOINTS = [
    "/api/v1/timesheets",
    "/api/v1/timesheets/summary",
]


class TestEndpointAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id, data) -> None:
        for path in READ_ENDPOINTS:
            response = await client.get(path)
            assert response.status_code == 401, path

    async def test_missing_the_permission_is_forbidden(self, client, org_id, data) -> None:
        """Entitled to pulseboard, but holding no pulseboard permission at all."""
        principal = principal_for(org_id, [], subject_ref=data["subject_a"])
        with as_principal(principal):
            for path in READ_ENDPOINTS:
                response = await client.get(path)
                assert response.status_code == 403, path
                assert response.headers["content-type"].startswith("application/problem+json")

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            ["pulseboard.time.timesheet.read:ORG"],
            subject_ref=data["subject_a"],
            entitlements=[],
        )
        with as_principal(principal):
            grid = await client.get("/api/v1/timesheets")
            rollup = await client.get("/api/v1/timesheets/summary")
        assert grid.status_code == 402
        assert rollup.status_code == 402

    async def test_a_reader_cannot_record_a_week(self, client, org_id, data) -> None:
        """Holding timesheet.read but not timesheet.write cannot record — the guard is by name."""
        principal = principal_for(
            org_id, ["pulseboard.time.timesheet.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.put(
                "/api/v1/timesheets",
                json={
                    "person_ref": "p03",
                    "person_id": str(uuid.uuid4()),
                    "week_start": WEEK_START.isoformat(),
                },
            )
        assert response.status_code == 403

    async def test_an_own_writer_cannot_record_someone_elses_week(
        self, client, org_id, data
    ) -> None:
        """OWN-scoped write is refused at the object level for another person's week."""
        principal = principal_for(
            org_id, ["pulseboard.time.timesheet.write:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.put(
                "/api/v1/timesheets",
                json={
                    "person_ref": "p02",
                    "person_id": str(data["subject_b"]),
                    "week_start": WEEK_START.isoformat(),
                    "status": "approved",
                },
            )
        assert response.status_code == 403

    async def test_an_own_reader_sees_only_their_row_over_http(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["pulseboard.time.timesheet.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/timesheets")
        assert response.status_code == 200
        rows = response.json()["items"]
        assert [r["person_id"] for r in rows] == ["p01"]

    async def test_the_grid_matches_the_spa_shape(self, client, org_id, data) -> None:
        """One row per person, seven cells Monday-first, hours as decimals, day sums."""
        principal = principal_for(org_id, ["pulseboard.time.timesheet.read:ORG"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/timesheets?week={WEEK_START.isoformat()}")
        assert response.status_code == 200
        rows = {r["person_id"]: r for r in response.json()["items"]}
        assert set(rows) == {"p01", "p02"}
        row = rows["p01"]
        assert row["week_start"] == WEEK_START.isoformat()
        assert row["status"] == "submitted"
        assert len(row["cells"]) == 7
        assert row["cells"][0]["date"] == WEEK_START.isoformat()
        assert row["cells"][0] == {
            "date": WEEK_START.isoformat(),
            "entered_hours": 4.0,
            "billable_hours": 3.0,
        }
        assert row["cells"][1]["entered_hours"] == 4.5
        # A day with no entries is an explicit zero cell, not a hole in the grid.
        assert row["cells"][6]["entered_hours"] == 0.0

    async def test_summary_rolls_up_the_week(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.time.timesheet.read:ORG"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/timesheets/summary?week={WEEK_START.isoformat()}")
        assert response.status_code == 200
        body = response.json()
        assert body["week_start"] == WEEK_START.isoformat()
        assert body["entered_hours"] == 4 + 4.5 + 6 + 1.5
        assert body["billable_hours"] == 3 + 4 + 5
        assert body["capacity_hours"] == 20 + 30
        # 12h billable of 50h capacity = 24.0%
        assert body["utilization_pct"] == 24.0
        assert len(body["series"]) == 12
        assert body["series"][-1]["value"] == 24.0


# --------------------------------------------------------------------- validation


class TestValidation:
    async def test_a_week_must_start_on_a_monday(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "pulseboard.time.timesheet.write", Scope.ORG)
        with pytest.raises(ValidationProblem, match="Monday"):
            async with session.begin():
                await services.record_week(
                    session,
                    ctx,
                    person_id=uuid.uuid4(),
                    person_ref="p09",
                    week_start=WEEK_START + timedelta(days=2),
                )

    async def test_an_entry_outside_the_week_is_refused(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "pulseboard.time.timesheet.write", Scope.ORG)
        with pytest.raises(ValidationProblem, match="outside the week"):
            async with session.begin():
                await services.record_week(
                    session,
                    ctx,
                    person_id=uuid.uuid4(),
                    person_ref="p09",
                    week_start=WEEK_START,
                    entries=[(WEEK_START + timedelta(days=9), None, 60, 60)],
                )

    async def test_billable_beyond_entered_is_refused(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "pulseboard.time.timesheet.write", Scope.ORG)
        with pytest.raises(ValidationProblem, match="billable"):
            async with session.begin():
                await services.record_week(
                    session,
                    ctx,
                    person_id=uuid.uuid4(),
                    person_ref="p09",
                    week_start=WEEK_START,
                    entries=[(WEEK_START, None, 60, 120)],
                )

    async def test_rerecording_replaces_rather_than_duplicates(self, session, org_id, data) -> None:
        """The same person-week recorded twice holds the second recording's entries only."""
        ctx = ctx_for(org_id, "pulseboard.time.timesheet.write", Scope.ORG)
        async with session.begin():
            sheet = await services.record_week(
                session,
                ctx,
                person_id=data["subject_a"],
                person_ref="p01",
                week_start=WEEK_START,
                status="approved",
                capacity_minutes=20 * 60,
                entries=[(WEEK_START, "PB-01", 8 * 60, 8 * 60)],
            )
        entries = await selectors.entries_by_sheet(session, org_id, [sheet.id])
        rows = entries.get(sheet.id, [])
        assert len(rows) == 1
        assert rows[0].entered_minutes == 8 * 60
        assert sheet.status == "approved"

    async def test_recording_stages_an_audit_row_and_an_outbox_event(
        self, session, org_id, data
    ) -> None:
        """The change, its audit row and its event commit together — nothing publishes inline."""
        from sqlalchemy import func, select

        from app.models import AuditLog, OutboxEvent

        ctx = ctx_for(org_id, "pulseboard.time.timesheet.write", Scope.ORG)
        async with session.begin():
            sheet = await services.record_week(
                session,
                ctx,
                person_id=uuid.uuid4(),
                person_ref="p05",
                week_start=WEEK_START,
                entries=[(WEEK_START, "PB-01", 60, 60)],
            )
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "timesheet.recorded", AuditLog.entity_id == sheet.id)
            )
        ).scalar_one()
        events = (
            (await session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == sheet.id)))
            .scalars()
            .all()
        )
        assert audits == 1
        assert len(events) == 1
        assert events[0].event_type == "pulseboard.time.timesheet.recorded"
        assert events[0].published_at is None
