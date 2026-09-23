"""Writes for lead.

Every function follows the platform shape: change the row, record the audit entry, stage the
event — **all inside one transaction**. `get_tenant_session` opens the transaction and does not
commit for the route, so a crash between the state change and the event cannot leave one
without the other, and a rolled-back write never leaves an audit row claiming it happened.
Events are staged into the outbox, never published inline (ADR-0000).

`convert_lead` is the one function here that also does network I/O (`app/clients.py`, calling
customer's and deal's `/internal/v1`) inside the request's transaction. That is a deliberate
exception to "no I/O inside a transaction that must stay fast" the rest of the platform
follows for event consumers: a conversion is a synchronous user action with two dependent
writes that must land together with the lead's own status change, and both targets are
idempotent by id (`domain.conversion_ids`), so a timeout here is safe to retry rather than
something that must be moved off the request path.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import clients, domain, selectors
from app.config import settings
from app.models import AssignmentRule, AuditLog, Lead, OutboxEvent
from app.schemas import LeadPatch
from platform_core.audit import record_audit, record_sensitive_read
from platform_core.auth import Scope, ScopeContext
from platform_core.errors import (
    ConflictProblem,
    ForbiddenProblem,
    UpstreamUnavailableProblem,
    ValidationProblem,
)
from platform_core.events import publish_event
from platform_core.models import utcnow
from platform_core.pii import blind_index, open_sealed, seal

# Event names. `crm.*` because these are the product's own, not the platform's.
EVT_LEAD_CREATED = "crm.lead.created"
EVT_LEAD_UPDATED = "crm.lead.updated"
EVT_LEAD_CONVERTED = "crm.lead.converted"

#: How far out a converted lead's deal defaults to closing. A lead carries no close date of
#: its own (ecosystem-gaps.md §2 — that is exactly why it is not modelled as a deal), so
#: `:convert` has to pick something; thirty days is deal-service's own board's typical early
#: pipeline horizon, and a rep changes it on the deal the moment they have a real one.
_DEFAULT_CLOSE_HORIZON_DAYS = 30


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


# ------------------------------------------------------------------ assignment rule


async def _get_or_create_rule(session: AsyncSession, ctx: ScopeContext) -> AssignmentRule:
    """The organization's rule, creating an empty (unconfigured) one on first touch so a
    lead can always be created — an org with no rule configured yet simply assigns nobody."""
    rule = await selectors.get_assignment_rule(session, ctx.organization_id)
    if rule is None:
        rule = AssignmentRule(
            organization_id=ctx.organization_id, created_by=str(ctx.principal.sub)
        )
        session.add(rule)
        await session.flush()
    return rule


async def set_assignment_rule(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    mode: str,
    owner_ids: list[uuid.UUID],
) -> AssignmentRule:
    """Configure the organization's round-robin. ORG-scoped only: this is workspace
    configuration, not any one rep's record, so an OWN-scoped `crm.lead.lead.write` grant is
    refused outright rather than silently allowed to reconfigure everyone's rotation."""
    if ctx.scope is not Scope.ORG:
        raise ForbiddenProblem("Configuring lead assignment requires ORG scope")

    rule = await _get_or_create_rule(session, ctx)
    before = {"mode": rule.mode, "owner_ids": [str(o) for o in rule.owner_ids]}
    rule.mode = mode
    rule.owner_ids = list(owner_ids)
    # Reset on reconfigure: a cursor from the old list has no meaningful position in a new
    # one, and starting over at the front is simpler to reason about than trying to carry a
    # fractional position across an edit.
    rule.cursor = 0
    rule.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="assignment_rule.updated",
        entity="assignment_rule",
        entity_id=rule.id,
        before=before,
        after={"mode": mode, "owner_ids": [str(o) for o in owner_ids]},
    )
    return rule


async def _assign_owner(session: AsyncSession, rule: AssignmentRule) -> uuid.UUID | None:
    """Round-robin's write half: `domain.next_owner` picks, this advances the cursor in the
    same transaction as the lead it assigns, so a crash between the two cannot skip or
    repeat a rep. Returns `None` under `manual` mode or an empty rotation — a lead with no
    owner is a valid, unassigned lead, not an error."""
    if rule.mode != "round_robin":
        return None
    owner_id, new_cursor = domain.next_owner(rule.owner_ids, rule.cursor)
    if owner_id is not None:
        rule.cursor = new_cursor
        await session.flush()
    return owner_id


