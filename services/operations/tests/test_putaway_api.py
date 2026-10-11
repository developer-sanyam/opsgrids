"""Storage put-away over HTTP (`POST /operations/inventory/putaway`) — what it *does*.

Authorization and cross-tenant behaviour of the new routes is `test_putaway_authz.py`'s; the audit
trail and the "coordinates appear nowhere else" scans are `test_putaway_audit.py`'s; the pure rules
(geo table, suggestion, captured_at window) are `test_putaway_domain.py`'s. This file is the rest:

* the happy path, for an untracked item and for a tracked roll, from receiving and from staging;
* `bin_transfer` regression — the existing transfer is untouched, and a put-away emits
  `putaway.recorded` and **not** `stock.transferred`;
* idempotency — a replay writes nothing, the same key for a different request is a 409, a retry
  that differs only in geo replays the original;
* the geo table over the wire, the raw-INSERT CHECK backstop, and a forced failure of the capture
  insert leaving the stock move posted;
* the validation matrix.

Every test ends where it can with the reconciler clean: whatever a put-away does or refuses, the
ledger and the balances still agree.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.inventory import ledger, stock_ops
from app.inventory import services as inv_services
from app.models import (
    AuditLog,
    InventoryMove,
    LedgerEntry,
    OutboxEvent,
    PutawayCapture,
    RollEvent,
    Warehouse,
    Worker,
)
from platform_core.events import validate_event_schema
from tests.helpers import principal_for
from tests.inventory_fixtures import q
from tests.putaway_fixtures import (
    ACC,
    LAT,
    LNG,
    PUTAWAY,
    REVEAL_AMR,
    REVEAL_PERM,
    STOCK_READ,
    STOCK_TRANSFER,
    PutawayWorld,
    audit_rows,
    build_putaway_world,
    call,
    captures,
    count,
    foreign_barcoded_bin,
    line,
    make_location,
    move_ids,
    opening,
    outbox_rows,
    put,
    putaway_body,
    tracked_body,
)
from tests.tracking_fixtures import assert_reconciled, bucket_qty, make_foreign_world

pytestmark = pytest.mark.integration

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"
TRANSFERS = "/operations/inventory/stock/transfers"
EVT_PUTAWAY = "operations.inventory.putaway.recorded"
EVT_TRANSFERRED = "operations.inventory.stock.transferred"
#: The put-away route's reference-id namespace, restated here on purpose: if the constant ever
#: changes, every key already stored would no longer replay, so the value is a contract.
NAMESPACE = uuid.UUID("5f0d2f3a-8f0c-4a63-9b8e-7a1f6c2d9e41")


@pytest.fixture
async def pw(session, org_id) -> PutawayWorld:
    return await build_putaway_world(session, org_id)


async def stage_nothing_posted(session, pw: PutawayWorld) -> None:
    """A refused put-away left no move, no capture, no put-away audit row, no put-away event — and
    the ledger still reconciles."""
    assert await move_ids(session, pw.org_id, reference_type="putaway") == []
    assert await count(session, PutawayCapture, pw.org_id) == 0
    assert await audit_rows(session, pw.org_id, action="putaway.recorded") == []
    assert await outbox_rows(session, pw.org_id, event_type=EVT_PUTAWAY) == []
    await assert_reconciled(session, pw.org_id)


# ------------------------------------------------------------------ the happy path


class TestHappyPath:
    async def test_stock_moves_from_receiving_into_the_scanned_bin_and_the_proof_is_kept(
        self, client, session, pw
    ) -> None:
        who = principal_for(pw.org_id, [STOCK_TRANSFER])
        resp = await put(
            client,
            pw,
            putaway_body(
                pw,
                suggested_location_id=str(pw.rack_bin),
                lat_micro=LAT,
                lng_micro=LNG,
                accuracy_m=ACC,
                note="pallet 7",
            ),
            principal=who,
        )
        assert resp.status_code == 201, resp.text
        out = resp.json()
        assert out["replayed"] is False and out["geo_recorded"] is True and out["warnings"] == []
        assert out["matched_suggestion"] is True
        assert out["from_location"] == {
            "id": str(pw.receiving),
            "code": "RCV-1",
            "name": "Receiving",
        }
        assert out["to_location"] == {
            "id": str(pw.rack_bin),
            "code": "R1-B01",
            "name": "Rack 1 bin 1",
            "kind": "bin",
        }
        assert set(out) == {
            "move_id",
            "replayed",
            "from_location",
            "to_location",
            "matched_suggestion",
            "geo_recorded",
            "warnings",
        }

        # The ledger: 5 out of receiving, 5 into the rack bin, nothing else touched.
        assert (await bucket_qty(session, pw.w, location=pw.receiving, item=pw.dye))[0] == q("15")
        assert (await bucket_qty(session, pw.w, location=pw.rack_bin, item=pw.dye))[0] == q("5")
        assert (await bucket_qty(session, pw.w, location=pw.staging, item=pw.dye))[0] == q("10")

        async with session.begin():
            move = (
                await session.execute(
                    select(InventoryMove).where(InventoryMove.id == out["move_id"])
                )
            ).scalar_one()
        assert (move.kind, move.reference_type) == ("relocation", "putaway")
        assert move.reference_id == uuid.uuid5(NAMESPACE, "pa-1")
        assert move.idempotency_key == "putaway:pa-1"
        assert move.note == "pallet 7"

        [capture] = await captures(session, pw.org_id)
        assert capture.move_id == uuid.UUID(out["move_id"])
        assert capture.location_id == pw.rack_bin
        assert capture.suggested_location_id == pw.rack_bin
        assert capture.created_by == who.sub
        assert (capture.lat_micro, capture.lng_micro, capture.accuracy_m) == (LAT, LNG, ACC)
        assert capture.matched_suggestion is True
        assert abs(capture.captured_at - datetime.now(UTC)) < timedelta(minutes=1)
        assert capture.worker_id is None  # a caller with no worker row still puts stock away
        await assert_reconciled(session, pw.org_id)

    async def test_the_move_is_recorded_for_an_audit_trail_and_an_event_that_match_their_schemas(
        self, client, session, pw
    ) -> None:
        resp = await put(client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC))
        move_id = resp.json()["move_id"]
        actions = {
            a.action for a in await audit_rows(session, pw.org_id) if str(a.entity_id) == move_id
        }
        assert actions == {"putaway.recorded", "inventory_move.posted"}
        events = {
            e.event_type: e
            for e in await outbox_rows(session, pw.org_id)
            if str(e.aggregate_id) == move_id
        }
        assert set(events) == {EVT_PUTAWAY, "operations.inventory.stock.posted"}
        payload = dict(events[EVT_PUTAWAY].payload)
        assert payload["has_location"] is True and payload["matched_suggestion"] is True
        assert payload["quantity"] == "5" and payload["status"] == "AVAILABLE"
        assert payload["from_location_id"] == str(pw.receiving)
        assert payload["to_location_id"] == str(pw.rack_bin)
        validate_event_schema(EVT_PUTAWAY, 1, payload, contracts_root=CONTRACTS_ROOT)

    async def test_a_tracked_roll_is_put_away_whole_and_its_history_records_the_move(
        self, client, session, pw
    ) -> None:
        w = pw.w
        resp = await put(client, pw, tracked_body(pw, lat_micro=LAT, lng_micro=LNG))
        assert resp.status_code == 201, resp.text
        bucket = {"item": pw.fabric, "lot": w.lot, "roll": w.roll}
        assert (await bucket_qty(session, w, location=pw.receiving, **bucket))[0] == 0
        assert (await bucket_qty(session, w, location=pw.rack_bin, **bucket))[0] == q("82.5")
        async with session.begin():
            moved = (
                (
                    await session.execute(
                        select(RollEvent).where(
                            RollEvent.roll_id == w.roll, RollEvent.kind == "MOVED"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert [(e.move_id, e.detail["to_location_id"]) for e in moved] == [
            (uuid.UUID(resp.json()["move_id"]), str(pw.rack_bin))
        ]
        assert len(await captures(session, pw.org_id)) == 1
        await assert_reconciled(session, pw.org_id)

    @pytest.mark.parametrize("source", ["receiving", "staging"])
    async def test_stock_may_come_from_either_place_it_waits_to_be_stored(
        self, client, session, pw, source
    ) -> None:
        src = {"receiving": pw.receiving, "staging": pw.staging}[source]
        resp = await put(client, pw, putaway_body(pw, from_location_id=str(src), quantity="10"))
        assert resp.status_code == 201, resp.text
        assert resp.json()["from_location"]["id"] == str(src)
        assert (await bucket_qty(session, pw.w, location=src, item=pw.dye))[0] == (
            q("10") if source == "receiving" else 0
        )

    async def test_a_shelf_is_a_storable_destination(self, client, session, pw) -> None:
        resp = await put(client, pw, putaway_body(pw, to_barcode="SHELF-1-BC"))
        assert resp.status_code == 201, resp.text
        assert resp.json()["to_location"]["kind"] == "shelf"
        assert (await bucket_qty(session, pw.w, location=pw.shelf, item=pw.dye))[0] == q("5")

    async def test_a_fraction_of_a_continuous_unit_is_exact(self, client, session, pw) -> None:
        resp = await put(client, pw, putaway_body(pw, quantity="2.125"))
        assert resp.status_code == 201
        assert (await bucket_qty(session, pw.w, location=pw.rack_bin, item=pw.dye))[0] == q("2.125")

    async def test_no_fix_still_puts_the_stock_away_and_keeps_a_capture_without_one(
        self, client, session, pw
    ) -> None:
        resp = await put(client, pw, putaway_body(pw))
        assert resp.status_code == 201
        assert resp.json()["geo_recorded"] is False and resp.json()["warnings"] == []
        [capture] = await captures(session, pw.org_id)
        assert (capture.lat_micro, capture.lng_micro, capture.accuracy_m) == (None, None, None)
        detail = await call(
            client, pw.org_id, [STOCK_READ], "GET", f"{PUTAWAY}/{resp.json()['move_id']}"
        )
        assert detail.json()["has_location"] is False

    async def test_the_worker_row_of_the_caller_is_linked_when_there_is_one(
        self, client, session, pw
    ) -> None:
        employee = uuid.uuid4()
        async with session.begin():
            worker = Worker(
                organization_id=pw.org_id,
                employee_id=employee,
                warehouse_id=pw.warehouse,
                display_name="Asha",
            )
            session.add(worker)
            await session.flush()
            worker_id = worker.id
        resp = await put(client, pw, putaway_body(pw), subject_ref=employee)
        assert resp.status_code == 201
        [capture] = await captures(session, pw.org_id)
        assert capture.worker_id == worker_id
        detail = await call(
            client, pw.org_id, [STOCK_READ], "GET", f"{PUTAWAY}/{resp.json()['move_id']}"
        )
        assert detail.json()["worker_id"] == str(worker_id)

    async def test_a_deleted_worker_or_another_tenants_worker_is_never_linked(
        self, client, session, admin_session, pw
    ) -> None:
        employee = uuid.uuid4()
        foreign = await make_foreign_world(admin_session)
        async with session.begin():
            session.add(
                Worker(
                    organization_id=pw.org_id,
                    employee_id=employee,
                    warehouse_id=pw.warehouse,
                    display_name="Left the company",
                    deleted_at=datetime.now(UTC),
                )
            )
        async with admin_session.begin():
            fw = (
                await admin_session.execute(
                    select(Warehouse).where(Warehouse.organization_id == foreign.org_id).limit(1)
                )
            ).scalar_one()
            admin_session.add(
                Worker(
                    organization_id=foreign.org_id,
                    employee_id=employee,  # the same person id, in another tenant
                    warehouse_id=fw.id,
                    display_name="Someone else's worker",
                )
            )
        resp = await put(client, pw, putaway_body(pw), subject_ref=employee)
        assert resp.status_code == 201
        [capture] = await captures(session, pw.org_id)
        assert capture.worker_id is None


# ------------------------------------------------------------------ the suggestion


class TestMatchedSuggestion:
    """`matched_suggestion` is the server's own judgement; the client only says what it *showed*."""

    async def test_scanning_the_bin_the_server_would_have_chosen_matches(
        self, client, session, pw
    ) -> None:
        resp = await put(client, pw, putaway_body(pw))  # R1-B01: the lowest pick sequence, empty
        assert resp.json()["matched_suggestion"] is True
        assert (await captures(session, pw.org_id))[0].matched_suggestion is True

    async def test_scanning_another_bin_does_not(self, client, session, pw) -> None:
        resp = await put(client, pw, putaway_body(pw, to_barcode="RACK-1-B02"))
        assert resp.status_code == 201
        assert resp.json()["matched_suggestion"] is False
        assert (await captures(session, pw.org_id))[0].matched_suggestion is False

    async def test_a_client_that_lies_about_what_it_showed_does_not_change_the_verdict(
        self, client, session, pw
    ) -> None:
        resp = await put(
            client,
            pw,
            putaway_body(pw, to_barcode="RACK-1-B02", suggested_location_id=str(pw.rack_bin2)),
        )
        assert resp.json()["matched_suggestion"] is False  # the server suggested R1-B01
        [capture] = await captures(session, pw.org_id)
        assert capture.suggested_location_id == pw.rack_bin2  # what the device claims it showed
        assert capture.matched_suggestion is False

    async def test_a_bin_that_already_holds_the_item_becomes_the_suggestion(
        self, client, session, pw
    ) -> None:
        await put(client, pw, putaway_body(pw, to_barcode="RACK-1-B02", quantity="1"), key="a")
        resp = await put(
            client, pw, putaway_body(pw, to_barcode="RACK-1-B02", quantity="1"), key="b"
        )
        assert (
            resp.json()["matched_suggestion"] is True
        )  # R1-B02 now holds the dye: keep like with like
        again = await put(
            client, pw, putaway_body(pw, to_barcode="RACK-1-B01", quantity="1"), key="c"
        )
        assert again.json()["matched_suggestion"] is False

    async def test_with_nothing_to_suggest_the_verdict_is_null_not_false(
        self, client, session, pw
    ) -> None:
        """Every storable bin holds some *other* item and none holds the dye: no suggestion, so the
        scan cannot have matched or missed it."""
        w = pw.w
        async with session.begin():
            lines = [
                line(w.t.buttons, loc, "3")
                for loc in (w.t.weaving, w.bin_b, pw.rack_bin, pw.rack_bin2, pw.shelf)
            ]
            await stock_ops.post_opening_stock(
                session,
                w.t.ctx,
                opening(lines),
                idempotency_key=None,
                occurred_on_explicit=False,
            )
        resp = await put(client, pw, putaway_body(pw))
        assert resp.status_code == 201
        assert resp.json()["matched_suggestion"] is None
        assert (await captures(session, pw.org_id))[0].matched_suggestion is None


