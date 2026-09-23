"""The stock posting kernel (ADR-0014 §3) against real PostgreSQL, as the NOBYPASSRLS app role.

Covers what the rest of the platform will build on: exact decimal quantities through the ledger,
balances that always equal the ledger, reservations that narrow availability without moving
stock, status and roll buckets, idempotent retries, refusal of malformed or cross-tenant input,
and the append-only guarantee.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.inventory import ledger
from app.inventory import selectors as inv_selectors
from app.inventory import services as inv_services
from app.inventory.ledger import InsufficientStock, MoveLine
from app.inventory.quantity import MAX_ABS_MILLI, format_qty
from app.inventory.reconcile import reconcile_organization
from app.models import (
    AuditLog,
    InventoryMove,
    InventoryReservation,
    InventoryReservationRelease,
    OutboxEvent,
)
from platform_core.auth import Scope
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from tests.helpers import ctx_for
from tests.inventory_fixtures import (
    TODAY,
    Textile,
    balance,
    build_textile,
    ledger_rows,
    ledger_total,
    q,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def t(session, org_id) -> Textile:
    return await build_textile(session, org_id)


async def post(session, t: Textile, kind: str, *lines: MoveLine, **kw):  # type: ignore[no-untyped-def]
    async with session.begin():
        return await ledger.post_move(
            session, t.ctx, kind=kind, lines=list(lines), occurred_on=TODAY, **kw
        )


async def assert_reconciles_clean(session, org_id) -> None:  # type: ignore[no-untyped-def]
    """Both invariants `reconcile_organization` checks — ledger-vs-balance and
    reserved-vs-open-reservations — hold. A release (full or partial) must never leave a
    discrepancy behind."""
    async with session.begin():
        report = await reconcile_organization(session, org_id)
    assert report.findings == [], [
        (f.kind, f.stored_milli, f.expected_milli) for f in report.findings
    ]


class TestDecimalQuantities:
    async def test_fabric_metres_are_exact_through_receipt_and_partial_issues(
        self, session, t
    ) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("82.5")))
        await post(session, t, "issue", MoveLine(t.fabric, t.weaving, -q("0.5")))
        await post(session, t, "issue", MoveLine(t.fabric, t.weaving, -q("1.25")))
        on_hand, _ = await balance(session, t.org_id, t.fabric, t.weaving)
        assert format_qty(on_hand) == "80.75"
        assert await ledger_total(session, t.org_id, t.fabric) == on_hand

    async def test_a_large_decimal_receipt_is_exact(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("99999.99")))
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("0.01")))
        on_hand, _ = await balance(session, t.org_id, t.fabric, t.weaving)
        assert format_qty(on_hand) == "100000"

    async def test_kilograms_to_the_gram(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.dye, t.weaving, q("14.8")))
        await post(session, t, "issue", MoveLine(t.dye, t.weaving, -q("0.125")))
        on_hand, _ = await balance(session, t.org_id, t.dye, t.weaving)
        assert format_qty(on_hand) == "14.675"

    async def test_pieces(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.buttons, t.weaving, q("1")))
        await post(session, t, "receipt", MoveLine(t.buttons, t.weaving, q("100")))
        on_hand, _ = await balance(session, t.org_id, t.buttons, t.weaving)
        assert format_qty(on_hand) == "101"

    async def test_the_item_view_reads_the_balances(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("82.5")))
        async with session.begin():
            item = await inv_selectors.get_inventory_item(session, t.org_id, t.fabric)
            out = inv_selectors.inventory_out(item)
        assert (out.on_hand_qty, out.available_qty, out.reserved_qty) == ("82.5", "82.5", "0")
        assert out.on_hand == 82  # the whole-unit compatibility field

    async def test_a_count_computes_its_change_under_the_lock(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("82.5")))
        posted = await post(
            session, t, "count", MoveLine(t.fabric, t.weaving, counted_milli=q("80.2"))
        )
        assert posted.entries[0].quantity_milli == -q("2.3")
        on_hand, _ = await balance(session, t.org_id, t.fabric, t.weaving)
        assert format_qty(on_hand) == "80.2"

    async def test_unit_cost_is_kept_on_inbound_rows_only(self, session, t) -> None:
        received = await post(
            session,
            t,
            "receipt",
            MoveLine(
                t.fabric, t.weaving, q("10"), unit_cost_minor=123_45, unit_cost_currency="INR"
            ),
        )
        issued = await post(
            session,
            t,
            "issue",
            MoveLine(
                t.fabric, t.weaving, -q("1"), unit_cost_minor=123_45, unit_cost_currency="INR"
            ),
        )
        assert (received.entries[0].unit_cost_minor, received.entries[0].unit_cost_currency) == (
            123_45,
            "INR",
        )
        assert (issued.entries[0].unit_cost_minor, issued.entries[0].unit_cost_currency) == (
            None,
            None,
        )

    async def test_a_receipt_records_its_own_currency_not_the_items(self, session, t) -> None:
        """A PO priced in a currency other than the item's own is recorded as-is (ADR-0014 §8,
        revised) — never converted, and never silently relabelled into the item's currency."""
        received = await post(
            session,
            t,
            "receipt",
            MoveLine(t.fabric, t.weaving, q("10"), unit_cost_minor=900, unit_cost_currency="USD"),
        )
        assert (received.entries[0].unit_cost_minor, received.entries[0].unit_cost_currency) == (
            900,
            "USD",
        )


