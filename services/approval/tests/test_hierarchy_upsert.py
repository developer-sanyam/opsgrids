"""The version-gated upsert (ADR-0006 §2, data model §1.1) and cross-tenant isolation.

Exercises `app.services.upsert_hierarchy_node` directly — the same function the consumer and the
backfill both call — against real PostgreSQL, under the **app** role, so row-level security is
live rather than assumed.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import HierarchyNodeWrite, upsert_hierarchy_node, upsert_hierarchy_nodes_bulk

pytestmark = pytest.mark.integration


async def _upsert(
    session: AsyncSession,
    org_id: uuid.UUID,
    employee_id: uuid.UUID,
    *,
    version: int,
    display_name: str = "Ananya Rao",
    status: str = "active",
) -> bool:
    return await upsert_hierarchy_node(
        session,
        organization_id=org_id,
        employee_id=employee_id,
        reporting_manager_id=None,
        dotted_line_manager_id=None,
        status=status,
        display_name=display_name,
        employee_no="EMP0001",
        location_code="MUM",
        source_version=version,
        source_occurred_at=datetime.now(UTC),
    )


async def _stored_version(session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID) -> int:
    row = (
        await session.execute(
            text(
                "SELECT source_version, display_name FROM approval.hierarchy_nodes "
                "WHERE organization_id = :org AND employee_id = :emp"
            ),
            {"org": str(org_id), "emp": str(employee_id)},
        )
    ).one()
    return row[0]


class TestVersionGate:
    async def test_the_first_write_is_an_insert(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        async with session.begin():
            applied = await _upsert(session, org_id, employee_id, version=1)
        assert applied is True
        assert await _stored_version(session, org_id, employee_id) == 1

    async def test_a_newer_version_is_applied(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        async with session.begin():
            await _upsert(session, org_id, employee_id, version=1, display_name="Old Name")
        async with session.begin():
            applied = await _upsert(
                session, org_id, employee_id, version=2, display_name="New Name"
            )
        assert applied is True
        row = (
            await session.execute(
                text(
                    "SELECT source_version, display_name FROM approval.hierarchy_nodes "
                    "WHERE organization_id = :org AND employee_id = :emp"
                ),
                {"org": str(org_id), "emp": str(employee_id)},
            )
        ).one()
        assert row[0] == 2
        assert row[1] == "New Name"

    async def test_an_older_version_is_dropped(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        async with session.begin():
            await _upsert(session, org_id, employee_id, version=5, display_name="Current")
        async with session.begin():
            applied = await _upsert(session, org_id, employee_id, version=3, display_name="Stale")
        assert applied is False
        assert await _stored_version(session, org_id, employee_id) == 5

    async def test_an_equal_version_is_dropped_as_a_duplicate(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        employee_id = uuid.uuid4()
        async with session.begin():
            await _upsert(session, org_id, employee_id, version=4, display_name="First")
        async with session.begin():
            applied = await _upsert(
                session, org_id, employee_id, version=4, display_name="Redelivered"
            )
        assert applied is False
        row = (
            await session.execute(
                text(
                    "SELECT display_name FROM approval.hierarchy_nodes "
                    "WHERE organization_id = :org AND employee_id = :emp"
                ),
                {"org": str(org_id), "emp": str(employee_id)},
            )
        ).one()
        assert row[0] == "First"


class TestUpdatedAtMovesOnConflict:
    async def test_updated_at_advances_when_a_newer_version_is_applied(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """data model §1.1 specifies `updated_at` moves with every applied change.
        `on_conflict_do_update`'s `set_` is taken literally — it does not consult
        `TimestampMixin.updated_at`'s column-level `onupdate=func.now()` — so omitting it left
        the column pinned at its insert-time value forever, indistinguishable from a row nothing
        had ever touched again."""
        employee_id = uuid.uuid4()

        async def _updated_at() -> datetime:
            row = (
                await session.execute(
                    text(
                        "SELECT updated_at FROM approval.hierarchy_nodes "
                        "WHERE organization_id = :org AND employee_id = :emp"
                    ),
                    {"org": str(org_id), "emp": str(employee_id)},
                )
            ).scalar_one()
            return row  # type: ignore[no-any-return]

        async with session.begin():
            await _upsert(session, org_id, employee_id, version=1)
        async with session.begin():
            first = await _updated_at()

        # A real gap, not just two round trips: `now()` is the transaction start time, and two
        # transactions issued back-to-back could otherwise land in the same database tick.
        await asyncio.sleep(0.05)

        async with session.begin():
            await _upsert(session, org_id, employee_id, version=2)
        async with session.begin():
            second = await _updated_at()

        assert second > first


class TestBulkUpsert:
    async def test_a_batch_with_a_new_row_and_a_stale_row_applies_only_the_new_one(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The multi-row form `app/backfill.py` batches a page through: the version gate applies
        per row inside one statement, exactly as `upsert_hierarchy_node` applies it per call."""
        fresh, stale_target = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            await _upsert(session, org_id, stale_target, version=9, display_name="Current")

        async with session.begin():
            applied = await upsert_hierarchy_nodes_bulk(
                session,
                organization_id=org_id,
                nodes=[
                    HierarchyNodeWrite(
                        employee_id=fresh,
                        reporting_manager_id=None,
                        dotted_line_manager_id=None,
                        status="active",
                        display_name="New",
                        employee_no="EMP0002",
                        location_code="MUM",
                        source_version=1,
                        source_occurred_at=datetime.now(UTC),
                    ),
                    HierarchyNodeWrite(
                        employee_id=stale_target,
                        reporting_manager_id=None,
                        dotted_line_manager_id=None,
                        status="active",
                        display_name="Stale",
                        employee_no="EMP0001",
                        location_code="MUM",
                        source_version=3,
                        source_occurred_at=datetime.now(UTC),
                    ),
                ],
            )

        assert applied == 1
        assert await _stored_version(session, org_id, fresh) == 1
        assert await _stored_version(session, org_id, stale_target) == 9

    async def test_an_empty_batch_is_a_no_op(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with session.begin():
            applied = await upsert_hierarchy_nodes_bulk(session, organization_id=org_id, nodes=[])
        assert applied == 0

    async def test_an_unknown_status_in_the_batch_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        with pytest.raises(ValueError, match="unknown hierarchy status"):
            async with session.begin():
                await upsert_hierarchy_nodes_bulk(
                    session,
                    organization_id=org_id,
                    nodes=[
                        HierarchyNodeWrite(
                            employee_id=uuid.uuid4(),
                            reporting_manager_id=None,
                            dotted_line_manager_id=None,
                            status="not-a-real-status",
                            display_name="Someone",
                            employee_no="EMP0003",
                            location_code=None,
                            source_version=1,
                            source_occurred_at=datetime.now(UTC),
                        )
                    ],
                )


class TestCrossTenantIsolation:
    async def test_one_organizations_session_cannot_see_anothers_hierarchy_node(
        self,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        employee_id = uuid.uuid4()
        async with admin_session.begin():
            await upsert_hierarchy_node(
                admin_session,
                organization_id=other_org,
                employee_id=employee_id,
                reporting_manager_id=None,
                dotted_line_manager_id=None,
                status="active",
                display_name="Someone Else's Employee",
                employee_no="EMP9999",
                location_code=None,
                source_version=1,
                source_occurred_at=datetime.now(UTC),
            )

        row = (
            await session.execute(
                text(
                    "SELECT 1 FROM approval.hierarchy_nodes "
                    "WHERE organization_id = :org AND employee_id = :emp"
                ),
                {"org": str(other_org), "emp": str(employee_id)},
            )
        ).first()
        assert row is None, "a tenant-bound session must not see another organization's row"

    async def test_writing_a_row_for_another_organization_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        """`WITH CHECK` — not just `USING` — is what stops an insert naming another tenant."""
        with pytest.raises(DBAPIError):
            async with session.begin():
                await upsert_hierarchy_node(
                    session,
                    organization_id=other_org,
                    employee_id=uuid.uuid4(),
                    reporting_manager_id=None,
                    dotted_line_manager_id=None,
                    status="active",
                    display_name="Cross Tenant",
                    employee_no="EMP0002",
                    location_code=None,
                    source_version=1,
                    source_occurred_at=datetime.now(UTC),
                )