# ------------------------------------------------------------------ bin_transfer regression


class TestBinTransferIsUnchanged:
    """The existing `/stock/transfers` is the other caller of the ledger helper put-away shares. It
    must stay exactly what it was: `bin_transfer`, `stock.transferred`, an optional key, any pair of
    kinds — and put-away must not leak into it, nor it into put-away."""

    TRANSFER_BODY = staticmethod(
        lambda pw, **extra: {
            "inventory_item_id": str(pw.dye),
            "from_location_id": str(pw.receiving),
            "to_location_id": str(pw.rack_bin),
            "quantity": "5",
            **extra,
        }
    )

    async def test_a_transfer_is_still_a_bin_transfer_that_stages_stock_transferred(
        self, client, session, pw
    ) -> None:
        resp = await call(
            client, pw.org_id, [STOCK_TRANSFER], "POST", TRANSFERS, json=self.TRANSFER_BODY(pw)
        )
        assert resp.status_code == 201, resp.text
        out = resp.json()
        assert (out["kind"], out["reference_type"], out["reference_id"]) == (
            "relocation",
            "bin_transfer",
            None,
        )
        move_id = out["move_id"]
        events = {
            e.event_type: e
            for e in await outbox_rows(session, pw.org_id)
            if str(e.aggregate_id) == move_id
        }
        assert set(events) == {EVT_TRANSFERRED, "operations.inventory.stock.posted"}
        payload = dict(events[EVT_TRANSFERRED].payload)
        # Byte-for-byte what it was: the nine keys of the committed v1 schema, no put-away fields.
        assert set(payload) == {
            "organization_id",
            "move_id",
            "inventory_item_id",
            "from_location_id",
            "to_location_id",
            "status",
            "lot_id",
            "roll_id",
            "quantity",
        }
        validate_event_schema(EVT_TRANSFERRED, 1, payload, contracts_root=CONTRACTS_ROOT)
        actions = {
            a.action for a in await audit_rows(session, pw.org_id) if str(a.entity_id) == move_id
        }
        assert actions == {"stock.transferred", "inventory_move.posted"}
        # …and nothing of put-away: no capture, no put-away audit row, no put-away event.
        assert await count(session, PutawayCapture, pw.org_id) == 0
        assert await audit_rows(session, pw.org_id, action="putaway.recorded") == []
        assert await outbox_rows(session, pw.org_id, event_type=EVT_PUTAWAY) == []
        async with session.begin():
            move = (
                await session.execute(select(InventoryMove).where(InventoryMove.id == move_id))
            ).scalar_one()
        assert (move.reference_type, move.reference_id, move.idempotency_key) == (
            "bin_transfer",
            None,
            None,
        )
        await assert_reconciled(session, pw.org_id)

    async def test_a_put_away_emits_putaway_recorded_and_not_stock_transferred(
        self, client, session, pw
    ) -> None:
        resp = await put(client, pw, putaway_body(pw))
        move_id = resp.json()["move_id"]
        types = {
            e.event_type
            for e in await outbox_rows(session, pw.org_id)
            if str(e.aggregate_id) == move_id
        }
        assert types == {EVT_PUTAWAY, "operations.inventory.stock.posted"}
        assert await outbox_rows(session, pw.org_id, event_type=EVT_TRANSFERRED) == []
        actions = {a.action for a in await audit_rows(session, pw.org_id)}
        assert "stock.transferred" not in actions

    async def test_the_two_operations_side_by_side_stay_what_they_are(
        self, client, session, pw
    ) -> None:
        await call(
            client, pw.org_id, [STOCK_TRANSFER], "POST", TRANSFERS, json=self.TRANSFER_BODY(pw)
        )
        await put(client, pw, putaway_body(pw, to_barcode="RACK-1-B02"))
        refs = sorted(
            [
                (m.reference_type, m.kind)
                async for m in _moves(session, pw.org_id)
                if m.kind == "relocation"
            ]
        )
        assert refs == [("bin_transfer", "relocation"), ("putaway", "relocation")]
        assert len(await outbox_rows(session, pw.org_id, event_type=EVT_TRANSFERRED)) == 1
        assert len(await outbox_rows(session, pw.org_id, event_type=EVT_PUTAWAY)) == 1
        assert await count(session, PutawayCapture, pw.org_id) == 1

    async def test_a_transfer_needs_no_key_and_stores_it_client_namespaced(
        self, client, session, pw
    ) -> None:
        no_key = await call(
            client, pw.org_id, [STOCK_TRANSFER], "POST", TRANSFERS, json=self.TRANSFER_BODY(pw)
        )
        assert no_key.status_code == 201
        keyed = await call(
            client,
            pw.org_id,
            [STOCK_TRANSFER],
            "POST",
            TRANSFERS,
            json=self.TRANSFER_BODY(pw, quantity="1"),
            key="raw-key",
        )
        assert keyed.status_code == 201
        async with session.begin():
            move = (
                await session.execute(
                    select(InventoryMove).where(InventoryMove.id == keyed.json()["move_id"])
                )
            ).scalar_one()
        # Client-namespaced, never given the put-away prefix (that is put-away's own).
        assert move.idempotency_key == ledger.client_move_key("raw-key")

    @pytest.mark.parametrize("kind", ["zone", "rack", "receiving", "staging", "quarantine", "dock"])
    async def test_a_transfer_still_goes_into_any_kind_of_location(
        self, client, session, pw, kind
    ) -> None:
        """Put-away's destination rule (a bin or a shelf) is put-away's. A transfer moves stock
        wherever it always could."""
        target = await make_location(session, pw, kind=kind, code=f"T-{kind.upper()}")
        resp = await call(
            client,
            pw.org_id,
            [STOCK_TRANSFER],
            "POST",
            TRANSFERS,
            json=self.TRANSFER_BODY(pw, to_location_id=str(target)),
        )
        assert resp.status_code == 201, resp.text
        assert (await bucket_qty(session, pw.w, location=target, item=pw.dye))[0] == q("5")

    async def test_a_transfer_out_of_a_storage_bin_still_works(self, client, session, pw) -> None:
        """Put-away is from receiving or staging only; moving stock *between* storage bins is the
        transfer's job and keeps working."""
        await put(client, pw, putaway_body(pw))
        resp = await call(
            client,
            pw.org_id,
            [STOCK_TRANSFER],
            "POST",
            TRANSFERS,
            json=self.TRANSFER_BODY(
                pw,
                from_location_id=str(pw.rack_bin),
                to_location_id=str(pw.rack_bin2),
                quantity="2",
            ),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["reference_type"] == "bin_transfer"
        await assert_reconciled(session, pw.org_id)

    async def test_the_same_raw_key_on_a_transfer_and_a_put_away_do_not_collide(
        self, client, session, pw
    ) -> None:
        a = await call(
            client,
            pw.org_id,
            [STOCK_TRANSFER],
            "POST",
            TRANSFERS,
            json=self.TRANSFER_BODY(pw),
            key="shared",
        )
        b = await put(client, pw, putaway_body(pw, quantity="1"), key="shared")
        assert (a.status_code, b.status_code) == (201, 201)
        assert a.json()["move_id"] != b.json()["move_id"]
        assert b.json()["replayed"] is False


async def _moves(session, org_id):  # type: ignore[no-untyped-def]
    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(InventoryMove).where(InventoryMove.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
    for row in rows:
        yield row


# ------------------------------------------------------------------ idempotency


class TestIdempotency:
    async def test_a_replay_is_200_and_writes_nothing_more(self, client, session, pw) -> None:
        body = putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC)
        first = await put(client, pw, body, key="k-1")
        ledger_rows = await count(session, LedgerEntry, pw.org_id)
        audits = await count(session, AuditLog, pw.org_id)
        events = await count(session, OutboxEvent, pw.org_id)

        again = await put(client, pw, body, key="k-1")
        assert (first.status_code, again.status_code) == (201, 200)
        assert again.json()["replayed"] is True
        assert again.json()["move_id"] == first.json()["move_id"]

        # One of each, however many times the retry arrives.
        for _ in range(2):
            await put(client, pw, body, key="k-1")
        assert len(await move_ids(session, pw.org_id, reference_type="putaway")) == 1
        assert len(await captures(session, pw.org_id)) == 1
        assert len(await audit_rows(session, pw.org_id, action="putaway.recorded")) == 1
        assert len(await outbox_rows(session, pw.org_id, event_type=EVT_PUTAWAY)) == 1
        assert await count(session, LedgerEntry, pw.org_id) == ledger_rows
        assert await count(session, AuditLog, pw.org_id) == audits
        assert await count(session, OutboxEvent, pw.org_id) == events
        assert (await bucket_qty(session, pw.w, location=pw.rack_bin, item=pw.dye))[0] == q("5")
        assert (await bucket_qty(session, pw.w, location=pw.receiving, item=pw.dye))[0] == q("15")
        await assert_reconciled(session, pw.org_id)

    async def test_the_replay_reports_what_was_stored_not_a_second_set_of_warnings(
        self, client, session, pw
    ) -> None:
        body = putaway_body(pw, lat_micro=LAT)  # unpaired: dropped, with a warning, the first time
        first = await put(client, pw, body, key="k-2")
        assert first.json()["warnings"] == ["geo_dropped:unpaired"]
        again = await put(client, pw, body, key="k-2")
        assert again.status_code == 200
        assert again.json()["warnings"] == []
        assert again.json()["geo_recorded"] is False
        assert again.json()["matched_suggestion"] is True

    async def test_the_replay_of_a_kept_fix_says_so(self, client, session, pw) -> None:
        body = putaway_body(pw, lat_micro=LAT, lng_micro=LNG, to_barcode="RACK-1-B02")
        await put(client, pw, body, key="k-3")
        again = await put(client, pw, body, key="k-3")
        assert again.json()["geo_recorded"] is True
        assert again.json()["matched_suggestion"] is False

    @pytest.mark.parametrize(
        "change",
        [
            {"quantity": "6"},
            {"note": "a different note"},
            {"to_barcode": "RACK-1-B02"},
            {"from_location_id": "staging"},
            {"inventory_item_id": "fabric", "quantity": "82.5", "lot_id": "lot", "roll_id": "roll"},
            {"occurred_on": "2026-09-01"},
        ],
        ids=["quantity", "note", "destination", "source", "item", "date"],
    )
    async def test_the_same_key_for_a_different_request_is_a_conflict(
        self, client, session, pw, change
    ) -> None:
        base = putaway_body(pw, occurred_on="2026-10-01")
        await put(client, pw, base, key="k-4")
        names = {
            "staging": str(pw.staging),
            "fabric": str(pw.fabric),
            "lot": str(pw.w.lot),
            "roll": str(pw.w.roll),
        }
        resolved = {k: names.get(v, v) if isinstance(v, str) else v for k, v in change.items()}
        other = await put(client, pw, {**base, **resolved}, key="k-4")
        assert other.status_code == 409, other.text
        assert len(await move_ids(session, pw.org_id, reference_type="putaway")) == 1
        assert len(await captures(session, pw.org_id)) == 1
        assert len(await audit_rows(session, pw.org_id, action="putaway.recorded")) == 1
        await assert_reconciled(session, pw.org_id)

    async def test_a_retry_that_differs_only_in_geo_replays_the_original(
        self, client, session, pw
    ) -> None:
        """Geo, `captured_at` and `suggested_location_id` are not in the request hash — a retry
        from a device that fixed its GPS in the meantime must not become a second move, a 409, or
        overwrite the first proof."""
        first = await put(
            client,
            pw,
            putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC),
            key="k-5",
        )
        retry = await put(
            client,
            pw,
            putaway_body(
                pw,
                lat_micro=LAT + 1_000,
                lng_micro=LNG + 1_000,
                accuracy_m=1,
                captured_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                suggested_location_id=str(pw.rack_bin2),
            ),
            key="k-5",
        )
        assert retry.status_code == 200 and retry.json()["move_id"] == first.json()["move_id"]
        [capture] = await captures(session, pw.org_id)
        assert (capture.lat_micro, capture.lng_micro, capture.accuracy_m) == (LAT, LNG, ACC)
        assert capture.suggested_location_id is None
        revealed = await call(
            client,
            pw.org_id,
            [REVEAL_PERM],
            "POST",
            f"{PUTAWAY}/{first.json()['move_id']}/location:reveal",
            amr=REVEAL_AMR,
        )
        assert (revealed.json()["lat_micro"], revealed.json()["lng_micro"]) == (LAT, LNG)

    async def test_different_keys_are_different_put_aways(self, client, session, pw) -> None:
        a = await put(client, pw, putaway_body(pw), key="k-6a")
        b = await put(client, pw, putaway_body(pw), key="k-6b")
        assert a.json()["move_id"] != b.json()["move_id"]
        assert len(await captures(session, pw.org_id)) == 2
        assert (await bucket_qty(session, pw.w, location=pw.rack_bin, item=pw.dye))[0] == q("10")
        await assert_reconciled(session, pw.org_id)

    async def test_the_key_is_required(self, client, session, pw) -> None:
        resp = await put(client, pw, putaway_body(pw), key=None)
        assert resp.status_code == 422
        assert resp.json()["errors"][0]["field"] == "header.Idempotency-Key"
        await stage_nothing_posted(session, pw)

    async def test_an_empty_key_is_refused(self, client, session, pw) -> None:
        resp = await put(client, pw, putaway_body(pw), key="")
        # An empty header value is no key at all.
        assert resp.status_code == 422
        await stage_nothing_posted(session, pw)

    async def test_the_key_is_at_most_120_characters_so_its_namespaced_form_fits(
        self, client, session, pw
    ) -> None:
        too_long = await put(client, pw, putaway_body(pw), key="x" * 121)
        assert too_long.status_code == 422
        await stage_nothing_posted(session, pw)
        fits = await put(client, pw, putaway_body(pw), key="x" * 120)
        assert fits.status_code == 201, fits.text
        async with session.begin():
            move = (
                await session.execute(
                    select(InventoryMove).where(InventoryMove.id == fits.json()["move_id"])
                )
            ).scalar_one()
        assert move.idempotency_key == "putaway:" + "x" * 120
        assert len(move.idempotency_key) == 128  # the column's width, exactly

    async def test_a_replay_survives_a_stale_suggestion_the_bin_was_since_filled(
        self, client, session, pw
    ) -> None:
        """What the retry *would* compute today (a different suggestion, another bin now holding the
        item) must not matter: the replay answers from what was stored."""
        body = putaway_body(pw)
        first = await put(client, pw, body, key="k-7")
        await put(client, pw, putaway_body(pw, to_barcode="RACK-1-B02"), key="k-7b")
        again = await put(client, pw, body, key="k-7")
        assert again.status_code == 200
        assert again.json()["matched_suggestion"] == first.json()["matched_suggestion"] is True


