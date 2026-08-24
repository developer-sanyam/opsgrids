"""The permission catalogue and its cross-product guard."""

from __future__ import annotations

import pytest

from app.catalogue import (
    PRODUCT,
    SUPPORT,
    SUPPORT_ROLE_TEMPLATES,
    Grant,
    RoleTemplate,
    validate,
)


def test_every_code_is_namespaced_to_support() -> None:
    assert all(code.startswith(f"{PRODUCT}.") for code in SUPPORT.codes())


def test_admin_is_built_from_the_support_catalogue_alone() -> None:
    admin = next(t for t in SUPPORT_ROLE_TEMPLATES if t.code == "SUPPORT_ADMIN")
    known = set(SUPPORT.codes())
    assert {g.code for g in admin.grants} <= known


def test_viewer_holds_read_but_not_write() -> None:
    viewer = next(t for t in SUPPORT_ROLE_TEMPLATES if t.code == "SUPPORT_VIEWER")
    codes = {g.code for g in viewer.grants}
    assert "support.desk.ticket.read" in codes
    assert "support.desk.ticket.write" not in codes


def test_guard_rejects_a_foreign_product_grant() -> None:
    rogue = RoleTemplate(
        code="ROGUE",
        name="Rogue",
        product=PRODUCT,
        grants=[Grant("crm.pipeline.deal.read", "ORG")],
    )
    with pytest.raises(ValueError, match="belongs to product"):
        validate([rogue])


def test_pick_rejects_a_typo() -> None:
    with pytest.raises(ValueError, match="not in the support catalogue"):
        SUPPORT.pick(("support.desk.ticket.reed", "ORG"))
