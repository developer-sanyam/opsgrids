"""Writes for the notification service.

Every function follows the platform shape: change the rows, record the audit entry, stage the
event — **all inside one transaction**. The caller owns the transaction and the commit, so a
crash between a state change and its event cannot leave one without the other.

Two callers, two shapes. Routes pass a `ScopeContext` and act as a principal. The consumer has
no principal — it acts on an event — so its entry points take an `organization_id` and stamp
`SYSTEM_ACTOR`. Both go through the same rendering and the same suppression check, which is the
point: a direct send cannot reach someone a rule-driven send would have skipped.

`Delivery` rows are **append-only**. Nothing here updates or deletes one.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from html import escape as html_escape
from typing import Any, Final, Literal, cast

from sqlalchemy import CursorResult, case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import (
    consent,
    dns_check,
    domain,
    e1_tracking,
    memberships,
    selectors,
    w1_services,
    webhooks,
    whatsapp_provider,
)
from app.config import Settings, settings
from app.models import (
    AuditLog,
    Campaign,
    CampaignRecipient,
    ConsentEvent,
    Contact,
    ContactList,
    ContactListMember,
    Conversation,
    Delivery,
    EmailConnection,
    EmailDomain,
    InboundDeadLetter,
    Message,
    OutboxEvent,
    Preference,
    ProviderAccount,
    ProviderTemplate,
    Rule,
    Sender,
    Suppression,
    Template,
)
from app.models import InboundMessage as InboundMessageRow
from app.targets import TargetResolver
from app.transports import (
    CAMPAIGN_SMTP_EXECUTOR,
    LogTransport,
    Outgoing,
    Receipt,
    SmtpTransport,
    Transport,
    TransportError,
    TransportRefused,
    TransportUnavailable,
)
from platform_core import pii
from platform_core.audit import record_audit, record_sensitive_read
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.cache import enforce_rate_limit
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    NotFoundProblem,
    UpstreamUnavailableProblem,
    ValidationProblem,
)
from platform_core.events import publish_event, schedule_event
from platform_core.logging import get_logger

log = get_logger("notification.services")

#: Published when a message is staged. The dispatcher consumes it — so sending is driven by the
#: outbox and the relay that already exist, rather than by a second polling loop with its own
#: privileged database role. The tenancy story comes free: the envelope carries
#: `organization_id`, and the dispatcher binds the GUC to it per message.
EVT_MESSAGE_QUEUED = "notification.delivery.message.queued"
EVT_MESSAGE_SENT = "notification.delivery.message.sent"
EVT_MESSAGE_FAILED = "notification.delivery.message.failed"
EVT_MESSAGE_DELIVERED = "notification.delivery.message.delivered"
EVT_MESSAGE_READ = "notification.delivery.message.read"

#: What the consumer stamps on rows it creates. A notification staged by an automation was not
#: created by a person, and recording one would be a lie in the audit trail.
SYSTEM_ACTOR = "system:notification"

#: Staged by the webhook fold once a customer message is durably recorded (`fold_inbound_message`
#: below). Deliberately **not** in `domain.SUBSCRIBABLE_EVENTS`: that list is what a `Rule` may
#: name and what the generic consumer fans an event out through `apply_rules`, and an inbound
#: message auto-triggering an outbound one is exactly the loop C14 exists to prevent (docs/
#: whatsapp/audit.md). Binding this routing key is therefore a deliberate code change — opening
#: it to the data-driven rule engine — never a row an operator can add on their own.
EVT_INBOUND_RECEIVED = "notification.whatsapp.inbound_received"


class OutsideServiceWindowProblem(ConflictProblem):
    """A free-text WhatsApp reply was refused because the 24-hour customer service window has
    closed. `remedy` is the machine-readable half of the body an ordinary `ConflictProblem`
    does not carry — the inbox composer switches to its template picker on that key rather than
    parsing `detail`."""

    problem_type = "outside-service-window"
    title = "Outside the WhatsApp service window"

    def to_problem(self, request: Request) -> dict[str, Any]:
        body = super().to_problem(request)
        body["remedy"] = "template"
        return body


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


# --------------------------------------------------------------------- templates & rules


async def upsert_template(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code: str,
    channel: str,
    subject: str | None,
    body: str,
    description: str = "",
    request: Request | None = None,
) -> Template:
    """Create or replace one template, keyed by `(code, channel)`. Idempotent.

    A template is *not* versioned, and that is a deliberate difference from a plan version: a
    plan version is a term of a contract and must be reconstructable years later, whereas a
    template is only ever a way of producing text. The text that matters is copied onto the
    message when it renders, so editing a template cannot rewrite what was already sent.
    """
    if channel not in domain.CHANNELS:
        raise ValidationProblem(f"{channel!r} is not a channel this service can reach")

    template = await selectors.get_template(session, ctx.organization_id, code, channel)
    if template is None:
        template = Template(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            code=code,
            channel=channel,
        )
        session.add(template)
    else:
        template.updated_by = str(ctx.principal.sub)
    template.subject = subject
    template.body = body
    template.description = description
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="template.upserted",
        entity="template",
        entity_id=template.id,
        after={"code": code, "channel": channel},
        request=request,
    )
    return template


async def upsert_provider_template(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code: str,
    channel: str,
    provider_name: str,
    language: str,
    category: str,
    parameter_format: str,
    status: str,
    status_reason: str | None,
    variables: list[dict[str, Any]],
    preview_text: str,
    request: Request | None = None,
) -> ProviderTemplate:
    """Register or update a template the provider holds. Keyed by `(code, channel)`. Idempotent.

    The variables are validated here rather than at send time, for the reason the validator
    itself explains: the provider fills parameters in order, so a gap does not leave a hole — it
    shifts every later value into the wrong placeholder and delivers a real message carrying
    somebody else's data. That is worth a 422 now.
    """
    if channel not in domain.CHANNELS:
        raise ValidationProblem(f"{channel!r} is not a channel this service can reach")
    if category not in domain.CATEGORIES:
        raise ValidationProblem(f"{category!r} is not a template category")
    if status not in domain.TEMPLATE_STATUSES:
        raise ValidationProblem(f"{status!r} is not a template status")
    if parameter_format not in (domain.FORMAT_POSITIONAL, domain.FORMAT_NAMED):
        # Validated here like the other three, so a typo is a 422 on the template rather than a
        # skipped send later. `named` is storable but not yet sendable; that is checked when the
        # payload is built, where the error can say why.
        raise ValidationProblem(f"{parameter_format!r} is not a template parameter format")

    try:
        parsed = [domain.TemplateVariable(**v) for v in variables]
        if parameter_format == domain.FORMAT_NAMED:
            # A positional template is wrong loudly — values land in the wrong placeholders. A
            # named one is wrong quietly: Meta matches on the label, so a missing or misspelled
            # name leaves that placeholder unfilled and the send still reports success. Caught
            # here rather than by a customer reading a message with a hole in it.
            domain.validate_named_variables(parsed)
        else:
            domain.validate_template_variables(parsed)
    except (domain.TemplateError, TypeError) as exc:
        raise ValidationProblem(str(exc)) from exc

    row = await selectors.get_provider_template(session, ctx.organization_id, code, channel)
    is_update = row is not None
    if row is None:
        row = ProviderTemplate(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            code=code,
            channel=channel,
        )
        session.add(row)
    else:
        row.updated_by = str(ctx.principal.sub)
    row.provider_name = provider_name
    row.language = language
    row.category = category
    row.parameter_format = parameter_format
    row.status = status
    row.status_reason = status_reason
    row.variables = variables
    row.preview_text = preview_text
    await session.flush()
    if is_update:
        # The `INSERT` path gets `updated_at` back for free — SQLAlchemy 2.0's
        # `eager_defaults="auto"` appends a server-side default to the statement's own
        # `RETURNING` clause. An `UPDATE`'s `onupdate=func.now()` is not fetched the same way,
        # so the flush leaves the attribute expired; the caller's own assembler reads it right
        # back (`_provider_template_out`), and an expired attribute read outside an `await` is
        # `MissingGreenlet` on an async session, not a stale value. Refresh the whole row rather
        # than only `updated_at`, the same call employee-service's own update path makes and for
        # the same reason: a flush can expire more than the one column being watched here today.
        await session.refresh(row)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="provider_template.upserted",
        entity="provider_template",
        entity_id=row.id,
        after={
            "code": code,
            "channel": channel,
            "provider_name": provider_name,
            "status": status,
            "category": category,
        },
        request=request,
    )
    return row


async def upsert_rule(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code: str,
    event_type: str,
    channel: str,
    template_code: str | None = None,
    provider_template_code: str | None = None,
    category: str = "operational",
    recipient_path: str | None = None,
    recipient_literal: str | None = None,
    recipient_contact_path: str | None = None,
    dedupe_key_path: str | None = None,
    active: bool = True,
    request: Request | None = None,
) -> Rule:
    """Create or replace one automation, keyed by `code`. Idempotent.

    The rule is validated against the template it names: a rule pointing at a template that does
    not exist would fire forever and send nothing, and the failure would surface as an absence,
    which is the hardest kind of bug to notice.
    """
    try:
        domain.validate_rule(
            channel=channel,
            event_type=event_type,
            recipient_path=recipient_path,
            recipient_literal=recipient_literal,
            recipient_contact_path=recipient_contact_path,
            template_code=template_code,
            provider_template_code=provider_template_code,
        )
    except domain.RuleError as exc:
        raise ValidationProblem(str(exc)) from exc

    if template_code and (
        await selectors.get_template(session, ctx.organization_id, template_code, channel) is None
    ):
        raise ValidationProblem(
            f"No {channel} template named {template_code!r} — create the template first"
        )
    if provider_template_code and (
        await selectors.get_provider_template(
            session, ctx.organization_id, provider_template_code, channel
        )
        is None
    ):
        raise ValidationProblem(
            f"No {channel} provider template named {provider_template_code!r} — "
            "register the template first"
        )

    rule = await selectors.get_rule(session, ctx.organization_id, code)
    if rule is None:
        rule = Rule(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            code=code,
        )
        session.add(rule)
    else:
        rule.updated_by = str(ctx.principal.sub)
    rule.event_type = event_type
    rule.template_code = template_code
    rule.provider_template_code = provider_template_code
    rule.channel = channel
    rule.category = category
    rule.recipient_path = recipient_path
    rule.recipient_literal = recipient_literal
    rule.recipient_contact_path = recipient_contact_path
    rule.dedupe_key_path = dedupe_key_path
    rule.active = active
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="rule.upserted",
        entity="rule",
        entity_id=rule.id,
        after={"code": code, "event_type": event_type, "active": active},
        request=request,
    )
    return rule


async def set_preference(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    recipient: str,
    channel: str,
    category: str = domain.CATEGORY_ALL,
    suppressed: bool = True,
    reason: str | None = None,
    request: Request | None = None,
) -> Preference:
    """Record or lift one recipient's refusal. Audited both ways.

    Lifting a suppression is as audited as setting one: "who turned marketing back on for this
    customer" is a question that gets asked after a complaint, and it needs an answer.
    """
    try:
        normalized = domain.normalize_recipient(channel, recipient)
    except domain.RuleError as exc:
        raise ValidationProblem(str(exc)) from exc

    pref = await selectors.get_preference(
        session, ctx.organization_id, normalized, channel, category
    )
    before = pref.suppressed if pref is not None else None
    if pref is None:
        pref = Preference(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            recipient=normalized,
            channel=channel,
            category=category,
        )
        session.add(pref)
    else:
        pref.updated_by = str(ctx.principal.sub)
    pref.suppressed = suppressed
    pref.reason = reason
    await session.flush()
    await _mirror_preference_into_ledger(
        session,
        ctx,
        recipient=normalized,
        channel=channel,
        category=category,
        suppressed=suppressed,
    )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="preference.set",
        entity="preference",
        entity_id=pref.id,
        before={"suppressed": before},
        after={"recipient": normalized, "channel": channel, "suppressed": suppressed},
        request=request,
    )
    return pref


async def _mirror_preference_into_ledger(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    recipient: str,
    channel: str,
    category: str,
    suppressed: bool,
) -> None:
    """Keep the consent ledger telling the same story as the `Preference` an operator just wrote.

    A refusal is appended as `opted_out` for the purposes its category covers. **Lifting** one
    appends `opted_in` only where consent existed before (`consent.restore`) — without it the
    ledger's own earlier refusal would outlive the operator's lift and the lift would do nothing,
    and with more than that it would manufacture marketing consent nobody gave. `in_app` has no
    ledger and is skipped.
    """
    if channel not in consent.CHANNELS:
        return
    subject = consent.subject_for(channel, recipient)
    purposes = consent.purposes_for_category(category)
    if suppressed:
        await consent.opt_out(
            session,
            organization_id=ctx.organization_id,
            subject=subject,
            purposes=purposes,
            source=consent.SOURCE_ADMIN,
            evidence=f"Operator set a preference refusal (category {category}).",
            recorded_by=ctx.principal.sub,
        )
    else:
        await consent.restore(
            session,
            organization_id=ctx.organization_id,
            subject=subject,
            purposes=purposes,
            source=consent.SOURCE_ADMIN,
            evidence=f"Operator lifted a preference refusal (category {category}).",
            recorded_by=ctx.principal.sub,
        )


# --------------------------------------------------------------------- staging a message


async def stage_message(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    channel: str,
    category: str,
    to_address: str,
    subject: str | None,
    body: str,
    created_by: str,
    rule_code: str | None = None,
    event_type: str | None = None,
    source_event_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
    actor: dict[str, Any] | None = None,
    provider_template_code: str | None = None,
    provider_template_payload: dict[str, Any] | None = None,
) -> Message | None:
    """Stage one already-rendered message, suppression and de-duplication applied.

    Returns None when the send was de-duplicated — an event legitimately republished, or a
    redelivery that got past `processed_events`. A suppressed recipient *does* get a row, in the
    `suppressed` state: "we deliberately did not tell this customer" is a fact worth keeping, and
    an absent row is indistinguishable from a rule that never fired.

    Nothing here touches a network. The message is committed as `pending` and the dispatcher
    picks it up from the queued event.
    """
    if dedupe_key and await selectors.find_by_dedupe_key(session, organization_id, dedupe_key):
        log.info("notification.deduplicated", dedupe_key=dedupe_key, rule=rule_code)
        return None

    suppressions = await selectors.suppressions_for(session, organization_id, to_address)
    suppressed = domain.is_suppressed(suppressions, channel=channel, category=category)
    suppression_reason = consent.STORED_PREFERENCE if suppressed else None
    if not suppressed:
        # The consent ledger's decision — the one every send path asks (`app/consent.py`).
        # Marketing needs an opt-in on record; transactional only an absence of an opt-out. A
        # refusal is stored on the message as a machine-readable reason, not just a state.
        decision = await consent.check_send(
            session,
            organization_id,
            channel=channel,
            category=category,
            address=to_address,
            provider_template_code=provider_template_code,
        )
        if decision is not None and not decision.allowed:
            suppressed = True
            suppression_reason = consent.stored_reason(decision)

    message = Message(
        organization_id=organization_id,
        created_by=created_by,
        channel=channel,
        category=category,
        to_address=to_address,
        subject=subject,
        body=body,
        state=domain.STATE_SUPPRESSED if suppressed else domain.STATE_PENDING,
        suppression_reason=suppression_reason,
        rule_code=rule_code,
        event_type=event_type,
        source_event_id=source_event_id,
        dedupe_key=dedupe_key,
        provider_template_code=provider_template_code,
        provider_template_payload=provider_template_payload,
    )
    session.add(message)
    await session.flush()

    if suppressed:
        # No queued event: there is nothing for the dispatcher to do, and publishing one would
        # make the delivery log claim an attempt that never happened.
        log.info(
            "notification.suppressed",
            channel=channel,
            category=category,
            rule=rule_code,
            reason=suppression_reason,
        )
        return message

    publish_event(
        session,
        OutboxEvent,
        organization_id=organization_id,
        event_type=EVT_MESSAGE_QUEUED,
        aggregate_type="message",
        aggregate_id=message.id,
        payload={
            "organization_id": str(organization_id),
            "message_id": str(message.id),
            "channel": channel,
            "category": category,
        },
        causation_id=source_event_id,
        actor=actor,
    )
    return message


async def apply_rules(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    envelope: dict[str, Any],
    resolve_target: TargetResolver | None = None,
) -> list[Message]:
    """**The automation engine.** Fold one inbound event into whatever it was configured to send.

    Every active rule watching this event type is evaluated independently: two rules on one
    event are two messages, and one rule failing to render must not stop the other from going
    out. A rule whose recipient the payload does not carry is skipped with a log line rather
    than raised — an event that legitimately has no phone number is not an error, and raising
    would dead-letter an event that several other rules handled correctly.

    A template that cannot render *is* logged loudly, because that is an authoring mistake
    somebody has to fix, and it is silent otherwise.
    """
    event_type = str(envelope.get("event_type", ""))
    payload = envelope.get("payload") or {}
    event_id = envelope.get("event_id")
    source_event_id = uuid.UUID(str(event_id)) if event_id else None

    rules = await selectors.active_rules_for(session, organization_id, event_type)
    staged: list[Message] = []

    for rule in rules:
        to_address = await _recipient_for(
            rule,
            payload,
            organization_id=organization_id,
            resolve_target=resolve_target,
        )
        if to_address is None:
            # Logged inside `_recipient_for` with the reason. A rule that cannot reach anyone
            # on this event is an ordinary outcome, not a fault — most events do not concern
            # most rules.
            continue

        wording = await _wording_for(session, organization_id, rule, payload)
        if wording is None:
            # Every reason is already logged inside `_wording_for`, each with the rule and the
            # template it names. One rule failing must not stop the others on this event.
            continue
        subject, body, provider_code, provider_payload = wording

        message = await stage_message(
            session,
            organization_id=organization_id,
            channel=rule.channel,
            category=rule.category,
            to_address=to_address,
            subject=subject,
            body=body,
            created_by=SYSTEM_ACTOR,
            rule_code=rule.code,
            event_type=event_type,
            source_event_id=source_event_id,
            dedupe_key=domain.dedupe_key(
                rule_code=rule.code,
                event_id=str(event_id) if event_id else None,
                payload=payload,
                dedupe_key_path=rule.dedupe_key_path,
            ),
            actor={"type": "system", "id": SYSTEM_ACTOR},
            provider_template_code=provider_code,
            provider_template_payload=provider_payload,
        )
        if message is not None:
            staged.append(message)

    return staged


async def _recipient_for(
    rule: Rule,
    payload: dict[str, Any],
    *,
    organization_id: uuid.UUID,
    resolve_target: TargetResolver | None,
) -> str | None:
    """Who this rule will message on this event, or None with the reason logged.

    Three sources, and the rule names exactly one:

      `recipient_literal`       a fixed address — the notify-the-team case.
      `recipient_path`          an address carried in the event, such as a ticket requester's
                                email. Whatever the producer put there.
      `recipient_contact_path`  a **contact id** carried in the event. The address is then
                                resolved by asking customer-service, because a customer's real
                                number is sealed and this service holds no key.

    `TargetUnavailable` is deliberately **not** caught. A contact with no number is a skip; a
    customer-service that cannot answer is a notification still owed, so the exception travels
    up to the consumer, the message is nacked and the broker redelivers. Swallowing it here
    would turn a transient outage into silently unsent mail.
    """
    if rule.recipient_contact_path:
        if resolve_target is None:
            # A rule configured to target a contact in a process wired without a resolver.
            # Refusing loudly beats silently sending nothing, because the absence would look
            # exactly like "no rule matched".
            log.error(
                "notification.rule.no_target_resolver",
                rule=rule.code,
                path=rule.recipient_contact_path,
            )
            return None

        contact_id = domain.resolve_path(payload, rule.recipient_contact_path)
        if contact_id is None or not str(contact_id).strip():
            log.info(
                "notification.rule.no_contact_in_event",
                rule=rule.code,
                path=rule.recipient_contact_path,
            )
            return None

        address = await resolve_target(str(organization_id), str(contact_id), rule.channel)
        if address is None:
            log.info(
                "notification.rule.contact_has_no_address",
                rule=rule.code,
                channel=rule.channel,
            )
            return None
        raw_recipient: Any = address
    elif rule.recipient_literal:
        raw_recipient = rule.recipient_literal
    else:
        raw_recipient = domain.resolve_path(payload, rule.recipient_path or "")
        if raw_recipient is None or not str(raw_recipient).strip():
            log.info(
                "notification.rule.no_recipient",
                rule=rule.code,
                path=rule.recipient_path,
            )
            return None

    try:
        # Normalised even when it came from customer-service, which already canonicalises it.
        # Two services agreeing by construction is better than one trusting the other.
        return domain.normalize_recipient(rule.channel, str(raw_recipient))
    except domain.RuleError as exc:
        log.warning(
            "notification.rule.bad_recipient",
            rule=rule.code,
            channel=rule.channel,
            error=str(exc),
        )
        return None


async def _wording_for(
    session: AsyncSession,
    organization_id: uuid.UUID,
    rule: Rule,
    payload: dict[str, Any],
) -> tuple[str | None, str, str | None, dict[str, Any] | None] | None:
    """Resolve what one rule will actually say, or None with the reason logged.

    Two sources, and the rule names exactly one. A `Template` is a body we own and render into
    free text. A `ProviderTemplate` is wording the provider holds and reviews, where we supply
    only parameters — and the only thing that reaches a WhatsApp user outside the 24-hour
    customer service window.

    Returns `(subject, body, provider_template_code, provider_template_payload)`. Every failure
    returns None after logging, because one unsendable rule must not stop the others reacting to
    the same event, and an absent message with an explanation beats a malformed one delivered.
    """
    if rule.provider_template_code:
        provider = await selectors.get_provider_template(
            session, organization_id, rule.provider_template_code, rule.channel
        )
        if provider is None:
            log.error(
                "notification.rule.provider_template_missing",
                rule=rule.code,
                template=rule.provider_template_code,
                channel=rule.channel,
            )
            return None
        if not domain.template_is_sendable(provider.status):
            # Checked here rather than left to the provider: its refusal would arrive as a 4xx
            # the dispatcher classifies permanent, costing a message and telling the operator
            # nothing that this log line does not say now.
            log.error(
                "notification.rule.provider_template_not_approved",
                rule=rule.code,
                template=provider.code,
                status=provider.status,
                reason=provider.status_reason,
            )
            return None
        try:
            variables = [domain.TemplateVariable(**v) for v in provider.variables]
            built = domain.build_template_payload(
                template_name=provider.provider_name,
                language=provider.language,
                variables=variables,
                context=payload,
                parameter_format=provider.parameter_format,
            )
        except (domain.TemplateError, TypeError) as exc:
            # TypeError covers a `variables` row whose keys do not match `TemplateVariable` —
            # stored JSON drifting from the dataclass, which is a data fault, not a send fault.
            log.error(
                "notification.rule.provider_template_invalid",
                rule=rule.code,
                template=provider.code,
                event_type=rule.event_type,
                error=str(exc),
            )
            return None
        # `body` is the provider's wording for display and for the record. It is deliberately
        # not substituted: the provider owns the text, the substitution happens on their side,
        # and a locally-rendered guess at the result would be a message we never sent.
        return None, provider.preview_text, provider.code, built

    template = await selectors.get_template(
        session, organization_id, rule.template_code or "", rule.channel
    )
    if template is None:
        log.error(
            "notification.rule.template_missing",
            rule=rule.code,
            template=rule.template_code,
            channel=rule.channel,
        )
        return None
    try:
        body = domain.render(template.body, payload)
        subject = domain.render(template.subject, payload) if template.subject else None
    except domain.RenderError as exc:
        log.error(
            "notification.rule.render_failed",
            rule=rule.code,
            template=rule.template_code,
            event_type=rule.event_type,
            error=str(exc),
        )
        return None
    return subject, body, None, None


async def send_direct(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    template_code: str,
    channel: str,
    to_address: str,
    category: str,
    context: dict[str, Any],
    request: Request | None = None,
) -> Message:
    """Render and stage one message outside any rule.

    Obeys the same suppression as a rule-driven send — the direct path is not a way around a
    recipient's refusal — and renders through the same strict template, so a missing value is a
    422 to the caller rather than a broken message to a customer.
    """
    try:
        normalized = domain.normalize_recipient(channel, to_address)
    except domain.RuleError as exc:
        raise ValidationProblem(str(exc)) from exc

    template = await selectors.get_template(session, ctx.organization_id, template_code, channel)
    if template is None:
        raise NotFoundProblem(f"No {channel} template named {template_code!r}")

    try:
        body = domain.render(template.body, context)
        subject = domain.render(template.subject, context) if template.subject else None
    except domain.RenderError as exc:
        raise ValidationProblem(str(exc)) from exc

    message = await stage_message(
        session,
        organization_id=ctx.organization_id,
        channel=channel,
        category=category,
        to_address=normalized,
        subject=subject,
        body=body,
        created_by=str(ctx.principal.sub),
        actor=_actor(ctx),
    )
    if message is None:  # pragma: no cover - a direct send carries no dedupe key
        raise ConflictProblem("That message has already been staged")

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="message.sent_direct",
        entity="message",
        entity_id=message.id,
        after={"channel": channel, "template": template_code, "state": message.state},
        request=request,
    )
    return message


# --------------------------------------------------------------------- the dispatch path
#
# Three functions, three transactions, and a provider call that happens between the first and the
# third with nothing open. The split is the whole reason a provider outage cannot become a
# database incident.


async def claim_for_send(
    session: AsyncSession, *, organization_id: uuid.UUID, message_id: uuid.UUID
) -> Message | None:
    """Move one message `pending` → `sending` and count the attempt. Returns None if unclaimable.

    Unclaimable covers every benign race: already sent, suppressed after the event was queued,
    or claimed by another dispatcher. Returning None rather than raising keeps a redelivered
    queued-event a no-op instead of a dead letter. Only `pending` is claimable, which is what
    makes the retry sweep safe: it may queue a second event for a message whose first is still
    in flight, and whichever event loses finds nothing to claim.

    The read takes the row lock (`FOR UPDATE`): two consumers holding two events for one message
    would otherwise both read `pending` and both send. The second one waits here until the first
    commits, then sees `sending` (or later) and returns None.
    """
    message = await selectors.get_message(session, organization_id, message_id, for_update=True)
    if message is None:
        return None
    if message.state != domain.STATE_PENDING:
        return None

    # Suppression is checked again here, not only at render time. A recipient can opt out
    # between the moment a rule staged the message and the moment it would be sent, and of the
    # two checks the later one is the one that is true. Honouring a stale answer is the failure
    # mode that gets a sender blocked.
    suppressions = await selectors.suppressions_for(session, organization_id, message.to_address)
    if domain.is_suppressed(suppressions, channel=message.channel, category=message.category):
        message.state = domain.STATE_SUPPRESSED
        message.suppression_reason = consent.STORED_PREFERENCE
        message.updated_by = SYSTEM_ACTOR
        await session.flush()
        log.info("notification.suppressed_at_dispatch", message_id=str(message.id))
        return None

    # The consent ledger, asked again for the same reason: a recipient can opt out (or an
    # operator can lift an opt-in) between staging and sending, and the later answer is true.
    decision = await consent.check_send(
        session,
        organization_id,
        channel=message.channel,
        category=message.category,
        address=message.to_address,
        provider_template_code=message.provider_template_code,
    )
    if decision is not None and not decision.allowed:
        message.state = domain.STATE_SUPPRESSED
        message.suppression_reason = consent.stored_reason(decision)
        message.updated_by = SYSTEM_ACTOR
        await session.flush()
        log.info(
            "notification.suppressed_at_dispatch",
            message_id=str(message.id),
            reason=message.suppression_reason,
        )
        return None

    message.state = domain.STATE_SENDING
    message.attempts = domain.next_attempt_no(message.attempts)
    message.updated_by = SYSTEM_ACTOR
    await session.flush()
    return message


async def record_sent(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    provider: str,
    provider_message_id: str | None,
    detail: str | None = None,
) -> None:
    """Mark a message sent and append the winning attempt to its ledger.

    `detail` is the provider's own acceptance note when it gave one (Meta's `message_status`,
    e.g. `held_for_quality_assessment`), kept on the attempt's ledger row.

    The wamid is committed here, *after* the provider call returned - there is no earlier moment
    it exists. A delivery receipt that beats this commit finds no message for its wamid;
    `fold_status` raises `ReceiptBeforeSend` for a fresh one so the webhook is redelivered
    instead of the receipt being dropped.
    """
    message = await selectors.get_message(session, organization_id, message_id)
    if message is None:  # pragma: no cover - the row was read moments earlier
        return

    message.state = domain.STATE_SENT
    message.sent_at = datetime.now(UTC)
    message.provider_message_id = provider_message_id
    message.last_error = None
    message.updated_by = SYSTEM_ACTOR

    session.add(
        Delivery(
            organization_id=organization_id,
            created_by=SYSTEM_ACTOR,
            message_id=message.id,
            attempt_no=message.attempts,
            provider=provider,
            outcome="sent",
            detail=detail,
            at=datetime.now(UTC),
        )
    )
    await session.flush()

    publish_event(
        session,
        OutboxEvent,
        organization_id=organization_id,
        event_type=EVT_MESSAGE_SENT,
        aggregate_type="message",
        aggregate_id=message.id,
        payload={
            "organization_id": str(organization_id),
            "message_id": str(message.id),
            "channel": message.channel,
            "to_address": message.to_address,
            "rule_code": message.rule_code,
        },
        actor={"type": "system", "id": SYSTEM_ACTOR},
    )


#: Backoff for a transient send failure: `RETRY_BASE_SECONDS * 2 ** (attempts - 1)`, capped.
#: Five waits (1, 2, 4, 8, 16 minutes) fit inside the default `dispatch_max_attempts` of six -
#: about half an hour end to end, long enough to ride out a Meta throttle or a short outage and
#: short enough that a message nobody can send is reported failed the same afternoon.
RETRY_BASE_SECONDS: Final = 60
RETRY_CAP_SECONDS: Final = 3600


def retry_delay(attempts: int) -> timedelta:
    """How long a message that has failed `attempts` times waits before its next attempt."""
    exponent = max(attempts - 1, 0)
    return timedelta(seconds=min(RETRY_BASE_SECONDS * (2**exponent), RETRY_CAP_SECONDS))


async def record_failure(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    provider: str,
    error: str,
    permanent: bool,
    max_attempts: int,
    error_code: str | None = None,
    now: datetime | None = None,
) -> None:
    """Append the failed attempt and decide whether the message waits for another.

    A permanent refusal goes straight to `failed` - retrying an address the provider will never
    accept only delays the moment somebody looks at it. A transient failure returns to `pending`
    while attempts remain, stamped with when it failed: **that stamp is the retry clock.**
    Nothing re-delivers it by itself - the queued event that brought it here is already
    processed, and a redelivery would hit `already_processed` - so `sweep_message_retries`
    finds it once `retry_delay(attempts)` has passed and queues a fresh event. Out of attempts,
    it fails terminally with a reason that says so.

    `error_code` is the provider's own code (Meta's `error.code`); it lands on
    `messages.provider_error_code` beside the text so an operator sees what Meta said.
    """
    message = await selectors.get_message(session, organization_id, message_id)
    if message is None:  # pragma: no cover
        return
    at = now or datetime.now(UTC)

    session.add(
        Delivery(
            organization_id=organization_id,
            created_by=SYSTEM_ACTOR,
            message_id=message.id,
            attempt_no=message.attempts,
            provider=provider,
            outcome="failed",
            detail=error[:2000],
            at=at,
        )
    )

    retryable = not permanent and domain.should_retry(message.attempts, max_attempts=max_attempts)
    exhausted = not permanent and not retryable
    message.state = domain.STATE_PENDING if retryable else domain.STATE_FAILED
    message.last_error = (
        f"retries_exhausted after {message.attempts} attempts: {error}"[:2000]
        if exhausted
        else error[:2000]
    )
    if error_code:
        message.provider_error_code = error_code[:40]
        message.provider_error_detail = error[:2000]
    message.updated_by = SYSTEM_ACTOR
    # The retry clock (see above). Set explicitly rather than left to `onupdate`, which would
    # be the transaction's start and not the failure's.
    message.updated_at = at
    await session.flush()

    if not retryable:
        publish_event(
            session,
            OutboxEvent,
            organization_id=organization_id,
            event_type=EVT_MESSAGE_FAILED,
            aggregate_type="message",
            aggregate_id=message.id,
            payload={
                "organization_id": str(organization_id),
                "message_id": str(message.id),
                "channel": message.channel,
                "attempts": message.attempts,
                "last_error": message.last_error,
            },
            actor={"type": "system", "id": SYSTEM_ACTOR},
        )


async def template_block(session: AsyncSession, message: Message) -> tuple[str, str] | None:
    """`(code, reason)` when the provider template this message names can no longer be sent.

    Staging checked the template once (`_wording_for`); this is asked again by the dispatcher
    between claiming a message and sending it, because a template is paused or disabled by Meta
    for quality at any time and a message can sit queued, or in retry backoff, for a while.
    The codes are Meta's own for the same conditions, so the failed message reads the same
    whether we caught it or Meta did: 132015 paused, 132016 disabled, 132001 anything else not
    approved (rejected, back in review, deleted). None means send - including for a message that
    names no provider template at all.
    """
    code = message.provider_template_code
    if not code:
        return None
    template = await selectors.get_provider_template(
        session, message.organization_id, code, message.channel
    )
    if template is None:
        return "132001", "template_not_found_or_not_approved: the template no longer exists"
    if domain.template_is_sendable(template.status):
        return None
    if template.status == "paused":
        return "132015", "template_paused: the template is paused by the provider"
    if template.status == "disabled":
        return "132016", "template_disabled: the template is disabled by the provider"
    return (
        "132001",
        f"template_not_found_or_not_approved: the template is {template.status}, not approved",
    )


def retry_max_age() -> timedelta:
    """How long after staging a failed message may still be retried (`Settings.
    dispatch_retry_max_age_seconds`)."""
    return timedelta(seconds=settings().dispatch_retry_max_age_seconds)


#: A message already waiting on the retry event a sweep queued for it is not queued again for at
#: least this long, however short its own backoff is: the dispatcher can be backed up well past a
#: minute, and a second event per tick would only pile duplicates into the broker.
REQUEUE_MIN_WAIT: Final = timedelta(minutes=5)

#: How many messages past their retry window one tick fails. Bounded so the first tick after a
#: deploy, which meets every old stuck row at once, drains them over several ticks rather than in
#: one long pass.
EXPIRE_BATCH: Final = 100


async def due_message_retries(
    session: AsyncSession, *, now: datetime, limit: int, max_age: timedelta | None = None
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """`(organization_id, message_id)` for every `pending` message that has failed at least once,
    is still inside its retry window, and whose backoff has elapsed.

    Cross-tenant, so it runs on the BYPASSRLS outbox role, which holds column-level `SELECT` on
    exactly the columns named here (`migrations/env.py`, `grant_message_retry_reader`) - never a
    message's address or body. Discovery only: every write the sweep makes off the back of it
    is tenant-bound. The due test is repeated there, under the tenant, against the row itself.

    The window is measured from `created_at` (when the message was staged): a row older than
    `max_age` is never returned here - `expired_message_retries` finds it to be failed instead.
    """
    delay = func.least(
        float(RETRY_CAP_SECONDS), float(RETRY_BASE_SECONDS) * func.power(2, Message.attempts - 1)
    )
    due_at = Message.updated_at + func.make_interval(0, 0, 0, 0, 0, 0, delay)
    conditions = [
        Message.state == domain.STATE_PENDING,
        Message.attempts > 0,
        Message.deleted_at.is_(None),
        due_at <= now,
    ]
    if max_age is not None:
        conditions.append(Message.created_at > now - max_age)
    stmt = (
        select(Message.organization_id, Message.id)
        .where(*conditions)
        .order_by(Message.updated_at.asc(), Message.id.asc())
        .limit(limit)
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


async def expired_message_retries(
    session: AsyncSession, *, now: datetime, limit: int, max_age: timedelta
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """`(organization_id, message_id)` for `pending` messages that have failed before and are now
    older than the retry window - the ones nothing may send any more. Same role and column grant
    as `due_message_retries`; oldest first, at most `limit`."""
    stmt = (
        select(Message.organization_id, Message.id)
        .where(
            Message.state == domain.STATE_PENDING,
            Message.attempts > 0,
            Message.deleted_at.is_(None),
            Message.created_at <= now - max_age,
        )
        .order_by(Message.created_at.asc(), Message.id.asc())
        .limit(limit)
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


def _publish_message_failed(session: AsyncSession, message: Message) -> None:
    publish_event(
        session,
        OutboxEvent,
        organization_id=message.organization_id,
        event_type=EVT_MESSAGE_FAILED,
        aggregate_type="message",
        aggregate_id=message.id,
        payload={
            "organization_id": str(message.organization_id),
            "message_id": str(message.id),
            "channel": message.channel,
            "attempts": message.attempts,
            "last_error": message.last_error,
        },
        actor={"type": "system", "id": SYSTEM_ACTOR},
    )


async def expire_stale_retry(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    now: datetime,
    max_age: timedelta,
) -> str:
    """Fail permanently a `pending`, previously-failed message that is past its retry window.
    Tenant-bound. Returns `expired`, or `skipped` when the row is no longer such a message (sent,
    claimed, retried in the meantime, or still inside the window).

    Without this a message stranded `pending` by a transient failure - including one from before
    the retry sweep existed - would sit in that state forever or, worse, be sent hours or days
    after the fact. The reason string starts with `retry_window_expired`, and the usual
    `message.failed` event goes out.
    """
    message = await selectors.get_message(session, organization_id, message_id, for_update=True)
    if (
        message is None
        or message.state != domain.STATE_PENDING
        or message.attempts < 1
        or message.created_at + max_age > now
    ):
        return "skipped"
    previous = message.last_error or "unknown"
    message.state = domain.STATE_FAILED
    message.last_error = f"retry_window_expired after {message.attempts} attempts: {previous}"[
        :2000
    ]
    message.updated_by = SYSTEM_ACTOR
    message.updated_at = now
    await session.flush()
    _publish_message_failed(session, message)
    return "expired"


async def _awaiting_retry_event(session: AsyncSession, message: Message) -> bool:
    """Has the sweep already queued a retry event for this message's latest failure?

    A failure stamps `updated_at` and its ledger row with the same instant; a re-queue moves
    `updated_at` on and leaves the ledger alone. So `updated_at` later than the newest failed
    attempt means the bump is a re-queue, whose event may still be waiting in the broker."""
    last_failure = (
        await session.execute(
            select(func.max(Delivery.at)).where(
                Delivery.organization_id == message.organization_id,
                Delivery.message_id == message.id,
                Delivery.outcome == "failed",
            )
        )
    ).scalar_one_or_none()
    return last_failure is not None and message.updated_at - last_failure > timedelta(seconds=1)


async def requeue_for_retry(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    max_attempts: int,
    now: datetime,
    max_age: timedelta | None = None,
) -> str:
    """Queue one fresh dispatch event for a message whose backoff has elapsed. Tenant-bound.

    Returns `requeued`, `exhausted` (out of attempts - failed terminally instead), or `skipped`
    (no longer pending, not yet due, past its retry window, or gone: another dispatcher or an
    earlier tick got there).

    Why a *new* event: the original was marked processed in the claim transaction, so
    redelivering it would be swallowed by `already_processed`. This one has its own id, so the
    dispatcher's dedupe lets it through, and `claim_for_send` (pending only, under a row lock) is
    what still guarantees one copy - if the message was sent, suppressed or claimed since, the
    claim finds nothing to do. The event goes through the outbox in this transaction, so it
    exists only if the stamp below does.

    The `updated_at` bump is the sweep's own dedupe: it restarts the message's clock, so the next
    tick does not queue the same message again while this event waits in the broker. After a
    re-queue that clock is the longer of the backoff and `REQUEUE_MIN_WAIT`, because a dispatcher
    can be backed up for much longer than a first-attempt backoff of a minute.
    """
    message = await selectors.get_message(session, organization_id, message_id, for_update=True)
    if message is None or message.state != domain.STATE_PENDING or message.attempts < 1:
        return "skipped"
    if max_age is not None and message.created_at + max_age <= now:
        return "skipped"
    wait = retry_delay(message.attempts)
    if await _awaiting_retry_event(session, message):
        wait = max(wait, REQUEUE_MIN_WAIT)
    if message.updated_at + wait > now:
        return "skipped"

    if not domain.should_retry(message.attempts, max_attempts=max_attempts):
        # Only reachable when `dispatch_max_attempts` was lowered while this waited. No new
        # ledger row: the attempt that got it here already wrote its own.
        message.state = domain.STATE_FAILED
        previous = message.last_error or "unknown"
        message.last_error = f"retries_exhausted after {message.attempts} attempts: {previous}"[
            :2000
        ]
        message.updated_by = SYSTEM_ACTOR
        message.updated_at = now
        await session.flush()
        _publish_message_failed(session, message)
        return "exhausted"

    message.updated_at = now
    message.updated_by = SYSTEM_ACTOR
    await session.flush()
    publish_event(
        session,
        OutboxEvent,
        organization_id=organization_id,
        event_type=EVT_MESSAGE_QUEUED,
        aggregate_type="message",
        aggregate_id=message.id,
        payload={
            "organization_id": str(organization_id),
            "message_id": str(message.id),
            "channel": message.channel,
            "category": message.category,
            "retry_of_attempt": message.attempts,
        },
        actor={"type": "system", "id": SYSTEM_ACTOR},
    )
    return "requeued"


async def sweep_message_retries(
    *,
    discovery_factory: Any,
    work_scope: Any,
    max_attempts: int,
    now: datetime | None = None,
    limit: int = 100,
    max_age: timedelta | None = None,
) -> int:
    """Re-queue every due retry, across organizations. Returns how many were queued.

    The same discovery/work split as the campaign driver and the automation sweep: *which
    organizations have a message due* runs on the outbox role (no tenant to bind while asking),
    and every write runs under that organization's own binding via `work_scope`, one
    transaction per message so one bad row cannot roll back another's re-queue.

    Only messages inside the retry window (`max_age`, default `retry_max_age()`) are re-queued.
    Ones past it that are still `pending` are failed permanently first (`expire_stale_retry`),
    at most `EXPIRE_BATCH` per tick, so they can neither be sent late nor sit pending forever.
    """
    at = now or datetime.now(UTC)
    window = max_age if max_age is not None else retry_max_age()
    async with discovery_factory() as session, session.begin():
        expired = await expired_message_retries(session, now=at, limit=EXPIRE_BATCH, max_age=window)
        due = await due_message_retries(session, now=at, limit=limit, max_age=window)
    failed = 0
    for organization_id, message_id in expired:
        try:
            async with work_scope(organization_id) as session:
                outcome = await expire_stale_retry(
                    session,
                    organization_id=organization_id,
                    message_id=message_id,
                    now=at,
                    max_age=window,
                )
        except Exception:
            log.exception(
                "notification.retry_sweep.expire_failed",
                organization_id=str(organization_id),
                message_id=str(message_id),
            )
            continue
        failed += outcome == "expired"
    if failed:
        log.warning("notification.retry_sweep.window_expired", count=failed)
    queued = 0
    for organization_id, message_id in due:
        try:
            async with work_scope(organization_id) as session:
                outcome = await requeue_for_retry(
                    session,
                    organization_id=organization_id,
                    message_id=message_id,
                    max_attempts=max_attempts,
                    now=at,
                    max_age=window,
                )
        except Exception:
            log.exception(
                "notification.retry_sweep.failed",
                organization_id=str(organization_id),
                message_id=str(message_id),
            )
            continue
        queued += outcome == "requeued"
    return queued


# --------------------------------------------------------------------- provider receipts


#: How long after a receipt's own timestamp an unknown wamid is still assumed to be a message
#: whose send has not committed yet, rather than one this service never sent. Long enough to
#: cover a slow `record_sent` transaction and Meta's first redelivery; short enough that a
#: receipt for another tool's message on the same number stops asking for redelivery quickly.
RECEIPT_RACE_WINDOW: Final = timedelta(minutes=10)


class ReceiptBeforeSend(Exception):
    """A delivery receipt named a wamid we have not recorded yet - the send that produced it has
    not committed. Deliberately not a `ValueError`/`LookupError`: `is_permanent_fold_error` reads
    those as "will never work", and this is exactly the failure a redelivery fixes. It is
    transient, so the webhook answers 5xx and Meta sends the receipt again."""

    def __init__(self, provider_message_id: str) -> None:
        super().__init__(f"no message yet for provider message id {provider_message_id!r}")


async def fold_status(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    provider_message_id: str,
    status: str,
    occurred_at: datetime,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> Message | None:
    """Apply one provider receipt to the message it concerns.

    Returns None when there is nothing to apply, which covers two ordinary cases and is not a
    failure in either:

      * **No such message.** A receipt for something this organization did not send — a message
        sent before this system existed, or from another tool on the same number. Recording it
        against nothing would invent a message. *Unless the receipt is fresh*: the wamid is only
        committed by `record_sent` after Meta's response came back, and a `sent`/`delivered`
        receipt can beat that commit. A receipt younger than `RECEIPT_RACE_WINDOW` for an unknown
        wamid therefore raises `ReceiptBeforeSend` instead of returning, so the caller does not
        mark it processed and Meta redelivers it once the wamid has landed.
      * **No progress.** Receipts arrive out of order, so a `delivered` can follow a `read`.
        `domain.advances` is what keeps the fold monotonic; without it a message would report
        as less delivered than it is, and the inbox would lie.

    Publishes an event on real progress so anything downstream — analytics, a projection — can
    react without polling the messages table.
    """
    message = await selectors.get_message_by_provider_id(
        session, organization_id, provider_message_id
    )
    if message is None:
        if datetime.now(UTC) - occurred_at <= RECEIPT_RACE_WINDOW:
            log.info(
                "notification.receipt.before_send_committed",
                provider_message_id=provider_message_id,
                status=status,
            )
            raise ReceiptBeforeSend(provider_message_id)
        log.info(
            "notification.receipt.unknown_message",
            provider_message_id=provider_message_id,
            status=status,
        )
        return None

    if not domain.advances(message.state, status):
        log.info(
            "notification.receipt.not_progress",
            provider_message_id=provider_message_id,
            current=message.state,
            incoming=status,
        )
        return None

    message.state = status
    message.updated_by = SYSTEM_ACTOR
    if status == domain.STATE_DELIVERED:
        message.delivered_at = occurred_at
    elif status == domain.STATE_READ:
        message.read_at = occurred_at
        # A read implies delivery. Meta does not always send both, and a message shown as read
        # but never delivered is a gap an operator would have to explain.
        if message.delivered_at is None:
            message.delivered_at = occurred_at
    elif status == domain.STATE_FAILED:
        message.provider_error_code = error_code
        message.provider_error_detail = error_detail
        message.last_error = error_detail or f"provider reported {error_code or 'failure'}"

    await session.flush()

    event = {
        domain.STATE_DELIVERED: EVT_MESSAGE_DELIVERED,
        domain.STATE_READ: EVT_MESSAGE_READ,
        domain.STATE_FAILED: EVT_MESSAGE_FAILED,
    }.get(status)
    if event is not None:
        publish_event(
            session,
            OutboxEvent,
            organization_id=organization_id,
            event_type=event,
            aggregate_type="message",
            aggregate_id=message.id,
            payload={
                "organization_id": str(organization_id),
                "message_id": str(message.id),
                "channel": message.channel,
                "status": status,
                # Never `to_address`: a delivery receipt is not a reason to put a customer's
                # phone number into an event payload every consumer can read.
                "rule_code": message.rule_code,
            },
            actor={"type": "system", "id": SYSTEM_ACTOR},
        )
    return message


# --------------------------------------------------------------------- engage inbox (WhatsApp)
#
# The inbound half of the pipeline: folding a customer's message into a conversation, acting on
# STOP, replying inside the 24-hour window, and the human workflow (assign, close) around it.
# `docs/whatsapp/audit.md` C5/C6/C9 and `docs/whatsapp/webhooks.md` are the design record.


#: What `body_masked` holds whenever a body was sealed. Fixed, never derived from the real text:
#: a length- or tail-revealing mask on free-form customer text (the "keep the last two
#: characters" convention `pii.mask`'s generic masker uses for a code or an account number) leaks
#: the one thing masking exists to hide from a sentence, and for a message long enough it also
#: overflowed `body_masked`'s column — a customer's 400-character message crashed the fold that
#: was supposed to record it. This constant cannot overflow anything and cannot leak anything,
#: whatever the real body says.
_BODY_MASK_PLACEHOLDER: Final = "•" * 10

#: How much of a WhatsApp display name an ordinary read may see: the first character, so a list
#: of conversations is not a wall of identical bullets, and nothing else — never a length- or
#: tail-revealing mask either, for the same reason `_BODY_MASK_PLACEHOLDER` is fixed-shape.
_PROFILE_NAME_MASK_TAIL: Final = "•••"


def _mask_profile_name(name: str) -> str:
    if not name:
        return ""
    return name[0] + _PROFILE_NAME_MASK_TAIL


async def fold_inbound_message(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    inbound: webhooks.InboundMessage,
) -> InboundMessageRow | None:
    """Persist one customer message: upsert its conversation, insert the message row, act on a
    STOP or START keyword, and stage the event a separate consumer resolves the contact id from.

    Returns None when the sender is not a number this service can canonicalise — `webhooks.
    sender_e164` refuses rather than guesses, the same rule `domain.normalize_recipient` applies
    everywhere else, so a malformed `from` is logged and the message is not written rather than
    filed under a phone number nobody could dial. The caller (`app/api/webhooks.py`) still marks
    the event processed either way: retrying a `from` that will never parse is a loop, not
    robustness — the same reasoning as an unfamiliar payload shape.

    Idempotent by construction rather than by a second check: `provider_message_id` is unique per
    organization (`uq_inbound_messages_org_provider_id`), so a redelivery that got past
    `processed_events` still cannot insert a second row — it raises `IntegrityError`, and the
    caller's `already_processed` guard is what actually stops that from being reached twice in
    the ordinary case. Any other failure here — including that `IntegrityError` — is the caller's
    to catch and park in `InboundDeadLetter`; this function raises rather than swallowing, so one
    bad item never looks like it folded when it did not.

    **Nothing here writes plaintext PII.** `raw_enc` seals the provider's whole message object —
    including `from` and the text body — and there is no plaintext `raw` column at all; the
    sender's profile name is sealed the same way. See `InboundMessageRow`'s docstring.
    """
    cfg = settings()
    sender = webhooks.sender_e164(inbound.from_address)
    if sender is None:
        log.warning("notification.inbound.bad_sender", message_type=inbound.message_type)
        return None

    address_index = pii.blind_index(sender, kind="phone", settings=cfg)
    conversation_id = await _upsert_conversation(
        session,
        organization_id=organization_id,
        sender=sender,
        address_index=address_index,
        occurred_at=inbound.occurred_at,
        cfg=cfg,
    )

    body_enc: bytes | None = None
    body_masked = ""
    if inbound.body:
        body_enc, _ = pii.seal(inbound.body, kind="generic", settings=cfg)
        body_masked = _BODY_MASK_PLACEHOLDER

    profile_name_enc: bytes | None = None
    profile_name_masked: str | None = None
    if inbound.profile_name:
        profile_name_enc, _ = pii.seal(inbound.profile_name, kind="generic", settings=cfg)
        profile_name_masked = _mask_profile_name(inbound.profile_name)

    # The provider's object whole, plus what the parser read out of it (a button payload, a media
    # reference and its `not_fetched` state) under a key Meta does not use, so a later reader
    # (or a media-fetch job) does not have to re-derive it. Sealed together: a caption, a filename
    # and a media id are all customer data.
    stored_raw: dict[str, Any] = dict(inbound.raw)
    parsed = inbound.parsed_metadata
    if parsed is not None:
        stored_raw["_opsgrids"] = parsed
    raw_enc, _ = pii.seal(
        json.dumps(stored_raw, separators=(",", ":")), kind="generic", settings=cfg
    )

    row = InboundMessageRow(
        organization_id=organization_id,
        created_by=SYSTEM_ACTOR,
        conversation_id=conversation_id,
        provider_message_id=inbound.provider_message_id,
        type=inbound.message_type,
        body_enc=body_enc,
        body_masked=body_masked,
        raw_enc=raw_enc,
        occurred_at=inbound.occurred_at,
        profile_name_enc=profile_name_enc,
        profile_name_masked=profile_name_masked,
    )
    session.add(row)
    await session.flush()

    if webhooks.is_stop_request(inbound):
        await _record_stop_request(
            session,
            organization_id=organization_id,
            phone=sender,
            reference=inbound.provider_message_id,
        )
    elif webhooks.is_start_request(inbound):
        await _record_start_request(
            session,
            organization_id=organization_id,
            phone=sender,
            reference=inbound.provider_message_id,
        )

    publish_event(
        session,
        OutboxEvent,
        organization_id=organization_id,
        event_type=EVT_INBOUND_RECEIVED,
        aggregate_type="conversation",
        aggregate_id=conversation_id,
        payload={
            "organization_id": str(organization_id),
            "conversation_id": str(conversation_id),
            "inbound_message_id": str(row.id),
        },
        actor={"type": "system", "id": SYSTEM_ACTOR},
    )
    return row


#: The one `IntegrityError` a redelivery — or a concurrent duplicate delivery — can legitimately
#: hit: two transactions racing the same provider message id, the loser finding the winner
#: already committed. Nothing to retry into existing — the message is already durably stored — so
#: `is_inbound_dedupe_conflict` is what the caller (`app/api/webhooks.py`) checks *before*
#: `is_permanent_fold_error`, and treats as success rather than a failure of any kind.
_INBOUND_DEDUPE_CONSTRAINT: Final = "uq_inbound_messages_org_provider_id"

#: `inbound_dead_letters`' own dedupe constraint (`organization_id`, `kind`,
#: `provider_message_id`) — `record_dead_letter` upserts on it with `ON CONFLICT DO NOTHING`, so
#: two concurrent attempts to park the same item (two redeliveries racing a permanent error, or a
#: replay that fails again for a row a second webhook already re-parked) produce one row, not two.
_DEAD_LETTER_DEDUPE_CONSTRAINT: Final = "uq_inbound_dead_letters_org_kind_provider_id"

#: `inbound_dead_letters.provider_message_id`'s column width — see `record_dead_letter`'s
#: docstring for why the value written there is truncated to it rather than left to overflow.
_DEAD_LETTER_PROVIDER_ID_MAX: Final = 200


def _dbapi_constraint(exc: DBAPIError) -> str | None:
    """The name of the constraint that actually fired, when the driver surfaces one.

    asyncpg's own exception carries `constraint_name` directly, but SQLAlchemy's asyncpg dialect
    re-raises it as a driver-shaped exception first, so the structured attribute can survive on
    `exc.orig.__cause__` rather than on `exc.orig` itself. Both are checked, in that order — the
    same helper `account/app/services.py` uses for its own unique-constraint check, for the same
    reason.
    """
    orig = exc.orig
    name = getattr(orig, "constraint_name", None)
    if name is not None:
        return str(name)
    cause = getattr(orig, "__cause__", None)
    name = getattr(cause, "constraint_name", None)
    return str(name) if name is not None else None


def _dbapi_sqlstate(exc: DBAPIError) -> str | None:
    """The Postgres SQLSTATE the driver attached, by the same two-hop lookup `_dbapi_constraint`
    uses — enough for an operator to tell a serialization failure from a data exception without
    opening a sealed column or ever seeing a parameter value."""
    orig = exc.orig
    state = getattr(orig, "sqlstate", None)
    if state is not None:
        return str(state)
    cause = getattr(orig, "__cause__", None)
    state = getattr(cause, "sqlstate", None)
    return str(state) if state is not None else None


#: Postgres SQLSTATE class/codes that mean "this exact statement will fail identically forever" —
#: a value or shape the database itself rejects, not a momentary condition. Class 22 (`22*`) is
#: "data exception" (a string too long, a value out of range, an invalid input syntax); `23502`
#: (`not_null_violation`) and `23514` (`check_violation`) are constraint violations that are about
#: the *value*, not a race with another transaction — unlike `23505` (`unique_violation`), which
#: can mean a genuine duplicate (`_INBOUND_DEDUPE_CONSTRAINT`, handled separately by
#: `is_inbound_dedupe_conflict` before this function ever runs) or an unrelated race, so it is
#: deliberately left out of this set and falls through to "transient" below.
_PERMANENT_SQLSTATE_PREFIXES: Final = ("22",)
_PERMANENT_SQLSTATES: Final[frozenset[str]] = frozenset({"23502", "23514"})

#: SQLSTATE class/codes that mean "ask again in a moment": a dropped or refused connection (`08*`),
#: the server shutting down or refusing new work (`57P*`), a serialization failure or a deadlock
#: (`40001`, `40P01`), or the server out of some resource — memory, disk, too many connections
#: (`53*`). Listed explicitly rather than inferred, because "transient" is also this function's
#: default for anything unrecognised, and calling that out here is what keeps the two from being
#: confused with each other later.
_TRANSIENT_SQLSTATE_PREFIXES: Final = ("08", "57P", "53")
_TRANSIENT_SQLSTATES: Final[frozenset[str]] = frozenset({"40001", "40P01"})


def is_inbound_dedupe_conflict(exc: BaseException) -> bool:
    """Is this the one `IntegrityError` a redelivery — or a concurrent duplicate delivery — can
    legitimately hit on `inbound_messages`?

    Two transactions racing the same provider message id, the loser finding the winner already
    committed: the message is already durably stored, so there is nothing left for this attempt to
    do. `app/api/webhooks.py` checks this **before** `is_permanent_fold_error` and, when it is
    true, treats the whole attempt as success — logging `webhook.redelivered` and recording
    `ProcessedEvent` the same as an ordinary redelivery — rather than parking anything: parking a
    duplicate would be park-and-never-replay-away, since replaying it would only hit this same
    conflict again.
    """
    return isinstance(exc, IntegrityError) and _dbapi_constraint(exc) == _INBOUND_DEDUPE_CONSTRAINT


def is_permanent_fold_error(exc: BaseException) -> bool:
    """Will retrying this exact item ever reach a different outcome?

    This is the line `app/api/webhooks.py` uses to decide between parking an item (acknowledge
    the request, 200) and letting the failure raise into a 5xx so Meta redelivers the whole body.
    Getting it backwards in either direction loses a message: parking a transient failure
    (a connection blip, a deadlock, the database restarting) throws away something a redelivery
    a moment later would have folded cleanly; asking Meta to redeliver a permanent one forever
    (a value that will never fit, a shape that will never parse) is a retry loop that never ends
    and never helps.

    Classified on the Postgres **SQLSTATE**, not the Python exception type: SQLAlchemy 2.0's
    asyncpg dialect only ever raises `IntegrityError`, `ProgrammingError`, `InterfaceError` or a
    generic `DBAPIError` — it never raises its own `DataError` or `OperationalError` classes, even
    for a value that does not fit a column or a connection the server dropped, because the asyncpg
    driver's own exception hierarchy does not map onto those names (`asyncpg.exceptions.DataError`
    is not translated to `dbapi.DataError`; it comes through as the same generic `Error` a
    connection failure does). Checking `isinstance(exc, DataError)` or
    `isinstance(exc, OperationalError)` therefore never matches anything real in production and
    silently classified every genuine data error as the unmatched, transient default instead —
    accepted forever, 503, retried forever, since the identical oversized value never becomes any
    smaller on redelivery. The SQLSTATE the driver attaches (`_dbapi_sqlstate`) survives that
    translation regardless of which Python exception class wraps it, so it is what this function
    reads instead.

      * The dedupe constraint (`_INBOUND_DEDUPE_CONSTRAINT`) is handled by
        `is_inbound_dedupe_conflict`, checked by the caller first — this function never needs to
        special-case it.
      * SQLSTATE class 22 (a data exception: too long, out of range, invalid input syntax) or
        `23502`/`23514` (a not-null or check constraint the *value* violated, not a race) —
        retrying sends the identical bytes again. Permanent.
      * A connection-class SQLSTATE (`08*`, `57P*`, `53*`, `40001`, `40P01` — dropped or refused
        connections, the server shutting down, a serialization failure or deadlock) — exactly what
        a redelivery a few seconds later is for. Transient.
      * Any other `DBAPIError`, with or without a SQLSTATE the driver surfaced (an unrecognised
        constraint, a foreign key that does not yet resolve) — not confidently identified as
        permanent, so treated the same as a connection problem: safer to ask for a retry than to
        silently drop something that might succeed once whatever it raced against settles.
        Transient.
      * A parse/validation error raised by this service's own code before a query ever ran
        (`ValueError`, `TypeError`, `LookupError`) — the shape will not change on redelivery.
        Permanent.
      * Anything else unrecognised — treated as transient. An exception this function has never
        seen is exactly the case where guessing "safe to drop" is the more expensive mistake.
    """
    if isinstance(exc, DBAPIError):
        sqlstate = _dbapi_sqlstate(exc)
        if sqlstate is None:
            return False
        if sqlstate.startswith(_PERMANENT_SQLSTATE_PREFIXES) or sqlstate in _PERMANENT_SQLSTATES:
            return True
        if sqlstate.startswith(_TRANSIENT_SQLSTATE_PREFIXES) or sqlstate in _TRANSIENT_SQLSTATES:
            return False
        # An unrecognised SQLSTATE. Guessing "safe to drop" is the more expensive mistake.
        return False
    return isinstance(exc, (ValueError, TypeError, LookupError))


async def record_dead_letter(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    kind: str,
    provider_message_id: str,
    occurred_at: datetime,
    error: BaseException,
    raw: dict[str, Any],
) -> None:
    """Park one webhook item that failed with a **permanent** error, in its own transaction --
    the caller's own fold transaction has already rolled back by the time this runs, so this is
    deliberately a fresh write rather than a continuation of it. A transient failure never
    reaches this function at all — `is_permanent_fold_error` is what the caller checks first, and
    a transient one is left to raise into a 5xx instead.

    `error` is reduced to the exception's type name, its SQLSTATE and (when the driver surfaces
    one) its constraint name -- never `str(exc)`. A database error's own message can echo a
    parameter value straight out of the statement that failed (a duplicate key error quotes the
    key; a check-constraint violation can quote the value that violated it), and this row is not
    sealed the way `raw_enc` is -- an operator worklist that could itself hold a customer's phone
    number or message text would be exactly the leak masking the rest of this table exists to
    prevent. The **log line** the caller writes alongside this call carries only
    `provider_message_id` and `kind` for the same reason.

    `raw_enc` seals the item's own provider object -- the one thing this row *does* keep a full
    copy of, because unlike the error text it is not free-form (it is exactly the shape
    `webhooks.status_from_raw`/`inbound_from_raw` already parse) and a permanent error is, by
    definition, recoverable: `app/replay_dead_letters.py` is the only place that opens it.

    `provider_message_id` is truncated to this column's own width before it is written -- unlike
    the dead-lettering path itself, which must survive an oversized id (that is exactly what a
    256-character `wamid` dead-lettered for being too long for `inbound_messages` looks like), the
    full value is never lost: it is still whole inside `raw_enc`, which is what `app/
    replay_dead_letters.py` actually replays from.

    `ON CONFLICT DO NOTHING` on (`organization_id`, `kind`, `provider_message_id`) rather than a
    plain insert: two redeliveries of the same permanently-broken item racing this function (or a
    replay that fails again for a row a concurrent webhook has already re-parked) must not raise a
    second `IntegrityError` here -- a dead-letter write failing *is* treated as transient by the
    caller (see `app/api/webhooks.py::_dead_letter`), and a permanent error's own park turning into
    a 503 loop over its own bookkeeping would be exactly the failure this table exists to prevent.
    """
    sealed, _ = pii.seal(
        json.dumps(raw, separators=(",", ":")), kind="generic", settings=settings()
    )
    stmt = (
        pg_insert(InboundDeadLetter)
        .values(
            organization_id=organization_id,
            created_by=SYSTEM_ACTOR,
            kind=kind,
            provider_message_id=provider_message_id[:_DEAD_LETTER_PROVIDER_ID_MAX],
            error=_dead_letter_error_text(error),
            occurred_at=occurred_at,
            raw_enc=sealed,
        )
        .on_conflict_do_nothing(constraint=_DEAD_LETTER_DEDUPE_CONSTRAINT)
    )
    await session.execute(stmt)


def _dead_letter_error_text(error: BaseException) -> str:
    """`type(error).__name__`, its SQLSTATE and its constraint name, joined -- never `error`'s
    own message. See `record_dead_letter`'s docstring for why."""
    parts = [type(error).__name__]
    if isinstance(error, DBAPIError):
        sqlstate = _dbapi_sqlstate(error)
        if sqlstate:
            parts.append(f"sqlstate={sqlstate}")
        constraint = _dbapi_constraint(error)
        if constraint:
            parts.append(f"constraint={constraint}")
    return " ".join(parts)[:500]


