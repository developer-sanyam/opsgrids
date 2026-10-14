"""Approval's slice of the `core` permission catalogue (ADR-0006 §11).

Approval is a platform surface, not a product (ADR-0006 §1) — its permissions therefore belong
to the **`core`** catalogue account-service already declares, not to a catalogue of their own.
Two services contributing to one product's vocabulary is the normal shape (`hrms.json` already
merges `attendance` and `employee`; ADR-0006 §11 cites it): each service declares its own slice
as data, `tools/export_permissions.py` unions grants per role-template code across every
service's manifest, and account never imports this module — the manifest is the seam.

This module is **not run** by this change. Declaring the catalogue is this slice's job; wiring
it into `libs/contracts/permissions/core.json` and rolling it out to live tenants
(ADR-0006 §11, ADR-0007) is the orchestrator's, once every service's slice exists.

The structure — and the reason it looks exactly like `account/app/catalogue.py` and
`attendance/app/catalogue.py` rather than importing either — is the same one those two modules
already state: in the system this platform replaces, the HR admin role was "every permission
that exists", so the moment a second product registered its permissions the HR admin silently
held them too. Here that is impossible rather than merely discouraged — a `RoleTemplate` names
exactly one product, and `validate()` refuses at import time (asserted by a test) if a template
holds a foreign product's permission without declaring `cross_product`.
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


# --------------------------------------------------------------------- approval's core slice

APPROVAL = PermissionCatalogue(
    PRODUCT,
    [
        PermissionSpec(
            "approval",
            "request",
            "read",
            "See approval requests: OWN reaches requests raised or eligible on, and the "
            "caller's own chain; ORG reaches every request and any employee's chain",
            "OWN",
            "ORG",
        ),
        PermissionSpec(
            "approval",
            "request",
            "decide",
            "Decide a pending request: OWN where the routing chain makes the caller eligible; "
            "ORG additionally decides unassigned steps",
            "OWN",
            "ORG",
        ),
        PermissionSpec(
            "approval",
            "delegation",
            "write",
            "Create and revoke delegations: OWN only where the caller is the delegator; ORG "
            "for any delegator",
            "OWN",
            "ORG",
        ),
        # No "own" reading: SLA policy is organization-wide configuration, never one person's.
        PermissionSpec(
            "approval", "policy", "write", "Edit the SLA policy for a request type", "ORG", "ORG"
        ),
    ],
)


APPROVAL_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="MEMBER",
        name="Member",
        product=PRODUCT,
        description=(
            "Everyone raises requests, decides what the routing chain sends them, and may hand "
            "over while away. Holding decide:OWN is not authority by itself — ADR-0006 §6's "
            "eligibility check is what actually decides who may answer."
        ),
        is_default=True,
        grants=APPROVAL.pick(
            ("approval.request.read", "OWN"),
            ("approval.request.decide", "OWN"),
            ("approval.delegation.write", "OWN"),
        ),
    ),
    RoleTemplate(
        code="ORG_ADMIN",
        name="Administrator",
        product=PRODUCT,
        description=(
            "The organization's administrator. SLA policy is organization configuration, not "
            "billing, so it is in scope for the role defined as people and structure."
        ),
        grants=APPROVAL.all("ORG"),
    ),
    RoleTemplate(
        code="ORG_OWNER",
        name="Organization owner",
        product=PRODUCT,
        description=(
            "Account's template already grants every `core` permission at ORG over its own "
            "specs; this is approval's contribution to that union. It is what guarantees every "
            "organization ends ADR-0007's reconcile with at least one holder of decide:ORG, so "
            "the unassigned queue always has an owner."
        ),
        grants=APPROVAL.all("ORG"),
    ),
    RoleTemplate(
        code="APPROVAL_ADMIN",
        name="Approvals administrator",
        product=PRODUCT,
        description=(
            "All four approval permissions at ORG, nothing else. HRMS's HR_ADMIN cannot be "
            "granted core.* directly (ADR-0000 §5's corollary — a product's admin role is built "
            "from that product's own catalogue), so an HR administrator who needs the unassigned "
            "queue is given this role alongside HR_ADMIN. Deliberately not the product's "
            "admin-most template: `ORG_OWNER` already holds every `core` grant, so marking this "
            "one admin-most too would be redundant and it is not auto-granted on activation."
        ),
        grants=APPROVAL.all("ORG"),
    ),
]


# --------------------------------------------------------------------- the guard


validate(APPROVAL_ROLE_TEMPLATES)


CORE_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=APPROVAL, role_templates=APPROVAL_ROLE_TEMPLATES
)
