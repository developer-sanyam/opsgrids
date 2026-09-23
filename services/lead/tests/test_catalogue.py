"""The permission catalogue is the product's vocabulary, and the guard that keeps an admin role
built from *this* service's own slice of `crm` only. A wrong-product grant must be a red build,
not a super-grant found during an audit.
"""

from __future__ import annotations

import pytest

from app.catalogue import (
    CRM_LEADS,
    CRM_LEADS_REGISTRATION,
    CRM_LEADS_ROLE_TEMPLATES,
    Grant,
    RoleTemplate,
    validate,
)


def test_every_template_belongs_to_the_crm_product() -> None:
    for template in CRM_LEADS_ROLE_TEMPLATES:
        assert template.product == "crm"
        for grant in template.grants:
            assert grant.code.startswith("crm.")


def test_admin_is_built_from_this_catalogue_alone() -> None:
    admin = next(t for t in CRM_LEADS_ROLE_TEMPLATES if t.code == "CRM_LEADS_ADMIN")
    assert {g.code for g in admin.grants} == set(CRM_LEADS.codes())
    assert all(g.scope == "ORG" for g in admin.grants)


def test_there_is_exactly_one_default_role() -> None:
    defaults = [t for t in CRM_LEADS_ROLE_TEMPLATES if t.is_default]
    assert [t.code for t in defaults] == ["CRM_LEAD_REP"]


def test_a_foreign_product_grant_is_refused_at_validation() -> None:
    rogue = RoleTemplate(
        code="ROGUE",
        name="Rogue",
        product="crm",
        grants=[Grant("hrms.employee.read", "ORG")],
    )
    with pytest.raises(ValueError, match="belongs to product"):
        validate([rogue])


def test_the_registration_validates_at_construction() -> None:
    assert CRM_LEADS_REGISTRATION.product == "crm"
    assert CRM_LEADS_REGISTRATION.catalogue is CRM_LEADS