async def _upsert_conversation(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    sender: str,
    address_index: str,
    occurred_at: datetime,
    cfg: Settings,
) -> uuid.UUID:
    """Insert-or-advance the conversation this number belongs to, in one statement.

    `ON CONFLICT ... DO UPDATE` rather than a read-then-write: two inbound messages from the same
    number processed concurrently (a redelivery racing the original, or two numbers' webhooks
    interleaved) must not create two conversations for one number, and a plain upsert makes that
    a database guarantee instead of an application lock. `GREATEST` is what keeps
    `last_customer_message_at` monotonic when Meta's batching delivers two messages out of order
    — the same reasoning `domain.advances` applies to a status receipt. A new message always
    reopens the conversation: a customer writing back is the one event that should always surface
    a closed thread again.
    """
    enc, masked = pii.seal(sender, kind="phone", settings=cfg)
    conversation_id = uuid.uuid4()
    stmt = (
        pg_insert(Conversation)
        .values(
            id=conversation_id,
            organization_id=organization_id,
            created_by=SYSTEM_ACTOR,
            updated_by=SYSTEM_ACTOR,
            channel="whatsapp",
            wa_address_enc=enc,
            wa_address_masked=masked,
            wa_address_index=address_index,
            last_customer_message_at=occurred_at,
            status="open",
        )
        .on_conflict_do_update(
            constraint="uq_conversations_org_channel_address",
            set_={
                "last_customer_message_at": func.greatest(
                    Conversation.last_customer_message_at, occurred_at
                ),
                "status": "open",
                "updated_by": SYSTEM_ACTOR,
            },
        )
        .returning(Conversation.id)
    )
    return (await session.execute(stmt)).scalar_one()


