"""The audited reveal, and the proof that a worker's coordinates exist **nowhere else**.

Owner decision D3 (2026-10-05): only the administrator may see where a worker stood, every sighting
is audited, and the coordinates sit behind that one door. So this file tests two things:

* **the door** — N successful reveals write N `entity.sensitive_read` rows carrying field *names*
  only (never values); a reveal of a capture with no fix still audits; a refused or 404 reveal
  writes nothing; the response is `Cache-Control: no-store`; there is no GET (or any other verb);
* **the walls** — a recursive scan of every audit row, outbox payload, every other table of the
  schema, every response a non-reveal route sends, and every log line, for a coordinate key or the
  digits of the fix the tests send. The only place they may be found is the `putaway_captures` row
  itself and the reveal's own response.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from structlog.testing import capture_logs

from app.catalogue import OPERATIONS_ROLE_TEMPLATES
from app.inventory import putaway_services, stock_ops
from app.inventory.putaway_schemas import PutawayIn
from app.models import AuditLog
from platform_core.auth import Scope, ScopeContext
from tests.helpers import principal_for
from tests.putaway_fixtures import (
    ACC,
    LAT,
    LNG,
    PUTAWAY,
    REVEAL_PERM,
    STOCK_READ,
    STOCK_TRANSFER,
    PutawayWorld,
    audit_rows,
    build_putaway_world,
    call,
    captures,
    coordinate_leaks,
    outbox_rows,
    put,
    putaway_body,
    tracked_body,
)

pytestmark = pytest.mark.integration

REVEALED_FIELDS = ["lat_micro", "lng_micro", "accuracy_m"]
READ = "entity.sensitive_read"
TRANSFERS = "/operations/inventory/stock/transfers"


@pytest.fixture
async def pw(session, org_id) -> PutawayWorld:
    return await build_putaway_world(session, org_id)


async def reveal(client, pw: PutawayWorld, move_id: Any, *, principal=None):  # type: ignore[no-untyped-def]
    return await call(
        client,
        pw.org_id,
        [REVEAL_PERM],
        "POST",
        f"{PUTAWAY}/{move_id}/location:reveal",
        principal=principal,
    )


async def reads(session, pw: PutawayWorld) -> list[AuditLog]:
    return await audit_rows(session, pw.org_id, action=READ)


# ------------------------------------------------------------------ the door


class TestTheRevealIsAuditedEveryTime:
    async def test_the_response_is_the_fix_and_nothing_else_and_is_never_cached(
        self, client, session, pw
    ) -> None:
        made = await put(client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC))
        resp = await reveal(client, pw, made.json()["move_id"])
        assert resp.status_code == 200, resp.text
        assert resp.headers["cache-control"] == "no-store"
        body = resp.json()
        assert set(body) == {"lat_micro", "lng_micro", "accuracy_m", "captured_at"}
        assert (body["lat_micro"], body["lng_micro"], body["accuracy_m"]) == (LAT, LNG, ACC)
        [capture] = await captures(session, pw.org_id)
        assert body["captured_at"].startswith(capture.captured_at.strftime("%Y-%m-%dT%H:%M:%S"))

    async def test_n_reveals_write_n_rows_each_naming_fields_and_never_values(
        self, client, session, pw
    ) -> None:
        made = await put(client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC))
        move_id = made.json()["move_id"]
        admins = [principal_for(pw.org_id, [REVEAL_PERM]) for _ in range(2)]
        calls = 5
        for i in range(calls):
            resp = await reveal(client, pw, move_id, principal=admins[i % 2])
            assert resp.status_code == 200
        rows = await reads(session, pw)
        assert len(rows) == calls
        for row in rows:
            assert row.action == READ and row.entity == "inventory_move"
            assert str(row.entity_id) == move_id
            assert row.after == {"fields": REVEALED_FIELDS}
            assert row.before is None and row.reason is None
            assert row.organization_id == pw.org_id
            assert coordinate_leaks(row.after) == []
        # Whoever asked is on the row: both admins, in the proportions they asked.
        assert sorted(str(r.actor_id) for r in rows) == sorted(
            str(admins[i % 2].sub) for i in range(calls)
        )

    async def test_a_capture_with_no_fix_still_audits_the_asking(self, client, session, pw) -> None:
        """What is audited is the act of looking, not what was found."""
        made = await put(client, pw, putaway_body(pw))
        resp = await reveal(client, pw, made.json()["move_id"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["lat_micro"], body["lng_micro"], body["accuracy_m"]) == (None, None, None)
        assert body["captured_at"]
        rows = await reads(session, pw)
        assert len(rows) == 1 and rows[0].after == {"fields": REVEALED_FIELDS}

    async def test_a_dropped_fix_reveals_as_no_fix_and_audits(self, client, session, pw) -> None:
        made = await put(client, pw, putaway_body(pw, lat_micro=LAT))  # unpaired: dropped
        resp = await reveal(client, pw, made.json()["move_id"])
        assert resp.json()["lat_micro"] is None
        assert len(await reads(session, pw)) == 1

    async def test_each_put_away_is_revealed_on_its_own_row(self, client, session, pw) -> None:
        first = await put(
            client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC), key="a"
        )
        second = await put(
            client,
            pw,
            putaway_body(
                pw, lat_micro=LAT + 5, lng_micro=LNG + 5, accuracy_m=1, to_barcode="RACK-1-B02"
            ),
            key="b",
        )
        await reveal(client, pw, first.json()["move_id"])
        await reveal(client, pw, second.json()["move_id"])
        await reveal(client, pw, second.json()["move_id"])
        by_move: dict[str, int] = {}
        for row in await reads(session, pw):
            by_move[str(row.entity_id)] = by_move.get(str(row.entity_id), 0) + 1
        assert by_move == {first.json()["move_id"]: 1, second.json()["move_id"]: 2}


class TestARefusedOrMissingRevealWritesNothing:
    async def test_no_permission_is_403_and_leaves_no_row(self, client, session, pw) -> None:
        made = await put(client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG))
        resp = await call(
            client,
            pw.org_id,
            [STOCK_READ, STOCK_TRANSFER],
            "POST",
            f"{PUTAWAY}/{made.json()['move_id']}/location:reveal",
        )
        assert resp.status_code == 403
        assert "lat_micro" not in resp.text and str(LAT) not in resp.text
        assert await reads(session, pw) == []

    async def test_a_missing_move_is_404_and_leaves_no_row(self, client, session, pw) -> None:
        resp = await reveal(client, pw, uuid.uuid4())
        assert resp.status_code == 404
        assert await reads(session, pw) == []

    async def test_a_move_that_was_not_a_put_away_is_404_and_leaves_no_row(
        self, client, session, pw
    ) -> None:
        transfer = await call(
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
        resp = await reveal(client, pw, transfer.json()["move_id"])
        assert resp.status_code == 404
        assert await reads(session, pw) == []

    async def test_a_purged_capture_is_404_and_leaves_no_row(
        self, client, session, admin_session, pw
    ) -> None:
        """After the 30-day purge (an admin-run `DELETE`) the move stands and its capture is gone:
        the detail and the reveal both say so, and a reveal that finds nothing audits nothing."""
        made = await put(client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG))
        move_id = made.json()["move_id"]
        async with admin_session.begin():
            await admin_session.execute(
                text("DELETE FROM operations.putaway_captures WHERE organization_id = :o"),
                {"o": pw.org_id},
            )
        assert (await reveal(client, pw, move_id)).status_code == 404
        detail = await call(client, pw.org_id, [STOCK_READ], "GET", f"{PUTAWAY}/{move_id}")
        assert detail.status_code == 404
        assert await reads(session, pw) == []

    async def test_a_malformed_move_id_is_422_and_leaves_no_row(self, client, session, pw) -> None:
        resp = await call(
            client, pw.org_id, [REVEAL_PERM], "POST", f"{PUTAWAY}/not-a-uuid/location:reveal"
        )
        assert resp.status_code == 422
        assert await reads(session, pw) == []

    async def test_a_reveal_that_rolls_back_leaves_no_row_claiming_it_happened(
        self, session, pw
    ) -> None:
        """The row and the response commit or roll back together: the service writes the row in the
        caller's transaction and never commits one of its own."""
        who = principal_for(pw.org_id, [REVEAL_PERM])
        ctx = ScopeContext(principal=who, scope=Scope.ORG, permission="x")
        async with session.begin():
            await stock_ops.putaway_stock(
                session,
                pw.w.t.ctx,
                _body(pw),
                idempotency_key="rb-1",
                occurred_on_explicit=False,
            )
        [capture] = await captures(session, pw.org_id)
        move_id = capture.move_id  # read now: a rollback expires every loaded row

        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            async with session.begin():
                await putaway_services.reveal_putaway_location(session, ctx, move_id)
                raise Boom
        assert await reads(session, pw) == []
        async with session.begin():
            await putaway_services.reveal_putaway_location(session, ctx, move_id)
        assert len(await reads(session, pw)) == 1


