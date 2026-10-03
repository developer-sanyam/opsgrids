"""Tables owned by the notification service — templates, rules, and what was sent.

Everything here lives in the `notification` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). This service reacts to other services' events, so the temptation to reach
into their tables is constant and refused everywhere: a rule names an **event type and a path
into that event's payload**, never a join. `support.tickets` is not readable from here and is not
supposed to be.

The shape:

- `Template`   — a body per channel, with `{{placeholders}}`.
- `Rule`       — *when this event happens, render this template to this recipient.* The
                 automation engine, stored as per-organization data rather than code.
- `Message`    — one rendered notification and its delivery state.
- `Delivery`   — **append-only** ledger of provider attempts.
- `Preference` — per-recipient, per-channel suppression.
- `ConsentEvent` — **append-only** ledger of who opted in or out of what, when, and through
                 which source (`app/consent.py` is the one place that reads it to decide).

`Message` carries the rendered subject and body rather than a reference to the template that
produced them. A template edited next month must not change what last month's message says it
said: the sent text is a record, not a view.

`EmailConnection`, `EmailDomain` and `Sender` are the **Engage** product surface — how an
organization sends as itself. They live in this schema rather than a new service's because
everything Engage reports on is a local join against `messages` and `deliveries`, and no service
may read another's tables. One schema, two product catalogues; a product is an entitlement and a
vocabulary, not a process.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import OrgScopedMixin, TenantBase, UUIDPrimaryKeyMixin, utcnow

SCHEMA = "notification"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Template(Base, TenantBase):
    """A named message body for one channel.

    `subject` is meaningful for email and ignored for WhatsApp, which has no subject line. It is
    nullable rather than empty-string so the distinction survives a round trip.

    Bodies hold `{{placeholders}}` resolved against the event payload at render time. Rendering
    is strict (`domain.render`): an unresolved placeholder raises. A customer reading
    "Dear {{name}}" is worse than a message that never left, because the undelivered one is
    visible in a queue and the other is visible to the customer.
    """

    __tablename__ = "templates"

    #: Stable code the rules refer to, e.g. ``ticket_acknowledged``. Unique per org and channel.
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    #: email | whatsapp | in_app.
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Email subject line. Null for channels that have none.
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        # Every index leads with `organization_id`: under RLS the policy qual is ANDed into
        # every query, so an index that did not lead with the tenant column would scan the
        # whole platform's rows and discard almost all of them (ADR-0002).
        Index("ix_templates_org_code", "organization_id", "code"),
        UniqueConstraint("organization_id", "code", "channel", name="uq_templates_org_code_chan"),
    )


class Rule(Base, TenantBase):
    """*When `event_type` happens, send `template_code` on `channel` to the recipient.*

    The automation engine, and deliberately **data rather than code**: adding "acknowledge every
    new ticket by email" is an INSERT, not a deploy. That is the whole difference between a
    platform that automates and a platform that has to be rebuilt to automate.

    `recipient_path` is a dotted path into the event payload (``requester_email``,
    ``customer.contact.phone``). `recipient_literal` is a fixed address for the
    notify-the-team case. Exactly one of the two is set, enforced in `domain.validate_rule`
    rather than by a check constraint, because the error belongs to the operator editing a rule
    and needs to say which field is wrong.

    `dedupe_key_path` names a payload field that makes a send unique. Two rules on the same
    event are two messages, but one event redelivered is one message: the consumer is idempotent
    by `processed_events`, and this is the second net for the case where an upstream service
    legitimately republishes.
    """

    __tablename__ = "rules"

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    #: The routing key this rule reacts to, e.g. ``support.desk.ticket.created``.
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    #: A `Template.code` whose body we render and send as free text. Null when the rule
    #: sends a provider-held template instead — exactly one of the two is set.
    template_code: Mapped[str | None] = mapped_column(String(80))
    #: email | whatsapp | in_app.
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Suppression bucket a recipient can opt out of, e.g. ``billing``, ``marketing``.
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="operational")

    #: Dotted path into the event payload holding the recipient address.
    recipient_path: Mapped[str | None] = mapped_column(String(120))
    #: A fixed recipient, for notify-the-team rules.
    recipient_literal: Mapped[str | None] = mapped_column(String(254))
    #: Dotted path to a **contact id** in the event payload. The recipient is then resolved by
    #: asking customer-service for that contact's address on this channel — the only way to
    #: reach a customer, because the CRM keeps their real number sealed and notification does
    #: not hold the key.
    #:
    #: This is the path a proactive customer notification uses. `recipient_path` still serves
    #: events that carry an address outright (a support ticket's requester email), and
    #: `recipient_literal` the notify-the-team case.
    recipient_contact_path: Mapped[str | None] = mapped_column(String(120))
    #: Dotted path to a value that makes this send unique. Optional.
    dedupe_key_path: Mapped[str | None] = mapped_column(String(120))

    #: A `ProviderTemplate.code` to send instead of rendering `template_code` as free text.
    #: Set for any rule that must reach a WhatsApp user outside the 24-hour window, which is
    #: every proactive notification. Null means free text, which stays correct for email.
    provider_template_code: Mapped[str | None] = mapped_column(String(80))

    #: An inactive rule is left in place and skipped — an audit trail of what used to fire.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        # The consumer's hot path: every message asks "which rules watch this event type?".
        Index("ix_rules_org_event", "organization_id", "event_type", "active"),
        UniqueConstraint("organization_id", "code", name="uq_rules_org_code"),
    )


class ProviderTemplate(Base, TenantBase):
    """A template registered with the **channel provider**, not text this service owns.

    The distinction from `Template` is the whole reason both exist. A `Template` is our body,
    rendered here and sent as free text. A `ProviderTemplate` is an artifact living at the
    provider: Meta holds the wording, reviews it, approves or rejects it, and we supply only the
    parameters. It is the only way to reach a WhatsApp user outside the 24-hour customer service
    window — which is to say, the only way to send a notification the customer did not just ask
    for, which is what "automation" means.

    Named `provider_template` rather than `whatsapp_template` because the shape is not
    WhatsApp's alone: SMS in India carries DLT-registered templates with the same
    register-then-reference lifecycle. The channel is a column, not a table name.

    `status` is the provider's answer, not ours. Only `approved` may be sent, and that is checked
    before staging rather than left to the provider, because Meta's refusal arrives as a 4xx the
    dispatcher classifies permanent — a dead message and an operator wondering why, when "that
    template is still in review" was knowable in advance.

    `variables` is a list of `{key, component, position, button_index, kind}` describing what the
    template expects. `key` is **our** vocabulary — the same name a rule's payload path resolves —
    so the rest of the service keeps speaking one language and the provider's positional
    ordering stays contained in this row.
    """

    __tablename__ = "provider_templates"

    #: Our stable code, referenced by a rule. Unique per organization and channel.
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    #: email | whatsapp.
    channel: Mapped[str] = mapped_column(String(20), nullable=False)

    #: The name the template is registered under at the provider, e.g. ``invoice_due_reminder``.
    provider_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The provider's own identifier, once known from a sync. Null until then.
    provider_template_id: Mapped[str | None] = mapped_column(String(200))
    #: Meta language code, e.g. ``en`` or ``en_US``. Part of the template's identity there.
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="en")
    #: utility | marketing | authentication. Drives pricing and consent obligations.
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="utility")
    #: positional | named. Declared when the template is created at the provider.
    parameter_format: Mapped[str] = mapped_column(String(20), nullable=False, default="positional")

    #: draft | pending | approved | rejected | paused | disabled. The provider's answer.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    #: Why the provider refused it, kept so an operator does not have to leave the product.
    status_reason: Mapped[str | None] = mapped_column(Text)
    #: When the status was last confirmed against the provider.
    status_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: List of {key, component, position, button_index, kind}. See the class docstring.
    variables: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    #: A copy of the provider's body text, for display only. Never rendered or sent — the
    #: provider holds the authoritative wording, and a stale copy that got sent would be a
    #: message we did not write.
    preview_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    #: high | medium | low | unknown. Meta's own quality rating, in our vocabulary.
    quality: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", server_default="unknown"
    )
    #: Meta's components JSON, whole, as last submitted or synced. Display and editing only.
    components: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: `[{index, name, kind, example}]` — the authoring view of the placeholders. `variables`
    #: above is the send path's mapping to payload keys and keeps its own shape.
    variable_specs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: When this template was last handed to Meta. Null for one that was only ever synced.
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: `[{status, at, source}]`, oldest first, capped by `w2_domain.HISTORY_LIMIT`.
    history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    __table_args__ = (
        Index("ix_provider_templates_org_code", "organization_id", "code"),
        Index("ix_provider_templates_org_meta_id", "organization_id", "provider_template_id"),
        Index("ix_provider_templates_org_channel", "organization_id", "channel", "status"),
        UniqueConstraint(
            "organization_id", "code", "channel", name="uq_provider_templates_org_code_chan"
        ),
    )


class ProviderAccount(Base, TenantBase):
    """One organization's connection to a channel provider.

    It exists for two jobs, and the first is routing. Meta posts every tenant's webhooks to a
    single URL — there is no per-tenant endpoint — and the only thing in the payload that says
    whose message it is, is `metadata.phone_number_id`. So that column is unique **globally**,
    not per organization: it is the routing key, and two tenants claiming one number would make
    an inbound message unattributable.

    Resolving it necessarily happens *before* a tenant is known, which row-level security cannot
    express. That read therefore runs on the service's SELECT-only BYPASSRLS directory reader —
    the same pattern account-service uses to resolve a person at sign-in, confined here to this
    one table.

    The second job is the credential. `access_token_enc` is the tenant's own WhatsApp token,
    sealed with `platform_core.pii.seal` like any other secret at rest — a token that can send
    messages as the customer's business is not less sensitive than a phone number. The App
    Secret and verify token are *not* here: the Meta App is the vendor's, so they are platform
    configuration and one of them signs every tenant's webhooks.
    """

    __tablename__ = "provider_accounts"

    #: email | whatsapp. A column so the table can hold the next channel without a new table.
    channel: Mapped[str] = mapped_column(String(20), nullable=False)

    #: Meta's WhatsApp Business Account id.
    waba_id: Mapped[str] = mapped_column(String(80), nullable=False)
    #: The sender. **Globally unique** — this is the webhook routing key. Null once the connection
    #: is disconnected: the routing claim is released so the number can be connected by another
    #: organization, and a webhook for it is unroutable (unknown number) until someone claims it
    #: again. The number it held is kept in `released_phone_number_id` for the record.
    phone_number_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    #: The number this connection held before it was disconnected. History only: nothing routes
    #: on it and it is not unique.
    released_phone_number_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    #: The human-readable number, for an operator to recognise. Not a secret and not a mask:
    #: it is the business's own published number, not a customer's.
    display_number: Mapped[str] = mapped_column(String(40), nullable=False, default="")

    #: The tenant's access token, sealed. Never returned by any API.
    access_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)

    #: connected | disconnected. A disconnected account holds no routing key (`phone_number_id`
    #: is null) and nothing is sent from it. (A legacy disconnected row that still carries a
    #: number keeps routing inbound webhooks, so its messages are not lost.)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="connected")

    #: The Facebook-scoped user id (`FB.login`'s `authResponse.userID`) of whoever completed
    #: Embedded Signup for this connection -- captured going forward only (nullable: every
    #: connection made before this column existed has none, and cannot be attributed to a Meta
    #: deauthorize or data-deletion callback). Not a phone number or email: an opaque id Meta
    #: mints per person per app, and the same id the deauthorize and data-deletion callbacks name.
    #: Global, not unique -- the same Facebook user can be the one who connected more than one
    #: organization's WhatsApp Business Account, and a deauthorize/data-deletion callback naming
    #: this id fans out to every organization it matches (`app/selectors.
    #: accounts_for_meta_user_id`, the same directory-reader shape as `phone_number_id`'s own
    #: lookup). See `docs/whatsapp/webhooks.md`.
    meta_user_id: Mapped[str | None] = mapped_column(String(40))

    __table_args__ = (
        Index("ix_provider_accounts_org_channel", "organization_id", "channel"),
        # Global, deliberately. See the class docstring: this is the routing key.
        UniqueConstraint("phone_number_id", name="uq_provider_accounts_phone_number_id"),
        # Partial: most rows have no `meta_user_id` (every connection made before it existed, and
        # any channel other than whatsapp), so indexing only the rows a deauthorize/data-deletion
        # lookup could ever match keeps the index small.
        Index(
            "ix_provider_accounts_meta_user_id",
            "meta_user_id",
            postgresql_where=text("meta_user_id IS NOT NULL"),
        ),
    )


class Message(Base, TenantBase):
    """One rendered notification and where it got to.

    State is `pending` → `sending` → `sent` | `failed`, or `suppressed` without ever leaving
    the consumer. The consumer only ever writes `pending` or `suppressed`; the dispatcher owns
    every transition after that, so exactly one process talks to a provider and no transaction is
    open while it does.

    `subject` and `body` are the **rendered** text, stored rather than re-derived. A template
    edited next month must not change what last month's message says it said.

    A `channel == "in_app"` row is also the whole of `GET /notifications/me` — `app/inbox.py`
    reads this same table, filtered to one recipient's own rows, rather than a second table kept
    in sync with it. Its `sent` means "in the feed"; there is no provider for it to fail against.
    """

    __tablename__ = "messages"

    #: email | whatsapp | in_app. `in_app` is the platform's own feed (`app/inbox.py`) rather
    #: than a provider — see `domain.CHANNELS`.
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="operational")
    #: An email, an E.164 phone number, or — for `channel == "in_app"` — the recipient's
    #: **membership id**. Generic on purpose: this column has never meant "email address", only
    #: "wherever the channel sends", the same reasoning `subject` already leans on for whatsapp.
    to_address: Mapped[str] = mapped_column(String(254), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, nullable=False)

    #: pending | sending | sent | delivered | read | failed | suppressed.
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    #: outbound | inbound. Every row this service has ever written is `outbound` — a customer's
    #: own message lives in `InboundMessage`, never here — so the column exists for symmetry
    #: with the conversation it may belong to rather than because this table ever holds the
    #: other value today. Kept rather than assumed so a later projection that reads `messages`
    #: alone (a thread view, an export) does not have to ask a second table what direction means.
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="outbound")
    #: The conversation this reply belongs to. Null for every message that is not a reply inside
    #: a WhatsApp conversation — a rule-driven notification or a campaign email has no thread.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: Which rule produced this, and which event it reacted to. Null for a direct API send —
    #: the platform can notify without an automation behind it.
    rule_code: Mapped[str | None] = mapped_column(String(80))
    event_type: Mapped[str | None] = mapped_column(String(120))
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: Stable identity for "this exact send". Unique per organization when present.
    dedupe_key: Mapped[str | None] = mapped_column(String(200))

    #: The provider template this was sent as, if any, and the exact parameter payload handed
    #: over. Stored rather than re-derived for the same reason `body` is: the provider may edit
    #: or retire a template, and "what did we actually send this customer" must survive that.
    provider_template_code: Mapped[str | None] = mapped_column(String(80))
    provider_template_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    #: Why a `suppressed` message was not sent, as a machine-readable code (`consent:no_opt_in`,
    #: `consent:opted_out`, `preference`, ...). Null on every message that was not suppressed —
    #: and on those suppressed before this column existed, which carry the state and no reason.
    suppression_reason: Mapped[str | None] = mapped_column(String(80))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Filled from provider receipts. Separate columns rather than only a state, because "when
    #: was it read" is a question analytics asks and a single state cannot answer.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The provider's own error code and title on a failure, kept so an operator sees what Meta
    #: said rather than "failed".
    provider_error_code: Mapped[str | None] = mapped_column(String(40))
    provider_error_detail: Mapped[str | None] = mapped_column(Text)
    #: Provider-side identifier, kept for support requests about a specific message.
    provider_message_id: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        # The dispatcher's claim query: pending rows for this org, oldest first.
        Index("ix_messages_org_state", "organization_id", "state", "created_at"),
        Index("ix_messages_org_channel", "organization_id", "channel"),
        # A receipt arrives knowing only the provider's message id, and the webhook has already
        # resolved the tenant, so the lookup leads with the organization like every other index
        # here (ADR-0002).
        Index(
            "ix_messages_org_provider_message_id",
            "organization_id",
            "provider_message_id",
        ),
        # The thread view: every outbound reply in one conversation, oldest first.
        Index("ix_messages_org_conversation", "organization_id", "conversation_id"),
        # `app/inbox.py`'s hot path: one recipient's own feed, newest first, keyset-paged on
        # `(created_at, id)`. `ix_messages_org_channel` alone would still work but forces a sort
        # over every message on the channel; this one is built for the query the feed actually
        # runs.
        Index(
            "ix_messages_org_channel_recipient",
            "organization_id",
            "channel",
            "to_address",
            "created_at",
        ),
        # Two rules on one event are two messages; one event twice is one message.
        UniqueConstraint("organization_id", "dedupe_key", name="uq_messages_org_dedupe"),
    )


class Delivery(Base, TenantBase):
    """One provider attempt against one message. **Append-only.**

    Nothing in `services.py` updates or deletes a row here. "Why did this customer not get their
    invoice?" is answered by reading the attempts, and an answer that can be edited is not an
    answer. The foreign key to `messages` stays inside this schema, which is the only kind
    allowed (ADR-0000 §10).
    """

    __tablename__ = "deliveries"

    message_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("notification.messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 1 for the first attempt. Monotonic per message.
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The transport that ran, e.g. ``smtp``, ``whatsapp_cloud``, ``log``.
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    #: sent | failed.
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_deliveries_org_message", "organization_id", "message_id"),
        UniqueConstraint("message_id", "attempt_no", name="uq_deliveries_message_attempt"),
    )


class Preference(Base, TenantBase):
    """Whether one recipient will accept one category on one channel.

    Absence means "send" — a platform that requires an opt-in row before it can tell a customer
    their invoice is due would deliver nothing on day one. A row exists to record a **refusal**.

    This is not a nicety. Sending marketing WhatsApp to someone who opted out is a regulatory
    problem, so suppression is checked at render time and again at dispatch: a rule can be edited
    between the two, and the later check is the one that is true.
    """

    __tablename__ = "preferences"

    #: The address this applies to — an email or a phone number in E.164.
    recipient: Mapped[str] = mapped_column(String(254), nullable=False)
    #: email | whatsapp.
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The suppression bucket, matching `Rule.category`. ``*`` suppresses every category.
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="*")
    #: True means "do not send". The row exists to record a refusal.
    suppressed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reason: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        Index("ix_preferences_org_recipient", "organization_id", "recipient", "channel"),
        UniqueConstraint(
            "organization_id",
            "recipient",
            "channel",
            "category",
            name="uq_preferences_org_recipient",
        ),
    )


class Conversation(Base, TenantBase):
    """One customer's WhatsApp thread with this organization.

    `wa_address_enc` is the customer's number, sealed like any other PII at rest —
    `wa_address_masked` is what an ordinary read returns, and `wa_address_index` is the keyed
    blind index that makes "does a conversation already exist for this number" one indexed
    equality rather than a decrypt of every row per inbound webhook. The unique constraint is on
    the index for the same reason `Contact.email_index`'s is: uniqueness of the normalised
    number without the database ever holding it in the clear.

    `last_customer_message_at` is the whole of the 24-hour customer service window (`webhooks.
    within_service_window`) — the fact that decides whether a reply may be free text or must be
    an approved template. It only ever moves forward (`webhooks` folds with `GREATEST`), because
    Meta redelivers and a batch is not guaranteed to arrive in order.

    `contact_id` is a plain UUID, never a foreign key (ADR-0000 §10, and `customer` is a
    different schema regardless): it names a row in `customer.contacts`, resolved by asking
    customer-service the same way a proactive send does (`app/contacts.py`), and it is null
    until that resolution runs or when it never finds a match — a stranger messaging the
    business is ordinary, not an error.

    `assignee_membership_id` is likewise a plain UUID naming a membership in `account`, not a
    foreign key, and not resolved to a name here — the inbox UI reads a name for it from
    whatever roster it already holds.
    """

    __tablename__ = "conversations"

    #: whatsapp today; a column rather than a fixed value because the shape (a sealed address,
    #: a service window, a contact link) is not WhatsApp's alone.
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="whatsapp")

    wa_address_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    wa_address_masked: Mapped[str] = mapped_column(String(40), nullable=False)
    wa_address_index: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Plain UUID naming a `customer.contacts` row. Null until resolved, or forever for a
    #: stranger. See the class docstring.
    contact_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: The 24-hour customer service window's clock. Null means the window has never opened —
    #: this conversation exists (an operator started it, or a message failed outside the
    #: window) but the customer has never actually written in.
    last_customer_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: open | closed. A triage state for the inbox, independent of the service window — a closed
    #: conversation still tracks `last_customer_message_at` and a reply reopens it.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")

    #: Plain UUID naming an `account.memberships` row. Null when unassigned.
    assignee_membership_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    __table_args__ = (
        # The webhook fold's hot path: does a conversation already exist for this number.
        UniqueConstraint(
            "organization_id",
            "channel",
            "wa_address_index",
            name="uq_conversations_org_channel_address",
        ),
        # The inbox list: open conversations, most recently active first.
        Index(
            "ix_conversations_org_status",
            "organization_id",
            "status",
            "last_customer_message_at",
        ),
        Index("ix_conversations_org_contact", "organization_id", "contact_id"),
    )


class InboundMessage(Base, TenantBase):
    """One message a customer sent, folded from a WhatsApp webhook.

    `provider_message_id` is Meta's `wamid`, unique per organization — the redelivery guard for
    the row itself, independent of `processed_events` (which guards the *fold*, keyed by the same
    id via `webhooks.InboundMessage.dedupe_id`, so the two never disagree in practice, but this
    constraint is what turns a bug in that derivation into a database error instead of a
    duplicate message in someone's inbox).

    Only `type == "text"` has a body. Meta's other shapes — media, interactive replies — are not
    parsed (`webhooks.parse_inbound`'s docstring says why), so `body_enc` is null. `body_masked`
    is a **fixed-length** placeholder, never derived from the real text: a length- or tail-
    revealing mask on free-form customer text leaks the one thing masking exists to hide, in a way
    a masked phone number or email does not — there is no fixed convention for "the last four
    characters of a sentence" the way there is for a card or an account number. Unmasking either
    field is `engage.inbox.message.unmask` — the dangerous, audited permission
    (`services.unmask_inbound_message`) — the only route that opens `body_enc` or `raw_enc`.

    `raw_enc` is the provider's own message object, whole, **sealed** — it carries `from`, the
    text body, and whatever Meta's next media or interactive shape puts in it, none of which may
    sit in the clear at rest. There is no plaintext `raw` column at all: a field this table does
    not yet know to strip (`context.from`, a location, a media caption) must not become a gap by
    omission the way an allowlist-of-things-to-redact would.

    `profile_name` is sealed the same way for the same reason — a WhatsApp display name is a
    customer's own chosen name, and `profile_name_masked` is what an ordinary read shows.
    """

    __tablename__ = "inbound_messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("notification.conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    #: text | unknown | image | ... — whatever Meta's `type` field said. Only `text` is
    #: interpreted; everything else is stored with `type` as given (see the class docstring).
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    body_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    #: A constant string when a body is sealed, empty when there is none — never a function of
    #: the real text's length or content. See `services._BODY_MASK_PLACEHOLDER`.
    body_masked: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    #: The provider's own message object, whole, sealed. See the class docstring.
    raw_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The sender's WhatsApp profile name, sealed. `None` when Meta included none.
    profile_name_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    #: `"J•••"` — first character, then a fixed run of bullets, never the name's real length.
    profile_name_masked: Mapped[str | None] = mapped_column(String(8))
    #: What `app/intent.py` judged the message to want (`intent.LABELS`), the action taken on it
    #: (`suppress` | `review` | `none`) and P(marketing opt-out). All null until classified, and
    #: forever null where the feature is off or the message is media or a keyword. A label and a
    #: probability, never the text -- so none of it is PII.
    intent: Mapped[str | None] = mapped_column(String(24))
    intent_action: Mapped[str | None] = mapped_column(String(12))
    intent_p_opt_out: Mapped[float | None] = mapped_column(Float)
    intent_classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "provider_message_id", name="uq_inbound_messages_org_provider_id"
        ),
        # The thread view: every inbound message in one conversation, oldest first.
        Index(
            "ix_inbound_messages_org_conversation",
            "organization_id",
            "conversation_id",
            "occurred_at",
        ),
    )


class InboundDeadLetter(Base, TenantBase):
    """One webhook item (a status or an inbound message) that failed to fold, parked here instead
    of failing the whole `POST` it arrived in.

    A single malformed or oversized item must not cost every other item in the same batch, or —
    worse — Meta's own retry of the *whole* body, which repeats the same failure forever and
    never lets the items beside it through either. `app/api/webhooks.py` wraps each item's fold in
    its own `try`/`except`, and only a **permanent** failure — one no amount of redelivery will
    ever fix — lands here; a transient one (a connection blip, a deadlock) is left to raise into a
    5xx instead, so Meta's own retry does the job this table does not need to.

    `error` is reduced to the exception's type name, its SQLSTATE and its constraint name — never
    `str(exc)`, which for a database error can echo a parameter value straight out of the query
    that failed (`services.record_dead_letter`'s docstring says why). `raw_enc` is sealed, the same
    way `InboundMessage.raw_enc` is: unlike an ordinary operator worklist, a permanent error is
    genuinely recoverable once whatever made it permanent is fixed (a schema widened, a bug
    patched), so the item's own payload is kept, sealed, rather than discarded — `app/
    replay_dead_letters.py` is the one place that reopens it, and every replay writes its own
    audit row.

    `provider_message_id` is unique per organization and `kind` — `services.record_dead_letter`
    upserts on it with `ON CONFLICT DO NOTHING` rather than a plain insert, so two redeliveries of
    the same permanently-broken item (or a replay failing again for a row a concurrent webhook has
    already re-parked) produce one row here, not two.
    """

    __tablename__ = "inbound_dead_letters"

    #: status | inbound. Which fold this item was headed for.
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The exception's type, SQLSTATE and constraint name only. Never `str(exc)`, never the
    #: payload.
    error: Mapped[str] = mapped_column(String(500), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The item's own raw provider object (a status or a message), sealed — what `app/
    #: replay_dead_letters.py` opens to retry the fold once the permanent error behind this row
    #: has been fixed.
    raw_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    __table_args__ = (
        Index("ix_inbound_dead_letters_org_created", "organization_id", "created_at"),
        # The park-side idempotency guard: `services.record_dead_letter` upserts on this rather
        # than inserting blind, so parking the same broken item twice (two redeliveries racing
        # each other, or a replay that fails again for a row a concurrent webhook already
        # re-parked) is one row, not a duplicate worklist entry.
        UniqueConstraint(
            "organization_id",
            "kind",
            "provider_message_id",
            name="uq_inbound_dead_letters_org_kind_provider_id",
        ),
    )


class EmailConnection(Base, TenantBase):
    """How one organization sends email.

    Deliberately not a row in `provider_accounts`. That table's only unique constraint is
    `phone_number_id` — Meta's webhook routing key, globally unique on purpose — and its
    WhatsApp columns are NOT NULL. Email has no routing key of that kind and needs transport
    settings that table has no room for, so borrowing it would mean weakening a constraint
    WhatsApp depends on in order to reuse three columns.

    `secret_enc` is an app password or an API key, sealed like any other secret at rest. No
    route returns it, not even masked: a masked secret is still a secret with a hint attached.
    """

    __tablename__ = "email_connections"

    #: google | microsoft | smtp | platform. The customer picks a name, not a port — the
    #: transport settings are derived from this (see `domain.transport_for`).
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    host: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=587)
    use_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)

    #: What this connection can be asked to send in a day. Derived from `kind` at creation and
    #: then editable, because a customer who raises their provider quota should be able to say so.
    daily_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=2000)

    #: connected | disconnected.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="connected")

    __table_args__ = (
        # One connection per organization for now. A second would make "which one sent this"
        # ambiguous before anything records the answer.
        UniqueConstraint("organization_id", name="uq_email_connection_org"),
    )


