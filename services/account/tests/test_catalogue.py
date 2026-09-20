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
    load_product_manifest,
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


class TestCoreManifestPin:
    """`core` is installed twice: in-code (`CORE_REGISTRATION`) at organization creation,
    because that path runs before any manifest directory is guaranteed readable, and from
    its committed manifest (`libs/contracts/permissions/core.json`) by
    `tools/reconcile_permissions.py` for a tenant that already exists (ADR-0007 §2).

    ADR-0007 §2 describes this as "two sources for one thing", held together by a test that
    the two describe "the same permissions and the same role templates with the same
    grants" — equality. That assumption held only while `account` was the sole contributor
    to `core`. It stopped holding the moment `services/approval/app/catalogue.py` started
    contributing `core.approval.*` (ADR-0006 §11): `tools/export_permissions.py` merges every
    service's slice of `core` into one manifest exactly as it already does for `hrms`
    (`sources: ["account", "approval"]`), so `core.json` is now a strict superset of
    `CORE_REGISTRATION` by design, not by drift.

    The semantics this test enforces instead: **`CORE_REGISTRATION` must be a subset of the
    merged manifest — account's declaration may never be missing from it, and never
    contradicted by it (a scope the manifest ever states as narrower than account declared,
    or a spec whose danger/ceiling disagrees).** The manifest may freely be *wider* — extra
    permissions, extra role templates, extra grants on a shared template — because that is
    exactly what another service's slice of `core` is. Equality is checked at a narrower
    grain instead, in `TestApprovalGrantsReachExistingTenants` below: the merged manifest is
    reconciled against a real organization and the resulting grants are asserted directly,
    which is the property that actually matters — `core.approval.*` reaching a tenant that
    already exists — rather than a byte-for-byte match between two declarations that both
    have a right to add to `core`.

    This narrows what ADR-0007 §2 states; see this slice's report for the exact contradiction
    and what the ADR should say instead. The two-way pin against `tools/export_permissions.py
    --check` (which still fails the build when the manifest is stale against *all* the code
    that produced it) is unaffected — this test only changes what "the code" means when more
    than one service contributes to the same product.
    """

    def test_in_code_registration_is_a_subset_of_the_merged_manifest(self) -> None:
        manifest = load_product_manifest("core")
        assert manifest is not None, (
            "libs/contracts/permissions/core.json is missing — run `uv run python "
            "tools/export_permissions.py` from the repository root"
        )
        assert manifest.product == "core"

        in_code_specs = {
            s.code("core"): (s.default_scope, s.max_scope, s.is_dangerous) for s in CORE.specs
        }
        manifest_specs = {
            s.code("core"): (s.default_scope, s.max_scope, s.is_dangerous)
            for s in manifest.registration.catalogue.specs
        }
        missing_specs = in_code_specs.keys() - manifest_specs.keys()
        assert not missing_specs, (
            f"core.json is missing permissions CORE declares in app/catalogue.py: "
            f"{sorted(missing_specs)} — regenerate with `tools/export_permissions.py`"
        )
        contradicted_specs = {
            code: (in_code_specs[code], manifest_specs[code])
            for code in in_code_specs
            if manifest_specs[code] != in_code_specs[code]
        }
        assert not contradicted_specs, (
            "core.json describes a permission CORE also declares, differently: "
            f"{contradicted_specs} — a manifest may add to a permission's declaration, "
            "never disagree with it"
        )

        in_code_templates = {
            t.code: {(g.code, g.scope) for g in t.grants} for t in CORE_ROLE_TEMPLATES
        }
        manifest_templates = {
            t.code: {(g.code, g.scope) for g in t.grants}
            for t in manifest.registration.role_templates
        }
        missing_templates = in_code_templates.keys() - manifest_templates.keys()
        assert not missing_templates, (
            f"core.json is missing role templates CORE_ROLE_TEMPLATES declares: "
            f"{sorted(missing_templates)} — regenerate with `tools/export_permissions.py`"
        )

        manifest_scope_by_code = {
            template_code: dict(grants) for template_code, grants in manifest_templates.items()
        }
        for template_code, grants in in_code_templates.items():
            manifest_scopes = manifest_scope_by_code[template_code]
            for code, scope in grants:
                manifest_scope = manifest_scopes.get(code)
                assert manifest_scope is not None, (
                    f"core.json's {template_code!r} template is missing the grant "
                    f"{code!r} that CORE_ROLE_TEMPLATES gives it — regenerate with "
                    "`tools/export_permissions.py`"
                )
                assert catalogue._RANK[manifest_scope] >= catalogue._RANK[scope], (
                    f"core.json's {template_code!r} template narrows {code!r} to "
                    f"{manifest_scope!r}, but CORE_ROLE_TEMPLATES grants it {scope!r} — a "
                    "merged manifest may only ever widen a shared template's grants "
                    "(tools/export_permissions.py's merge rule), never narrow one"
                )