def _body(pw: PutawayWorld) -> PutawayIn:
    return PutawayIn(**putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC))


class TestThereIsOnlyOneDoor:
    @pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE"])
    async def test_the_reveal_url_answers_no_other_verb(self, client, session, pw, method) -> None:
        made = await put(client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG))
        resp = await call(
            client,
            pw.org_id,
            [REVEAL_PERM, STOCK_READ, STOCK_TRANSFER],
            method,
            f"{PUTAWAY}/{made.json()['move_id']}/location:reveal",
        )
        assert resp.status_code == 405, resp.text
        assert str(LAT) not in resp.text
        assert await reads(session, pw) == []

    @pytest.mark.parametrize("suffix", ["/location", "/coordinates", "/geo", "/fix", ":reveal"])
    async def test_there_is_no_sibling_route_that_returns_the_fix(self, client, pw, suffix) -> None:
        made = await put(client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG))
        for method in ("GET", "POST"):
            resp = await call(
                client,
                pw.org_id,
                [REVEAL_PERM, STOCK_READ],
                method,
                f"{PUTAWAY}/{made.json()['move_id']}{suffix}",
            )
            # 422: `…:reveal` glued onto the id is a malformed id, not a route.
            assert resp.status_code in (404, 405, 422), (method, suffix, resp.status_code)
            assert str(LAT) not in resp.text

    async def test_the_detail_says_a_fix_exists_but_never_what_it_is(self, client, pw) -> None:
        made = await put(client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC))
        resp = await call(
            client, pw.org_id, [STOCK_READ], "GET", f"{PUTAWAY}/{made.json()['move_id']}"
        )
        assert set(resp.json()) == {
            "move_id",
            "to_location",
            "worker_id",
            "captured_at",
            "matched_suggestion",
            "has_location",
        }
        assert resp.json()["has_location"] is True
        assert coordinate_leaks(resp.json()) == []


