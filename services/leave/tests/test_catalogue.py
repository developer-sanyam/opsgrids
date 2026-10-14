"""The HRMS leave catalogue is well-formed and cannot reach another product.

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


def test_every_permission_code_is_under_the_hrms_time_module() -> None:
    assert set(HRMS.codes()) == {
        "hrms.time.leave_type.read",
        "hrms.time.leave_type.write",
        "hrms.time.leave_balance.read",
        "hrms.time.leave_balance.write",
        "hrms.time.leave_request.write",
        "hrms.time.leave_request.read",
        "hrms.time.leave_request.manage",
    }


def test_leave_types_default_to_org_and_balances_and_requests_have_an_own_reading() -> None:
    by_code = {s.code(HRMS.product): s for s in HRMS.specs}
    assert by_code["hrms.time.leave_type.read"].default_scope == "ORG"
    assert by_code["hrms.time.leave_type.write"].default_scope == "ORG"
    assert by_code["hrms.time.leave_balance.read"].default_scope == "OWN"
    assert by_code["hrms.time.leave_request.read"].default_scope == "OWN"
    assert by_code["hrms.time.leave_request.write"].default_scope == "OWN"


def test_the_self_service_write_permission_tops_out_at_own() -> None:
    """There is no meaningful "apply for leave on someone else's behalf", so the ceiling is
    OWN, not ORG — mirrors `hrms.time.attendance.request`'s identical reasoning."""
    by_code = {s.code(HRMS.product): s for s in HRMS.specs}
    spec = by_code["hrms.time.leave_request.write"]
    assert spec.default_scope == "OWN"
    assert spec.max_scope == "OWN"


def test_the_manage_override_never_defaults_below_org() -> None:
    by_code = {s.code(HRMS.product): s for s in HRMS.specs}
    spec = by_code["hrms.time.leave_request.manage"]
    assert spec.default_scope == "ORG"
    assert spec.max_scope == "ORG"


def test_the_role_templates_grant_only_their_expected_scopes() -> None:
    by_code = {t.code: t for t in HRMS_ROLE_TEMPLATES}

    hr_admin = {(g.code, g.scope) for g in by_code["HR_ADMIN"].grants}
    assert hr_admin == {
        ("hrms.time.leave_type.read", "ORG"),
        ("hrms.time.leave_type.write", "ORG"),
        ("hrms.time.leave_balance.read", "ORG"),
        ("hrms.time.leave_balance.write", "ORG"),
        ("hrms.time.leave_request.write", "OWN"),
        ("hrms.time.leave_request.read", "ORG"),
        ("hrms.time.leave_request.manage", "ORG"),
    }

    employee = {(g.code, g.scope) for g in by_code["EMPLOYEE"].grants}
    assert employee == {
        ("hrms.time.leave_type.read", "ORG"),
        ("hrms.time.leave_balance.read", "OWN"),
        ("hrms.time.leave_request.write", "OWN"),
        ("hrms.time.leave_request.read", "OWN"),
    }


def test_exactly_one_template_is_the_default() -> None:
    defaults = [t.code for t in HRMS_ROLE_TEMPLATES if t.is_default]
    assert defaults == ["EMPLOYEE"]


def test_a_template_reaching_into_another_product_is_refused() -> None:
    foreign = RoleTemplate(
        code="LEAK",
        name="Leak",
        product="hrms",
        grants=[Grant("crm.pipeline.deal.read", "ORG")],
    )
    with pytest.raises(ValueError, match="belongs to product 'hrms' but grants"):
        validate([foreign])


def test_a_deliberately_cross_product_template_is_allowed() -> None:
    catalogue = PermissionCatalogue("hrms", [PermissionSpec("time", "leave_type", "read", "x")])
    assert catalogue.codes() == ["hrms.time.leave_type.read"]
    crossing = RoleTemplate(
        code="OWNER",
        name="Owner",
        product="hrms",
        grants=[Grant("crm.pipeline.deal.read", "ORG")],
        cross_product=True,
    )
    validate([crossing])  # does not raise
