"""The permission catalogue is the product's vocabulary, and the guard that keeps an admin
role built from *this* product only. A wrong-product grant must be a red build, not a
super-grant found during an audit.
"""

from __future__ import annotations

import pytest

from app.catalogue import (
    CRM_BOOK,
    CRM_BOOK_REGISTRATION,
    CRM_BOOK_ROLE_TEMPLATES,
    Grant,
    RoleTemplate,
    validate,
)


def test_every_template_belongs_to_the_crm_product() -> None:
    for template in CRM_BOOK_ROLE_TEMPLATES:
        assert template.product == "crm"
        for grant in template.grants:
            assert grant.code.startswith("crm.")


def test_admin_is_built_from_this_services_catalogue_alone() -> None:
    admin = next(t for t in CRM_BOOK_ROLE_TEMPLATES if t.code == "CRM_BOOK_ADMIN")
    assert {g.code for g in admin.grants} == set(CRM_BOOK.codes())
    assert all(g.scope == "ORG" for g in admin.grants)


def test_there_is_exactly_one_default_role() -> None:
    defaults = [t for t in CRM_BOOK_ROLE_TEMPLATES if t.is_default]
    assert [t.code for t in defaults] == ["CRM_SALES_REP"]


def test_a_foreign_product_grant_is_refused_at_validation() -> None:
    rogue = RoleTemplate(
        code="ROGUE",
        name="Rogue",
        product="crm",
        grants=[Grant("hrms.profile.employee.read", "ORG")],
    )
    with pytest.raises(ValueError, match="belongs to product"):
        validate([rogue])


def test_the_registration_validates_at_construction() -> None:
    assert CRM_BOOK_REGISTRATION.product == "crm"
    assert CRM_BOOK_REGISTRATION.catalogue is CRM_BOOK