# ------------------------------------------------------------------ the put-away's own row


class TestThePutawayAuditRow:
    async def test_it_carries_the_ids_and_the_two_flags_and_no_position(
        self, client, session, pw
    ) -> None:
        who = principal_for(pw.org_id, [STOCK_TRANSFER])
        made = await put(
            client,
            pw,
            putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC, note="pallet 7"),
            principal=who,
        )
        [row] = await audit_rows(session, pw.org_id, action="putaway.recorded")
        assert row.actor_id == who.sub and row.entity == "inventory_move"
        assert str(row.entity_id) == made.json()["move_id"]
        assert row.reason == "pallet 7"
        assert row.before is None
        assert row.after == {
            "move_id": made.json()["move_id"],
            "inventory_item_id": str(pw.dye),
            "from_location_id": str(pw.receiving),
            "to_location_id": str(pw.rack_bin),
            "status": "AVAILABLE",
            "lot_id": None,
            "roll_id": None,
            "quantity": "5",
            "matched_suggestion": True,
            "has_location": True,
        }


# ------------------------------------------------------------------ the walls

#: A digit run that is the fix and not part of a longer number, a hex run or a uuid group.
_FIX_DIGITS = re.compile(rf"(?<![0-9a-f-])({LAT}|{LNG})(?![0-9a-f-])")


async def _scenario(client, session, pw: PutawayWorld) -> dict[str, str]:
    """Every shape of put-away that carries or drops a fix, a replay, a tracked one, a transfer —
    and one reveal, so the audit trail holds the reveal's row too."""
    full = await put(
        client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC), key="s-1"
    )
    await put(client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC), key="s-1")
    dropped = await put(
        client,
        pw,
        putaway_body(pw, lat_micro=LAT, to_barcode="RACK-1-B02", quantity="1"),
        key="s-2",
    )
    tracked = await put(
        client,
        pw,
        tracked_body(pw, lat_micro=LAT, lng_micro=LNG, to_barcode="SHELF-1-BC"),
        key="s-3",
    )
    await call(
        client,
        pw.org_id,
        [STOCK_TRANSFER],
        "POST",
        TRANSFERS,
        json={
            "inventory_item_id": str(pw.dye),
            "from_location_id": str(pw.staging),
            "to_location_id": str(pw.rack_bin2),
            "quantity": "2",
        },
    )
    assert (await reveal(client, pw, full.json()["move_id"])).status_code == 200
    return {
        "full": full.json()["move_id"],
        "dropped": dropped.json()["move_id"],
        "tracked": tracked.json()["move_id"],
    }


