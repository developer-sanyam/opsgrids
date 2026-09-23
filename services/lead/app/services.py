"""Writes for lead.

Every function follows the platform shape: change the row, record the audit entry, stage the
event — **all inside one transaction**. `get_tenant_session` opens the transaction and does not
commit for the route, so a crash between the state change and the event cannot leave one
without the other, and a rolled-back write never leaves an audit row claiming it happened.
Events are staged into the outbox, never published inline (ADR-0000).

`convert_lead` is the one exception to "one transaction" here, deliberately: it is claim, call,
record — three steps, three transactions, with the two calls to customer's and deal's
`/internal/v1` running with **no transaction open** between them. `claim_conversion`,
`finalize_conversion` and `fail_conversion` each open and commit their own; see `convert_lead`'s
own docstring for why holding a row lock (or worse, a whole request's transaction) across two
network calls was the bug this shape replaces.

`set_assignment_rule`, `create_lead` and `update_lead`'s owner validation (`_ensure_owners_
active`) are a smaller, accepted version of the same trade-off: they call customer-service's
`owners:validate` from inside the request's one transaction, holding it open for the round
trip. Unlike `:convert`, none of these sits on a hot path a rep repeats constantly, and none of
them opens a sealed value first — a live database transaction blocked on a single fast internal
call from a low-frequency write is a smaller cost than the extra transaction-splitting `:convert`
needed to keep decrypted PII off an open connection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import clients, domain, selectors
from app.config import settings
from app.models import AssignmentRule, AuditLog, Lead, OutboxEvent
from app.schemas import LeadPatch
from platform_core.audit import record_audit, record_sensitive_read
from platform_core.auth import Scope, ScopeContext
from platform_core.db import assert_tenant, set_tenant
from platform_core.errors import (
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
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


async def _bind_tenant(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Bind a transaction to the caller's organization — the same tenancy chokepoint
    `get_tenant_session` applies for every other route's one transaction, done by hand here
    because `:convert`'s claim/call/record shape needs three separate transactions on one
    session rather than the single one `get_tenant_session` opens for the whole request.
    Must run as the first statement inside each transaction."""
    await set_tenant(session, organization_id)
    if not settings().is_production:
        await assert_tenant(session, organization_id)


async def _ensure_owners_active(organization_id: uuid.UUID, owner_ids: list[uuid.UUID]) -> None:
    """lead-service's own gate for an `owner_id` it is about to write — ask customer-service's
    `owners:validate` (restricted to lead-service alone) which of `owner_ids` are **not**
    active reps, and refuse if any are. Called before an explicit `owner_id` on a lead
    create/patch, and before accepting a round-robin owner list — an inactive or unknown rep
    never enters lead-service's own writes in the first place, matching the same refusal
    customer's own `_assert_owner_assignable` already gives every write there.
    """
    if not owner_ids:
        return
    cfg = settings()
    try:
        invalid = await clients.invalid_owner_ids(cfg, organization_id, owner_ids)
    except clients.OwnersValidateUnavailable as exc:
        raise UpstreamUnavailableProblem(
            "Could not confirm the owner directory. Try again shortly."
        ) from exc
    if invalid:
        raise ValidationProblem(
            "One or more owners are not active reps in this organization's CRM book"
        )


# ------------------------------------------------------------------ assignment rule


