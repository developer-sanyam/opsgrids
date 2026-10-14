"""The permission catalogue and role templates for the `support` product.

Copied in shape from employee- and account-service, and for the same load-bearing reason: a
product's admin role is built from *that product's own* catalogue, never "every permission that
exists". The structure makes the wrong-product grant impossible rather than merely discouraged —
`PermissionCatalogue.all()` can only return this catalogue's own codes, and `validate()` refuses
at import time if a template holds a foreign product's permission without declaring
`cross_product`. A test exercises the guard, so a mistake is a red build, not an audit finding.

The construct is copied rather than imported from another service on purpose: a product declares
its own vocabulary, and importing account's module would couple this service across the very
boundary the platform exists to keep clean.
"""

from __future__ import annotations

from platform_core.catalogue import (
    PermissionCatalogue,
    PermissionSpec,
    ProductRegistration,
    RoleTemplate,
    validate,
)

PRODUCT = "support"


# --------------------------------------------------------------------- support catalogue
#
# The support queue is a shared surface: an agent works the whole queue, not only their own
# tickets. So both permissions top out at ORG and the routes floor reads and writes at ORG —
# there is no self-service "own tickets only" reading to protect, and a permission guarded by
# name alone would let any holder act. Per-assignee narrowing is a query filter (`?assignee=`),
# never a security boundary.

SUPPORT = PermissionCatalogue(
    PRODUCT,
    [
        PermissionSpec(
            "desk",
            "ticket",
            "read",
            "See the support queue: tickets, threads, agents, SLA policy and the overview",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "desk",
            "ticket",
            "write",
            "Reply to tickets, assign them, change their status, and create or edit them",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "desk",
            "agent",
            "write",
            "Add, edit and deactivate the agents who work the queue",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "desk",
            "sla_policy",
            "write",
            "Set the SLA policy governing the queue",
            "ORG",
            "ORG",
        ),
    ],
)


SUPPORT_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="SUPPORT_ADMIN",
        name="Support administrator",
        product=PRODUCT,
        description=(
            "Runs the support desk: reads the whole queue and every thread, replies and assigns, "
            "and is the only role that manages agents and the SLA policy. Built from the support "
            "catalogue alone — running the desk is not the same job as owning the account."
        ),
        grants=SUPPORT.pick(
            ("support.desk.ticket.read", "ORG"),
            ("support.desk.ticket.write", "ORG"),
            ("support.desk.agent.write", "ORG"),
            ("support.desk.sla_policy.write", "ORG"),
        ),
    ),
    RoleTemplate(
        code="SUPPORT_AGENT",
        name="Support agent",
        product=PRODUCT,
        description="Works the shared queue: reads every ticket, replies and assigns.",
        is_default=True,
        grants=SUPPORT.pick(
            ("support.desk.ticket.read", "ORG"),
            ("support.desk.ticket.write", "ORG"),
        ),
    ),
    RoleTemplate(
        code="SUPPORT_VIEWER",
        name="Support viewer",
        product=PRODUCT,
        description="Read-only visibility into the queue and overview — no replies, no assigns.",
        grants=SUPPORT.pick(
            ("support.desk.ticket.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


validate(SUPPORT_ROLE_TEMPLATES)


SUPPORT_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=SUPPORT, role_templates=SUPPORT_ROLE_TEMPLATES
)
