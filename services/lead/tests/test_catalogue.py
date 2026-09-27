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


REVEAL = "crm.lead.lead.read_sensitive"


def test_the_reveal_permission_is_dangerous_own_by_default_and_org_at_most() -> None:
    spec = next(s for s in CRM_LEADS.specs if s.action == "read_sensitive")
    assert spec.code("crm") == REVEAL
    assert spec.description == "Reveal a lead's real phone number or email, audited"
    assert spec.is_dangerous is True
    assert spec.default_scope == "OWN"
    assert spec.max_scope == "ORG"


def test_the_admin_holds_the_reveal_at_org() -> None:
    admin = next(t for t in CRM_LEADS_ROLE_TEMPLATES if t.code == "CRM_LEADS_ADMIN")
    assert Grant(REVEAL, "ORG") in admin.grants


def test_the_rep_may_reveal_only_the_leads_they_own() -> None:
    """The owner-approved default: reps reveal their own records, never the whole intake. A
    rep's read stays ORG (sales works from one shared book) but the reveal is OWN — widening
    it here would hand every seat the intake's phone book."""
    rep = next(t for t in CRM_LEADS_ROLE_TEMPLATES if t.code == "CRM_LEAD_REP")
    assert {(g.code, g.scope) for g in rep.grants} == {
        ("crm.lead.lead.read", "ORG"),
        ("crm.lead.lead.write", "OWN"),
        ("crm.lead.lead.convert", "OWN"),
        (REVEAL, "OWN"),
    }


def test_the_reveal_is_the_only_dangerous_permission_a_rep_holds() -> None:
    dangerous = {s.code("crm") for s in CRM_LEADS.specs if s.is_dangerous}
    rep = next(t for t in CRM_LEADS_ROLE_TEMPLATES if t.code == "CRM_LEAD_REP")
    assert {g.code for g in rep.grants} & dangerous == {REVEAL}


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