class TestRefusals:
    async def test_taking_more_than_is_there_names_the_numbers(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("320")))
        with pytest.raises(InsufficientStock) as refused:
            await post(session, t, "issue", MoveLine(t.fabric, t.weaving, -q("500")))
        assert "500 m of COT-180-NAVY" in refused.value.detail
        assert "320 m on hand" in refused.value.detail
        assert refused.value.errors == [
            {
                "sku": "COT-180-NAVY",
                "location": "WH-WEAVE/DEFAULT",
                "status": "AVAILABLE",
                "uom": "m",
                "requested": "500",
                "on_hand": "320",
                "reserved": "0",
                "available": "320",
                "reservations": [],
            }
        ]
        # Nothing was written: the whole transaction rolled back.
        assert await ledger_rows(session, t.org_id, t.fabric) == 1

    async def test_a_float_quantity_is_refused(self, session, t) -> None:
        with pytest.raises(ValidationProblem, match="integer of milli"):
            await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, 82.5))  # type: ignore[arg-type]

    async def test_a_zero_change_is_refused_outside_a_count(self, session, t) -> None:
        with pytest.raises(ValidationProblem, match="must change"):
            await post(session, t, "adjustment", MoveLine(t.fabric, t.weaving, 0))

    async def test_an_unknown_status_or_kind_is_refused(self, session, t) -> None:
        with pytest.raises(ValidationProblem, match="stock status"):
            await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, 1, status="LOST"))
        with pytest.raises(ValidationProblem, match="move kind"):
            await post(session, t, "teleport", MoveLine(t.fabric, t.weaving, 1))

    async def test_a_quantity_larger_than_the_ceiling_is_refused(self, session, t) -> None:
        with pytest.raises(ValidationProblem, match="too large"):
            await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, MAX_ABS_MILLI + 1))
        with pytest.raises(ValidationProblem, match="too large"):
            await post(session, t, "issue", MoveLine(t.fabric, t.weaving, -(MAX_ABS_MILLI + 1)))

    async def test_an_oversized_counted_quantity_is_refused(self, session, t) -> None:
        with pytest.raises(ValidationProblem, match="too large"):
            await post(
                session,
                t,
                "count",
                MoveLine(t.fabric, t.weaving, counted_milli=MAX_ABS_MILLI + 1),
            )

    async def test_a_unit_cost_without_a_currency_is_refused(self, session, t) -> None:
        with pytest.raises(ValidationProblem, match="given together"):
            await post(
                session, t, "receipt", MoveLine(t.fabric, t.weaving, q("1"), unit_cost_minor=100)
            )

    async def test_a_currency_without_a_unit_cost_is_refused(self, session, t) -> None:
        with pytest.raises(ValidationProblem, match="given together"):
            await post(
                session,
                t,
                "receipt",
                MoveLine(t.fabric, t.weaving, q("1"), unit_cost_currency="INR"),
            )

    async def test_a_malformed_currency_code_is_refused(self, session, t) -> None:
        with pytest.raises(ValidationProblem, match="3-letter"):
            await post(
                session,
                t,
                "receipt",
                MoveLine(t.fabric, t.weaving, q("1"), unit_cost_minor=100, unit_cost_currency="XX"),
            )

    async def test_one_bucket_twice_in_a_move_is_refused(self, session, t) -> None:
        with pytest.raises(ValidationProblem, match="at most once"):
            await post(
                session,
                t,
                "receipt",
                MoveLine(t.fabric, t.weaving, 1),
                MoveLine(t.fabric, t.weaving, 2),
            )


