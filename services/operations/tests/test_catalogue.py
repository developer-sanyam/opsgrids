"""The permission catalogue is the product's vocabulary, and the guard that keeps an admin role
built from *this* product only. A wrong-product grant must be a red build, not a super-grant found
during an audit.
"""

from __future__ import annotations

import pytest

from app.catalogue import OPERATIONS, OPERATIONS_REGISTRATION, OPERATIONS_ROLE_TEMPLATES
from platform_core.catalogue import Grant, RoleTemplate, validate


def test_every_template_belongs_to_the_operations_product() -> None:
    for template in OPERATIONS_ROLE_TEMPLATES:
        assert template.product == "operations"
        for grant in template.grants:
            assert grant.code.startswith("operations.")


def test_admin_is_built_from_the_operations_catalogue_alone() -> None:
    admin = next(t for t in OPERATIONS_ROLE_TEMPLATES if t.code == "OPERATIONS_ADMIN")
    assert {g.code for g in admin.grants} == set(OPERATIONS.codes())
    # Every grant tops out at ORG *unless* the permission's own `max_scope` is narrower —
    # `fulfilment.task.execute`'s ceiling is `OWN` (ADR-0025 §2/§3, plan F3): acting on another
    # worker's own offered/accepted task is `task.override`'s job, never a wider blanket grant,
    # so even `OPERATIONS_ADMIN`'s `all("ORG")` is clamped down to `OWN` here by `min_scope`,
    # deliberately (CLAUDE.md: holding a permission is not holding enough scope).
    for grant in admin.grants:
        if grant.code == "operations.fulfilment.task.execute":
            assert grant.scope == "OWN"
        else:
            assert grant.scope == "ORG"


def test_there_is_exactly_one_default_role() -> None:
    defaults = [t for t in OPERATIONS_ROLE_TEMPLATES if t.is_default]
    assert [t.code for t in defaults] == ["OPERATIONS_USER"]


def test_a_foreign_product_grant_is_refused_at_validation() -> None:
    rogue = RoleTemplate(
        code="ROGUE",
        name="Rogue",
        product="operations",
        grants=[Grant("crm.pipeline.deal.read", "ORG")],
    )
    with pytest.raises(ValueError, match="belongs to product"):
        validate([rogue])


def test_the_registration_validates_at_construction() -> None:
    assert OPERATIONS_REGISTRATION.product == "operations"
    assert OPERATIONS_REGISTRATION.catalogue is OPERATIONS


PHASE_1_PERMISSIONS = (
    "operations.inventory.lot.write",
    "operations.inventory.roll.write",
    "operations.inventory.stock.read",
    "operations.inventory.stock.transfer",
)


def _grants(role_code: str) -> dict[str, str]:
    template = next(t for t in OPERATIONS_ROLE_TEMPLATES if t.code == role_code)
    return {g.code: g.scope for g in template.grants}


def test_the_phase_1_inventory_permissions_are_declared_org_only_and_not_dangerous() -> None:
    declared = {spec.code(OPERATIONS.product): spec for spec in OPERATIONS.specs}
    for code in PHASE_1_PERMISSIONS:
        assert code in declared, code
        assert declared[code].max_scope == "ORG" and not declared[code].is_dangerous, code


def test_the_phase_1_permissions_reach_the_roles_that_should_hold_them() -> None:
    assert set(PHASE_1_PERMISSIONS) <= set(_grants("OPERATIONS_ADMIN"))
    manager = _grants("OPERATIONS_MANAGER")
    assert all(manager.get(code) == "ORG" for code in PHASE_1_PERMISSIONS)
    worker = _grants("OPERATIONS_WORKER")
    assert {c for c in PHASE_1_PERMISSIONS if c in worker} == {
        "operations.inventory.stock.read",
        "operations.inventory.stock.transfer",
    }
    # A worker moves and reads stock; registering, splitting and merging rolls stays with the
    # manager. The plain seat sees none of the new surface.
    assert not set(PHASE_1_PERMISSIONS) & set(_grants("OPERATIONS_USER"))


PHASE_2_PERMISSIONS = (
    "operations.procurement.requisition.read",
    "operations.procurement.requisition.write",
    "operations.procurement.requisition.approve",
    "operations.procurement.receipt.read",
    "operations.procurement.receipt.write",
    "operations.inventory.stock.receive",
)


def test_the_phase_2_permissions_are_declared_and_not_dangerous() -> None:
    declared = {spec.code(OPERATIONS.product): spec for spec in OPERATIONS.specs}
    for code in PHASE_2_PERMISSIONS:
        assert code in declared, code
        assert not declared[code].is_dangerous, code
    # approve and the receiving keys have no OWN reading: they are ORG-only
    for code in (
        "operations.procurement.requisition.approve",
        "operations.procurement.receipt.read",
        "operations.procurement.receipt.write",
        "operations.inventory.stock.receive",
    ):
        assert declared[code].default_scope == "ORG" and declared[code].max_scope == "ORG", code
    # a requisition has an OWN reading (the ones I raised), topping out at ORG
    for code in (
        "operations.procurement.requisition.read",
        "operations.procurement.requisition.write",
    ):
        assert (declared[code].default_scope, declared[code].max_scope) == ("OWN", "ORG"), code


