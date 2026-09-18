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

`Message` carries the rendered subject and body rather than a reference to the template that
produced them. A template edited next month must not change what last month's message says it
said: the sent text is a record, not a view.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

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
    #: email | whatsapp.
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
    template_code: Mapped[str] = mapped_column(String(80), nullable=False)
    #: email | whatsapp.
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Suppression bucket a recipient can opt out of, e.g. ``billing``, ``marketing``.
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="operational")

    #: Dotted path into the event payload holding the recipient address.
    recipient_path: Mapped[str | None] = mapped_column(String(120))
    #: A fixed recipient, for notify-the-team rules.
    recipient_literal: Mapped[str | None] = mapped_column(String(254))
    #: Dotted path to a value that makes this send unique. Optional.
    dedupe_key_path: Mapped[str | None] = mapped_column(String(120))

    #: An inactive rule is left in place and skipped — an audit trail of what used to fire.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        # The consumer's hot path: every message asks "which rules watch this event type?".
        Index("ix_rules_org_event", "organization_id", "event_type", "active"),
        UniqueConstraint("organization_id", "code", name="uq_rules_org_code"),
    )


class Message(Base, TenantBase):
    """One rendered notification and where it got to.

    State is `pending` → `sending` → `sent` | `failed`, or `suppressed` without ever leaving
    the consumer. The consumer only ever writes `pending` or `suppressed`; the dispatcher owns
    every transition after that, so exactly one process talks to a provider and no transaction is
    open while it does.

    `subject` and `body` are the **rendered** text, stored rather than re-derived. A template
    edited next month must not change what last month's message says it said.
    """

    __tablename__ = "messages"

    #: email | whatsapp.
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="operational")
    to_address: Mapped[str] = mapped_column(String(254), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, nullable=False)

    #: pending | sending | sent | failed | suppressed.
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    #: Which rule produced this, and which event it reacted to. Null for a direct API send —
    #: the platform can notify without an automation behind it.
    rule_code: Mapped[str | None] = mapped_column(String(80))
    event_type: Mapped[str | None] = mapped_column(String(120))
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: Stable identity for "this exact send". Unique per organization when present.
    dedupe_key: Mapped[str | None] = mapped_column(String(200))

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Provider-side identifier, kept for support requests about a specific message.
    provider_message_id: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        # The dispatcher's claim query: pending rows for this org, oldest first.
        Index("ix_messages_org_state", "organization_id", "state", "created_at"),
        Index("ix_messages_org_channel", "organization_id", "channel"),
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


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