# ------------------------------------------------------------------ geo, over the wire


GEO_CASES: list[tuple[str, dict[str, Any], bool, list[str], tuple[Any, Any, Any]]] = [
    # id, body fields, geo_recorded, warnings, stored (lat, lng, accuracy)
    ("full", {"lat_micro": LAT, "lng_micro": LNG, "accuracy_m": ACC}, True, [], (LAT, LNG, ACC)),
    ("no-accuracy", {"lat_micro": LAT, "lng_micro": LNG}, True, [], (LAT, LNG, None)),
    ("zero-zero", {"lat_micro": 0, "lng_micro": 0, "accuracy_m": 0}, True, [], (0, 0, 0)),
    (
        "inclusive-edges",
        {"lat_micro": 90_000_000, "lng_micro": -180_000_000, "accuracy_m": 2_147_483_647},
        True,
        [],
        (90_000_000, -180_000_000, 2_147_483_647),
    ),
    ("none", {}, False, [], (None, None, None)),
    ("accuracy-alone", {"accuracy_m": 5}, False, [], (None, None, None)),
    ("lat-only", {"lat_micro": LAT}, False, ["geo_dropped:unpaired"], (None, None, None)),
    (
        "lng-only",
        {"lng_micro": LNG, "accuracy_m": 3},
        False,
        ["geo_dropped:unpaired"],
        (None, None, None),
    ),
    (
        "lat-high",
        {"lat_micro": 90_000_001, "lng_micro": LNG},
        False,
        ["geo_dropped:lat_out_of_range"],
        (None, None, None),
    ),
    (
        "lat-low",
        {"lat_micro": -90_000_001, "lng_micro": LNG},
        False,
        ["geo_dropped:lat_out_of_range"],
        (None, None, None),
    ),
    (
        "lng-high",
        {"lat_micro": LAT, "lng_micro": 180_000_001},
        False,
        ["geo_dropped:lng_out_of_range"],
        (None, None, None),
    ),
    (
        "lng-low",
        {"lat_micro": LAT, "lng_micro": -180_000_001},
        False,
        ["geo_dropped:lng_out_of_range"],
        (None, None, None),
    ),
    (
        "negative-accuracy",
        {"lat_micro": LAT, "lng_micro": LNG, "accuracy_m": -1},
        False,
        ["geo_dropped:accuracy_invalid"],
        (None, None, None),
    ),
    (
        "accuracy-overflows-int32",
        {"lat_micro": LAT, "lng_micro": LNG, "accuracy_m": 2_147_483_648},
        False,
        ["geo_dropped:accuracy_invalid"],
        (None, None, None),
    ),
    (
        "beyond-bigint",
        {"lat_micro": 10**30, "lng_micro": LNG},
        False,
        ["geo_dropped:lat_out_of_range"],
        (None, None, None),
    ),
]


