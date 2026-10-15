"""The contract every audit-log source is tested against, written once.

Each service that mounts `build_audit_router` runs these checks in its own test process (each
service owns a top-level `app`, so they cannot share one), against its own real schema, as the
**app** role with RLS on — and, for the cross-tenant check, as the BYPASSRLS admin role too, so a
query that relied on RLS alone to hide another organization fails here instead of passing.

Rows are seeded straight into the service's `audit_logs` under fresh organization ids and
removed afterwards, so the checks neither depend on nor disturb the service's own fixtures.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from platform_core.audit_read import (
    AUDIT_READ_PERMISSION,
    SENSITIVE_READ_ACTION,
    AuditFilters,
    audit_page_stmt,
)
from platform_core.auth import Principal, get_principal
from platform_core.config import ServiceSettings
from platform_core.db import get_session

#: A value that must never appear in any response, whatever field it was stored under.
REVEALED_VALUE = "+91 98765 43210"

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@dataclass
class Seeded:
    org: uuid.UUID
    other_org: uuid.UUID
    alice: uuid.UUID
    bob: uuid.UUID
    reveal_id: uuid.UUID
    other_org_row_id: uuid.UUID


@dataclass
class AuditReadContract:
    app: FastAPI
    settings: ServiceSettings
    audit_model: type[Any]
    service: str
    #: The entitlement the gateway gates this prefix on — carried on the principal so a service
    #: that also checks it locally answers as it would in production.
    product: str

    @property
    def path(self) -> str:
        return f"/api/v1/audit/{self.service}"

    # ------------------------------------------------------------------ plumbing

    @asynccontextmanager
    async def _admin(self) -> AsyncIterator[AsyncSession]:
        engine = create_async_engine(self.settings.admin_dsn)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                yield session
        finally:
            await engine.dispose()

    def principal(
        self, org: uuid.UUID, perms: list[str], *, sub: uuid.UUID | None = None
    ) -> Principal:
        return Principal(
            sub=sub or uuid.uuid4(),
            organization_id=org,
            membership_id=uuid.uuid4(),
            perms=perms,
            entitlements=[self.product],
        )

    @contextmanager
    def _as(self, principal: Principal) -> Iterator[None]:
        self.app.dependency_overrides[get_principal] = lambda: principal
        try:
            yield
        finally:
            self.app.dependency_overrides.pop(get_principal, None)

    async def get(self, principal: Principal, params: dict[str, str] | None = None) -> Response:
        """Call the route as `principal`, on an app-role (NOBYPASSRLS) session the route's own
        tenant chokepoint binds — exactly the production path behind the gateway."""
        engine = create_async_engine(self.settings.app_dsn)
        maker = async_sessionmaker(engine, expire_on_commit=False)

        async def _session() -> AsyncIterator[AsyncSession]:
            async with maker() as session:
                yield session

        previous = self.app.dependency_overrides.get(get_session)
        self.app.dependency_overrides[get_session] = _session
        try:
            with self._as(principal):
                transport = ASGITransport(app=self.app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    return await client.get(self.path, params=params or {})
        finally:
            if previous is None:
                self.app.dependency_overrides.pop(get_session, None)
            else:
                self.app.dependency_overrides[get_session] = previous
            await engine.dispose()

    def reader(self, org: uuid.UUID) -> Principal:
        return self.principal(org, [f"{AUDIT_READ_PERMISSION}:ORG"])

    @asynccontextmanager
    async def seeded(self) -> AsyncIterator[Seeded]:
        """Five rows in one organization and one in another, removed on exit."""
        org, other_org = uuid.uuid4(), uuid.uuid4()
        alice, bob = uuid.uuid4(), uuid.uuid4()
        reveal_id, other_row = uuid.uuid4(), uuid.uuid4()
        model = self.audit_model
        rows = [
            model(
                organization_id=org,
                actor_id=alice,
                action="member.invited",
                entity="membership",
                entity_id=uuid.uuid4(),
                after={"email": "new.hire@example.com", "role": "MEMBER"},
                at=T0,
            ),
            model(
                organization_id=org,
                actor_id=alice,
                action="contact.updated",
                entity="contact",
                entity_id=uuid.uuid4(),
                before={"title": "Engineer", "phone": REVEALED_VALUE},
                after={"title": "Lead engineer", "phone": REVEALED_VALUE},
                at=T0 + timedelta(hours=1),
            ),
            model(
                id=reveal_id,
                organization_id=org,
                actor_id=bob,
                action=SENSITIVE_READ_ACTION,
                entity="contact",
                entity_id=uuid.uuid4(),
                after={"fields": ["phone"], "session_id": "sid-123", "amr": ["pwd"]},
                at=T0 + timedelta(hours=2),
            ),
            model(
                organization_id=org,
                actor_id=None,
                acting_as="support@opsgrids.example",
                action="subscription.suspended",
                entity="subscription",
                entity_id=uuid.uuid4(),
                reason="Requested by the account owner",
                at=T0 + timedelta(days=1),
            ),
            model(
                organization_id=org,
                actor_id=bob,
                action="member.invited",
                entity="membership",
                entity_id=uuid.uuid4(),
                after={"email": "second.hire@example.com"},
                at=T0 + timedelta(days=2),
            ),
            model(
                id=other_row,
                organization_id=other_org,
                actor_id=uuid.uuid4(),
                action="member.invited",
                entity="membership",
                entity_id=uuid.uuid4(),
                after={"email": "victim@example.com"},
                at=T0 + timedelta(hours=3),
            ),
        ]
        async with self._admin() as admin, admin.begin():
            admin.add_all(rows)
        try:
            yield Seeded(org, other_org, alice, bob, reveal_id, other_row)
        finally:
            async with self._admin() as admin, admin.begin():
                await admin.execute(
                    delete(model).where(model.organization_id.in_([org, other_org]))
                )

    # ------------------------------------------------------------------ the checks

    async def check_lists_the_callers_org_newest_first(self) -> None:
        async with self.seeded() as s:
            response = await self.get(self.reader(s.org))
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["service"] == self.service
            assert body["product"] == self.product
            items = body["items"]
            assert len(items) == 5
            ats = [i["at"] for i in items]
            assert ats == sorted(ats, reverse=True)
            assert {i["service"] for i in items} == {self.service}
            assert body["next_cursor"] is None

    async def check_refuses_a_caller_without_the_permission(self) -> None:
        async with self.seeded() as s:
            nobody = self.principal(s.org, ["core.identity.membership.read:ORG"])
            assert (await self.get(nobody)).status_code == 403
            # The code at OWN scope is not enough either: the trail is organization-wide.
            own = self.principal(s.org, [f"{AUDIT_READ_PERMISSION}:OWN"])
            assert (await self.get(own)).status_code == 403

    async def check_never_returns_another_orgs_rows(self) -> None:
        async with self.seeded() as s:
            response = await self.get(self.reader(s.org))
            ids = {i["id"] for i in response.json()["items"]}
            assert str(s.other_org_row_id) not in ids
            assert "victim@example.com" not in response.text
            # The other organization's reader sees its one row and none of ours.
            theirs = (await self.get(self.reader(s.other_org))).json()["items"]
            assert [i["id"] for i in theirs] == [str(s.other_org_row_id)]
            # And the selector filters on the organization by itself: run as the BYPASSRLS
            # admin role, where RLS hides nothing, it still returns only the caller's rows.
            stmt = audit_page_stmt(
                self.audit_model,
                organization_id=s.org,
                filters=AuditFilters(),
                after=None,
                limit=100,
            )
            async with self._admin() as admin, admin.begin():
                rows = (await admin.execute(stmt)).scalars().all()
            assert rows and {r.organization_id for r in rows} == {s.org}

    async def check_a_reveal_shows_who_what_and_when_but_never_a_value(self) -> None:
        async with self.seeded() as s:
            response = await self.get(self.reader(s.org))
            assert REVEALED_VALUE not in response.text
            assert "new.hire@example.com" not in response.text
            assert "sid-123" not in response.text
            assert "support@opsgrids.example" not in response.text
            items = {i["id"]: i for i in response.json()["items"]}
            reveal = items[str(s.reveal_id)]
            assert reveal["sensitive"] is True
            assert reveal["actor_id"] == str(s.bob)
            assert reveal["fields"] == ["phone"]
            assert reveal["at"].startswith("2026-10-01T11:00:00")
            for item in items.values():
                assert "before" not in item and "after" not in item
            updated = next(i for i in items.values() if i["action"] == "contact.updated")
            # `phone` did not change, so it is not named; `title` did.
            assert updated["fields"] == ["title"]
            suspended = next(i for i in items.values() if i["action"] == "subscription.suspended")
            assert suspended["by_operator"] is True

    async def check_filters_and_pages(self) -> None:
        async with self.seeded() as s:
            reader = self.reader(s.org)

            by_actor = (await self.get(reader, {"actor_id": str(s.bob)})).json()["items"]
            assert {i["actor_id"] for i in by_actor} == {str(s.bob)}
            assert len(by_actor) == 2

            by_action = (await self.get(reader, {"action": "member.invited"})).json()["items"]
            assert len(by_action) == 2 and {i["action"] for i in by_action} == {"member.invited"}

            window = {
                "from": (T0 + timedelta(minutes=30)).isoformat(),
                "to": (T0 + timedelta(days=1)).isoformat(),
            }
            in_window = (await self.get(reader, window)).json()["items"]
            assert [i["action"] for i in in_window] == ["entity.sensitive_read", "contact.updated"]

            seen: list[str] = []
            cursor: str | None = None
            for _ in range(5):
                params = {"limit": "2", **({"cursor": cursor} if cursor else {})}
                page = (await self.get(reader, params)).json()
                seen += [i["id"] for i in page["items"]]
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            assert len(seen) == 5 and len(set(seen)) == 5

    async def check_rejects_bad_input(self) -> None:
        async with self.seeded() as s:
            reader = self.reader(s.org)
            assert (await self.get(reader, {"cursor": "not-a-cursor"})).status_code == 422
            backwards = {"from": (T0 + timedelta(days=1)).isoformat(), "to": T0.isoformat()}
            assert (await self.get(reader, backwards)).status_code == 422
            assert (await self.get(reader, {"from": "2026-10-01T00:00:00"})).status_code == 422
            assert (await self.get(reader, {"limit": "500"})).status_code == 422

    async def run_all(self) -> None:
        await self.check_lists_the_callers_org_newest_first()
        await self.check_refuses_a_caller_without_the_permission()
        await self.check_never_returns_another_orgs_rows()
        await self.check_a_reveal_shows_who_what_and_when_but_never_a_value()
        await self.check_filters_and_pages()
        await self.check_rejects_bad_input()
