"""The permission catalogue is the product's vocabulary, and the guard that keeps an admin role
built from *this* product only. A wrong-product grant must be a red build, not a super-grant found
during an audit.
"""

from __future__ import annotations

import pytest

from app.catalogue import (
    PULSEBOARD,
    PULSEBOARD_REGISTRATION,
    PULSEBOARD_ROLE_TEMPLATES,
    Grant,
    RoleTemplate,
    validate,
)


def test_every_template_belongs_to_the_pulseboard_product() -> None:
    for template in PULSEBOARD_ROLE_TEMPLATES:
        assert template.product == "pulseboard"
        for grant in template.grants:
            assert grant.code.startswith("pulseboard.")


def test_admin_is_built_from_the_pulseboard_catalogue_alone() -> None:
    admin = next(t for t in PULSEBOARD_ROLE_TEMPLATES if t.code == "PULSEBOARD_ADMIN")
    assert {g.code for g in admin.grants} == set(PULSEBOARD.codes())
    assert all(g.scope == "ORG" for g in admin.grants)


def test_there_is_exactly_one_default_role() -> None:
    defaults = [t for t in PULSEBOARD_ROLE_TEMPLATES if t.is_default]
    assert [t.code for t in defaults] == ["PULSEBOARD_MEMBER"]


def test_a_foreign_product_grant_is_refused_at_validation() -> None:
    rogue = RoleTemplate(
        code="ROGUE",
        name="Rogue",
        product="pulseboard",
        grants=[Grant("crm.pipeline.deal.read", "ORG")],
    )
    with pytest.raises(ValueError, match="belongs to product"):
        validate([rogue])


def test_the_registration_validates_at_construction() -> None:
    assert PULSEBOARD_REGISTRATION.product == "pulseboard"
    assert PULSEBOARD_REGISTRATION.catalogue is PULSEBOARD