async def _set_whatsapp_preference(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    phone: str,
    suppressed: bool,
    reason: str,
    category: str = consent.MARKETING_CATEGORY,
) -> None:
    """The write STOP, START and Meta's marketing-preference signals all are: one `Preference` row
    per number and category (**marketing** unless told otherwise — a customer's STOP is a request
    to stop promotions, not to stop hearing about their order) — the same row `services.
    set_preference` writes for a human operator, so a later send checks one table regardless of
    who recorded the refusal or its reversal.
    `stage_message` re-checks it at render **and** dispatch, so a reply already queued when a
    customer says STOP still does not go out.

    A single `INSERT ... ON CONFLICT DO UPDATE`, not a read then a conditional insert-or-update:
    two STOPs for the same number racing (a customer sending the word twice before the first
    fold commits, each its own message and its own transaction) used to both read "no row yet"
    and both attempt an insert, and the loser raised `IntegrityError` on `uq_preferences_org_
    recipient` — an ordinary customer action crashing the fold it triggered. The upsert makes
    the write itself race-free at the database; the `SELECT ... FOR UPDATE` immediately before it
    exists only to capture a `before` value for the audit row, on a best-effort basis — in the
    same true-concurrent race it can occasionally report "no previous preference" for the
    request that lost the race to create one, but the write it precedes is correct regardless.
    """
    before = (
        await session.execute(
            select(Preference.suppressed)
            .where(
                Preference.organization_id == organization_id,
                Preference.recipient == phone,
                Preference.channel == "whatsapp",
                Preference.category == category,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    stmt = (
        pg_insert(Preference)
        .values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            created_by=SYSTEM_ACTOR,
            updated_by=SYSTEM_ACTOR,
            recipient=phone,
            channel="whatsapp",
            category=category,
            suppressed=suppressed,
            reason=reason,
        )
        .on_conflict_do_update(
            constraint="uq_preferences_org_recipient",
            set_={"suppressed": suppressed, "reason": reason, "updated_by": SYSTEM_ACTOR},
        )
        .returning(Preference.id)
    )
    pref_id = (await session.execute(stmt)).scalar_one()

    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        acting_as=SYSTEM_ACTOR,
        action="preference.set",
        entity="preference",
        entity_id=pref_id,
        before={"suppressed": before},
        after={
            "channel": "whatsapp",
            "category": category,
            "suppressed": suppressed,
            "reason": reason,
        },
    )


def _keyword_evidence(keyword_kind: str, reference: str | None) -> str:
    """What the ledger records for a customer's own STOP or START: the fact and a pointer to the
    inbound message that carried it (Meta's `wamid`, an opaque provider id) — never the words
    themselves or the number."""
    where = f" (WhatsApp message {reference})" if reference else ""
    return f"Customer replied with a {keyword_kind} keyword{where}."[:500]


async def _record_stop_request(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    phone: str,
    reference: str | None = None,
) -> None:
    """STOP, UNSUBSCRIBE and the "Stop promotions" labels (`webhooks.STOP_KEYWORDS`), typed or
    tapped. Opts the number out of **marketing** on WhatsApp — in the consent ledger and in the
    `Preference` every send path already checks — and nothing else.

    It used to refuse both purposes, and to treat CANCEL/END/QUIT the same way. A customer who
    answered an order reminder with "Cancel" then lost every notification about that order, which
    is the opposite of what they meant. Meta's own model agrees with the narrower reading: the
    `user_preferences` control a customer has in WhatsApp is for *marketing* messages only.
    """
    await _set_whatsapp_preference(
        session,
        organization_id=organization_id,
        phone=phone,
        suppressed=True,
        reason=_CUSTOMER_STOP_REASON,
    )
    await consent.opt_out(
        session,
        organization_id=organization_id,
        subject=consent.subject_for("whatsapp", phone),
        purposes=(consent.MARKETING,),
        source=consent.SOURCE_INBOUND_KEYWORD,
        evidence=_keyword_evidence("STOP", reference),
    )
    log.info("notification.whatsapp.stop_received")


#: The one `reason` START is entitled to reverse. An operator suppression (a complaint handled
#: by hand, a legal hold, anything `services.set_preference` writes for a human) was never put
#: there by this convention, and a customer's stray "START" must not be the thing that lifts it.
_CUSTOMER_STOP_REASON: Final = "customer_stop"

#: What a marketing refusal that came from **Meta** (`user_preferences` stop, or a 131050 delivery
#: failure) is recorded under. Reversible by Meta's own `resume`, and by START like a customer STOP.
_META_STOP_REASON: Final = "meta_marketing_stop"

#: The reasons a START / `resume` may lift.
_CUSTOMER_REVERSIBLE_REASONS: Final = frozenset({_CUSTOMER_STOP_REASON, _META_STOP_REASON})


async def _record_start_request(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    phone: str,
    reference: str | None = None,
) -> None:
    """START, UNSTOP, SUBSCRIBE (`webhooks.START_KEYWORDS`) — the re-subscribe half of the same
    convention. A customer who typed STOP by habit should not have to contact support to undo it.

    Only un-suppresses a row this same convention suppressed. No preference row at all, or one
    suppressed for any other reason, is left exactly as it is — see `_CUSTOMER_STOP_REASON`.

    A pre-change STOP wrote a `*` row (every category) and opted out of both ledger purposes; that
    row is lifted here too, and only then are **both** purposes restored. A current STOP wrote a
    `marketing` row and only a marketing ledger event, so only marketing is restored.

    In the consent ledger a START **restores, never creates** (`consent.restore`): transactional
    is re-opened, and marketing only if the customer had opted in to it before their STOP. A bare
    keyword is not the affirmative, specific marketing opt-in Meta requires.
    """
    lifted_legacy_all = False
    lifted_any = False
    for category in (consent.MARKETING_CATEGORY, domain.CATEGORY_ALL):
        pref = await selectors.get_preference(session, organization_id, phone, "whatsapp", category)
        if pref is None or not pref.suppressed or pref.reason != _CUSTOMER_STOP_REASON:
            continue
        await _set_whatsapp_preference(
            session,
            organization_id=organization_id,
            phone=phone,
            suppressed=False,
            reason="customer_start",
            category=category,
        )
        lifted_any = True
        lifted_legacy_all = lifted_legacy_all or category == domain.CATEGORY_ALL
    if not lifted_any:
        log.info("notification.whatsapp.start_ignored")
        return
    await consent.restore(
        session,
        organization_id=organization_id,
        subject=consent.subject_for("whatsapp", phone),
        purposes=consent.BOTH_PURPOSES if lifted_legacy_all else (consent.MARKETING,),
        source=consent.SOURCE_INBOUND_KEYWORD,
        evidence=_keyword_evidence("START", reference),
    )
    log.info("notification.whatsapp.start_received")


async def apply_meta_marketing_preference(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    recipient: str,
    opted_out: bool,
    signal: str,
) -> bool:
    """Fold a marketing-preference signal **from Meta** into the same two stores STOP writes.

    Two sources reach here: the `user_preferences` webhook (`value: "stop"` / `"resume"` for
    `category: "marketing_messages"`) and a `failed` status carrying error 131050 ("recipient has
    chosen to stop receiving marketing messages"). `recipient` is Meta's bare-digit `wa_id` /
    `recipient_id`; `signal` is a short machine label (`user_preferences`, `error_131050`) that
    goes into the ledger's evidence — never the number, never free text from the payload.

    * **stop** — `Preference(marketing)` suppressed and a marketing `opted_out` ledger event
      (`consent.opt_out`, source `meta_user_preference`), audited by `_set_whatsapp_preference`.
    * **resume** — the reverse, but only through `consent.restore`: consent that existed is
      reinstated, consent that never did is not invented. Whether Meta's `resume` is itself
      an affirmative marketing opt-in in the sense Meta's policy requires is *unverified*, so it
      is deliberately not treated as one. A refusal an operator recorded (a different `reason`)
      is left alone, exactly as START leaves it.

    Idempotent: `consent.opt_out`/`restore` write only on a state change, and the preference
    upsert is a no-op rewrite of the same values. Returns whether the recipient was a usable
    number (False → nothing written).
    """
    phone = webhooks.sender_e164(recipient)
    if phone is None:
        log.warning("notification.whatsapp.marketing_preference_bad_recipient", signal=signal)
        return False
    evidence = (
        f"Meta reported the recipient {'stopped' if opted_out else 'resumed'} marketing ({signal})."
    )
    subject = consent.subject_for("whatsapp", phone)
    if opted_out:
        existing = await selectors.get_preference(
            session, organization_id, phone, "whatsapp", consent.MARKETING_CATEGORY
        )
        # An operator's own suppression keeps its reason; only a fresh or Meta/customer-made one
        # is (re)written under Meta's.
        if (
            existing is None
            or existing.reason in _CUSTOMER_REVERSIBLE_REASONS
            or not existing.suppressed
        ):
            await _set_whatsapp_preference(
                session,
                organization_id=organization_id,
                phone=phone,
                suppressed=True,
                reason=_META_STOP_REASON,
            )
        await consent.opt_out(
            session,
            organization_id=organization_id,
            subject=subject,
            purposes=(consent.MARKETING,),
            source=consent.SOURCE_META_PREFERENCE,
            evidence=evidence,
        )
        log.info("notification.whatsapp.marketing_stop_from_meta", signal=signal)
        return True

    existing = await selectors.get_preference(
        session, organization_id, phone, "whatsapp", consent.MARKETING_CATEGORY
    )
    if existing is not None and existing.suppressed:
        if existing.reason not in _CUSTOMER_REVERSIBLE_REASONS:
            # An operator's suppression: Meta's `resume` is not the thing that lifts it.
            log.info("notification.whatsapp.marketing_resume_ignored")
            return True
        await _set_whatsapp_preference(
            session,
            organization_id=organization_id,
            phone=phone,
            suppressed=False,
            reason="meta_marketing_resume",
        )
    await consent.restore(
        session,
        organization_id=organization_id,
        subject=subject,
        purposes=(consent.MARKETING,),
        source=consent.SOURCE_META_PREFERENCE,
        evidence=evidence,
    )
    log.info("notification.whatsapp.marketing_resume_from_meta", signal=signal)
    return True


async def resolve_conversation_contact(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
    contact_id: str | None,
) -> bool:
    """Fold one **definite** CRM answer onto a conversation (`app/consumers.py`'s
    `fold_inbound_contact` — never call this for a resolver that could not be asked at all; see
    `app/contacts.ContactResolution`).

    Idempotent by comparison rather than by a guard at the top: setting the same contact id
    twice, or re-confirming "unknown" a second time, writes nothing new. A conversation the event
    named that no longer exists (deleted, or a stale redelivery) is silently skipped — there is
    nothing to fold onto and nothing to retry into existing.

    Returns whether a row was actually written, so the caller can tell "already correct" from
    "nothing to fold onto" without a second read.
    """
    conversation = await selectors.get_conversation(session, organization_id, conversation_id)
    if conversation is None:
        return False
    try:
        new_contact_id = uuid.UUID(contact_id) if contact_id else None
    except ValueError:
        # customer-service answered with something that is not a UUID at all — its own bug, not
        # this conversation's, and not a reason to crash the fold or overwrite a good value with
        # garbage. Treated as "no usable answer" rather than "the answer is nobody".
        log.error(
            "notification.inbound.contact_id_unparseable", conversation_id=str(conversation_id)
        )
        return False
    if conversation.contact_id == new_contact_id:
        return False
    conversation.contact_id = new_contact_id
    conversation.updated_by = SYSTEM_ACTOR
    await session.flush()
    return True


async def reply_to_conversation(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    conversation_id: uuid.UUID,
    body: str,
    idempotency_key: str,
    request: Request | None = None,
) -> Message:
    """Send free text inside one conversation's 24-hour window, or refuse with the remedy.

    The conversation is row-locked (`selectors.get_conversation(..., lock=True)`) for the whole
    of this transaction: a webhook folding a new inbound message for the same number blocks on
    the lock rather than racing the window check underneath it, so "is the window open" and "act
    on that answer" happen as one atomic step instead of two reads that could each see a
    different truth. Outside the window this raises before anything is staged — no message row,
    no dispatch, no partial send to explain.
    """
    conversation = await selectors.get_conversation(
        session, ctx.organization_id, conversation_id, lock=True
    )
    if conversation is None:
        raise NotFoundProblem("No such conversation")
    if not webhooks.within_service_window(conversation.last_customer_message_at):
        raise OutsideServiceWindowProblem(
            "This conversation's 24-hour service window has closed. Send an approved "
            "template instead of free text."
        )

    cfg = settings()
    phone = pii.open_sealed(conversation.wa_address_enc, settings=cfg)

    dedupe_key = f"engage:reply:{conversation_id}:{idempotency_key}"
    message = await stage_message(
        session,
        organization_id=ctx.organization_id,
        channel="whatsapp",
        # Not `operational`, `marketing` or any rule-authored bucket: this is a human answering
        # a human, one conversation at a time, and it is checked against `Preference` exactly
        # like any other category. STOP now records a **marketing** refusal only
        # (`_record_stop_request`), so it does not silence a human answering the customer's own
        # message inside the service window; a `*` row an operator (or a pre-change STOP) wrote
        # still does.
        category="conversational",
        to_address=phone,
        subject=None,
        body=body,
        created_by=str(ctx.principal.sub),
        dedupe_key=dedupe_key,
        actor=_actor(ctx),
    )
    if message is None:
        # The same idempotency key was used before — the caller gets back what that first call
        # produced, not a second message.
        message = await selectors.find_by_dedupe_key(session, ctx.organization_id, dedupe_key)
        assert message is not None  # the dedupe_key that just conflicted has to name a row
    else:
        message.conversation_id = conversation_id
        message.direction = "outbound"
        await session.flush()

    if conversation.status != "open":
        conversation.status = "open"
        conversation.updated_by = str(ctx.principal.sub)
        conversation.updated_at = datetime.now(UTC)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.inbox.message.replied",
        entity="conversation",
        entity_id=conversation.id,
        after={"message_id": str(message.id), "state": message.state},
        request=request,
    )
    return message


