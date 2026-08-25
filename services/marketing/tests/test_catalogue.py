"""The Marketing catalogue is well-formed and cannot reach another product.

No database: the catalogue is code, and this is the guard that turns "the marketing admin holds
every other product's permission" — the defect this structure exists to prevent — into a red
build.
"""

from __future__ import annotations

import pytest

from app.catalogue import (
    MARKETING,
    MARKETING_ROLE_TEMPLATES,
    Grant,
    PermissionCatalogue,
    PermissionSpec,
    RoleTemplate,
    validate,
)


def test_every_permission_code_is_under_the_marketing_product() -> None:
    assert set(MARKETING.codes()) == {
        "marketing.analytics.campaign.read",
        "marketing.analytics.campaign.write",
    }


def test_the_role_templates_grant_only_their_expected_scopes() -> None:
    by_code = {t.code: t for t in MARKETING_ROLE_TEMPLATES}

    admin = {(g.code, g.scope) for g in by_code["MARKETING_ADMIN"].grants}
    assert admin == {
        ("marketing.analytics.campaign.read", "ORG"),
        ("marketing.analytics.campaign.write", "ORG"),
    }

    analyst = {(g.code, g.scope) for g in by_code["MARKETING_ANALYST"].grants}
    assert analyst == {("marketing.analytics.campaign.read", "ORG")}


def test_exactly_one_template_is_the_default() -> None:
    defaults = [t.code for t in MARKETING_ROLE_TEMPLATES if t.is_default]
    assert defaults == ["MARKETING_ANALYST"]


def test_reading_defaults_to_org_because_the_data_is_org_wide() -> None:
    """Unlike HRMS, there is no 'own campaign' reading — an analyst sees the whole org."""
    by_code = {s.code(MARKETING.product): s for s in MARKETING.specs}
    assert by_code["marketing.analytics.campaign.read"].default_scope == "ORG"


def test_a_template_reaching_into_another_product_is_refused() -> None:
    """The guard that makes a wrong-product grant a failing build rather than a silent
    super-grant discovered during an audit."""
    foreign = RoleTemplate(
        code="LEAK",
        name="Leak",
        product="marketing",
        grants=[Grant("hrms.people.employee.read", "ORG")],
    )
    with pytest.raises(ValueError, match="belongs to product 'marketing' but grants"):
        validate([foreign])


def test_a_deliberately_cross_product_template_is_allowed() -> None:
    catalogue = PermissionCatalogue(
        "marketing", [PermissionSpec("analytics", "campaign", "read", "x", "ORG", "ORG")]
    )
    assert catalogue.codes() == ["marketing.analytics.campaign.read"]
    crossing = RoleTemplate(
        code="OWNER",
        name="Owner",
        product="marketing",
        grants=[Grant("hrms.people.employee.read", "ORG")],
        cross_product=True,
    )
    validate([crossing])  # does not raise
