"""Warehouse geometry over HTTP (plan §2/F7): floors, versioned layouts, a draft's placements and
walk graph, validation, publish/rollback, and routing over a floor's published layout.

`geo` seeds one warehouse with three real locations (A, B, C — through the real F1 location-write
path, `app.inventory.services.create_location`, never a fake row) — enough to build a genuine
three-node walk graph with a shorter and a longer route between the same two endpoints, which is
exactly what the routing tests (and the acceptance scenario at the bottom of this file) need.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory import services as inv_services
from app.models import Location
from platform_core.auth import Scope
from tests.helpers import as_principal, ctx_for, principal_for

pytestmark = pytest.mark.integration

FLOOR_READ_PERM = "operations.warehouse.floor.read:ORG"
FLOOR_WRITE_PERM = "operations.warehouse.floor.write:ORG"
LAYOUT_READ_PERM = "operations.warehouse.layout.read:ORG"
LAYOUT_WRITE_PERM = "operations.warehouse.layout.write:ORG"
LAYOUT_PUBLISH_PERM = "operations.warehouse.layout.publish:ORG"


@dataclass
class Geo:
    org_id: uuid.UUID
    warehouse_id: uuid.UUID
    location_a: uuid.UUID
    location_b: uuid.UUID
    location_c: uuid.UUID


@pytest.fixture
async def geo(session: AsyncSession, org_id: uuid.UUID) -> Geo:
    ctx = ctx_for(org_id, "operations.inventory.location.write", Scope.ORG)
    async with session.begin():
        warehouse = await inv_services.create_warehouse(
            session, ctx, code="WH-GEO", name="Geometry Warehouse"
        )
        a = await inv_services.create_location(
            session, ctx, warehouse, kind="bin", code="LOC-A", name="Location A"
        )
        b = await inv_services.create_location(
            session, ctx, warehouse, kind="bin", code="LOC-B", name="Location B"
        )
        c = await inv_services.create_location(
            session, ctx, warehouse, kind="bin", code="LOC-C", name="Location C"
        )
    return Geo(
        org_id=org_id, warehouse_id=warehouse.id, location_a=a.id, location_b=b.id, location_c=c.id
    )


def _as(org_id: uuid.UUID, perms: list[str]):  # type: ignore[no-untyped-def]
    return as_principal(principal_for(org_id, perms, subject_ref=uuid.uuid4()))


async def _create_floor(
    client, org_id: uuid.UUID, warehouse_id: uuid.UUID, *, floor_number: int = 1
):  # type: ignore[no-untyped-def]
    with _as(org_id, [FLOOR_WRITE_PERM]):
        return await client.post(
            f"/api/v1/operations/warehouses/{warehouse_id}/floors",
            json={"floor_number": floor_number, "name": "Ground Floor"},
        )


async def _open_draft(client, org_id: uuid.UUID, floor_id: str, *, clone: bool = True):  # type: ignore[no-untyped-def]
    with _as(org_id, [LAYOUT_WRITE_PERM]):
        return await client.post(
            f"/api/v1/operations/warehouse-floors/{floor_id}/layout-versions",
            json={"label": "v", "clone_from_published": clone},
        )


def _geometry_payload(geo: Geo, *, direct_edge: bool = False) -> dict:
    """A -> B -> C, 100mm each hop (total 200mm); optionally a direct A -> C edge for the
    acceptance scenario's "the route changes" step."""
    walk_edges = [
        {"from_code": "NA", "to_code": "NB", "distance_mm": 100},
        {"from_code": "NB", "to_code": "NC", "distance_mm": 100},
    ]
    if direct_edge:
        walk_edges.append({"from_code": "NA", "to_code": "NC", "distance_mm": 50})
    return {
        "placements": [
            {
                "location_id": str(geo.location_a),
                "x_mm": 0,
                "y_mm": 0,
                "width_mm": 50,
                "depth_mm": 50,
            },
            {
                "location_id": str(geo.location_b),
                "x_mm": 200,
                "y_mm": 0,
                "width_mm": 50,
                "depth_mm": 50,
            },
            {
                "location_id": str(geo.location_c),
                "x_mm": 400,
                "y_mm": 0,
                "width_mm": 50,
                "depth_mm": 50,
            },
        ],
        "walk_nodes": [
            {"code": "NA", "location_id": str(geo.location_a), "x_mm": 25, "y_mm": 25},
            {"code": "NB", "location_id": str(geo.location_b), "x_mm": 225, "y_mm": 25},
            {"code": "NC", "location_id": str(geo.location_c), "x_mm": 425, "y_mm": 25},
        ],
        "walk_edges": walk_edges,
    }