class EmailDomain(Base, TenantBase):
    """A domain an organization may send from, and the state of its DNS.

    `records` is the last checked record set — kind, type, fqdn, registrar name, value, status
    and instruction, one entry per record `domain.expected_records` says this domain needs —
    stored so the setup screen can show what DNS said the last time anyone asked rather than
    nothing between visits. It is re-derived from the organization's *current* connection every
    time `verify` runs (`services.record_domain_check`), never trusted as a frozen snapshot: a
    provider switch means a different SPF include and a different DKIM shape, and the last
    provider's "verified" must not quietly keep passing for the new one.
    """

    __tablename__ = "email_domains"

    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    #: pending | verified | failed. Derived from `records` by `domain.fold_domain_status`.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    records: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Unused since platform-managed sending was never built: nothing signs outgoing mail with
    #: this key, so a domain created today never has one (`services.create_domain` leaves it
    #: null). Kept — not dropped — because a destructive migration is not worth taking to remove
    #: a nullable column with no reader left; see `c3a1ecc45df8_engage_dkim_signing_key`.
    dkim_private_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary)

    #: The DKIM selector this domain's TXT/CNAME records are published at — `google` by default,
    #: matching Google Workspace's own default, and editable per domain because a customer
    #: rotating their key or running a second selector needs to point this at the new one.
    #: Required for `smtp`, where there is no sensible default: the selector is whatever the
    #: customer's own provider assigned them.
    dkim_selector: Mapped[str] = mapped_column(String(63), nullable=False, default="google")

    #: The SPF include host a customer's own SMTP provider publishes — `amazonses.com`,
    #: `sendgrid.net` — entered by them because this platform cannot know a self-hosted relay's
    #: SPF host the way it knows Google's and Microsoft's. Null until they say it, and only
    #: meaningful for an `smtp` connection; `google` and `microsoft` derive their SPF target from
    #: the connection kind alone (`domain.spf_target`) and ignore this column entirely.
    spf_include: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        UniqueConstraint("organization_id", "domain", name="uq_email_domain_org_domain"),
        # Leads with the tenant column like every index here: the RLS policy qual is ANDed into
        # every query, so one that did not would scan the platform and discard almost all of it
        # (ADR-0002).
        Index("ix_email_domains_org_status", "organization_id", "status"),
    )


