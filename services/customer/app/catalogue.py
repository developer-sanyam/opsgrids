"""The CRM account-book permission catalogue and role templates, for the `crm` product.

This service is one of three processes in the `crm` product — deal-service and lead-service
are the others — and it declares its own slice of that product's vocabulary (the `crm.book.*`
permissions) rather than importing theirs across a service boundary the platform exists to
keep clean. Registering the `crm` product here is independent of, and consistent with, the
other two registering it: each process owns the catalogue for the permissions it serves,
exactly as the HRMS services do.

The structure mirrors account- and employee-service's catalogue, and for the same reason
it is shaped the way it is: in the system this platform replaces, an admin role was "every
permission that exists", so the moment another product registered its permissions the
admin silently held them too.

The structure makes that impossible rather than merely discouraged:

* a `RoleTemplate` names exactly one product;
* `PermissionCatalogue.all()` can only return that catalogue's own codes;
* and `validate()` refuses at import time if a template holds a foreign product's
  permission without declaring `cross_product`.

The guard is exercised by a test, so a wrong-product grant is a red build rather than a
super-grant discovered during an audit. A product's admin role is built from that
product's own catalogue, never "all permissions" (CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"

PRODUCT = "crm"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    module: str
    resource: str
    action: str
    description: str
    default_scope: Scope = "OWN"
    max_scope: Scope = "ORG"
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


# --------------------------------------------------------------------- crm book catalogue

CRM_BOOK = PermissionCatalogue(
    PRODUCT,
    [
        # The account book. Reading has a meaningful OWN reading (the accounts a rep owns),
        # so it defaults to OWN and tops out at ORG. The owners list — the rep directory the
        # SPA resolves display names from — is the book's reference data and rides on
        # company.read, the way ITSM's catalog rides on request.read.
        PermissionSpec("book", "company", "read", "See companies and the rep directory", "OWN"),
        PermissionSpec("book", "company", "write", "Create and update companies", "OWN"),
        # Deleting a company (soft). Dangerous: it is the one write here that removes
        # something from the book rather than adding or changing it.
        PermissionSpec(
            "book", "company", "delete", "Delete a company", "OWN", "ORG", is_dangerous=True
        ),
        PermissionSpec("book", "contact", "read", "See contacts", "OWN"),
        PermissionSpec("book", "contact", "write", "Create and update contacts", "OWN"),
        PermissionSpec(
            "book", "contact", "delete", "Delete a contact", "OWN", "ORG", is_dangerous=True
        ),
        # The rep directory's own write. A production org starts with zero owners, and
        # every other create in this product needs an `owner_id` that resolves to one, so
        # this is the prerequisite permission. ORG-only: adding a rep to the directory is
        # an administrative act, not something an individual rep's OWN scope covers.
        PermissionSpec("book", "owner", "write", "Add a rep to the directory", "ORG", "ORG"),
        # Reading a contact's real phone number, as opposed to the masked hint every other
        # read returns. Classified dangerous and stepped up at the route (`amr={"otp"}`),
        # exactly as employee-service treats `read_sensitive`: the ordinary CRM permission
        # must not be a route to a customer's personal number, and every unmask writes an
        # `entity.sensitive_read` audit row naming the field but never its value.
        PermissionSpec(
            "book",
            "contact",
            "read_sensitive",
            "Reveal a contact's real phone number, audited",
            "OWN",
            "ORG",
            is_dangerous=True,
        ),
    ],
)


CRM_BOOK_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="CRM_BOOK_ADMIN",
        name="CRM administrator (book)",
        product=PRODUCT,
        description=(
            "Administers the whole account book across the organization. Built from this "
            "service's own slice of the CRM catalogue alone — administering the CRM is not "
            "the same job as owning the account."
        ),
        grants=CRM_BOOK.all("ORG"),
    ),
    RoleTemplate(
        code="CRM_SALES_REP",
        name="Sales representative",
        product=PRODUCT,
        description=(
            "Every person with a CRM seat. Reads the whole book — sales works from one shared "
            "book — and maintains the companies and contacts they own, including revealing "
            "the real phone number of a contact they own (stepped up and audited)."
        ),
        is_default=True,
        grants=CRM_BOOK.pick(
            ("crm.book.company.read", "ORG"),
            ("crm.book.company.write", "OWN"),
            ("crm.book.contact.read", "ORG"),
            ("crm.book.contact.write", "OWN"),
            ("crm.book.contact.read_sensitive", "OWN"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product.

    Called at import and asserted by a test, so the failure mode is a red build rather
    than a customer's CRM administrator quietly holding every HRMS permission.
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


validate(CRM_BOOK_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What this product hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


CRM_BOOK_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=CRM_BOOK, role_templates=CRM_BOOK_ROLE_TEMPLATES
)