class TestBuckets:
    async def test_a_status_move_keeps_physical_stock_and_changes_availability(
        self, session, t
    ) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        await post(
            session,
            t,
            "relocation",
            MoveLine(t.fabric, t.weaving, -q("30")),
            MoveLine(t.fabric, t.weaving, q("30"), status="QUALITY_HOLD"),
        )
        async with session.begin():
            item = await inv_selectors.get_inventory_item(session, t.org_id, t.fabric)
            out = inv_selectors.inventory_out(item)
        assert out.on_hand_qty == "100"
        assert out.available_qty == "70"

    async def test_a_status_change_out_of_a_reserved_bucket_is_refused_and_names_it(
        self, session, t
    ) -> None:
        """A status change is a paired `relocation` move (ADR-0014 §4) — the same outbound line
        the reservation check already covers, so it refuses on the same numbers a count would
        (ADR-0014 §11)."""
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("50")))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("45")
            )
        rid = made.reservation.id
        with pytest.raises(InsufficientStock) as refused:
            await post(
                session,
                t,
                "relocation",
                MoveLine(t.fabric, t.weaving, -q("10")),
                MoveLine(t.fabric, t.weaving, q("10"), status="QUALITY_HOLD"),
            )
        assert [r["reservation_id"] for r in refused.value.errors[0]["reservations"]] == [str(rid)]
        # Nothing moved: physical stock and the reservation are untouched.
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("50"), q("45"))

    async def test_off_site_stock_is_not_on_hand(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        await post(
            session,
            t,
            "relocation",
            MoveLine(t.fabric, t.weaving, -q("40")),
            MoveLine(t.fabric, t.finishing, q("40"), status="AT_JOB_WORKER"),
        )
        async with session.begin():
            item = await inv_selectors.get_inventory_item(session, t.org_id, t.fabric)
        assert format_qty(item.on_hand_milli) == "60"

    async def test_two_rolls_of_one_fabric_are_separate_buckets(self, session, t) -> None:
        roll_a, roll_b = uuid.uuid4(), uuid.uuid4()
        await post(
            session,
            t,
            "receipt",
            MoveLine(t.fabric, t.weaving, q("82.5"), roll_id=roll_a),
            MoveLine(t.fabric, t.weaving, q("75"), roll_id=roll_b),
        )
        await post(session, t, "issue", MoveLine(t.fabric, t.weaving, -q("12.3"), roll_id=roll_a))
        a, _ = await balance(session, t.org_id, t.fabric, t.weaving, roll_id=roll_a)
        b, _ = await balance(session, t.org_id, t.fabric, t.weaving, roll_id=roll_b)
        assert (format_qty(a), format_qty(b)) == ("70.2", "75")
        with pytest.raises(InsufficientStock):
            await post(session, t, "issue", MoveLine(t.fabric, t.weaving, -q("71"), roll_id=roll_a))


class TestReservations:
    async def test_a_reservation_narrows_availability_without_moving_stock(
        self, session, t
    ) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        async with session.begin():
            await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("30")
            )
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("30"))
        assert await ledger_rows(session, t.org_id, t.fabric) == 1  # no ledger row

        with pytest.raises(InsufficientStock, match="30 m reserved, so 70 m available"):
            await post(session, t, "issue", MoveLine(t.fabric, t.weaving, -q("80")))
        await post(session, t, "issue", MoveLine(t.fabric, t.weaving, -q("70")))
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("30"), q("30"))

    async def test_over_reservation_is_refused(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        async with session.begin():
            await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("60")
            )
        with pytest.raises(InsufficientStock, match="40 m available"):
            async with session.begin():
                await ledger.reserve(
                    session,
                    t.ctx,
                    item_id=t.fabric,
                    location_id=t.weaving,
                    quantity_milli=q("60"),
                )

    async def test_issuing_against_a_reservation_consumes_it_partially_then_fully(
        self, session, t
    ) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("40")
            )
        rid = made.reservation.id
        await post(session, t, "issue", MoveLine(t.fabric, t.weaving, -q("25"), reservation_id=rid))
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("75"), q("15"))
        # Excess consumption: 20 more against a 15 remainder — 15 from the reservation, 5 free.
        await post(session, t, "issue", MoveLine(t.fabric, t.weaving, -q("20"), reservation_id=rid))
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("55"), 0)
        async with session.begin():
            res = await session.get(InventoryReservation, rid)
            assert (res.status, res.consumed_milli) == ("consumed", q("40"))

    async def test_release_gives_back_the_remainder_and_is_safe_to_retry(self, session, t) -> None:
        """A reservation nothing has ever drawn against closes as `released`, and a further
        keyless release is a safe no-op — the reservation's own already-`released` early return,
        not the append-only idempotency table (that needs a key). Contrast
        `test_a_keyless_release_after_a_consuming_close_is_a_real_conflict` below: once any of it
        has been consumed, the terminal status is `consumed` and a retry is not a no-op."""
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("40")
            )
        rid = made.reservation.id
        for _ in range(2):
            async with session.begin():
                released = await ledger.release(session, t.ctx, rid)
            assert released.status == "released"
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), 0)

    async def test_a_keyless_release_after_a_consuming_close_is_a_real_conflict(
        self, session, t
    ) -> None:
        """MEDIUM #2's other half: once `consumed_milli > 0`, the reservation closes as
        `consumed` regardless of which call closed it — so a further keyless release hits the
        same `ConflictProblem` a reservation consumed from the start would, not a silent no-op."""
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("40")
            )
        rid = made.reservation.id
        await post(session, t, "issue", MoveLine(t.fabric, t.weaving, -q("10"), reservation_id=rid))
        async with session.begin():
            released = await ledger.release(session, t.ctx, rid)
        assert (released.status, released.consumed_milli, released.released_milli) == (
            "consumed",
            q("10"),
            q("30"),
        )
        with pytest.raises(ConflictProblem, match="fully consumed"):
            async with session.begin():
                await ledger.release(session, t.ctx, rid)
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("90"), 0)

    async def test_nothing_to_reserve_is_refused(self, session, t) -> None:
        with pytest.raises(InsufficientStock):
            async with session.begin():
                await ledger.reserve(
                    session, t.ctx, item_id=t.dye, location_id=t.weaving, quantity_milli=q("1")
                )

    async def test_a_count_below_reserved_is_refused_and_names_the_reservation(
        self, session, t
    ) -> None:
        """ADR-0014 §11: the count-below-reserved refusal Phase 0 built, now naming what blocks
        it — the resolution a UI can act on is "release this reservation, then count"."""
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("50")))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("45")
            )
        rid = made.reservation.id
        with pytest.raises(InsufficientStock) as refused:
            await post(session, t, "count", MoveLine(t.fabric, t.weaving, counted_milli=q("40")))
        assert refused.value.errors[0]["reservations"] == [
            {
                "reservation_id": str(rid),
                "reference_type": None,
                "reference_id": None,
                "quantity": "45",
                "quantity_milli": q("45"),
            }
        ]
        # Nothing posted: the count never happened, the reservation never shrank.
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("50"), q("45"))

    async def test_a_shortfall_with_no_stock_at_all_names_no_reservation(self, session, t) -> None:
        """A reservation would not have helped here — there is nothing physically there to give
        — so naming one would mislead (ADR-0014 §11)."""
        with pytest.raises(InsufficientStock) as refused:
            await post(session, t, "issue", MoveLine(t.fabric, t.weaving, -q("1")))
        assert refused.value.errors[0]["reservations"] == []

    async def test_releasing_part_of_a_reservation_leaves_the_rest_active(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("40")
            )
        rid = made.reservation.id
        async with session.begin():
            released = await ledger.release(session, t.ctx, rid, quantity_milli=q("12"))
        assert released.status == "active"
        assert (released.released_milli, released.consumed_milli) == (q("12"), 0)
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("28"))
        await assert_reconciles_clean(session, t.org_id)

        # The count that was refused above now fits, once enough of the reservation is released.
        await post(session, t, "count", MoveLine(t.fabric, t.weaving, counted_milli=q("85")))
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("85"), q("28"))

        async with session.begin():
            fully = await ledger.release(session, t.ctx, rid)
        assert fully.status == "released"
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("85"), 0)
        await assert_reconciles_clean(session, t.org_id)

    async def test_a_fractional_release_of_a_discrete_item_is_refused(self, session, t) -> None:
        """`ledger.release` enforces discreteness itself (MEDIUM #1) rather than relying on
        `parse_qty`'s — `routes.py`'s release endpoint never threads an item's `uom` into
        `parse_qty`, so without this a caller could ask to release half of one button."""
        await post(session, t, "receipt", MoveLine(t.buttons, t.weaving, q("100")))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.buttons, location_id=t.weaving, quantity_milli=q("100")
            )
        rid = made.reservation.id
        with pytest.raises(ValidationProblem, match="whole units"):
            async with session.begin():
                await ledger.release(session, t.ctx, rid, quantity_milli=q("0.5"))
        # Refused before anything moved.
        assert await balance(session, t.org_id, t.buttons, t.weaving) == (q("100"), q("100"))

    async def test_a_partial_release_then_an_issue_draws_only_what_is_still_reserved(
        self, session, t
    ) -> None:
        """B1: `drawn` must subtract `released_milli` too, not just `consumed_milli` — the
        reservation has 40 open after a 60-of-100 release, so issuing 50 against it draws 40 from
        the reservation and 10 from free stock, never over-consumes the 40 that is actually left
        (the bug drew `min(50, 100 - 0)` = 50, over the 40 truly open, and raised the
        `consumed + released <= quantity` CHECK as an IntegrityError instead of a clean shortfall
        or a clean split)."""
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("100")
            )
        rid = made.reservation.id
        async with session.begin():
            await ledger.release(session, t.ctx, rid, quantity_milli=q("60"))
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("40"))

        await post(session, t, "issue", MoveLine(t.fabric, t.weaving, -q("50"), reservation_id=rid))
        # 40 drawn from the reservation, 10 from free stock: 100 - 50 = 50 on hand. The
        # reservation has 0 open left (100 - 40 consumed - 60 released = 0), so it closes.
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("50"), 0)
        async with session.begin():
            res = await session.get(InventoryReservation, rid)
            assert (res.status, res.consumed_milli, res.released_milli) == (
                "consumed",
                q("40"),
                q("60"),
            )
        await assert_reconciles_clean(session, t.org_id)

    async def test_issuing_exactly_what_is_left_after_a_release_closes_the_reservation(
        self, session, t
    ) -> None:
        """B1's other repro: issuing exactly the 40 still open after a 60-of-100 release used to
        leave the reservation `active` with 0 open forever (the old close check compared
        `consumed_milli == quantity_milli`, never true once part of the promise was released
        rather than consumed) — it must close, the same as fully consuming an unreleased
        reservation does."""
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("100")
            )
        rid = made.reservation.id
        async with session.begin():
            await ledger.release(session, t.ctx, rid, quantity_milli=q("60"))

        await post(session, t, "issue", MoveLine(t.fabric, t.weaving, -q("40"), reservation_id=rid))
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("60"), 0)
        async with session.begin():
            res = await session.get(InventoryReservation, rid)
            assert (res.status, res.consumed_milli, res.released_milli) == (
                "consumed",
                q("40"),
                q("60"),
            )
        await assert_reconciles_clean(session, t.org_id)

    async def test_releasing_more_than_is_open_is_refused(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("40")
            )
        with pytest.raises(ConflictProblem, match="Only 40 is still open"):
            async with session.begin():
                await ledger.release(session, t.ctx, made.reservation.id, quantity_milli=q("41"))

    async def test_a_release_key_replays_and_a_different_quantity_conflicts(
        self, session, t
    ) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("40")
            )
        rid = made.reservation.id

        async with session.begin():
            first = await ledger.release(
                session, t.ctx, rid, quantity_milli=q("10"), idempotency_key="rel-1"
            )
        async with session.begin():
            again = await ledger.release(
                session, t.ctx, rid, quantity_milli=q("10"), idempotency_key="rel-1"
            )
        assert (first.released_milli, again.released_milli) == (q("10"), q("10"))
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("30"))

        with pytest.raises(ConflictProblem, match="different release"):
            async with session.begin():
                await ledger.release(
                    session, t.ctx, rid, quantity_milli=q("20"), idempotency_key="rel-1"
                )

    async def test_two_distinct_release_keys_both_take_effect_and_the_first_still_replays(
        self, session, t
    ) -> None:
        """H1: a single mutable column on the reservation can only remember the *most recent*
        key — releasing again with a genuinely different key would silently forget the first one
        ever happened, and retrying the first key afterwards would either false-conflict or
        replay the second release's numbers. `inventory_reservation_releases` is append-only, so
        both keys are remembered and each replays its own result."""
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("40")
            )
        rid = made.reservation.id

        async with session.begin():
            await ledger.release(session, t.ctx, rid, quantity_milli=q("10"), idempotency_key="A")
        async with session.begin():
            await ledger.release(session, t.ctx, rid, quantity_milli=q("10"), idempotency_key="B")
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("20"))

        # Retrying the *first* key still replays cleanly — it was not overwritten by the second.
        async with session.begin():
            replay_a = await ledger.release(
                session, t.ctx, rid, quantity_milli=q("10"), idempotency_key="A"
            )
        assert replay_a.released_milli == q("20")  # current total, not a re-release
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("20"))

        async with session.begin():
            rows = (
                await session.execute(
                    select(InventoryReservationRelease).where(
                        InventoryReservationRelease.reservation_id == rid
                    )
                )
            ).scalars()
            found = {(r.idempotency_key, r.released_milli) for r in rows}
        assert found == {("A", q("10")), ("B", q("10"))}
        await assert_reconciles_clean(session, t.org_id)

    async def test_releasing_a_reservation_from_another_org_is_not_found(
        self, session, admin_session, org_id
    ) -> None:
        other = await build_textile(admin_session, uuid.uuid4())
        async with admin_session.begin():
            await ledger.post_move(
                admin_session,
                other.ctx,
                kind="receipt",
                lines=[MoveLine(other.fabric, other.weaving, q("10"))],
                occurred_on=TODAY,
            )
            made = await ledger.reserve(
                admin_session,
                other.ctx,
                item_id=other.fabric,
                location_id=other.weaving,
                quantity_milli=q("5"),
            )
        ctx = ctx_for(org_id, "operations.inventory.stock.reserve", Scope.ORG)
        with pytest.raises(NotFoundProblem):
            async with session.begin():
                await ledger.release(session, ctx, made.reservation.id)


