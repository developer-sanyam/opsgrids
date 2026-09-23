"""HTTP surface for the notification service.

Every route declares the permission it needs. Notification governs what the **whole
organization** sends, so every permission is floored at ORG (`minimum=Scope.ORG`): there is no
self-service "my own templates" reading to protect, and a route guarded by the permission name
alone would let any holder act.

Every principal route is also guarded by `require_product("notification")` — defence in depth.
The gateway already refuses a request for a product the organization has not activated (402),
but a routing-table mistake must not become an entitlement bypass.

Reads go through `selectors`, writes through `services`. A router that builds its own query is
how an organization leaks, so none of them do. Every route takes the tenant-bound session and
opens no transaction of its own — the chokepoint owns it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, scope, selectors, services
from app.config import settings
from app.models import SCHEMA, Delivery, Message, Preference, ProviderTemplate, Rule, Template
from app.schemas import (
    DeliveryOut,
    MessageOut,
    PreferenceIn,
    PreferenceOut,
    ProviderTemplateIn,
    ProviderTemplateOut,
    RuleIn,
    RuleOut,
    SendIn,
    ServiceInfo,
    TemplateIn,
    TemplateOut,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the notification scope resolver at import of the api module ties it to the product
# it serves: the process that mounts these routes is the one that answers its scope questions.
scope.register()

router = APIRouter(tags=["notification"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: The `notification` entitlement gate, applied to every principal route as well as the gateway.
Entitled = Depends(require_product("notification"))

TemplateRead = Annotated[
    ScopeContext, Depends(require("notification.delivery.template.read", minimum=Scope.ORG))
]
TemplateWrite = Annotated[
    ScopeContext, Depends(require("notification.delivery.template.write", minimum=Scope.ORG))
]
#: Reading a delivered body means reading the invoice total or the payslip line that was sent,
#: so it is its own, more sensitive permission — not folded into template reading.
MessageRead = Annotated[
    ScopeContext, Depends(require("notification.delivery.message.read", minimum=Scope.ORG))
]
MessageSend = Annotated[
    ScopeContext, Depends(require("notification.delivery.message.send", minimum=Scope.ORG))
]
PreferenceWrite = Annotated[
    ScopeContext, Depends(require("notification.delivery.preference.write", minimum=Scope.ORG))
]


# --------------------------------------------------------------------- assemblers


def _template_out(row: Template) -> TemplateOut:
    refs = domain.placeholders(row.body) | domain.placeholders(row.subject or "")
    return TemplateOut(
        id=row.code,
        channel=row.channel,
        subject=row.subject,
        body=row.body,
        description=row.description,
        placeholders=sorted(refs),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _provider_template_out(row: ProviderTemplate) -> ProviderTemplateOut:
    return ProviderTemplateOut(
        id=row.code,
        channel=row.channel,
        provider_name=row.provider_name,
        provider_template_id=row.provider_template_id,
        language=row.language,
        category=row.category,
        parameter_format=row.parameter_format,
        status=row.status,
        status_reason=row.status_reason,
        status_checked_at=row.status_checked_at,
        variables=list(row.variables),
        preview_text=row.preview_text,
        # Derived rather than stored, so it can never disagree with `status`.
        sendable=domain.template_is_sendable(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _rule_out(row: Rule) -> RuleOut:
    return RuleOut(
        id=row.code,
        event_type=row.event_type,
        template_code=row.template_code,
        provider_template_code=row.provider_template_code,
        channel=row.channel,
        category=row.category,
        recipient_path=row.recipient_path,
        recipient_literal=row.recipient_literal,
        recipient_contact_path=row.recipient_contact_path,
        dedupe_key_path=row.dedupe_key_path,
        active=row.active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _delivery_out(row: Delivery) -> DeliveryOut:
    return DeliveryOut(
        attempt_no=row.attempt_no,
        provider=row.provider,
        outcome=row.outcome,
        detail=row.detail,
        at=row.at,
    )


def _message_out(row: Message, deliveries: list[Delivery]) -> MessageOut:
    return MessageOut(
        id=str(row.id),
        channel=row.channel,
        category=row.category,
        to_address=row.to_address,
        subject=row.subject,
        body=row.body,
        state=row.state,
        rule_code=row.rule_code,
        event_type=row.event_type,
        attempts=row.attempts,
        last_error=row.last_error,
        sent_at=row.sent_at,
        provider_message_id=row.provider_message_id,
        created_at=row.created_at,
        deliveries=[_delivery_out(d) for d in deliveries],
    )


def _preference_out(row: Preference) -> PreferenceOut:
    return PreferenceOut(
        recipient=row.recipient,
        channel=row.channel,
        category=row.category,
        suppressed=row.suppressed,
        reason=row.reason,
        updated_at=row.updated_at,
    )


# --------------------------------------------------------------------- service info


@router.get("/notifications/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


@router.get(
    "/notifications/subscribable-events",
    response_model=list[str],
    summary="Every event type a rule may react to",
    dependencies=[Entitled],
)
async def subscribable_events(ctx: TemplateRead) -> list[str]:
    """What the platform actually publishes.

    Exposed because a rule naming an unknown event type is refused, and an operator should be
    able to see the list rather than discover it one 422 at a time.
    """
    return list(domain.SUBSCRIBABLE_EVENTS)


# --------------------------------------------------------------------- templates


@router.get(
    "/notifications/templates",
    response_model=Page[TemplateOut],
    summary="Message templates",
    dependencies=[Entitled],
)
async def list_templates(
    session: SessionDep,
    ctx: TemplateRead,
    params: PageQuery,
    channel: Annotated[str | None, Query(description="email or whatsapp")] = None,
) -> Page[TemplateOut]:
    stmt = selectors.templates_stmt(ctx, channel=channel)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_template_out(r) for r in rows], total=total, params=params)


@router.put(
    "/notifications/templates",
    response_model=TemplateOut,
    status_code=status.HTTP_200_OK,
    summary="Create or replace a template",
    dependencies=[Entitled],
)
async def put_template(
    body: TemplateIn, session: SessionDep, ctx: TemplateWrite, request: Request
) -> TemplateOut:
    row = await services.upsert_template(
        session,
        ctx,
        code=body.code,
        channel=body.channel,
        subject=body.subject,
        body=body.body,
        description=body.description,
        request=request,
    )
    return _template_out(row)


# --------------------------------------------------------------------- provider templates


@router.get(
    "/notifications/provider-templates",
    response_model=Page[ProviderTemplateOut],
    summary="Templates the channel provider holds",
    dependencies=[Entitled],
)
async def list_provider_templates(
    session: SessionDep,
    ctx: TemplateRead,
    params: PageQuery,
    channel: Annotated[str | None, Query(description="email or whatsapp")] = None,
) -> Page[ProviderTemplateOut]:
    """What can be sent outside a conversation window, and what is still in review."""
    stmt = selectors.provider_templates_stmt(ctx, channel=channel)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_provider_template_out(r) for r in rows], total=total, params=params)


@router.put(
    "/notifications/provider-templates",
    response_model=ProviderTemplateOut,
    summary="Register or update a provider-held template",
    dependencies=[Entitled],
)
async def put_provider_template(
    body: ProviderTemplateIn, session: SessionDep, ctx: TemplateWrite, request: Request
) -> ProviderTemplateOut:
    row = await services.upsert_provider_template(
        session,
        ctx,
        code=body.code,
        channel=body.channel,
        provider_name=body.provider_name,
        language=body.language,
        category=body.category,
        parameter_format=body.parameter_format,
        status=body.status,
        status_reason=body.status_reason,
        variables=[v.model_dump() for v in body.variables],
        preview_text=body.preview_text,
        request=request,
    )
    return _provider_template_out(row)


# --------------------------------------------------------------------- rules


@router.get(
    "/notifications/rules",
    response_model=Page[RuleOut],
    summary="The automation rules",
    dependencies=[Entitled],
)
async def list_rules(
    session: SessionDep,
    ctx: TemplateRead,
    params: PageQuery,
    event_type: Annotated[str | None, Query()] = None,
) -> Page[RuleOut]:
    stmt = selectors.rules_stmt(ctx, event_type=event_type)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_rule_out(r) for r in rows], total=total, params=params)


@router.put(
    "/notifications/rules",
    response_model=RuleOut,
    summary="Create or replace an automation rule",
    dependencies=[Entitled],
)
async def put_rule(
    body: RuleIn, session: SessionDep, ctx: TemplateWrite, request: Request
) -> RuleOut:
    row = await services.upsert_rule(
        session,
        ctx,
        code=body.code,
        event_type=body.event_type,
        template_code=body.template_code,
        provider_template_code=body.provider_template_code,
        channel=body.channel,
        category=body.category,
        recipient_path=body.recipient_path,
        recipient_literal=body.recipient_literal,
        recipient_contact_path=body.recipient_contact_path,
        dedupe_key_path=body.dedupe_key_path,
        active=body.active,
        request=request,
    )
    return _rule_out(row)


# --------------------------------------------------------------------- messages


@router.get(
    "/notifications/messages",
    response_model=Page[MessageOut],
    summary="What was sent, and whether it arrived",
    dependencies=[Entitled],
)
async def list_messages(
    session: SessionDep,
    ctx: MessageRead,
    params: PageQuery,
    state: Annotated[
        str | None, Query(description="pending, sending, sent, failed or suppressed")
    ] = None,
    channel: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(description="Recipient or subject")] = None,
) -> Page[MessageOut]:
    stmt = selectors.messages_stmt(ctx, state=state, channel=channel, q=q)
    rows, total = await paginate(session, stmt, params)
    ledgers = await selectors.deliveries_by_message(
        session, ctx.organization_id, [r.id for r in rows]
    )
    items = [_message_out(r, ledgers.get(r.id, [])) for r in rows]
    return Page.build(items, total=total, params=params)


@router.get(
    "/notifications/messages/{message_id}",
    response_model=MessageOut,
    summary="One message and its attempt ledger",
    dependencies=[Entitled],
)
async def read_message(message_id: uuid.UUID, session: SessionDep, ctx: MessageRead) -> MessageOut:
    row = await selectors.get_message(session, ctx.organization_id, message_id)
    if row is None:
        raise NotFoundProblem("No such message")
    ledger = await selectors.get_deliveries(session, ctx.organization_id, row.id)
    return _message_out(row, ledger)


@router.post(
    "/notifications/messages",
    response_model=MessageOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a message now, outside any rule",
    dependencies=[Entitled],
)
async def send_message(
    body: SendIn, session: SessionDep, ctx: MessageSend, request: Request
) -> MessageOut:
    """202, not 201: the message is *staged*, and the dispatcher sends it.

    Returning 201 would claim a delivery this request cannot know about — the provider has not
    been called yet and will not be until the transaction commits.
    """
    row = await services.send_direct(
        session,
        ctx,
        template_code=body.template_code,
        channel=body.channel,
        to_address=body.to_address,
        category=body.category,
        context=dict(body.context),
        request=request,
    )
    return _message_out(row, [])


# --------------------------------------------------------------------- preferences


@router.get(
    "/notifications/preferences",
    response_model=Page[PreferenceOut],
    summary="Recipients who have refused something",
    dependencies=[Entitled],
)
async def list_preferences(
    session: SessionDep,
    ctx: TemplateRead,
    params: PageQuery,
    recipient: Annotated[str | None, Query()] = None,
) -> Page[PreferenceOut]:
    stmt = selectors.preferences_stmt(ctx, recipient=recipient)
    rows, total = await paginate(session, stmt, params)
    return Page.build([_preference_out(r) for r in rows], total=total, params=params)


@router.put(
    "/notifications/preferences",
    response_model=PreferenceOut,
    summary="Record or lift a recipient's suppression",
    dependencies=[Entitled],
)
async def put_preference(
    body: PreferenceIn, session: SessionDep, ctx: PreferenceWrite, request: Request
) -> PreferenceOut:
    row = await services.set_preference(
        session,
        ctx,
        recipient=body.recipient,
        channel=body.channel,
        category=body.category,
        suppressed=body.suppressed,
        reason=body.reason,
        request=request,
    )
    return _preference_out(row)
