"""The MRP permissions, their role grants, and the committed manifest ADR-0007's reconcile reads: a
permission nobody can grant, or a manifest behind the code, is a 403 in every existing tenant with
no error anywhere."""

from __future__ import annotations

import json
from pathlib import Path

from app.catalogue import OPERATIONS, OPERATIONS_ROLE_TEMPLATES

MRP = (
    "operations.manufacturing.mrp.read",
    "operations.manufacturing.mrp.run",
    "operations.manufacturing.mrp.requisition",
)
MANIFEST = (
    Path(__file__).resolve().parents[3] / "libs" / "contracts" / "permissions" / "operations.json"
)


def template(code: str):  # type: ignore[no-untyped-def]
    return next(t for t in OPERATIONS_ROLE_TEMPLATES if t.code == code)


def test_the_catalogue_holds_exactly_the_three_mrp_permissions() -> None:
    assert {c for c in OPERATIONS.codes() if ".mrp." in c} == set(MRP)


def test_every_mrp_permission_is_org_only() -> None:
    for spec in OPERATIONS.specs:
        if spec.resource == "mrp":
            assert (spec.default_scope, spec.max_scope) == ("ORG", "ORG"), spec


def test_the_admin_holds_all_of_them_at_org() -> None:
    held = {g.code: g.scope for g in template("OPERATIONS_ADMIN").grants}
    for code in MRP:
        assert held[code] == "ORG"


def test_the_manager_and_the_planner_hold_all_of_them() -> None:
    for role in ("OPERATIONS_MANAGER", "PRODUCTION_PLANNER"):
        held = {g.code: g.scope for g in template(role).grants}
        for code in MRP:
            assert held[code] == "ORG", (role, code)


def test_no_other_role_gains_mrp_by_accident() -> None:
    for code in ("OPERATIONS_USER", "OPERATIONS_WORKER"):
        assert not {g.code for g in template(code).grants} & set(MRP), code


def test_the_committed_manifest_carries_them() -> None:
    """`tools/reconcile_permissions.py operations --all` reads this file (ADR-0007): without the
    codes in it, no existing organization ever receives them."""
    manifest = json.loads(MANIFEST.read_text())
    codes = {
        f"operations.{p['module']}.{p['resource']}.{p['action']}": p
        for p in manifest["permissions"]
    }
    for code in MRP:
        assert code in codes, code
    templates = {t["code"]: t for t in manifest["role_templates"]}
    for role in ("OPERATIONS_MANAGER", "PRODUCTION_PLANNER"):
        assert set(MRP) <= {g["code"] for g in templates[role]["grants"]}, role
