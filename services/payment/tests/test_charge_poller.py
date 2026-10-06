"""The charge poller (`app/checkout/cron.py`, ADR-0032): the only way a platform charge learns an
outcome while the provider webhooks are private, and the sweep exclusion that makes a checkout
safe to leave `created`.

What these pin:

* **What it asks about** — a checkout, and an authorized debit — and what it never does: a plain
  `created` debit is the stuck-charge sweep's, and a checkout is **never** the sweep's (it would
  "retry" a payment nobody made, fail it `attempts_exhausted` and suspend a customer who has not
  yet paid).
* **Claim → call → record**, routed by the **row's** provider.
* **Expiry** — an unpaid checkout past its expiry is closed with **no** `commerce.payment.failed`,
  but only if the provider was successfully asked and reports no attempt still in flight; a closed
  one keeps being asked about for its late-capture window, so a capture on it is still recorded.
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

    async def test_a_capture_that_lands_after_the_close_is_recorded_by_the_poller(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        """Razorpay Orders do not expire on their own: a customer with the page still open can pay
        after this platform stopped waiting. With the webhooks private, the poller is the only
        thing that can learn of it — so it keeps asking about a closed checkout, at a longer gap,
        and the money moved is recorded through the path production takes (not a direct call)."""
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        await force_charge(
            admin_session, out["charge_id"], checkout_expires_at=datetime.now(UTC) - LONG_AGO
        )
        poller = _poller(lambda name: live)
        assert (await poller.tick()).expired == 1
        assert (await _row(admin_session, out["charge_id"]))["state"] == "failed"

        paid = cs.razorpay_pay(live, out["client_action"]["order_id"], status="captured")
        # Inside the late gap it is not asked about again...
        assert (await poller.tick()).considered == 0
        # ...and once the gap has passed, it is.
        report = await poller.tick(now=datetime.now(UTC) + timedelta(minutes=11))

        assert (report.polled, report.captured, report.expired) == (1, 1, 0)
        row = await _row(admin_session, out["charge_id"])
        assert row["state"] == "captured" and row["failure_reason"] is None
        assert row["provider_payment_id"] == paid
        actions = [a["action"] for a in await audit_rows(admin_session, org_id)]
        assert "payment.charge.captured_after_local_failure" in actions
        assert (await emitted(admin_session, org_id)).count("commerce.payment.captured") == 1

    async def test_a_closed_checkout_is_asked_about_only_inside_its_window(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        await force_charge(
            admin_session,
            out["charge_id"],
            checkout_expires_at=datetime.now(UTC) - timedelta(days=6),
        )
        poller = _poller(lambda name: live)
        assert (await poller.tick()).expired == 1
        cs.razorpay_pay(live, out["client_action"]["order_id"], status="captured")
        calls = len(live.fake.requests)

        later = datetime.now(UTC) + timedelta(minutes=11)
        report = await poller.tick(now=later)

        # Discovered once more, retired by the claim, and never asked about again.
        assert (report.considered, report.skipped, report.polled) == (1, 1, 0)
        assert len(live.fake.requests) == calls
        row = await _row(admin_session, out["charge_id"])
        assert (row["state"], row["next_attempt_at"]) == ("failed", None)
        assert (await poller.tick(now=later + timedelta(hours=1))).considered == 0


class TestAnAttemptInFlightAtExpiry:
    """A checkout is closed only when nobody is paying it: an attempt the provider still reports in
    progress keeps it open (for a bounded grace), so a payment completing seconds later is
    captured instead of labelled "nobody paid" and offered a second checkout."""

    @pytest.mark.parametrize("status", ["created", "authorized"])
    async def test_it_is_kept_open_and_the_capture_that_follows_is_recorded(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        engines: None,
        status: str,
    ) -> None:
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        order_id = out["client_action"]["order_id"]
        cs.razorpay_pay(live, order_id, status=status)
        await force_charge(
            admin_session,
            out["charge_id"],
            checkout_expires_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        poller = _poller(lambda name: live)

        report = await poller.tick()

        assert (report.polled, report.expired) == (1, 0)
        assert (await _row(admin_session, out["charge_id"]))["state"] == "created"
        live.fake.payments[order_id][0]["status"] = "captured"
        report = await poller.tick(now=datetime.now(UTC) + timedelta(minutes=2))
        assert report.captured == 1
        assert (await _row(admin_session, out["charge_id"]))["state"] == "captured"
        assert (await emitted(admin_session, org_id)).count("commerce.payment.captured") == 1

    async def test_a_cashfree_payment_still_pending_keeps_it_open(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        cashfree = cs.cashfree_stub()
        _, out = await _open(client, org_id, cashfree)
        cs.cashfree_pay(cashfree, out["charge_id"], status="PENDING")
        await force_charge(
            admin_session,
            out["charge_id"],
            checkout_expires_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        report = await _poller(lambda name: cashfree).tick()
        assert (report.polled, report.expired) == (1, 0)
        assert (await _row(admin_session, out["charge_id"]))["state"] == "created"

    async def test_an_attempt_the_provider_never_resolves_holds_it_only_for_the_grace(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        cs.razorpay_pay(live, out["client_action"]["order_id"], status="created")
        await force_charge(
            admin_session,
            out["charge_id"],
            checkout_expires_at=datetime.now(UTC) - timedelta(hours=2),
        )
        report = await _poller(lambda name: live).tick()
        assert report.expired == 1
        row = await _row(admin_session, out["charge_id"])
        assert (row["state"], row["failure_reason"]) == ("failed", "checkout_expired")
        # Closed, but still asked about for the late-capture window.
        assert row["next_attempt_at"] is not None


class TestStrayOrders:
    """Razorpay accepts a duplicate receipt (sandbox, 2026-10-06), so an unknown-outcome create can
    leave a second Order for one checkout that the charge never recorded and nobody was handed.
    Before the poller closes a checkout — and through its late window — it searches the receipt
    for such an Order, and money that moved on one is recorded against the charge."""

    async def _expired_with_stray(
        self, client: AsyncClient, admin: AsyncSession, org_id: uuid.UUID, *, ago: timedelta
    ) -> tuple[Any, dict[str, Any], str]:
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        stray = live.fake.duplicate(out["charge_id"])
        await force_charge(admin, out["charge_id"], checkout_expires_at=datetime.now(UTC) - ago)
        return live, out, stray

    async def test_a_capture_on_a_stray_order_is_found_before_the_checkout_is_closed(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        live, out, stray = await self._expired_with_stray(
            client, admin_session, org_id, ago=LONG_AGO
        )
        paid = cs.razorpay_pay(live, stray, status="captured")

        report = await _poller(lambda name: live).tick()

        assert (report.captured, report.expired) == (1, 0)
        row = await _row(admin_session, out["charge_id"])
        assert (row["state"], row["provider_payment_id"]) == ("captured", paid)
        (recorded,) = await rows(
            admin_session,
            "SELECT provider_order_id FROM payment.charges WHERE id = :id",
            id=uuid.UUID(out["charge_id"]),
        )
        assert recorded["provider_order_id"] == out["client_action"]["order_id"]
        audits = [
            a
            for a in await audit_rows(admin_session, org_id)
            if str(a["entity_id"]) == out["charge_id"]
        ]
        (flagged,) = [a for a in audits if a["action"] == "payment.checkout.stray_order_payment"]
        assert flagged["after"]["stray_order_id"] == stray
        assert flagged["after"]["recorded_order_id"] == out["client_action"]["order_id"]
        assert (await emitted(admin_session, org_id)).count("commerce.payment.captured") == 1

    async def test_a_payment_in_flight_on_a_stray_order_holds_the_close(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        live, out, stray = await self._expired_with_stray(
            client, admin_session, org_id, ago=timedelta(seconds=5)
        )
        cs.razorpay_pay(live, stray, status="authorized")
        report = await _poller(lambda name: live).tick()
        assert (report.polled, report.expired) == (1, 0)
        assert (await _row(admin_session, out["charge_id"]))["state"] == "created"

    async def test_a_stray_capture_after_the_close_is_recorded_in_the_late_window(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        """The receipt search can miss a fresh Order, so a stray may only show after the close: the
        late-capture window keeps searching, and money found then is still recorded."""
        live, out, stray = await self._expired_with_stray(
            client, admin_session, org_id, ago=LONG_AGO
        )
        live.fake.unindexed.add(stray)  # not visible to the search yet
        poller = _poller(lambda name: live)
        assert (await poller.tick()).expired == 1

        paid = cs.razorpay_pay(live, stray, status="captured")
        live.fake.unindexed.discard(stray)
        report = await poller.tick(now=datetime.now(UTC) + timedelta(minutes=11))

        assert report.captured == 1
        row = await _row(admin_session, out["charge_id"])
        assert (row["state"], row["failure_reason"], row["provider_payment_id"]) == (
            "captured",
            None,
            paid,
        )
        actions = [a["action"] for a in await audit_rows(admin_session, org_id)]
        assert "payment.checkout.stray_order_payment" in actions
        assert "payment.charge.captured_after_local_failure" in actions

    async def test_a_stray_payment_for_another_amount_is_flagged_never_applied(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        live, out, stray = await self._expired_with_stray(
            client, admin_session, org_id, ago=timedelta(seconds=5)
        )
        cs.razorpay_pay(live, stray, status="captured", amount=100)
        await _poller(lambda name: live).tick()
        row = await _row(admin_session, out["charge_id"])
        assert row["state"] == "created" and row["provider_payment_id"] is None
        not_applied = [
            a["after"]
            for a in await audit_rows(admin_session, org_id)
            if a["action"] == "payment.charge.sync_not_applied"
        ]
        assert [a["stray_order_id"] for a in not_applied] == [stray]

    async def test_a_stray_paid_as_well_as_the_recorded_order_is_flagged_not_dropped(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        """Both Orders paid: the recorded Order's capture settles the charge, and the stray's is the
        same invoice collected twice — kept and flagged for a person, never silently dropped."""
        live, out, stray = await self._expired_with_stray(
            client, admin_session, org_id, ago=LONG_AGO
        )
        recorded_paid = cs.razorpay_pay(live, out["client_action"]["order_id"], status="captured")
        cs.razorpay_pay(live, stray, status="captured")

        report = await _poller(lambda name: live).tick()

        assert report.captured == 1
        row = await _row(admin_session, out["charge_id"])
        assert (row["state"], row["provider_payment_id"]) == ("captured", recorded_paid)
        not_applied = [
            a["after"]
            for a in await audit_rows(admin_session, org_id)
            if a["action"] == "payment.charge.sync_not_applied"
        ]
        assert [a["stray_order_id"] for a in not_applied] == [stray]
        assert (await emitted(admin_session, org_id)).count("commerce.payment.captured") == 1

    async def test_strays_are_not_searched_for_while_a_checkout_is_open(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        """A search per open checkout per poll would be a request Razorpay rate-limits for a
        question that only matters at the close."""
        live = cs.razorpay_live()
        _, out = await _open(client, org_id, live)
        live.fake.requests.clear()
        await _poller(lambda name: live).tick()
        assert live.fake.methods() == [
            f"GET /v1/orders/{out['client_action']['order_id']}/payments"
        ]

    async def test_a_stray_search_that_fails_does_not_hold_the_close(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, engines: None
    ) -> None:
        """Best-effort: a `429` on the search is "nothing found", logged; the recorded Order was
        asked, so the checkout closes — and the late window searches again."""
        live, out, _ = await self._expired_with_stray(client, admin_session, org_id, ago=LONG_AGO)
        live.fake.inject(httpx.Response(200, json={"items": []}))  # the recorded Order's payments
        live.fake.inject(httpx.Response(429, json={"error": {"code": "BAD_REQUEST_ERROR"}}))
        report = await _poller(lambda name: live).tick()
        assert report.expired == 1
        row = await _row(admin_session, out["charge_id"])
        assert (row["state"], row["failure_reason"]) == ("failed", "checkout_expired")
        assert row["next_attempt_at"] is not None

    async def test_the_read_through_finds_a_stray_capture_too(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        live, out, stray = await self._expired_with_stray(
            client, admin_session, org_id, ago=LONG_AGO
        )
        cs.install(live)
        cs.razorpay_pay(live, stray, status="captured")
        status = await cs.get_checkout(client, org_id, out["charge_id"])
        assert status.status_code == 200 and status.json()["paid"] is True


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
