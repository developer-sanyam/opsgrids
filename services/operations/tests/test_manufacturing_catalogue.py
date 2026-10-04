"""The manufacturing permissions, their role grants, and the committed manifest ADR-0007's
reconcile reads: a permission nobody can grant, or a manifest behind the code, is a 403 in every
existing tenant with no error anywhere."""

from __future__ import annotations

import json
from pathlib import Path

from app.catalogue import OPERATIONS, OPERATIONS_ROLE_TEMPLATES

MANUFACTURING = (
    "operations.manufacturing.bom.read",
    "operations.manufacturing.bom.write",
    "operations.manufacturing.bom.publish",
    "operations.manufacturing.production_order.read",
    "operations.manufacturing.production_order.write",
    "operations.manufacturing.production_order.approve",
    "operations.manufacturing.production_order.issue",
    "operations.manufacturing.production_order.complete",
)
MANIFEST = (
    Path(__file__).resolve().parents[3] / "libs" / "contracts" / "permissions" / "operations.json"
)


def template(code: str):  # type: ignore[no-untyped-def]
    return next(t for t in OPERATIONS_ROLE_TEMPLATES if t.code == code)


def test_the_catalogue_holds_exactly_the_eight_manufacturing_permissions() -> None:
    assert {c for c in OPERATIONS.codes() if ".manufacturing." in c} == set(MANUFACTURING)


def test_publishing_a_bom_and_approving_an_order_are_dangerous_and_nothing_else_is() -> None:
    dangerous = {
        s.code("operations")
        for s in OPERATIONS.specs
        if s.module == "manufacturing" and s.is_dangerous
    }
    assert dangerous == {
        "operations.manufacturing.bom.publish",
        "operations.manufacturing.production_order.approve",
    }


def test_every_manufacturing_permission_is_org_only() -> None:
    for spec in OPERATIONS.specs:
        if spec.module == "manufacturing":
            assert (spec.default_scope, spec.max_scope) == ("ORG", "ORG"), spec


def test_the_admin_holds_all_of_them_at_org() -> None:
    held = {g.code: g.scope for g in template("OPERATIONS_ADMIN").grants}
    for code in MANUFACTURING:
        assert held[code] == "ORG"


def test_the_manager_holds_all_of_them() -> None:
    held = {g.code: g.scope for g in template("OPERATIONS_MANAGER").grants}
    for code in MANUFACTURING:
        assert held[code] == "ORG"


def test_the_production_planner_runs_production_but_cannot_publish_or_approve() -> None:
    planner = template("PRODUCTION_PLANNER")
    assert planner.product == "operations" and not planner.is_default
    held = {g.code for g in planner.grants}
    assert held >= {
        "operations.manufacturing.bom.read",
        "operations.manufacturing.bom.write",
        "operations.manufacturing.production_order.read",
        "operations.manufacturing.production_order.write",
        "operations.manufacturing.production_order.issue",
        "operations.manufacturing.production_order.complete",
        "operations.inventory.item.read",
        "operations.inventory.warehouse.read",
        "operations.inventory.location.read",
    }
    assert "operations.manufacturing.bom.publish" not in held
    assert "operations.manufacturing.production_order.approve" not in held
    # ... and nothing outside its remit: no stock adjustment, no order accept, no PO approval.
    assert not {
        c for c in held if ".inventory.stock." in c or ".orders." in c or ".procurement." in c
    }


def test_no_other_role_gains_manufacturing_by_accident() -> None:
    for code in ("OPERATIONS_USER", "OPERATIONS_WORKER"):
        assert not {g.code for g in template(code).grants} & set(MANUFACTURING), code


def test_the_committed_manifest_carries_them_and_the_planner_template() -> None:
    """`tools/reconcile_permissions.py operations --all` reads this file (ADR-0007): without the
    codes in it, no existing organization ever receives them."""
    manifest = json.loads(MANIFEST.read_text())
    codes = {
        f"operations.{p['module']}.{p['resource']}.{p['action']}": p
        for p in manifest["permissions"]
    }
    for code in MANUFACTURING:
        assert code in codes, code
    assert codes["operations.manufacturing.bom.publish"]["is_dangerous"] is True
    assert codes["operations.manufacturing.production_order.approve"]["is_dangerous"] is True
    templates = {t["code"]: t for t in manifest["role_templates"]}
    assert "PRODUCTION_PLANNER" in templates
    manager = {g["code"] for g in templates["OPERATIONS_MANAGER"]["grants"]}
    assert set(MANUFACTURING) <= manager