class TestGeoOverTheWire:
    @pytest.mark.parametrize(
        ("fields", "recorded", "warnings", "stored"),
        [c[1:] for c in GEO_CASES],
        ids=[c[0] for c in GEO_CASES],
    )
    async def test_a_fix_is_kept_or_dropped_and_the_stock_moves_either_way(
        self, client, session, pw, fields, recorded, warnings, stored
    ) -> None:
        resp = await put(client, pw, putaway_body(pw, **fields))
        assert resp.status_code == 201, resp.text  # a bad fix never blocks the move
        assert resp.json()["geo_recorded"] is recorded
        assert resp.json()["warnings"] == warnings
        assert (await bucket_qty(session, pw.w, location=pw.rack_bin, item=pw.dye))[0] == q("5")
        [capture] = await captures(session, pw.org_id)
        assert (capture.lat_micro, capture.lng_micro, capture.accuracy_m) == stored
        detail = await call(
            client, pw.org_id, [STOCK_READ], "GET", f"{PUTAWAY}/{resp.json()['move_id']}"
        )
        assert detail.json()["has_location"] is recorded
        await assert_reconciled(session, pw.org_id)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("lat_micro", 12.9716),  # a float: degrees, not micro-degrees
            ("lat_micro", 12_971_600.0),  # an integral float is still a float
            ("lng_micro", "77594600"),  # a string
            ("lat_micro", True),  # a bool is not an integer here
            ("accuracy_m", 8.5),
            ("accuracy_m", "8"),
            ("lat_micro", [LAT]),
        ],
    )
    async def test_a_coordinate_that_is_not_an_integer_is_a_422_and_posts_nothing(
        self, client, session, pw, field, value
    ) -> None:
        extra = {"lat_micro": LAT, "lng_micro": LNG, field: value}
        resp = await put(client, pw, putaway_body(pw, **extra))
        assert resp.status_code == 422, resp.text
        assert resp.json()["errors"][0]["field"] == field
        await stage_nothing_posted(session, pw)

    @pytest.mark.parametrize(
        ("when", "kept"),
        [
            (lambda n: n - timedelta(minutes=2), True),
            (lambda n: n - timedelta(days=6), True),
            (lambda n: n + timedelta(minutes=3), True),
            (lambda n: n + timedelta(hours=1), False),
            (lambda n: n + timedelta(days=365), False),
            (lambda n: n - timedelta(days=8), False),
            (lambda n: datetime(2000, 1, 1, tzinfo=UTC), False),
        ],
        ids=[
            "2-min-ago",
            "6-days-ago",
            "3-min-ahead",
            "1-hour-ahead",
            "a-year-ahead",
            "8-days-ago",
            "y2k",
        ],
    )
    async def test_captured_at_is_believed_only_when_plausible(
        self, client, session, pw, when, kept
    ) -> None:
        claimed = when(datetime.now(UTC))
        resp = await put(client, pw, putaway_body(pw, captured_at=claimed.isoformat()))
        assert resp.status_code == 201, resp.text
        assert resp.json()["warnings"] == ([] if kept else ["captured_at_adjusted"])
        [capture] = await captures(session, pw.org_id)
        if kept:
            assert capture.captured_at == claimed
        else:
            assert abs(capture.captured_at - datetime.now(UTC)) < timedelta(minutes=1)

    async def test_a_claimed_time_with_no_zone_is_replaced_not_refused(
        self, client, session, pw
    ) -> None:
        resp = await put(client, pw, putaway_body(pw, captured_at="2026-10-05T10:00:00"))
        assert resp.status_code == 201
        assert resp.json()["warnings"] == ["captured_at_adjusted"]

    async def test_a_claimed_time_that_is_not_a_time_is_a_422(self, client, session, pw) -> None:
        resp = await put(client, pw, putaway_body(pw, captured_at="yesterday"))
        assert resp.status_code == 422
        await stage_nothing_posted(session, pw)

    async def test_a_dropped_fix_and_an_adjusted_time_are_both_reported(
        self, client, session, pw
    ) -> None:
        resp = await put(
            client,
            pw,
            putaway_body(
                pw, lat_micro=LAT, captured_at=(datetime.now(UTC) + timedelta(days=1)).isoformat()
            ),
        )
        assert resp.json()["warnings"] == ["geo_dropped:unpaired", "captured_at_adjusted"]