def test_the_phase_2_permissions_reach_the_roles_that_should_hold_them() -> None:
    assert set(PHASE_2_PERMISSIONS) <= set(_grants("OPERATIONS_ADMIN"))
    manager = _grants("OPERATIONS_MANAGER")
    assert all(manager.get(code) == "ORG" for code in PHASE_2_PERMISSIONS)
    worker = _grants("OPERATIONS_WORKER")
    # goods-in: read POs and receipts, post a receipt — never raise or approve anything
    assert {c for c in PHASE_2_PERMISSIONS if c in worker} == {
        "operations.procurement.receipt.read",
        "operations.procurement.receipt.write",
        "operations.inventory.stock.receive",
    }
    assert worker["operations.procurement.purchase_order.read"] == "ORG"
    assert "operations.procurement.purchase_order.write" not in worker
    user = _grants("OPERATIONS_USER")
    assert {c for c in PHASE_2_PERMISSIONS if c in user} == {
        "operations.procurement.requisition.read",
        "operations.procurement.requisition.write",
    }
    assert user["operations.procurement.requisition.read"] == "OWN"
    assert user["operations.procurement.requisition.write"] == "OWN"


def test_the_buyer_buys_but_approves_and_receives_nothing() -> None:
    buyer = _grants("BUYER")
    assert buyer["operations.procurement.purchase_order.write"] == "ORG"
    assert buyer["operations.procurement.requisition.write"] == "ORG"
    assert buyer["operations.procurement.receipt.read"] == "ORG"
    for forbidden in (
        "operations.procurement.purchase_order.approve",
        "operations.procurement.requisition.approve",
        "operations.procurement.receipt.write",
        "operations.inventory.stock.receive",
        "operations.inventory.stock.adjust",
    ):
        assert forbidden not in buyer, forbidden
    # a role template of this product is built from this product's own catalogue
    assert all(code.startswith("operations.") for code in buyer)


PHASE_3_PERMISSIONS = (
    "operations.inventory.transfer.write",
    "operations.inventory.transfer.approve",
    "operations.inventory.transfer.dispatch",
    "operations.inventory.transfer.receive",
    "operations.inventory.control.configure",
)
PHASE_3_DANGEROUS = {
    "operations.inventory.transfer.approve",
    "operations.inventory.control.configure",
}


def test_the_phase_3_permissions_are_org_only_and_the_right_ones_are_dangerous() -> None:
    declared = {spec.code(OPERATIONS.product): spec for spec in OPERATIONS.specs}
    for code in PHASE_3_PERMISSIONS:
        assert code in declared, code
        assert declared[code].max_scope == "ORG", code
        assert declared[code].is_dangerous is (code in PHASE_3_DANGEROUS), code


def test_the_phase_3_permissions_reach_the_roles_that_should_hold_them() -> None:
    assert set(PHASE_3_PERMISSIONS) <= set(_grants("OPERATIONS_ADMIN"))
    # The manager runs the whole transfer flow but never sets the thresholds that decide whether a
    # transfer or an adjustment needs approval at all — that stays with the admin.
    manager = _grants("OPERATIONS_MANAGER")
    assert {c for c in PHASE_3_PERMISSIONS if c in manager} == set(PHASE_3_PERMISSIONS) - {
        "operations.inventory.control.configure"
    }
    # The storekeeper drafts, ships and receives; they never sign a transfer off (the separation
    # that makes `approve` mean something), and never configure.
    worker = _grants("OPERATIONS_WORKER")
    assert {c for c in PHASE_3_PERMISSIONS if c in worker} == {
        "operations.inventory.transfer.write",
        "operations.inventory.transfer.dispatch",
        "operations.inventory.transfer.receive",
    }
    assert not set(PHASE_3_PERMISSIONS) & set(_grants("OPERATIONS_USER"))


PHASE_9_PERMISSIONS = (
    "operations.inventory.data.import",
    "operations.inventory.data.export",
)


def test_the_phase_9_data_permissions_are_org_only_and_only_import_is_dangerous() -> None:
    declared = {spec.code(OPERATIONS.product): spec for spec in OPERATIONS.specs}
    for code in PHASE_9_PERMISSIONS:
        assert code in declared, code
        assert declared[code].max_scope == "ORG", code
    assert declared["operations.inventory.data.import"].is_dangerous
    assert not declared["operations.inventory.data.export"].is_dangerous


def test_bulk_import_stays_with_the_admin_and_export_reaches_the_manager() -> None:
    assert set(PHASE_9_PERMISSIONS) <= set(_grants("OPERATIONS_ADMIN"))
    manager = _grants("OPERATIONS_MANAGER")
    assert manager.get("operations.inventory.data.export") == "ORG"
    # Dangerous: one request creates thousands of records, so only the admin holds it by default.
    assert "operations.inventory.data.import" not in manager
    for role in ("OPERATIONS_WORKER", "OPERATIONS_USER"):
        assert not set(PHASE_9_PERMISSIONS) & set(_grants(role)), role


def test_only_the_admin_may_change_the_fulfilment_policy() -> None:
    code = "operations.fulfilment.settings.write"
    spec = next(s for s in OPERATIONS.specs if s.code(OPERATIONS.product) == code)
    assert spec.is_dangerous and spec.default_scope == spec.max_scope == "ORG"
    holders = {t.code for t in OPERATIONS_ROLE_TEMPLATES if code in _grants(t.code)}
    assert holders == {"OPERATIONS_ADMIN"}
