"""Permission catalogues and role templates, registered per product.

The bug this module is shaped to prevent: in the system this platform replaces, the HR
admin role was defined as *every permission that exists*. Under a shared catalogue that
would silently grant CRM, finance and billing permissions the moment those products
registered theirs.

A comment saying "remember to filter" fixes it until someone forgets. So the structure
makes it impossible instead:

* a `RoleTemplate` names exactly one product;
* `PermissionCatalogue.all()` can only return that catalogue's own codes;
* and `validate()` refuses at import time if a template holds a foreign product's
  permission without declaring `cross_product`.

The guard is tested. Adding a wrong-product grant is a failing build, not a silent
super-grant discovered during an audit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    module: str
    resource: str
    action: str
    description: str
    default_scope: Scope = "OWN"
    max_scope: Scope = "ORG"
    #: Requires a recent second factor. Payroll approval, PII unmask, product activation.
    is_dangerous: bool = False

    def code(self, product: str) -> str:
        return f"{product}.{self.module}.{self.resource}.{self.action}"


@dataclass(frozen=True, slots=True)
class Grant:
    code: str
    scope: Scope


@dataclass(frozen=True, slots=True)
class RoleTemplate:
    code: str
    name: str
    product: str
    grants: list[Grant]
    description: str = ""
    is_default: bool = False
    #: Only a deliberately cross-product role (the organization owner) may hold another
    #: product's permissions, and saying so is explicit.
    cross_product: bool = False


class PermissionCatalogue:
    """One product's permissions. A role builder can only see its own."""

    def __init__(self, product: str, specs: list[PermissionSpec]) -> None:
        self.product = product
        self.specs = specs

    def codes(self) -> list[str]:
        return [s.code(self.product) for s in self.specs]

    def all(self, scope: Scope = "ORG") -> list[Grant]:
        """Every permission **in this catalogue**, at `scope`.

        This is the safe form of "give the admin everything": bound to one product by
        construction, so it cannot reach another's.
        """
        return [Grant(s.code(self.product), min_scope(scope, s.max_scope)) for s in self.specs]

    def pick(self, *pairs: tuple[str, Scope]) -> list[Grant]:
        """Named grants, validated against this catalogue so a typo fails loudly."""
        known = set(self.codes())
        out: list[Grant] = []
        for code, scope in pairs:
            full = code if code.startswith(f"{self.product}.") else f"{self.product}.{code}"
            if full not in known:
                raise ValueError(f"{full} is not in the {self.product} catalogue")
            out.append(Grant(full, scope))
        return out


_RANK = {"OWN": 0, "TEAM": 1, "LOCATION": 2, "ORG": 3}


def min_scope(wanted: Scope, ceiling: Scope) -> Scope:
    """Never grant wider than the permission's own ceiling."""
    return wanted if _RANK[wanted] <= _RANK[ceiling] else ceiling


# --------------------------------------------------------------------- core catalogue

CORE = PermissionCatalogue(
    "core",
    [
        PermissionSpec("org", "organization", "read", "See the organization's profile", "ORG"),
        PermissionSpec(
            "org", "organization", "write", "Change name, address, tax identifiers", "ORG", "ORG"
        ),
        PermissionSpec("org", "unit", "read", "See departments and teams", "ORG"),
        PermissionSpec("org", "unit", "write", "Create and reorganize departments", "ORG", "ORG"),
        PermissionSpec("org", "location", "read", "See sites", "ORG"),
        PermissionSpec("org", "location", "write", "Create and edit sites", "ORG", "ORG"),
        PermissionSpec("identity", "membership", "read", "See who is in the organization", "TEAM"),
        PermissionSpec(
            "identity",
            "membership",
            "write",
            "Invite, deactivate and edit members",
            "ORG",
            "ORG",
            is_dangerous=True,
        ),
        PermissionSpec("identity", "role", "read", "See roles and what they grant", "ORG"),
        PermissionSpec(
            "identity",
            "role",
            "write",
            "Create roles and change grants",
            "ORG",
            "ORG",
            is_dangerous=True,
        ),
        PermissionSpec("identity", "audit", "read", "Read the audit trail", "ORG", "ORG"),
        # Single sign-on. The rows these guard live in auth-service (`auth.org_idp_configs`),
        # not here — auth holds every credential, and an IdP client secret is a credential.
        # The *vocabulary* still belongs to `core`, which this service declares, and a
        # permission code must have exactly one owner (`tools/export_permissions.py` refuses
        # a code declared by two services). So the code is minted here and enforced there.
        PermissionSpec("identity", "sso", "read", "See the single sign-on setup", "ORG", "ORG"),
        PermissionSpec(
            "identity",
            "sso",
            "write",
            "Configure the identity provider users sign in through",
            "ORG",
            "ORG",
            # Whoever holds this decides which external issuer may assert who a person is,
            # and `enforce_sso` turns password login off for the whole organization.
            is_dangerous=True,
        ),
        # The marketplace and what the organization has turned on. These live in `core`
        # rather than in a `product` product, because browsing and activating is a platform
        # capability every customer has whatever they bought — the same reason
        # product-service marks its own `core` entry `is_platform`.
        PermissionSpec("product", "catalogue", "read", "Browse the marketplace", "ORG"),
        PermissionSpec("product", "activation", "read", "See which products are active", "ORG"),
        PermissionSpec(
            "product",
            "activation",
            "write",
            "Activate and deactivate products",
            "ORG",
            "ORG",
            # Activation creates a financial commitment. `ORG_OWNER` describes itself as
            # running billing and activation, and `ORG_ADMIN` as explicitly not billing —
            # so this is granted to the owner only, and the templates below say so.
            is_dangerous=True,
        ),
        PermissionSpec("profile", "self", "read", "See your own profile", "OWN", "OWN"),
        PermissionSpec("profile", "self", "write", "Edit your own profile", "OWN", "OWN"),
    ],
)


