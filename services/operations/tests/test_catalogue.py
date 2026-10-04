"""The permission catalogue is the product's vocabulary, and the guard that keeps an admin role
built from *this* product only. A wrong-product grant must be a red build, not a super-grant found
during an audit.
"""

from __future__ import annotations

import pytest

from app.catalogue import (
    OPERATIONS,
    OPERATIONS_REGISTRATION,
    OPERATIONS_ROLE_TEMPLATES,
    Grant,
    RoleTemplate,
    validate,
)


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
