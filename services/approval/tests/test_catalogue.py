"""Approval's `core` slice is well-formed and cannot reach another product.

No database: the catalogue is code, and this is the guard that turns "a role quietly holds a
foreign product's permission" — the defect this structure exists to prevent — into a red build.
"""

from __future__ import annotations

from app.catalogue import APPROVAL, APPROVAL_ROLE_TEMPLATES
from platform_core.catalogue import (
    Grant,
    PermissionCatalogue,
    PermissionSpec,
    RoleTemplate,
    validate,
)


def test_every_permission_code_is_under_the_core_approval_module() -> None:
    assert set(APPROVAL.codes()) == {
        "core.approval.request.read",
        "core.approval.request.decide",
        "core.approval.delegation.write",
        "core.approval.policy.write",
    }


def test_policy_write_has_no_own_reading() -> None:
    """SLA policy is organization-wide configuration, never one person's (ADR-0006 §11)."""
    by_code = {s.code(APPROVAL.product): s for s in APPROVAL.specs}
    spec = by_code["core.approval.policy.write"]
    assert spec.default_scope == "ORG"
    assert spec.max_scope == "ORG"


def test_member_holds_only_own_scope_grants() -> None:
    member = next(t for t in APPROVAL_ROLE_TEMPLATES if t.code == "MEMBER")
    assert member.is_default
    assert {g.scope for g in member.grants} == {"OWN"}
    assert {g.code for g in member.grants} == {
        "core.approval.request.read",
        "core.approval.request.decide",
        "core.approval.delegation.write",
    }


def test_approval_admin_holds_every_grant_at_org_and_nothing_else() -> None:
    admin = next(t for t in APPROVAL_ROLE_TEMPLATES if t.code == "APPROVAL_ADMIN")
    assert {g.code for g in admin.grants} == set(APPROVAL.codes())
    assert {g.scope for g in admin.grants} == {"ORG"}


def test_org_owner_and_org_admin_also_hold_every_grant_at_org() -> None:
    """This is what guarantees every organization ends ADR-0007's reconcile with at least one
    holder of `decide:ORG`, so the unassigned queue always has an owner (ADR-0006 §11)."""
    for code in ("ORG_ADMIN", "ORG_OWNER"):
        template = next(t for t in APPROVAL_ROLE_TEMPLATES if t.code == code)
        assert {g.code for g in template.grants} == set(APPROVAL.codes())
        assert {g.scope for g in template.grants} == {"ORG"}


def test_validate_refuses_a_foreign_product_grant() -> None:
    foreign = RoleTemplate(
        code="ROGUE",
        name="Rogue",
        product="core",
        grants=[Grant("hrms.time.attendance.write", "ORG")],
    )
    try:
        validate([foreign])
    except ValueError as exc:
        assert "ROGUE" in str(exc)
    else:
        raise AssertionError("expected validate() to refuse a foreign-product grant")


def test_pick_refuses_an_unknown_code() -> None:
    catalogue = PermissionCatalogue(
        "core", [PermissionSpec("approval", "request", "read", "read", "OWN", "ORG")]
    )
    try:
        catalogue.pick(("approval.request.decide", "ORG"))
    except ValueError:
        pass
    else:
        raise AssertionError("expected pick() to refuse an unknown permission code")