async def assign_conversation(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    conversation_id: uuid.UUID,
    assignee_membership_id: uuid.UUID | None,
    request: Request | None = None,
) -> Conversation:
    """Hand a conversation to a membership, or clear its assignment with `None`.

    `assignee_membership_id` is verified against account-service before it is ever written —
    `memberships.membership_is_active` — because an unverified id here is not a display glitch,
    it is a conversation silently routed to nobody. Clearing the assignment (`None`) needs no
    such check: there is no id to misroute to.
    """
    if assignee_membership_id is not None:
        confirmed = await memberships.membership_is_active(
            settings(), str(ctx.organization_id), str(assignee_membership_id)
        )
        if not confirmed:
            raise ValidationProblem(
                "That membership does not belong to this organization, is not active, or "
                "could not be confirmed."
            )

    conversation = await selectors.get_conversation(session, ctx.organization_id, conversation_id)
    if conversation is None:
        raise NotFoundProblem("No such conversation")

    before = conversation.assignee_membership_id
    conversation.assignee_membership_id = assignee_membership_id
    conversation.updated_by = str(ctx.principal.sub)
    # Set explicitly rather than left to the column's `onupdate=func.now()`: this row is
    # serialised back to the caller in the same request, and a server-generated value is not
    # populated on the ORM object until a round trip refreshes it — the same reason `update_list`
    # and every other same-request read-after-write here does this.
    conversation.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.inbox.conversation.assigned",
        entity="conversation",
        entity_id=conversation.id,
        before={"assignee_membership_id": str(before) if before else None},
        after={
            "assignee_membership_id": (
                str(assignee_membership_id) if assignee_membership_id else None
            )
        },
        request=request,
    )
    return conversation


async def close_conversation(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    conversation_id: uuid.UUID,
    request: Request | None = None,
) -> Conversation:
    """Mark a conversation closed. A later reply, or the customer writing back, reopens it."""
    conversation = await selectors.get_conversation(session, ctx.organization_id, conversation_id)
    if conversation is None:
        raise NotFoundProblem("No such conversation")

    before = conversation.status
    conversation.status = "closed"
    conversation.updated_by = str(ctx.principal.sub)
    conversation.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.inbox.conversation.closed",
        entity="conversation",
        entity_id=conversation.id,
        before={"status": before},
        after={"status": "closed"},
        request=request,
    )
    return conversation


async def unmask_inbound_message(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    request: Request | None = None,
) -> str | None:
    """Reveal one customer message's real body — the audited, stepped-up path.

    Mirrors `customer.services.unmask_contact_phone`: reaching here means the caller held the
    dangerous `engage.inbox.message.unmask` permission and stepped up (`amr={"otp"}`). An
    `entity.sensitive_read` row is written in the caller's transaction whether or not anything
    was sealed — the auditable act is the attempt, not whether a body happened to be text.
    """
    row = await selectors.get_inbound_message(
        session, ctx.organization_id, conversation_id, message_id
    )
    if row is None:
        raise NotFoundProblem("No such message")

    plaintext: str | None = None
    if row.body_enc is not None:
        plaintext = pii.open_sealed(row.body_enc, settings=settings())

    record_sensitive_read(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        entity="inbound_message",
        entity_id=row.id,
        fields=["body"],
        request=request,
    )
    return plaintext


# --------------------------------------------------------------------- engage sending identity
#
# How an organization sends as itself: one provider connection, the domains it has proven it
# owns, and the named senders mail goes out as. The order is a gate, not a wizard's convenience
# — a sender is unusable until a test message has actually left the building.

#: The pure-rules module under a second name. `domain` is also what a customer calls the name
#: they send from, and the writers below take `domain=` because that is the field the API and the
#: setup screen use — so the module yields the word rather than the parameter taking a worse one.
_rules = domain

EVT_CONNECTION_CREATED = "engage.setup.connection.created"
EVT_CONNECTION_REPLACED = "engage.setup.connection.replaced"
EVT_WHATSAPP_CONNECTION_CREATED = "engage.setup.whatsapp.created"
EVT_WHATSAPP_CONNECTION_REPLACED = "engage.setup.whatsapp.replaced"
EVT_DOMAIN_CREATED = "engage.setup.domain.created"
EVT_DOMAIN_VERIFIED = "engage.setup.domain.verified"
EVT_SENDER_CREATED = "engage.setup.sender.created"
EVT_SENDER_VERIFIED = "engage.setup.sender.verified"

#: Kinds whose submission host is derived entirely from the provider, never from the caller —
#: fixed, known-public endpoints. `smtp` is the only kind whose host is a customer's own value,
#: and the only one the outbound dial guard below ever has to run against; asking `platform`'s
#: (unreachable) host through the guard would be pointless, and its writer refuses before this
#: is consulted anyway.
_FIXED_PROVIDER_KINDS: Final[frozenset[str]] = frozenset({"google", "microsoft"})

#: The one sentence a caller ever sees for a submission host or port the dial guard refused.
#: Deliberately not the reason: "that resolves to a private address" tells a probe its target
#: was reachable, and "that isn't a real domain" tells it the opposite. A single fixed sentence
#: for every rejection is what keeps a scan of this endpoint from learning anything at all.
_HOST_NOT_ALLOWED = (
    "The submission host must be a public mail server hostname, and the port one of "
    "25, 465, 587, or 2525."
)

#: One or more DNS labels — the shape a DKIM selector actually has to be, since it becomes
#: `<selector>._domainkey.<domain>`. Checked here, at the boundary, so a selector shaped like
#: `a..b` or `.x` is refused with a sentence naming the field rather than reaching
#: `dns.name.from_text` inside the resolver at verify time, where dnspython's own
#: `EmptyLabel`/`BadEscape` would otherwise surface as a 500.
_DKIM_SELECTOR: Final = re.compile(
    r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9])?(?:\.[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9])?)*$"
)

#: What a test message says. Fixed text, because its only job is to prove the path: a customer
#: reading it needs to know which address it came from and that nothing else is expected of them.
TEST_SEND_SUBJECT = "OpsGrids test message"
TEST_SEND_BODY = (
    "This is a test message from OpsGrids.\n\n"
    "Receiving it means {address} can send mail through your provider, and that sender is now "
    "usable in your campaigns.\n"
)


async def _submission_endpoint(
    ctx: ScopeContext,
    *,
    kind: str,
    transport: domain.TransportSettings,
    host: str,
    port: int | None,
    resolver: dns_check.Resolver | None,
) -> tuple[str, int]:
    """The host and port a connection will submit on, decided in ONE place for every writer.

    `create_connection` and `replace_connection` both store what this returns. A second copy
    of this decision is how the save-time check gets skipped on one path: a fixed-provider
    kind never reads the caller's host, and a caller-named host must be a public mail server
    here, at rest, as well as at dial time.
    """
    if kind in _FIXED_PROVIDER_KINDS:
        # The provider's own host and port, full stop — both are `_KINDS[kind]` in `domain.py`,
        # never request data. A caller sending `host="postgres"`, `port=5432` alongside
        # `kind="google"` gets Google's own submission endpoint regardless: this is what closes
        # that off rather than merely discouraging it. Consistent, too, with why this kind asks
        # for neither in the first place — "the customer chose Google Workspace, not a port".
        submission_host = transport.host
        submission_port = transport.port
    else:
        submission_host = (host or transport.host).strip()
        if not submission_host:
            raise ValidationProblem("An SMTP connection needs the host its provider submits on")
        submission_port = port or transport.port

        # The one path where `host`/`port` are the caller's own words. Checked here, at rest,
        # and checked again by `_transport_for_connection` immediately before every dial — DNS
        # is free to answer this name differently between the two moments.
        try:
            await dns_check.assert_dialable(submission_host, submission_port, resolver=resolver)
        except dns_check.HostNotAllowed as exc:
            log.warning(
                "engage.connection.host_rejected",
                organization_id=str(ctx.organization_id),
                reason=str(exc),
            )
            raise ValidationProblem(_HOST_NOT_ALLOWED) from exc
    return submission_host, submission_port


async def create_connection(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    kind: str,
    username: str,
    secret: str,
    host: str = "",
    port: int | None = None,
    request: Request | None = None,
    resolver: dns_check.Resolver | None = None,
) -> EmailConnection:
    """Record how one organization sends email, its credential sealed on the way in.

    The transport is derived from the kind rather than asked for: the customer chose "Google
    Workspace", and a wizard that then asked them for a port would be asking them to look up
    something we already know. For `google` and `microsoft` that derivation is total — the
    caller's `host` is never even read — because a fixed-provider kind exists precisely so a
    customer cannot aim this connection anywhere but the provider they named.

    One per organization, refused here as well as by the unique constraint. The constraint is the
    net; a caller deserves a sentence naming the connection that already exists rather than an
    IntegrityError from the driver.

    An `smtp` connection's host and port are the one thing here that came from outside — this
    process is going to open a socket to whatever they name — so they pass the outbound dial
    guard before anything is stored. `resolver` exists only for that guard's own test to hand it
    a name that resolves differently than the real internet, the same way `verify_domain`'s does.
    """
    try:
        transport = _rules.transport_for(kind, username)
    except ValueError as exc:
        raise ValidationProblem(str(exc)) from exc

    if kind == "platform":
        # Modelled in `domain.transport_for` and deliberately unreachable: platform-managed
        # sending needs an MTA the platform does not run. Accepting the connection would let a
        # customer finish setup and discover at their first campaign that nothing sends.
        raise BusinessRuleProblem(
            "Platform-managed sending is not available yet — connect Google Workspace, "
            "Microsoft 365, or your own SMTP server."
        )

    existing = await selectors.get_connection(session, ctx.organization_id)
    if existing is not None:
        raise BusinessRuleProblem(
            f"This organization already sends through {existing.username or existing.kind}. "
            "Disconnect that connection before adding another."
        )

    submission_host, submission_port = await _submission_endpoint(
        ctx, kind=kind, transport=transport, host=host, port=port, resolver=resolver
    )

    # `seal` returns (ciphertext, mask); the mask is discarded deliberately. A hint of an app
    # password is a hint of a credential, and nothing in this product displays one.
    sealed, _ = pii.seal(secret, kind="generic", settings=settings())

    connection = EmailConnection(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        kind=kind,
        host=submission_host,
        port=submission_port,
        use_tls=transport.use_tls,
        username=username,
        secret_enc=sealed,
        daily_cap=transport.daily_cap,
        status="connected",
    )
    session.add(connection)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.connection.created",
        entity="email_connection",
        entity_id=connection.id,
        # Never the secret, and never the envelope either: an audit row is read by more people
        # than a credential should be, and ciphertext in it is one key away from plaintext.
        after={
            "kind": kind,
            "username": username,
            "host": submission_host,
            "port": connection.port,
            "daily_cap": connection.daily_cap,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_CONNECTION_CREATED,
        aggregate_type="email_connection",
        aggregate_id=connection.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "connection_id": str(connection.id),
            "kind": kind,
            "daily_cap": connection.daily_cap,
        },
        actor=_actor(ctx),
    )
    return connection


async def replace_connection(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    kind: str,
    username: str,
    secret: str,
    host: str = "",
    port: int | None = None,
    request: Request | None = None,
    resolver: dns_check.Resolver | None = None,
) -> EmailConnection:
    """Replace the one row `create_connection` refuses to add a second of.

    An app password gets rotated, a provider gets switched — and because the unique
    constraint is on `organization_id` alone, there is no way to have zero or two rows at
    once. **In place, not delete-then-create**: a delete followed by a create leaves a window
    with no connection at all, and a crash between the two steps strands the organization
    there. Overwriting the existing row inside one transaction has no such window — the
    caller either ends up with the old connection or the new one, never neither.

    The old credential is destroyed, not archived: `secret_enc` is overwritten with the newly
    sealed value and the prior ciphertext is not copied anywhere else this function touches
    (audit, outbox) before it is gone. There is no history column for it and there must not
    become one — a credential that no longer authenticates anything is not worth keeping a
    trail of, and a trail of it is one more place it can leak from.

    Every sender in the organization is reset to unusable in the same transaction. See the
    comment at that statement for why — this is the safety property the slice exists for.
    """
    try:
        transport = _rules.transport_for(kind, username)
    except ValueError as exc:
        raise ValidationProblem(str(exc)) from exc

    if kind == "platform":
        # Same refusal as `create_connection`, for the same reason: modelled but unreachable
        # until the platform runs an MTA of its own.
        raise BusinessRuleProblem(
            "Platform-managed sending is not available yet — connect Google Workspace, "
            "Microsoft 365, or your own SMTP server."
        )

    existing = await selectors.get_connection(session, ctx.organization_id)
    if existing is None:
        # PUT replaces a resource that already exists; a PUT with nothing to replace is a
        # client error, not an invitation to create one. `create_connection` already owns
        # creation (POST, precisely because a second call must fail) — a PUT that silently
        # created one too would leave two routes claiming the same job, and a caller who
        # mistypes the path would get a row instead of a sentence telling them so.
        raise NotFoundProblem("No email connection to replace. Connect a provider first.")

    # The same decision, by the same function, as `create_connection`: a replace is a second
    # way to put a host on the row, so it must not be a way around the check on the first.
    submission_host, submission_port = await _submission_endpoint(
        ctx, kind=kind, transport=transport, host=host, port=port, resolver=resolver
    )

    # Snapshotted before the row changes, for the audit `before`. Never the secret or its
    # envelope — that rule is not this snapshot's alone, `after` below keeps it too.
    before = {
        "kind": existing.kind,
        "username": existing.username,
        "host": existing.host,
        "port": existing.port,
    }

    # `seal` returns (ciphertext, mask); the mask is discarded deliberately, as in
    # `create_connection`. Sealed before anything on the row is touched, so a failure here
    # leaves the old connection intact rather than half-replaced.
    sealed, _ = pii.seal(secret, kind="generic", settings=settings())

    existing.kind = kind
    existing.host = submission_host
    existing.port = submission_port
    existing.use_tls = transport.use_tls
    existing.username = username
    # The overwrite. Assigning a new value to the same column, in the same row, in the same
    # transaction as everything else here — there is no intermediate state where the row
    # holds neither credential, and no second location the old one was ever copied to.
    existing.secret_enc = sealed
    existing.daily_cap = transport.daily_cap
    # A replace is presumed working until proven otherwise, exactly like a fresh connection —
    # `send_test` is what actually proves it, on whichever sender gets tested next.
    existing.status = "connected"
    existing.updated_by = str(ctx.principal.sub)

    # Every sender's `verified_at` is proof that a test message reached the provider through
    # *this* connection's credential. The moment the credential or the provider changes, that
    # proof is about a path that no longer exists — the new credential might not even
    # authenticate, and even if it does, nobody has sent a single message through it yet. A
    # sender left `usable` after this would let a campaign go out over a connection nobody has
    # tested, which is the exact failure the whole setup wizard exists to prevent. So every
    # sender in the organization is reset here, unconditionally, in the same transaction as
    # the credential swap — not just the ones a caller happens to remember to re-test.
    #
    # A single UPDATE rather than a loop over `selectors.senders_stmt`: an organization can
    # hold many senders, and looping a write per row is the N+1 this codebase treats as a bug.
    # `synchronize_session="fetch"` so any `Sender` already loaded into this session (a route
    # or a test holding one from an earlier call in the same transaction) is expired and
    # reflects the reset on its next read, rather than going stale in memory while the
    # database has already moved on.
    await session.execute(
        update(Sender)
        .where(Sender.organization_id == ctx.organization_id, Sender.deleted_at.is_(None))
        .values(verified_at=None, updated_by=str(ctx.principal.sub))
        .execution_options(synchronize_session="fetch")
    )

    if before["kind"] != kind:
        # A different provider means a different SPF target and a different DKIM shape entirely
        # (`domain.expected_records`) — every domain here was last checked against the *old*
        # provider's requirements, and a `verified` from that check says nothing about this one.
        # Reset to `pending` so the next `verify` re-derives and re-checks against what `kind`
        # actually requires now, the same reasoning as the sender reset just above and in the
        # same transaction as the credential swap that makes it true. `records` is cleared too —
        # not just `status` — so a read between now and the next verify shows the fresh preview
        # for the new provider rather than the old provider's records still marked `verified`.
        await session.execute(
            update(EmailDomain)
            .where(
                EmailDomain.organization_id == ctx.organization_id,
                EmailDomain.deleted_at.is_(None),
            )
            .values(status="pending", records=[], updated_by=str(ctx.principal.sub))
            .execution_options(synchronize_session="fetch")
        )

    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.connection.replaced",
        entity="email_connection",
        entity_id=existing.id,
        # Neither half carries the secret or its ciphertext — see the docstring. kind,
        # username, host and port are the whole of what changed that is safe to record.
        before=before,
        after={
            "kind": kind,
            "username": username,
            "host": submission_host,
            "port": existing.port,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_CONNECTION_REPLACED,
        aggregate_type="email_connection",
        aggregate_id=existing.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "connection_id": str(existing.id),
            "kind": kind,
            "daily_cap": existing.daily_cap,
        },
        actor=_actor(ctx),
    )
    return existing


async def create_whatsapp_connection(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    directory: AsyncSession,
    waba_id: str,
    phone_number_id: str,
    display_number: str,
    access_token: str,
    request: Request | None = None,
) -> ProviderAccount:
    """Record one organization's WhatsApp Cloud API account, its token sealed on the way in.

    One per organization, refused here as well as by the org+channel index — mirrors
    `create_connection`'s reasoning for email. `phone_number_id` is also globally unique (see
    `ProviderAccount`'s docstring: it is the webhook routing key), so a number already claimed by
    another organization is refused with a sentence rather than surfaced as an IntegrityError —
    checked on `directory`, the cross-tenant BYPASSRLS reader, because `session` is scoped to this
    caller's own organization by row-level security and would never see another tenant's row to
    conflict with in the first place. The `IntegrityError` guard below is the second net for the
    race between that check and this insert, not the primary mechanism.

    Before either check, `whatsapp_provider.verify_ownership` proves the supplied token can
    actually read this phone number from Meta and see it listed under the named WABA — without
    that, anyone who can reach this endpoint could claim a number they do not control simply by
    typing its id, and every one of that number's future inbound webhooks would route here.
    """
    try:
        await whatsapp_provider.verify_ownership(
            settings(), waba_id=waba_id, phone_number_id=phone_number_id, access_token=access_token
        )
    except whatsapp_provider.WhatsAppOwnershipError as exc:
        raise ValidationProblem(str(exc)) from exc

    existing = await selectors.get_whatsapp_connection(session, ctx.organization_id)
    if existing is not None:
        raise BusinessRuleProblem(
            "This organization already has a WhatsApp account connected. Disconnect it before "
            "adding another."
        )

    claimed = await selectors.account_for_phone_number(directory, phone_number_id)
    if claimed is not None:
        raise ConflictProblem("This WhatsApp phone number is already connected elsewhere.")

    sealed, _ = pii.seal(access_token, kind="generic", settings=settings())

    account = ProviderAccount(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        channel="whatsapp",
        waba_id=waba_id,
        phone_number_id=phone_number_id,
        display_number=display_number,
        access_token_enc=sealed,
        status="connected",
    )
    session.add(account)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictProblem("This WhatsApp phone number is already connected elsewhere.") from exc

    # Embedded Signup subscribes the app to the WABA as its own setup step; a manual connection
    # needs the same call or Meta never has anywhere to deliver this account's inbound messages
    # and delivery statuses — the connection would look `connected` and sending would still work,
    # with nothing ever heard back. Best-effort: a token that can read the number but lacks
    # `whatsapp_business_management` for this one call is not a reason to refuse the connection.
    await w1_services.subscribe_waba(waba_id, access_token)

    # Embedded Signup also registers the number for the Cloud API as its own setup step
    # (`w1_setup._step_register`); a manually connected number never goes through that wizard, so
    # without this call it could sit here `connected` in our own eyes while Meta refuses every
    # single send — a number that has never been registered cannot send or receive at all. Same
    # best-effort rule as `subscribe_waba` just above: see `register_number_if_needed`'s docstring
    # for why a registration refusal must not undo a connection Meta already proved this token
    # controls.
    await w1_services.register_number_if_needed(
        session, ctx.organization_id, waba_id, phone_number_id, access_token
    )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.whatsapp.created",
        entity="provider_account",
        entity_id=account.id,
        # Never the token or its ciphertext — same rule as the email connection's audit row.
        after={
            "waba_id": waba_id,
            "phone_number_id": phone_number_id,
            "display_number": display_number,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_WHATSAPP_CONNECTION_CREATED,
        aggregate_type="provider_account",
        aggregate_id=account.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "provider_account_id": str(account.id),
            "channel": "whatsapp",
        },
        actor=_actor(ctx),
    )
    return account