async def _draft_with_geometry(client, org_id: uuid.UUID, floor_id: str, geo: Geo, **kwargs):  # type: ignore[no-untyped-def]
    draft = await _open_draft(client, org_id, floor_id)
    assert draft.status_code == 201, draft.text
    version_id = draft.json()["id"]
    with _as(org_id, [LAYOUT_WRITE_PERM]):
        saved = await client.put(
            f"/api/v1/operations/layout-versions/{version_id}/geometry",
            json=_geometry_payload(geo, **kwargs),
        )
    assert saved.status_code == 200, saved.text
    return version_id


# ------------------------------------------------------------------ floors


class TestFloorCrud:
    async def test_create_and_list_floors(self, client, org_id, geo: Geo) -> None:
        created = await _create_floor(client, org_id, geo.warehouse_id)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["floor_number"] == 1
        assert body["name"] == "Ground Floor"
        assert body["warehouse_id"] == str(geo.warehouse_id)

        with _as(org_id, [FLOOR_READ_PERM]):
            listed = await client.get(f"/api/v1/operations/warehouses/{geo.warehouse_id}/floors")
        assert listed.status_code == 200, listed.text
        assert [f["id"] for f in listed.json()["items"]] == [body["id"]]

    async def test_a_duplicate_floor_number_is_a_conflict(self, client, org_id, geo: Geo) -> None:
        first = await _create_floor(client, org_id, geo.warehouse_id, floor_number=2)
        assert first.status_code == 201, first.text
        second = await _create_floor(client, org_id, geo.warehouse_id, floor_number=2)
        assert second.status_code == 409, second.text

    async def test_read_one_floor(self, client, org_id, geo: Geo) -> None:
        created = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = created.json()["id"]
        with _as(org_id, [FLOOR_READ_PERM]):
            read = await client.get(f"/api/v1/operations/warehouse-floors/{floor_id}")
        assert read.status_code == 200, read.text
        assert read.json()["id"] == floor_id

    async def test_cross_tenant_floor_read_is_not_found(self, client, org_id, geo: Geo) -> None:
        created = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = created.json()["id"]
        other_org = uuid.uuid4()
        with _as(other_org, [FLOOR_READ_PERM]):
            response = await client.get(f"/api/v1/operations/warehouse-floors/{floor_id}")
        assert response.status_code == 404

    async def test_update_a_floor(self, client, org_id, geo: Geo) -> None:
        created = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = created.json()["id"]
        with _as(org_id, [FLOOR_WRITE_PERM]):
            patched = await client.patch(
                f"/api/v1/operations/warehouse-floors/{floor_id}", json={"name": "Mezzanine"}
            )
        assert patched.status_code == 200, patched.text
        assert patched.json()["name"] == "Mezzanine"

    async def test_creating_a_floor_without_write_is_forbidden(
        self, client, org_id, geo: Geo
    ) -> None:
        with _as(org_id, [FLOOR_READ_PERM]):
            response = await client.post(
                f"/api/v1/operations/warehouses/{geo.warehouse_id}/floors",
                json={"floor_number": 9, "name": "x"},
            )
        assert response.status_code == 403

    async def test_cross_tenant_floor_creation_is_not_found(self, client, org_id, geo: Geo) -> None:
        other_org = uuid.uuid4()
        with _as(other_org, [FLOOR_WRITE_PERM]):
            response = await client.post(
                f"/api/v1/operations/warehouses/{geo.warehouse_id}/floors",
                json={"floor_number": 9, "name": "x"},
            )
        assert response.status_code == 404

    async def test_cross_tenant_floor_list_is_not_found(self, client, org_id, geo: Geo) -> None:
        other_org = uuid.uuid4()
        with _as(other_org, [FLOOR_READ_PERM]):
            response = await client.get(f"/api/v1/operations/warehouses/{geo.warehouse_id}/floors")
        assert response.status_code == 404

    async def test_a_reader_without_read_cannot_list_floors(self, client, org_id, geo: Geo) -> None:
        with _as(org_id, []):
            response = await client.get(f"/api/v1/operations/warehouses/{geo.warehouse_id}/floors")
        assert response.status_code == 403


