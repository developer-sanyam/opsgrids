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

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.models import (
    AuditLog,
    Delivery,
    Message,
    OutboxEvent,
    Preference,
    ProviderTemplate,
    Rule,
    Template,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
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
        domain.validate_template_variables([domain.TemplateVariable(**v) for v in variables])
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
        raw_recipient = (
            rule.recipient_literal
            if rule.recipient_literal
            else domain.resolve_path(payload, rule.recipient_path or "")
        )
        if raw_recipient is None or not str(raw_recipient).strip():
            log.info(
                "notification.rule.no_recipient",
                rule=rule.code,
                event_type=event_type,
                path=rule.recipient_path,
            )
            continue

        try:
            to_address = domain.normalize_recipient(rule.channel, str(raw_recipient))
        except domain.RuleError as exc:
            log.warning(
                "notification.rule.bad_recipient",
                rule=rule.code,
                event_type=event_type,
                error=str(exc),
            )
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
