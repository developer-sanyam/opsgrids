"""The permission catalogue: four permissions, two role templates, one product.

The guard these assertions exist for is the one every catalogue in the platform states: in the
system this replaces, the admin role was "every permission that exists", so the moment a second
product registered its permissions the admin silently held them too.
"""

from __future__ import annotations

import pytest

from app.catalogue import (
    STOREFRONT,
    STOREFRONT_REGISTRATION,
    STOREFRONT_ROLE_TEMPLATES,
    Grant,
    PermissionCatalogue,
    PermissionSpec,
    RoleTemplate,
    validate,
)


def test_the_permissions_are_exactly_what_the_adrs_and_gaps_name() -> None:
    assert STOREFRONT.codes() == [
        "storefront.store.settings.read",
        "storefront.store.settings.write",
        "storefront.delivery.zone.read",
        "storefront.delivery.zone.write",
        "storefront.catalogue.product.read",
        "storefront.catalogue.product.write",
        "storefront.sales.enquiry.read",
        "storefront.sales.enquiry.write",
        "storefront.sales.enquiry.unmask",
        "storefront.sales.enquiry.delete",
    ]


def test_there_is_no_fifth_permission_for_the_demand_report() -> None:
    """Folded into `zone.read` (ADR-0010 §9). Nobody has asked for a read-the-demand-but-not-the-
    zones role, and a fifth permission is a fifth thing to deliver. The trade, plainly: anyone
    who can see the zones can see which postal codes were turned away."""
    assert len(STOREFRONT.specs) == 10  # the tenth is enquiry erasure, not a demand reader


@pytest.mark.parametrize("spec", STOREFRONT.specs, ids=lambda s: s.code("storefront"))
def test_every_permission_is_pinned_at_org_in_both_directions(spec: PermissionSpec) -> None:
    """`max_scope` is a ladder **ceiling**, not an allowed-set — account validates a grant with a
    single rank comparison, so a permission ceilinged at ORG leaves OWN, TEAM and LOCATION
    grantable whether or not this service has an opinion about them, and an undefined rung gets
    improvised per endpoint (ADR-0007 §1). Setting the *default* to ORG as well makes every lower
    rung unreachable rather than merely ungranted.

    A delivery zone has no owner, no team and no location reading."""
    assert spec.default_scope == "ORG"
    assert spec.max_scope == "ORG"


def test_nothing_is_flagged_dangerous() -> None:
    """`is_dangerous` has zero references in `libs/platform-core` and changes no authorization
    outcome (ADR-0000 §5's note). What gates these routes is the ORG floor and the audit row."""
    assert not any(spec.is_dangerous for spec in STOREFRONT.specs)


def test_the_two_role_templates_are_new_codes_on_a_new_product() -> None:
    """**This is why no ADR-0007 reconcile is needed**, and it is a consequence of a choice
    rather than luck. `install_registration` skips role codes that already exist; ADR-0009's two
    permissions went on `core`, whose `ORG_OWNER` and `ORG_ADMIN` already exist in every live
    tenant, so they could not reach one without a fleet-wide reconcile. Nothing here shares a
    code with an existing role."""
    codes = {t.code for t in STOREFRONT_ROLE_TEMPLATES}
    assert codes == {"STOREFRONT_ADMIN", "STOREFRONT_STAFF"}
    assert not codes & {"ORG_OWNER", "ORG_ADMIN", "MEMBER"}
    assert all(t.product == "storefront" for t in STOREFRONT_ROLE_TEMPLATES)


def test_the_admin_template_holds_the_whole_catalogue_and_nothing_else() -> None:
    admin = next(t for t in STOREFRONT_ROLE_TEMPLATES if t.code == "STOREFRONT_ADMIN")
    assert {g.code for g in admin.grants} == set(STOREFRONT.codes())


def test_the_staff_template_is_the_default_and_reads_only() -> None:
    staff = next(t for t in STOREFRONT_ROLE_TEMPLATES if t.code == "STOREFRONT_STAFF")
    assert staff.is_default
    assert all(g.code.endswith(".read") for g in staff.grants)


def test_no_template_declares_cross_product() -> None:
    assert not any(t.cross_product for t in STOREFRONT_ROLE_TEMPLATES)


def test_a_template_reaching_into_another_product_is_refused_at_import() -> None:
    """The guard, exercised — so a wrong-product grant is a red build rather than a super-grant
    discovered during an audit."""
    rogue = RoleTemplate(
        code="ROGUE",
        name="Rogue",
        product="storefront",
        grants=[Grant("crm.pipeline.deal.write", "ORG")],
    )
    with pytest.raises(ValueError, match=r"crm\.pipeline\.deal\.write"):
        validate([rogue])


def test_pick_refuses_a_code_that_is_not_in_this_catalogue() -> None:
    with pytest.raises(ValueError, match="not in the storefront catalogue"):
        STOREFRONT.pick(("storefront.delivery.node.write", "ORG"))


def test_all_never_grants_wider_than_a_permission_ceiling() -> None:
    narrow = PermissionCatalogue(
        "storefront", [PermissionSpec("m", "r", "read", "d", "OWN", "TEAM")]
    )
    assert narrow.all("ORG") == [Grant("storefront.m.r.read", "TEAM")]


def test_the_registration_is_what_the_exporter_reads() -> None:
    assert STOREFRONT_REGISTRATION.product == "storefront"
    assert STOREFRONT_REGISTRATION.catalogue is STOREFRONT
    assert STOREFRONT_REGISTRATION.role_templates is STOREFRONT_ROLE_TEMPLATES
