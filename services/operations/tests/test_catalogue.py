"""The permission catalogue is the product's vocabulary, and the guard that keeps an admin role
built from *this* product only. A wrong-product grant must be a red build, not a super-grant found
during an audit.
"""

from __future__ import annotations

import pytest

from app.catalogue import (
    OPERATIONS,
    OPERATIONS_REGISTRATION,
    OPERATIONS_ROLE_TEMPLATES,
    Grant,
    RoleTemplate,
    validate,
)


def test_every_template_belongs_to_the_operations_product() -> None:
    for template in OPERATIONS_ROLE_TEMPLATES:
        assert template.product == "operations"
        for grant in template.grants:
            assert grant.code.startswith("operations.")


def test_admin_is_built_from_the_operations_catalogue_alone() -> None:
    admin = next(t for t in OPERATIONS_ROLE_TEMPLATES if t.code == "OPERATIONS_ADMIN")
    assert {g.code for g in admin.grants} == set(OPERATIONS.codes())
    # Every grant tops out at ORG *unless* the permission's own `max_scope` is narrower —
    # `fulfilment.task.execute`'s ceiling is `OWN` (ADR-0025 §2/§3, plan F3): acting on another
    # worker's own offered/accepted task is `task.override`'s job, never a wider blanket grant,
    # so even `OPERATIONS_ADMIN`'s `all("ORG")` is clamped down to `OWN` here by `min_scope`,
    # deliberately (CLAUDE.md: holding a permission is not holding enough scope).
    for grant in admin.grants:
        if grant.code == "operations.fulfilment.task.execute":
            assert grant.scope == "OWN"
        else:
            assert grant.scope == "ORG"


def test_there_is_exactly_one_default_role() -> None:
    defaults = [t for t in OPERATIONS_ROLE_TEMPLATES if t.is_default]
    assert [t.code for t in defaults] == ["OPERATIONS_USER"]


def test_a_foreign_product_grant_is_refused_at_validation() -> None:
    rogue = RoleTemplate(
        code="ROGUE",
        name="Rogue",
        product="operations",
        grants=[Grant("crm.pipeline.deal.read", "ORG")],
    )
    with pytest.raises(ValueError, match="belongs to product"):
        validate([rogue])


def test_the_registration_validates_at_construction() -> None:
    assert OPERATIONS_REGISTRATION.product == "operations"
    assert OPERATIONS_REGISTRATION.catalogue is OPERATIONS