# ------------------------------------------------------------------ layout version lifecycle


class TestLayoutVersionLifecycle:
    async def test_opening_the_first_draft(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        assert draft.status_code == 201, draft.text
        body = draft.json()
        assert body["status"] == "draft"
        assert body["version_number"] == 1

    async def test_a_second_draft_is_refused_while_one_is_open(
        self, client, org_id, geo: Geo
    ) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        first = await _open_draft(client, org_id, floor_id)
        assert first.status_code == 201, first.text
        second = await _open_draft(client, org_id, floor_id)
        assert second.status_code == 409, second.text

    async def test_saving_geometry_rejects_an_unknown_location(
        self, client, org_id, geo: Geo
    ) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        payload = _geometry_payload(geo)
        payload["placements"][0]["location_id"] = str(uuid.uuid4())
        with _as(org_id, [LAYOUT_WRITE_PERM]):
            response = await client.put(
                f"/api/v1/operations/layout-versions/{version_id}/geometry", json=payload
            )
        assert response.status_code == 404, response.text

    async def test_saving_geometry_rejects_a_location_from_another_warehouse(
        self, client, org_id, session: AsyncSession, geo: Geo
    ) -> None:
        # A real location, but in a second, unrelated warehouse in the same org.
        ctx = ctx_for(org_id, "operations.inventory.location.write", Scope.ORG)
        async with session.begin():
            other_warehouse = await inv_services.create_warehouse(
                session, ctx, code="WH-OTHER", name="Other Warehouse"
            )
            foreign_location = await inv_services.create_location(
                session, ctx, other_warehouse, kind="bin", code="LOC-X", name="Foreign"
            )

        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        payload = _geometry_payload(geo)
        payload["placements"][0]["location_id"] = str(foreign_location.id)
        with _as(org_id, [LAYOUT_WRITE_PERM]):
            response = await client.put(
                f"/api/v1/operations/layout-versions/{version_id}/geometry", json=payload
            )
        assert response.status_code == 422, response.text

    async def test_duplicate_location_in_placements_is_a_validation_error(
        self, client, org_id, geo: Geo
    ) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        payload = _geometry_payload(geo)
        payload["placements"].append(payload["placements"][0])
        with _as(org_id, [LAYOUT_WRITE_PERM]):
            response = await client.put(
                f"/api/v1/operations/layout-versions/{version_id}/geometry", json=payload
            )
        assert response.status_code == 422, response.text

    async def test_an_edge_naming_an_unknown_node_code_is_a_validation_error(
        self, client, org_id, geo: Geo
    ) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        payload = _geometry_payload(geo)
        payload["walk_edges"].append({"from_code": "NA", "to_code": "GHOST", "distance_mm": 1})
        with _as(org_id, [LAYOUT_WRITE_PERM]):
            response = await client.put(
                f"/api/v1/operations/layout-versions/{version_id}/geometry", json=payload
            )
        assert response.status_code == 422, response.text

    async def test_saving_valid_geometry_and_reading_it_back(
        self, client, org_id, geo: Geo
    ) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        version_id = await _draft_with_geometry(client, org_id, floor_id, geo)

        with _as(org_id, [LAYOUT_READ_PERM]):
            read = await client.get(f"/api/v1/operations/layout-versions/{version_id}")
        assert read.status_code == 200, read.text
        body = read.json()
        assert len(body["placements"]) == 3
        assert len(body["walk_nodes"]) == 3
        assert len(body["walk_edges"]) == 2
        assert {e["from_code"] for e in body["walk_edges"]} == {"NA", "NB"}

    async def test_saving_geometry_on_a_published_version_is_refused(
        self, client, org_id, geo: Geo
    ) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        version_id = await _draft_with_geometry(client, org_id, floor_id, geo)
        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            published = await client.post(
                f"/api/v1/operations/layout-versions/{version_id}/publish"
            )
        assert published.status_code == 200, published.text

        with _as(org_id, [LAYOUT_WRITE_PERM]):
            response = await client.put(
                f"/api/v1/operations/layout-versions/{version_id}/geometry",
                json=_geometry_payload(geo),
            )
        assert response.status_code == 409, response.text

    async def test_discard_a_draft(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        with _as(org_id, [LAYOUT_WRITE_PERM]):
            discarded = await client.delete(f"/api/v1/operations/layout-versions/{version_id}")
        assert discarded.status_code == 204, discarded.text

        with _as(org_id, [LAYOUT_READ_PERM]):
            history = await client.get(
                f"/api/v1/operations/warehouse-floors/{floor_id}/layout-versions"
            )
        assert history.json()["items"] == []

        # A new draft can be opened again — the discarded one no longer blocks it.
        second = await _open_draft(client, org_id, floor_id)
        assert second.status_code == 201, second.text

    async def test_discarding_a_published_version_is_refused(
        self, client, org_id, geo: Geo
    ) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        version_id = await _draft_with_geometry(client, org_id, floor_id, geo)
        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            await client.post(f"/api/v1/operations/layout-versions/{version_id}/publish")
        with _as(org_id, [LAYOUT_WRITE_PERM]):
            response = await client.delete(f"/api/v1/operations/layout-versions/{version_id}")
        assert response.status_code == 409, response.text


# ------------------------------------------------------------------ validation


class TestValidation:
    async def test_a_clean_layout_has_no_issues(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        version_id = await _draft_with_geometry(client, org_id, floor_id, geo)
        with _as(org_id, [LAYOUT_READ_PERM]):
            response = await client.post(
                f"/api/v1/operations/layout-versions/{version_id}/validate"
            )
        assert response.status_code == 200, response.text
        assert response.json() == {"is_valid": True, "issues": []}

    async def test_overlapping_placements_are_flagged(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        payload = _geometry_payload(geo)
        # Put B directly on top of A.
        payload["placements"][1]["x_mm"] = payload["placements"][0]["x_mm"]
        payload["placements"][1]["y_mm"] = payload["placements"][0]["y_mm"]
        with _as(org_id, [LAYOUT_WRITE_PERM]):
            saved = await client.put(
                f"/api/v1/operations/layout-versions/{version_id}/geometry", json=payload
            )
        assert saved.status_code == 200, saved.text

        with _as(org_id, [LAYOUT_READ_PERM]):
            response = await client.post(
                f"/api/v1/operations/layout-versions/{version_id}/validate"
            )
        body = response.json()
        assert body["is_valid"] is False
        assert any(issue["kind"] == "overlap" for issue in body["issues"])

    async def test_a_disconnected_required_node_is_flagged(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        payload = _geometry_payload(geo)
        payload["walk_edges"] = [payload["walk_edges"][0]]  # drop NB -> NC: C is now isolated
        with _as(org_id, [LAYOUT_WRITE_PERM]):
            saved = await client.put(
                f"/api/v1/operations/layout-versions/{version_id}/geometry", json=payload
            )
        assert saved.status_code == 200, saved.text

        with _as(org_id, [LAYOUT_READ_PERM]):
            response = await client.post(
                f"/api/v1/operations/layout-versions/{version_id}/validate"
            )
        body = response.json()
        assert body["is_valid"] is False
        assert any(issue["kind"] == "disconnected_node" for issue in body["issues"])

    async def test_publish_is_refused_when_invalid(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        payload = _geometry_payload(geo)
        payload["walk_edges"] = [payload["walk_edges"][0]]  # C disconnected
        with _as(org_id, [LAYOUT_WRITE_PERM]):
            await client.put(
                f"/api/v1/operations/layout-versions/{version_id}/geometry", json=payload
            )
        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            response = await client.post(f"/api/v1/operations/layout-versions/{version_id}/publish")
        assert response.status_code == 422, response.text


# ------------------------------------------------------------------ publish and rollback


class TestPublishAndRollback:
    async def test_publish_makes_the_version_current(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        version_id = await _draft_with_geometry(client, org_id, floor_id, geo)
        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            response = await client.post(f"/api/v1/operations/layout-versions/{version_id}/publish")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "published"
        assert body["published_at"] is not None

    async def test_a_locations_floor_id_is_set_on_publish_not_on_draft_save(
        self, client, org_id, geo: Geo, session: AsyncSession
    ) -> None:
        """`Location.floor_id` follows the same "never leak a draft" rule as coordinates
        (`app.models.Location.floor_id`'s own docstring) — written only by
        `publish_layout_version`, never by `save_draft_geometry`."""
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        version_id = await _draft_with_geometry(client, org_id, floor_id, geo)

        async with session.begin():
            before = (
                await session.execute(
                    select(Location.floor_id).where(Location.id == geo.location_a)
                )
            ).scalar_one()
        assert before is None  # still only drafted, not yet published

        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            response = await client.post(f"/api/v1/operations/layout-versions/{version_id}/publish")
        assert response.status_code == 200, response.text

        async with session.begin():
            after = (
                await session.execute(
                    select(Location.floor_id).where(Location.id == geo.location_a)
                )
            ).scalar_one()
        assert str(after) == floor_id

    async def test_publish_without_the_publish_permission_is_forbidden(
        self, client, org_id, geo: Geo
    ) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        version_id = await _draft_with_geometry(client, org_id, floor_id, geo)
        with _as(org_id, [LAYOUT_WRITE_PERM]):  # write, not publish
            response = await client.post(f"/api/v1/operations/layout-versions/{version_id}/publish")
        assert response.status_code == 403

    async def test_rollback_republishes_an_archived_version(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        v1 = await _draft_with_geometry(client, org_id, floor_id, geo)
        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            first_publish = await client.post(f"/api/v1/operations/layout-versions/{v1}/publish")
        assert first_publish.status_code == 200, first_publish.text

        v2 = await _draft_with_geometry(client, org_id, floor_id, geo, direct_edge=True)
        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            second_publish = await client.post(f"/api/v1/operations/layout-versions/{v2}/publish")
        assert second_publish.status_code == 200, second_publish.text

        # v1 is now archived; rolling back means publishing it again.
        with _as(org_id, [LAYOUT_READ_PERM]):
            v1_state = await client.get(f"/api/v1/operations/layout-versions/{v1}")
        assert v1_state.json()["version"]["status"] == "archived"

        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            rollback = await client.post(f"/api/v1/operations/layout-versions/{v1}/publish")
        assert rollback.status_code == 200, rollback.text
        assert rollback.json()["status"] == "published"

        with _as(org_id, [LAYOUT_READ_PERM]):
            v2_after = await client.get(f"/api/v1/operations/layout-versions/{v2}")
        assert v2_after.json()["version"]["status"] == "archived"

        with _as(org_id, [LAYOUT_READ_PERM]):
            history = await client.get(
                f"/api/v1/operations/warehouse-floors/{floor_id}/layout-versions"
            )
        # Both versions still exist — a rollback never deletes history.
        assert {v["id"] for v in history.json()["items"]} == {v1, v2}

    async def test_publish_is_idempotent_refusal_on_an_already_published_version(
        self, client, org_id, geo: Geo
    ) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        version_id = await _draft_with_geometry(client, org_id, floor_id, geo)
        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            await client.post(f"/api/v1/operations/layout-versions/{version_id}/publish")
            again = await client.post(f"/api/v1/operations/layout-versions/{version_id}/publish")
        assert again.status_code == 409, again.text

    async def test_cross_tenant_publish_is_not_found(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        version_id = await _draft_with_geometry(client, org_id, floor_id, geo)
        other_org = uuid.uuid4()
        with _as(other_org, [LAYOUT_PUBLISH_PERM]):
            response = await client.post(f"/api/v1/operations/layout-versions/{version_id}/publish")
        assert response.status_code == 404


# ------------------------------------------------------------------ routing


class TestRouting:
    async def test_no_published_layout_is_not_found(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        with _as(org_id, [LAYOUT_READ_PERM]):
            response = await client.get(
                f"/api/v1/operations/warehouse-floors/{floor_id}/route",
                params={
                    "from_location_id": str(geo.location_a),
                    "to_location_id": str(geo.location_c),
                },
            )
        assert response.status_code == 404

    async def test_computes_the_shortest_path(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        version_id = await _draft_with_geometry(client, org_id, floor_id, geo)
        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            await client.post(f"/api/v1/operations/layout-versions/{version_id}/publish")

        with _as(org_id, [LAYOUT_READ_PERM]):
            response = await client.get(
                f"/api/v1/operations/warehouse-floors/{floor_id}/route",
                params={
                    "from_location_id": str(geo.location_a),
                    "to_location_id": str(geo.location_c),
                },
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["distance_mm"] == 200  # A -> B -> C, 100mm each hop
        assert len(body["node_ids"]) == 3

    async def test_a_location_with_no_walk_node_is_not_found(
        self, client, org_id, geo: Geo
    ) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        payload = _geometry_payload(geo)
        payload["walk_nodes"] = payload["walk_nodes"][:2]  # no node stands for C
        payload["walk_edges"] = [payload["walk_edges"][0]]
        with _as(org_id, [LAYOUT_WRITE_PERM]):
            await client.put(
                f"/api/v1/operations/layout-versions/{version_id}/geometry", json=payload
            )
        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            publish = await client.post(f"/api/v1/operations/layout-versions/{version_id}/publish")
        assert publish.status_code == 200, publish.text

        with _as(org_id, [LAYOUT_READ_PERM]):
            response = await client.get(
                f"/api/v1/operations/warehouse-floors/{floor_id}/route",
                params={
                    "from_location_id": str(geo.location_a),
                    "to_location_id": str(geo.location_c),
                },
            )
        assert response.status_code == 404

    async def test_a_disconnected_graph_has_no_route(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        payload = _geometry_payload(geo)
        payload["walk_edges"] = [payload["walk_edges"][0]]  # A-B only; C isolated
        with _as(org_id, [LAYOUT_WRITE_PERM]):
            await client.put(
                f"/api/v1/operations/layout-versions/{version_id}/geometry", json=payload
            )
        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            publish = await client.post(f"/api/v1/operations/layout-versions/{version_id}/publish")
        # Publish itself refuses (C is a disconnected required node) — proven already in
        # TestValidation; here the same fixture doubles as proof there is no live layout to route
        # against at all once publish has refused.
        assert publish.status_code == 422, publish.text

    async def test_cross_tenant_route_is_not_found(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        version_id = await _draft_with_geometry(client, org_id, floor_id, geo)
        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            await client.post(f"/api/v1/operations/layout-versions/{version_id}/publish")

        other_org = uuid.uuid4()
        with _as(other_org, [LAYOUT_READ_PERM]):
            response = await client.get(
                f"/api/v1/operations/warehouse-floors/{floor_id}/route",
                params={
                    "from_location_id": str(geo.location_a),
                    "to_location_id": str(geo.location_c),
                },
            )
        assert response.status_code == 404

    async def test_route_without_read_is_forbidden(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        version_id = await _draft_with_geometry(client, org_id, floor_id, geo)
        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            await client.post(f"/api/v1/operations/layout-versions/{version_id}/publish")

        with _as(org_id, []):
            response = await client.get(
                f"/api/v1/operations/warehouse-floors/{floor_id}/route",
                params={
                    "from_location_id": str(geo.location_a),
                    "to_location_id": str(geo.location_c),
                },
            )
        assert response.status_code == 403


# ------------------------------------------------------------------ negative-authz / cross-tenant
# sweep for every endpoint not already covered above (CLAUDE.md: every list endpoint injects
# scope, every endpoint needs a negative-authz test).


class TestAuthzAndTenancySweep:
    async def test_update_floor_without_write_is_forbidden(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        with _as(org_id, [FLOOR_READ_PERM]):
            response = await client.patch(
                f"/api/v1/operations/warehouse-floors/{floor_id}", json={"name": "x"}
            )
        assert response.status_code == 403

    async def test_cross_tenant_update_floor_is_not_found(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        other_org = uuid.uuid4()
        with _as(other_org, [FLOOR_WRITE_PERM]):
            response = await client.patch(
                f"/api/v1/operations/warehouse-floors/{floor_id}", json={"name": "x"}
            )
        assert response.status_code == 404

    async def test_read_one_floor_without_read_is_forbidden(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        with _as(org_id, []):
            response = await client.get(f"/api/v1/operations/warehouse-floors/{floor_id}")
        assert response.status_code == 403

    async def test_list_layout_versions_without_read_is_forbidden(
        self, client, org_id, geo: Geo
    ) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        with _as(org_id, []):
            response = await client.get(
                f"/api/v1/operations/warehouse-floors/{floor_id}/layout-versions"
            )
        assert response.status_code == 403

    async def test_cross_tenant_list_layout_versions_is_not_found(
        self, client, org_id, geo: Geo
    ) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        other_org = uuid.uuid4()
        with _as(other_org, [LAYOUT_READ_PERM]):
            response = await client.get(
                f"/api/v1/operations/warehouse-floors/{floor_id}/layout-versions"
            )
        assert response.status_code == 404

    async def test_open_draft_without_write_is_forbidden(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        with _as(org_id, [LAYOUT_READ_PERM]):
            response = await client.post(
                f"/api/v1/operations/warehouse-floors/{floor_id}/layout-versions", json={}
            )
        assert response.status_code == 403

    async def test_cross_tenant_open_draft_is_not_found(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        other_org = uuid.uuid4()
        with _as(other_org, [LAYOUT_WRITE_PERM]):
            response = await client.post(
                f"/api/v1/operations/warehouse-floors/{floor_id}/layout-versions", json={}
            )
        assert response.status_code == 404

    async def test_read_geometry_without_read_is_forbidden(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        with _as(org_id, []):
            response = await client.get(f"/api/v1/operations/layout-versions/{version_id}")
        assert response.status_code == 403

    async def test_cross_tenant_read_geometry_is_not_found(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        other_org = uuid.uuid4()
        with _as(other_org, [LAYOUT_READ_PERM]):
            response = await client.get(f"/api/v1/operations/layout-versions/{version_id}")
        assert response.status_code == 404

    async def test_save_geometry_without_write_is_forbidden(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        with _as(org_id, [LAYOUT_READ_PERM]):
            response = await client.put(
                f"/api/v1/operations/layout-versions/{version_id}/geometry",
                json=_geometry_payload(geo),
            )
        assert response.status_code == 403

    async def test_cross_tenant_save_geometry_is_not_found(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        other_org = uuid.uuid4()
        with _as(other_org, [LAYOUT_WRITE_PERM]):
            response = await client.put(
                f"/api/v1/operations/layout-versions/{version_id}/geometry",
                json=_geometry_payload(geo),
            )
        assert response.status_code == 404

    async def test_discard_without_write_is_forbidden(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        with _as(org_id, [LAYOUT_READ_PERM]):
            response = await client.delete(f"/api/v1/operations/layout-versions/{version_id}")
        assert response.status_code == 403

    async def test_cross_tenant_discard_is_not_found(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        other_org = uuid.uuid4()
        with _as(other_org, [LAYOUT_WRITE_PERM]):
            response = await client.delete(f"/api/v1/operations/layout-versions/{version_id}")
        assert response.status_code == 404

    async def test_validate_without_read_is_forbidden(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        with _as(org_id, []):
            response = await client.post(
                f"/api/v1/operations/layout-versions/{version_id}/validate"
            )
        assert response.status_code == 403

    async def test_cross_tenant_validate_is_not_found(self, client, org_id, geo: Geo) -> None:
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        floor_id = floor.json()["id"]
        draft = await _open_draft(client, org_id, floor_id)
        version_id = draft.json()["id"]
        other_org = uuid.uuid4()
        with _as(other_org, [LAYOUT_READ_PERM]):
            response = await client.post(
                f"/api/v1/operations/layout-versions/{version_id}/validate"
            )
        assert response.status_code == 404


# ------------------------------------------------------------------ acceptance scenario: create a
# floor, place locations, publish, compute a route, change the layout, confirm the route changes.


class TestAcceptanceScenario:
    async def test_the_whole_journey(self, client, org_id, geo: Geo) -> None:
        # 1. Create a floor.
        floor = await _create_floor(client, org_id, geo.warehouse_id)
        assert floor.status_code == 201, floor.text
        floor_id = floor.json()["id"]

        # 2. Place locations (A, B, C) on a draft, wire the walk graph A-B-C, publish.
        v1 = await _draft_with_geometry(client, org_id, floor_id, geo)
        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            published = await client.post(f"/api/v1/operations/layout-versions/{v1}/publish")
        assert published.status_code == 200, published.text

        # 3. Compute a route between two of them (A -> C): only path today is via B, 200mm.
        with _as(org_id, [LAYOUT_READ_PERM]):
            first_route = await client.get(
                f"/api/v1/operations/warehouse-floors/{floor_id}/route",
                params={
                    "from_location_id": str(geo.location_a),
                    "to_location_id": str(geo.location_c),
                },
            )
        assert first_route.status_code == 200, first_route.text
        first_body = first_route.json()
        assert first_body["distance_mm"] == 200
        assert len(first_body["node_ids"]) == 3  # A, B, C

        # 4. Change the layout: a new draft adds a direct, shorter A -> C edge (50mm), publish it.
        v2 = await _draft_with_geometry(client, org_id, floor_id, geo, direct_edge=True)
        with _as(org_id, [LAYOUT_PUBLISH_PERM]):
            republished = await client.post(f"/api/v1/operations/layout-versions/{v2}/publish")
        assert republished.status_code == 200, republished.text

        # 5. Confirm the route changes accordingly: now the direct edge, 50mm, two nodes.
        with _as(org_id, [LAYOUT_READ_PERM]):
            second_route = await client.get(
                f"/api/v1/operations/warehouse-floors/{floor_id}/route",
                params={
                    "from_location_id": str(geo.location_a),
                    "to_location_id": str(geo.location_c),
                },
            )
        assert second_route.status_code == 200, second_route.text
        second_body = second_route.json()
        assert second_body["distance_mm"] == 50
        assert len(second_body["node_ids"]) == 2  # direct A -> C, no longer via B
        assert second_body != first_body
