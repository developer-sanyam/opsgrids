"""The finance permission catalogue and its guard.

A product's admin role is built from that product's own catalogue, never "all permissions". The
guard that makes the wrong thing a red build rather than an audit finding is exercised here.
"""

from __future__ import annotations

import pytest

from app.catalogue import (
    FINANCE,
    FINANCE_REGISTRATION,
    FINANCE_ROLE_TEMPLATES,
    Grant,
    RoleTemplate,
    validate,
)


def test_every_code_is_namespaced_to_finance() -> None:
    assert all(code.startswith("finance.") for code in FINANCE.codes())


def test_admin_is_built_from_the_finance_catalogue_alone() -> None:
    admin = next(t for t in FINANCE_ROLE_TEMPLATES if t.code == "FINANCE_ADMIN")
    assert {g.code for g in admin.grants} == set(FINANCE.codes())
    assert all(g.scope == "ORG" for g in admin.grants)


def test_default_role_is_the_viewer() -> None:
    defaults = [t for t in FINANCE_ROLE_TEMPLATES if t.is_default]
    assert [t.code for t in defaults] == ["FINANCE_VIEWER"]


def test_registration_validates_at_construction() -> None:
    assert FINANCE_REGISTRATION.product == "finance"


def test_a_foreign_product_grant_is_refused() -> None:
    rogue = RoleTemplate(
        code="ROGUE",
        name="Rogue",
        product="finance",
        grants=[Grant("crm.pipeline.deal.read", "ORG")],
    )
    with pytest.raises(ValueError, match="belongs to product"):
        validate([rogue])
