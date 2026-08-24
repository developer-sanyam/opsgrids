"""Append-only at the database — `refunds` and `provider_events` cannot be rewritten.

The reconciliation record and the refund record are financial truth (ADR-0000 §9): a correction is
a new row, never an edit. A `BEFORE UPDATE OR DELETE` trigger enforces that below the application,
so even a raw statement that bypasses every service check is refused — and the guarantee survives
`apply_schema_grants` re-granting UPDATE/DELETE on every migration, because a trigger is
independent of privileges (unlike a REVOKE, which the re-grant would undo).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed_charge(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    charge_id = uuid.uuid4()
    async with session.begin():
        await session.execute(
            text(
                "INSERT INTO payment.charges "
                "(id, organization_id, provider, amount_minor, currency, state, idempotency_key) "
                "VALUES (:id, :org, 'razorpay', 1000, 'USD', 'captured', :key)"
            ),
            {"id": str(charge_id), "org": str(org_id), "key": uuid.uuid4().hex},
        )
    return charge_id


class TestRefundsAppendOnly:
    async def test_update_on_a_refund_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        charge_id = await _seed_charge(session, org_id)
        async with session.begin():
            await session.execute(
                text(
                    "INSERT INTO payment.refunds "
                    "(id, organization_id, charge_id, provider, amount_minor, currency, state) "
                    "VALUES (gen_random_uuid(), :org, :cid, 'razorpay', 500, 'USD', 'processed')"
                ),
                {"org": str(org_id), "cid": str(charge_id)},
            )

        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    text("UPDATE payment.refunds SET reason = 'edited' WHERE charge_id = :cid"),
                    {"cid": str(charge_id)},
                )

    async def test_delete_on_a_refund_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        charge_id = await _seed_charge(session, org_id)
        async with session.begin():
            await session.execute(
                text(
                    "INSERT INTO payment.refunds "
                    "(id, organization_id, charge_id, provider, amount_minor, currency, state) "
                    "VALUES (gen_random_uuid(), :org, :cid, 'razorpay', 500, 'USD', 'processed')"
                ),
                {"org": str(org_id), "cid": str(charge_id)},
            )

        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    text("DELETE FROM payment.refunds WHERE charge_id = :cid"),
                    {"cid": str(charge_id)},
                )


class TestProviderEventsAppendOnly:
    async def _seed_event(self, session: AsyncSession, org_id: uuid.UUID, event_id: str) -> None:
        async with session.begin():
            await session.execute(
                text(
                    "INSERT INTO payment.provider_events "
                    "(id, organization_id, provider, provider_event_id, event_name, payload) "
                    "VALUES (gen_random_uuid(), :org, 'razorpay', :eid, 'payment.captured', "
                    "'{}'::jsonb)"
                ),
                {"org": str(org_id), "eid": event_id},
            )

    async def test_update_on_the_inbox_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await self._seed_event(session, org_id, "evt_upd")
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE payment.provider_events SET event_name = 'x' "
                        "WHERE provider_event_id = 'evt_upd'"
                    )
                )

    async def test_delete_on_the_inbox_is_refused(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await self._seed_event(session, org_id, "evt_del")
        with pytest.raises(Exception, match="append-only"):
            async with session.begin():
                await session.execute(
                    text("DELETE FROM payment.provider_events WHERE provider_event_id = 'evt_del'")
                )
