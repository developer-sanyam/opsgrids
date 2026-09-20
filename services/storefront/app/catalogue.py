"""The Storefront permission catalogue and role templates, for the **new** `storefront` product.

Mirrors `operations/app/catalogue.py` and `document/app/catalogue.py`, and is repeated rather
than imported for the reason all of them state: in the system this platform replaces, the admin
role was "every permission that exists", so the moment a second product registered its
permissions the admin silently held them too. Here that is impossible rather than discouraged —
a `RoleTemplate` names exactly one product, `PermissionCatalogue.all()` can only return that
catalogue's own codes, and `validate()` refuses at import time if a template holds a foreign
product's permission without declaring `cross_product`.

────────────────────────────────────────────────────────────────────────────────────────────
**This slice needs no ADR-0007 reconcile, and that is a consequence of a choice rather than
luck.**

`install_registration` skips role codes that already exist. ADR-0009 put two permissions on
**`core`**, whose `ORG_OWNER` and `ORG_ADMIN` already exist in every live organization, so
those grants could not reach a single existing tenant without ADR-0007's fleet-wide reconcile:
every upload 403'd in every live tenant while a freshly created organization worked perfectly.

Everything here is on a **new product key** with **new role codes**. `STOREFRONT_ADMIN` and
`STOREFRONT_STAFF` exist in no organization, so `install_registration` installs them normally
when the product is activated, and nothing has to be back-filled. Putting these permissions on
`storefront` rather than on `core` is what buys that — the direct contrast with ADR-0009, and
the reason it is written down here rather than left looking like an accident.

The ordering rule ADR-0007 states still applies and is still silent when violated: **export the
manifest before installing it**. A grant naming a permission row that does not exist is dropped
by `install_registration` without an error.
────────────────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass, field

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"

PRODUCT = "storefront"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    module: str
    resource: str
    action: str
    description: str
    default_scope: Scope = "OWN"
    max_scope: Scope = "ORG"
    #: False on all four, deliberately: `is_dangerous` has zero references in
    #: `libs/platform-core` and changes no authorization outcome (ADR-0000 §5's note). What
    #: gates these routes is the ORG floor and the audit row.
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
    #: Only a deliberately cross-product role may hold another product's permissions. Neither
    #: template here needs it — every grant is `storefront.*`.
    cross_product: bool = False


_RANK = {"OWN": 0, "TEAM": 1, "LOCATION": 2, "ORG": 3}


def min_scope(wanted: Scope, ceiling: Scope) -> Scope:
    """Never grant wider than the permission's own ceiling."""
    return wanted if _RANK[wanted] <= _RANK[ceiling] else ceiling


class PermissionCatalogue:
    """One product's permissions. A role builder can only see its own."""

    def __init__(self, product: str, specs: list[PermissionSpec]) -> None:
        self.product = product
        self.specs = specs

    def codes(self) -> list[str]:
        return [s.code(self.product) for s in self.specs]

    def all(self, scope: Scope = "ORG") -> list[Grant]:
        """Every permission **in this catalogue**, at `scope` — the safe "give the admin
        everything", bound to one product by construction."""
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


# --------------------------------------------------------------------- storefront catalogue

#: **All four are pinned at `default ORG, max ORG`**, which is the house answer for organization
#: reference data (`operations`' supplier, item and warehouse specs) and is the whole design
#: rather than an oversight (ADR-0010 §9).
#:
#: `max_scope` is a ladder *ceiling*, not an allowed-set: account validates a grant with a single
#: `_RANK` comparison, so a permission ceilinged at ORG leaves OWN, TEAM and LOCATION grantable
#: whether or not this service has an opinion about them — and a rung with no defined meaning
#: gets improvised per endpoint, which is the trap ADR-0007 §1 records. Setting the *default* to
#: ORG as well makes every lower rung unreachable rather than merely ungranted.
#:
#: A delivery zone has no owner, no team and no location reading. There is nothing OWN could
#: mean for a postal code.
STOREFRONT = PermissionCatalogue(
    PRODUCT,
    [
        PermissionSpec(
            "store",
            "settings",
            "read",
            "See the store's identity — its name, tagline, logo text, address, status, "
            "currency, timezone and custom-domain claim",
            "ORG",
            "ORG",
        ),
        # Publishing stays on this permission rather than getting a fifth: it is the store's own
        # setting with the largest consequence, not a different job, and nobody has asked for an
        # edit-the-brand-but-not-publish role (ADR-0011 §4). The trade, plainly: whoever can
        # rename the store can put it in front of the internet.
        PermissionSpec(
            "store",
            "settings",
            "write",
            "Change the store's name, brand and settings, claim a custom domain, and publish or "
            "unpublish the store — the act that makes it visible to anyone",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "delivery",
            "zone",
            "read",
            "See delivery zones and their postal codes, test a postal code, and read the "
            "out-of-zone demand report",
            "ORG",
            "ORG",
        ),
        # **The demand report is folded into `zone.read`** rather than given a fifth permission.
        # Nobody has asked for a read-the-demand-but-not-the-zones role, and a fifth permission
        # is a fifth thing to deliver. The trade, plainly: anyone who can see the zones can see
        # which postal codes were turned away (ADR-0010 §9).
        PermissionSpec(
            "delivery",
            "zone",
            "write",
            "Create and edit delivery zones, map postal codes to them, and import a postal-code "
            "file",
            "ORG",
            "ORG",
        ),
    ],
)


STOREFRONT_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="STOREFRONT_ADMIN",
        name="Storefront administrator",
        product=PRODUCT,
        description=(
            "Runs the store: its published identity and its delivery map. Built from the "
            "Storefront catalogue alone — publishing a store is not the same job as owning the "
            "account."
        ),
        grants=STOREFRONT.all("ORG"),
    ),
    RoleTemplate(
        code="STOREFRONT_STAFF",
        name="Storefront staff",
        product=PRODUCT,
        is_default=True,
        description=(
            "Every person with a Storefront seat. Reads the store's settings and its delivery "
            "zones, including which postal codes were turned away, and changes neither."
        ),
        grants=STOREFRONT.pick(
            ("storefront.store.settings.read", "ORG"),
            ("storefront.delivery.zone.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product.

    Called at import and asserted by a test, so the failure mode is a red build rather than a
    customer's storefront administrator quietly holding every CRM permission.
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


validate(STOREFRONT_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What this product hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


STOREFRONT_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=STOREFRONT, role_templates=STOREFRONT_ROLE_TEMPLATES
)
