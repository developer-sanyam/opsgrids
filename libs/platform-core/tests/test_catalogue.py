"""The shared permission-catalogue machinery.

The guard these tests defend is "a product's admin role is built from that product's own
catalogue, never all permissions" (CLAUDE.md): a role template reaching into a foreign product
is a red build, not a super-grant found in an audit.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from platform_core.catalogue import (
    Grant,
    PermissionCatalogue,
    PermissionSpec,
    ProductRegistration,
    RoleTemplate,
    min_scope,
    validate,
)


def _catalogue(product: str = "crm") -> PermissionCatalogue:
    return PermissionCatalogue(
        product,
        [
            PermissionSpec("lead", "lead", "read", "See leads"),
            PermissionSpec("lead", "lead", "delete", "Delete leads", max_scope="TEAM"),
        ],
    )


class TestPermissionSpec:
    def test_code_is_product_module_resource_action(self) -> None:
        spec = PermissionSpec("lead", "lead", "read", "See leads")
        assert spec.code("crm") == "crm.lead.lead.read"

    def test_default_scope_is_own(self) -> None:
        assert PermissionSpec("m", "r", "a", "d").default_scope == "OWN"

    def test_a_service_can_redeclare_only_the_default_scope(self) -> None:
        @dataclass(frozen=True, slots=True)
        class OrgSpec(PermissionSpec):
            default_scope: str = "ORG"

        spec = OrgSpec("billing", "invoice", "read", "See invoices")
        assert spec.default_scope == "ORG"
        assert spec.max_scope == "ORG"
        assert spec.code("finance") == "finance.billing.invoice.read"
        # Positional order is unchanged: the fifth argument is still the default scope.
        assert OrgSpec("m", "r", "a", "d", "TEAM").default_scope == "TEAM"


class TestMinScope:
    def test_never_wider_than_the_ceiling(self) -> None:
        assert min_scope("ORG", "TEAM") == "TEAM"
        assert min_scope("OWN", "ORG") == "OWN"
        assert min_scope("LOCATION", "LOCATION") == "LOCATION"


class TestCatalogue:
    def test_all_is_bound_to_this_catalogue_and_capped_at_each_ceiling(self) -> None:
        grants = _catalogue().all()
        assert grants == [Grant("crm.lead.lead.read", "ORG"), Grant("crm.lead.lead.delete", "TEAM")]

    def test_pick_accepts_short_and_full_codes(self) -> None:
        picked = _catalogue().pick(("lead.lead.read", "OWN"), ("crm.lead.lead.delete", "TEAM"))
        assert [g.code for g in picked] == ["crm.lead.lead.read", "crm.lead.lead.delete"]

    def test_pick_refuses_a_code_outside_the_catalogue(self) -> None:
        with pytest.raises(ValueError, match="is not in the crm catalogue"):
            _catalogue().pick(("lead.lead.nope", "OWN"))
        with pytest.raises(ValueError, match="is not in the crm catalogue"):
            _catalogue().pick(("hrms.leave.request.read", "OWN"))


class TestValidate:
    def test_a_template_inside_its_own_product_passes(self) -> None:
        template = RoleTemplate("ADMIN", "Admin", "crm", _catalogue().all())
        validate([template])

    def test_a_foreign_grant_is_refused(self) -> None:
        template = RoleTemplate(
            "ADMIN", "Admin", "crm", [*_catalogue().all(), Grant("hrms.leave.request.read", "ORG")]
        )
        with pytest.raises(ValueError, match=r"hrms\.leave\.request\.read"):
            validate([template])

    def test_cross_product_is_the_only_way_through(self) -> None:
        template = RoleTemplate(
            "BRIDGE", "Bridge", "crm", [Grant("hrms.leave.request.read", "ORG")], cross_product=True
        )
        validate([template])

    def test_every_template_is_checked_not_just_the_first(self) -> None:
        good = RoleTemplate("A", "A", "crm", _catalogue().all())
        bad = RoleTemplate("B", "B", "crm", [Grant("finance.billing.invoice.read", "ORG")])
        with pytest.raises(ValueError, match="'B'"):
            validate([good, bad])


class TestProductRegistration:
    def test_registration_validates_its_templates_on_construction(self) -> None:
        bad = RoleTemplate("ADMIN", "Admin", "crm", [Grant("hrms.leave.request.read", "ORG")])
        with pytest.raises(ValueError, match="belongs to product 'crm'"):
            ProductRegistration(product="crm", catalogue=_catalogue(), role_templates=[bad])

    def test_a_clean_registration_keeps_its_parts(self) -> None:
        catalogue = _catalogue()
        admin = RoleTemplate("ADMIN", "Admin", "crm", catalogue.all())
        registration = ProductRegistration(
            product="crm", catalogue=catalogue, role_templates=[admin]
        )
        assert registration.catalogue is catalogue
        assert registration.role_templates == [admin]