class TestNoCoordinateOutsideTheCapture:
    async def test_no_audit_row_names_or_carries_a_coordinate(self, client, session, pw) -> None:
        await _scenario(client, session, pw)
        rows = await audit_rows(session, pw.org_id)
        assert len(rows) > 5
        for row in rows:
            for part in (row.before, row.after, row.reason, row.action, row.entity):
                assert coordinate_leaks(part) == [], (row.action, row.entity, part)
            assert not _FIX_DIGITS.search(json.dumps(row.after, default=str))

    async def test_no_event_payload_names_or_carries_a_coordinate(
        self, client, session, pw
    ) -> None:
        await _scenario(client, session, pw)
        events = await outbox_rows(session, pw.org_id)
        assert any(e.event_type == "operations.inventory.putaway.recorded" for e in events)
        for event in events:
            for part in (event.payload, event.actor, event.event_type, event.aggregate_type):
                assert coordinate_leaks(part) == [], (event.event_type, part)

    async def test_no_table_but_the_captures_holds_the_digits_of_the_fix(
        self, client, session, admin_session, pw
    ) -> None:
        """The widest net: every row of every table of the schema, rendered as text. The fix is in
        `putaway_captures` — and nowhere else."""
        await _scenario(client, session, pw)
        async with admin_session.begin():
            tables = (
                (
                    await admin_session.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'operations'")
                    )
                )
                .scalars()
                .all()
            )
            hits: dict[str, int] = {}
            for table in tables:
                rows = (
                    (
                        await admin_session.execute(
                            text(f'SELECT t::text FROM operations."{table}" t')  # noqa: S608 - catalog names
                        )
                    )
                    .scalars()
                    .all()
                )
                found = sum(1 for r in rows if _FIX_DIGITS.search(r))
                if found:
                    hits[table] = found
        assert hits == {"putaway_captures": 2}  # the full fix and the tracked roll's: two rows

    async def test_no_non_reveal_response_names_or_carries_a_coordinate(
        self, client, session, pw
    ) -> None:
        moves = await _scenario(client, session, pw)
        w = pw.w
        broad = [f"{perm}:ORG" for perm in _every_permission_but_the_reveal()]
        gets = [
            f"{PUTAWAY}/{moves['full']}",
            f"{PUTAWAY}/{moves['dropped']}",
            f"{PUTAWAY}/{moves['tracked']}",
            f"{PUTAWAY}/suggest?inventory_item_id={pw.dye}&warehouse_id={pw.warehouse}",
            f"/operations/inventory/locate?inventory_item_id={pw.dye}",
            f"/operations/inventory/locate?inventory_item_id={pw.fabric}&roll_id={w.roll}",
            "/operations/inventory/ledger",
            "/operations/inventory/stock",
            f"/operations/inventory/{pw.dye}",
            f"/operations/inventory/{pw.dye}/movements",
            f"/operations/inventory/{pw.dye}/balances",
            f"/operations/inventory/{pw.fabric}/movements",
            f"/operations/inventory/rolls/{w.roll}",
            f"/operations/inventory/rolls/{w.roll}/events",
            f"/operations/inventory/rolls/{w.roll}/trace",
            f"/operations/inventory/lots/{w.lot}",
            f"/operations/inventory/lots/{w.lot}/trace",
            "/operations/inventory/rolls",
            "/operations/inventory/lots",
        ]
        checked = 0
        for path in gets:
            resp = await call(client, pw.org_id, broad, "GET", path)
            assert resp.status_code == 200, (path, resp.status_code, resp.text[:200])
            assert coordinate_leaks(resp.json()) == [], path
            checked += 1
        assert checked == len(gets)

    async def test_the_put_away_responses_fresh_replayed_and_dropped_carry_no_coordinate(
        self, client, session, pw
    ) -> None:
        body = putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC)
        for key in ("r-1", "r-1"):  # fresh, then the replay
            resp = await put(client, pw, body, key=key)
            assert coordinate_leaks(resp.json()) == []
        dropped = await put(client, pw, putaway_body(pw, lat_micro=LAT, quantity="1"), key="r-2")
        assert dropped.json()["warnings"] == ["geo_dropped:unpaired"]
        assert coordinate_leaks(dropped.json()) == []

    async def test_a_refused_put_away_echoes_no_coordinate(self, client, session, pw) -> None:
        overrides = (
            {"to_barcode": "NOPE-1"},  # 404
            {"from_location_id": str(pw.rack_bin)},  # 422 (source)
            {"quantity": "999"},  # 409
            {"quantity": "zero"},  # 422 (body)
        )
        for i, override in enumerate(overrides):
            resp = await put(
                client,
                pw,
                putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC, **override),
                key=f"x-{i}",
            )
            assert resp.status_code in (404, 409, 422)
            assert not _FIX_DIGITS.search(resp.text), (override, resp.text)

    async def test_the_capture_holds_the_fix_and_the_fix_only_there(
        self, client, session, pw
    ) -> None:
        moves = await _scenario(client, session, pw)
        stored = {
            str(c.move_id): (c.lat_micro, c.lng_micro, c.accuracy_m)
            for c in await captures(session, pw.org_id)
        }
        assert stored[moves["full"]] == (LAT, LNG, ACC)
        assert stored[moves["tracked"]] == (LAT, LNG, None)
        assert stored[moves["dropped"]] == (None, None, None)