class TestIdempotency:
    async def test_the_same_key_replays_instead_of_posting_twice(self, session, t) -> None:
        line = MoveLine(t.fabric, t.weaving, q("82.5"))
        first = await post(session, t, "receipt", line, idempotency_key="grn-1-line-1")
        again = await post(session, t, "receipt", line, idempotency_key="grn-1-line-1")
        assert (first.replayed, again.replayed) == (False, True)
        assert again.move.id == first.move.id
        assert [e.id for e in again.entries] == [e.id for e in first.entries]
        assert await ledger_rows(session, t.org_id, t.fabric) == 1
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("82.5"), 0)

    async def test_a_defaulted_occurred_on_does_not_break_a_retry_across_midnight(
        self, session, t
    ) -> None:
        """`occurred_on_explicit=False` marks an `occurred_on` the caller defaulted rather than
        received from the client — the item-level adjustments API's case, since its schema
        defaults `occurred_on` to `date.today()` at parse time. A retry landing on the other side
        of midnight must still replay, not 409, even though the two calls carry different dates."""
        line = MoveLine(t.fabric, t.weaving, q("10"))
        async with session.begin():
            first = await ledger.post_move(
                session,
                t.ctx,
                kind="adjustment",
                lines=[line],
                occurred_on=TODAY,
                occurred_on_explicit=False,
                idempotency_key="adj-midnight",
            )
        async with session.begin():
            again = await ledger.post_move(
                session,
                t.ctx,
                kind="adjustment",
                lines=[line],
                occurred_on=TODAY.replace(day=TODAY.day + 1),
                occurred_on_explicit=False,
                idempotency_key="adj-midnight",
            )
        assert (first.replayed, again.replayed) == (False, True)
        assert again.move.id == first.move.id

    async def test_an_explicit_occurred_on_still_participates_in_the_hash(self, session, t) -> None:
        """The default behaviour (`occurred_on_explicit=True`) is unchanged: a caller that does
        send `occurred_on` still has it treated as part of what makes a request the *same*
        request, so a retry with a genuinely different date is a conflict, not a replay."""
        line = MoveLine(t.fabric, t.weaving, q("10"))
        await post(session, t, "adjustment", line, idempotency_key="adj-explicit")
        with pytest.raises(ConflictProblem, match="different stock move"):
            async with session.begin():
                await ledger.post_move(
                    session,
                    t.ctx,
                    kind="adjustment",
                    lines=[line],
                    occurred_on=TODAY.replace(day=TODAY.day + 1),
                    idempotency_key="adj-explicit",
                )

    async def test_the_same_key_for_a_different_request_is_a_conflict(self, session, t) -> None:
        await post(
            session, t, "receipt", MoveLine(t.fabric, t.weaving, q("1")), idempotency_key="k"
        )
        with pytest.raises(ConflictProblem, match="different stock move"):
            await post(
                session, t, "receipt", MoveLine(t.fabric, t.weaving, q("2")), idempotency_key="k"
            )

    async def test_a_reservation_key_replays(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        results = []
        for _ in range(2):
            async with session.begin():
                results.append(
                    await ledger.reserve(
                        session,
                        t.ctx,
                        item_id=t.fabric,
                        location_id=t.weaving,
                        quantity_milli=q("30"),
                        idempotency_key="so-7-line-2",
                    )
                )
        assert results[1].replayed and results[1].reservation.id == results[0].reservation.id
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("30"))


