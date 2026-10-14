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

from platform_core.catalogue import (
    PermissionCatalogue,
    PermissionSpec,
    ProductRegistration,
    RoleTemplate,
    validate,
)

PRODUCT = "core"


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
        description=("Document's contribution to that union is the organization's image library."),
        grants=DOCUMENT.all("ORG"),
    ),
    RoleTemplate(
        code="ORG_ADMIN",
        name="Administrator",
        product=PRODUCT,
        description=(
            "The image library is organization content rather than billing, so it belongs to "
            "the same role."
        ),
        grants=DOCUMENT.all("ORG"),
    ),
    # **`MEMBER` gets neither grant, deliberately.** An ordinary employee has no reason to list
    # or upload to the organization's asset library, and "the images are public anyway so read
    # is harmless" is an argument for adding a grant that nothing uses.
    #
    # There is also no storefront role here, because no `storefront` product is registered
    # anywhere in the platform. When one is, whether store editing gets its own role is that
    # ADR's question, not this one's.
]


# --------------------------------------------------------------------- the guard


validate(DOCUMENT_ROLE_TEMPLATES)


CORE_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=DOCUMENT, role_templates=DOCUMENT_ROLE_TEMPLATES
)
