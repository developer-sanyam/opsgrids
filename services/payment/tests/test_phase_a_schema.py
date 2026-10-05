"""The Phase A revision, as the database enforces it (ADR-0029): checkout charges, the poll index
and the sweep reader's column grant.

The migration is additive, with server defaults and no backfill: every existing charge is a `debit`,
which is exactly what it always was. The round trip (`upgrade head -> downgrade base -> upgrade
head`) is a gate (`make migration-roundtrip`); the refusal to downgrade while a checkout exists is
pinned here at the SQL it relies on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from platform_core.db import get_outbox_sessionmaker
from tests.helpers import rows

pytestmark = pytest.mark.integration


async def _insert(admin: AsyncSession, **over: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "org": uuid.uuid4(),
        "provider": "razorpay",
        "amount": 49_900,
        "currency": "INR",
        "state": "created",
        "key": f"k-{uuid.uuid4()}",
        "flow": "debit",
        "payee": "platform",
        "invoice": None,
        "expires": None,
        "ref_type": None,
        "ref_id": None,
        "account": None,
    }
    values.update(over)
    async with admin.begin():
        await admin.execute(
            text(
                "INSERT INTO payment.charges (id, organization_id, provider, amount_minor, "
                "currency, state, idempotency_key, flow, payee, invoice_id, checkout_expires_at, "
                "reference_type, reference_id, merchant_account_id) "
                "VALUES (:id, :org, :provider, :amount, :currency, :state, :key, :flow, :payee, "
                ":invoice, :expires, :ref_type, :ref_id, :account)"
            ),
            values,
        )
    return values["id"]  # type: ignore[no-any-return]


SOON = datetime.now(UTC) + timedelta(days=1)


class TestFlow:
    async def test_a_charge_with_no_flow_is_a_debit(self, admin_session: AsyncSession) -> None:
        async with admin_session.begin():
            charge_id = uuid.uuid4()
            await admin_session.execute(
                text(
                    "INSERT INTO payment.charges (id, organization_id, provider, amount_minor, "
                    "currency, state, idempotency_key) "
                    "VALUES (:id, :org, 'razorpay', 1, 'INR', 'created', :k)"
                ),
                {"id": charge_id, "org": uuid.uuid4(), "k": "legacy"},
            )
        (row,) = await rows(
            admin_session,
            "SELECT flow, checkout_expires_at FROM payment.charges WHERE id = :id",
            id=charge_id,
        )
        assert row == {"flow": "debit", "checkout_expires_at": None}

    async def test_only_debit_and_checkout_exist(self, admin_session: AsyncSession) -> None:
        with pytest.raises(IntegrityError, match="ck_charges_flow"):
            # (With an expiry and a platform payee, so only the flow CHECK can be the one to fail.)
            await _insert(admin_session, flow="subscription", expires=SOON)

    async def test_a_checkout_is_a_platform_charge_with_an_expiry(
        self, admin_session: AsyncSession
    ) -> None:
        await _insert(admin_session, flow="checkout", expires=SOON, invoice=uuid.uuid4())
        with pytest.raises(IntegrityError, match="ck_charges_checkout_flow_shape"):
            await _insert(admin_session, flow="checkout", expires=None)
        with pytest.raises(IntegrityError, match="ck_charges_checkout_flow_shape"):
            await _insert(
                admin_session,
                flow="checkout",
                expires=SOON,
                payee="merchant",
                ref_type="operations.order",
                ref_id=uuid.uuid4(),
                account=None,
            )


class TestOneOpenCheckoutPerInvoice:
    async def test_a_second_open_checkout_for_an_invoice_is_refused_by_the_database(
        self, admin_session: AsyncSession
    ) -> None:
        org, invoice = uuid.uuid4(), uuid.uuid4()
        await _insert(admin_session, org=org, invoice=invoice, flow="checkout", expires=SOON)
        with pytest.raises(IntegrityError, match="uq_charges_open_checkout"):
            await _insert(admin_session, org=org, invoice=invoice, flow="checkout", expires=SOON)

    async def test_a_closed_one_does_not_block_a_new_one_and_other_invoices_are_independent(
        self, admin_session: AsyncSession
    ) -> None:
        org, invoice = uuid.uuid4(), uuid.uuid4()
        await _insert(
            admin_session, org=org, invoice=invoice, flow="checkout", expires=SOON, state="failed"
        )
        await _insert(admin_session, org=org, invoice=invoice, flow="checkout", expires=SOON)
        await _insert(admin_session, org=org, invoice=uuid.uuid4(), flow="checkout", expires=SOON)
        await _insert(
            admin_session, org=uuid.uuid4(), invoice=invoice, flow="checkout", expires=SOON
        )

    async def test_debit_charges_of_one_invoice_are_not_limited(
        self, admin_session: AsyncSession
    ) -> None:
        org, invoice = uuid.uuid4(), uuid.uuid4()
        await _insert(admin_session, org=org, invoice=invoice)
        await _insert(admin_session, org=org, invoice=invoice)  # the dunning ladder's retries


class TestIndexesAndGrants:
    async def test_the_poll_index_exists_and_is_partial(self, admin_session: AsyncSession) -> None:
        (row,) = await rows(
            admin_session,
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'payment' AND indexname = 'ix_charges_poll'",
        )
        definition = row["indexdef"]
        assert (
            "(state, provider, flow)" in definition
            and "provider_order_id IS NOT NULL" in definition
        )

    async def test_the_discovery_role_may_read_flow_and_still_nothing_it_should_not(
        self, engines: None
    ) -> None:
        async with get_outbox_sessionmaker()() as s, s.begin():
            await s.execute(
                text("SELECT flow, provider_order_id, next_attempt_at FROM payment.charges LIMIT 1")
            )
        for column in ("amount_minor", "idempotency_key", "failure_reason", "checkout_expires_at"):
            async with get_outbox_sessionmaker()() as s:
                with pytest.raises(DBAPIError, match="permission denied"):
                    async with s.begin():
                        # `column` comes from the tuple above, never from input.
                        await s.execute(text(f"SELECT {column} FROM payment.charges LIMIT 1"))  # noqa: S608

    async def test_row_level_security_still_binds_the_app_role_on_the_new_columns(
        self, admin_session: AsyncSession, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        other = uuid.uuid4()
        await _insert(admin_session, org=other, flow="checkout", expires=SOON, invoice=uuid.uuid4())
        async with session.begin():
            visible = (
                await session.execute(
                    text("SELECT count(*) FROM payment.charges WHERE flow = 'checkout'")
                )
            ).scalar_one()
        assert visible == 0, "another tenant's checkout must be invisible to the app role"


class TestTheDowngradeGuard:
    async def test_the_count_the_downgrade_refuses_on_is_what_a_checkout_makes_it(
        self, admin_session: AsyncSession
    ) -> None:
        (before,) = await rows(
            admin_session, "SELECT count(*) AS n FROM payment.charges WHERE flow = 'checkout'"
        )
        await _insert(admin_session, flow="checkout", expires=SOON, invoice=uuid.uuid4())
        (after,) = await rows(
            admin_session, "SELECT count(*) AS n FROM payment.charges WHERE flow = 'checkout'"
        )
        assert after["n"] == before["n"] + 1


def test_the_revision_chains_from_the_previous_head() -> None:
    import importlib.util
    from pathlib import Path

    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    (path,) = versions.glob("c7d8e9f0a1b2_*.py")
    spec = importlib.util.spec_from_file_location("phase_a_revision", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "c7d8e9f0a1b2" and module.down_revision == "a8c1d2e3f4b5"
    assert callable(module.upgrade) and callable(module.downgrade)
