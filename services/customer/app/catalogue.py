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

from platform_core.catalogue import (
    PermissionCatalogue,
    PermissionSpec,
    ProductRegistration,
    RoleTemplate,
    validate,
)

PRODUCT = "crm"


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
        # read returns. Classified dangerous and gated on the permission alone at the route
        # (no step-up, ADR-0023 addendum), as employee-service treats `read_sensitive`: the
        # ordinary CRM permission must not be a route to a customer's personal number, and every
        # unmask writes an
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
        name="Sales representative (companies and contacts)",
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


validate(CRM_BOOK_ROLE_TEMPLATES)


CRM_BOOK_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=CRM_BOOK, role_templates=CRM_BOOK_ROLE_TEMPLATES
)
