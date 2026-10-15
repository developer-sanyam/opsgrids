"""The CRM lead-intake permission catalogue and role templates, for the `crm` product.

This service is the third process in the `crm` product — customer-service (the account book,
`crm.book.*`) and deal-service (the pipeline, `crm.pipeline.*`) are the other two — and it
declares its own slice of that product's vocabulary (`crm.lead.*`) rather than importing
either's catalogue across a service boundary the platform exists to keep clean. Registering
the `crm` product here is independent of, and consistent with, the other two registering it in
their own processes: each service owns the catalogue for the permissions it serves, exactly as
the HRMS services do.

ADR-0017 records why this is a third slice of `crm` rather than a new `salescrm` product: the
onboarding cards this service's first three cards were built from (`docs/onboarding/
cards-dev-a-lead.md`) originally specified a standalone `salescrm` product with its own
manifest, written before `customer` and `deal` existed on this branch. Building that would
have made `crm` and `salescrm` two entitlements for one CRM, and split the pipeline's already
years-shipped `crm.pipeline.*` from a lead's conversion into it. This catalogue is the
corrected version.

The structure mirrors customer- and deal-service's own catalogue, and for the same reason it
is shaped the way it is: in the system this platform replaces, an admin role was "every
permission that exists", so the moment another product registered its permissions the admin
silently held them too.

The structure makes that impossible rather than merely discouraged:

* a `RoleTemplate` names exactly one product;
* `PermissionCatalogue.all()` can only return that catalogue's own codes;
* and `validate()` refuses at import time if a template holds a foreign product's
  permission without declaring `cross_product`.

The guard is exercised by a test, so a wrong-product grant is a red build rather than a
super-grant discovered during an audit. A product's admin role is built from that product's
own catalogue, never "all permissions" (CLAUDE.md).
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


# --------------------------------------------------------------------- crm lead catalogue

CRM_LEADS = PermissionCatalogue(
    PRODUCT,
    [
        # Leads. Reading has a meaningful OWN reading (the leads a rep is working), so it
        # defaults to OWN and tops out at ORG — the same shape `book.company.read` and
        # `pipeline.deal.read` already take.
        PermissionSpec("lead", "lead", "read", "See leads", "OWN"),
        PermissionSpec("lead", "lead", "write", "Create and update leads", "OWN"),
        # Converting a lead creates a company (+ contact) on customer-service and a deal on
        # deal-service — a bigger act than an ordinary field edit, so it is its own
        # permission rather than folded into `write`. OWN by default: a rep converts the
        # leads they are working.
        PermissionSpec(
            "lead", "lead", "convert", "Convert a qualified lead into a company and deal", "OWN"
        ),
        # Reading a lead's real phone number or email, as opposed to the masked hint every
        # other read returns. Classified dangerous and gated on the permission alone
        # at the route (no step-up, ADR-0023 addendum), exactly as customer-service treats
        # `crm.book.contact.read_sensitive`: the ordinary lead permission must not be a route to an
        # unconverted prospect's
        # personal details, and every reveal writes an `entity.sensitive_read` audit row naming
        # the field but never its value. OWN by default (a rep reveals the leads they are
        # working), ORG at most. As ADR-0000 §5's note says, `is_dangerous` is metadata and
        # not a control — what actually constrains a reveal is the permission on the route, the
        # scope re-applied to the one lead, and the audit row.
        PermissionSpec(
            "lead",
            "lead",
            "read_sensitive",
            "Reveal a lead's real phone number or email, audited",
            "OWN",
            "ORG",
            is_dangerous=True,
        ),
    ],
)


CRM_LEADS_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="CRM_LEADS_ADMIN",
        name="CRM administrator (leads)",
        product=PRODUCT,
        description=(
            "Administers lead intake across the organization, including the assignment rule. "
            "Built from this service's own slice of the CRM catalogue alone — administering "
            "the CRM is not the same job as owning the account."
        ),
        grants=CRM_LEADS.all("ORG"),
    ),
    RoleTemplate(
        code="CRM_LEAD_REP",
        name="Sales representative (leads)",
        product=PRODUCT,
        description=(
            "Every person with a CRM seat. Reads the whole intake list — sales works from one "
            "shared book — works and converts the leads they own, and may reveal the real "
            "phone number or email of a lead they own (stepped up and audited)."
        ),
        is_default=True,
        grants=CRM_LEADS.pick(
            ("crm.lead.lead.read", "ORG"),
            ("crm.lead.lead.write", "OWN"),
            ("crm.lead.lead.convert", "OWN"),
            ("crm.lead.lead.read_sensitive", "OWN"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


validate(CRM_LEADS_ROLE_TEMPLATES)


CRM_LEADS_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=CRM_LEADS, role_templates=CRM_LEADS_ROLE_TEMPLATES
)