class Sender(Base, TenantBase):
    """A named identity mail goes out as: Sales, Support, Accounts.

    `verified_at` is set by one thing only — a test send that actually reached the provider.
    Until then `usable` is False and a campaign may not select this sender. That gate is the
    whole point of the setup wizard: it converts "we filled in a form" into "mail left the
    building".

    `domain_id` references `email_domains` by plain UUID rather than a foreign key, for the
    same reason nothing else here does either — a constraint is fine inside this schema, but
    the rule that keeps a database-per-service split a config change is applied uniformly
    rather than case by case (ADR-0000 §10).
    """

    __tablename__ = "senders"

    domain_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str] = mapped_column(String(254), nullable=False)
    reply_to: Mapped[str] = mapped_column(String(254), nullable=False, default="")
    signature: Mapped[str] = mapped_column(Text, nullable=False, default="")
    department: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("organization_id", "address", name="uq_sender_org_address"),
        Index("ix_senders_org_domain", "organization_id", "domain_id"),
    )

    @property
    def usable(self) -> bool:
        """Whether a campaign may send as this sender."""
        return self.verified_at is not None


class Contact(Base, TenantBase):
    """Someone an organization's campaigns may reach — Engage's audience.

    The address is PII, so it is stored the way the platform stores PII (`platform_core.pii`):
    `email_enc` holds it sealed, `email_masked` is the only form an ordinary read returns, and
    `email_index` is the keyed blind index that makes "is this address already a contact"
    one indexed equality instead of a decrypt of every row. Uniqueness is on the index, so it is
    uniqueness of the normalised (trimmed, lower-cased) address without the database ever holding
    the address in the clear.

    Deleted for real rather than soft-deleted: removing someone from an audience is exactly the
    request whose honest answer is "the address is gone", and a tombstone holding the sealed
    address would also hold the unique slot a later re-import needs.
    """

    __tablename__ = "contacts"

    email_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    email_masked: Mapped[str] = mapped_column(String(254), nullable=False)
    email_index: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("organization_id", "email_index", name="uq_contact_org_email"),
        Index("ix_contacts_org_name", "organization_id", "name"),
    )


