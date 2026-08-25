"""Authorization, scope and validation — against real PostgreSQL.

Written as *negative* assertions wherever possible: proving a call is refused is worth more than
proving one succeeds. `ctx.apply` narrows the *list* and the write path's `assert_can_touch`
guards the *object*, because filtering the list and forgetting the object is the named
vulnerability. A user who can read only their own check-ins must also be refused posting
another's.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.main import app
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.errors import ForbiddenProblem
from tests.conftest import TODAY
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
            (await session.execute(selectors.entries_stmt(ctx, entry_date=TODAY))).scalars().all()
        )
        return {e.person_ref for e in rows}

    async def test_org_scope_sees_the_whole_wall(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "pulseboard.standup.entry.read", Scope.ORG)
        assert await self._refs(session, ctx) == {"p02", "p03"}

    async def test_own_scope_sees_only_your_own_entries(self, session, org_id, data) -> None:
        ctx = ctx_for(
            org_id, "pulseboard.standup.entry.read", Scope.OWN, subject_ref=data["subject_a"]
        )
        assert await self._refs(session, ctx) == {"p02"}

    async def test_own_scope_with_no_subject_sees_nothing(self, session, org_id, data) -> None:
        """OWN with nothing to own must match nothing, never everything."""
        ctx = ctx_for(org_id, "pulseboard.standup.entry.read", Scope.OWN, subject_ref=None)
        assert await self._refs(session, ctx) == set()

    async def test_location_scope_is_refused_rather_than_widened(
        self, session, org_id, data
    ) -> None:
        ctx = ctx_for(
            org_id,
            "pulseboard.standup.entry.read",
            Scope.LOCATION,
            subject_ref=data["subject_a"],
            locations=["BLR"],
        )
        with pytest.raises(ForbiddenProblem, match="LOCATION"):
            selectors.entries_stmt(ctx, entry_date=TODAY)


# --------------------------------------------------------------------- endpoint authz


class TestEndpointAuthorization:
    async def test_unauthenticated_is_rejected(self, client, org_id, data) -> None:
        response = await client.get("/api/v1/standups")
        assert response.status_code == 401

    async def test_missing_the_permission_is_forbidden(self, client, org_id, data) -> None:
        """Entitled to pulseboard, but holding no pulseboard permission at all."""
        principal = principal_for(org_id, [], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.get("/api/v1/standups")
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_without_the_product_entitlement_is_payment_required(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id,
            ["pulseboard.standup.entry.read:ORG"],
            subject_ref=data["subject_a"],
            entitlements=[],
        )
        with as_principal(principal):
            response = await client.get("/api/v1/standups")
        assert response.status_code == 402

    async def test_a_reader_cannot_post_an_entry(self, client, org_id, data) -> None:
        """Holding entry.read but not entry.write cannot post — the guard is by name."""
        principal = principal_for(
            org_id, ["pulseboard.standup.entry.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/standups",
                json={
                    "person_ref": "p05",
                    "person_id": str(uuid.uuid4()),
                    "date": TODAY.isoformat(),
                    "yesterday": "x",
                    "today": "y",
                },
            )
        assert response.status_code == 403

    async def test_an_own_writer_cannot_post_someone_elses_entry(
        self, client, org_id, data
    ) -> None:
        """OWN-scoped write is refused at the object level for another person's check-in."""
        principal = principal_for(
            org_id, ["pulseboard.standup.entry.write:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/standups",
                json={
                    "person_ref": "p03",
                    "person_id": str(data["subject_b"]),
                    "date": TODAY.isoformat(),
                    "yesterday": "forged",
                    "today": "forged",
                },
            )
        assert response.status_code == 403

    async def test_an_own_writer_may_post_their_own_entry(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["pulseboard.standup.entry.write:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                "/api/v1/standups",
                json={
                    "person_ref": "p02",
                    "person_id": str(data["subject_a"]),
                    "date": TODAY.isoformat(),
                    "yesterday": "Repaired the flaky pipeline.",
                    "today": "Pairing on the migration.",
                    "blockers": None,
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["person_id"] == "p02"
        assert body["blockers"] is None

    async def test_an_own_reader_sees_only_their_entries_over_http(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["pulseboard.standup.entry.read:OWN"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get("/api/v1/standups")
        assert response.status_code == 200
        rows = response.json()["items"]
        assert [r["person_id"] for r in rows] == ["p02"]

    async def test_the_wall_matches_the_spa_shape(self, client, org_id, data) -> None:
        """The wire entry is exactly the SPA's `StandupEntry`, blockers null when clear."""
        principal = principal_for(org_id, ["pulseboard.standup.entry.read:ORG"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/standups?date={TODAY.isoformat()}")
        assert response.status_code == 200
        rows = {r["person_id"]: r for r in response.json()["items"]}
        assert set(rows) == {"p02", "p03"}
        blocked = rows["p02"]
        assert set(blocked) == {"id", "person_id", "date", "yesterday", "today", "blockers"}
        assert blocked["date"] == TODAY.isoformat()
        assert blocked["blockers"] == "Waiting on Stellar's sandbox credentials."
        assert rows["p03"]["blockers"] is None

    async def test_the_blocked_filter_narrows_the_wall(self, client, org_id, data) -> None:
        principal = principal_for(org_id, ["pulseboard.standup.entry.read:ORG"])
        with as_principal(principal):
            response = await client.get("/api/v1/standups?blocked=true")
        assert response.status_code == 200
        rows = response.json()["items"]
        assert [r["person_id"] for r in rows] == ["p02"]


# --------------------------------------------------------------------- write semantics


class TestWriteSemantics:
    async def test_reposting_replaces_rather_than_duplicates(self, session, org_id, data) -> None:
        """The same person-day posted twice holds the second posting only."""
        ctx = ctx_for(org_id, "pulseboard.standup.entry.write", Scope.ORG)
        async with session.begin():
            entry = await services.post_entry(
                session,
                ctx,
                person_id=data["subject_a"],
                person_ref="p02",
                entry_date=TODAY,
                yesterday="Rewritten.",
                today="Also rewritten.",
                blockers=None,
            )
        read = ctx_for(org_id, "pulseboard.standup.entry.read", Scope.ORG)
        rows = await selectors.entries_for(session, read, TODAY)
        mine = [e for e in rows if e.person_ref == "p02"]
        assert len(mine) == 1
        assert mine[0].id == entry.id
        assert mine[0].yesterday == "Rewritten."
        assert mine[0].blockers is None  # the blocker was cleared by the repost

    async def test_posting_stages_an_audit_row_and_an_outbox_event(
        self, session, org_id, data
    ) -> None:
        """The change, its audit row and its event commit together — nothing publishes inline."""
        from sqlalchemy import func, select

        from app.models import AuditLog, OutboxEvent

        ctx = ctx_for(org_id, "pulseboard.standup.entry.write", Scope.ORG)
        async with session.begin():
            entry = await services.post_entry(
                session,
                ctx,
                person_id=uuid.uuid4(),
                person_ref="p07",
                entry_date=TODAY,
                yesterday="a",
                today="b",
            )
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "standup.posted", AuditLog.entity_id == entry.id)
            )
        ).scalar_one()
        events = (
            (await session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == entry.id)))
            .scalars()
            .all()
        )
        assert audits == 1
        assert len(events) == 1
        assert events[0].event_type == "pulseboard.standup.entry.posted"
        assert events[0].published_at is None
