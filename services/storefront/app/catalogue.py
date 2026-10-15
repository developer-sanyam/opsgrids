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

from platform_core.catalogue import (
    PermissionCatalogue,
    PermissionSpec,
    ProductRegistration,
    RoleTemplate,
    validate,
)

PRODUCT = "storefront"


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
        # ---------------------------------------------------------------- catalogue (gap 3)
        # One pair covers categories too, on ADR-0010 §9's "no fifth permission" precedent —
        # a category is reference data for the same catalogue a product belongs to, not a
        # separately-permissioned resource.
        PermissionSpec(
            "catalogue",
            "product",
            "read",
            "See the product and category catalogue — products, their variants and categories",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "catalogue",
            "product",
            "write",
            "Create, edit, archive and delete products, their variants, and categories",
            "ORG",
            "ORG",
        ),
        # ---------------------------------------------------------------- enquiries (gap 6)
        PermissionSpec(
            "sales",
            "enquiry",
            "read",
            "See enquiries and their status, with buyer contact details masked",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "sales",
            "enquiry",
            "write",
            "Change an enquiry's status (new, contacted, won, lost)",
            "ORG",
            "ORG",
        ),
        # `is_dangerous` stays False like the rest of this catalogue's note above; what gates
        # this route is the ORG floor, the permission itself (no step-up, ADR-0023 addendum),
        # and the audit row `services.unmask_enquiry_contact` writes on every call.
        PermissionSpec(
            "sales",
            "enquiry",
            "unmask",
            "Reveal an enquiry's real email and phone number (audited)",
            "ORG",
            "ORG",
        ),
        # Its own permission rather than folded into `write`: `write` moves a status, this
        # destroys a buyer's record for good (the erasure-request path).
        PermissionSpec(
            "sales",
            "enquiry",
            "delete",
            "Permanently erase an enquiry, with the buyer's contact details and WhatsApp opt-in "
            "it holds",
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
            "Runs the store: its published identity, its delivery map, its catalogue and its "
            "enquiries. Built from the Storefront catalogue alone — publishing a store is not "
            "the same job as owning the account."
        ),
        grants=STOREFRONT.all("ORG"),
    ),
    RoleTemplate(
        code="STOREFRONT_STAFF",
        name="Storefront staff",
        product=PRODUCT,
        is_default=True,
        description=(
            "Every person with a Storefront seat. Reads the store's settings, its delivery "
            "zones and its catalogue, including which postal codes were turned away, and "
            "changes neither. Holds no enquiry permission — enquiries carry buyer PII and stay "
            "with the admin role until a narrower reading is asked for."
        ),
        grants=STOREFRONT.pick(
            ("storefront.store.settings.read", "ORG"),
            ("storefront.delivery.zone.read", "ORG"),
            ("storefront.catalogue.product.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


validate(STOREFRONT_ROLE_TEMPLATES)


STOREFRONT_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=STOREFRONT, role_templates=STOREFRONT_ROLE_TEMPLATES
)
