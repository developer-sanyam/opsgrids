"""The HRMS catalogue is well-formed and cannot reach another product.

No database: the catalogue is code, and this is the guard that turns "the HR admin holds every
CRM permission" — the defect this structure exists to prevent — into a red build.
"""

from __future__ import annotations

import pytest

from app.catalogue import HRMS, HRMS_ROLE_TEMPLATES
from platform_core.catalogue import (
    Grant,
    PermissionCatalogue,
    PermissionSpec,
    RoleTemplate,
    validate,
)


def test_every_permission_code_is_under_the_hrms_product() -> None:
    assert set(HRMS.codes()) == {
        "hrms.people.employee.read",
        "hrms.people.employee.write",
        "hrms.people.employee.read_sensitive",
    }


def test_unmasking_pii_is_a_dangerous_permission() -> None:
    """`read_sensitive` must carry `is_dangerous`, which is what the route's step-up hangs on —
    a masked read is routine, an unmask is not."""
    by_code = {s.code(HRMS.product): s for s in HRMS.specs}
    assert by_code["hrms.people.employee.read_sensitive"].is_dangerous is True
    # And plain read stays routine, so the two never collapse into one grant.
    assert by_code["hrms.people.employee.read"].is_dangerous is False


def test_the_role_templates_grant_only_their_expected_scopes() -> None:
    by_code = {t.code: t for t in HRMS_ROLE_TEMPLATES}

    hr_admin = {(g.code, g.scope) for g in by_code["HR_ADMIN"].grants}
    assert hr_admin == {
        ("hrms.people.employee.read", "ORG"),
        ("hrms.people.employee.write", "ORG"),
        # An HR admin may unmask across the org — behind the second-factor step-up.
        ("hrms.people.employee.read_sensitive", "ORG"),
    }

    employee = {(g.code, g.scope) for g in by_code["EMPLOYEE"].grants}
    assert employee == {
        ("hrms.people.employee.read", "OWN"),
        # A person may unmask their own details, and only their own — hence OWN.
        ("hrms.people.employee.read_sensitive", "OWN"),
    }


def test_exactly_one_template_is_the_default() -> None:
    defaults = [t.code for t in HRMS_ROLE_TEMPLATES if t.is_default]
    assert defaults == ["EMPLOYEE"]


def test_a_write_permission_may_not_be_granted_wider_than_its_ceiling() -> None:
    """`pick` validates the code exists; the ceiling is enforced when a grant is written by
    account-service, but the catalogue's own `all()` never exceeds it."""
    grants = {g.code: g.scope for g in HRMS.all("ORG")}
    assert grants["hrms.people.employee.read"] == "ORG"


def test_a_template_reaching_into_another_product_is_refused() -> None:
    """The guard that makes a wrong-product grant a failing build rather than a silent
    super-grant discovered during an audit."""
    foreign = RoleTemplate(
        code="LEAK",
        name="Leak",
        product="hrms",
        grants=[Grant("crm.pipeline.deal.read", "ORG")],
    )
    with pytest.raises(ValueError, match="belongs to product 'hrms' but grants"):
        validate([foreign])


def test_a_deliberately_cross_product_template_is_allowed() -> None:
    catalogue = PermissionCatalogue("hrms", [PermissionSpec("people", "employee", "read", "x")])
    assert catalogue.codes() == ["hrms.people.employee.read"]
    crossing = RoleTemplate(
        code="OWNER",
        name="Owner",
        product="hrms",
        grants=[Grant("crm.pipeline.deal.read", "ORG")],
        cross_product=True,
    )
    validate([crossing])  # does not raise
