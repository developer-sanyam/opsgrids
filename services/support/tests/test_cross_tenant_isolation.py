"""Cross-tenant isolation, against real PostgreSQL as the NOBYPASSRLS app role.

Row-level security is the net; the explicit `organization_id` filter in every selector is the
tightrope. Both are proven here: a second organization's rows are seeded through a BYPASSRLS
admin session, and the app session — bound to the first organization — must not see them, even
on a query with no explicit filter, and must not be able to write into the other tenant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app import selectors
from app.models import Ticket
from tests.conftest import seed_queue

pytestmark = pytest.mark.integration


async def _seed_other_org(admin_session, other_org: uuid.UUID) -> None:
    """Two tickets belonging to a *different* tenant, inserted with BYPASSRLS."""
    async with admin_session.begin():
        for n in (1, 2):
            admin_session.add(
                Ticket(
                    organization_id=other_org,
                    number=f"TK-900{n}",
                    subject="Another tenant's ticket",
                    requester_name="Someone Else",
                    requester_email="else@other.example",
                    requester_company="Other Corp",
                    requester_kind="customer",
                    priority="normal",
                    status="open",
                    channel="email",
                    tags=[],
                    sla_breached=False,
                    created_at=datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
                    updated_at=datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
                )
            )


class TestIsolation:
    async def test_selector_sees_only_its_own_tenant(
        self, session, admin_session, org_id, queue
    ) -> None:
        other_org = uuid.uuid4()
        await _seed_other_org(admin_session, other_org)

        mine = await selectors.all_tickets(session, org_id)
        numbers = {t.number for t in mine}
        assert numbers == {"TK-0001", "TK-0002", "TK-0003"}
        assert not any(n.startswith("TK-900") for n in numbers)

    async def test_rls_hides_other_tenants_even_without_an_explicit_filter(
        self, session, admin_session, org_id, queue
    ) -> None:
        """The net under the tightrope: a bare count with no `organization_id` predicate still
        returns only the bound tenant's rows, because RLS ANDs its qual into every query."""
        other_org = uuid.uuid4()
        await _seed_other_org(admin_session, other_org)

        total = (await session.execute(select(func.count()).select_from(Ticket))).scalar_one()
        assert total == 3  # only this org's three, never the other tenant's two

    async def test_detail_lookup_cannot_reach_another_tenant(
        self, session, admin_session, org_id, queue
    ) -> None:
        other_org = uuid.uuid4()
        await _seed_other_org(admin_session, other_org)
        # A number that exists — for the *other* tenant — resolves to nothing here.
        assert await selectors.get_ticket(session, org_id, "TK-9001") is None

    async def test_write_check_blocks_inserting_into_another_tenant(self, session, org_id) -> None:
        """RLS `WITH CHECK` refuses a row stamped with a foreign organization id, even though the
        app role is the one attempting it."""
        other_org = uuid.uuid4()
        session.add(
            Ticket(
                organization_id=other_org,
                number="TK-EVIL",
                subject="Planted",
                requester_name="x",
                requester_email="x@x.example",
                requester_company="x",
                requester_kind="customer",
                priority="normal",
                status="new",
                channel="email",
                tags=[],
                sla_breached=False,
                created_at=datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
                updated_at=datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
            )
        )
        with pytest.raises(Exception):  # noqa: B017 - the driver raises a RLS violation
            await session.flush()


async def test_two_orgs_seed_independently(session, admin_session, org_id) -> None:
    """Both tenants can hold a ticket numbered the same — uniqueness is per organization."""
    await seed_queue(session, org_id)
    other_org = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            Ticket(
                organization_id=other_org,
                number="TK-0001",  # same number as this org's — legal across tenants
                subject="Other tenant, same number",
                requester_name="y",
                requester_email="y@y.example",
                requester_company="y",
                requester_kind="customer",
                priority="low",
                status="new",
                channel="web",
                tags=[],
                sla_breached=False,
                created_at=datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
                updated_at=datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
            )
        )
    mine = await selectors.get_ticket(session, org_id, "TK-0001")
    assert mine is not None
    assert mine.organization_id == org_id