# ------------------------------------------------------------------ leads


async def create_lead(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    full_name: str,
    company: str | None,
    email: str | None,
    phone: str | None,
    source: str,
    owner_id: uuid.UUID | None,
    notes: str,
    request: Request | None = None,
) -> Lead:
    """Capture a lead. `email` / `phone` are sealed and masked before anything is stored —
    the raw values are never written to a column that survives this function. A repeat
    submission of the same email in this organization refuses (409): the dedup lookup reads
    the blind index, never a decrypted value, so checking costs no sensitive read.

    `owner_id` omitted assigns from the organization's round-robin (`AssignmentRule`); a form
    submission has no rep to name, so this is the path most captures take.
    """
    cfg = settings()

    email_masked: str | None = None
    email_enc: bytes | None = None
    email_index: str | None = None
    if email:
        email_index = blind_index(email, kind="email", settings=cfg)
        if await selectors.get_lead_by_email_index(session, ctx.organization_id, email_index):
            raise ConflictProblem(f"A lead with email {email!r} already exists")
        email_enc, email_masked = seal(email, kind="email", settings=cfg)

    phone_masked: str | None = None
    phone_enc: bytes | None = None
    phone_index: str | None = None
    if phone:
        phone_enc, phone_masked = seal(phone, kind="phone", settings=cfg)
        phone_index = blind_index(phone, kind="phone", settings=cfg)

    resolved_owner_id = owner_id
    if resolved_owner_id is None:
        rule = await _get_or_create_rule(session, ctx)
        resolved_owner_id = await _assign_owner(session, rule)

    lead = Lead(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        full_name=full_name,
        company=company,
        email_masked=email_masked,
        email_enc=email_enc,
        email_index=email_index,
        phone_masked=phone_masked,
        phone_enc=phone_enc,
        phone_index=phone_index,
        source=source,
        status="new",
        owner_id=resolved_owner_id,
        notes=notes,
    )
    session.add(lead)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="lead.created",
        entity="lead",
        entity_id=lead.id,
        after={"full_name": full_name, "source": source, "status": "new"},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LEAD_CREATED,
        aggregate_type="lead",
        aggregate_id=lead.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "lead_id": str(lead.id),
            "source": source,
            "owner_id": str(resolved_owner_id) if resolved_owner_id else None,
        },
        actor=_actor(ctx),
    )
    return lead


