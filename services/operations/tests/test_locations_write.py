"""Locations over HTTP: the warehouse tree, barcodes and capacity (ADR-0024 §5, Fulfilment F1).

`data`'s seeded low-stock item posts an opening move to Pune Plant's default bin, so that bin is
a ready-made "location with ledger history" for the `layout.publish` gate — no extra stock setup
needed to prove it.
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration

READ_PERM = "operations.inventory.location.read:ORG"
WRITE_PERM = "operations.inventory.location.write:ORG"
PUBLISH_PERM = "operations.inventory.layout.publish:ORG"


async def _warehouse_id(client, org_id, data, name: str) -> str:
    principal = principal_for(
        org_id, ["operations.inventory.warehouse.read:ORG"], subject_ref=data["subject_a"]
    )
    with as_principal(principal):
        listed = await client.get("/api/v1/operations/warehouses")
    return str(next(w for w in listed.json()["items"] if w["name"] == name)["id"])


async def _default_location(client, org_id, data, warehouse_id: str) -> dict:
    principal = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
    with as_principal(principal):
        listed = await client.get(f"/api/v1/operations/warehouses/{warehouse_id}/locations")
    return next(loc for loc in listed.json()["items"] if loc["is_default"])


class TestListLocations:
    async def test_happy_path_includes_the_default_bin(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        principal = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/operations/warehouses/{warehouse_id}/locations")
        assert response.status_code == 200
        codes = {loc["code"] for loc in response.json()["items"]}
        assert "DEFAULT" in codes

    async def test_a_caller_without_read_is_forbidden(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        principal = principal_for(org_id, [], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.get(f"/api/v1/operations/warehouses/{warehouse_id}/locations")
        assert response.status_code == 403

    async def test_cross_tenant_warehouse_is_not_found(self, client, org_id, data) -> None:
        other = principal_for(uuid.uuid4(), [READ_PERM], subject_ref=uuid.uuid4())
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        with as_principal(other):
            response = await client.get(f"/api/v1/operations/warehouses/{warehouse_id}/locations")
        assert response.status_code == 404


class TestCreateLocation:
    async def test_happy_path(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={"kind": "rack", "code": "RACK-1", "name": "Rack 1"},
            )
        assert response.status_code == 201
        body = response.json()
        assert body["kind"] == "rack"
        assert body["warehouse_id"] == warehouse_id
        assert body["is_default"] is False

    async def test_with_barcode_and_capacity(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={
                    "kind": "bin",
                    "code": "BIN-CAP",
                    "name": "Capacity bin",
                    "barcode": "LOC-000123",
                    "pick_sequence": 5,
                    "max_weight_grams": 20000,
                    "max_volume_milli": 500000,
                    "storage_type": "ambient",
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["barcode"] == "LOC-000123"
        assert body["pick_sequence"] == 5
        assert body["max_weight_grams"] == 20000
        assert body["max_volume_milli"] == 500000
        assert body["storage_type"] == "ambient"

    async def test_duplicate_code_in_same_warehouse_is_conflict(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            first = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={"kind": "bin", "code": "DUP-1", "name": "First"},
            )
        assert first.status_code == 201
        with as_principal(principal):
            second = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={"kind": "bin", "code": "DUP-1", "name": "Second"},
            )
        assert second.status_code == 409
        assert second.json()["errors"][0]["code"] == "duplicate"

    async def test_duplicate_barcode_across_locations_is_conflict(
        self, client, org_id, data
    ) -> None:
        pune = await _warehouse_id(client, org_id, data, "Pune Plant")
        blr = await _warehouse_id(client, org_id, data, "Bengaluru Store")
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            first = await client.post(
                f"/api/v1/operations/warehouses/{pune}/locations",
                json={"kind": "bin", "code": "BC-1", "name": "First", "barcode": "SHARED-BC"},
            )
        assert first.status_code == 201
        with as_principal(principal):
            second = await client.post(
                f"/api/v1/operations/warehouses/{blr}/locations",
                json={"kind": "bin", "code": "BC-2", "name": "Second", "barcode": "SHARED-BC"},
            )
        assert second.status_code == 409
        assert second.json()["errors"][0]["code"] == "duplicate"

    async def test_same_barcode_in_two_orgs_both_succeed(self, client, org_id, data) -> None:
        # Proves the uniqueness index is scoped by organization_id, not global: the exact barcode
        # string succeeds for a second, unrelated organization with its own fresh warehouse.
        pune = await _warehouse_id(client, org_id, data, "Pune Plant")
        home_principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(home_principal):
            home = await client.post(
                f"/api/v1/operations/warehouses/{pune}/locations",
                json={"kind": "bin", "code": "BC-HOME", "name": "Home", "barcode": "CROSS-ORG-BC"},
            )
        assert home.status_code == 201

        other_org = uuid.uuid4()
        other_subject = uuid.uuid4()
        other_write = principal_for(
            other_org,
            ["operations.inventory.warehouse.write:ORG", WRITE_PERM],
            subject_ref=other_subject,
        )
        with as_principal(other_write):
            other_warehouse = await client.post(
                "/api/v1/operations/warehouses",
                json={"code": "WH-OTHER", "name": "Other Org Depot"},
            )
        assert other_warehouse.status_code == 201
        other_warehouse_id = other_warehouse.json()["id"]

        with as_principal(other_write):
            away = await client.post(
                f"/api/v1/operations/warehouses/{other_warehouse_id}/locations",
                json={"kind": "bin", "code": "BC-AWAY", "name": "Away", "barcode": "CROSS-ORG-BC"},
            )
        assert away.status_code == 201

    async def test_unknown_kind_is_422(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={"kind": "warehouse_floor", "code": "X-1", "name": "Bad kind"},
            )
        assert response.status_code == 422

    async def test_unknown_parent_is_422(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={
                    "kind": "bin",
                    "code": "ORPHAN",
                    "name": "Orphan",
                    "parent_id": str(uuid.uuid4()),
                },
            )
        assert response.status_code == 422

    async def test_parent_from_another_warehouse_is_422(self, client, org_id, data) -> None:
        # A parent_id must resolve within the *same* warehouse the new location is created into.
        # A well-formed id belonging to a location in a different warehouse (here, a different
        # org entirely, which is the strictest case) must be refused the same way an unknown id
        # is, not silently accepted or cross-linked.
        pune = await _warehouse_id(client, org_id, data, "Pune Plant")
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])

        other_org = uuid.uuid4()
        other_write = principal_for(
            other_org,
            ["operations.inventory.warehouse.write:ORG", WRITE_PERM],
            subject_ref=uuid.uuid4(),
        )
        with as_principal(other_write):
            other_warehouse = await client.post(
                "/api/v1/operations/warehouses", json={"code": "WH-FOREIGN", "name": "Foreign"}
            )
        assert other_warehouse.status_code == 201
        with as_principal(other_write):
            foreign_location = await client.post(
                f"/api/v1/operations/warehouses/{other_warehouse.json()['id']}/locations",
                json={"kind": "zone", "code": "FOREIGN-Z", "name": "Foreign zone"},
            )
        assert foreign_location.status_code == 201

        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/warehouses/{pune}/locations",
                json={
                    "kind": "bin",
                    "code": "CROSS-PARENT",
                    "name": "Cross-org parent",
                    "parent_id": foreign_location.json()["id"],
                },
            )
        assert response.status_code == 422

    async def test_a_reader_is_forbidden(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        principal = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={"kind": "bin", "code": "NOPE", "name": "Nope"},
            )
        assert response.status_code == 403

    async def test_cross_tenant_warehouse_is_not_found(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        other = principal_for(uuid.uuid4(), [WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={"kind": "bin", "code": "NOPE", "name": "Nope"},
            )
        assert response.status_code == 404


class TestUpdateLocation:
    async def test_plain_field_edit_under_write_alone(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Bengaluru Store")
        principal = principal_for(org_id, [WRITE_PERM, READ_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            created = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={"kind": "shelf", "code": "SH-1", "name": "Shelf one"},
            )
        location_id = created.json()["id"]

        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/locations/{location_id}",
                json={"name": "Shelf renamed", "pick_sequence": 3, "barcode": "SH-1-BC"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Shelf renamed"
        assert body["pick_sequence"] == 3
        assert body["barcode"] == "SH-1-BC"

    async def test_kind_change_with_no_history_is_allowed_under_write_alone(
        self, client, org_id, data
    ) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Bengaluru Store")
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            created = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={"kind": "zone", "code": "Z-EMPTY", "name": "Empty zone"},
            )
        location_id = created.json()["id"]

        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/locations/{location_id}", json={"kind": "aisle"}
            )
        assert response.status_code == 200
        assert response.json()["kind"] == "aisle"

    async def test_kind_change_on_a_location_with_history_needs_layout_publish(
        self, client, org_id, data
    ) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        default_bin = await _default_location(client, org_id, data, warehouse_id)

        write_only = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(write_only):
            refused = await client.patch(
                f"/api/v1/operations/locations/{default_bin['id']}", json={"kind": "staging"}
            )
        assert refused.status_code == 403
        assert "layout.publish" in refused.json()["detail"]

        with_publish = principal_for(
            org_id, [WRITE_PERM, PUBLISH_PERM], subject_ref=data["subject_a"]
        )
        with as_principal(with_publish):
            allowed = await client.patch(
                f"/api/v1/operations/locations/{default_bin['id']}", json={"kind": "staging"}
            )
        assert allowed.status_code == 200
        assert allowed.json()["kind"] == "staging"

    async def test_capacity_change_on_a_location_with_history_needs_layout_publish(
        self, client, org_id, data
    ) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        default_bin = await _default_location(client, org_id, data, warehouse_id)

        write_only = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(write_only):
            refused = await client.patch(
                f"/api/v1/operations/locations/{default_bin['id']}",
                json={"max_weight_grams": 50000},
            )
        assert refused.status_code == 403
        assert "layout.publish" in refused.json()["detail"]

    async def test_explicit_null_capacity_on_a_location_with_history_needs_layout_publish(
        self, client, org_id, data
    ) -> None:
        # Clearing a capacity value is as much a layout change as setting one: an explicit `null`
        # must be caught by the same guard as a non-null change, not waved through because the
        # naive comparison "was a value sent" misses "was set to null".
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        default_bin = await _default_location(client, org_id, data, warehouse_id)

        with_publish = principal_for(
            org_id, [WRITE_PERM, PUBLISH_PERM], subject_ref=data["subject_a"]
        )
        with as_principal(with_publish):
            seeded = await client.patch(
                f"/api/v1/operations/locations/{default_bin['id']}",
                json={"max_weight_grams": 50000},
            )
        assert seeded.status_code == 200

        write_only = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(write_only):
            refused = await client.patch(
                f"/api/v1/operations/locations/{default_bin['id']}",
                json={"max_weight_grams": None},
            )
        assert refused.status_code == 403
        assert "layout.publish" in refused.json()["detail"]

    async def test_resending_unchanged_kind_with_history_is_allowed_under_write_alone(
        self, client, org_id, data
    ) -> None:
        # The guard is keyed on an actual value change, not on the field's mere presence in the
        # payload — a write-only caller who echoes back the location's current `kind` (as a naive
        # "load, edit one field, save the whole form" client would) must not be refused for a
        # field it never actually touched.
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        default_bin = await _default_location(client, org_id, data, warehouse_id)

        write_only = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(write_only):
            response = await client.patch(
                f"/api/v1/operations/locations/{default_bin['id']}",
                json={"kind": default_bin["kind"], "name": default_bin["name"]},
            )
        assert response.status_code == 200
        assert response.json()["kind"] == default_bin["kind"]

    async def test_duplicate_code_on_update_is_conflict(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Bengaluru Store")
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={"kind": "bin", "code": "TAKEN", "name": "Taken"},
            )
        with as_principal(principal):
            created = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={"kind": "bin", "code": "FREE", "name": "Free"},
            )
        location_id = created.json()["id"]

        with as_principal(principal):
            response = await client.patch(
                f"/api/v1/operations/locations/{location_id}", json={"code": "TAKEN"}
            )
        assert response.status_code == 409

    async def test_a_reader_is_forbidden(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Bengaluru Store")
        write_principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(write_principal):
            created = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={"kind": "bin", "code": "RO-1", "name": "Read only target"},
            )
        location_id = created.json()["id"]

        reader = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
        with as_principal(reader):
            response = await client.patch(
                f"/api/v1/operations/locations/{location_id}", json={"name": "Renamed"}
            )
        assert response.status_code == 403

    async def test_cross_tenant_is_not_found(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Bengaluru Store")
        principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(principal):
            created = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={"kind": "bin", "code": "XT-1", "name": "Cross tenant target"},
            )
        location_id = created.json()["id"]

        other = principal_for(uuid.uuid4(), [WRITE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.patch(
                f"/api/v1/operations/locations/{location_id}", json={"name": "Stolen"}
            )
        assert response.status_code == 404


class TestBarcodeLookup:
    async def test_round_trips_to_the_same_location(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        write_principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(write_principal):
            created = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={
                    "kind": "bin",
                    "code": "SCAN-1",
                    "name": "Scan target",
                    "barcode": "LOC-SCAN-000042",
                },
            )
        assert created.status_code == 201
        location_id = created.json()["id"]

        reader = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
        with as_principal(reader):
            resolved = await client.get("/api/v1/operations/locations/by-barcode/LOC-SCAN-000042")
        assert resolved.status_code == 200
        assert resolved.json()["id"] == location_id
        assert resolved.json()["barcode"] == "LOC-SCAN-000042"

    async def test_unknown_barcode_is_not_found(self, client, org_id, data) -> None:
        reader = principal_for(org_id, [READ_PERM], subject_ref=data["subject_a"])
        with as_principal(reader):
            response = await client.get("/api/v1/operations/locations/by-barcode/NOPE-000")
        assert response.status_code == 404

    async def test_a_caller_without_read_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.get("/api/v1/operations/locations/by-barcode/LOC-SCAN-000042")
        assert response.status_code == 403

    async def test_another_tenants_barcode_is_not_found(self, client, org_id, data) -> None:
        warehouse_id = await _warehouse_id(client, org_id, data, "Pune Plant")
        write_principal = principal_for(org_id, [WRITE_PERM], subject_ref=data["subject_a"])
        with as_principal(write_principal):
            seeded = await client.post(
                f"/api/v1/operations/warehouses/{warehouse_id}/locations",
                json={
                    "kind": "bin",
                    "code": "SCAN-2",
                    "name": "Scan target 2",
                    "barcode": "LOC-SCAN-999",
                },
            )
        # If the seed itself failed, a 404 below would prove nothing about tenant isolation.
        assert seeded.status_code == 201

        other = principal_for(uuid.uuid4(), [READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.get("/api/v1/operations/locations/by-barcode/LOC-SCAN-999")
        assert response.status_code == 404