CORE_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="ORG_OWNER",
        name="Organization owner",
        product="core",
        description=(
            "Runs the account: billing, product activation, members and roles. Deliberately "
            "separate from a product administrator — owning the company is not the same job as "
            "administering its HR data."
        ),
        # Safe by construction: this is core's catalogue, so it cannot reach CRM or HRMS.
        grants=CORE.all("ORG"),
    ),
    RoleTemplate(
        code="ORG_ADMIN",
        name="Administrator",
        product="core",
        description="Manages people and structure, but not billing.",
        grants=CORE.pick(
            ("org.organization.read", "ORG"),
            ("org.unit.read", "ORG"),
            ("org.unit.write", "ORG"),
            ("org.location.read", "ORG"),
            ("org.location.write", "ORG"),
            ("identity.membership.read", "ORG"),
            ("identity.membership.write", "ORG"),
            ("identity.role.read", "ORG"),
            # Can see the marketplace and what is active; cannot activate, because that
            # spends money and this role is defined as the one that does not.
            ("product.catalogue.read", "ORG"),
            ("product.activation.read", "ORG"),
            ("profile.self.read", "OWN"),
            ("profile.self.write", "OWN"),
        ),
    ),
    RoleTemplate(
        code="MEMBER",
        name="Member",
        product="core",
        description="Everyone. Sees the organization chart and edits their own profile.",
        is_default=True,
        grants=CORE.pick(
            ("org.organization.read", "ORG"),
            ("org.unit.read", "ORG"),
            ("org.location.read", "ORG"),
            ("identity.membership.read", "TEAM"),
            ("profile.self.read", "OWN"),
            ("profile.self.write", "OWN"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product.

    Called at import and asserted by a test, so the failure mode is a red build rather
    than a customer's HR administrator quietly holding every CRM permission.
    """
    for template in templates:
        if template.cross_product:
            continue
        for grant in template.grants:
            product = grant.code.split(".", 1)[0]
            if product != template.product:
                raise ValueError(
                    f"Role template {template.code!r} belongs to product "
                    f"{template.product!r} but grants {grant.code!r}. "
                    "Set cross_product=True deliberately, or remove the grant."
                )


validate(CORE_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What a product hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


CORE_REGISTRATION = ProductRegistration(
    product="core", catalogue=CORE, role_templates=CORE_ROLE_TEMPLATES
)


# --------------------------------------------------------------------- product manifests
#
# Core is the only catalogue this service declares. Every *product's* catalogue lives with
# the product — and deliberately cannot be imported from here, because importing another
# service's module would couple the two across the very boundary the platform exists to keep
# clean. So products publish their catalogues as **data**: `tools/export_permissions.py`
# writes one JSON manifest per product to `libs/contracts/permissions/` at build time (the
# same committed-contract idea as the OpenAPI documents beside them), and this section reads
# a manifest back into the same `ProductRegistration` shape `install_registration` already
# installs. The consumer installs a product's manifest when the product is activated for an
# organization; `tools/wire_product.py` drives the identical path by hand.

MANIFEST_VERSION = 1

#: A product key as it appears in a manifest filename. The key arrives in an event payload,
#: so it is validated before it goes anywhere near a path — `../../secret` is not a product.
_PRODUCT_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


@dataclass(frozen=True, slots=True)
class ProductManifest:
    """One product's catalogue and role templates, parsed from its committed manifest."""

    product: str
    registration: ProductRegistration
    #: Every template the exporter marked admin-most — one per service the product spans,
    #: each granting the widest slice of that service's catalogue. Installed like any other
    #: template, and additionally granted to the organization's owners on activation so a
    #: newly activated product has an administrator from the first minute.
    #:
    #: A list rather than one code because a product is not a service: CRM spans customer
    #: and deal, and granting only the last-parsed admin role left its owners able to read
    #: contacts and not deals.
    admin_role_codes: tuple[str, ...]

    @property
    def admin_role_code(self) -> str | None:
        """The first admin template, for callers that want a single representative code."""
        return self.admin_role_codes[0] if self.admin_role_codes else None


def registration_from_manifest(data: dict[str, Any]) -> ProductManifest:
    """Parse one manifest into the shape `install_registration` installs.

    Every guard the in-code catalogues get at import time runs here too: constructing the
    `ProductRegistration` re-runs `validate()`, so a manifest whose template reaches into a
    foreign product is refused exactly as a wrong-product grant in code would be. On top of
    that, a grant naming a permission its own catalogue does not declare is refused —
    `install_registration` would silently skip it, and a silently skipped grant is a role
    that quietly does less than its manifest says.
    """
    version = data.get("manifest_version")
    if version != MANIFEST_VERSION:
        raise ValueError(f"unsupported manifest_version {version!r}")
    product = data["product"]

    catalogue = PermissionCatalogue(
        product,
        [
            PermissionSpec(
                module=spec["module"],
                resource=spec["resource"],
                action=spec["action"],
                description=spec["description"],
                default_scope=spec["default_scope"],
                max_scope=spec["max_scope"],
                is_dangerous=bool(spec.get("is_dangerous", False)),
            )
            for spec in data["permissions"]
        ],
    )

    own_codes = set(catalogue.codes())
    templates: list[RoleTemplate] = []
    admin_role_codes: list[str] = []
    for raw in data["role_templates"]:
        template = RoleTemplate(
            code=raw["code"],
            name=raw["name"],
            product=product,
            grants=[Grant(g["code"], g["scope"]) for g in raw["grants"]],
            description=raw.get("description", ""),
            is_default=bool(raw.get("is_default", False)),
            cross_product=bool(raw.get("cross_product", False)),
        )
        if not template.cross_product:
            unknown = [g.code for g in template.grants if g.code not in own_codes]
            if unknown:
                raise ValueError(
                    f"Role template {template.code!r} grants {unknown} which the "
                    f"{product} manifest does not declare"
                )
        templates.append(template)
        if raw.get("is_admin"):
            admin_role_codes.append(template.code)

    registration = ProductRegistration(
        product=product, catalogue=catalogue, role_templates=templates
    )
    return ProductManifest(
        product=product,
        registration=registration,
        admin_role_codes=tuple(admin_role_codes),
    )


def _default_manifest_dir() -> Path | None:
    """`libs/contracts/permissions/`, found the way settings find the workspace `.env`.

    A deployment that does not ship the repository layout sets
    `permission_manifest_dir` instead; walking up is the development and CI path.
    """
    # Imported here rather than at module top so the catalogue definitions above stay a
    # pure, settings-free module — tests and the seeder import them with no environment.
    from app.config import settings

    configured = settings().permission_manifest_dir
    if configured:
        return Path(configured)
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists() and (parent / "services").is_dir():
            return parent / "libs" / "contracts" / "permissions"
    return None


def load_product_manifest(product_key: str) -> ProductManifest | None:
    """The committed manifest for `product_key`, or None when there is none.

    None is a normal answer, not an error: a product with no backend catalogue — a purely
    presentational one — has nothing to register, and the consumer records the activation as
    processed either way.
    """
    if not _PRODUCT_KEY.match(product_key):
        return None
    directory = _default_manifest_dir()
    if directory is None:
        return None
    path = directory / f"{product_key}.json"
    if not path.is_file():
        return None
    manifest = registration_from_manifest(json.loads(path.read_text(encoding="utf-8")))
    if manifest.product != product_key:
        raise ValueError(
            f"manifest {path.name} declares product {manifest.product!r}; the file is misnamed"
        )
    return manifest
