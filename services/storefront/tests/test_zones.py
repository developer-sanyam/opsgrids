"""The merchant's surface: the store, zones, postal codes, the bulk import, the tester and the
demand report.

Two of these routes exist for one reason, and it is worth stating where the tests for them live:
**nothing in this design detects a wrong zone map.** A postal code mapped to the wrong zone
returns a confident answer. The tester and the demand report are the only two instruments, which
is why both ship in slice 1 rather than being deferred as internal tooling (ADR-0010 §13).
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.models import AuditLog, ZonePostalCode
from tests.helpers import all_perms, csv_body, seed_store, seed_zone

pytestmark = pytest.mark.integration

ZONES = "/api/v1/storefront/delivery/zones"
IMPORT = "/api/v1/storefront/delivery/postal-codes/import"
CHECK = "/api/v1/storefront/delivery/check"
DEMAND = "/api/v1/storefront/delivery/demand"

CSV_HEADERS = {"content-type": "text/csv"}


class TestZones:
    async def test_create_list_patch_and_delete(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            created = await client.post(
                ZONES,
                json={
                    "code": "BANDRA",
                    "name": "Bandra",
                    "mode": "PROMISED",
                    "promise_kind": "DURATION",
                    "promise_minutes": 30,
                    "delivery_fee_minor": 2900,
                },
            )
            zone_id = created.json()["id"]
            listed = await client.get(ZONES)
            patched = await client.patch(f"{ZONES}/{zone_id}", json={"promise_minutes": 45})
            removed = await client.delete(f"{ZONES}/{zone_id}")

        assert created.status_code == 201
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["postal_code_count"] == 0
        assert patched.json()["promise_minutes"] == 45
        assert removed.status_code == 204

    async def test_slotted_is_refused_with_a_message_naming_the_next_slice(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        """**The writer refuses it, not the type.** A merchant who picks the mode the UI shows
        them deserves a sentence, not "Input should be 'PROMISED' or 'UNSERVED'". The value stays
        in the column's check constraint so slice 2 is a validation change, not a migration."""
        with as_principal(all_perms(org_id)):
            response = await client.post(
                ZONES, json={"code": "Q", "name": "Quick", "mode": "SLOTTED"}
            )

        assert response.status_code == 422
        body = response.json()
        assert "not available yet" in body["detail"]
        assert body["errors"][0]["code"] == "mode_not_available"

    async def test_a_promised_zone_with_no_promise_is_refused(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            response = await client.post(ZONES, json={"code": "P", "name": "P", "mode": "PROMISED"})
        assert response.status_code == 422

    async def test_a_duplicate_zone_code_is_a_conflict(
        self, client, as_principal, admin_session, org_id: uuid.UUID
    ) -> None:
        await seed_zone(admin_session, org_id, code="BANDRA")
        with as_principal(all_perms(org_id)):
            response = await client.post(
                ZONES,
                json={
                    "code": "BANDRA",
                    "name": "again",
                    "mode": "PROMISED",
                    "promise_kind": "DURATION",
                    "promise_minutes": 30,
                },
            )
        assert response.status_code == 409

    async def test_deleting_a_zone_with_postal_codes_is_refused_naming_the_count(
        self, client, as_principal, admin_session, org_id: uuid.UUID
    ) -> None:
        """Cascading would silently un-serve every one of those areas, and the merchant would
        find out from a buyer."""
        zone = await seed_zone(admin_session, org_id, postal_codes=("400050", "400051"))

        with as_principal(all_perms(org_id)):
            response = await client.delete(f"{ZONES}/{zone.id}")

        assert response.status_code == 422
        assert "2 postal code(s)" in response.json()["detail"]


class TestThePostalCodeTextarea:
    async def test_the_list_replaces_rather_than_appends(
        self, client, as_principal, admin_session, org_id: uuid.UUID
    ) -> None:
        """Without replace, a merchant who **shrinks** a zone watches the removal do nothing and
        the old area keep delivering."""
        zone = await seed_zone(admin_session, org_id, postal_codes=("400050", "400051"))

        with as_principal(all_perms(org_id)):
            response = await client.put(
                f"{ZONES}/{zone.id}/postal-codes", json={"postal_codes": "400 050\n400052"}
            )

        assert response.status_code == 200
        assert response.json()["postal_codes"] == ["400050", "400052"]

    async def test_a_code_belonging_to_another_zone_is_refused_naming_that_zone(
        self, client, as_principal, admin_session, org_id: uuid.UUID
    ) -> None:
        """Silently reassigning an area because it was typed into a second box is the kind of
        change a merchant discovers from a customer. One postal code, one zone."""
        await seed_zone(admin_session, org_id, code="BANDRA", postal_codes=("400050",))
        other = await seed_zone(admin_session, org_id, code="ANDHERI")

        with as_principal(all_perms(org_id)):
            response = await client.put(
                f"{ZONES}/{other.id}/postal-codes", json={"postal_codes": "400050"}
            )

        assert response.status_code == 409
        assert "BANDRA" in response.json()["detail"]

    async def test_an_invalid_code_refuses_the_whole_list(
        self, client, as_principal, admin_session, session, org_id: uuid.UUID
    ) -> None:
        zone = await seed_zone(admin_session, org_id, postal_codes=("400050",))

        with as_principal(all_perms(org_id)):
            response = await client.put(
                f"{ZONES}/{zone.id}/postal-codes", json={"postal_codes": "400052\nnope"}
            )

        assert response.status_code == 422
        kept = (
            (
                await session.execute(
                    select(ZonePostalCode.postal_code).where(
                        ZonePostalCode.organization_id == org_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert list(kept) == ["400050"]


class TestTheBulkImport:
    async def test_merge_inserts_and_moves_and_counts_both(
        self, client, as_principal, admin_session, org_id: uuid.UUID
    ) -> None:
        await seed_zone(admin_session, org_id, code="BANDRA", postal_codes=("400050",))
        await seed_zone(admin_session, org_id, code="ANDHERI")

        with as_principal(all_perms(org_id)):
            response = await client.post(
                IMPORT,
                content=csv_body(("400050", "ANDHERI"), ("400051", "ANDHERI")),
                headers=CSV_HEADERS,
            )

        assert response.status_code == 200
        assert response.json() == {
            "mode": "merge",
            "rows_read": 2,
            "inserted": 1,
            "updated": 1,
            "deleted": 0,
            "rejected": 0,
        }

    async def test_replace_removes_what_the_file_does_not_mention(
        self, client, as_principal, admin_session, org_id: uuid.UUID
    ) -> None:
        """What a merchant re-uploading their master file means. Without it their **removals
        never take effect** — a zone they shrank keeps delivering."""
        await seed_zone(admin_session, org_id, code="BANDRA", postal_codes=("400050", "400051"))

        with as_principal(all_perms(org_id)):
            response = await client.post(
                f"{IMPORT}?mode=replace",
                content=csv_body(("400052", "BANDRA")),
                headers=CSV_HEADERS,
            )
            listed = await client.get(ZONES)

        assert response.json()["deleted"] == 2
        assert response.json()["inserted"] == 1
        assert listed.json()["items"][0]["postal_code_count"] == 1

    async def test_a_duplicate_in_the_file_is_refused_and_nothing_is_written(
        self, client, as_principal, admin_session, session, org_id: uuid.UUID
    ) -> None:
        """**Last-wins is the silent wrong answer**, and a half-applied import is worse than a
        failed one because the merchant cannot tell which half (ADR-0010 §9)."""
        await seed_zone(admin_session, org_id, code="BANDRA")
        await seed_zone(admin_session, org_id, code="ANDHERI")

        with as_principal(all_perms(org_id)):
            response = await client.post(
                IMPORT,
                content=csv_body(("400050", "BANDRA"), ("400051", "BANDRA"), ("400050", "ANDHERI")),
                headers=CSV_HEADERS,
            )

        assert response.status_code == 422
        body = response.json()
        assert body["errors"][0]["code"] == "import_duplicate_postal_code"
        assert body["errors"][0]["lines"] == [2, 4]

        written = (
            (
                await session.execute(
                    select(ZonePostalCode).where(ZonePostalCode.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
        assert written == []

    async def test_an_unknown_zone_code_refuses_the_whole_file(
        self, client, as_principal, admin_session, org_id: uuid.UUID
    ) -> None:
        await seed_zone(admin_session, org_id, code="BANDRA")

        with as_principal(all_perms(org_id)):
            response = await client.post(
                IMPORT,
                content=csv_body(("400050", "BANDRA"), ("400051", "NOWHERE")),
                headers=CSV_HEADERS,
            )

        assert response.status_code == 422
        assert "NOWHERE" in response.json()["detail"]

    async def test_one_audit_row_carries_the_mode_and_the_counters(
        self, client, as_principal, admin_session, session, org_id: uuid.UUID
    ) -> None:
        """One row per import, not 19,000. The mode is on the row because a `replace` is a
        destructive act and a `merge` is not, and the trail has to distinguish them."""
        await seed_zone(admin_session, org_id, code="BANDRA")

        with as_principal(all_perms(org_id)):
            await client.post(
                f"{IMPORT}?mode=replace",
                content=csv_body(("400050", "BANDRA"), ("400051", "BANDRA")),
                headers=CSV_HEADERS,
            )

        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "delivery.postal_codes.imported",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].after == {
            "mode": "replace",
            "rows_read": 2,
            "inserted": 2,
            "updated": 0,
            "deleted": 0,
        }


class TestTheTester:
    async def test_it_returns_the_same_answer_plus_the_zone_that_matched(
        self, client, as_principal, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """The second concrete caller of the chokepoint selector, and the only way a merchant
        verifies that a 19,000-row import did what they meant."""
        await seed_store(admin_session, org_id, slug)
        await seed_zone(admin_session, org_id, code="BANDRA", postal_codes=("400050",))

        with as_principal(all_perms(org_id)):
            response = await client.post(CHECK, json={"postal_code": "400 050"})

        body = response.json()
        assert body["serviceable"] is True
        assert body["zone_code"] == "BANDRA"
        assert body["postal_code"] == "400050"

    async def test_an_unmapped_code_reads_the_same_as_it_does_for_a_buyer(
        self, client, as_principal, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug)

        with as_principal(all_perms(org_id)):
            body = (await client.post(CHECK, json={"postal_code": "422001"})).json()

        assert body["serviceable"] is False
        assert body["reason"] == "no_mapping"
        assert body["zone_id"] is None

    async def test_it_writes_no_demand_row(
        self, client, as_principal, admin_session, session, org_id: uuid.UUID, slug: str
    ) -> None:
        """The merchant testing their own map is not a buyer asking. Counting it would poison the
        one signal the report exists to give."""
        await seed_store(admin_session, org_id, slug)

        with as_principal(all_perms(org_id)):
            await client.post(CHECK, json={"postal_code": "422001"})
            demand = await client.get(DEMAND)

        assert demand.json()["items"] == []

    async def test_it_needs_a_store_because_the_answer_has_a_timezone_in_it(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            response = await client.post(CHECK, json={"postal_code": "400050"})
        assert response.status_code == 404


class TestTheDemandReport:
    async def test_it_ranks_the_postal_codes_that_were_turned_away(
        self, client, as_principal, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """ "Where should I expand next", not "what happened most recently"."""
        await seed_store(admin_session, org_id, slug)
        public = "/api/v1/storefront/public/serviceability"
        for postal_code, times in (("422001", 3), ("110001", 1)):
            for _ in range(times):
                await client.post(public, json={"store": slug, "postal_code": postal_code})

        with as_principal(all_perms(org_id)):
            response = await client.get(f"{DEMAND}?outcome=NO_MAPPING")

        items = response.json()["items"]
        assert [(row["postal_code"], row["checks"]) for row in items] == [
            ("422001", 3),
            ("110001", 1),
        ]

    async def test_a_window_wider_than_the_retention_period_is_refused(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        today = dt.date.today()
        with as_principal(all_perms(org_id)):
            response = await client.get(
                f"{DEMAND}?since={today - dt.timedelta(days=500)}&until={today}"
            )
        assert response.status_code == 422