async def _get_or_create_rule(session: AsyncSession, ctx: ScopeContext) -> AssignmentRule:
    """The organization's rule, row-locked, creating an empty (unconfigured) one on first
    touch so a lead can always be created — an org with no rule configured yet simply assigns
    nobody.

    `INSERT ... ON CONFLICT DO NOTHING` rather than "check then insert": two concurrent first
    writes for the same organization racing a plain check-then-insert would both see no rule
    and both try to create one, and the loser would hit `uq_assignment_rules_org` as an
    unhandled `IntegrityError` instead of quietly finding the row the winner just created.
    """
    stmt = (
        pg_insert(AssignmentRule)
        .values(organization_id=ctx.organization_id, created_by=str(ctx.principal.sub))
        .on_conflict_do_nothing(index_elements=["organization_id"])
    )
    await session.execute(stmt)
    rule = await selectors.get_assignment_rule_for_update(session, ctx.organization_id)
    if rule is None:
        # Unreachable except by a bug in the statement above: the insert either created the
        # row or a concurrent one already had, and either way a row now exists to lock.
        raise RuntimeError(f"assignment rule for {ctx.organization_id} could not be created")
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
    refused outright rather than silently allowed to reconfigure everyone's rotation. Every
    id in `owner_ids` must be a known, active rep (`_ensure_owners_active`) — the rule is
    refused rather than saved with an inactive owner already inside it."""
    if ctx.scope is not Scope.ORG:
        raise ForbiddenProblem("Configuring lead assignment requires ORG scope")
    await _ensure_owners_active(ctx.organization_id, owner_ids)

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
    same transaction as the lead it assigns. `rule` must already be row-locked
    (`_get_or_create_rule`/`get_assignment_rule_for_update`) — the lock plus this one atomic
    read-modify-write is what stops two concurrent lead creates from both picking
    `owner_ids[0]` and both advancing the cursor to `1`, the same rep twice with the rotation
    never having moved. Returns `None` under `manual` mode or an empty rotation — a lead with
    no owner is a valid, unassigned lead, not an error."""
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
    the raw values are never written to a column that survives this function. `phone` is
    expected already canonicalised to E.164 (`app.schemas.LeadIn`'s own validator does this
    at the boundary, before this function ever sees it). A repeat submission of the same
    email in this organization refuses (409): the dedup lookup reads the blind index, never a
    decrypted value, so checking costs no sensitive read.

    `owner_id` omitted assigns from the organization's round-robin (`AssignmentRule`); a form
    submission has no rep to name, so this is the path most captures take. Named explicitly,
    it must be a rep the caller may act as (`ctx.assert_can_touch` — an OWN-scoped caller may
    only create leads owned by themselves) and a known, active rep (`_ensure_owners_active`).
    """
    if owner_id is not None:
        ctx.assert_can_touch(owner_id=owner_id)
        await _ensure_owners_active(ctx.organization_id, [owner_id])

    cfg = settings()

    email_masked: str | None = None
    email_enc: bytes | None = None
    email_index: str | None = None
    if email:
        email_index = blind_index(email, kind="email", settings=cfg)
        if await selectors.get_lead_by_email_index(session, ctx.organization_id, email_index):
            raise ConflictProblem("A lead with that email already exists")
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
    company and deal the status implies.

    `owner_id` set to a rep is re-scope-checked on the *new* value (an OWN-scoped caller may
    only reassign to themselves) and must be a known, active rep (`_ensure_owners_active`).
    `owner_id` set to `None` — unassigning — requires ORG scope: the route's own object-level
    check already confirmed the caller may touch this lead's *current* owner, but removing an
    owner is a step further than reassigning to yourself, and OWN scope was never meant to
    cover it.
    """
    changes = payload.model_dump(exclude_unset=True)

    if "owner_id" in changes:
        if changes["owner_id"] is None:
            if ctx.scope is not Scope.ORG:
                raise ForbiddenProblem("Unassigning a lead requires ORG scope")
        else:
            ctx.assert_can_touch(owner_id=changes["owner_id"])
            await _ensure_owners_active(ctx.organization_id, [changes["owner_id"]])

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


# ------------------------------------------------------------------ conversion: claim, call, record


@dataclass(frozen=True, slots=True)
class ConversionClaim:
    """A snapshot of what `convert_lead` needs once `claim_conversion`'s transaction has
    committed. Deliberately a plain dataclass, never the ORM `Lead` row: the whole point of
    splitting this into its own transaction is that the network calls which follow run with
    no transaction, and therefore no session state, open — touching an ORM object after its
    transaction closed is exactly the "attribute access needs IO the sync bridge cannot do
    outside a transaction" trap `services.convert_lead`'s tests guard against."""

    lead_id: uuid.UUID
    claim_id: uuid.UUID | None
    already_converted: bool
    company_id: uuid.UUID
    contact_id: uuid.UUID | None
    deal_id: uuid.UUID
    #: `None` only on the `already_converted` branch's stale-data edge case (the owner was
    #: unassigned by a `PATCH` after this lead already converted) — never on a fresh claim,
    #: which refuses before it gets here (`claim_conversion`'s own "assign an owner" check).
    #: `convert_lead` narrows this itself before it is ever passed to a client that needs it.
    owner_id: uuid.UUID | None
    full_name: str
    company: str | None
    source: str
    raw_email: str | None
    raw_phone: str | None