async def replace_whatsapp_connection(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    directory: AsyncSession,
    waba_id: str,
    phone_number_id: str,
    display_number: str,
    access_token: str,
    request: Request | None = None,
) -> ProviderAccount:
    """Replace the one WhatsApp account `create_whatsapp_connection` refuses to add a second of.

    In place, not delete-then-create, for the same reason `replace_connection` is: no window
    with no connection at all. The old token is overwritten, not archived — there is no history
    column for it and there must not become one. Ownership is proved the same way creation
    proves it — see `create_whatsapp_connection`'s docstring — and the uniqueness check runs on
    the same cross-tenant `directory` reader for the same reason.
    """
    try:
        await whatsapp_provider.verify_ownership(
            settings(), waba_id=waba_id, phone_number_id=phone_number_id, access_token=access_token
        )
    except whatsapp_provider.WhatsAppOwnershipError as exc:
        raise ValidationProblem(str(exc)) from exc

    existing = await selectors.get_whatsapp_connection(session, ctx.organization_id)
    if existing is None:
        raise NotFoundProblem("No WhatsApp account to replace. Connect one first.")

    if phone_number_id != existing.phone_number_id:
        claimed = await selectors.account_for_phone_number(directory, phone_number_id)
        if claimed is not None:
            raise ConflictProblem("This WhatsApp phone number is already connected elsewhere.")

    before = {
        "waba_id": existing.waba_id,
        "phone_number_id": existing.phone_number_id,
        "display_number": existing.display_number,
    }

    sealed, _ = pii.seal(access_token, kind="generic", settings=settings())

    existing.waba_id = waba_id
    existing.phone_number_id = phone_number_id
    existing.display_number = display_number
    existing.access_token_enc = sealed
    existing.status = "connected"
    existing.updated_by = str(ctx.principal.sub)
    # This path (pasting a token by hand) never carries a fresh Facebook user id -- there is no
    # `FB.login` round trip behind it at all -- so whatever identity link the *previous* token's
    # Embedded Signup left behind must not survive onto a token nobody has attributed to anyone.
    # Left in place, a deauthorize or data-deletion callback for the old login could later act on
    # a connection a different person now controls.
    existing.meta_user_id = None

    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictProblem("This WhatsApp phone number is already connected elsewhere.") from exc

    # Same reasoning as `create_whatsapp_connection`: re-subscribing is a harmless no-op if the
    # WABA was already subscribed, and it is exactly the fix for an org replacing credentials
    # because their webhooks never arrived in the first place.
    await w1_services.subscribe_waba(waba_id, access_token)

    # Same gap `create_whatsapp_connection` closes, and just as needed on a replace: the new
    # credentials may point at a number that was never registered for the Cloud API, and without
    # this call the replacement would look `connected` while every send to it still failed.
    await w1_services.register_number_if_needed(
        session, ctx.organization_id, waba_id, phone_number_id, access_token
    )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.whatsapp.replaced",
        entity="provider_account",
        entity_id=existing.id,
        before=before,
        after={
            "waba_id": waba_id,
            "phone_number_id": phone_number_id,
            "display_number": display_number,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_WHATSAPP_CONNECTION_REPLACED,
        aggregate_type="provider_account",
        aggregate_id=existing.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "provider_account_id": str(existing.id),
            "channel": "whatsapp",
        },
        actor=_actor(ctx),
    )
    return existing


async def create_domain(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    domain: str,
    request: Request | None = None,
) -> EmailDomain:
    """Add a sending domain. Nothing is generated yet — what it needs to publish depends on the
    organization's connection, which this domain may be added before, and `verify` is what
    derives that fresh every time it runs (`services.record_domain_check`).

    `domain` is normalised and validated by `dns_check.normalize_domain_name` before anything
    else sees it: a pasted URL becomes its bare domain, and anything that is not a plain public
    hostname — a path, a port, an IP literal, a single label — is refused at the boundary rather
    than accepted and stored as a host nothing can ever publish DNS for.
    """
    try:
        name = dns_check.normalize_domain_name(domain)
    except ValueError as exc:
        raise ValidationProblem(str(exc)) from exc

    if await selectors.get_domain_by_name(session, ctx.organization_id, name) is not None:
        raise ConflictProblem(f"{name} is already a sending domain for this organization")

    row = EmailDomain(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        domain=name,
        status="pending",
        records=[],
    )
    session.add(row)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.domain.created",
        entity="email_domain",
        entity_id=row.id,
        after={"domain": name, "status": row.status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DOMAIN_CREATED,
        aggregate_type="email_domain",
        aggregate_id=row.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "domain_id": str(row.id),
            "domain": name,
            "status": row.status,
        },
        actor=_actor(ctx),
    )
    return row


async def load_domain_for_check(
    session: AsyncSession, ctx: ScopeContext, *, domain_id: uuid.UUID
) -> tuple[EmailDomain, EmailConnection | None]:
    """The first of two acts a domain re-check takes: read what is on file, and nothing else.

    Returns the domain alongside the organization's *current* connection — provider-aware
    verification needs to know which provider's rules to check against, and that answer must be
    read here, before the DNS lookups run, rather than assumed from whatever the domain's own
    `records` last remembered.

    `record_domain_check` is the second act. The DNS lookups run between the two with **no
    transaction open at all** (D3) — a resolver having a bad day (`NoNameservers`, `Timeout`)
    must not hold a pool connection for however long it takes to time out, and unlike a portal
    charge or an e-invoice filing there is nothing here to leave inconsistent: a lookup that never
    gets its answer recorded because the process died has changed nothing anywhere, so a person
    pressing *verify* again (or the campaign path re-checking a sender's domain) is the whole
    recovery story — no reconciler is needed for a read of somebody else's DNS.
    """
    row = await selectors.get_domain(session, ctx.organization_id, domain_id)
    if row is None:
        raise NotFoundProblem("No such sending domain")
    connection = await selectors.get_connection(session, ctx.organization_id)
    return row, connection


async def record_domain_check(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    domain_id: uuid.UUID,
    checked: list[dict[str, Any]],
    request: Request | None = None,
) -> EmailDomain:
    """Fold an already-completed DNS answer into the domain's status — the second act, in its
    own transaction opened only after the lookups have returned.

    Re-reads the row rather than trusting a caller's instance from the first act's now-closed
    transaction — the same discipline every other "claim, call, record" split in this platform
    applies (payment's `finalize_charge`, finance's filing record step), because nothing handed
    across a network call in between can be assumed still current.

    `records` is replaced rather than mutated: `dns_check.check_domain_records` returns a copy,
    and JSONB only notices a reassignment. `checked` is empty when no provider was connected at
    load time, which folds to `pending` with nothing published — exactly the "connect your email
    provider first" state the setup screen shows.
    """
    row = await selectors.get_domain(session, ctx.organization_id, domain_id)
    if row is None:
        raise NotFoundProblem("No such sending domain")

    was = row.status
    row.records = checked
    row.status = _rules.fold_domain_status(checked)
    row.last_checked_at = datetime.now(UTC)
    row.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.domain.checked",
        entity="email_domain",
        entity_id=row.id,
        before={"status": was},
        after={
            "status": row.status,
            "records": {record["kind"]: record["status"] for record in checked},
        },
        request=request,
    )

    if row.status == "verified" and was != "verified":
        # Only on the transition. Everything downstream reads this event as permission to send
        # as the name, and republishing it on every re-check would make "when did this domain
        # become usable" unanswerable from the log.
        publish_event(
            session,
            OutboxEvent,
            organization_id=ctx.organization_id,
            event_type=EVT_DOMAIN_VERIFIED,
            aggregate_type="email_domain",
            aggregate_id=row.id,
            payload={
                "organization_id": str(ctx.organization_id),
                "domain_id": str(row.id),
                "domain": row.domain,
            },
            actor=_actor(ctx),
        )
    return row


async def verify_domain(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    domain_id: uuid.UUID,
    request: Request | None = None,
    resolver: dns_check.Resolver | None = None,
) -> EmailDomain:
    """Both acts run back to back, inside whatever transaction the caller already holds.

    Kept for the unit tests that verify the DNS-folding rules against a hand-built session and do
    not care about the transaction boundary. The real caller —
    `POST /engage/domains/{id}/verify` — calls `load_domain_for_check`, awaits
    `dns_check.check_domain_records` with **no session and no transaction open**, and then calls
    `record_domain_check`; see `app/api/engage.py`.
    """
    row, connection = await load_domain_for_check(session, ctx, domain_id=domain_id)
    checked = await dns_check.check_domain_records(
        row.domain,
        connection_kind=connection.kind if connection else None,
        dkim_selector=row.dkim_selector,
        spf_include=row.spf_include,
        resolver=resolver,
    )
    return await record_domain_check(
        session, ctx, domain_id=domain_id, checked=checked, request=request
    )


async def update_domain_settings(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    domain_id: uuid.UUID,
    dkim_selector: str | None,
    spf_include: str | None,
    request: Request | None = None,
) -> EmailDomain:
    """Edit the DKIM selector and/or the SMTP SPF include a customer names for themselves.

    Either change resets the domain to `pending`: the records this domain needs and the answer
    DNS gave for the old selector or include say nothing about the new one, and a domain left
    `verified` on stale grounds is exactly the failure the whole provider-aware rewrite exists to
    close. `verify` re-derives and re-checks on the customer's next click, same as after any
    other change that affects what a domain must publish.
    """
    row = await selectors.get_domain(session, ctx.organization_id, domain_id)
    if row is None:
        raise NotFoundProblem("No such sending domain")

    if spf_include is not None and not dns_check.is_public_fqdn(spf_include.strip().lower()):
        raise ValidationProblem("The SPF include must be a plain hostname, like amazonses.com")

    normalized_selector = dkim_selector.strip().lower() if dkim_selector is not None else None
    if normalized_selector is not None and not _DKIM_SELECTOR.match(normalized_selector):
        # A selector this loose reaches `dns.name.from_text` inside the resolver at verify time,
        # where dnspython's own `EmptyLabel`/`BadEscape` would otherwise surface as a 500 rather
        # than a sentence about the field that caused it.
        raise ValidationProblem(
            "The DKIM selector must look like a DNS label — letters, digits, hyphens and "
            "underscores, like google or sg1."
        )

    changed: dict[str, Any] = {}
    if normalized_selector is not None and normalized_selector != row.dkim_selector:
        changed["dkim_selector"] = (row.dkim_selector, normalized_selector)
        row.dkim_selector = normalized_selector
    if spf_include is not None:
        normalized_include = spf_include.strip().lower()
        if normalized_include != (row.spf_include or ""):
            changed["spf_include"] = (row.spf_include, normalized_include)
            row.spf_include = normalized_include

    if not changed:
        return row

    was = row.status
    row.status = "pending"
    # Cleared for the same reason `replace_connection` clears it on a provider switch: the
    # records shown must never be answered against a selector or include this domain no longer
    # asks DNS about — a stale `verified` DKIM row here would be the same class of failure this
    # whole rewrite exists to close.
    row.records = []
    row.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.domain.settings_updated",
        entity="email_domain",
        entity_id=row.id,
        before={"status": was, **{k: v[0] for k, v in changed.items()}},
        after={"status": row.status, **{k: v[1] for k, v in changed.items()}},
        request=request,
    )
    return row


async def delete_domain(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    domain_id: uuid.UUID,
    request: Request | None = None,
) -> None:
    """Delete a sending domain and every sender on it.

    Refused (409) when any of those senders is used by a campaign that is not a draft or
    cancelled — that campaign's record of what it sent as is not this domain's to take away, the
    same reasoning `delete_list` already applies to a list a campaign addressed. A draft or
    cancelled campaign never reached anyone, so its sender choice is free to go with the domain.
    """
    row = await selectors.get_domain(session, ctx.organization_id, domain_id)
    if row is None:
        raise NotFoundProblem("No such sending domain")

    domain_senders = (
        (
            await session.execute(
                select(Sender).where(
                    Sender.organization_id == ctx.organization_id,
                    Sender.domain_id == domain_id,
                    Sender.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    for sender in domain_senders:
        blocking = await selectors.campaigns_using_sender(session, ctx.organization_id, sender.id)
        if blocking:
            raise ConflictProblem(
                f"{sender.address} is used by {blocking} campaign(s) that are not a draft or "
                "cancelled; delete or reassign them before deleting this domain."
            )

    for sender in domain_senders:
        record_audit(
            session,
            AuditLog,
            organization_id=ctx.organization_id,
            actor_id=ctx.principal.sub,
            action="engage.sender.deleted",
            entity="sender",
            entity_id=sender.id,
            before={"address": sender.address, "domain": row.domain},
            request=request,
        )
        await session.delete(sender)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.domain.deleted",
        entity="email_domain",
        entity_id=row.id,
        before={"domain": row.domain, "status": row.status},
        request=request,
    )
    await session.delete(row)
    await session.flush()


async def create_sender(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    domain_id: uuid.UUID,
    name: str,
    address: str,
    reply_to: str = "",
    signature: str = "",
    department: str = "",
    request: Request | None = None,
) -> Sender:
    """Add a named identity mail may go out as. Unusable until a test send proves it.

    Two rules, and they are the same rule seen from both ends: the domain must be verified, and
    the address must be on it. Without the second, one verified domain becomes a licence to send
    as any name at all — which is the thing domain verification exists to prevent.
    """
    try:
        sender_address = _rules.normalize_recipient("email", address)
    except _rules.RuleError as exc:
        raise ValidationProblem(str(exc)) from exc

    email_domain = await selectors.get_domain(session, ctx.organization_id, domain_id)
    if email_domain is None:
        raise NotFoundProblem("No such sending domain")
    if email_domain.status != "verified":
        raise BusinessRuleProblem(
            f"{email_domain.domain} has not been verified yet. Publish its DNS records and "
            "verify it before adding senders."
        )
    if not sender_address.endswith(f"@{email_domain.domain}"):
        raise BusinessRuleProblem(f"{sender_address} is not an address on {email_domain.domain}")

    taken = await selectors.get_sender_by_address(session, ctx.organization_id, sender_address)
    if taken is not None:
        raise ConflictProblem(f"{sender_address} is already a sender")

    sender = Sender(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        domain_id=email_domain.id,
        name=name,
        address=sender_address,
        reply_to=reply_to,
        signature=signature,
        department=department,
    )
    session.add(sender)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.sender.created",
        entity="sender",
        entity_id=sender.id,
        after={"name": name, "address": sender_address, "domain": email_domain.domain},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_SENDER_CREATED,
        aggregate_type="sender",
        aggregate_id=sender.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "sender_id": str(sender.id),
            "domain_id": str(email_domain.id),
            "address": sender_address,
        },
        actor=_actor(ctx),
    )
    return sender


async def _transport_for_connection(
    connection: EmailConnection,
    *,
    from_address: str,
    resolver: dns_check.Resolver | None = None,
    executor: ThreadPoolExecutor | None = None,
) -> Transport:
    """The transport this connection describes, credential unsealed for the send.

    Unsealing here is machine-to-machine: the plaintext reaches an SMTP handshake and nothing
    else — no response body, no log line, no audit `after`. That is why this is not a sensitive
    *read* in the PII sense, and why the audit row the caller writes records that a test send
    happened rather than what it used.

    For an `smtp` connection this is also **the outbound dial guard's second and final check**.
    `create_connection` ran it once, against the host as it resolved the day the connection was
    saved; DNS answers to whoever controls the zone, and a name that was public then can be
    repointed at the compose network by the time a test send actually runs. So the guard runs
    again here, against a fresh resolution, and the transport dials the address that resolution
    returned rather than the name — a third lookup, made by the socket layer instead of by this
    guard, is exactly the gap a rebinding attack needs. `google` and `microsoft` connections
    carry a fixed, trusted host and skip both checks, the same as at creation.

    `executor` is `None` (the process's default pool) unless the caller passes
    `transports.CAMPAIGN_SMTP_EXECUTOR` — see that constant's own docstring for why a campaign's
    sends must never share a pool with a single, latency-sensitive send like a password reset.
    """
    cfg = settings()
    dial_host = connection.host
    if connection.kind not in _FIXED_PROVIDER_KINDS:
        try:
            dial_host = await dns_check.assert_dialable(
                connection.host, connection.port, resolver=resolver
            )
        except dns_check.HostNotAllowed as exc:
            log.warning(
                "engage.test_send.host_rejected",
                organization_id=str(connection.organization_id),
                reason=str(exc),
            )
            raise BusinessRuleProblem(_HOST_NOT_ALLOWED) from exc

    password = (
        pii.open_sealed(connection.secret_enc, settings=cfg) if connection.secret_enc else None
    )
    return SmtpTransport(
        host=dial_host,
        port=connection.port,
        username=connection.username or None,
        password=password,
        use_tls=connection.use_tls,
        timeout=cfg.smtp_timeout_seconds,
        from_address=from_address,
        executor=executor,
    )


async def send_test(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    sender_id: uuid.UUID,
    to_address: str,
    request: Request | None = None,
    transport: Transport | None = None,
    resolver: dns_check.Resolver | None = None,
) -> Sender:
    """**The gate.** Send a real message through the customer's own connection, and only if it
    arrives, mark the sender usable.

    Nothing else writes `verified_at`. That is what converts "we filled in a form" into "mail
    left the building", and it is the reason every check above it is worth having: a campaign
    selecting this sender is selecting a path somebody has actually walked.

    The send is not staged through the outbox, and that is not a violation of the rule that
    background work is enqueued after commit — this is not background work. Its result *is* the
    state change, and a customer pressing *send test* is waiting for the provider's answer, not
    for a queue. Nothing is written until the provider has given one.

    A failure raises one fixed sentence per class — unreachable, refused, or a host the outbound
    dial guard would not open a socket to. Never the provider's own words: an arbitrary
    `to_address` and a live SMTP handshake is exactly the shape a port scan wants an oracle from,
    and `535 5.7.8 ...` or a connection-refused errno is that oracle. The exception itself is
    logged, with a correlation id and this organization's, for whoever operates the connection to
    read; nothing from the socket reaches the caller.

    Rate limited per organization — `daily_cap` bounds a connected provider's patience, not a
    caller's, and an arbitrary `to_address` on every attempt makes this route worth bounding on
    its own rather than trusting that cap alone.
    """
    cfg = settings()
    await enforce_rate_limit(
        f"engage.test_send:{ctx.organization_id}",
        limit=cfg.engage_test_send_rate_limit_per_minute,
        window_seconds=60,
    )

    sender = await selectors.get_sender(session, ctx.organization_id, sender_id)
    if sender is None:
        raise NotFoundProblem("No such sender")

    connection = await selectors.get_connection(session, ctx.organization_id)
    if connection is None or connection.status != "connected":
        raise BusinessRuleProblem("Connect an email provider before sending a test message.")

    email_domain = await selectors.get_domain(session, ctx.organization_id, sender.domain_id)
    if email_domain is None or email_domain.status != "verified":
        # Checked again at send time rather than trusted from creation, for the same reason the
        # dispatcher re-checks suppression: a domain can fall out of verification between the
        # two moments, and of the two answers the later one is the true one.
        raise BusinessRuleProblem(
            f"The domain behind {sender.address} is no longer verified. Re-verify its DNS "
            "records before sending as it."
        )

    try:
        recipient = _rules.normalize_recipient("email", to_address)
    except _rules.RuleError as exc:
        raise ValidationProblem(str(exc)) from exc

    outgoing = Outgoing(
        to_address=recipient,
        subject=TEST_SEND_SUBJECT,
        body=TEST_SEND_BODY.format(address=sender.address),
    )
    wire = transport
    if wire is None:
        wire = await _transport_for_connection(
            connection, from_address=sender.address, resolver=resolver
        )
    try:
        receipt = await wire.send(outgoing)
    except TransportRefused as exc:
        # Nothing is written, so nothing is audited: the audit row would be rolled back with the
        # rest of the transaction and only the log would remember. The provider's own words stay
        # in the log, keyed to this sender — never in the response, which an arbitrary
        # `to_address` and a caller who chose the host turns into exactly the oracle a port scan
        # wants.
        log.warning("engage.test_send.refused", sender_id=str(sender.id), error=str(exc))
        raise BusinessRuleProblem(
            "The provider refused the test message. Check the connection's credentials and "
            "the sender's address."
        ) from exc
    except TransportUnavailable as exc:
        log.warning("engage.test_send.unavailable", sender_id=str(sender.id), error=str(exc))
        raise UpstreamUnavailableProblem(
            "The provider could not be reached. Try again in a few minutes."
        ) from exc

    sender.verified_at = datetime.now(UTC)
    sender.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.sender.verified",
        entity="sender",
        entity_id=sender.id,
        after={
            "address": sender.address,
            "tested_with": recipient,
            "provider": receipt.provider,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_SENDER_VERIFIED,
        aggregate_type="sender",
        aggregate_id=sender.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "sender_id": str(sender.id),
            "address": sender.address,
            "provider": receipt.provider,
            "provider_message_id": receipt.provider_message_id,
        },
        actor=_actor(ctx),
    )
    return sender


# --------------------------------------------------------------------- engage audience
#
# Contacts and suppressions. The address is PII and is handled the platform's way: normalised,
# then sealed (`email_enc`), masked (`email_masked`) and blind-indexed (`email_index`). Nothing
# here writes the plaintext anywhere but the sealed column — audit rows carry the mask.

#: The environments `ServiceSettings` already refuses development secrets in. There the
#: `dev-only-` unsubscribe key is publicly known, and a link signed with it anyone could forge.
_REAL_ENVIRONMENTS: Final[frozenset[str]] = frozenset({"production", "staging", "uat"})


def _normalize_email(raw: str) -> str:
    try:
        return _rules.normalize_recipient("email", raw)
    except _rules.RuleError as exc:
        raise ValidationProblem(str(exc)) from exc


def _sealed_email(email: str) -> tuple[bytes, str, str]:
    """`(sealed, masked, index)` for a normalised address."""
    sealed, masked = pii.seal(email, kind="email", settings=settings())
    return sealed, masked, selectors.email_index(email)


#: What the ledger records when an operator adds a contact. It is an **attestation** — the
#: organization saying it may email this address — and the text says so: nothing here checks it,
#: and a reader of the ledger must not mistake it for a customer's own opt-in.
_CONTACT_ADDED_EVIDENCE = (
    "Added to the audience by an operator. The organization attests that it may email this "
    "address; no separate opt-in evidence was captured (unverified)."
)
_CONTACT_IMPORTED_EVIDENCE = (
    "Imported into the audience by an operator. The organization attests that it may email these "
    "addresses; no separate opt-in evidence was captured (unverified)."
)


def _email_subject(*, index: str, enc: bytes, masked: str) -> consent.Subject:
    return consent.Subject(channel="email", index=index, enc=enc, masked=masked)


async def _attest_new_contacts(
    session: AsyncSession,
    ctx: ScopeContext,
    contacts: list[Contact],
    *,
    source: str,
    evidence: str,
) -> None:
    """Record the organization's opt-in attestation for contacts an operator just added, so "add a
    contact, then send a campaign" keeps working now that marketing needs an opt-in on record.
    Only where the ledger is silent for that address — see `consent.attest_opt_ins`."""
    await consent.attest_opt_ins(
        session,
        organization_id=ctx.organization_id,
        contacts=[
            consent.ContactSubject(
                subject=_email_subject(index=c.email_index, enc=c.email_enc, masked=c.email_masked),
                contact_id=c.id,
            )
            for c in contacts
        ],
        source=source,
        evidence=evidence,
        recorded_by=ctx.principal.sub,
    )


def unsubscribe_key() -> bytes | None:
    """The key unsubscribe links are signed with, or None where it must not be used.

    None — not an exception — when a real environment is still on the `dev-only-` placeholder,
    so a misconfigured deploy refuses every link (issuing and verifying alike) instead of
    honouring forgeries. The service keeps running; only this feature is off.
    """
    cfg = settings()
    secret = cfg.engage_unsubscribe_secret.get_secret_value()
    if secret.startswith(("dev-only-", "ci-only-")) and cfg.environment in _REAL_ENVIRONMENTS:
        return None
    return secret.encode()


def _check_public_base(cfg: Settings) -> None:
    """Refuse to send a campaign whose unsubscribe link would point nowhere a recipient's
    mailbox can reach.

    `ENGAGE_PUBLIC_API_BASE` defaults to `http://localhost:8080/api/v1` — right for a developer's
    own machine, and useless in anyone else's mail. Outside `local`/`test`/`ci` a base that is
    still `localhost`/`127.0.0.1` or still plain `http://` is exactly the same class of mistake
    the `dev-only-` secret prefix already refuses to sign with: an unconfigured deploy that would
    otherwise mail a link nobody outside the box can open. The gate lives here, at the one place
    every campaign send passes through, rather than only in `infrastructure/edge/preflight.sh` —
    preflight catches it before a deploy; this catches it if preflight was skipped or the value
    changed after.
    """
    if cfg.environment not in _REAL_ENVIRONMENTS:
        return
    base = cfg.engage_public_api_base.strip().lower()
    if not base.startswith("https://") or "localhost" in base or "127.0.0.1" in base:
        raise BusinessRuleProblem(
            "ENGAGE_PUBLIC_API_BASE is not a public https address in this environment, so "
            "campaigns cannot be sent."
        )