class TestTheDatabaseIsTheBackstop:
    """Python validates the fix before the move posts, so the CHECK constraints are never what a
    caller sees — but they are what makes a bug in that validation, or a writer that skips it, a
    refused row instead of a bad one. Driven by raw `INSERT`s as the app role, bypassing every
    Python rule."""

    INSERT = text(
        """
        INSERT INTO operations.putaway_captures
            (id, organization_id, move_id, location_id, created_by, lat_micro, lng_micro,
             accuracy_m, captured_at)
        VALUES (gen_random_uuid(), :org, :move, :loc, gen_random_uuid(), :lat, :lng, :acc, now())
        """
    )

    async def _free_move(self, client, pw) -> str:  # type: ignore[no-untyped-def]
        """A move with no capture yet (a bin transfer), for a raw capture row to reference."""
        resp = await call(
            client,
            pw.org_id,
            [STOCK_TRANSFER],
            "POST",
            TRANSFERS,
            json={
                "inventory_item_id": str(pw.dye),
                "from_location_id": str(pw.receiving),
                "to_location_id": str(pw.rack_bin),
                "quantity": "1",
            },
        )
        assert resp.status_code == 201
        return str(resp.json()["move_id"])

    @pytest.mark.parametrize(
        ("lat", "lng", "acc", "constraint"),
        [
            (LAT, None, None, "ck_putaway_captures_coords_paired"),
            (None, LNG, None, "ck_putaway_captures_coords_paired"),
            (90_000_001, 0, None, "ck_putaway_captures_lat_in_range"),
            (-90_000_001, 0, None, "ck_putaway_captures_lat_in_range"),
            (0, 180_000_001, None, "ck_putaway_captures_lng_in_range"),
            (0, -180_000_001, None, "ck_putaway_captures_lng_in_range"),
            (0, 0, -1, "ck_putaway_captures_accuracy_non_negative"),
        ],
    )
    async def test_a_row_that_breaks_a_check_is_refused_by_name(
        self, client, session, pw, lat, lng, acc, constraint
    ) -> None:
        move = await self._free_move(client, pw)
        with pytest.raises(IntegrityError, match=constraint):
            async with session.begin():
                await session.execute(
                    self.INSERT,
                    {
                        "org": pw.org_id,
                        "move": move,
                        "loc": pw.rack_bin,
                        "lat": lat,
                        "lng": lng,
                        "acc": acc,
                    },
                )
        assert await count(session, PutawayCapture, pw.org_id) == 0

    @pytest.mark.parametrize(
        ("lat", "lng", "acc"),
        [
            (None, None, None),
            (0, 0, 0),
            (90_000_000, 180_000_000, 0),
            (-90_000_000, -180_000_000, None),
        ],
    )
    async def test_the_edges_the_checks_allow_are_accepted(
        self, client, session, pw, lat, lng, acc
    ) -> None:
        move = await self._free_move(client, pw)
        async with session.begin():
            await session.execute(
                self.INSERT,
                {
                    "org": pw.org_id,
                    "move": move,
                    "loc": pw.rack_bin,
                    "lat": lat,
                    "lng": lng,
                    "acc": acc,
                },
            )
        assert await count(session, PutawayCapture, pw.org_id) == 1

    async def test_one_capture_per_move(self, client, session, pw) -> None:
        resp = await put(client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG))
        with pytest.raises(IntegrityError, match="uq_putaway_captures_org_move"):
            async with session.begin():
                await session.execute(
                    self.INSERT,
                    {
                        "org": pw.org_id,
                        "move": resp.json()["move_id"],
                        "loc": pw.rack_bin,
                        "lat": None,
                        "lng": None,
                        "acc": None,
                    },
                )
        assert len(await captures(session, pw.org_id)) == 1