async def claim_conversion(
    session: AsyncSession,
    ctx: ScopeContext,
    lead_id: uuid.UUID,
    *,
    request: Request | None = None,
) -> ConversionClaim:
    """Tx1 of `convert_lead`'s claim / call / record shape.

    Locks the lead row and either: short-circuits an already-converted lead (no claim, no
    network call, the same ids returned again); refuses (409) a live claim another attempt
    holds (`domain.claim_is_live`, a `domain.CONVERSION_LEASE_SECONDS`-second lease); or takes
    the claim — a fresh `claim_id`, freshly stamped `conversion_claimed_at` — whether the lead
    was never claimed or a previous claim's lease has expired (a takeover is not a special
    case here: it is simply "not live", handled the same way as "never claimed").

    Opening the lead's own sealed email/phone and writing the `entity.sensitive_read` audit
    row for that happens **in this transaction**, before it commits — the fix for the finding
    that mattered most: previously, that audit row lived in the same transaction as the two
    network calls that followed it, so a failure in either call rolled the audit row back
    after the plaintext had already left this process on the wire. Now the record of "this
    attempt opened this lead's PII" is durable before a single byte of it is sent anywhere.
    """
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)

        lead = await selectors.get_lead_for_update(session, ctx.organization_id, lead_id)
        if lead is None:
            raise NotFoundProblem("That lead is not part of this organization")
        ctx.assert_can_touch(owner_id=lead.owner_id)

        if lead.status == "converted":
            if lead.converted_company_id is None or lead.converted_deal_id is None:
                # The model's own invariant (`app/models.py`: these are set together, never
                # separately) broken by something outside this function's control — a
                # hand-edited row, a bad migration. Not something a retry fixes.
                raise RuntimeError(f"lead {lead.id} is converted but is missing its conversion ids")
            return ConversionClaim(
                lead_id=lead.id,
                claim_id=None,
                already_converted=True,
                company_id=lead.converted_company_id,
                contact_id=lead.converted_contact_id,
                deal_id=lead.converted_deal_id,
                owner_id=lead.owner_id,
                full_name=lead.full_name,
                company=lead.company,
                source=lead.source,
                raw_email=None,
                raw_phone=None,
            )

        if not domain.can_convert(lead.status):
            raise ConflictProblem(
                f"A lead in {lead.status!r} cannot be converted — qualify it first"
            )
        if lead.owner_id is None:
            raise ValidationProblem("Assign an owner to this lead before converting it")
        owner_id: uuid.UUID = lead.owner_id

        now = utcnow()
        if domain.claim_is_live(lead.conversion_claimed_at, now=now):
            raise ConflictProblem("This lead is already being converted. Try again in a moment.")

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
            # Opening a lead's own sealed contact details, to hand them to customer-service so
            # it can seal its own copy, is itself a sensitive read — audited the same way
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

        claim_id = uuid.uuid4()
        lead.conversion_claim_id = claim_id
        lead.conversion_claimed_at = now
        lead.updated_by = str(ctx.principal.sub)
        await session.flush()

        company_id, contact_id, deal_id = domain.conversion_ids(lead.id)

        return ConversionClaim(
            lead_id=lead.id,
            claim_id=claim_id,
            already_converted=False,
            company_id=company_id,
            contact_id=contact_id,
            deal_id=deal_id,
            owner_id=owner_id,
            full_name=lead.full_name,
            company=lead.company,
            source=lead.source,
            raw_email=raw_email,
            raw_phone=raw_phone,
        )


async def finalize_conversion(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    lead_id: uuid.UUID,
    claim_id: uuid.UUID,
    company_id: uuid.UUID,
    contact_id: uuid.UUID | None,
    deal_id: uuid.UUID,
    request: Request | None = None,
) -> None:
    """Tx2 of `convert_lead`'s claim / call / record shape: record a successful conversion.

    Re-locks the lead and proceeds only if it still holds *this* `claim_id` — if the lease
    expired while the network calls were in flight and a second attempt has since taken over,
    this raises rather than finalizing a conversion the row no longer agrees this attempt
    owns (the second attempt's own `finalize_conversion` is the one that gets to write the
    outcome; this one's caller should treat the 409 as "someone else finished this").
    """
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)

        lead = await selectors.get_lead_for_update(session, ctx.organization_id, lead_id)
        if lead is None or lead.conversion_claim_id != claim_id:
            raise ConflictProblem(
                "This lead's conversion claim was taken over by another attempt. "
                "Call :convert again to get the current result."
            )

        lead.status = "converted"
        lead.converted_company_id = company_id
        lead.converted_contact_id = contact_id
        lead.converted_deal_id = deal_id
        lead.converted_at = utcnow()
        lead.conversion_claim_id = None
        lead.conversion_claimed_at = None
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
                "company_id": str(company_id),
                "contact_id": str(contact_id) if contact_id else None,
                "deal_id": str(deal_id),
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
                "company_id": str(company_id),
                "contact_id": str(contact_id) if contact_id else None,
                "deal_id": str(deal_id),
            },
            actor=_actor(ctx),
        )