def unsubscribe_token(organization_id: uuid.UUID, email: str) -> str:
    """The v1 unsubscribe token for `email`.

    Nothing that mails a real recipient calls this any more — `claim_campaign_batch` mints
    `unsubscribe_token_for_recipient` instead, precisely because this shape puts the address in
    the link (see `domain`'s unsubscribe-link module docstring). It stays public for the tests
    that prove `verify_unsubscribe_link` still accepts a v1 link already sent.
    """
    key = unsubscribe_key()
    if key is None:
        raise UpstreamUnavailableProblem("Unsubscribe links are not configured")
    return _rules.make_unsubscribe_token(organization_id, _normalize_email(email), key)


def unsubscribe_token_for_recipient(organization_id: uuid.UUID, recipient_id: uuid.UUID) -> str:
    """The token that goes into the unsubscribe link of a campaign delivery. Opaque — see
    `domain`'s unsubscribe-link module docstring for why this, rather than `unsubscribe_token`,
    is what every real send uses."""
    key = unsubscribe_key()
    if key is None:
        raise UpstreamUnavailableProblem("Unsubscribe links are not configured")
    return _rules.make_unsubscribe_token_v2(organization_id, recipient_id, key)


#: v1 links stop verifying on this date, even though the MAC would still check out — see
#: `domain`'s unsubscribe-link module docstring. Chosen as a stated window rather than an
#: instant cutover: mail already delivered before this change keeps its links working long
#: enough that a recipient reading a backlogged newsletter can still leave, but not
#: indefinitely — every day past it is another day a real address keeps working from
#: cleartext an old access log entry.
_V1_UNSUBSCRIBE_SUNSET: Final = date(2026, 12, 22)


def verify_unsubscribe_link(token: str) -> domain.UnsubscribeTarget | None:
    """The one place a route resolves an unsubscribe token — GET's confirmation page and POST's
    actual unsubscribe both call this, so the two can never disagree about what a token names,
    and neither has to know the sunset policy for itself.
    """
    key = unsubscribe_key()
    if key is None:
        return None
    target = _rules.verify_unsubscribe_token(token, key)
    if target is None:
        return None
    if target.email is not None and datetime.now(UTC).date() > _V1_UNSUBSCRIBE_SUNSET:
        return None
    return target


async def create_contact(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    email: str,
    name: str = "",
    attributes: dict[str, Any] | None = None,
    request: Request | None = None,
) -> Contact:
    normalized = _normalize_email(email)
    sealed, masked, index = _sealed_email(normalized)
    if await selectors.contacts_by_index(session, ctx.organization_id, [index]):
        raise ConflictProblem(f"{masked} is already a contact")

    contact = Contact(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        email_enc=sealed,
        email_masked=masked,
        email_index=index,
        name=name,
        attributes=attributes or {},
    )
    session.add(contact)
    await session.flush()
    await _attest_new_contacts(
        session, ctx, [contact], source=consent.SOURCE_ADMIN, evidence=_CONTACT_ADDED_EVIDENCE
    )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.contact.created",
        entity="contact",
        entity_id=contact.id,
        after={"email": masked, "name": name},
        request=request,
    )
    return contact


async def update_contact(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    contact_id: uuid.UUID,
    email: str | None = None,
    name: str | None = None,
    attributes: dict[str, Any] | None = None,
    request: Request | None = None,
) -> Contact:
    contact = await selectors.get_contact(session, ctx.organization_id, contact_id)
    if contact is None:
        raise NotFoundProblem("No such contact")

    before = {"email": contact.email_masked, "name": contact.name}
    changed_address = False
    if email is not None:
        normalized = _normalize_email(email)
        index = selectors.email_index(normalized)
        if index != contact.email_index:
            if await selectors.contacts_by_index(session, ctx.organization_id, [index]):
                raise ConflictProblem("Another contact already has that address")
            contact.email_enc, contact.email_masked, contact.email_index = _sealed_email(normalized)
            changed_address = True
    if name is not None:
        contact.name = name
    if attributes is not None:
        contact.attributes = attributes
    contact.updated_by = str(ctx.principal.sub)
    contact.updated_at = datetime.now(UTC)
    await session.flush()
    if changed_address:
        # A new address is a new subject with no consent history, so without this an edited
        # contact would quietly drop out of every campaign. Only where the ledger is silent for
        # the new address — an address that opted out elsewhere keeps its refusal.
        await _attest_new_contacts(
            session, ctx, [contact], source=consent.SOURCE_ADMIN, evidence=_CONTACT_ADDED_EVIDENCE
        )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.contact.updated",
        entity="contact",
        entity_id=contact.id,
        before=before,
        after={"email": contact.email_masked, "name": contact.name},
        request=request,
    )
    return contact


async def delete_contact(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    contact_id: uuid.UUID,
    request: Request | None = None,
) -> None:
    """Deletes the row outright. A suppression for the same address is untouched: removing
    someone from the audience must never make them mailable again on re-import.

    A campaign's own record of having mailed this contact is not untouched, though: it is
    erased, in the same transaction, by `erase_contact_campaign_copies` below. `campaigns` keeps
    the contact's sealed address as a snapshot of who a send went to (see `CampaignRecipient`'s
    docstring) and a contact deletion is exactly the request whose honest answer is that the
    address is gone — everywhere, not just from `contacts`.
    """
    contact = await selectors.get_contact(session, ctx.organization_id, contact_id)
    if contact is None:
        raise NotFoundProblem("No such contact")
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.contact.deleted",
        entity="contact",
        entity_id=contact.id,
        before={"email": contact.email_masked, "name": contact.name},
        request=request,
    )
    await session.delete(contact)
    await session.flush()
    await erase_contact_campaign_copies(
        session,
        organization_id=ctx.organization_id,
        contact_id=contact_id,
        actor_id=ctx.principal.sub,
        request=request,
    )


#: What `erase_contact_campaign_copies` overwrites `email_enc` with — never the real address
#: again. Not a crypto-shred in the strict sense (the encryption key itself is never rotated or
#: destroyed by this call; see the module note on erasure limits): this is an ordinary
#: application-level overwrite, and the platform's usual data-retention horizons still apply —
#: backups taken before this commits, WAL not yet recycled, and the dead tuple Postgres has not
#: yet vacuumed all still hold the row this replaced, for as long as this deployment's backup
#: and WAL retention windows say.
_ERASED_PLACEHOLDER = "erased contact"
#: What it writes to `email_masked` — a hint that says plainly what happened, rather than
#: `pii.mask`'s generic bullets-plus-tail over the placeholder above, which would read as a
#: broken mask rather than as "this address was erased" to whoever looks at the recipient list.
_ERASED_MASK = "(erased)"
#: The one failure reason for "this recipient's address is gone" — written by
#: `erase_contact_campaign_copies` for a row still `queued` when erasure runs, and by
#: `claim_campaign_batch`/`drop_erased_from_batch` for a row erased after being claimed. One
#: string so a report never has to explain why the same outcome reads two different ways.
_ERASED_ERROR: Final = "This recipient's address was erased"


async def erase_contact_campaign_copies(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    request: Request | None = None,
) -> int:
    """Overwrite a contact's sealed address in every `campaign_recipients` row it was ever
    copied into, in this organization. Returns how many rows were touched.

    Called from `delete_contact`, in the same transaction as the contact's own deletion — but
    takes an `organization_id` and a `contact_id` rather than a `ScopeContext` so a future
    cross-service erasure event can call it the same way `unsubscribe` already handles a
    suppression with no principal of its own (there is no generic erasure event on this platform
    yet; `grep -rn erasure services libs` finds none, so this is the one path that exists today).

    Matched on `contact_id` rather than the blind index: `CampaignRecipient.contact_id` is a
    plain, FK-less column copied at expansion for exactly this reason — it names *this* contact
    even after the `contacts` row is gone, where the blind index would also catch a different,
    later contact re-imported under the same address. `email_enc` is re-sealed over a placeholder
    instead of nulled or zeroed, so the column stays `NOT NULL` and valid ciphertext for the one
    caller that ever opens it (`claim_campaign_batch`, which checks `erased_at` first and never
    actually does).

    A row still `queued` is also moved straight to `failed` here, in the same statement — not
    left for the driver to discover. Without this, a contact deleted between expansion and claim
    would leave a `queued` row an ordinary claim could still pick up, decrypt, and send before
    anything downstream ever re-checks `erased_at`. A row already `sending` (mid-claim elsewhere)
    is not touched by this — `drop_erased_from_batch` is what catches that narrower window,
    immediately before the batch reaches a transport. Rows already erased are skipped, so a
    repeat call (a second deletion attempt, a redelivered erasure event) is a no-op.
    """
    sealed, _masked = pii.seal(_ERASED_PLACEHOLDER, kind="generic", settings=settings())
    # `session.execute` is typed to return the generic `Result`, but an UPDATE statement always
    # produces a `CursorResult` at runtime — which is what actually carries `rowcount`.
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            update(CampaignRecipient)
            .where(
                CampaignRecipient.organization_id == organization_id,
                CampaignRecipient.contact_id == contact_id,
                CampaignRecipient.erased_at.is_(None),
            )
            .values(
                email_enc=sealed,
                email_masked=_ERASED_MASK,
                email_index="",
                erased_at=datetime.now(UTC),
                status=case(
                    (CampaignRecipient.status == "queued", "failed"),
                    else_=CampaignRecipient.status,
                ),
                error=case(
                    (CampaignRecipient.status == "queued", _ERASED_ERROR),
                    else_=CampaignRecipient.error,
                ),
            )
        ),
    )
    erased = result.rowcount or 0
    if erased:
        record_audit(
            session,
            AuditLog,
            organization_id=organization_id,
            actor_id=actor_id,
            action="engage.campaign_recipient.erased",
            entity="contact",
            entity_id=contact_id,
            after={"rows_erased": erased},
            request=request,
        )
    return erased


async def import_contacts(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    rows: list[tuple[str, str, dict[str, Any]]],
    request: Request | None = None,
) -> list[tuple[str, Contact | None, str | None]]:
    """Upsert a batch of `(email, name, attributes)` keyed on the normalised address.

    Returns one `(status, contact, error)` per input row, in order. Idempotent: replaying the
    same batch creates nothing new and reports every valid row `updated`. A row repeating an
    earlier row's address updates the contact that row created. One audit row for the batch,
    counts only — a thousand masked addresses in one audit entry would be noise, not evidence.
    """
    prepared: list[tuple[str, str, dict[str, Any], str] | str] = []
    for email, name, attributes in rows:
        try:
            normalized = _rules.normalize_recipient("email", email)
        except _rules.RuleError as exc:
            prepared.append(str(exc))
            continue
        if len(normalized) > 254:
            prepared.append("Address is longer than 254 characters")
            continue
        if len(name) > 200:
            prepared.append("Name is longer than 200 characters")
            continue
        prepared.append((normalized, name, attributes, selectors.email_index(normalized)))

    indexes = [p[3] for p in prepared if isinstance(p, tuple)]
    existing = await selectors.contacts_by_index(session, ctx.organization_id, indexes)

    results: list[tuple[str, Contact | None, str | None]] = []
    created = updated = 0
    actor = str(ctx.principal.sub)
    for item in prepared:
        if isinstance(item, str):
            results.append(("invalid", None, item))
            continue
        normalized, name, attributes, index = item
        contact = existing.get(index)
        if contact is None:
            sealed, masked = pii.seal(normalized, kind="email", settings=settings())
            contact = Contact(
                organization_id=ctx.organization_id,
                created_by=actor,
                email_enc=sealed,
                email_masked=masked,
                email_index=index,
                name=name,
                attributes=attributes,
            )
            session.add(contact)
            existing[index] = contact
            created += 1
            results.append(("created", contact, None))
        else:
            contact.name = name
            contact.attributes = attributes
            contact.updated_by = actor
            contact.updated_at = datetime.now(UTC)
            updated += 1
            results.append(("updated", contact, None))
    await session.flush()
    await _attest_new_contacts(
        session,
        ctx,
        [c for outcome, c, _ in results if outcome == "created" and c is not None],
        source=consent.SOURCE_IMPORT,
        evidence=_CONTACT_IMPORTED_EVIDENCE,
    )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.contact.imported",
        entity="contact",
        after={"created": created, "updated": updated, "invalid": len(rows) - created - updated},
        request=request,
    )
    return results


async def add_suppression(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    email: str,
    request: Request | None = None,
) -> Suppression:
    """A person's manual suppression. Refused if the address is already suppressed for any
    reason: overwriting `complained` with `manual` would make it liftable."""
    normalized = _normalize_email(email)
    sealed, masked, index = _sealed_email(normalized)
    if await selectors.get_suppression_by_index(session, ctx.organization_id, index):
        raise ConflictProblem(f"{masked} is already suppressed")

    row = Suppression(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        email_enc=sealed,
        email_masked=masked,
        email_index=index,
        reason="manual",
    )
    session.add(row)
    await session.flush()
    # A manual suppression only ever stopped campaigns, so it opts out of marketing alone —
    # the same mapping the backfill applied to every one that already existed.
    await consent.opt_out(
        session,
        organization_id=ctx.organization_id,
        subject=_email_subject(index=index, enc=sealed, masked=masked),
        purposes=(consent.MARKETING,),
        source=consent.SOURCE_ADMIN,
        evidence="Manual suppression added by an operator.",
        recorded_by=ctx.principal.sub,
    )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.suppression.added",
        entity="suppression",
        entity_id=row.id,
        after={"email": masked, "reason": "manual"},
        request=request,
    )
    return row


async def remove_suppression(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    suppression_id: uuid.UUID,
    request: Request | None = None,
) -> None:
    """Lift a manual suppression. Any other reason is the recipient's or their provider's
    decision, not the organization's to reverse."""
    row = await selectors.get_suppression(session, ctx.organization_id, suppression_id)
    if row is None:
        raise NotFoundProblem("No such suppression")
    if row.reason != "manual":
        raise BusinessRuleProblem(
            f"Only manual suppressions can be removed; this one is {row.reason}"
        )
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.suppression.removed",
        entity="suppression",
        entity_id=row.id,
        before={"email": row.email_masked, "reason": row.reason},
        request=request,
    )
    # Lifting the suppression must lift the ledger's own refusal too, or the operator's lift would
    # change nothing: `consent.restore` re-opens marketing where the address had opted in before,
    # and never invents an opt-in it did not have.
    await consent.restore(
        session,
        organization_id=ctx.organization_id,
        subject=_email_subject(index=row.email_index, enc=row.email_enc, masked=row.email_masked),
        purposes=(consent.MARKETING,),
        source=consent.SOURCE_ADMIN,
        evidence="Manual suppression lifted by an operator.",
        recorded_by=ctx.principal.sub,
    )
    await session.delete(row)
    await session.flush()


#: What the ledger records for a recipient's own click. No address, no token — a pointer to the
#: mechanism only.
_UNSUBSCRIBE_EVIDENCE = "Recipient used the unsubscribe link in a campaign email."


async def unsubscribe(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    email: str,
    request: Request | None = None,
) -> bool:
    """Record a recipient's unsubscribe. Returns whether a new suppression was written.

    The caller has already verified the token and bound the tenant GUC to `organization_id`.
    `ON CONFLICT DO NOTHING` makes a repeat click, a mail scanner prefetching the link, and two
    clicks racing each other all the same single row; an existing suppression of any reason is
    left as it is. Audited only when it wrote, with no actor — the recipient has no principal.
    """
    sealed, masked, index = _sealed_email(email)
    stmt = (
        pg_insert(Suppression)
        .values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            created_by="recipient:unsubscribe",
            email_enc=sealed,
            email_masked=masked,
            email_index=index,
            reason="unsubscribed",
        )
        .on_conflict_do_nothing(constraint="uq_suppression_org_email")
        .returning(Suppression.id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    # In the ledger whether or not a suppression already existed: the recipient's own act is
    # worth recording even over a bounce, and `opt_out` adds nothing to a history that already
    # ends in a refusal. An unsubscribe opts out of marketing only: it is the link in a
    # marketing mail, and an invoice or receipt is not one.
    await consent.opt_out(
        session,
        organization_id=organization_id,
        subject=_email_subject(index=index, enc=sealed, masked=masked),
        purposes=(consent.MARKETING,),
        source=consent.SOURCE_UNSUBSCRIBE_LINK,
        evidence=_UNSUBSCRIBE_EVIDENCE,
    )
    if inserted is None:
        return False
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        action="engage.suppression.unsubscribed",
        entity="suppression",
        entity_id=inserted,
        after={"email": masked, "reason": "unsubscribed"},
        request=request,
    )
    return True


async def unsubscribe_recipient(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    recipient_id: uuid.UUID,
    request: Request | None = None,
) -> tuple[bool, str] | None:
    """The v2 counterpart to `unsubscribe`: resolves the address from a `campaign_recipients`
    row a signed link names, rather than from plaintext the link itself carries (see `domain`'s
    unsubscribe-link module docstring). `email_enc`/`email_masked`/`email_index` are copied
    from that row exactly as it already held them — never decrypted, never re-derived — so this
    never touches `pii.open_sealed` and needs no sensitive-read audit of its own.

    Returns `(wrote, masked_email)` — `masked_email` handed back so the route that shows a
    result page needs no second query — or `None` when there is nothing to act on: the
    recipient id names no row in this organization, or it was erased
    (`erase_contact_campaign_copies`). The caller answers that identically to an invalid token
    — neither may learn anything, same as `unsubscribe`'s caller does for a bad MAC.
    """
    recipient = await selectors.get_recipient_for_unsubscribe(
        session, organization_id, recipient_id
    )
    if recipient is None:
        return None
    stmt = (
        pg_insert(Suppression)
        .values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            created_by="recipient:unsubscribe",
            email_enc=recipient.email_enc,
            email_masked=recipient.email_masked,
            email_index=recipient.email_index,
            reason="unsubscribed",
        )
        .on_conflict_do_nothing(constraint="uq_suppression_org_email")
        .returning(Suppression.id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    await consent.opt_out(
        session,
        organization_id=organization_id,
        subject=_email_subject(
            index=recipient.email_index, enc=recipient.email_enc, masked=recipient.email_masked
        ),
        purposes=(consent.MARKETING,),
        source=consent.SOURCE_UNSUBSCRIBE_LINK,
        evidence=_UNSUBSCRIBE_EVIDENCE,
        contact_id=recipient.contact_id,
    )
    if inserted is None:
        return False, recipient.email_masked
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        action="engage.suppression.unsubscribed",
        entity="suppression",
        entity_id=inserted,
        after={"email": recipient.email_masked, "reason": "unsubscribed"},
        request=request,
    )
    return True, recipient.email_masked


# --------------------------------------------------------------------- consent ledger (API)
#
# The operator-facing writes. The ledger's automatic writers (STOP/START, unsubscribe, a contact
# being added, a suppression or preference being set or lifted) live beside the thing that
# triggers them above; the decision itself is `app/consent.py`.

#: An event may be dated a little ahead of the server clock — the browser that recorded it is not
#: perfectly synchronised — but not meaningfully: a future-dated event would be "latest" until the
#: date arrived, outranking a real refusal recorded in the meantime.
_CONSENT_FUTURE_SKEW = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class RecordedConsent:
    """What `record_consent_event` did: the event, what the decision says now that it is in, and
    whether this was a replay of an earlier request under the same idempotency key."""

    event: ConsentEvent
    effective: consent.ConsentDecision
    replayed: bool


_EMAIL_IN_TEXT = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
#: A run of digits that could be a phone number: digits with the separators people put in one,
#: ten or more of them. A reference like "order 1042" has far fewer; a long order number can trip
#: this, which costs the operator one reworded line and never a leaked number.
_PHONE_IN_TEXT = re.compile(r"\+?\d[\d\s().-]{8,}\d")


def _evidence_names_a_person(evidence: str) -> bool:
    """Does free-text `evidence` carry an email address or something shaped like a phone number?"""
    if _EMAIL_IN_TEXT.search(evidence):
        return True
    return any(
        sum(ch.isdigit() for ch in match.group()) >= 10
        for match in _PHONE_IN_TEXT.finditer(evidence)
    )


async def record_consent_event(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    channel: str,
    purpose: str,
    status: str,
    source: str,
    evidence: str,
    address: str | None,
    contact_id: uuid.UUID | None,
    occurred_at: datetime | None,
    idempotency_key: str | None,
    request: Request | None = None,
) -> RecordedConsent:
    """Record that a subject opted in or out — who said so, when and through what.

    The subject is an `address` or, for an email contact, a `contact_id` (the address is then
    copied sealed from the contact, never opened — an operator working from the masked contact
    list has no plaintext to send). Both may be given if they agree. `source` is one of
    `consent.API_SOURCES`: the sources this service writes itself cannot be claimed here.

    **Recording a fact is not the same as it taking effect.** An `opted_in` for an address whose
    `Suppression` is `unsubscribed`, or whose WhatsApp `Preference` is a customer's STOP, is
    written — the person did say it — but the decision still refuses until the legacy store
    lifts, because nothing that was blocked before may be unblocked by adding a row. The answer
    is returned as `effective` so the caller sees which it is.

    Idempotent on `idempotency_key`: a retry returns the original event, and a key reused for a
    *different* event is a 409, not a silent second write.
    """
    if source not in consent.API_SOURCES:
        raise ValidationProblem(f"source must be one of {', '.join(consent.API_SOURCES)}")

    subject: consent.Subject
    normalized_address: str | None = None
    if address is not None:
        try:
            normalized_address = consent.normalize(channel, address)
            subject = consent.subject_for(channel, address)
        except domain.RuleError as exc:
            raise ValidationProblem(str(exc)) from exc
    if contact_id is not None:
        if channel != "email":
            raise ValidationProblem("contact_id names an email contact; give an address instead")
        contact = await selectors.get_contact(session, ctx.organization_id, contact_id)
        if contact is None:
            raise NotFoundProblem("No such contact")
        if address is not None and subject.index != contact.email_index:
            raise ValidationProblem("That address is not this contact's address")
        subject = _email_subject(
            index=contact.email_index, enc=contact.email_enc, masked=contact.email_masked
        )
    elif address is None:
        raise ValidationProblem("Give an address or a contact_id")

    if (
        normalized_address is not None and normalized_address.lower() in evidence.lower()
    ) or _evidence_names_a_person(evidence):
        # The column is not sealed. A reference ("order 1042", "form submission 88") is what
        # belongs here, never the person: not their address, not any email address, and not a
        # phone number however it is written.
        raise ValidationProblem(
            "evidence must be a reference, not an email address or a phone number"
        )

    now = datetime.now(UTC)
    when = occurred_at.astimezone(UTC) if occurred_at is not None else now
    if when > now + _CONSENT_FUTURE_SKEW:
        raise ValidationProblem("occurred_at cannot be in the future")

    replayed = False
    event: ConsentEvent | None = None
    if idempotency_key:
        event = await selectors.get_consent_event_by_key(
            session, ctx.organization_id, idempotency_key
        )
        replayed = event is not None
    if event is None:
        written = await consent.append(
            session,
            organization_id=ctx.organization_id,
            subject=subject,
            purposes=(purpose,),
            status=status,
            source=source,
            evidence=evidence,
            recorded_by=ctx.principal.sub,
            contact_id=contact_id,
            occurred_at=when,
            idempotency_key=idempotency_key,
        )
        if written:
            event = written[0]
        elif idempotency_key:
            # Lost a race to another request carrying the same key: theirs is the one that counts.
            event = await selectors.get_consent_event_by_key(
                session, ctx.organization_id, idempotency_key
            )
            replayed = True
    if event is None:  # pragma: no cover - the insert wrote nothing and no key explains it
        raise ConflictProblem("That consent event could not be recorded")
    if replayed and (
        (event.channel, event.purpose, event.status, event.subject_index)
        != (channel, purpose, status, subject.index)
    ):
        raise ConflictProblem("That idempotency_key was already used for a different event")

    if not replayed:
        record_audit(
            session,
            AuditLog,
            organization_id=ctx.organization_id,
            actor_id=ctx.principal.sub,
            action="engage.consent.recorded",
            entity="consent_event",
            entity_id=event.id,
            after={
                "channel": channel,
                "purpose": purpose,
                "status": status,
                "source": source,
                "subject": subject.masked,
            },
            request=request,
        )

    if normalized_address is not None:
        effective = await consent.decide(
            session, ctx.organization_id, channel, purpose, normalized_address
        )
    else:
        effective = (
            await consent.decide_indexes(
                session, ctx.organization_id, channel, purpose, [subject.index]
            )
        )[subject.index]
    return RecordedConsent(event=event, effective=effective, replayed=replayed)


