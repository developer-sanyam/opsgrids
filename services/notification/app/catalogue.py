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

from platform_core.catalogue import (
    PermissionCatalogue,
    PermissionSpec,
    ProductRegistration,
    RoleTemplate,
    validate,
)

PRODUCT = "notification"


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
        PermissionSpec(
            "audience",
            "list",
            "read",
            "See contact lists and who is on them",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "audience",
            "list",
            "write",
            "Create, rename and delete contact lists, and add or remove members",
            "ORG",
            "ORG",
        ),
        # Campaigns. Drafting and sending are separate grants: writing a draft reaches nobody,
        # sending one puts mail in front of the whole audience under the organization's name.
        PermissionSpec(
            "campaign",
            "campaign",
            "read",
            "See campaigns, their recipients and their delivery stats",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "campaign",
            "campaign",
            "write",
            "Draft, edit and delete campaigns, and cancel one in flight",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "campaign",
            "campaign",
            "send",
            "Send a campaign to its audience",
            "ORG",
            "ORG",
            is_dangerous=True,
        ),
        # A WhatsApp template lives in the same `ProviderTemplate` row `notification`'s own
        # `delivery.template.*` reads and writes — this is Engage's own name for the same read
        # and write, so an organization that has bought Engage but not the transactional
        # `notification` product can still see and register the templates it sends through its
        # own WhatsApp account. Its own module rather than folded into `setup.whatsapp.*`: that
        # permission protects the account credential, this protects the wording sent through it,
        # and the two are held by different people on a larger team.
        PermissionSpec(
            "whatsapp",
            "template",
            "read",
            "See the organization's WhatsApp templates and their real approval status",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "whatsapp",
            "template",
            "write",
            "Register or update a WhatsApp template for Meta's review",
            "ORG",
            "ORG",
        ),
        # An email template lives in the same `Template` row `notification`'s own
        # `delivery.template.*` reads and writes — this is Engage's own name for the same read
        # and write, so an organization that has bought Engage but not the transactional
        # `notification` product can still see and edit the email templates it sends through its
        # own account. Its own module, the same shape as `whatsapp.template.*` above: that pair
        # protects the WhatsApp wording, this protects the email wording, and neither is folded
        # into `setup.connection.*`, which protects the sending identity rather than the text.
        PermissionSpec(
            "email",
            "template",
            "read",
            "See the organization's email templates",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "email",
            "template",
            "write",
            "Create and edit an email template",
            "ORG",
            "ORG",
        ),
        # The WhatsApp inbox: a customer's two-way conversation with the organization, not a
        # one-shot campaign send. `conversation.read`/`.write` are the triage surface (the list,
        # the thread, assignment, closing); `message.reply` is its own grant because sending words
        # to a real customer under the organization's WhatsApp number is a materially bigger
        # action than looking at a list of threads.
        PermissionSpec(
            "inbox",
            "conversation",
            "read",
            "See WhatsApp conversations and their message threads, bodies masked",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "inbox",
            "conversation",
            "write",
            "Assign and close WhatsApp conversations",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "inbox",
            "message",
            "reply",
            "Send a free-text WhatsApp reply inside the 24-hour service window",
            "ORG",
            "ORG",
        ),
        # Reading a customer's actual words, unmasked. Dangerous in the same sense
        # `crm.book.contact.read_sensitive` is, and paired the same way: the permission is the
        # gate (no step-up since 2026-10-15, ADR-0023 addendum); every reveal is audited.
        PermissionSpec(
            "inbox",
            "message",
            "unmask",
            "Reveal the real text of a customer's WhatsApp message (audited every time)",
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
            ("engage.whatsapp.template.read", "ORG"),
            ("engage.email.template.read", "ORG"),
            ("engage.audience.contact.read", "ORG"),
            ("engage.audience.suppression.read", "ORG"),
            ("engage.audience.list.read", "ORG"),
            ("engage.campaign.campaign.read", "ORG"),
            ("engage.inbox.conversation.read", "ORG"),
        ),
    ),
    RoleTemplate(
        code="ENGAGE_INBOX_AGENT",
        name="Engage inbox agent",
        product=ENGAGE_PRODUCT,
        description=(
            "Works the WhatsApp inbox: reads conversations, replies inside the service window, "
            "assigns and closes threads. No unmasking of a customer's message history, and no "
            "sending setup or campaigns."
        ),
        grants=ENGAGE.pick(
            ("engage.inbox.conversation.read", "ORG"),
            ("engage.inbox.conversation.write", "ORG"),
            ("engage.inbox.message.reply", "ORG"),
            # An agent replying inside the service window still needs to see which templates
            # are approved to reach a customer once that window has closed — reading, not
            # registering one, which stays the administrator's job.
            ("engage.whatsapp.template.read", "ORG"),
            # Mirrors the WhatsApp grant above, for the same reason and the same symmetry: an
            # inbox agent may need to see the wording of an email template referenced from a
            # conversation without holding a `notification` seat of their own.
            ("engage.email.template.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


validate(NOTIFICATION_ROLE_TEMPLATES)
validate(ENGAGE_ROLE_TEMPLATES)


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