async def fail_conversion(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    lead_id: uuid.UUID,
    claim_id: uuid.UUID,
    reason: str,
    request: Request | None = None,
) -> None:
    """The other half of Tx2: clear a claim that will not be finalized, so the *next* attempt
    does not have to wait out a lease on a conversion that has already, definitively, failed —
    a permanent rejection from either target service, or a transient failure lead-service has
    already decided to surface to its own caller rather than silently retry. `reason` is one
    of `app/clients.py`'s own fixed, generic exception messages — never an upstream response
    body — recorded so the audit trail says *that* a conversion attempt failed and roughly
    why, without carrying anything either target service said back to us.

    A no-op if the claim has already been taken over or cleared by someone else — there is
    nothing left for this attempt to fail on the row's behalf.
    """
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)

        lead = await selectors.get_lead_for_update(session, ctx.organization_id, lead_id)
        if lead is None or lead.conversion_claim_id != claim_id:
            return

        lead.conversion_claim_id = None
        lead.conversion_claimed_at = None
        lead.updated_by = str(ctx.principal.sub)
        await session.flush()

        record_audit(
            session,
            AuditLog,
            organization_id=ctx.organization_id,
            actor_id=ctx.principal.sub,
            action="lead.conversion_failed",
            entity="lead",
            entity_id=lead.id,
            after={"reason": reason},
            request=request,
        )


async def convert_lead(
    session: AsyncSession,
    ctx: ScopeContext,
    lead_id: uuid.UUID,
    *,
    request: Request | None = None,
) -> tuple[uuid.UUID, uuid.UUID | None, uuid.UUID]:
    """Convert a qualified lead into a company (+ contact) and a deal — claim, call, record.

    Three steps, three transactions (`claim_conversion`, then customer's and deal's
    `/internal/v1` with **no transaction open**, then `finalize_conversion` or
    `fail_conversion`). Concurrent `:convert` calls on the *same* lead are serialized by the
    claim/lease pair, not by holding a row lock across the network calls — the earlier
    version of this function did exactly that (one transaction, the whole way through), and
    holding a lock — or worse, an entire request's transaction — across two upstream calls
    turned a slow customer-service or deal-service into a database problem for every other
    reader of this lead's row, and rolled back the sensitive-read audit row whenever either
    call failed after the PII had already gone out on the wire.

    Idempotent: a lead already `converted` short-circuits inside `claim_conversion`, before
    any claim is taken and before any network call — ADR-0017's guarantee that converting
    twice is one company, one contact (or none), one deal.
    """
    claim = await claim_conversion(session, ctx, lead_id, request=request)
    if claim.already_converted:
        return claim.company_id, claim.contact_id, claim.deal_id
    if claim.claim_id is None or claim.owner_id is None or claim.contact_id is None:
        # `claim_conversion` always sets all three on the non-`already_converted` branch — a
        # fault to investigate, not a validation error to hand back, if this is ever hit.
        # (`contact_id` here is the *deterministic* id `domain.conversion_ids` derived, always
        # present on a fresh claim regardless of whether the lead has an email — customer
        # decides whether to actually use it and may still answer with no contact at all.)
        raise RuntimeError(f"lead {claim.lead_id} was claimed but is missing claim state")
    claim_id = claim.claim_id
    owner_id = claim.owner_id
    deterministic_contact_id = claim.contact_id

    cfg = settings()
    company_name = claim.company or claim.full_name
    close_date = date.today() + timedelta(days=_DEFAULT_CLOSE_HORIZON_DAYS)

    try:
        company_id, contact_id = await clients.ensure_company_and_contact(
            cfg,
            ctx.organization_id,
            company_id=claim.company_id,
            contact_id=deterministic_contact_id,
            company_name=company_name,
            owner_id=owner_id,
            contact_full_name=claim.full_name,
            contact_email=claim.raw_email,
            contact_raw_phone=claim.raw_phone,
            actor_id=ctx.principal.sub,
        )
        deal_id = await clients.ensure_deal(
            cfg,
            ctx.organization_id,
            deal_id=claim.deal_id,
            company_id=company_id,
            contact_id=contact_id,
            owner_id=owner_id,
            name=f"{company_name} — {claim.full_name}",
            close_date=close_date.isoformat(),
            source=claim.source,
            actor_id=ctx.principal.sub,
        )
    except clients.OwnerUnknown as exc:
        await fail_conversion(
            session, ctx, lead_id=claim.lead_id, claim_id=claim_id, reason=str(exc)
        )
        raise ValidationProblem(
            "Assign an owner known to the CRM book before converting this lead"
        ) from exc
    except clients.ConversionRejected as exc:
        await fail_conversion(
            session, ctx, lead_id=claim.lead_id, claim_id=claim_id, reason=str(exc)
        )
        raise ValidationProblem(
            "The conversion could not be completed. Check the lead's details and try again."
        ) from exc
    except clients.ConversionUnavailable as exc:
        await fail_conversion(
            session, ctx, lead_id=claim.lead_id, claim_id=claim_id, reason=str(exc)
        )
        raise UpstreamUnavailableProblem(
            "The CRM is temporarily unable to complete this conversion. Try again shortly."
        ) from exc

    await finalize_conversion(
        session,
        ctx,
        lead_id=claim.lead_id,
        claim_id=claim_id,
        company_id=company_id,
        contact_id=contact_id,
        deal_id=deal_id,
        request=request,
    )
    return company_id, contact_id, deal_id