async def unmask_consent_subject(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    event_id: uuid.UUID,
    request: Request | None = None,
) -> str:
    """Reveal the address one consent event is about — the audited, stepped-up path.

    Mirrors `unmask_inbound_message`: reaching here means the caller held the dangerous
    `engage.inbox.message.unmask` permission and stepped up (`amr={"otp"}`). An
    `entity.sensitive_read` row is written in the caller's transaction every time.
    """
    row = await selectors.get_consent_event(session, ctx.organization_id, event_id)
    if row is None:
        raise NotFoundProblem("No such consent event")
    plaintext = pii.open_sealed(row.subject_enc, settings=settings())
    record_sensitive_read(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        entity="consent_event",
        entity_id=row.id,
        fields=["subject"],
        request=request,
    )
    return plaintext


# --------------------------------------------------------------------- engage lists
#
# A list is a named set of contacts. Membership rows cascade with either end, so deleting a
# contact takes them off every list, and a list is refused deletion while a campaign names it —
# a draft whose audience silently vanished would send to nobody, or be edited into sending to
# everybody.


async def create_list(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    name: str,
    description: str = "",
    request: Request | None = None,
) -> ContactList:
    if await selectors.get_list_by_name(session, ctx.organization_id, name):
        raise ConflictProblem(f"A list named {name!r} already exists")
    row = ContactList(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        name=name,
        description=description,
    )
    session.add(row)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.list.created",
        entity="contact_list",
        entity_id=row.id,
        after={"name": name},
        request=request,
    )
    return row


async def update_list(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    list_id: uuid.UUID,
    name: str | None = None,
    description: str | None = None,
    request: Request | None = None,
) -> ContactList:
    row = await selectors.get_list(session, ctx.organization_id, list_id)
    if row is None:
        raise NotFoundProblem("No such list")
    before = {"name": row.name, "description": row.description}
    if name is not None and name != row.name:
        if await selectors.get_list_by_name(session, ctx.organization_id, name):
            raise ConflictProblem(f"A list named {name!r} already exists")
        row.name = name
    if description is not None:
        row.description = description
    row.updated_by = str(ctx.principal.sub)
    row.updated_at = datetime.now(UTC)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.list.updated",
        entity="contact_list",
        entity_id=row.id,
        before=before,
        after={"name": row.name, "description": row.description},
        request=request,
    )
    return row


async def delete_list(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    list_id: uuid.UUID,
    request: Request | None = None,
) -> None:
    row = await selectors.get_list(session, ctx.organization_id, list_id)
    if row is None:
        raise NotFoundProblem("No such list")
    # Blocks a list referenced by *any* campaign, including a finished one — considered and
    # deliberately not narrowed to "only a draft/scheduled campaign" (review finding: allow
    # deleting a list a finished campaign used). `Campaign.list_id` is a real foreign key
    # (`fk_campaigns_list_id_contact_lists`, no cascade) precisely because a sent campaign's
    # audience is part of its record — CampaignOut still reports `list_id` for a `sent` or
    # `cancelled` campaign, and the `(audience = 'list') = (list_id IS NOT NULL)` CHECK means
    # allowing the delete would need either an `ON DELETE SET NULL` (which breaks that CHECK for
    # every historical `list` campaign, not just future ones) or a special-cased nullable FK for
    # finished campaigns only, which is a schema integrity guarantee this fix is not worth
    # weakening. An operator wanting the list gone can rename it instead; the campaign's own
    # record is unaffected either way.
    if await selectors.campaigns_using_list(session, ctx.organization_id, list_id):
        raise ConflictProblem("A campaign is addressed to this list; delete or re-target it first")
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.list.deleted",
        entity="contact_list",
        entity_id=row.id,
        before={"name": row.name},
        request=request,
    )
    await session.delete(row)
    await session.flush()


async def change_list_members(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    list_id: uuid.UUID,
    contact_ids: list[uuid.UUID],
    add: bool,
    request: Request | None = None,
) -> tuple[int, list[uuid.UUID]]:
    """Add or remove members. Returns `(changed, unknown_ids)`.

    Idempotent both ways: adding someone already on the list, or removing someone who is not,
    changes nothing and is not an error. An id that is not one of this organization's contacts
    is reported back as unknown — the same answer whether it is absent or another tenant's.
    """
    row = await selectors.get_list(session, ctx.organization_id, list_id)
    if row is None:
        raise NotFoundProblem("No such list")
    wanted = list(dict.fromkeys(contact_ids))
    known = await selectors.existing_contact_ids(session, ctx.organization_id, wanted)
    unknown = [cid for cid in wanted if cid not in known]
    ids = [cid for cid in wanted if cid in known]
    changed = 0
    if ids and add:
        stmt = (
            pg_insert(ContactListMember)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "organization_id": ctx.organization_id,
                        "created_by": str(ctx.principal.sub),
                        "list_id": list_id,
                        "contact_id": cid,
                    }
                    for cid in ids
                ]
            )
            .on_conflict_do_nothing(constraint="uq_contact_list_member")
            .returning(ContactListMember.id)
        )
        changed = len((await session.execute(stmt)).scalars().all())
    elif ids:
        result = await session.execute(
            delete(ContactListMember)
            .where(
                ContactListMember.organization_id == ctx.organization_id,
                ContactListMember.list_id == list_id,
                ContactListMember.contact_id.in_(ids),
            )
            .returning(ContactListMember.id)
        )
        changed = len(result.scalars().all())
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.list.members_added" if add else "engage.list.members_removed",
        entity="contact_list",
        entity_id=list_id,
        after={"changed": changed, "unknown": len(unknown)},
        request=request,
    )
    return changed, unknown


# --------------------------------------------------------------------- engage campaigns
#
# A campaign is drafted, sent, and fanned out. `request_send` is the gate and the only writer of
# `sending`; it stages `EVT_CAMPAIGN_SEND_REQUESTED` in the same transaction, so the fan-out
# starts only after the commit (the relay publishes it) and never inline. The fan-out is
# `expand_campaign` once and then `send_campaign_batch` until it says stop — each its own
# transaction, driven by `consumers.fan_out`.

EVT_CAMPAIGN_SEND_REQUESTED = "engage.campaign.campaign.send_requested"
EVT_CAMPAIGN_CANCELLED = "engage.campaign.campaign.cancelled"

#: What the fan-out stamps on rows it creates. It acts on an event, not for a person.
CAMPAIGN_ACTOR = "system:engage-campaign"

_CANCELLABLE: Final[frozenset[str]] = frozenset({"draft", "scheduled", "sending", "paused"})

#: Campaign statuses the send driver (`app/cron.py`) will pick up: actively sending, or paused
#: and due a retry. Anything else (draft, scheduled, sent, cancelled) it leaves alone.
_DRIVER_ACTIVE: Final[frozenset[str]] = frozenset({"sending", "paused"})

#: The recipient-facing wording of a refused send. The provider's own words go to the log only.
_RECIPIENT_REFUSED = "The provider refused this message"


def system_ctx(organization_id: uuid.UUID) -> ScopeContext:
    """An ORG-scoped context for the fan-out, which has no principal. ORG narrows nothing, so
    it lets the consumer reuse `selectors.sendable_contacts` rather than a second copy of it."""
    principal = Principal(sub=uuid.UUID(int=0), organization_id=organization_id)
    return ScopeContext(
        principal=principal, scope=Scope.ORG, permission="engage.campaign.campaign.send"
    )


async def _check_audience(
    session: AsyncSession, org_id: uuid.UUID, audience: str, list_id: uuid.UUID | None
) -> None:
    if audience == "list":
        if list_id is None:
            raise ValidationProblem("A list audience needs a list_id")
        if await selectors.get_list(session, org_id, list_id) is None:
            raise ValidationProblem("No such list")
    elif list_id is not None:
        raise ValidationProblem("list_id is only meaningful when the audience is a list")


async def _check_sender_exists(
    session: AsyncSession, org_id: uuid.UUID, sender_id: uuid.UUID
) -> None:
    if await selectors.get_sender(session, org_id, sender_id) is None:
        raise ValidationProblem("No such sender")


async def create_campaign(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    fields: dict[str, Any],
    request: Request | None = None,
) -> Campaign:
    await _check_sender_exists(session, ctx.organization_id, fields["sender_id"])
    await _check_audience(session, ctx.organization_id, fields["audience"], fields.get("list_id"))
    row = Campaign(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        status="draft",
        **fields,
    )
    session.add(row)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.campaign.created",
        entity="campaign",
        entity_id=row.id,
        after={"name": row.name, "audience": row.audience},
        request=request,
    )
    return row


async def update_campaign(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    campaign_id: uuid.UUID,
    changes: dict[str, Any],
    request: Request | None = None,
) -> Campaign:
    """Edit a draft. Anything past draft is a record of what went out, and is refused."""
    row = await selectors.get_campaign(session, ctx.organization_id, campaign_id, lock=True)
    if row is None:
        raise NotFoundProblem("No such campaign")
    if row.status != "draft":
        raise ConflictProblem(f"Only a draft can be edited; this campaign is {row.status}")
    if "sender_id" in changes and changes["sender_id"] is not None:
        await _check_sender_exists(session, ctx.organization_id, changes["sender_id"])
    audience = changes.get("audience") or row.audience
    list_id = changes.get("list_id", row.list_id)
    if audience == "all_sendable" and "audience" in changes and "list_id" not in changes:
        list_id = None
    await _check_audience(session, ctx.organization_id, audience, list_id)
    for key, value in changes.items():
        if key in ("audience", "list_id"):
            continue
        if value is not None or key == "scheduled_at":
            setattr(row, key, value)
    row.audience = audience
    row.list_id = list_id
    row.updated_by = str(ctx.principal.sub)
    row.updated_at = datetime.now(UTC)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.campaign.updated",
        entity="campaign",
        entity_id=row.id,
        after={"fields": sorted(changes)},
        request=request,
    )
    return row


async def delete_campaign(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    campaign_id: uuid.UUID,
    request: Request | None = None,
) -> None:
    """Delete a draft. A campaign that started sending is kept: its recipients are a record."""
    row = await selectors.get_campaign(session, ctx.organization_id, campaign_id, lock=True)
    if row is None:
        raise NotFoundProblem("No such campaign")
    if row.status != "draft":
        raise ConflictProblem(f"Only a draft can be deleted; this campaign is {row.status}")
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.campaign.deleted",
        entity="campaign",
        entity_id=row.id,
        before={"name": row.name},
        request=request,
    )
    await session.delete(row)
    await session.flush()


async def _ensure_campaign_sendable(
    session: AsyncSession, ctx: ScopeContext, row: Campaign
) -> None:
    """The send gate's own rules, shared by `request_send` and `schedule_send` (ADR-0015): no
    subject or body, a sender that never passed its test send, a domain that fell out of
    verification, no connected provider, a missing list, an audience with nobody sendable in it
    right now, or unsubscribe links that cannot be signed — mail without a working unsubscribe
    link is not sent at all. Raises `BusinessRuleProblem` (422) on the first rule that fails.

    The "nobody sendable" check reads `selectors.sendable_contacts(..., limit=1)` rather than
    trust `audience`/`list_id` alone: a list that exists can still hold only suppressed or
    deleted contacts, and `all_sendable` can name an org with none at all. Catching that here,
    before `status` ever moves to `sending` or `scheduled`, is what keeps a campaign from
    completing as `sent` with `recipient_count = 0` for a reason a human could have been told
    about up front instead — contacts removed *after* this check still expand to nobody and
    still end up `sent` with zero recipients (see `expand_campaign`'s own docstring), which is
    the one case this gate cannot close because the audience has not shrunk yet when it runs. A
    scheduled campaign gets this check again, for the *state at the time it was scheduled*
    only — nothing re-validates it the instant it actually fires.
    """
    if not row.subject.strip():
        raise BusinessRuleProblem("Add a subject before sending.")
    if not (row.text_body.strip() or row.html_body.strip()):
        raise BusinessRuleProblem("Add a body before sending.")

    sender = await selectors.get_sender(session, ctx.organization_id, row.sender_id)
    if sender is None:
        raise BusinessRuleProblem("The campaign's sender no longer exists. Choose another.")
    if not sender.usable:
        raise BusinessRuleProblem(
            f"{sender.address} has not passed a test send. Verify the sender before sending."
        )
    email_domain = await selectors.get_domain(session, ctx.organization_id, sender.domain_id)
    if email_domain is None or email_domain.status != "verified":
        raise BusinessRuleProblem(
            f"The domain behind {sender.address} is no longer verified. Re-verify its DNS "
            "records before sending."
        )
    connection = await selectors.get_connection(session, ctx.organization_id)
    if connection is None or connection.status != "connected":
        raise BusinessRuleProblem("Connect an email provider before sending a campaign.")
    if (
        row.audience == "list"
        and (
            row.list_id is None
            or await selectors.get_list(session, ctx.organization_id, row.list_id)
        )
        is None
    ):
        raise BusinessRuleProblem("The campaign's list no longer exists.")
    if not await selectors.sendable_contacts(
        session, ctx, limit=1, list_id=row.list_id if row.audience == "list" else None
    ):
        raise BusinessRuleProblem(
            "This list has no contacts who can be emailed."
            if row.audience == "list"
            else "You have no contacts who can be emailed."
        )
    if unsubscribe_key() is None:
        raise BusinessRuleProblem(
            "Unsubscribe links are not configured for this environment, so campaigns cannot "
            "be sent."
        )
    _check_public_base(settings())


def _campaign_send_idempotency_key(campaign_id: uuid.UUID, scheduled_at: datetime) -> str:
    """One scheduled-job row per distinct instant asked for. Rescheduling asks for a new instant
    and so gets a fresh key — the previous job, if the scheduler has not fired it yet, is left in
    place rather than cancelled (ADR-0015 has no cancel primitive yet); `activate_scheduled_
    campaign` is what makes that harmless by dropping a firing that no longer matches the
    campaign's current `scheduled_at`."""
    return f"engage-campaign-send:{campaign_id}:{scheduled_at.astimezone(UTC).isoformat()}"


async def request_send(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    campaign_id: uuid.UUID,
    request: Request | None = None,
) -> Campaign:
    """**The send gate.** Moves a draft to `sending` and stages the fan-out event.

    409 when the campaign is not a draft (sent twice, or cancelled). 422 when it is a draft
    that may not go — see `_ensure_campaign_sendable`.
    """
    row = await selectors.get_campaign(session, ctx.organization_id, campaign_id, lock=True)
    if row is None:
        raise NotFoundProblem("No such campaign")
    if row.status != "draft":
        raise ConflictProblem(f"Only a draft can be sent; this campaign is {row.status}")
    await _ensure_campaign_sendable(session, ctx, row)

    row.status = "sending"
    row.started_at = datetime.now(UTC)
    row.updated_by = str(ctx.principal.sub)
    row.updated_at = row.started_at
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.campaign.send_requested",
        entity="campaign",
        entity_id=row.id,
        after={"sender_id": str(row.sender_id), "audience": row.audience},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_CAMPAIGN_SEND_REQUESTED,
        aggregate_type="campaign",
        aggregate_id=row.id,
        payload={"organization_id": str(ctx.organization_id), "campaign_id": str(row.id)},
        actor=_actor(ctx),
    )
    return row


async def schedule_send(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    campaign_id: uuid.UUID,
    scheduled_at: datetime,
    request: Request | None = None,
    now: datetime | None = None,
) -> Campaign:
    """Gate a future send exactly like `request_send`, but hold it for the scheduler (ADR-0015)
    instead of publishing immediately. Moves a draft — or an already-`scheduled` campaign, to
    reschedule it — to `scheduled`; `activate_scheduled_campaign` is what flips it to `sending`,
    the moment the scheduler's own job for it actually fires.

    409 when the campaign is not a draft or already `scheduled` (sending, paused, sent or
    cancelled). 422 when `scheduled_at` is not in the future, or the campaign fails the same
    checks `request_send` applies. Callable a second time on a `scheduled` campaign to
    reschedule it — see `_campaign_send_idempotency_key`'s docstring for how a stale, previously
    scheduled firing is made harmless rather than cancelled outright.
    """
    now = now or datetime.now(UTC)
    if scheduled_at <= now:
        raise ValidationProblem("scheduled_at must be in the future")
    row = await selectors.get_campaign(session, ctx.organization_id, campaign_id, lock=True)
    if row is None:
        raise NotFoundProblem("No such campaign")
    if row.status not in ("draft", "scheduled"):
        raise ConflictProblem(
            f"Only a draft or scheduled campaign can be scheduled; this campaign is {row.status}"
        )
    await _ensure_campaign_sendable(session, ctx, row)

    before_status = row.status
    row.status = "scheduled"
    row.scheduled_at = scheduled_at
    row.updated_by = str(ctx.principal.sub)
    row.updated_at = now
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.campaign.scheduled",
        entity="campaign",
        entity_id=row.id,
        before={"status": before_status},
        after={"status": "scheduled", "scheduled_at": scheduled_at.isoformat()},
        request=request,
    )
    schedule_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        due_at=scheduled_at,
        idempotency_key=_campaign_send_idempotency_key(campaign_id, scheduled_at),
        target_event_type=EVT_CAMPAIGN_SEND_REQUESTED,
        target_aggregate_type="campaign",
        target_aggregate_id=row.id,
        target_payload={
            "organization_id": str(ctx.organization_id),
            "campaign_id": str(row.id),
            "scheduled_for": scheduled_at.astimezone(UTC).isoformat(),
        },
        target_actor=_actor(ctx),
    )
    return row


#: How much a firing may arrive before its own `scheduled_at` and still be trusted, the same
#: guard ADR-0015 §5 gives finance's own scheduled consumer
#: (`finance.app.domain.FILING_EARLY_ARRIVAL_TOLERANCE`). This should never happen in normal
#: operation — the scheduler holds a job until the database's own clock says `due_at` has
#: passed — so it only ever catches an operator's manual redelivery, a replayed dead letter, or a
#: scheduler with a badly wrong clock. Unlike finance, nothing here re-arms a fresh wait: the
#: firing is simply dropped, and the campaign is left `scheduled` for its own still-pending
#: `platform.schedule.requested` job to fire again, on time.
_CAMPAIGN_FIRE_SKEW: Final[timedelta] = timedelta(seconds=30)


async def activate_scheduled_campaign(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    campaign_id: uuid.UUID,
    scheduled_for: datetime,
    now: datetime | None = None,
) -> bool:
    """Flip a `scheduled` campaign to `sending` when the scheduler's own job for it fires.
    Returns whether it did.

    `scheduled_for` is the instant `schedule_send` staged this particular firing for, carried on
    the event so it can be checked against the campaign's *current* `scheduled_at`: a reschedule
    stages a fresh `platform.schedule.requested` without cancelling the one it replaces
    (`_campaign_send_idempotency_key`'s docstring — ADR-0015 has no cancel primitive), so the
    instant a superseded job eventually fires no longer matches and this drops it rather than
    start a second send. A campaign that was cancelled, or already moved on, is not `scheduled`
    either by the time this runs, so it is dropped the same way as a matter of course. A firing
    that arrives more than `_CAMPAIGN_FIRE_SKEW` before `scheduled_at` is dropped too — see that
    constant's own docstring.

    Before flipping to `sending`, this re-runs `_ensure_campaign_sendable` — the same gate
    `request_send` and `schedule_send` already applied, but now against the campaign's state
    *at the moment it actually fires* rather than the moment it was scheduled (see that
    function's own docstring for the gap this closes: a sender that stopped being usable, a
    domain that fell out of verification, a provider that got disconnected, or an audience that
    emptied out, in the time between scheduling and firing). A campaign that fails the gate here
    is moved to `paused` with the failing rule's own message as `pause_reason` and an audit row —
    the same status and reason column the send driver already uses for a route that breaks
    mid-send (`_pause_campaign`) — and nothing is sent; an operator resumes it with
    `resume_campaign` once the problem is fixed, exactly as they would a campaign paused
    mid-flight.

    Called from `app/consumers.py:expand_campaign_send`, in the same transaction as the expansion
    that follows it — a dropped or paused firing here means `expand_campaign` right after it is a
    no-op too, since it only ever acts on a campaign that is `sending`.
    """
    now = now or datetime.now(UTC)
    row = await selectors.get_campaign(session, organization_id, campaign_id, lock=True)
    if row is None or row.status != "scheduled":
        return False
    if row.scheduled_at is None or row.scheduled_at.astimezone(UTC) != scheduled_for.astimezone(
        UTC
    ):
        return False
    if row.scheduled_at.astimezone(UTC) - now > _CAMPAIGN_FIRE_SKEW:
        log.info(
            "engage.campaign.fired_early",
            campaign_id=str(campaign_id),
            scheduled_at=row.scheduled_at.isoformat(),
            now=now.isoformat(),
        )
        return False

    try:
        await _ensure_campaign_sendable(session, system_ctx(organization_id), row)
    except BusinessRuleProblem as exc:
        row.status = "paused"
        row.pause_reason = exc.detail[:300]
        row.paused_at = now
        row.updated_by = CAMPAIGN_ACTOR
        row.updated_at = now
        await session.flush()
        record_audit(
            session,
            AuditLog,
            organization_id=organization_id,
            actor_id=None,
            acting_as=CAMPAIGN_ACTOR,
            action="engage.campaign.scheduled_send_failed",
            entity="campaign",
            entity_id=row.id,
            before={"status": "scheduled"},
            after={"status": "paused", "pause_reason": row.pause_reason},
            reason=exc.detail,
        )
        log.info(
            "engage.campaign.scheduled_send_gate_failed",
            campaign_id=str(campaign_id),
            reason=exc.detail,
        )
        return False

    row.status = "sending"
    row.started_at = now
    row.updated_by = CAMPAIGN_ACTOR
    row.updated_at = row.started_at
    await session.flush()
    return True


async def cancel_campaign(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    campaign_id: uuid.UUID,
    request: Request | None = None,
) -> Campaign:
    """Stop a campaign. Recipients not yet sent stay `queued` and are never sent: the fan-out
    re-reads the status before every batch and stops at the first one that is not `sending`."""
    row = await selectors.get_campaign(session, ctx.organization_id, campaign_id, lock=True)
    if row is None:
        raise NotFoundProblem("No such campaign")
    if row.status not in _CANCELLABLE:
        raise ConflictProblem(f"A {row.status} campaign cannot be cancelled")
    before = row.status
    row.status = "cancelled"
    row.cancelled_at = datetime.now(UTC)
    row.updated_by = str(ctx.principal.sub)
    row.updated_at = row.cancelled_at
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.campaign.cancelled",
        entity="campaign",
        entity_id=row.id,
        before={"status": before},
        after={"status": "cancelled"},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_CAMPAIGN_CANCELLED,
        aggregate_type="campaign",
        aggregate_id=row.id,
        payload={"organization_id": str(ctx.organization_id), "campaign_id": str(row.id)},
        actor=_actor(ctx),
    )
    return row


async def expand_campaign(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    campaign_id: uuid.UUID,
    batch_size: int,
) -> bool:
    """Write one `queued` recipient per sendable contact in the audience. Returns whether the
    campaign is still `sending` (and so worth sending batches for).

    Keyset-paged through `selectors.sendable_contacts`, so suppressed addresses are never
    expanded at all. `ON CONFLICT DO NOTHING` on `(campaign_id, contact_id)` makes a redelivered
    event, or two workers racing, insert each recipient once — see
    `test_two_concurrent_expansions_insert_each_recipient_once` for the two-racer case, which
    this and `sendable_contacts`'s own `FOR KEY SHARE` are what make safe.

    Sets `Campaign.expanded_at` in this same transaction, even when nobody turned out to be
    sendable — the send driver's own signal that expansion has actually happened, distinct from
    `recipient_count`, which is legitimately zero both before expansion and after one that found
    nobody to mail. Set once and left alone after that: a redelivered event or the driver's own
    call onto an already-expanded campaign must not keep bumping it forward, since it marks when
    the audience was first computed, not the last time anything here ran.
    """
    campaign = await selectors.get_campaign(session, organization_id, campaign_id)
    if campaign is None or campaign.status != "sending":
        return False
    ctx = system_ctx(organization_id)
    after: uuid.UUID | None = None
    while True:
        contacts = await selectors.sendable_contacts(
            session,
            ctx,
            limit=batch_size,
            after_id=after,
            list_id=campaign.list_id if campaign.audience == "list" else None,
            # FOR KEY SHARE: a concurrent `delete_contact` on one of these rows must wait for
            # this transaction to commit, so its own erasure of `campaign_recipients` — which
            # only sees rows that exist yet — always runs after the copy below exists to
            # erase, never before. See `sendable_contacts`'s docstring.
            lock=True,
        )
        if not contacts:
            break
        await session.execute(
            pg_insert(CampaignRecipient)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "organization_id": organization_id,
                        "created_by": CAMPAIGN_ACTOR,
                        "campaign_id": campaign_id,
                        "contact_id": c.id,
                        "email_enc": c.email_enc,
                        "email_masked": c.email_masked,
                        "email_index": c.email_index,
                        "status": "queued",
                        "error": "",
                    }
                    for c in contacts
                ]
            )
            .on_conflict_do_nothing(constraint="uq_campaign_recipient")
        )
        after = contacts[-1].id
    if campaign.expanded_at is None:
        campaign.expanded_at = datetime.now(UTC)
    await _fold_campaign_counts(session, campaign)
    return True


