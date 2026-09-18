"""The permission catalogue's guard.

The rule being protected is the one from CLAUDE.md: a product's admin role is built from *that
product's own* catalogue, never "every permission that exists". These assertions make the
wrong-product grant a red build rather than an audit finding.
"""

from __future__ import annotations

import pytest

from app.catalogue import (
    NOTIFICATION,
    NOTIFICATION_REGISTRATION,
    NOTIFICATION_ROLE_TEMPLATES,
    Grant,
    RoleTemplate,
    validate,
)


def test_every_code_is_namespaced_to_this_product() -> None:
    assert all(code.startswith("notification.") for code in NOTIFICATION.codes())


def test_the_admin_role_is_built_from_this_catalogue_alone() -> None:
    admin = next(t for t in NOTIFICATION_ROLE_TEMPLATES if t.code == "NOTIFICATION_ADMIN")
    assert {g.code for g in admin.grants} == set(NOTIFICATION.codes())


def test_a_foreign_product_grant_is_refused() -> None:
    """The guard itself. Without it, `all()` on the wrong catalogue is a silent super-grant."""
    rogue = RoleTemplate(
        code="ROGUE",
        name="Rogue",
        product="notification",
        grants=[Grant("finance.billing.invoice.write", "ORG")],
    )
    with pytest.raises(ValueError, match="belongs to product"):
        validate([rogue])


def test_a_cross_product_template_may_opt_in_deliberately() -> None:
    allowed = RoleTemplate(
        code="BRIDGE",
        name="Bridge",
        product="notification",
        grants=[Grant("finance.billing.invoice.write", "ORG")],
        cross_product=True,
    )
    validate([allowed])  # does not raise


def test_pick_refuses_a_typo() -> None:
    with pytest.raises(ValueError, match="not in the notification catalogue"):
        NOTIFICATION.pick(("notification.delivery.template.raed", "ORG"))


def test_reading_a_delivered_body_is_marked_dangerous() -> None:
    """Reading what was sent means reading the invoice total or payslip line that went out, so
    it is classified alongside the other unmasking permissions rather than as ordinary reading."""
    spec = next(s for s in NOTIFICATION.specs if s.resource == "message" and s.action == "read")
    assert spec.is_dangerous


def test_the_author_role_cannot_read_message_bodies() -> None:
    """Authoring an automation is not the same job as reading a customer's payslip email."""
    author = next(t for t in NOTIFICATION_ROLE_TEMPLATES if t.code == "NOTIFICATION_AUTHOR")
    assert "notification.delivery.message.read" not in {g.code for g in author.grants}


def test_exactly_one_role_is_the_default() -> None:
    assert sum(1 for t in NOTIFICATION_ROLE_TEMPLATES if t.is_default) == 1


def test_the_registration_validates_itself() -> None:
    assert NOTIFICATION_REGISTRATION.product == "notification"
    assert NOTIFICATION_REGISTRATION.role_templates