class TestAFailedCaptureLeavesTheMovePosted:
    """The stock move is the thing that matters; the capture is proof. If writing the proof fails
    (forced here — Python's pre-validation makes it unreachable otherwise), the stock must still
    be where the worker put it, the response must say the proof was not kept, and nothing the
    failure touched may take the move down with it."""

    @pytest.fixture
    def broken_capture(self, monkeypatch):  # type: ignore[no-untyped-def]
        real = stock_ops.PutawayCapture

        def broken(**kwargs):  # type: ignore[no-untyped-def]
            # Violates `ck_putaway_captures_accuracy_non_negative` at flush.
            return real(**{**kwargs, "accuracy_m": -1})

        monkeypatch.setattr(stock_ops, "PutawayCapture", broken)

    async def test_the_move_stands_and_the_response_says_the_proof_was_not_kept(
        self, client, session, pw, broken_capture
    ) -> None:
        resp = await put(client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC))
        assert resp.status_code == 201, resp.text
        out = resp.json()
        assert out["warnings"] == ["geo_not_recorded"] and out["geo_recorded"] is False
        assert (await bucket_qty(session, pw.w, location=pw.rack_bin, item=pw.dye))[0] == q("5")
        assert (await bucket_qty(session, pw.w, location=pw.receiving, item=pw.dye))[0] == q("15")
        assert await captures(session, pw.org_id) == []
        assert len(await move_ids(session, pw.org_id, reference_type="putaway")) == 1
        # The audit row and the event of the put-away itself are still staged — and honest about it.
        [event] = await outbox_rows(session, pw.org_id, event_type=EVT_PUTAWAY)
        assert event.payload["has_location"] is False
        assert len(await audit_rows(session, pw.org_id, action="putaway.recorded")) == 1
        await assert_reconciled(session, pw.org_id)

    async def test_a_tracked_rolls_history_row_survives_the_failed_capture(
        self, client, session, pw, broken_capture
    ) -> None:
        resp = await put(client, pw, tracked_body(pw, lat_micro=LAT, lng_micro=LNG))
        assert resp.status_code == 201 and resp.json()["warnings"] == ["geo_not_recorded"]
        async with session.begin():
            moved = (
                (
                    await session.execute(
                        select(RollEvent).where(
                            RollEvent.roll_id == pw.w.roll, RollEvent.kind == "MOVED"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(moved) == 1  # the savepoint rolled back the capture, not the roll's history
        assert (
            await bucket_qty(
                session, pw.w, location=pw.rack_bin, item=pw.fabric, lot=pw.w.lot, roll=pw.w.roll
            )
        )[0] == q("82.5")
        await assert_reconciled(session, pw.org_id)

    async def test_no_capture_means_the_detail_and_the_reveal_are_404_and_the_reveal_audits_nothing(
        self, client, session, pw, broken_capture
    ) -> None:
        resp = await put(client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG))
        move = resp.json()["move_id"]
        detail = await call(client, pw.org_id, [STOCK_READ], "GET", f"{PUTAWAY}/{move}")
        reveal = await call(
            client,
            pw.org_id,
            [REVEAL_PERM],
            "POST",
            f"{PUTAWAY}/{move}/location:reveal",
            amr=REVEAL_AMR,
        )
        assert (detail.status_code, reveal.status_code) == (404, 404)
        assert await audit_rows(session, pw.org_id, action="entity.sensitive_read") == []

    async def test_a_replay_of_it_is_a_replay_not_a_second_try_at_the_capture(
        self, client, session, pw, broken_capture, monkeypatch
    ) -> None:
        body = putaway_body(pw, lat_micro=LAT, lng_micro=LNG)
        await put(client, pw, body, key="k-fail")
        monkeypatch.undo()  # the capture insert would work now
        again = await put(client, pw, body, key="k-fail")
        assert again.status_code == 200 and again.json()["replayed"] is True
        assert again.json()["geo_recorded"] is False and again.json()["matched_suggestion"] is None
        assert (
            await captures(session, pw.org_id) == []
        )  # the original is the record; no late capture


# ------------------------------------------------------------------ the validation matrix


def _error(resp) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return resp.json()["errors"][0]


class TestValidation:
    # -------------------------------------------------- tracking

    async def test_a_tracked_item_must_say_which_lot_and_which_roll(
        self, client, session, pw
    ) -> None:
        none = await put(client, pw, tracked_body(pw, lot_id=None, roll_id=None))
        assert (none.status_code, _error(none)["field"], _error(none)["code"]) == (
            422,
            "lot_id",
            "required",
        )
        roll_missing = await put(client, pw, tracked_body(pw, roll_id=None), key="pa-2")
        assert (roll_missing.status_code, _error(roll_missing)["field"]) == (422, "roll_id")
        await stage_nothing_posted(session, pw)

    @pytest.mark.parametrize("field", ["lot_id", "roll_id"])
    async def test_an_untracked_item_refuses_a_lot_or_a_roll(
        self, client, session, pw, field
    ) -> None:
        value = {"lot_id": pw.w.lot, "roll_id": pw.w.roll}[field]
        resp = await put(client, pw, putaway_body(pw, **{field: str(value)}))
        assert (resp.status_code, _error(resp)["code"], _error(resp)["field"]) == (
            422,
            "not_tracked",
            field,
        )
        await stage_nothing_posted(session, pw)

    # -------------------------------------------------- from == to

    async def test_scanning_the_bin_the_stock_is_already_in_is_refused(
        self, client, session, pw
    ) -> None:
        # RCV-1 carries a barcode, so the worker can scan it as the "destination".
        resp = await put(client, pw, putaway_body(pw, to_barcode="RCV-1-BC"))
        assert resp.status_code == 422, resp.text
        assert (_error(resp)["code"], _error(resp)["field"]) == ("same_location", "to_barcode")
        await stage_nothing_posted(session, pw)
        assert (await bucket_qty(session, pw.w, location=pw.receiving, item=pw.dye))[0] == q("20")

    # -------------------------------------------------- source kind (D1)

    @pytest.mark.parametrize(
        "kind",
        ["bin", "shelf", "zone", "aisle", "rack", "dock", "pack_station", "returns", "quarantine"],
    )
    async def test_a_source_that_is_not_receiving_or_staging_is_refused(
        self, client, session, pw, kind
    ) -> None:
        source = await make_location(session, pw, kind=kind, code=f"SRC-{kind.upper()}")
        resp = await put(client, pw, putaway_body(pw, from_location_id=str(source)))
        assert resp.status_code == 422, resp.text
        assert _error(resp)["code"] == "source_not_receiving_or_staging"
        assert _error(resp)["field"] == "from_location_id"
        await stage_nothing_posted(session, pw)

    async def test_a_storage_bin_holding_stock_cannot_be_the_source(
        self, client, session, pw
    ) -> None:
        """The transfer's job: put the dye away, then try to put it away again from where it is."""
        await put(client, pw, putaway_body(pw), key="first")
        resp = await put(
            client,
            pw,
            putaway_body(pw, from_location_id=str(pw.rack_bin), to_barcode="RACK-1-B02"),
            key="second",
        )
        assert (resp.status_code, _error(resp)["code"]) == (422, "source_not_receiving_or_staging")
        assert (await bucket_qty(session, pw.w, location=pw.rack_bin, item=pw.dye))[0] == q("5")

    # -------------------------------------------------- destination kind

    @pytest.mark.parametrize(
        "kind",
        [
            "zone",
            "aisle",
            "rack",
            "staging",
            "pack_station",
            "dock",
            "receiving",
            "returns",
            "quarantine",
        ],
    )
    async def test_a_destination_that_is_not_a_bin_or_a_shelf_is_refused(
        self, client, session, pw, kind
    ) -> None:
        dest = await make_location(
            session, pw, kind=kind, code=f"DST-{kind.upper()}", barcode=f"DST-{kind.upper()}-BC"
        )
        resp = await put(client, pw, putaway_body(pw, to_barcode=f"DST-{kind.upper()}-BC"))
        assert resp.status_code == 422, resp.text
        assert (_error(resp)["code"], _error(resp)["field"]) == (
            "destination_not_storable",
            "to_barcode",
        )
        assert (await bucket_qty(session, pw.w, location=dest, item=pw.dye))[0] == 0
        await stage_nothing_posted(session, pw)

    # -------------------------------------------------- warehouse

    async def test_a_bin_in_another_warehouse_is_refused(self, client, session, pw) -> None:
        resp = await put(client, pw, putaway_body(pw, to_barcode="FIN-B01-BC"))
        assert resp.status_code == 422, resp.text
        assert (_error(resp)["code"], _error(resp)["field"]) == (
            "different_warehouse",
            "to_barcode",
        )
        assert (await bucket_qty(session, pw.w, location=pw.other_warehouse_bin, item=pw.dye))[
            0
        ] == 0
        await stage_nothing_posted(session, pw)

    # -------------------------------------------------- stock

    async def test_reserved_stock_cannot_be_put_away_and_the_reservation_is_named(
        self, client, session, pw
    ) -> None:
        async with session.begin():
            made = await ledger.reserve(
                session,
                pw.w.t.ctx,
                item_id=pw.dye,
                location_id=pw.receiving,
                quantity_milli=q("15"),
                reference_type="sales_order",
                reference_id=uuid.uuid4(),
            )
            reservation_id = made.reservation.id
        refused = await put(client, pw, putaway_body(pw, quantity="5.001"), key="over")
        assert refused.status_code == 409, refused.text
        reservations = _error(refused)["reservations"]
        assert [r["reservation_id"] for r in reservations] == [str(reservation_id)]
        await stage_nothing_posted(session, pw)
        # The five that are free still move.
        ok = await put(client, pw, putaway_body(pw, quantity="5"), key="free")
        assert ok.status_code == 201, ok.text
        await assert_reconciled(session, pw.org_id)

    async def test_more_than_is_there_is_a_409_with_the_numbers(self, client, session, pw) -> None:
        resp = await put(client, pw, putaway_body(pw, quantity="20.001"))
        assert resp.status_code == 409
        assert "20" in resp.text
        await stage_nothing_posted(session, pw)
        assert (await bucket_qty(session, pw.w, location=pw.receiving, item=pw.dye))[0] == q("20")

    async def test_only_available_stock_is_put_away(self, client, session, pw) -> None:
        """Stock on quality hold sits in the receiving bin too; a put-away moves available stock
        only, so a bin holding nothing *but* held stock has nothing to put away."""
        held = await make_location(
            session, pw, kind="receiving", code="RCV-HOLD", barcode="RCV-HOLD-BC"
        )
        async with session.begin():
            await stock_ops.post_opening_stock(
                session,
                pw.w.t.ctx,
                opening(
                    [
                        line(pw.dye, held, "5", status="QUALITY_HOLD"),
                    ]
                ),
                idempotency_key=None,
                occurred_on_explicit=False,
            )
        resp = await put(client, pw, putaway_body(pw, from_location_id=str(held), quantity="1"))
        assert resp.status_code == 409
        assert (await bucket_qty(session, pw.w, location=held, status="QUALITY_HOLD", item=pw.dye))[
            0
        ] == q("5")
        await stage_nothing_posted(session, pw)

    async def test_the_body_has_no_status_field_a_put_away_is_always_available_stock(
        self, client, session, pw
    ) -> None:
        resp = await put(client, pw, putaway_body(pw, status="QUALITY_HOLD"))
        assert resp.status_code == 422
        await stage_nothing_posted(session, pw)

    @pytest.mark.parametrize("quantity", ["0", "-1", "abc", "1e3", "", "1.2345", " 5"])
    async def test_malformed_quantities_are_refused_not_rounded(
        self, client, session, pw, quantity
    ) -> None:
        resp = await put(client, pw, putaway_body(pw, quantity=quantity))
        assert resp.status_code == 422
        await stage_nothing_posted(session, pw)

    async def test_a_json_number_is_not_a_quantity(self, client, session, pw) -> None:
        resp = await put(client, pw, putaway_body(pw, quantity=5))
        assert resp.status_code == 422
        await stage_nothing_posted(session, pw)

    async def test_a_discrete_unit_refuses_a_fraction(self, client, session, pw) -> None:
        async with session.begin():
            await stock_ops.post_opening_stock(
                session,
                pw.w.t.ctx,
                opening([line(pw.w.t.buttons, pw.receiving, "100")]),
                idempotency_key=None,
                occurred_on_explicit=False,
            )
        resp = await put(
            client, pw, putaway_body(pw, inventory_item_id=str(pw.w.t.buttons), quantity="1.5")
        )
        assert resp.status_code == 422
        whole = await put(
            client,
            pw,
            putaway_body(pw, inventory_item_id=str(pw.w.t.buttons), quantity="40"),
            key="whole",
        )
        assert whole.status_code == 201, whole.text

    # -------------------------------------------------- the body

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("unexpected", 1),
            ("to_location_id", str(uuid.uuid4())),  # the destination is a barcode, never an id
            ("note", "n" * 501),
            ("occurred_on", (datetime.now(UTC) + timedelta(days=3)).date().isoformat()),
            ("inventory_item_id", "not-a-uuid"),
            ("from_location_id", "not-a-uuid"),
        ],
    )
    async def test_a_malformed_body_is_a_422_and_posts_nothing(
        self, client, session, pw, field, value
    ) -> None:
        resp = await put(client, pw, putaway_body(pw, **{field: value}))
        assert resp.status_code == 422, resp.text
        await stage_nothing_posted(session, pw)

    @pytest.mark.parametrize(
        "missing", ["inventory_item_id", "from_location_id", "to_barcode", "quantity"]
    )
    async def test_every_required_field_is_required(self, client, session, pw, missing) -> None:
        resp = await put(client, pw, putaway_body(pw, **{missing: None}))
        assert resp.status_code == 422
        assert _error(resp)["field"] == missing
        await stage_nothing_posted(session, pw)

    # -------------------------------------------------- the barcode

    @pytest.mark.parametrize(
        "barcode",
        [
            "RACK-1-B01\r\n",  # what a scanner appends: the apps trim it, the server refuses it
            "RACK-1-B01\n",
            "RACK-1-B01\r",
            "\tRACK-1-B01",
            "RACK 1 B01",
            "RACK/1/B01",
            "",
            "B" * 65,
            "RÄCK-1",
        ],
        ids=["crlf", "lf", "cr", "tab", "space", "slash", "empty", "65-chars", "non-ascii"],
    )
    async def test_a_barcode_not_in_the_stored_shape_is_a_422_never_trimmed_into_a_match(
        self, client, session, pw, barcode
    ) -> None:
        resp = await put(client, pw, putaway_body(pw, to_barcode=barcode))
        assert resp.status_code == 422, resp.text
        assert _error(resp)["field"] == "to_barcode"
        await stage_nothing_posted(session, pw)

    async def test_a_barcode_is_matched_exactly(self, client, session, pw) -> None:
        resp = await put(client, pw, putaway_body(pw, to_barcode="rack-1-b01"))
        assert resp.status_code == 404
        await stage_nothing_posted(session, pw)

    async def test_an_unknown_foreign_and_deleted_barcode_are_one_and_the_same_404(
        self, client, session, admin_session, pw
    ) -> None:
        foreign = await foreign_barcoded_bin(admin_session, "THEIRS-1")
        dead = await make_location(session, pw, kind="bin", code="DEAD-1", barcode="DEAD-1-BC")
        async with session.begin():
            await inv_services.delete_location(session, pw.w.t.ctx, dead)

        bodies = []
        for i, barcode in enumerate(["NOPE-1", "THEIRS-1", "DEAD-1-BC"]):
            resp = await put(client, pw, putaway_body(pw, to_barcode=barcode), key=f"k-{i}")
            assert resp.status_code == 404, (barcode, resp.text)
            body = resp.json()
            bodies.append({k: v for k, v in body.items() if k != "request_id"})
        assert bodies[0] == bodies[1] == bodies[2]
        assert foreign  # the foreign bin existed — and was not a hint of anything
        await stage_nothing_posted(session, pw)

    async def test_a_barcode_that_exists_in_both_tenants_resolves_to_the_callers_own(
        self, client, session, admin_session, pw
    ) -> None:
        theirs = await foreign_barcoded_bin(admin_session, "RACK-1-B01")  # the same string
        resp = await put(client, pw, putaway_body(pw))
        assert resp.status_code == 201
        assert resp.json()["to_location"]["id"] == str(pw.rack_bin) != str(theirs)

    async def test_a_barcode_reused_after_its_bin_was_deleted_resolves_to_the_new_bin_only(
        self, client, session, pw
    ) -> None:
        old = await make_location(session, pw, kind="bin", code="OLD-1", barcode="REUSED-BC")
        async with session.begin():
            await inv_services.delete_location(session, pw.w.t.ctx, old)
        new = await make_location(session, pw, kind="bin", code="NEW-1", barcode="REUSED-BC")
        resp = await put(client, pw, putaway_body(pw, to_barcode="REUSED-BC"))
        assert resp.status_code == 201, resp.text
        assert resp.json()["to_location"]["id"] == str(new) != str(old)
        assert (await bucket_qty(session, pw.w, location=new, item=pw.dye))[0] == q("5")
        assert (await bucket_qty(session, pw.w, location=old, item=pw.dye))[0] == 0
        [capture] = await captures(session, pw.org_id)
        assert capture.location_id == new

    # -------------------------------------------------- ids that are not the caller's

    async def test_ids_that_are_unknown_or_another_tenants_are_404_and_post_nothing(
        self, client, session, admin_session, pw
    ) -> None:
        foreign = await make_foreign_world(admin_session)
        cases = {
            "unknown item": putaway_body(pw, inventory_item_id=str(uuid.uuid4())),
            "foreign item": putaway_body(pw, inventory_item_id=str(foreign.t.dye)),
            "unknown source": putaway_body(pw, from_location_id=str(uuid.uuid4())),
            "foreign source": putaway_body(pw, from_location_id=str(foreign.t.weaving)),
            "foreign lot": tracked_body(pw, lot_id=str(foreign.lot)),
            "foreign roll": tracked_body(pw, roll_id=str(foreign.roll)),
        }
        for i, (name, body) in enumerate(cases.items()):
            resp = await put(client, pw, body, key=f"k-{i}")
            assert resp.status_code == 404, (name, resp.status_code, resp.text)
        await stage_nothing_posted(session, pw)

    # -------------------------------------------------- a suggestion the server cannot resolve

    async def test_an_unknown_deleted_or_foreign_suggestion_is_ignored_not_a_404(
        self, client, session, admin_session, pw
    ) -> None:
        """`suggested_location_id` is what the device showed, not something the move depends on: a
        put-away queued offline must not park over a bin deleted since. The move posts, the id is
        not stored, and the warning is the same word for all three causes — the response never says
        whether the id exists in another tenant."""
        foreign = await make_foreign_world(admin_session)
        dead = await make_location(session, pw, kind="bin", code="DEAD-1", barcode="DEAD-1-BC")
        async with session.begin():
            await inv_services.delete_location(session, pw.w.t.ctx, dead)
        cases = {
            "unknown": uuid.uuid4(),
            "foreign": foreign.t.weaving,
            "deleted": dead,
        }
        bodies = []
        for i, (name, suggested) in enumerate(cases.items()):
            resp = await put(
                client,
                pw,
                putaway_body(pw, suggested_location_id=str(suggested), quantity="1"),
                key=f"s-{i}",
            )
            assert resp.status_code == 201, (name, resp.status_code, resp.text)
            out = resp.json()
            assert out["warnings"] == ["suggestion_ignored"], name
            assert out["matched_suggestion"] is not None, name
            bodies.append({k: v for k, v in out.items() if k != "move_id"})
        assert bodies[0] == bodies[1] == bodies[2]

        stored = await captures(session, pw.org_id)
        assert len(stored) == 3
        assert [c.suggested_location_id for c in stored] == [None, None, None]
        assert len(await move_ids(session, pw.org_id, reference_type="putaway")) == 3
        assert (await bucket_qty(session, pw.w, location=pw.rack_bin, item=pw.dye))[0] == q("3")
        await assert_reconciled(session, pw.org_id)

    async def test_an_ignored_suggestion_does_not_change_the_servers_own_verdict(
        self, client, session, pw
    ) -> None:
        """`matched_suggestion` is judged against the server's rule whatever the device claims: the
        suggestion R1-B01 matches a scan of R1-B01 and misses a scan of R1-B02, ignored id or
        not."""
        hit = await put(
            client, pw, putaway_body(pw, suggested_location_id=str(uuid.uuid4())), key="v-1"
        )
        miss = await put(
            client,
            pw,
            putaway_body(
                pw, to_barcode="RACK-1-B02", suggested_location_id=str(uuid.uuid4()), quantity="1"
            ),
            key="v-2",
        )
        assert hit.json()["matched_suggestion"] is True
        assert miss.json()["matched_suggestion"] is False
        assert hit.json()["warnings"] == miss.json()["warnings"] == ["suggestion_ignored"]
        by_move = {str(c.move_id): c for c in await captures(session, pw.org_id)}
        assert by_move[hit.json()["move_id"]].matched_suggestion is True
        assert by_move[miss.json()["move_id"]].matched_suggestion is False

    async def test_a_resolvable_suggestion_is_stored_and_raises_no_warning(
        self, client, session, pw
    ) -> None:
        resp = await put(
            client, pw, putaway_body(pw, suggested_location_id=str(pw.rack_bin2)), key="r-1"
        )
        assert resp.status_code == 201
        assert resp.json()["warnings"] == []
        [capture] = await captures(session, pw.org_id)
        assert capture.suggested_location_id == pw.rack_bin2

    async def test_an_ignored_suggestion_sits_beside_the_other_warnings(
        self, client, session, pw
    ) -> None:
        resp = await put(
            client,
            pw,
            putaway_body(pw, suggested_location_id=str(uuid.uuid4()), lat_micro=LAT),
            key="w-1",
        )
        assert resp.status_code == 201
        assert resp.json()["warnings"] == ["suggestion_ignored", "geo_dropped:unpaired"]

    async def test_the_replay_of_a_put_away_with_an_ignored_suggestion_is_a_quiet_200(
        self, client, session, pw
    ) -> None:
        body = putaway_body(pw, suggested_location_id=str(uuid.uuid4()))
        first = await put(client, pw, body, key="p-1")
        again = await put(client, pw, body, key="p-1")
        assert (first.status_code, again.status_code) == (201, 200)
        assert again.json()["move_id"] == first.json()["move_id"]
        assert again.json()["warnings"] == []
        assert len(await captures(session, pw.org_id)) == 1
        assert len(await move_ids(session, pw.org_id, reference_type="putaway")) == 1

    async def test_a_suggestion_that_is_not_a_uuid_is_still_a_422(
        self, client, session, pw
    ) -> None:
        """Ignoring a *resolvable-but-wrong* id is not leniency about the shape of the field."""
        resp = await put(
            client, pw, putaway_body(pw, suggested_location_id="not-a-uuid"), key="u-1"
        )
        assert resp.status_code == 422
        await stage_nothing_posted(session, pw)
