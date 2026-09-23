"""The permission catalogue's guard.

The rule being protected is the one from CLAUDE.md: a product's admin role is built from *that
product's own* catalogue, never "every permission that exists". These assertions make the
wrong-product grant a red build rather than an audit finding.
"""

from __future__ import annotations

import pytest

from app import catalogue as catalogue_module
from app.catalogue import (
    ENGAGE,
    ENGAGE_REGISTRATION,
    ENGAGE_ROLE_TEMPLATES,
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


# ------------------------------------------------------------------ the engage surface
#
# Engage is a second product served by this one service, so the guard above has a second thing
# to guard: nothing in `engage` may be granted by a `notification` role or the other way round.


def test_engage_catalogue_declares_setup_and_audience_permissions() -> None:
    assert set(ENGAGE.codes()) == {
        "engage.setup.connection.read",
        "engage.setup.connection.write",
        "engage.setup.domain.read",
        "engage.setup.domain.write",
        "engage.setup.sender.read",
        "engage.setup.sender.write",
        "engage.setup.whatsapp.read",
        "engage.setup.whatsapp.write",
        "engage.audience.contact.read",
        "engage.audience.contact.write",
        "engage.audience.suppression.read",
        "engage.audience.suppression.write",
        "engage.audience.list.read",
        "engage.audience.list.write",
        "engage.campaign.campaign.read",
        "engage.campaign.campaign.write",
        "engage.campaign.campaign.send",
    }


def test_sending_a_campaign_is_dangerous_and_not_the_viewers() -> None:
    """Drafting reaches nobody; sending reaches the whole audience. Only the second is flagged,
    and the default seat holds neither."""
    send = next(s for s in ENGAGE.specs if s.code("engage") == "engage.campaign.campaign.send")
    assert send.is_dangerous
    viewer = next(t for t in ENGAGE_ROLE_TEMPLATES if t.code == "ENGAGE_VIEWER")
    assert not any(g.code.endswith((".write", ".send")) for g in viewer.grants)


def test_engage_admin_gets_every_engage_permission() -> None:
    admin = next(t for t in ENGAGE_ROLE_TEMPLATES if t.code == "ENGAGE_ADMIN")
    assert {g.code for g in admin.grants} == set(ENGAGE.codes())


def test_the_two_catalogues_on_this_service_share_no_codes() -> None:
    """One service, two products. A code belongs to exactly one of them — the exporter refuses
    a code declared twice across services, and nothing would catch it declared twice here."""
    assert not set(ENGAGE.codes()) & set(NOTIFICATION.codes())


def test_an_engage_role_cannot_reach_the_notification_catalogue() -> None:
    """The two products are sold separately. Sharing a process must not mean sharing a grant."""
    rogue = RoleTemplate(
        code="ROGUE_ENGAGE",
        name="Rogue",
        product="engage",
        grants=[Grant("notification.delivery.message.read", "ORG")],
    )
    with pytest.raises(ValueError, match="belongs to product"):
        validate([rogue])


def test_the_engage_viewer_cannot_change_the_sending_setup() -> None:
    """A wrong provider credential silently stops every send, so seeing the setup and changing
    it are separate jobs rather than one "settings" permission."""
    viewer = next(t for t in ENGAGE_ROLE_TEMPLATES if t.code == "ENGAGE_VIEWER")
    assert not any(g.code.endswith(".write") for g in viewer.grants)


def test_the_default_engage_seat_is_the_viewer_not_the_administrator() -> None:
    """`is_default` is the template account-service grants every new seat on activation. The
    administrator holds `connection.write` — the credential the whole organization sends
    through — so handing it out by default would make every seat able to repoint the mail."""
    defaults = [t.code for t in ENGAGE_ROLE_TEMPLATES if t.is_default]
    assert defaults == ["ENGAGE_VIEWER"]


def test_both_products_are_exported() -> None:
    """`tools/export_permissions.py` discovers registrations by name — every module-level object
    whose name ends in `_REGISTRATION`. This service is the first to declare two, so the
    convention is pinned here: renaming one out of that shape would drop a whole product's
    manifest from `libs/contracts/permissions/` with nothing failing."""
    registrations = [
        obj for name, obj in vars(catalogue_module).items() if name.endswith("_REGISTRATION")
    ]
    assert {reg.product for reg in registrations} == {"notification", "engage"}
    assert ENGAGE_REGISTRATION.catalogue is ENGAGE
