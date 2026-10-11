"""BOMs and their versions: immutability once published (service and database), cycle rejection,
effective dating, multi-level explosion, substitutes."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.manufacturing_fixtures import (
    P_BOM_READ,
    Mfg,
    build_mfg,
    call,
    make_bom,
    make_order,
    ok,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def m(session, org_id) -> Mfg:
    return await build_mfg(session, org_id)


def vpath(bom: dict, suffix: str = "") -> str:
    return f"/boms/{bom['bom_id']}/versions/{bom['id']}{suffix}"


class TestLifecycle:
    async def test_a_new_bom_starts_with_an_empty_draft_that_cannot_be_published(
        self, client, m: Mfg
    ) -> None:
        bom = await ok(
            client,
            m.org_id,
            "POST",
            "/boms",
            expect=201,
            json={
                "code": "BOM-1",
                "name": "Shirt",
                "item_id": str(m.shirt),
                "output_quantity": "1",
            },
        )
        (draft,) = bom["versions"]
        assert (draft["version_no"], draft["status"], draft["component_count"]) == (1, "DRAFT", 0)
        assert bom["current_version_id"] is None
        refused = await call(
            client, m.org_id, "POST", f"/boms/{bom['id']}/versions/{draft['id']}/publish"
        )
        assert refused.status_code == 409 and "component" in refused.json()["detail"]

    async def test_one_bom_per_item_and_unique_codes(self, client, m: Mfg) -> None:
        await make_bom(client, m, m.shirt, "BOM-1", [(m.t.fabric, "1", "0")])
        again = await call(
            client,
            m.org_id,
            "POST",
            "/boms",
            json={"code": "BOM-2", "name": "x", "item_id": str(m.shirt)},
        )
        assert again.status_code == 409
        clash = await call(
            client,
            m.org_id,
            "POST",
            "/boms",
            json={"code": "BOM-1", "name": "x", "item_id": str(m.panel)},
        )
        assert clash.status_code == 409

    async def test_publishing_stamps_the_version_and_a_content_hash(self, client, m: Mfg) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [(m.t.fabric, "1.5", "2")])
        assert bom["status"] == "PUBLISHED"
        assert bom["published_at"] and bom["published_by"]
        assert len(bom["content_hash"]) == 64
        detail = await ok(client, m.org_id, "GET", f"/boms/{bom['bom_id']}")
        assert detail["current_version_no"] == 1 and detail["version_count"] == 1

    async def test_only_one_draft_at_a_time(self, client, m: Mfg) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [(m.t.fabric, "1", "0")])
        await ok(client, m.org_id, "POST", f"/boms/{bom['bom_id']}/versions", expect=201, json={})
        second = await call(client, m.org_id, "POST", f"/boms/{bom['bom_id']}/versions", json={})
        assert second.status_code == 409 and "draft" in second.json()["detail"]

    async def test_a_new_version_can_copy_components_and_substitutes(self, client, m: Mfg) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [(m.t.fabric, "1", "0")], publish=False)
        await ok(
            client,
            m.org_id,
            "POST",
            vpath(bom, f"/components/{bom['components'][0]['id']}/substitutes"),
            expect=201,
            json={"substitute_item_id": str(m.t.dye), "ratio": "1.5", "priority": 2},
        )
        await ok(client, m.org_id, "POST", vpath(bom, "/publish"))
        v2 = await ok(
            client,
            m.org_id,
            "POST",
            f"/boms/{bom['bom_id']}/versions",
            expect=201,
            json={"copy_from_version_id": bom["id"], "output_quantity": "2"},
        )
        assert v2["version_no"] == 2 and v2["status"] == "DRAFT" and v2["output_quantity"] == "2"
        (line,) = v2["components"]
        assert line["quantity"] == "1"
        assert [(s["sku"], s["ratio"], s["priority"]) for s in line["substitutes"]] == [
            ("DYE-RED-01", "1.5", 2)
        ]

    async def test_a_draft_can_be_discarded_but_not_a_published_version(
        self, client, m: Mfg
    ) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [(m.t.fabric, "1", "0")])
        draft = await ok(
            client, m.org_id, "POST", f"/boms/{bom['bom_id']}/versions", expect=201, json={}
        )
        await ok(
            client,
            m.org_id,
            "DELETE",
            f"/boms/{bom['bom_id']}/versions/{draft['id']}",
            expect=204,
        )
        gone = await call(client, m.org_id, "GET", f"/boms/{bom['bom_id']}/versions/{draft['id']}")
        assert gone.status_code == 404
        refused = await call(client, m.org_id, "DELETE", vpath(bom))
        assert refused.status_code == 409

    async def test_quantities_are_validated_against_the_unit(self, client, m: Mfg) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [], publish=False)
        for body in (
            {"component_item_id": str(m.t.buttons), "quantity": "1.5"},  # pcs is discrete
            {"component_item_id": str(m.t.fabric), "quantity": "0"},
            {"component_item_id": str(m.t.fabric), "quantity": "1.2345"},
            {"component_item_id": str(m.t.fabric), "quantity": "1", "scrap_percent": "101"},
            {"component_item_id": str(m.t.fabric), "quantity": "1", "scrap_percent": "1.234"},
        ):
            response = await call(client, m.org_id, "POST", vpath(bom, "/components"), json=body)
            assert response.status_code in (409, 422), (body, response.text)
            assert response.status_code == 422, (body, response.text)

    async def test_effective_window_must_be_ordered(self, client, m: Mfg) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [], publish=False)
        response = await call(
            client,
            m.org_id,
            "POST",
            vpath(bom, "/components"),
            json={
                "component_item_id": str(m.t.fabric),
                "quantity": "1",
                "effective_from": "2026-12-01",
                "effective_to": "2026-11-01",
            },
        )
        assert response.status_code == 422

    async def test_creating_twice_with_one_key_makes_one_bom(self, client, m: Mfg) -> None:
        body = {"code": "BOM-1", "name": "Shirt", "item_id": str(m.shirt)}
        first = await ok(client, m.org_id, "POST", "/boms", expect=201, json=body, key="b-1")
        again = await ok(client, m.org_id, "POST", "/boms", expect=200, json=body, key="b-1")
        assert first["id"] == again["id"]
        assert (await ok(client, m.org_id, "GET", "/boms"))["total"] == 1

    async def test_a_retried_add_with_one_key_adds_one_line_and_one_substitute(
        self, client, m: Mfg
    ) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [], publish=False)
        body = {"component_item_id": str(m.t.fabric), "quantity": "2"}
        path = vpath(bom, "/components")
        first = await ok(client, m.org_id, "POST", path, expect=201, json=body, key="c-1")
        again = await ok(client, m.org_id, "POST", path, expect=201, json=body, key="c-1")
        assert len(first["components"]) == len(again["components"]) == 1
        component = first["components"][0]["id"]
        sub = {"substitute_item_id": str(m.t.dye), "ratio": "1.5"}
        sub_path = vpath(bom, f"/components/{component}/substitutes")
        await ok(client, m.org_id, "POST", sub_path, expect=201, json=sub, key="s-1")
        replay = await ok(client, m.org_id, "POST", sub_path, expect=201, json=sub, key="s-1")
        assert len(replay["components"][0]["substitutes"]) == 1
        # Once published, a replay of the earlier add still answers with the result, while a
        # fresh add (new key) is the 409 it always was.
        await ok(client, m.org_id, "POST", vpath(bom, "/publish"))
        late = await ok(client, m.org_id, "POST", path, expect=201, json=body, key="c-1")
        assert len(late["components"]) == 1
        fresh = await call(client, m.org_id, "POST", path, json=body, key="c-2")
        assert fresh.status_code == 409


class TestImmutability:
    async def test_every_edit_of_a_published_version_is_a_409(self, client, m: Mfg) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [(m.t.fabric, "1", "0")])
        component = bom["components"][0]["id"]
        attempts = [
            ("PATCH", vpath(bom), {"output_quantity": "2"}),
            (
                "POST",
                vpath(bom, "/components"),
                {"component_item_id": str(m.t.dye), "quantity": "1"},
            ),
            ("PATCH", vpath(bom, f"/components/{component}"), {"quantity": "9"}),
            ("DELETE", vpath(bom, f"/components/{component}"), None),
            (
                "POST",
                vpath(bom, f"/components/{component}/substitutes"),
                {"substitute_item_id": str(m.t.dye)},
            ),
            ("POST", vpath(bom, "/publish"), None),
        ]
        for method, path, body in attempts:
            response = await call(client, m.org_id, method, path, json=body)
            assert response.status_code == 409, (method, path, response.text)
        # ... and it is unchanged.
        again = await ok(client, m.org_id, "GET", vpath(bom))
        assert again["components"][0]["quantity"] == "1" and again["output_quantity"] == "1"
        assert again["content_hash"] == bom["content_hash"]

    async def test_a_retired_version_is_as_frozen(self, client, m: Mfg) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [(m.t.fabric, "1", "0")])
        retired = await ok(client, m.org_id, "POST", vpath(bom, "/retire"))
        assert retired["status"] == "RETIRED"
        again = await call(client, m.org_id, "PATCH", vpath(bom), json={"notes": "x"})
        assert again.status_code == 409
        twice = await call(client, m.org_id, "POST", vpath(bom, "/retire"))
        assert twice.status_code == 409

    async def test_the_database_refuses_even_a_write_that_skips_the_service(
        self, client, session, admin_session, m: Mfg
    ) -> None:
        """The triggers are the floor: a SQL console, an import script or a path that forgot the
        service check still cannot change what a production order was planned from."""
        bom = await make_bom(client, m, m.shirt, "BOM-1", [(m.t.fabric, "1", "0")])
        statements = [
            "UPDATE operations.bom_components SET quantity_milli = 9999 WHERE bom_version_id = :v",
            "DELETE FROM operations.bom_components WHERE bom_version_id = :v",
            "UPDATE operations.bom_versions SET output_quantity_milli = 5000 WHERE id = :v",
            "UPDATE operations.bom_versions SET content_hash = 'forged' WHERE id = :v",
            "UPDATE operations.bom_versions SET status = 'DRAFT' WHERE id = :v",
            "DELETE FROM operations.bom_versions WHERE id = :v",
            """INSERT INTO operations.bom_components
                 (id, organization_id, bom_version_id, line_no, component_item_id,
                  quantity_milli, scrap_bps, notes)
               VALUES (gen_random_uuid(), :org, :v, 99, gen_random_uuid(), 1000, 0, '')""",
        ]
        for sql in statements:
            # as the app role, tenant-bound — and as the bypassing role
            for s in (session, admin_session):
                with pytest.raises(DBAPIError, match=r"immutable|cannot"):
                    async with s.begin():
                        await s.execute(text(sql), {"v": uuid.UUID(bom["id"]), "org": m.org_id})
        again = await ok(client, m.org_id, "GET", vpath(bom))
        assert again["components"][0]["quantity"] == "1" and again["status"] == "PUBLISHED"

    async def test_publish_to_retire_is_the_only_status_change_the_database_allows(
        self, client, admin_session, m: Mfg
    ) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [(m.t.fabric, "1", "0")])
        async with admin_session.begin():
            await admin_session.execute(
                text("UPDATE operations.bom_versions SET status = 'RETIRED' WHERE id = :v"),
                {"v": uuid.UUID(bom["id"])},
            )
        with pytest.raises(DBAPIError, match="cannot move"):
            async with admin_session.begin():
                await admin_session.execute(
                    text("UPDATE operations.bom_versions SET status = 'PUBLISHED' WHERE id = :v"),
                    {"v": uuid.UUID(bom["id"])},
                )

    async def test_an_order_keeps_its_figures_when_a_newer_version_is_published(
        self, client, m: Mfg
    ) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [(m.t.fabric, "2", "0")])
        order = await make_order(client, m, m.shirt, "5", approve=False)
        v2 = await ok(
            client,
            m.org_id,
            "POST",
            f"/boms/{bom['bom_id']}/versions",
            expect=201,
            json={"copy_from_version_id": bom["id"]},
        )
        await ok(
            client,
            m.org_id,
            "PATCH",
            f"/boms/{bom['bom_id']}/versions/{v2['id']}/components/{v2['components'][0]['id']}",
            json={"quantity": "3"},
        )
        await ok(client, m.org_id, "POST", f"/boms/{bom['bom_id']}/versions/{v2['id']}/publish")
        new_order = await make_order(client, m, m.shirt, "5", approve=False)
        old = await ok(client, m.org_id, "GET", f"/orders/{order['id']}")
        assert old["materials"][0]["required"] == "10" and old["bom_version_no"] == 1
        # The newer order is planned from the version in force today: v2 (no start date, later
        # version number wins the tie).
        assert new_order["materials"][0]["required"] == "15" and new_order["bom_version_no"] == 2


class TestCycles:
    async def test_a_bom_cannot_contain_its_own_item(self, client, m: Mfg) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [], publish=False)
        response = await call(
            client,
            m.org_id,
            "POST",
            vpath(bom, "/components"),
            json={"component_item_id": str(m.shirt), "quantity": "1"},
        )
        assert response.status_code == 409
        assert "SHIRT-01 -> SHIRT-01" in response.json()["detail"]

    async def test_an_indirect_loop_through_a_published_bom_is_refused_with_the_path(
        self, client, m: Mfg
    ) -> None:
        # panel is made from fabric...
        await make_bom(client, m, m.panel, "BOM-PANEL", [(m.t.fabric, "1", "0")])
        # ...so a panel BOM may not later include the shirt that contains the panel.
        shirt = await make_bom(client, m, m.shirt, "BOM-SHIRT", [(m.panel, "2", "0")])
        v2 = await ok(
            client,
            m.org_id,
            "POST",
            f"/boms/{shirt['bom_id']}/versions",
            expect=201,
            json={"copy_from_version_id": shirt["id"]},
        )
        # a draft of the PANEL bom adding the shirt closes the loop panel -> shirt -> panel
        panel_bom = await ok(client, m.org_id, "GET", "/boms", params={"item_id": str(m.panel)})
        panel_id = panel_bom["items"][0]["id"]
        panel_v2 = await ok(
            client, m.org_id, "POST", f"/boms/{panel_id}/versions", expect=201, json={}
        )
        response = await call(
            client,
            m.org_id,
            "POST",
            f"/boms/{panel_id}/versions/{panel_v2['id']}/components",
            json={"component_item_id": str(m.shirt), "quantity": "1"},
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "PANEL-NAVY -> SHIRT-01 -> PANEL-NAVY" in detail
        assert v2["status"] == "DRAFT"

    async def test_a_substitute_cannot_close_a_loop_either(self, client, m: Mfg) -> None:
        await make_bom(client, m, m.panel, "BOM-PANEL", [(m.shirt, "1", "0")])
        bom = await make_bom(
            client, m, m.shirt, "BOM-SHIRT", [(m.t.fabric, "1", "0")], publish=False
        )
        response = await call(
            client,
            m.org_id,
            "POST",
            vpath(bom, f"/components/{bom['components'][0]['id']}/substitutes"),
            json={"substitute_item_id": str(m.panel)},
        )
        assert response.status_code == 409 and "contain itself" in response.json()["detail"]

    async def test_publishing_rechecks_two_drafts_that_were_each_fine_alone(
        self, client, m: Mfg
    ) -> None:
        # Both drafts are valid while the other is unpublished; the second publish closes a loop.
        a = await make_bom(client, m, m.shirt, "BOM-A", [(m.panel, "1", "0")], publish=False)
        b = await make_bom(client, m, m.panel, "BOM-B", [(m.shirt, "1", "0")], publish=False)
        await ok(client, m.org_id, "POST", vpath(a, "/publish"))
        response = await call(client, m.org_id, "POST", vpath(b, "/publish"))
        assert response.status_code == 409 and "contain itself" in response.json()["detail"]
        still = await ok(client, m.org_id, "GET", vpath(b))
        assert still["status"] == "DRAFT"


class TestEffectiveDating:
    async def test_the_order_uses_the_version_in_force_on_its_start_date(
        self, client, m: Mfg
    ) -> None:
        v1 = await make_bom(client, m, m.shirt, "BOM-1", [(m.t.fabric, "1", "0")])
        v2 = await ok(
            client,
            m.org_id,
            "POST",
            f"/boms/{v1['bom_id']}/versions",
            expect=201,
            json={"copy_from_version_id": v1["id"], "effective_from": "2099-01-01"},
        )
        await ok(
            client,
            m.org_id,
            "PATCH",
            f"/boms/{v1['bom_id']}/versions/{v2['id']}/components/{v2['components'][0]['id']}",
            json={"quantity": "4"},
        )
        await ok(client, m.org_id, "POST", f"/boms/{v1['bom_id']}/versions/{v2['id']}/publish")
        now = await make_order(client, m, m.shirt, "2", approve=False)
        future = await make_order(
            client, m, m.shirt, "2", approve=False, planned_start="2099-06-01"
        )
        assert (now["bom_version_no"], now["materials"][0]["required"]) == (1, "2")
        assert (future["bom_version_no"], future["materials"][0]["required"]) == (2, "8")

    async def test_nothing_in_force_on_the_date_is_refused(self, client, m: Mfg) -> None:
        await make_bom(
            client, m, m.shirt, "BOM-1", [(m.t.fabric, "1", "0")], effective_from="2099-01-01"
        )
        response = await call(
            client,
            m.org_id,
            "POST",
            "/orders",
            json={
                "item_id": str(m.shirt),
                "quantity": "1",
                "output_location_id": str(m.t.finishing),
            },
        )
        assert response.status_code == 409 and "no published version" in response.json()["detail"]

    async def test_a_component_outside_its_window_is_left_off_the_order(
        self, client, m: Mfg
    ) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [(m.t.fabric, "1", "0")], publish=False)
        await ok(
            client,
            m.org_id,
            "POST",
            vpath(bom, "/components"),
            expect=201,
            json={
                "component_item_id": str(m.t.dye),
                "quantity": "1",
                "effective_from": "2099-01-01",
            },
        )
        await ok(client, m.org_id, "POST", vpath(bom, "/publish"))
        today = await make_order(client, m, m.shirt, "1", approve=False)
        later = await make_order(client, m, m.shirt, "1", approve=False, planned_start="2099-02-01")
        assert [x["sku"] for x in today["materials"]] == ["COT-180-NAVY"]
        assert sorted(x["sku"] for x in later["materials"]) == ["COT-180-NAVY", "DYE-RED-01"]

    async def test_a_retired_version_cannot_be_pinned(self, client, m: Mfg) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [(m.t.fabric, "1", "0")])
        await ok(client, m.org_id, "POST", vpath(bom, "/retire"))
        response = await call(
            client,
            m.org_id,
            "POST",
            "/orders",
            json={
                "item_id": str(m.shirt),
                "bom_version_id": bom["id"],
                "quantity": "1",
                "output_location_id": str(m.t.finishing),
            },
        )
        assert response.status_code == 409


class TestExplosion:
    async def test_multi_level_requirements_and_leaf_totals(self, client, m: Mfg) -> None:
        # 10 m of panel = 12 m of fabric + 0.5 kg dye ; 1 shirt = 2 m panel (10 % scrap) + 6 buttons
        await make_bom(
            client,
            m,
            m.panel,
            "BOM-PANEL",
            [(m.t.fabric, "12", "0"), (m.t.dye, "0.5", "0")],
            output="10",
        )
        shirt = await make_bom(
            client, m, m.shirt, "BOM-SHIRT", [(m.panel, "2", "10"), (m.t.buttons, "6", "0")]
        )
        tree = await ok(
            client, m.org_id, "GET", vpath(shirt, "/explosion"), params={"quantity": "5"}
        )
        root = tree["root"]
        assert (root["sku"], root["quantity"], root["level"]) == ("SHIRT-01", "5", 0)
        by_sku = {c["sku"]: c for c in root["children"]}
        assert by_sku["PANEL-NAVY"]["quantity"] == "11"  # 5 * 2 * 1.1
        assert by_sku["PANEL-NAVY"]["is_produced"] is True
        assert by_sku["BTN-14MM"]["quantity"] == "30"
        leaves = {c["sku"]: c["quantity"] for c in by_sku["PANEL-NAVY"]["children"]}
        assert leaves == {"COT-180-NAVY": "13.2", "DYE-RED-01": "0.55"}
        assert {t["sku"]: t["quantity"] for t in tree["leaf_totals"]} == {
            "BTN-14MM": "30",
            "COT-180-NAVY": "13.2",
            "DYE-RED-01": "0.55",
        }

    async def test_a_bad_quantity_is_a_422(self, client, m: Mfg) -> None:
        bom = await make_bom(client, m, m.shirt, "BOM-1", [(m.t.fabric, "1", "0")])
        response = await call(
            client, m.org_id, "GET", vpath(bom, "/explosion"), params={"quantity": "1.5"}
        )
        assert response.status_code == 422  # shirts are counted in pieces


class TestReads:
    async def test_list_filters(self, client, m: Mfg) -> None:
        await make_bom(client, m, m.shirt, "BOM-SHIRT", [(m.t.fabric, "1", "0")])
        await make_bom(client, m, m.panel, "BOM-PANEL", [(m.t.fabric, "1", "0")])
        everything = await ok(client, m.org_id, "GET", "/boms", perms=[P_BOM_READ])
        assert everything["total"] == 2
        by_code = await ok(client, m.org_id, "GET", "/boms", params={"q": "panel"})
        assert [b["code"] for b in by_code["items"]] == ["BOM-PANEL"]
        deactivated = await ok(
            client,
            m.org_id,
            "PATCH",
            f"/boms/{by_code['items'][0]['id']}",
            json={"is_active": False},
        )
        assert deactivated["is_active"] is False
        active = await ok(client, m.org_id, "GET", "/boms", params={"is_active": "true"})
        assert [b["code"] for b in active["items"]] == ["BOM-SHIRT"]
        # An inactive BOM cannot be planned from.
        response = await call(
            client,
            m.org_id,
            "POST",
            "/orders",
            json={
                "item_id": str(m.panel),
                "quantity": "1",
                "output_location_id": str(m.t.finishing),
            },
        )
        assert response.status_code == 409
