"""The permission catalogue and the guard on role templates.

`test_a_role_cannot_grant_another_products_permission` is the important one. The system
this platform replaces defined its HR admin role as "every permission that exists"; under
a shared catalogue that silently grants every other product's permissions the moment they
register. This test is the reason that cannot happen here.
"""

from __future__ import annotations

import pytest

from app import catalogue
from app.catalogue import (
    CORE,
    CORE_ROLE_TEMPLATES,
    Grant,
    PermissionCatalogue,
    PermissionSpec,
    RoleTemplate,
    min_scope,
    validate,
)


class TestCatalogue:
    def test_codes_are_four_part_and_namespaced(self) -> None:
        for code in CORE.codes():
            product, module, resource, action = code.split(".")
            assert product == "core"
            assert module and resource and action

    def test_all_is_bound_to_its_own_product(self) -> None:
        # The safe form of "give the admin everything".
        assert {g.code.split(".")[0] for g in CORE.all("ORG")} == {"core"}

    def test_all_respects_each_permissions_ceiling(self) -> None:
        # profile.self.* is OWN-only; asking for ORG must not widen it.
        by_code = {g.code: g.scope for g in CORE.all("ORG")}
        assert by_code["core.profile.self.write"] == "OWN"
        assert by_code["core.org.unit.write"] == "ORG"

    def test_pick_rejects_a_typo(self) -> None:
        with pytest.raises(ValueError, match="not in the core catalogue"):
            CORE.pick(("org.unit.raed", "ORG"))

    def test_pick_accepts_bare_or_qualified_codes(self) -> None:
        bare = CORE.pick(("org.unit.read", "ORG"))
        qualified = CORE.pick(("core.org.unit.read", "ORG"))
        assert bare == qualified


class TestScopeCeiling:
    @pytest.mark.parametrize(
        ("wanted", "ceiling", "expected"),
        [
            ("ORG", "OWN", "OWN"),
            ("ORG", "ORG", "ORG"),
            ("OWN", "ORG", "OWN"),
            ("TEAM", "LOCATION", "TEAM"),
            ("LOCATION", "TEAM", "TEAM"),
        ],
    )
    def test_never_grants_wider_than_the_ceiling(
        self, wanted: str, ceiling: str, expected: str
    ) -> None:
        assert min_scope(wanted, ceiling) == expected


class TestRoleTemplateGuard:
    def test_shipped_templates_are_valid(self) -> None:
        validate(CORE_ROLE_TEMPLATES)

    def test_a_role_cannot_grant_another_products_permission(self) -> None:
        rogue = RoleTemplate(
            code="HR_ADMIN",
            name="HR administrator",
            product="hrms",
            grants=[Grant("crm.pipeline.deal.write", "ORG")],
        )
        with pytest.raises(ValueError, match="belongs to product 'hrms' but grants"):
            validate([rogue])

    def test_cross_product_must_be_declared_deliberately(self) -> None:
        # The escape hatch exists, and using it is visible in the source.
        allowed = RoleTemplate(
            code="ORG_OWNER",
            name="Owner",
            product="core",
            grants=[Grant("crm.pipeline.deal.read", "ORG")],
            cross_product=True,
        )
        validate([allowed])

    def test_every_default_role_is_narrow(self) -> None:
        # A role auto-assigned on joining must not carry a dangerous write.
        dangerous = {s.code("core") for s in CORE.specs if s.is_dangerous}
        for template in CORE_ROLE_TEMPLATES:
            if not template.is_default:
                continue
            held = {g.code for g in template.grants}
            assert not (held & dangerous), f"{template.code} auto-grants a dangerous permission"


class TestCatalogueIsolation:
    def test_two_catalogues_cannot_reach_each_other(self) -> None:
        crm = PermissionCatalogue(
            "crm", [PermissionSpec("pipeline", "deal", "read", "See deals", "OWN")]
        )
        assert {g.code.split(".")[0] for g in crm.all()} == {"crm"}
        with pytest.raises(ValueError):
            crm.pick(("org.unit.read", "ORG"))


class TestProductActivationIsOwnerOnly:
    """Activating a product spends money, so it is not an administrator's to do.

    `ORG_OWNER` describes itself as running billing and product activation; `ORG_ADMIN`
    describes itself as managing people and structure "but not billing". Those descriptions
    are load-bearing — a customer reading them decides who to make an administrator — so the
    grants have to match, and a drift between them is the kind that only surfaces as an
    unexpected invoice.
    """

    def test_the_owner_can_activate(self) -> None:
        owner = next(t for t in catalogue.CORE_ROLE_TEMPLATES if t.code == "ORG_OWNER")
        codes = {g.code for g in owner.grants}
        assert "core.product.activation.write" in codes

    def test_an_administrator_cannot_activate(self) -> None:
        admin = next(t for t in catalogue.CORE_ROLE_TEMPLATES if t.code == "ORG_ADMIN")
        codes = {g.code for g in admin.grants}
        assert "core.product.activation.write" not in codes, (
            "ORG_ADMIN describes itself as not handling billing, but was granted the "
            "permission that creates one"
        )

    def test_an_administrator_can_still_see_the_marketplace(self) -> None:
        """Seeing what could be bought is not the same as buying it."""
        admin = next(t for t in catalogue.CORE_ROLE_TEMPLATES if t.code == "ORG_ADMIN")
        codes = {g.code for g in admin.grants}
        assert "core.product.catalogue.read" in codes
        assert "core.product.activation.read" in codes

    def test_a_plain_member_gets_none_of_it(self) -> None:
        member = next(t for t in catalogue.CORE_ROLE_TEMPLATES if t.code == "MEMBER")
        codes = {g.code for g in member.grants}
        assert not [c for c in codes if c.startswith("core.product.")]