async def update_lead(
    session: AsyncSession,
    ctx: ScopeContext,
    lead: Lead,
    payload: LeadPatch,
    *,
    request: Request | None = None,
) -> Lead:
    """Patch a lead's plain fields and status. `status` is checked against
    `domain.can_transition`, which has no move landing on `converted` — only
    `POST /leads/{id}:convert` can set that, because it is the one path that also creates the
    company and deal the status implies. `owner_id` is re-scope-checked on the *new* value,
    exactly as `deal.update_deal` does: an OWN-scoped caller may only reassign to themselves.
    """
    changes = payload.model_dump(exclude_unset=True)

    if "owner_id" in changes and changes["owner_id"] is not None:
        ctx.assert_can_touch(owner_id=changes["owner_id"])

    if "status" in changes:
        new_status = changes["status"]
        if not domain.can_transition(lead.status, new_status):
            raise ConflictProblem(f"A lead in {lead.status!r} cannot move to {new_status!r}")

    before = {field: getattr(lead, field) for field in changes}
    for field, value in changes.items():
        setattr(lead, field, value)
    lead.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="lead.updated",
        entity="lead",
        entity_id=lead.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LEAD_UPDATED,
        aggregate_type="lead",
        aggregate_id=lead.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "lead_id": str(lead.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return lead


async def convert_lead(
    session: AsyncSession,
    ctx: ScopeContext,
    lead: Lead,
    *,
    request: Request | None = None,
) -> tuple[uuid.UUID, uuid.UUID | None, uuid.UUID]:
    """Convert a qualified lead into a company (+ contact) and a deal.

    Idempotent: a lead already `converted` short-circuits to its stored ids without calling
    either target service again — the second half of ADR-0017's guarantee (the first half,
    "the *same* ids both times", is `domain.conversion_ids`). Concurrent conversions of the
    *same* lead are not additionally guarded here (no row lock beyond RLS's own tenant
    isolation) — two simultaneous calls would both attempt the same idempotent-by-id creates
    on customer/deal, and the loser of that race sees a database conflict rather than a clean
    reuse. Accepted for this slice: the ordinary path is one rep converting one lead once, and
    the sequential retry this function *is* built for (a timed-out first call, retried) is
    exactly the case that matters.

    Raises `ValidationProblem` when the lead has no owner, or its owner is not an active rep
    in customer's directory. Raises `UpstreamUnavailableProblem` when either target service
    could not be reached — safe to retry, since nothing here has committed yet.
    """
    if lead.status == "converted":
        if lead.converted_company_id is None or lead.converted_deal_id is None:
            # The model's own invariant (`app/models.py`: these are set together, never
            # separately) broken by something outside this function's control — a hand-edited
            # row, a bad migration. Not something a retry fixes, so this is a fault to
            # investigate, not a validation error to hand back.
            raise RuntimeError(f"lead {lead.id} is converted but is missing its conversion ids")
        return lead.converted_company_id, lead.converted_contact_id, lead.converted_deal_id

    if not domain.can_convert(lead.status):
        raise ConflictProblem(f"A lead in {lead.status!r} cannot be converted — qualify it first")
    if lead.owner_id is None:
        raise ValidationProblem("Assign an owner to this lead before converting it")

    cfg = settings()
    raw_email: str | None = None
    raw_phone: str | None = None
    sensitive_fields: list[str] = []
    if lead.email_enc is not None:
        raw_email = open_sealed(lead.email_enc, settings=cfg)
        sensitive_fields.append("email")
    if lead.phone_enc is not None:
        raw_phone = open_sealed(lead.phone_enc, settings=cfg)
        sensitive_fields.append("phone")
    if sensitive_fields:
        # Opening a lead's own sealed contact details, to hand them to customer-service so it
        # can seal its own copy, is itself a sensitive read — audited the same way
        # `unmask_contact_phone` audits a human's stepped-up unmask.
        record_sensitive_read(
            session,
            AuditLog,
            organization_id=ctx.organization_id,
            actor_id=ctx.principal.sub,
            entity="lead",
            entity_id=lead.id,
            fields=sensitive_fields,
            request=request,
        )

    company_id, contact_id, deal_id = domain.conversion_ids(lead.id)
    company_name = lead.company or lead.full_name
    close_date = date.today() + timedelta(days=_DEFAULT_CLOSE_HORIZON_DAYS)

    try:
        resolved_company_id, resolved_contact_id = await clients.ensure_company_and_contact(
            cfg,
            ctx.organization_id,
            company_id=company_id,
            contact_id=contact_id,
            company_name=company_name,
            owner_id=lead.owner_id,
            contact_full_name=lead.full_name,
            contact_email=raw_email,
            contact_raw_phone=raw_phone,
        )
        resolved_deal_id = await clients.ensure_deal(
            cfg,
            ctx.organization_id,
            deal_id=deal_id,
            company_id=resolved_company_id,
            contact_id=resolved_contact_id,
            owner_id=lead.owner_id,
            name=f"{company_name} — {lead.full_name}",
            close_date=close_date.isoformat(),
            source=lead.source,
        )
    except clients.OwnerUnknown as exc:
        raise ValidationProblem(str(exc)) from exc
    except clients.ConversionUnavailable as exc:
        raise UpstreamUnavailableProblem(str(exc)) from exc

    lead.status = "converted"
    lead.converted_company_id = resolved_company_id
    lead.converted_contact_id = resolved_contact_id
    lead.converted_deal_id = resolved_deal_id
    lead.converted_at = utcnow()
    lead.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="lead.converted",
        entity="lead",
        entity_id=lead.id,
        before={"status": "qualified"},
        after={
            "status": "converted",
            "company_id": str(resolved_company_id),
            "contact_id": str(resolved_contact_id) if resolved_contact_id else None,
            "deal_id": str(resolved_deal_id),
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_LEAD_CONVERTED,
        aggregate_type="lead",
        aggregate_id=lead.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "lead_id": str(lead.id),
            "company_id": str(resolved_company_id),
            "contact_id": str(resolved_contact_id) if resolved_contact_id else None,
            "deal_id": str(resolved_deal_id),
        },
        actor=_actor(ctx),
    )
    return resolved_company_id, resolved_contact_id, resolved_deal_id
