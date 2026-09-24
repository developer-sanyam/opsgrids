"""The HRMS time catalogue is well-formed and cannot reach another product.

No database: the catalogue is code, and this is the guard that turns "the HR admin holds every
CRM permission" — the defect this structure exists to prevent — into a red build.
"""

from __future__ import annotations

import pytest

from app.catalogue import (
    HRMS,
    HRMS_ROLE_TEMPLATES,
    Grant,
    PermissionCatalogue,
    PermissionSpec,
    RoleTemplate,
    validate,
)


def test_every_permission_code_is_under_the_hrms_time_module() -> None:
    assert set(HRMS.codes()) == {
        "hrms.time.attendance.read",
        "hrms.time.attendance.write",
        "hrms.time.attendance.request",
        # R22: the anti-spoofing flags, and the unmask of the address and device id behind one.
        "hrms.time.attendance.read_sensitive",
        "hrms.time.attendance_flag.read",
        "hrms.time.shift.read",
        "hrms.time.shift.write",
    }


def test_attendance_permissions_default_to_own_and_shifts_to_org() -> None:
    """A punch has an "own" reading; a shift is an org definition with none."""
    by_code = {s.code(HRMS.product): s for s in HRMS.specs}
    assert by_code["hrms.time.attendance.read"].default_scope == "OWN"
    assert by_code["hrms.time.attendance.write"].default_scope == "OWN"
    assert by_code["hrms.time.shift.read"].default_scope == "ORG"
    assert by_code["hrms.time.shift.write"].default_scope == "ORG"


def test_the_self_service_request_permission_tops_out_at_own() -> None:
    """ADR-0006 §11: there is no meaningful "request a correction for someone else", so the
    ceiling is OWN, not ORG — an ORG-wide request-for-others power is unrepresentable, not
    merely ungranted."""
    by_code = {s.code(HRMS.product): s for s in HRMS.specs}
    spec = by_code["hrms.time.attendance.request"]
    assert spec.default_scope == "OWN"
    assert spec.max_scope == "OWN"


def test_the_role_templates_grant_only_their_expected_scopes() -> None:
    by_code = {t.code: t for t in HRMS_ROLE_TEMPLATES}

    hr_admin = {(g.code, g.scope) for g in by_code["HR_ADMIN"].grants}
    assert hr_admin == {
        ("hrms.time.attendance.read", "ORG"),
        ("hrms.time.attendance.write", "ORG"),
        # An HR administrator is also an employee with their own attendance (ADR-0006 §11).
        ("hrms.time.attendance.request", "OWN"),
        # R22: HR reads every flag and is the one role that may unmask what is behind one.
        ("hrms.time.attendance_flag.read", "ORG"),
        ("hrms.time.attendance.read_sensitive", "ORG"),
        ("hrms.time.shift.read", "ORG"),
        ("hrms.time.shift.write", "ORG"),
    }

    employee = {(g.code, g.scope) for g in by_code["EMPLOYEE"].grants}
    assert employee == {
        # A person punches and reads their own attendance — hence OWN.
        ("hrms.time.attendance.read", "OWN"),
        ("hrms.time.attendance.write", "OWN"),
        # Everyone may ask for a correction to their own attendance.
        ("hrms.time.attendance.request", "OWN"),
        # Shifts are org definitions everyone needs to see.
        ("hrms.time.shift.read", "ORG"),
    }


def test_exactly_one_template_is_the_default() -> None:
    defaults = [t.code for t in HRMS_ROLE_TEMPLATES if t.is_default]
    assert defaults == ["EMPLOYEE"]


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
    catalogue = PermissionCatalogue("hrms", [PermissionSpec("time", "attendance", "read", "x")])
    assert catalogue.codes() == ["hrms.time.attendance.read"]
    crossing = RoleTemplate(
        code="OWNER",
        name="Owner",
        product="hrms",
        grants=[Grant("crm.pipeline.deal.read", "ORG")],
        cross_product=True,
    )
    validate([crossing])  # does not raise
