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

import base64
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import dns_check, domain, selectors
from app.config import settings
from app.models import (
    AuditLog,
    Contact,
    Delivery,
    EmailConnection,
    EmailDomain,
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
from app.targets import TargetResolver
from app.transports import (
    Outgoing,
    SmtpTransport,
    Transport,
    TransportRefused,
    TransportUnavailable,
)
from platform_core import pii
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.cache import enforce_rate_limit
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    NotFoundProblem,
    UpstreamUnavailableProblem,
    ValidationProblem,
)
from platform_core.events import publish_event
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

    message = Message(
        organization_id=organization_id,
        created_by=created_by,
        channel=channel,
        category=category,
        to_address=to_address,
        subject=subject,
        body=body,
        state=domain.STATE_SUPPRESSED if suppressed else domain.STATE_PENDING,
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
    out of attempts, or claimed by another dispatcher. Returning None rather than raising keeps a
    redelivered queued-event a no-op instead of a dead letter.
    """
    message = await selectors.get_message(session, organization_id, message_id)
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
        message.updated_by = SYSTEM_ACTOR
        await session.flush()
        log.info("notification.suppressed_at_dispatch", message_id=str(message.id))
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
) -> None:
    """Mark a message sent and append the winning attempt to its ledger."""
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
            detail=None,
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


async def record_failure(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    message_id: uuid.UUID,
    provider: str,
    error: str,
    permanent: bool,
    max_attempts: int,
) -> None:
    """Append the failed attempt and decide whether the message waits for another.

    A permanent refusal goes straight to `failed` — retrying an address the provider will never
    accept only delays the moment somebody looks at it. A transient failure returns to `pending`
    while attempts remain, and the broker's redelivery brings it back.
    """
    message = await selectors.get_message(session, organization_id, message_id)
    if message is None:  # pragma: no cover
        return

    session.add(
        Delivery(
            organization_id=organization_id,
            created_by=SYSTEM_ACTOR,
            message_id=message.id,
            attempt_no=message.attempts,
            provider=provider,
            outcome="failed",
            detail=error[:2000],
            at=datetime.now(UTC),
        )
    )

    retryable = not permanent and domain.should_retry(message.attempts, max_attempts=max_attempts)
    message.state = domain.STATE_PENDING if retryable else domain.STATE_FAILED
    message.last_error = error[:2000]
    message.updated_by = SYSTEM_ACTOR
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


# --------------------------------------------------------------------- provider receipts


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
        against nothing would invent a message.
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

#: 2048 bits, and not a choice of ours: 1024 is being retired by the large mailbox providers, and
#: a 4096-bit public key does not fit one 255-byte TXT character-string — a registrar that will
#: not split it publishes a record no verifier can read.
_DKIM_KEY_BITS = 2048

#: What a test message says. Fixed text, because its only job is to prove the path: a customer
#: reading it needs to know which address it came from and that nothing else is expected of them.
TEST_SEND_SUBJECT = "OpsGrids test message"
TEST_SEND_BODY = (
    "This is a test message from OpsGrids.\n\n"
    "Receiving it means {address} can send mail through your provider, and that sender is now "
    "usable in your campaigns.\n"
)


def dkim_selector(organization_id: uuid.UUID, domain_name: str) -> str:
    """The DKIM selector for one organization's domain. Derived, never random.

    The record set is stored so the setup screen shows the values it showed yesterday; a selector
    minted from a uuid or a clock would hand the customer a different record to paste every time
    anything regenerated it.

    Derived from the organization as well as the name so two organizations adding the same domain
    get different selectors. Only one of them owns the zone at a time, but a hand-over between
    them is then two records published side by side rather than one record they take turns
    breaking.
    """
    digest = hashlib.sha256(f"{organization_id}:{domain_name}".encode()).hexdigest()
    return f"og{digest[:12]}"


def _dkim_keypair() -> tuple[str, str]:
    """A fresh signing key as `(private PEM, base64 public key)`.

    The public half is base64 of the DER SubjectPublicKeyInfo, which is what `p=` in a DKIM
    record is defined to carry — not a PEM, whose header lines and newlines no verifier strips.
    The private half is unencrypted PEM because it is sealed by the caller a line later; a
    passphrase on top would only move the problem to wherever that passphrase lived.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=_DKIM_KEY_BITS)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, base64.b64encode(public_der).decode("ascii")


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
    waba_id: str,
    phone_number_id: str,
    display_number: str,
    access_token: str,
    request: Request | None = None,
) -> ProviderAccount:
    """Record one organization's WhatsApp Cloud API account, its token sealed on the way in.

    One per organization, refused here as well as by the org+channel index — mirrors
    `create_connection`'s reasoning for email. `phone_number_id` is also globally unique (see
    `ProviderAccount`'s docstring: it is the webhook routing key), so a number already claimed
    by another organization is refused with a sentence rather than surfaced as an IntegrityError.
    """
    existing = await selectors.get_whatsapp_connection(session, ctx.organization_id)
    if existing is not None:
        raise BusinessRuleProblem(
            "This organization already has a WhatsApp account connected. Disconnect it before "
            "adding another."
        )

    claimed = await selectors.account_for_phone_number(session, phone_number_id)
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
    await session.flush()

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
    waba_id: str,
    phone_number_id: str,
    display_number: str,
    access_token: str,
    request: Request | None = None,
) -> ProviderAccount:
    """Replace the one WhatsApp account `create_whatsapp_connection` refuses to add a second of.

    In place, not delete-then-create, for the same reason `replace_connection` is: no window
    with no connection at all. The old token is overwritten, not archived — there is no history
    column for it and there must not become one.
    """
    existing = await selectors.get_whatsapp_connection(session, ctx.organization_id)
    if existing is None:
        raise NotFoundProblem("No WhatsApp account to replace. Connect one first.")

    if phone_number_id != existing.phone_number_id:
        claimed = await selectors.account_for_phone_number(session, phone_number_id)
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

    await session.flush()

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
    """Add a sending domain and generate everything the customer has to publish for it.

    The DKIM keypair is minted **here**, because `domain.dns_records_for` is pure and cannot:
    the record it returns carries an empty value until a public key exists to fill it, and a
    domain created without one publishes a record that can never verify — a setup flow that
    looks complete and signs nothing.

    The private half is sealed like any other secret at rest. It is the thing that lets anyone
    sign mail as this customer, which makes it the most dangerous value in the schema.
    """
    name = domain.strip().lower().rstrip(".")
    if not name or "@" in name or " " in name or "." not in name:
        raise ValidationProblem(f"{domain!r} is not a domain name")

    if await selectors.get_domain_by_name(session, ctx.organization_id, name) is not None:
        raise ConflictProblem(f"{name} is already a sending domain for this organization")

    selector = dkim_selector(ctx.organization_id, name)
    private_pem, public_key = _dkim_keypair()
    records = _rules.dns_records_for(name, selector, f"track.{name}")
    for record in records:
        if record["kind"] == "dkim":
            record["value"] = f"v=DKIM1; k=rsa; p={public_key}"

    sealed, _ = pii.seal(private_pem, kind="generic", settings=settings())
    row = EmailDomain(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        domain=name,
        # Folded rather than written as "pending", so one function owns what a record set means.
        # A freshly generated set has verified nothing, so it folds to pending — which is the
        # point: the customer has published none of this yet.
        status=_rules.fold_domain_status(records),
        records=records,
        dkim_private_key_enc=sealed,
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
        after={"domain": name, "dkim_selector": selector, "status": row.status},
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


async def verify_domain(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    domain_id: uuid.UUID,
    request: Request | None = None,
    resolver: dns_check.Resolver | None = None,
) -> EmailDomain:
    """Re-read what DNS actually publishes for one domain and fold the answer into its status.

    The lookups happen inside the caller's transaction, which is the one place in this service
    that is true. It is a bounded read of a handful of names, run because an operator pressed
    *verify* and is waiting for the answer; splitting it into two transactions would mean a route
    that commits twice and a row that can be left claiming it was checked when it was not.

    `records` is replaced rather than mutated: `check_records` returns a copy, and JSONB only
    notices a reassignment.
    """
    row = await selectors.get_domain(session, ctx.organization_id, domain_id)
    if row is None:
        raise NotFoundProblem("No such sending domain")

    was = row.status
    checked = await dns_check.check_records(row.records, resolver=resolver)
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
            "records": {record["kind"]: record["verified"] for record in checked},
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


def unsubscribe_token(organization_id: uuid.UUID, email: str) -> str:
    """The token that goes into the unsubscribe link of mail sent to `email`."""
    key = unsubscribe_key()
    if key is None:
        raise UpstreamUnavailableProblem("Unsubscribe links are not configured")
    return _rules.make_unsubscribe_token(organization_id, _normalize_email(email), key)


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
    if email is not None:
        normalized = _normalize_email(email)
        index = selectors.email_index(normalized)
        if index != contact.email_index:
            if await selectors.contacts_by_index(session, ctx.organization_id, [index]):
                raise ConflictProblem("Another contact already has that address")
            contact.email_enc, contact.email_masked, contact.email_index = _sealed_email(normalized)
    if name is not None:
        contact.name = name
    if attributes is not None:
        contact.attributes = attributes
    contact.updated_by = str(ctx.principal.sub)
    contact.updated_at = datetime.now(UTC)
    await session.flush()

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
    someone from the audience must never make them mailable again on re-import."""
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
    await session.delete(row)
    await session.flush()


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