class TestAuditAndEvents:
    async def test_a_post_writes_one_audit_row_and_one_event_and_a_replay_neither(
        self, session, t
    ) -> None:
        line = MoveLine(t.fabric, t.weaving, q("82.5"))
        first = await post(session, t, "receipt", line, idempotency_key="once")
        await post(session, t, "receipt", line, idempotency_key="once")
        async with session.begin():
            audits = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.action == "inventory_move.posted",
                            AuditLog.entity_id == first.move.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.event_type == ledger.EVT_STOCK_POSTED,
                            OutboxEvent.aggregate_id == first.move.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(audits) == 1 and len(events) == 1
        line_out = events[0].payload["lines"][0]
        assert (line_out["quantity"], line_out["balance_after"]) == ("82.5", "82.5")
        assert line_out["sku"] == "COT-180-NAVY"


class TestIsolationAndImmutability:
    async def test_another_orgs_item_or_location_is_not_found(
        self, session, admin_session, t
    ) -> None:
        other_org = uuid.uuid4()
        other = await build_textile(admin_session, other_org)
        with pytest.raises(NotFoundProblem):
            await post(session, t, "receipt", MoveLine(other.fabric, t.weaving, q("1")))
        with pytest.raises(NotFoundProblem):
            await post(session, t, "receipt", MoveLine(t.fabric, other.weaving, q("1")))

    async def test_the_app_role_cannot_rewrite_a_move_header(self, session, t) -> None:
        posted = await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("1")))
        with pytest.raises(Exception, match="permission denied"):
            async with session.begin():
                await session.execute(
                    InventoryMove.__table__.update()
                    .where(InventoryMove.id == posted.move.id)
                    .values(note="rewritten")
                )

    async def test_the_balance_check_constraints_are_a_backstop(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("5")))
        with pytest.raises(Exception, match=r"reserved_within|quantity_non_negative"):
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE operations.inventory_balances SET reserved_milli = 999999 "
                        "WHERE inventory_item_id = :i"
                    ),
                    {"i": t.fabric},
                )


