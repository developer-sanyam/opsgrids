"""The permission catalogue and role templates for the `notification` product.

Copied in shape from support- and account-service, and for the same load-bearing reason: a
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

from dataclasses import dataclass, field

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"

PRODUCT = "notification"


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
        """Every permission in this catalogue, at `scope` — the safe form of "give the admin
        everything", bound to one product by construction so it cannot reach another's."""
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


# --------------------------------------------------------------------- notification catalogue
#
# Notification is an administrative surface, not a per-user one: templates and rules govern what
# the whole organization sends, so reading and writing them top out at ORG and the routes floor
# there. There is no "my own templates" reading to protect.
#
# `message.read` is split from `rule.write` on purpose. Seeing what was sent to a customer means
# reading the rendered body — the invoice total, the payslip line, the address — so it is the
# more sensitive of the two and a role can hold rule authoring without it.

NOTIFICATION = PermissionCatalogue(
    PRODUCT,
    [
        PermissionSpec(
            "delivery",
            "template",
            "read",
            "See the message templates and the automation rules",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "delivery",
            "template",
            "write",
            "Author templates and the rules that fire them",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "delivery",
            "message",
            "read",
            "Read what was sent, to whom, and whether it arrived",
            "ORG",
            "ORG",
            is_dangerous=True,
        ),
        PermissionSpec(
            "delivery",
            "message",
            "send",
            "Send a message directly, outside any rule",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "delivery",
            "preference",
            "write",
            "Record or lift a recipient's suppression",
            "ORG",
            "ORG",
        ),
    ],
)


NOTIFICATION_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="NOTIFICATION_ADMIN",
        name="Notification administrator",
        product=PRODUCT,
        description=(
            "Runs what the organization sends: templates, rules, suppression, and the delivery "
            "record. Built from the notification catalogue alone."
        ),
        grants=NOTIFICATION.all("ORG"),
    ),
    RoleTemplate(
        code="NOTIFICATION_AUTHOR",
        name="Notification author",
        product=PRODUCT,
        description=(
            "Writes templates and rules without reading the delivered messages — authoring an "
            "automation is not the same job as reading a customer's payslip email."
        ),
        is_default=True,
        grants=NOTIFICATION.pick(
            ("notification.delivery.template.read", "ORG"),
            ("notification.delivery.template.write", "ORG"),
        ),
    ),
    RoleTemplate(
        code="NOTIFICATION_VIEWER",
        name="Notification viewer",
        product=PRODUCT,
        description="Read-only visibility into templates and rules. No message bodies.",
        grants=NOTIFICATION.pick(
            ("notification.delivery.template.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- engage catalogue
#
# Engage is a second product surface on this service. `notification` stays the platform's
# transactional plumbing — rules, templates, the message API every product sends through.
# `engage` is what a customer buys: their own sending identity, their audience and their
# campaigns. One service, two catalogues, because a product is an entitlement and a vocabulary,
# not a process; the gateway gates `/api/v1/engage` on the `engage` entitlement while the
# existing notification routes stay platform plumbing nobody is billed for.
#
# Everything here tops out at ORG for the same reason the notification catalogue does: a sending
# domain and a sender belong to the organization, not to whoever happened to add them.

ENGAGE_PRODUCT = "engage"

ENGAGE = PermissionCatalogue(
    ENGAGE_PRODUCT,
    [
        # The connection holds a credential that can send mail as the customer's own domain,
        # so writing it is the most dangerous thing in this slice. It is its own resource
        # rather than folded into a broader "settings" permission for that reason alone.
        PermissionSpec(
            "setup",
            "connection",
            "read",
            "See how the organization sends email",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "setup",
            "connection",
            "write",
            "Connect or disconnect the email provider",
            "ORG",
            "ORG",
            is_dangerous=True,
        ),
        PermissionSpec(
            "setup",
            "domain",
            "read",
            "See sending domains and their DNS state",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "setup",
            "domain",
            "write",
            "Add a sending domain and verify its DNS",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "setup",
            "sender",
            "read",
            "See the senders mail can go out as",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "setup",
            "sender",
            "write",
            "Add and edit senders, and run the test send that makes one usable",
            "ORG",
            "ORG",
        ),
        # WhatsApp is a second channel on the same sending-identity setup, not a separate
        # product: the credential is as sensitive as the email connection's, so it gets its own
        # resource for the same reason `connection` did.
        PermissionSpec(
            "setup",
            "whatsapp",
            "read",
            "See how the organization sends WhatsApp messages",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "setup",
            "whatsapp",
            "write",
            "Connect or replace the organization's WhatsApp Business account",
            "ORG",
            "ORG",
            is_dangerous=True,
        ),
        # The audience. Contacts are an organization's list of people, not a salesperson's own
        # book, so they top out at ORG like everything else here. Addresses are sealed at rest
        # and every read returns them masked; there is no unmasking route in this slice.
        PermissionSpec(
            "audience",
            "contact",
            "read",
            "See the organization's contacts, with addresses masked",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "audience",
            "contact",
            "write",
            "Add, edit, import and delete contacts",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "audience",
            "suppression",
            "read",
            "See which addresses may not be mailed, and why",
            "ORG",
            "ORG",
        ),
        # Dangerous because lifting a suppression makes an address mailable again — the one
        # write here that can put mail in front of someone who asked not to receive it.
        PermissionSpec(
            "audience",
            "suppression",
            "write",
            "Suppress an address by hand, or lift a manual suppression",
            "ORG",
            "ORG",
            is_dangerous=True,
        ),
    ],
)


ENGAGE_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="ENGAGE_ADMIN",
        name="Engage administrator",
        product=ENGAGE_PRODUCT,
        description=(
            "Owns how the organization sends: the provider connection, its domains and its "
            "senders. Built from the Engage catalogue alone."
        ),
        grants=ENGAGE.all("ORG"),
    ),
    RoleTemplate(
        code="ENGAGE_VIEWER",
        name="Engage viewer",
        product=ENGAGE_PRODUCT,
        description=(
            "Every person with an Engage seat. Reads the sending setup without being able to "
            "change it — a new seat should not be able to repoint the organization's mail."
        ),
        is_default=True,
        grants=ENGAGE.pick(
            ("engage.setup.connection.read", "ORG"),
            ("engage.setup.domain.read", "ORG"),
            ("engage.setup.sender.read", "ORG"),
            ("engage.setup.whatsapp.read", "ORG"),
            ("engage.audience.contact.read", "ORG"),
            ("engage.audience.suppression.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product. Called at import and asserted by a
    test, so the failure mode is a red build rather than a silent super-grant."""
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


validate(NOTIFICATION_ROLE_TEMPLATES)
validate(ENGAGE_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What this product hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


NOTIFICATION_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=NOTIFICATION, role_templates=NOTIFICATION_ROLE_TEMPLATES
)

#: The second registration this module exposes. `tools/export_permissions.py` reads every
#: module-level name ending in `_REGISTRATION`, so declaring it is the whole of "ship engage's
#: manifest" — one service contributing two products is a shape the exporter already merges by
#: product, the mirror of `hrms`, where two services contribute one.
ENGAGE_REGISTRATION = ProductRegistration(
    product=ENGAGE_PRODUCT, catalogue=ENGAGE, role_templates=ENGAGE_ROLE_TEMPLATES
)
