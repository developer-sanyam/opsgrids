"""The charge poller (`app/checkout/cron.py`, ADR-0029): the only way a platform charge learns an
outcome while the provider webhooks are private, and the sweep exclusion that makes a checkout
safe to leave `created`.

What these pin:

* **What it asks about** — a checkout, and an authorized debit — and what it never does: a plain
  `created` debit is the stuck-charge sweep's, and a checkout is **never** the sweep's (it would
  "retry" a payment nobody made, fail it `attempts_exhausted` and suspend a customer who has not
  yet paid).
* **Claim → call → record**, routed by the **row's** provider.
* **Expiry** — an unpaid checkout past its expiry is closed with **no** `commerce.payment.failed`,
  but only if the provider was successfully asked, and a late capture on it is still recorded.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.checkout import services as checkout_services
from app.checkout.cron import ChargePoller
from app.cron import StuckChargeSweep
from app.providers.base import PaymentSnapshot
from app.providers.razorpay import RazorpayProvider
from platform_core.db import get_outbox_sessionmaker, session_scope
from tests import checkout_support as cs
from tests.helpers import audit_rows, emitted, force_charge, rows

pytestmark = pytest.mark.integration

LONG_AGO = timedelta(hours=1)


def _poller(resolver: Any) -> ChargePoller:
    return ChargePoller(
        discovery_factory=get_outbox_sessionmaker(),
        work_scope=lambda organization_id: session_scope(organization_id=organization_id),
        provider_resolver=resolver,
        poll_interval=30.0,
    )


async def _open(
    client: AsyncClient, org_id: uuid.UUID, provider: Any, **seed: Any
) -> tuple[uuid.UUID, dict[str, Any]]:
    cs.install(provider)
    invoice_id, _ = await cs.seed_invoice(client, org_id, **seed)
    response = await cs.open_checkout(client, org_id, invoice_id, cust=cs.customer())
    assert response.status_code == 201, response.text
    return invoice_id, response.json()


async def _row(admin: AsyncSession, charge_id: str | uuid.UUID) -> dict[str, Any]:
    found = await rows(
        admin,
        "SELECT state, failure_reason, provider_payment_id, next_attempt_at, flow "
        "FROM payment.charges WHERE id = :id",
        id=uuid.UUID(str(charge_id)),
    )
    return found[0]


async def _discover(now: datetime) -> list[uuid.UUID]:
    async with get_outbox_sessionmaker()() as session, session.begin():
        due = await selectors.due_pollable_charges(session, now=now)
    return [charge_id for _, charge_id in due]


class TestWhatItAsksAbout:
    async def test_checkouts_and_authorized_debits_only(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        live = cs.razorpay_live()
        _, open_checkout = await _open(client, org_id, live)
        _, captured = await _open(client, org_id, live)
        await force_charge(admin_session, captured["charge_id"], state="captured")
        _, no_order = await _open(client, org_id, live)
        await force_charge(admin_session, no_order["charge_id"], provider_order_id=None)
        _, backed_off = await _open(client, org_id, live)
        await force_charge(
            admin_session,
            backed_off["charge_id"],
            next_attempt_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        # Stub debits are `authorized` with an Order: the poller's second kind.
        cs.install(RazorpayProvider(key_id=None, key_secret=None, webhook_secret="x"))
        _, authorized = await cs.seed_invoice(client, org_id)
        assert authorized["state"] == "authorized"
        # (The call's own backoff, 5 minutes, is still on it: the first poll of a debit waits for
        # that. Cleared here so the test is about *what* is asked, not *when*.)
        await force_charge(admin_session, authorized["id"], next_attempt_at=None)
        _, created = await cs.seed_invoice(client, org_id)
        await force_charge(admin_session, created["id"], state="created", provider_payment_id=None)
        # A debit that failed `no_mandate` has no Order and no future.
        cs.install(live)
        _, no_mandate = await cs.seed_invoice(client, org_id)
        assert no_mandate["state"] == "failed"

        due = set(await _discover(datetime.now(UTC)))

        assert uuid.UUID(open_checkout["charge_id"]) in due
        assert uuid.UUID(authorized["id"]) in due
        for left_out in (
            captured["charge_id"],  # terminal
            no_order["charge_id"],  # nothing to ask about
            backed_off["charge_id"],  # asked a moment ago
            created["id"],  # a `created` debit is the stuck-charge sweep's
            no_mandate["id"],
        ):
            assert uuid.UUID(str(left_out)) not in due

    async def test_discovery_reads_only_the_columns_it_is_granted(
        self, client: AsyncClient, org_id: uuid.UUID, engines: None
    ) -> None:
        """The poller's cross-tenant read runs on the BYPASSRLS role through the same column grant
        as the stuck-charge sweep (`SWEEP_READER_CHARGE_COLUMNS`); `flow` is in it. A column outside
        it would fail with 42501 rather than widen the bypass."""
        assert "flow" in selectors.SWEEP_READER_CHARGE_COLUMNS
        await _discover(datetime.now(UTC))


class TestAPass:
    async def test_a_paid_checkout_is_captured_and_announced_once(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        paid = cs.razorpay_pay(live, out["client_action"]["order_id"], status="captured")

        report = await _poller(lambda name: live).tick()

        assert (report.considered, report.polled, report.captured) == (1, 1, 1)
        row = await _row(admin_session, out["charge_id"])
        assert row["state"] == "captured" and row["provider_payment_id"] == paid
        assert (await emitted(admin_session, org_id)).count("commerce.payment.captured") == 1
        again = await _poller(lambda name: live).tick()
        assert again.considered == 0  # terminal: not asked about again

    async def test_one_charge_is_not_asked_about_twice_inside_the_gap(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        poller = _poller(lambda name: live)
        first = await poller.tick()
        calls = len(live.fake.requests)
        second = await poller.tick()
        assert first.polled == 1 and second.considered == 0
        assert len(live.fake.requests) == calls
        assert (await _row(admin_session, out["charge_id"]))["next_attempt_at"] is not None
        # Once the gap has passed it is asked again.
        later = await poller.tick(now=datetime.now(UTC) + timedelta(minutes=2))
        assert later.polled == 1

    async def test_a_provider_that_cannot_be_asked_changes_nothing(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        live.fake.inject(httpx.ConnectError("down"))
        report = await _poller(lambda name: live).tick()
        assert (report.unchecked, report.polled, report.captured) == (1, 0, 0)
        assert (await _row(admin_session, out["charge_id"]))["state"] == "created"

    async def test_each_charge_is_asked_at_the_provider_it_was_created_under(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        razorpay, cashfree = cs.razorpay_live(), cs.cashfree_stub()
        _, rzp = await _open(client, org_id, razorpay)
        _, cf = await _open(client, org_id, cashfree)
        asked: list[str] = []

        def resolver(name: str) -> Any:
            asked.append(name)
            return {"razorpay": razorpay, "cashfree": cashfree}[name]

        cs.razorpay_pay(razorpay, rzp["client_action"]["order_id"], status="captured")
        cs.cashfree_pay(cashfree, cf["charge_id"], status="SUCCESS")
        report = await _poller(resolver).tick()

        assert sorted(asked) == ["cashfree", "razorpay"] and report.captured == 2
        assert (await _row(admin_session, rzp["charge_id"]))["state"] == "captured"
        assert (await _row(admin_session, cf["charge_id"]))["state"] == "captured"


class TestExpiry:
    async def test_an_unpaid_checkout_past_its_expiry_is_closed_and_announces_nothing(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        await force_charge(
            admin_session, out["charge_id"], checkout_expires_at=datetime.now(UTC) - LONG_AGO
        )
        before = await emitted(admin_session, org_id)

        report = await _poller(lambda name: live).tick()

        assert (report.expired, report.captured) == (1, 0)
        row = await _row(admin_session, out["charge_id"])
        assert (row["state"], row["failure_reason"]) == ("failed", "checkout_expired")
        assert await emitted(admin_session, org_id) == before
        audits = [
            (a["action"], a["acting_as"])
            for a in await audit_rows(admin_session, org_id)
            if str(a["entity_id"]) == out["charge_id"]
        ]
        assert ("payment.charge.checkout_expired", "checkout-poller") in audits

    async def test_it_is_not_closed_on_a_question_that_could_not_be_asked(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        await force_charge(
            admin_session, out["charge_id"], checkout_expires_at=datetime.now(UTC) - LONG_AGO
        )
        live.fake.inject(httpx.ConnectError("down"))
        report = await _poller(lambda name: live).tick()
        assert (report.unchecked, report.expired) == (1, 0)
        assert (await _row(admin_session, out["charge_id"]))["state"] == "created"

    async def test_a_stub_never_expires_a_checkout_a_live_wire_opened(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        """The rollback (`PAYMENT_WIRE=stub`) with a checkout still open at the real provider. The
        stub holds no such Order, and "no payments" would be a lie it cannot back: the customer
        may have paid. It must say it cannot answer, and the charge must stay open."""
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        await force_charge(
            admin_session, out["charge_id"], checkout_expires_at=datetime.now(UTC) - LONG_AGO
        )
        stub = RazorpayProvider(
            key_id=None, key_secret=None, webhook_secret="x", platform_billing=True
        )

        report = await _poller(lambda name: stub).tick()

        assert (report.unchecked, report.expired, report.captured) == (1, 0, 0)
        row = await _row(admin_session, out["charge_id"])
        assert (row["state"], row["failure_reason"]) == ("created", None)

    async def test_one_paid_before_it_was_closed_is_captured_not_expired(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        await force_charge(
            admin_session, out["charge_id"], checkout_expires_at=datetime.now(UTC) - LONG_AGO
        )
        cs.razorpay_pay(live, out["client_action"]["order_id"], status="captured")
        report = await _poller(lambda name: live).tick()
        assert (report.captured, report.expired) == (1, 0)
        assert (await _row(admin_session, out["charge_id"]))["state"] == "captured"

    async def test_a_capture_that_lands_after_the_close_is_still_recorded(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        session: AsyncSession,
        org_id: uuid.UUID,
        engines: None,
    ) -> None:
        """Razorpay Orders do not expire on their own: a customer with the page still open can pay
        after this platform stopped waiting. The money moved, so the record follows it."""
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        await force_charge(
            admin_session, out["charge_id"], checkout_expires_at=datetime.now(UTC) - LONG_AGO
        )
        await _poller(lambda name: live).tick()
        assert (await _row(admin_session, out["charge_id"]))["state"] == "failed"

        paid = cs.razorpay_pay(live, out["client_action"]["order_id"], status="captured")
        async with session_scope(organization_id=org_id) as s:
            charge = await selectors.lock_charge(s, org_id, uuid.UUID(out["charge_id"]))
            assert charge is not None
            moved = await services.apply_payment_snapshot(
                s,
                charge,
                PaymentSnapshot(
                    provider_payment_id=paid, status="captured", amount_minor=49_900, currency="INR"
                ),
            )
        assert moved is True
        row = await _row(admin_session, out["charge_id"])
        assert row["state"] == "captured" and row["failure_reason"] is None
        actions = [a["action"] for a in await audit_rows(admin_session, org_id)]
        assert "payment.charge.captured_after_local_failure" in actions
        assert "commerce.payment.captured" in await emitted(admin_session, org_id)


class TestTheStuckChargeSweepNeverTouchesACheckout:
    async def test_a_checkout_is_invisible_to_discovery_the_claim_and_the_sweep(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        # Everything that makes a plain `created` charge look stuck: no payment, an old row, no
        # lease, no backoff.
        await force_charge(
            admin_session,
            out["charge_id"],
            created_at=datetime.now(UTC) - LONG_AGO,
            lease_until=None,
            next_attempt_at=None,
            attempts=0,
        )
        charge_id = uuid.UUID(out["charge_id"])
        now = datetime.now(UTC)

        async with get_outbox_sessionmaker()() as session, session.begin():
            stuck = await selectors.due_stuck_charges(
                session, now=now, older_than=now - timedelta(minutes=5)
            )
        assert charge_id not in [c for _, c in stuck]

        async with session_scope(organization_id=org_id) as s:
            assert (
                await services.claim_charge_lease(s, charge_id, organization_id=org_id, now=now)
            ) is None
            step, claimed = await services.claim_stuck_charge(
                s, charge_id, organization_id=org_id, now=now
            )
            assert (step, claimed) == ("not_due", None)
            assert (
                await services.fail_exhausted_charge(s, charge_id, organization_id=org_id)
            ) is None

        sweep = StuckChargeSweep(
            discovery_factory=get_outbox_sessionmaker(),
            work_scope=lambda organization_id: session_scope(organization_id=organization_id),
            provider_factory=lambda: live,
            reclaim_after=timedelta(minutes=5),
        )
        events_before = await emitted(admin_session, org_id)
        before = len(live.fake.requests)
        report = await sweep.tick()
        assert report.considered == 0 and len(live.fake.requests) == before
        row = await _row(admin_session, out["charge_id"])
        assert (row["state"], row["failure_reason"]) == ("created", None)
        assert await emitted(admin_session, org_id) == events_before


async def test_the_poll_stamp_is_the_only_thing_a_poll_claim_writes(
    client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
) -> None:
    live = cs.razorpay_live()
    _, out = await _open(client, org_id, live)
    before = await _row(admin_session, out["charge_id"])
    async with session_scope(organization_id=org_id) as s:
        ticket = await checkout_services.claim_poll(
            s, org_id, uuid.UUID(out["charge_id"]), now=datetime.now(UTC), respect_gap=True
        )
    assert ticket is not None and ticket.provider == "razorpay"
    after = await _row(admin_session, out["charge_id"])
    assert after["state"] == before["state"] and after["next_attempt_at"] is not None