def _every_permission_but_the_reveal() -> list[str]:
    admin = next(t for t in OPERATIONS_ROLE_TEMPLATES if t.code == "OPERATIONS_ADMIN")
    return [g.code for g in admin.grants if g.code != "operations.inventory.putaway.location_read"]


# ------------------------------------------------------------------ the logs


class TestLogsCarryIdsAndCountsNeverCoordinates:
    @pytest.fixture
    def broken_capture(self, monkeypatch):  # type: ignore[no-untyped-def]
        real = stock_ops.PutawayCapture
        monkeypatch.setattr(
            stock_ops, "PutawayCapture", lambda **kw: real(**{**kw, "accuracy_m": -1})
        )

    async def test_every_putaway_line_is_free_of_the_fix(self, client, session, pw) -> None:
        with capture_logs() as entries:
            await put(
                client,
                pw,
                putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC),
                key="l-1",
            )
            await put(
                client,
                pw,
                putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC),
                key="l-1",
            )
            await put(client, pw, putaway_body(pw, lat_micro=LAT, quantity="1"), key="l-2")
            made = await put(
                client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG, quantity="1"), key="l-3"
            )
            await reveal(client, pw, made.json()["move_id"])
            await call(
                client,
                pw.org_id,
                [STOCK_READ, "operations.warehouse.layout.read:ORG"],
                "GET",
                "/operations/inventory/locate",
                params={"inventory_item_id": str(pw.dye)},
            )
        names = {e["event"] for e in entries}
        assert {"putaway.replayed", "putaway.geo_dropped", "putaway.location_revealed"} <= names
        for entry in entries:
            rendered = json.dumps(entry, default=str)
            assert not _FIX_DIGITS.search(rendered), entry
            assert coordinate_leaks({k: v for k, v in entry.items() if k != "event"}) == [], entry

    async def test_the_lines_are_exactly_the_ids_the_runbook_says(
        self, client, session, pw
    ) -> None:
        who = principal_for(pw.org_id, [REVEAL_PERM])
        with capture_logs() as entries:
            await put(client, pw, putaway_body(pw, lat_micro=LAT), key="l-4")  # geo dropped
            made = await put(client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG), key="l-5")
            await put(
                client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG), key="l-5"
            )  # replay
            await reveal(client, pw, made.json()["move_id"], principal=who)
        by_event = {e["event"]: e for e in entries if e["event"].startswith("putaway.")}
        dropped = by_event["putaway.geo_dropped"]
        assert dropped["reason"] == "unpaired" and set(dropped) == {
            "event",
            "log_level",
            "move_id",
            "reason",
        }
        assert set(by_event["putaway.replayed"]) == {"event", "log_level", "move_id"}
        revealed = by_event["putaway.location_revealed"]
        assert revealed["actor_id"] == str(who.sub)
        assert revealed["move_id"] == made.json()["move_id"]
        assert set(revealed) == {"event", "log_level", "actor_id", "move_id"}

    async def test_a_failed_capture_logs_the_class_of_the_error_and_nothing_it_said(
        self, client, session, pw, broken_capture
    ) -> None:
        """A database error's text can quote the failing row — coordinates included. The line names
        the exception class and the move, no more."""
        with capture_logs() as entries:
            resp = await put(
                client,
                pw,
                putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=ACC),
                key="l-6",
            )
        assert resp.json()["warnings"] == ["geo_not_recorded"]
        [line] = [e for e in entries if e["event"] == "putaway.capture_failed"]
        assert set(line) == {"event", "log_level", "move_id", "error"}
        assert line["error"] == "IntegrityError" and line["log_level"] == "error"
        for entry in entries:
            assert not _FIX_DIGITS.search(json.dumps(entry, default=str)), entry

    async def test_no_line_carries_the_maps_key_or_a_maps_url(self, client, session, pw) -> None:
        with capture_logs() as entries:
            await put(client, pw, putaway_body(pw, lat_micro=LAT, lng_micro=LNG), key="l-7")
        rendered = json.dumps(entries, default=str).lower()
        assert "maps.googleapis" not in rendered and "aiza" not in rendered