class TestItemLevelCompatibility:
    async def test_changing_an_items_warehouse_moves_its_stock_through_the_ledger(
        self, session, t
    ) -> None:
        from app.schemas import InventoryItemPatch

        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("82.5")))
        async with session.begin():
            item = await inv_selectors.get_inventory_item(
                session, t.org_id, t.fabric, for_update=True
            )
            await inv_services.update_inventory_item(
                session, t.ctx, item, InventoryItemPatch(warehouse="Finishing")
            )
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (0, 0)
        assert await balance(session, t.org_id, t.fabric, t.finishing) == (q("82.5"), 0)
        assert await ledger_total(session, t.org_id, t.fabric) == q("82.5")

    async def test_reserved_stock_blocks_a_warehouse_change(self, session, t) -> None:
        """`_relocate_home_stock` locks both buckets before reading either one (review finding
        on lock order), then relies on `post_move`'s own outbound check to refuse a relocation
        that would eat into reserved stock (ADR-0014 §11) — one refusal, reused rather than
        copied, so the reservation blocking it is named exactly as a count's would be."""
        from app.schemas import InventoryItemPatch

        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("100")))
        async with session.begin():
            made = await ledger.reserve(
                session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("30")
            )
        rid = made.reservation.id
        with pytest.raises(InsufficientStock) as refused:
            async with session.begin():
                item = await inv_selectors.get_inventory_item(
                    session, t.org_id, t.fabric, for_update=True
                )
                await inv_services.update_inventory_item(
                    session, t.ctx, item, InventoryItemPatch(warehouse="Finishing")
                )
        assert "30 m reserved" in refused.value.detail
        assert refused.value.errors[0]["reservations"] == [
            {
                "reservation_id": str(rid),
                "reference_type": None,
                "reference_id": None,
                "quantity": "30",
                "quantity_milli": q("30"),
            }
        ]
        # Refused before anything moved.
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("100"), q("30"))
        assert await balance(session, t.org_id, t.fabric, t.finishing) == (0, 0)

    async def test_uom_change_is_refused_once_the_item_has_stock_history(self, session, t) -> None:
        """A `uom` change rewrites what every existing milli-quantity for the item means (M5) —
        once a balance or ledger row exists it is refused outright, not silently reinterpreted."""
        from app.schemas import InventoryItemPatch

        await post(session, t, "receipt", MoveLine(t.fabric, t.weaving, q("10")))
        with pytest.raises(ConflictProblem, match="stock has moved"):
            async with session.begin():
                item = await inv_selectors.get_inventory_item(
                    session, t.org_id, t.fabric, for_update=True
                )
                await inv_services.update_inventory_item(
                    session, t.ctx, item, InventoryItemPatch(uom="kg")
                )

    async def test_uom_change_is_allowed_before_any_stock_has_moved(self, session, t) -> None:
        """The guard only fires once history exists — an item that has never carried stock is
        free to have its unit corrected."""
        from app.schemas import InventoryItemPatch

        async with session.begin():
            item = await inv_selectors.get_inventory_item(
                session, t.org_id, t.fabric, for_update=True
            )
            await inv_services.update_inventory_item(
                session, t.ctx, item, InventoryItemPatch(uom="kg")
            )
        item = await inv_selectors.get_inventory_item(session, t.org_id, t.fabric)
        assert item is not None
        assert item.uom == "kg"

    async def test_a_count_refuses_when_stock_is_outside_the_home_bucket(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.fabric, t.finishing, q("5")))
        with pytest.raises(ConflictProblem, match="count each location"):
            async with session.begin():
                item = await inv_selectors.get_inventory_item(
                    session, t.org_id, t.fabric, for_update=True
                )
                await inv_services.adjust_stock(session, t.ctx, item, counted_on_hand=0)

    async def test_a_warehouse_holding_stock_cannot_be_deleted(self, session, t) -> None:
        # The dye's home is the weaving store, but some of it sits in finishing.
        await post(session, t, "receipt", MoveLine(t.dye, t.finishing, q("2")))
        async with session.begin():
            fin = await inv_selectors.get_warehouse_by_code(session, t.org_id, "WH-FIN")
            with pytest.raises(ConflictProblem, match="still holds stock"):
                await inv_services.delete_warehouse(session, t.ctx, fin)

    async def test_an_item_with_stock_anywhere_cannot_be_deleted(self, session, t) -> None:
        await post(session, t, "receipt", MoveLine(t.buttons, t.finishing, q("3")))
        async with session.begin():
            item = await inv_selectors.get_inventory_item(session, t.org_id, t.buttons)
            with pytest.raises(ConflictProblem, match="3 on hand"):
                await inv_services.delete_inventory_item(session, t.ctx, item)
