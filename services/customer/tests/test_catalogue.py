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


def test_the_rep_may_reveal_only_the_contacts_they_own() -> None:
    """The owner-approved default: reps reveal a contact's phone for their own records. Their
    reads stay ORG (sales works from one shared book) — the reveal is the one grant that must
    not follow them there, or every seat would hold the whole book's phone numbers."""
    rep = next(t for t in CRM_BOOK_ROLE_TEMPLATES if t.code == "CRM_SALES_REP")
    assert {(g.code, g.scope) for g in rep.grants} == {
        ("crm.book.company.read", "ORG"),
        ("crm.book.company.write", "OWN"),
        ("crm.book.contact.read", "ORG"),
        ("crm.book.contact.write", "OWN"),
        ("crm.book.contact.read_sensitive", "OWN"),
    }


def test_the_admin_still_holds_the_reveal_at_org() -> None:
    admin = next(t for t in CRM_BOOK_ROLE_TEMPLATES if t.code == "CRM_BOOK_ADMIN")
    assert Grant("crm.book.contact.read_sensitive", "ORG") in admin.grants


def test_the_reveal_is_the_only_dangerous_permission_a_rep_holds() -> None:
    dangerous = {s.code("crm") for s in CRM_BOOK.specs if s.is_dangerous}
    rep = next(t for t in CRM_BOOK_ROLE_TEMPLATES if t.code == "CRM_SALES_REP")
    assert {g.code for g in rep.grants} & dangerous == {"crm.book.contact.read_sensitive"}


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
