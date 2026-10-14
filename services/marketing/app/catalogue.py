"""The Marketing permission catalogue and role templates, for the `marketing` product.

Same construct and the same guarantee as HRMS's catalogue: a `RoleTemplate` names exactly one
product, `PermissionCatalogue.all()` can only return this catalogue's own codes, and `validate()`
refuses at import time if a template grants a foreign product's permission without declaring
`cross_product`. The guard is exercised by a test, so a wrong-product grant is a red build rather
than a super-grant discovered during an audit.

The vocabulary is deliberately small — marketing analytics is read-dominated. Reads default to
ORG because the product's data is org-wide analytics, not per-person records: an analyst is meant
to see the whole picture. Writes carry an ORG ceiling too, and the routes set an ORG floor.
"""

from __future__ import annotations

from platform_core.catalogue import (
    PermissionCatalogue,
    PermissionSpec,
    ProductRegistration,
    RoleTemplate,
    validate,
)

PRODUCT = "marketing"


# --------------------------------------------------------------------- marketing catalogue

MARKETING = PermissionCatalogue(
    PRODUCT,
    [
        # Reading marketing analytics is an org-wide act — an analyst sees every campaign,
        # audience and the roll-ups — so it defaults to ORG. There is no meaningful "own campaign"
        # reading in this product, which is exactly why the default is not OWN.
        PermissionSpec(
            "analytics",
            "campaign",
            "read",
            "See campaigns, audiences and marketing analytics",
            "ORG",
            "ORG",
        ),
        # Creating and editing campaigns and audiences. The routes also set an ORG floor, so a
        # narrower grant cannot reach the write endpoints.
        PermissionSpec(
            "analytics",
            "campaign",
            "write",
            "Create and edit campaigns and audiences",
            "ORG",
            "ORG",
        ),
    ],
)


MARKETING_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="MARKETING_ADMIN",
        name="Marketing administrator",
        product=PRODUCT,
        description=(
            "Runs the marketing function: reads every campaign and audience and creates and edits "
            "them. Built from the Marketing catalogue alone — administering marketing is not the "
            "same job as owning the account."
        ),
        grants=MARKETING.pick(
            ("marketing.analytics.campaign.read", "ORG"),
            ("marketing.analytics.campaign.write", "ORG"),
        ),
    ),
    RoleTemplate(
        code="MARKETING_ANALYST",
        name="Marketing analyst",
        product=PRODUCT,
        description="Every person with a Marketing seat. Reads the org's campaigns and analytics.",
        is_default=True,
        grants=MARKETING.pick(
            ("marketing.analytics.campaign.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


validate(MARKETING_ROLE_TEMPLATES)


MARKETING_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=MARKETING, role_templates=MARKETING_ROLE_TEMPLATES
)
