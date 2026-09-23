"""`POST /operations/inventory/reservations/{id}:release` (ADR-0014 §11) — the first HTTP surface
on the reservation kernel Phase 0 built. There is no create endpoint yet (reservations are still
made through `ledger.reserve` by other write paths), so every test here seeds one directly and
exercises release through the real gateway route.
"""

from __future__ import annotations

import uuid

import pytest

from app.inventory import ledger
from app.inventory.ledger import MoveLine
from tests.helpers import as_principal, principal_for
from tests.inventory_fixtures import TODAY, build_textile, q

pytestmark = pytest.mark.integration

READ = "operations.inventory.item.read:ORG"
RESERVE = "operations.inventory.stock.reserve:ORG"


async def _reserved(session, org_id, *, quantity: str = "40") -> tuple[uuid.UUID, uuid.UUID]:
    """A textile item with `quantity` reserved. Returns (item_id, reservation_id)."""
    t = await build_textile(session, org_id)
    async with session.begin():
        await ledger.post_move(
            session,
            t.ctx,
            kind="receipt",
            lines=[MoveLine(t.fabric, t.weaving, q("100"))],
            occurred_on=TODAY,
        )
        made = await ledger.reserve(
            session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q(quantity)
        )
    return t.fabric, made.reservation.id


async def _reserved_discrete(session, org_id, *, quantity: str = "100") -> uuid.UUID:
    """`t.buttons` (pcs — a discrete unit) with `quantity` reserved. Returns the reservation id."""
    t = await build_textile(session, org_id)
    async with session.begin():
        await ledger.post_move(
            session,
            t.ctx,
            kind="receipt",
            lines=[MoveLine(t.buttons, t.weaving, q("100"))],
            occurred_on=TODAY,
        )
        made = await ledger.reserve(
            session, t.ctx, item_id=t.buttons, location_id=t.weaving, quantity_milli=q(quantity)
        )
    return made.reservation.id


class TestRelease:
    async def test_a_full_release_empties_the_promise(self, client, session, org_id) -> None:
        _, rid = await _reserved(session, org_id)
        with as_principal(principal_for(org_id, [RESERVE])):
            resp = await client.post(f"/api/v1/operations/inventory/reservations/{rid}:release")
        assert resp.status_code == 200
        body = resp.json()
        assert (body["status"], body["remaining"], body["released"]) == ("released", "0", "40")

    async def test_a_partial_release_leaves_the_reservation_active(
        self, client, session, org_id
    ) -> None:
        _, rid = await _reserved(session, org_id)
        with as_principal(principal_for(org_id, [RESERVE])):
            resp = await client.post(
                f"/api/v1/operations/inventory/reservations/{rid}:release",
                json={"quantity": "12.5"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert (body["status"], body["released"], body["remaining"]) == (
            "active",
            "12.5",
            "27.5",
        )

    async def test_releasing_more_than_is_open_is_a_conflict(self, client, session, org_id) -> None:
        _, rid = await _reserved(session, org_id, quantity="10")
        with as_principal(principal_for(org_id, [RESERVE])):
            resp = await client.post(
                f"/api/v1/operations/inventory/reservations/{rid}:release",
                json={"quantity": "11"},
            )
        assert resp.status_code == 409

    async def test_a_negative_quantity_is_a_validation_error(self, client, session, org_id) -> None:
        _, rid = await _reserved(session, org_id)
        with as_principal(principal_for(org_id, [RESERVE])):
            resp = await client.post(
                f"/api/v1/operations/inventory/reservations/{rid}:release",
                json={"quantity": "-1"},
            )
        assert resp.status_code == 422

    async def test_a_fractional_release_of_a_discrete_item_is_a_validation_error(
        self, client, session, org_id
    ) -> None:
        """MEDIUM #1: `parse_qty` in `routes.py` never gets the item's `uom`, so a fractional
        piece only gets caught because `ledger.release` checks discreteness itself."""
        rid = await _reserved_discrete(session, org_id)
        with as_principal(principal_for(org_id, [RESERVE])):
            resp = await client.post(
                f"/api/v1/operations/inventory/reservations/{rid}:release",
                json={"quantity": "0.5"},
            )
        assert resp.status_code == 422

    async def test_a_retried_release_with_the_same_key_replays(
        self, client, session, org_id
    ) -> None:
        _, rid = await _reserved(session, org_id, quantity="20")
        headers = {"Idempotency-Key": "release-2026-09-23-001"}
        payload = {"quantity": "5"}
        with as_principal(principal_for(org_id, [RESERVE])):
            first = await client.post(
                f"/api/v1/operations/inventory/reservations/{rid}:release",
                json=payload,
                headers=headers,
            )
            retry = await client.post(
                f"/api/v1/operations/inventory/reservations/{rid}:release",
                json=payload,
                headers=headers,
            )
        assert first.status_code == retry.status_code == 200
        assert first.json()["remaining"] == retry.json()["remaining"] == "15"

    async def test_the_same_key_with_a_different_quantity_is_a_conflict(
        self, client, session, org_id
    ) -> None:
        _, rid = await _reserved(session, org_id, quantity="20")
        headers = {"Idempotency-Key": "release-dup"}
        with as_principal(principal_for(org_id, [RESERVE])):
            await client.post(
                f"/api/v1/operations/inventory/reservations/{rid}:release",
                json={"quantity": "5"},
                headers=headers,
            )
            clash = await client.post(
                f"/api/v1/operations/inventory/reservations/{rid}:release",
                json={"quantity": "6"},
                headers=headers,
            )
        assert clash.status_code == 409


class TestNegativeAuthz:
    async def test_release_needs_the_reserve_permission(self, client, session, org_id) -> None:
        _, rid = await _reserved(session, org_id)
        with as_principal(principal_for(org_id, [READ])):
            resp = await client.post(f"/api/v1/operations/inventory/reservations/{rid}:release")
        assert resp.status_code == 403

    async def test_an_own_scoped_grant_is_not_enough(self, client, session, org_id) -> None:
        """The permission's ceiling floors every grant at ORG (no OWN reading for a reservation);
        a caller some other route gave OWN scope on this code must still be refused."""
        _, rid = await _reserved(session, org_id)
        with as_principal(principal_for(org_id, ["operations.inventory.stock.reserve:OWN"])):
            resp = await client.post(f"/api/v1/operations/inventory/reservations/{rid}:release")
        assert resp.status_code == 403


class TestCrossTenant:
    async def test_another_organizations_reservation_is_not_found(
        self, client, admin_session, org_id
    ) -> None:
        _, rid = await _reserved(admin_session, uuid.uuid4())
        with as_principal(principal_for(org_id, [RESERVE])):
            resp = await client.post(f"/api/v1/operations/inventory/reservations/{rid}:release")
        assert resp.status_code == 404

    async def test_an_unknown_reservation_is_not_found(self, client, org_id) -> None:
        with as_principal(principal_for(org_id, [RESERVE])):
            resp = await client.post(
                f"/api/v1/operations/inventory/reservations/{uuid.uuid4()}:release"
            )
        assert resp.status_code == 404