class Suppression(Base, TenantBase):
    """An address Engage must not send to, and why.

    Keyed by the same blind index as `Contact.email_index`, which is what lets
    `selectors.sendable_contacts` exclude suppressed contacts with a join on equality rather
    than a decrypt. It is deliberately not a column on `contacts`: an unsubscribe outlives the
    contact row — delete a contact, re-import them next month, and they must still be
    suppressed.

    Only `manual` rows may be lifted through the API. The other reasons are facts reported by
    the recipient or their mailbox provider, and un-suppressing a complaint is how a sending
    domain loses its reputation.
    """

    __tablename__ = "suppressions"

    email_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    email_masked: Mapped[str] = mapped_column(String(254), nullable=False)
    email_index: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "email_index", name="uq_suppression_org_email"),
        CheckConstraint(
            "reason IN ('unsubscribed', 'bounced', 'complained', 'manual')",
            name="reason",
        ),
    )


class ConsentEvent(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One fact about consent: *this address said yes (or no) to this purpose on this channel,
    then, through this source.* **Append-only.**

    Consent is a history, never a boolean somebody flips (Engage design spec §13): "who agreed to
    what, when, and how" is the question a regulator, a mailbox provider or Meta asks after a
    complaint, and an answer that can be edited is not an answer. So nothing in this service
    updates or deletes a row here — a change of mind is a **new** row — and the database enforces
    it rather than trusting the code: `UPDATE` and `DELETE` are revoked from the application role
    (`migrations/env.py`, `reapply_append_only`), the same table-privilege tool `audit_logs` and
    the console's `revenue_events` use. There is deliberately no `updated_at`, `deleted_at` or
    `TenantBase` here for the same reason: a column that implies a row can change invites the
    code that changes it.

    **The latest event for a subject wins**, per channel and purpose, ordered by `(occurred_at,
    created_at, id)`. `app/consent.py` is the one place that reads that ordering into a decision
    (`decide`); `selectors.sendable_contacts` restates the same predicate in SQL for the bulk
    audience read and a test pins the two together.

    The subject is the contact address, stored the way `suppressions` and `contacts` store it
    (`platform_core.pii`): `subject_enc` holds it sealed, `subject_masked` is the only form an
    ordinary read returns, and `subject_index` is the keyed blind index the decision looks it up
    by. **There is no plaintext address in this table.** The index is the *same* one
    `Contact.email_index`/`Suppression.email_index` carry for an email and `Conversation.
    wa_address_index` carries for a WhatsApp number, so a lookup needs no decrypt and the legacy
    stores' rows can be copied in without opening them.

    `purpose` is `marketing` or `transactional`. An opt-out from a STOP keyword or an
    unsubscribe link is written as **two rows**, one per purpose, so "the latest event per
    purpose" stays a single ordering with no cross-purpose special case.

    `source` says where the answer came from. The values a person may claim through the API are
    `checkout`, `web_form`, `import`, `admin` and `api`; the rest are written only by this
    service itself — `inbound_keyword` (a STOP/START reply), `unsubscribe_link`, `bounce`,
    `complaint` and `legacy_import` — and cannot be forged by a caller, the same reason
    `SuppressionIn` has no `reason` field. **`legacy_import` is unverified consent**: it marks
    an existing contact carried over when this ledger was introduced, with no record of how or
    when they agreed. It keeps existing campaigns working; it is not evidence of anything.

    `contact_id` is a plain UUID naming a `contacts` row, never a foreign key (a deleted contact
    must not delete the record that they once opted out — the suppression rule, applied here),
    and null whenever the subject is not, or is no longer, a contact.

    `idempotency_key` makes a retried write a no-op: unique per organization when present. The
    backfill (`app/consent_backfill.py`) derives one per legacy row so it is safe to re-run.
    """

    __tablename__ = "consent_events"

    #: whatsapp | email | sms.
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    #: marketing | transactional.
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)

    subject_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    subject_masked: Mapped[str] = mapped_column(String(254), nullable=False)
    subject_index: Mapped[str] = mapped_column(String(64), nullable=False)

    contact_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: opted_in | opted_out.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    #: Short free text or a reference — never the address itself. Nullable at the database
    #: because the service's own writers (a STOP keyword) have a source but no prose; the API
    #: refuses a caller-supplied event without one.
    evidence: Mapped[str | None] = mapped_column(String(500))
    #: The user who recorded it, when a person did. Null for events this service wrote itself.
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: When the person actually consented (or refused) — which is not always when it was
    #: recorded. What "latest" is ordered by.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        CheckConstraint("channel IN ('whatsapp', 'email', 'sms')", name="channel"),
        CheckConstraint("purpose IN ('marketing', 'transactional')", name="purpose"),
        CheckConstraint("status IN ('opted_in', 'opted_out')", name="status"),
        # The decision's own lookup: one subject's events on one channel and purpose, read newest
        # first. Leads with `organization_id` like every index here (ADR-0002).
        Index(
            "ix_consent_events_org_subject",
            "organization_id",
            "channel",
            "purpose",
            "subject_index",
            "occurred_at",
        ),
        # The history list, newest first.
        Index("ix_consent_events_org_occurred", "organization_id", "occurred_at"),
        # A retried write is one row. Partial, because most rows carry no key at all.
        Index(
            "uq_consent_events_org_idempotency",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )


class ContactList(Base, TenantBase):
    """A named slice of the audience a campaign can be addressed to.

    Membership is its own table rather than an array on the list, so "is this contact on that
    list" is a unique index and removing a contact takes their memberships with it.
    """

    __tablename__ = "contact_lists"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_contact_list_org_name"),)


class ContactListMember(Base, TenantBase):
    """One contact on one list. Foreign keys are fine here — both ends live in this schema — and
    cascade, so deleting a contact or a list leaves no dangling membership."""

    __tablename__ = "contact_list_members"

    list_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.contact_lists.id", ondelete="CASCADE"),
        nullable=False,
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.contacts.id", ondelete="CASCADE"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("list_id", "contact_id", name="uq_contact_list_member"),
        Index("ix_contact_list_members_org_list", "organization_id", "list_id", "contact_id"),
    )


class Campaign(Base, TenantBase):
    """One email sent to an audience: a list, or every sendable contact.

    `status` moves draft -> sending -> sent, and to cancelled from any state before sent, or to
    `paused` and back when the resumable send driver (`app/cron.py`) cannot find a route to send
    on (no connected provider, no verified sender). Only a draft is editable; what a campaign
    said once it started going out is a record. The counts are folded from `campaign_recipients`
    by the sender as it goes, so a stats read is one row.
    """

    __tablename__ = "campaigns"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sender_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    html_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    text_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: list | all_sendable. `list_id` is set exactly when the audience is a list.
    audience: Mapped[str] = mapped_column(String(20), nullable=False, default="all_sendable")
    list_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.contact_lists.id")
    )
    #: draft | scheduled | sending | paused | sent | cancelled.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Why the driver stopped and set `status='paused'` — no connected provider, no usable
    #: sender, and so on. Cleared when the campaign resumes. Empty otherwise.
    pause_reason: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    #: When the driver last paused this campaign. The driver retries a paused campaign no more
    #: often than `Settings.engage_campaign_pause_retry_seconds` after this, so a still-broken
    #: route is not hammered every poll — and an operator's explicit resume clears it so the
    #: very next tick tries again.
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When `services.expand_campaign` last turned the audience into `queued` recipient rows.
    #: Set once, in the same transaction as the inserts, even when nobody was sendable — the
    #: send driver (`app/cron.py`) reads this as "has expansion actually happened yet", never
    #: `recipient_count`, which is legitimately zero both before expansion and after an
    #: audience that expanded to nobody. `claim_campaign_batch` refuses to mark a campaign
    #: `sent` while this is null, and expands the campaign itself, in its own transaction,
    #: rather than wait for a `send_requested` event that may be late or lost — see its
    #: docstring for the race this closes.
    expanded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suppressed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Open and click tracking (slice E1). Off unless the operator turns it on: the Engage design
    #: sends through the customer's own provider and runs no tracking domain, so tracking is an
    #: opt-in that rewrites this campaign's links through the platform's signed tracking URLs.
    #: Only a draft is editable, so these are fixed for the life of a send.
    track_opens: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    track_clicks: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'scheduled', 'sending', 'paused', 'sent', 'cancelled')",
            name="status",
        ),
        CheckConstraint("audience IN ('list', 'all_sendable')", name="audience"),
        CheckConstraint("(audience = 'list') = (list_id IS NOT NULL)", name="audience_list"),
        Index("ix_campaigns_org_status", "organization_id", "status"),
    )


class CampaignRecipient(Base, TenantBase):
    """One contact a campaign was addressed to, and what happened.

    The address is copied, sealed, at expansion: the contact may be edited or deleted while the
    campaign is going out, and what this row records is who it was sent to. `(campaign_id,
    contact_id)` is unique, which is what makes expansion idempotent — a redelivered send event
    inserts nothing twice.

    `sending` is a lease, not a terminal state: the driver (`app/cron.py`) sets it (with
    `claimed_at`) when it claims a batch to send, outside any open transaction, and either
    resolves it to `sent`/`failed`/`suppressed` afterwards or — on a provider outage — releases
    it straight back to `queued` so the next tick retries. A row stuck at `sending` because a
    process died mid-batch is reclaimed once its lease (`Settings.engage_campaign_claim_lease_
    seconds`) is stale, which is what makes a crash resumable rather than a stall.

    `erased_at` is set once, by `services.erase_contact_campaign_copies`, when the contact this
    row was addressed to is deleted or otherwise erased. The row is never removed — a campaign's
    delivery history and stats must outlive the contact that received it, the same reason the
    address is copied here in the first place rather than joined from `contacts` — but from that
    moment `email_enc` no longer holds the real address: it is re-sealed over a placeholder, so
    the ciphertext column stays valid without the original plaintext being recoverable from it.
    `claim_campaign_batch` checks this before ever calling `pii.open_sealed`, so an erased row is
    resolved `failed` rather than attempted.
    """

    __tablename__ = "campaign_recipients"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Plain UUID: the contact may be deleted, and this row must outlive it.
    contact_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    email_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    email_masked: Mapped[str] = mapped_column(String(254), nullable=False)
    email_index: Mapped[str] = mapped_column(String(64), nullable=False)
    #: queued | sending | sent | failed | suppressed. `sending` is a claim lease, see above.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    #: Set when a batch claims this row. Null once it resolves to a terminal status.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When the contact's sealed address was crypto-shredded. Null for every row whose contact
    #: has not been deleted or erased.
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("campaign_id", "contact_id", name="uq_campaign_recipient"),
        CheckConstraint(
            "status IN ('queued', 'sending', 'sent', 'failed', 'suppressed')", name="status"
        ),
        Index("ix_campaign_recipients_org_campaign", "organization_id", "campaign_id", "status"),
        Index("ix_campaign_recipients_org_sent_at", "organization_id", "sent_at"),
        #: What `services.erase_contact_campaign_copies` scans: every recipient row across every
        #: campaign this contact was ever addressed by, in one organization.
        Index("ix_campaign_recipients_org_contact", "organization_id", "contact_id"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""


from app import (  # noqa: E402
    a1_models,  # noqa: F401
    e1_models,  # noqa: F401
    m1_models,  # noqa: F401
    w1_models,  # noqa: F401
)
