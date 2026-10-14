"""The CRM pipeline permission catalogue and role templates, for the `crm` product.

This service is one of three processes in the `crm` product — customer-service and
lead-service are the others — and it declares its own slice of that product's vocabulary
(the `crm.pipeline.*` permissions) rather than importing theirs across a service boundary
the platform exists to keep clean. Registering the `crm` product here is independent of, and
consistent with, the other two registering it: each process owns the catalogue for the
permissions it serves, exactly as the HRMS services do.

The structure mirrors account- and employee-service's catalogue, and for the same reason
it is shaped the way it is: in the system this platform replaces, an admin role was
"every permission that exists", so the moment another product registered its permissions
the admin silently held them too.

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


# --------------------------------------------------------------------- crm pipeline catalogue

CRM_PIPELINE = PermissionCatalogue(
    PRODUCT,
    [
        # Deals. Reading has a meaningful OWN reading (the deals a rep runs), so it defaults
        # to OWN and tops out at ORG. The summary rollup rides on deal.read — it is the same
        # deal list, aggregated. Writing (create, move stage) has an OWN reading too, and the
        # object-level check does the rest.
        PermissionSpec("pipeline", "deal", "read", "See deals and the pipeline summary", "OWN"),
        PermissionSpec(
            "pipeline", "deal", "write", "Create deals and move them through stages", "OWN"
        ),
        # Deleting a deal (soft). Dangerous: the one write here that removes something
        # from the pipeline rather than adding or changing it.
        PermissionSpec(
            "pipeline", "deal", "delete", "Delete a deal", "OWN", "ORG", is_dangerous=True
        ),
        # The timeline. Append-only: activity.write can only ever add an entry.
        PermissionSpec("pipeline", "activity", "read", "See the activity timeline", "OWN"),
        PermissionSpec(
            "pipeline", "activity", "write", "Log calls, emails, meetings and notes", "OWN"
        ),
    ],
)


CRM_PIPELINE_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="CRM_PIPELINE_ADMIN",
        name="CRM administrator (pipeline)",
        product=PRODUCT,
        description=(
            "Administers the whole pipeline across the organization. Built from this "
            "service's own slice of the CRM catalogue alone — administering the CRM is not "
            "the same job as owning the account."
        ),
        grants=CRM_PIPELINE.all("ORG"),
    ),
    RoleTemplate(
        code="CRM_DEAL_REP",
        name="Sales representative",
        product=PRODUCT,
        description=(
            "Every person with a CRM seat. Reads the whole board — sales works from one "
            "shared pipeline — and moves and logs against the deals they own."
        ),
        is_default=True,
        grants=CRM_PIPELINE.pick(
            ("crm.pipeline.deal.read", "ORG"),
            ("crm.pipeline.deal.write", "OWN"),
            ("crm.pipeline.activity.read", "ORG"),
            ("crm.pipeline.activity.write", "OWN"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


validate(CRM_PIPELINE_ROLE_TEMPLATES)


CRM_PIPELINE_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=CRM_PIPELINE, role_templates=CRM_PIPELINE_ROLE_TEMPLATES
)
