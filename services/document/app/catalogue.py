"""Document's slice of the `core` permission catalogue (ADR-0009 §4).

`document` is a platform surface, not a product — `/api/v1/documents` is routed with **no
`product` key**, the same shape as `/api/v1/members`, so it must work for a customer who has
bought nothing yet. Its permissions therefore belong to the **`core`** catalogue that
account-service declares and approval-service already extends, not to a catalogue of their own.
`core.json` becomes a **three-source merge** with this file: account, approval, document.

The structure — and the reason it looks exactly like `account/app/catalogue.py` and
`approval/app/catalogue.py` rather than importing either — is the one those modules already
state: in the system this platform replaces, the admin role was "every permission that exists",
so the moment a second product registered its permissions the admin silently held them too.
Here that is impossible rather than discouraged: a `RoleTemplate` names exactly one product,
and `validate()` refuses at import time if a template holds a foreign product's permission
without declaring `cross_product`.

**This slice requires an ADR-0007 reconcile and cannot reach a live tenant without one.**
`install_registration` skips role codes that already exist, so `ORG_OWNER` and `ORG_ADMIN` in
every existing organization keep exactly the grants they have: every upload 403s in every live
tenant while a freshly created organization works perfectly. Two ordering rules from ADR-0007
apply verbatim and both are silent when violated — catalogue export precedes the reconcile (a
grant naming a permission row that does not exist is dropped without error), and the pin on the
merged manifest must assert **in-code ⊆ manifest**, never equality, which already cost this
repo one build failure when `approval` became `core`'s second source.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"

PRODUCT = "core"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    module: str
    resource: str
    action: str
    description: str
    default_scope: Scope = "OWN"
    max_scope: Scope = "ORG"
    #: Requires a recent second factor. False on both of document's permissions, and
    #: deliberately: `is_dangerous` has zero references in `libs/platform-core` and changes no
    #: authorization outcome (ADR-0000 §5's note, added after ADR-0008 had to retract exactly
    #: this claim). What gates these routes is the ORG floor and the audit row.
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
    #: Only a deliberately cross-product role may hold another product's permissions, and
    #: saying so is explicit. None of document's templates need it — every grant here is
    #: `core.content.*`, which is this module's own product.
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
        """Every permission **in this catalogue**, at `scope`.

        The safe form of "give the admin everything": bound to one product by construction, so
        it cannot reach another's.
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


# --------------------------------------------------------------------- document's core slice

#: **Both permissions pin `default_scope` and `max_scope` at ORG**, and that is the whole
#: design rather than an oversight (ADR-0009 §4).
#:
#: `max_scope` is a ladder *ceiling*, not an allowed-set: account validates a grant with a
#: single rank comparison, so a permission ceilinged at ORG still leaves OWN, TEAM and
#: LOCATION grantable whether or not this service has an opinion about them — and a rung with
#: no defined meaning gets improvised per endpoint, which is the trap ADR-0007 §1 records.
#: Setting the *default* to ORG as well makes every rung below it unreachable rather than
#: merely ungranted, so there is nothing left to improvise.
#:
#: An asset library is an organization-level resource. An image has no team and no location,
#: and the "own uploads" reading nobody has asked for would be the only thing OWN could mean.
DOCUMENT = PermissionCatalogue(
    PRODUCT,
    [
        PermissionSpec(
            "content",
            "asset",
            "read",
            "List the organization's uploaded images and read one image's details. "
            "Organization-wide: an asset library has no team or location reading",
            "ORG",
            "ORG",
        ),
        # No third `core.content.asset.delete` permission. The house grammar is read/write,
        # and ADR-0000 §5 makes a new verb free of migration cost, so this is a choice: nobody
        # has asked for an upload-but-not-remove role, a third permission is a third thing
        # ADR-0007's reconcile has to deliver to every live tenant, and an ordinary delete is
        # non-destructive by design. **The trade, plainly: anyone who can upload can also take
        # an image down.** Revisit when a customer asks for the split.
        PermissionSpec(
            "content",
            "asset",
            "write",
            "Upload an image to the organization's library, and remove one from it — "
            "including an immediate takedown of the published object",
            "ORG",
            "ORG",
        ),
        # -------------------------------------------------------------- private objects (Q1)
        # Unlike the asset permissions, **default OWN, ceiling ORG**: a private object (a
        # payslip, an ID scan) is personal by default, and its own uploader can act on it
        # without an ORG grant. A grant this service hands out at ORG (an admin role) can still
        # reach every organization's object, the same ladder every other OWN-ceilinged
        # permission in the platform uses — this is not a special case, "asset" is (ADR-0009 §4
        # ceilings *both* asset permissions at ORG because an asset library has no owner
        # reading; a private object is the opposite: it always has one).
        #
        # OWN here is `owner_subject_ref` (`app/selectors.py`), the same ADR-0008 employee link
        # `itsm`/`crm`/`leave` read OWN against — and it inherits that ADR's own documented
        # gap: a tenant with no HRMS activated links no membership to an employee, so this rung
        # stays permanently empty there, the same way ADR-0008 names for those products. Named
        # once, here, because `document` has no HRMS dependency of its own and a later reader
        # could otherwise mistake this for a bug rather than an inherited, accepted trade-off.
        PermissionSpec(
            "content",
            "private_document",
            "write",
            "Upload a private document for this organization, and remove one before it has "
            "been attached to anything",
            "OWN",
            "ORG",
        ),
        PermissionSpec(
            "content",
            "private_document",
            "read",
            "Read a private document's metadata and obtain a short-lived signed download URL",
            "OWN",
            "ORG",
        ),
    ],
)


DOCUMENT_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="ORG_OWNER",
        name="Organization owner",
        product=PRODUCT,
        # Worded so it reads well *merged*: the exporter concatenates each contributing
        # service's description, so a sentence that restates what account and approval have
        # already said lands in the manifest twice.
        description=(
            "Document's contribution to that union is the organization's image library, plus "
            "every private document in the organization."
        ),
        grants=DOCUMENT.all("ORG"),
    ),
    RoleTemplate(
        code="ORG_ADMIN",
        name="Administrator",
        product=PRODUCT,
        description=(
            "The image library is organization content rather than billing, so it belongs to "
            "the same role — and an administrator can read and remove any member's private "
            "document, the same reach an HR or compliance admin needs over an employee's ID "
            "scan or payslip."
        ),
        grants=DOCUMENT.all("ORG"),
    ),
    # `MEMBER` gets the asset grants **not at all** — still no reason to list or upload to the
    # organization's public image library — but does get both private-document grants **at
    # OWN**: an ordinary employee uploads their own KYC document or reads their own payslip
    # without needing an admin role, and OWN means exactly that reach and no wider one.
    RoleTemplate(
        code="MEMBER",
        name="Member",
        product=PRODUCT,
        description=(
            "Every member can upload, read and remove their own private documents — a payslip, "
            "an ID scan, a signed form — without a reason to touch anyone else's."
        ),
        grants=DOCUMENT.pick(
            ("content.private_document.write", "OWN"),
            ("content.private_document.read", "OWN"),
        ),
    ),
    # There is also no storefront role here, because no `storefront` product is registered
    # anywhere in the platform. When one is, whether store editing gets its own role is that
    # ADR's question, not this one's.
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product.

    Called at import and asserted by a test, so the failure mode is a red build rather than a
    role quietly holding a foreign product's permission.
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


validate(DOCUMENT_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What this service hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


CORE_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=DOCUMENT, role_templates=DOCUMENT_ROLE_TEMPLATES
)