async def _fold_campaign_counts(session: AsyncSession, campaign: Campaign) -> dict[str, int]:
    counts = (
        await selectors.recipient_status_counts(session, campaign.organization_id, [campaign.id])
    ).get(campaign.id, {})
    campaign.recipient_count = sum(counts.values())
    campaign.sent_count = counts.get("sent", 0)
    campaign.failed_count = counts.get("failed", 0)
    campaign.suppressed_count = counts.get("suppressed", 0)
    await session.flush()
    return counts


def unsubscribe_url(organization_id: uuid.UUID, email: str) -> str:
    """The v1 unsubscribe link for `email`. See `unsubscribe_token` — nothing that mails a real
    recipient calls this; `unsubscribe_url_for_recipient` is what a campaign send uses."""
    base = settings().engage_public_api_base.rstrip("/")
    return f"{base}/engage/unsubscribe/{unsubscribe_token(organization_id, email)}"


def unsubscribe_url_for_recipient(organization_id: uuid.UUID, recipient_id: uuid.UUID) -> str:
    """The unsubscribe link for one campaign delivery — opaque, carrying no address."""
    base = settings().engage_public_api_base.rstrip("/")
    token = unsubscribe_token_for_recipient(organization_id, recipient_id)
    return f"{base}/engage/unsubscribe/{token}"


def campaign_message(
    campaign: Campaign, *, to_address: str, unsubscribe: str, html_body: str | None = None
) -> Outgoing:
    """One recipient's copy: the campaign body plus the unsubscribe footer, and the RFC 8058
    one-click headers. Built per recipient because the link is per address.

    `html_body` replaces the campaign's HTML body when given: the tracked copy (slice E1), with
    links rewritten and a pixel added. The unsubscribe footer is always appended after it, so it
    is never rewritten."""
    text = campaign.text_body.rstrip()
    footer = f"Unsubscribe: {unsubscribe}\n"
    text = f"{text}\n\n--\n{footer}" if text else footer
    html: str | None = None
    if campaign.html_body.strip():
        link = html_escape(unsubscribe, quote=True)
        html = (
            f"{campaign.html_body if html_body is None else html_body}\n"
            '<p style="font-size:12px;color:#666">'
            f'<a href="{link}">Unsubscribe</a></p>'
        )
    return Outgoing(
        to_address=to_address,
        subject=campaign.subject,
        body=text,
        html=html,
        headers={
            "List-Unsubscribe": f"<{unsubscribe}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )


async def send_campaign_test(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    campaign_id: uuid.UUID,
    to_address: str,
    request: Request | None = None,
    transport: Transport | None = None,
    resolver: dns_check.Resolver | None = None,
) -> None:
    """Send a test copy of a campaign exactly as a recipient would receive it.

    Same subject, same body, the same List-Unsubscribe headers — prefixed `[Test] ` so nobody
    mistakes it for the real send — through a real-looking unsubscribe link that names nobody:
    the recipient id inside it is a fresh `uuid4()`, never a row in `campaign_recipients`, so a
    click on it resolves nothing and suppresses nothing. That is what "exactly as recipients get
    it" is worth proving without either touching the audience or waiting for the real send.

    Domain verification is **not** part of this gate, unlike `request_send`'s: a test never
    reaches a real recipient's mailbox, so it proves the *content* renders and the *connection*
    accepts a message, neither of which needs the domain's DNS to be settled. A connected
    provider and a sender that has passed its own test send are still required — there is
    nothing to prove by mailing through a connection or a sender nobody has shown works at all.

    Same shape as `send_test`: rate limited per organization, and the network call runs inside
    this route's one transaction (claim → call → record) rather than split around it — see
    `tests/infra/test_io_outside_transaction.py`'s allow-list entry for this route, which mirrors
    the sender test's own unfixed instance (D3 #19).
    """
    cfg = settings()
    await enforce_rate_limit(
        f"engage.campaign_test_send:{ctx.organization_id}",
        limit=cfg.engage_test_send_rate_limit_per_minute,
        window_seconds=60,
    )

    campaign = await selectors.get_campaign(session, ctx.organization_id, campaign_id)
    if campaign is None:
        raise NotFoundProblem("No such campaign")

    sender = await selectors.get_sender(session, ctx.organization_id, campaign.sender_id)
    if sender is None:
        raise BusinessRuleProblem("The campaign's sender no longer exists. Choose another.")
    if not sender.usable:
        raise BusinessRuleProblem(
            f"{sender.address} has not passed a test send. Verify the sender before sending a test."
        )
    connection = await selectors.get_connection(session, ctx.organization_id)
    if connection is None or connection.status != "connected":
        raise BusinessRuleProblem("Connect an email provider before sending a test message.")

    try:
        recipient = _rules.normalize_recipient("email", to_address)
    except _rules.RuleError as exc:
        raise ValidationProblem(str(exc)) from exc

    key = unsubscribe_key()
    if key is not None:
        token = _rules.make_unsubscribe_token_v2(ctx.organization_id, uuid.uuid4(), key)
        unsubscribe = f"{cfg.engage_public_api_base.rstrip('/')}/engage/unsubscribe/{token}"
    else:
        # Same placeholder shape a customer's own link would take, minus a signature nothing can
        # issue yet — real-looking, and no more forgeable than the environment already is.
        unsubscribe = f"{cfg.engage_public_api_base.rstrip('/')}/engage/unsubscribe/preview"

    outgoing = campaign_message(campaign, to_address=recipient, unsubscribe=unsubscribe)
    outgoing = replace(outgoing, subject=f"[Test] {outgoing.subject}")

    wire = transport
    if wire is None:
        wire = await _transport_for_connection(
            connection, from_address=sender.address, resolver=resolver
        )
    try:
        await wire.send(outgoing)
    except TransportRefused as exc:
        log.warning(
            "engage.campaign_test_send.refused", campaign_id=str(campaign.id), error=str(exc)
        )
        raise BusinessRuleProblem(
            "The provider refused the test message. Check the connection's credentials and "
            "the sender's address."
        ) from exc
    except TransportUnavailable as exc:
        log.warning(
            "engage.campaign_test_send.unavailable", campaign_id=str(campaign.id), error=str(exc)
        )
        raise UpstreamUnavailableProblem(
            "The provider could not be reached. Try again in a few minutes."
        ) from exc

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.campaign.test_sent",
        entity="campaign",
        entity_id=campaign.id,
        after={"tested_with": recipient},
        request=request,
    )
    await session.flush()


async def campaign_transport(connection: EmailConnection, sender: Sender) -> Transport:
    """The wire a campaign goes out on.

    The organization's own connection — unless this process runs with `EMAIL_TRANSPORT=log`,
    which is the platform's "send nothing real" switch and is honoured here as everywhere: a
    staging deploy must not mail real audiences because a customer's credential happened to be
    in its database.

    Sends on `transports.CAMPAIGN_SMTP_EXECUTOR`, not the process's default pool — see that
    constant's own docstring. This is the only caller of `_transport_for_connection` that passes
    it: `send_test`'s own call (below) is a single, interactive, low-volume send and stays on the
    default pool on purpose, so a large campaign already in flight can never make an operator's
    "send a test" button hang behind it.
    """
    if settings().email_transport == "log":
        return LogTransport("email")
    return await _transport_for_connection(
        connection, from_address=sender.address, executor=CAMPAIGN_SMTP_EXECUTOR
    )


@dataclass(frozen=True, slots=True)
class ClaimedMessage:
    """One recipient's built message, ready to hand to a transport. `recipient_id` is what the
    record step (`record_campaign_batch`) uses to write the outcome back — never the address,
    which lives only in `outgoing.to_address` and only for as long as the batch is in flight."""

    recipient_id: uuid.UUID
    outgoing: Outgoing


@dataclass(frozen=True, slots=True)
class ClaimedBatch:
    """What `claim_campaign_batch` hands the driver: the connection and sender to send this
    batch on, the messages worth sending, and the recipients already resolved without ever
    reaching a transport (suppressed since expansion, or a row whose message could not even be
    built) — the latter go straight to `record_campaign_batch` alongside whatever the transport
    returns for `messages`.

    Deliberately not a built `Transport`: for a self-hosted `smtp` connection, building one
    re-resolves the host (`_transport_for_connection`'s own rebinding guard) — network I/O that
    must not run inside the claim's open transaction any more than the send itself may. The
    driver calls `resolve_campaign_transport` on this **after** the claim transaction has
    committed. `transport_override` exists only for tests, which build nothing and so pay no
    such cost either way.
    """

    connection: EmailConnection
    sender: Sender
    messages: list[ClaimedMessage]
    resolved: list[tuple[uuid.UUID, Literal["suppressed", "failed"], str]]
    transport_override: Transport | None = None


async def resolve_campaign_transport(batch: ClaimedBatch) -> Transport:
    """The transport a claimed batch sends on. Call this after the claim transaction has
    committed — see `ClaimedBatch`'s docstring for why building it is not free of I/O."""
    if batch.transport_override is not None:
        return batch.transport_override
    return await campaign_transport(batch.connection, batch.sender)


async def drop_erased_from_batch(
    session: AsyncSession, *, organization_id: uuid.UUID, batch: ClaimedBatch
) -> ClaimedBatch:
    """Re-checks `erased_at` for a claimed batch's own recipients, immediately before the
    driver hands any of them to a transport. Closes the window `claim_campaign_batch`'s own
    check cannot: `erased_at` is read once, at claim time, and the batch then sits in the
    driver's hands — resolving the transport, then a network call per message — for however
    long that takes, which is time enough for `erase_contact_campaign_copies` to run on one of
    these very rows in between. A message already built with a now-erased address is dropped
    here rather than sent, and resolved `failed` alongside whatever the transport goes on to
    report for the rest of the batch.

    Narrows the window; does not close it — the address could still be erased in the instant
    between this query and the transport call, which is irreducible without serialising a send
    behind the same row lock `expand_campaign` takes on `contacts` (and campaigns send far more
    often than contacts are deleted, so that trade is not taken here). The other, wider half of
    the window — a row still `queued` when erasure runs — is closed directly by
    `erase_contact_campaign_copies`, which fails it on the spot rather than leaving it to be
    claimed at all.
    """
    if not batch.messages:
        return batch
    erased = await selectors.erased_recipient_ids(
        session, organization_id, [m.recipient_id for m in batch.messages]
    )
    if not erased:
        return batch
    kept = [m for m in batch.messages if m.recipient_id not in erased]
    dropped: list[tuple[uuid.UUID, Literal["suppressed", "failed"], str]] = [
        (m.recipient_id, "failed", _ERASED_ERROR)
        for m in batch.messages
        if m.recipient_id in erased
    ]
    return replace(batch, messages=kept, resolved=[*batch.resolved, *dropped])


ClaimOutcome = Literal["claimed", "done", "stopped", "capped", "paused", "idle", "expand"]


async def _pause_campaign(session: AsyncSession, campaign: Campaign, reason: str) -> None:
    """Move a `sending` (or already-`paused`) campaign to `paused`, conditionally: never
    overwrite a `cancelled` written by an operator between this read and this write. Bumping
    `paused_at` even when already paused is what lets `selectors.due_campaigns` back off a
    route that is still broken rather than retrying it every tick."""
    now = datetime.now(UTC)
    # `session.execute` is typed to return the generic `Result`, but an UPDATE statement always
    # produces a `CursorResult` at runtime — which is what actually carries `rowcount`.
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            update(Campaign)
            .where(
                Campaign.id == campaign.id,
                Campaign.organization_id == campaign.organization_id,
                Campaign.status.in_(("sending", "paused")),
            )
            .values(status="paused", pause_reason=reason[:300], paused_at=now, updated_at=now)
        ),
    )
    if result.rowcount:
        log.info("engage.campaign.paused", campaign_id=str(campaign.id), reason=reason)


async def _resume_if_paused(session: AsyncSession, campaign: Campaign) -> None:
    """The mirror of `_pause_campaign`, run once a route exists again: conditional, so it only
    ever moves a campaign this process itself finds still `paused` — never `cancelled`."""
    if campaign.status != "paused":
        return
    now = datetime.now(UTC)
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            update(Campaign)
            .where(
                Campaign.id == campaign.id,
                Campaign.organization_id == campaign.organization_id,
                Campaign.status == "paused",
            )
            .values(status="sending", pause_reason="", paused_at=None, updated_at=now)
        ),
    )
    if result.rowcount:
        log.info("engage.campaign.resumed", campaign_id=str(campaign.id))


async def resume_campaign(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    campaign_id: uuid.UUID,
    request: Request | None = None,
) -> Campaign:
    """An operator's explicit "try again now" — bypasses the driver's own backoff
    (`Settings.engage_campaign_pause_retry_seconds`) by clearing `paused_at` immediately, so the
    very next tick attempts this campaign regardless of when it last failed.

    Re-runs `_ensure_campaign_sendable` first, the same gate `activate_scheduled_campaign` applies
    at fire time: a campaign paused because that gate failed must not slip past it just because
    someone pressed resume. When it still fails the 422 carries the failing rule's own message
    and the campaign stays `paused` with its `pause_reason` untouched."""
    row = await selectors.get_campaign(session, ctx.organization_id, campaign_id, lock=True)
    if row is None:
        raise NotFoundProblem("No such campaign")
    if row.status != "paused":
        raise ConflictProblem(f"Only a paused campaign can be resumed; this one is {row.status}")
    await _ensure_campaign_sendable(session, ctx, row)
    row.status = "sending"
    row.pause_reason = ""
    row.paused_at = None
    row.updated_by = str(ctx.principal.sub)
    row.updated_at = datetime.now(UTC)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.campaign.resumed",
        entity="campaign",
        entity_id=row.id,
        request=request,
    )
    return row


async def _mark_sent_if_still_sending(session: AsyncSession, campaign_id: uuid.UUID) -> None:
    """The other half of the same guarantee `_pause_campaign` gives: a campaign is only ever
    marked `sent` if it is still `sending` right now, so an operator's `cancel` committed after
    the last batch and before this call is never quietly overwritten back to `sent`."""
    now = datetime.now(UTC)
    await session.execute(
        update(Campaign)
        .where(Campaign.id == campaign_id, Campaign.status == "sending")
        .values(status="sent", completed_at=now, updated_at=now)
    )


async def _claim_recipient_rows(
    session: AsyncSession,
    organization_id: uuid.UUID,
    campaign_id: uuid.UUID,
    *,
    limit: int,
    lease_seconds: int,
) -> list[CampaignRecipient]:
    """Claim the next `limit` recipients: `queued`, or `sending` past a stale lease — a batch
    another process claimed and then died before recording. `FOR UPDATE SKIP LOCKED` inside the
    CTE is what makes two drivers working the same campaign safe: the loser of a race skips rows
    the winner already has, rather than blocking on them.

    A CTE joined into the `UPDATE ... FROM`, not `WHERE id IN (subquery)` — the subquery form
    self-references the table the UPDATE is writing, and Postgres does not guarantee `LIMIT`
    inside it bounds how many rows the outer statement ends up touching (the standard "claim N
    rows" recipe uses exactly this CTE shape for that reason).
    """
    stale_before = datetime.now(UTC) - timedelta(seconds=lease_seconds)
    candidates = (
        select(CampaignRecipient.id)
        .where(
            CampaignRecipient.organization_id == organization_id,
            CampaignRecipient.campaign_id == campaign_id,
            (CampaignRecipient.status == "queued")
            | (
                (CampaignRecipient.status == "sending")
                & (CampaignRecipient.claimed_at < stale_before)
            ),
        )
        .order_by(CampaignRecipient.contact_id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
        .cte("claimable_recipients")
    )
    stmt = (
        update(CampaignRecipient)
        .where(CampaignRecipient.id == candidates.c.id)
        .values(status="sending", claimed_at=datetime.now(UTC))
        .returning(CampaignRecipient)
    )
    return list((await session.execute(stmt)).scalars())


async def claim_campaign_batch(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    campaign_id: uuid.UUID,
    batch_size: int,
    lease_seconds: int,
    transport: Transport | None = None,
) -> tuple[ClaimOutcome, ClaimedBatch | None]:
    """The driver's claim step — a short transaction, committed by the caller before anything
    touches a network. Returns what the caller should do next:

    - `claimed` — a batch is ready; send it and call `record_campaign_batch`, then call again.
    - `done` — nothing left `queued` or `sending`; the campaign is now `sent`.
    - `stopped` — the campaign is not `sending`/`paused` any more (cancelled, or already sent).
    - `capped` — the connection's `daily_cap` is used up for today; remaining stay `queued`.
    - `paused` — no connected provider or usable sender (the campaign was moved to `paused`, or
      already was), or the campaign was already `paused` and never got as far as being expanded
      (`activate_scheduled_campaign` paused it at fire time). Either way `Campaign.pause_reason`
      says why, and it waits for `resume_campaign`.
    - `idle` — nothing claimable right now (another worker holds the rest); try again later.
    - `expand` — this campaign is `sending` but `Campaign.expanded_at` is still null: the send
      driver (`app/cron.py`'s `_one_batch`) should call `expand_campaign` itself, in its own
      transaction, and then call this again. **Never `done`** in this state, whatever
      `_claim_recipient_rows`/`_fold_campaign_counts` would otherwise say — an unexpanded
      campaign always has zero `queued`/`sending` rows, which is indistinguishable from "finished"
      by count alone. That confusion is the exact bug this column exists to close: a campaign
      whose driver tick landed before `consumers.expand_campaign_send` had run (or after that
      event was lost entirely) used to be marked `sent` with nobody ever mailed. Checked before
      the connection/sender pause gate below, so a campaign is expanded before it can ever be
      paused for a route problem that has nothing to do with whether its audience exists yet.

    Suppression is re-checked in one `IN (...)` query for the whole batch rather than one query
    per recipient. Building each message can fail without failing the batch: a row whose sealed
    address will not decrypt, for instance, is recorded `failed` and does not stop the rest.
    """
    campaign = await selectors.get_campaign(session, organization_id, campaign_id)
    if campaign is None or campaign.status not in _DRIVER_ACTIVE:
        return "stopped", None
    if campaign.status == "paused" and campaign.expanded_at is None:
        # `paused` with no expansion ever having happened is not the mid-flight pause this
        # column's invariant otherwise guarantees (a route breaking after `expand_campaign` has
        # already run) — it is `activate_scheduled_campaign` pausing a scheduled campaign that
        # failed the send gate the instant it fired, before it was ever `sending`. Left alone
        # here rather than treated as `expand`: nothing has been claimed yet to lose, and running
        # the fall-through below would `_resume_if_paused` it and, with `expanded_at` still null
        # and nothing `queued`, race straight to `done` with nobody ever mailed — the exact
        # zero-recipient "sent" bug `expanded_at` exists to prevent. Only an operator's explicit
        # `resume_campaign` moves this back to `sending`; the very next tick then finds it
        # `sending` with `expanded_at` still null and expands it normally, below.
        return "paused", None
    if campaign.expanded_at is None:
        return "expand", None

    connection = await selectors.get_connection(session, organization_id, lock=True)
    sender = await selectors.get_sender(session, organization_id, campaign.sender_id)
    if connection is None or connection.status != "connected":
        await _pause_campaign(session, campaign, "No connected email provider.")
        return "paused", None
    if sender is None or not sender.usable:
        await _pause_campaign(session, campaign, "The campaign's sender is no longer usable.")
        return "paused", None
    await _resume_if_paused(session, campaign)

    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    used = await selectors.campaign_mail_sent_since(session, organization_id, today)
    room = connection.daily_cap - used
    if room <= 0:
        log.info("engage.campaign.capped", campaign_id=str(campaign_id), cap=connection.daily_cap)
        return "capped", None

    claimed = await _claim_recipient_rows(
        session,
        organization_id,
        campaign_id,
        limit=min(batch_size, room),
        lease_seconds=lease_seconds,
    )
    if not claimed:
        counts = await _fold_campaign_counts(session, campaign)
        outstanding = counts.get("queued", 0) + counts.get("sending", 0)
        if outstanding == 0:
            await _mark_sent_if_still_sending(session, campaign_id)
            return "done", None
        return "idle", None  # Another worker holds the rest; nothing to do this tick.

    indexes = [r.email_index for r in claimed]
    suppressed = await selectors.suppressed_indexes(session, organization_id, indexes)
    # Consent is asked again here for the reason suppression is: an opt-out can land between
    # expansion and this claim, and the later answer is the true one. One query pair for the
    # whole batch (`consent.decide_indexes`), erased rows excluded — their index is blank.
    consent_decisions = await consent.decide_indexes(
        session,
        organization_id,
        "email",
        consent.MARKETING,
        [r.email_index for r in claimed if r.erased_at is None],
    )

    cfg = settings()
    # Open/click tracking (slice E1): None unless the campaign opted in. Registers the campaign's
    # link destinations once for the batch; the per-recipient work below is string rewriting only.
    tracking = await e1_tracking.prepare_plan(session, campaign)
    messages: list[ClaimedMessage] = []
    resolved: list[tuple[uuid.UUID, Literal["suppressed", "failed"], str]] = []
    for recipient in claimed:
        if recipient.email_index in suppressed:
            resolved.append((recipient.id, "suppressed", ""))
            continue
        if recipient.erased_at is not None:
            # The contact was deleted or erased after this row was queued. `email_enc` no
            # longer holds a real address (see `erase_contact_campaign_copies`), so this is
            # checked before `pii.open_sealed` rather than left to the `except` below — the
            # same outcome either way, but a clear reason instead of a decrypt failure.
            resolved.append((recipient.id, "failed", _ERASED_ERROR))
            continue
        refusal = consent_decisions.get(recipient.email_index)
        if refusal is not None and not refusal.allowed:
            # Recorded on the recipient as a machine-readable reason (`consent:opted_out`, ...).
            resolved.append((recipient.id, "suppressed", consent.stored_reason(refusal)))
            continue
        try:
            address = pii.open_sealed(recipient.email_enc, settings=cfg)
            message = campaign_message(
                campaign,
                to_address=address,
                unsubscribe=unsubscribe_url_for_recipient(organization_id, recipient.id),
                html_body=(
                    # Off the event loop: a pass over a body of up to 500 KB, and in production
                    # this driver shares a process (and a loop) with every tenant's API traffic.
                    # Pure string work on immutable inputs, so a worker thread is safe; the
                    # session is never touched from it.
                    await asyncio.to_thread(
                        tracking.render,
                        campaign.html_body,
                        organization_id=organization_id,
                        recipient_id=recipient.id,
                    )
                    if tracking is not None and campaign.html_body.strip()
                    else None
                ),
            )
        except Exception:
            # One row's own problem (a sealed address that will not open, a body that will not
            # render) must not sink the batch — logged without the address, which is exactly
            # what did not decrypt.
            log.warning(
                "engage.campaign.build_failed",
                campaign_id=str(campaign_id),
                recipient_id=str(recipient.id),
            )
            resolved.append((recipient.id, "failed", "Could not prepare this message"))
            continue
        messages.append(ClaimedMessage(recipient_id=recipient.id, outgoing=message))
    return "claimed", ClaimedBatch(
        connection=connection,
        sender=sender,
        messages=messages,
        resolved=resolved,
        transport_override=transport,
    )


async def record_campaign_batch(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    campaign_id: uuid.UUID,
    resolved: list[tuple[uuid.UUID, Literal["suppressed", "failed"], str]],
    sent: list[tuple[uuid.UUID, Receipt | TransportError]],
) -> bool:
    """The driver's record step — a second short transaction, after the provider call and with
    no transaction open while it ran. Returns whether any recipient came back `unavailable`,
    which is the driver's signal to back off this campaign rather than claim another batch
    immediately.

    A `TransportUnavailable` result releases its row straight back to `queued` (rather than
    waiting out its claim lease) so the very next successful tick resends it — at-least-once,
    the same guarantee the shared consumer's batches always gave, just resumable across a crash
    instead of bounded by one broker redelivery.
    """
    ids = [rid for rid, _, _ in resolved] + [rid for rid, _ in sent]
    rows = await selectors.recipients_by_id(session, organization_id, campaign_id, ids)
    now = datetime.now(UTC)
    any_unavailable = False

    for recipient_id, status, note in resolved:
        row = rows.get(recipient_id)
        if row is None:
            continue
        row.status = status
        row.error = note
        row.claimed_at = None

    for recipient_id, outcome in sent:
        row = rows.get(recipient_id)
        if row is None:
            continue
        if isinstance(outcome, Receipt):
            row.status = "sent"
            row.sent_at = now
            row.provider_message_id = outcome.provider_message_id
            row.error = ""
            row.claimed_at = None
        elif isinstance(outcome, TransportRefused):
            log.warning("engage.campaign.refused", recipient_id=str(recipient_id))
            row.status = "failed"
            row.error = _RECIPIENT_REFUSED
            row.claimed_at = None
        else:
            any_unavailable = True
            log.warning(
                "engage.campaign.unavailable",
                campaign_id=str(campaign_id),
                recipient_id=str(recipient_id),
            )
            # Released rather than left `sending`: the driver's backoff (not this row's lease)
            # is what paces the retry, so the row itself should look immediately retryable.
            row.status = "queued"
            row.claimed_at = None

    await session.flush()
    campaign = await selectors.get_campaign(session, organization_id, campaign_id)
    if campaign is not None:
        await _fold_campaign_counts(session, campaign)
    return any_unavailable
