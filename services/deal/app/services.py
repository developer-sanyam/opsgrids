"""Writes for the deal service.

Every function follows the platform shape: change the row, record the audit entry, stage
the event — **all inside one transaction**. `get_tenant_session` opens the transaction and
does not commit for the route, so a crash between the state change and the event cannot
leave one without the other, and a rolled-back write never leaves an audit row claiming it
happened. Events are staged into the outbox, never published inline (ADR-0000).

Activities are **append-only**: `log_activity` inserts, and there is deliberately no
update or delete — a database trigger backs that up so the timeline is a record, not a
draft. Logging against a deal also advances the deal's `last_activity_at`, which is the
staleness clock the Overview reads.

Stage moves go through `move_stage`, which enforces `domain.can_transition`: open deals
move freely and close either way; a closed deal is a record and moves nowhere.

These writers drive both the public write routes (`app/api/routes.py`) and the demo seed,
which goes through the same real, RLS-bound path (the finance-service precedent), so
every seeded row lands with an audit row and an outbox event exactly as the live product
produces them. Delete is soft: `deleted_at` is set, never removed — deleting a deal must
never cascade into its (append-only) activities.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors
from app.models import Activity, AuditLog, Deal, OutboxEvent, Pipeline, PipelineStage
from app.pipeline import (
    CLOSING_PROBABILITY,
    DEFAULT_PIPELINE,
    DEFAULT_PIPELINE_KEY,
    DEFAULT_PIPELINE_NAME,
    Outcome,
    PipelineSpec,
    StageSpec,
)
from app.schemas import DealPatch
from platform_core.audit import record_audit
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    NotFoundProblem,
    ValidationProblem,
)
from platform_core.events import actor_for, publish_event
from platform_core.models import utcnow

# Event names. `crm.*` because these are the product's own, not the platform's.
EVT_DEAL_CREATED = "crm.pipeline.deal.created"
EVT_DEAL_UPDATED = "crm.pipeline.deal.updated"
EVT_DEAL_DELETED = "crm.pipeline.deal.deleted"
EVT_DEAL_STAGE_CHANGED = "crm.pipeline.deal.stage_changed"
EVT_ACTIVITY_LOGGED = "crm.pipeline.activity.logged"

#: Where a deal's probability lands on entering a stage of the *default* pipeline when nothing
#: says otherwise — derived from `app.pipeline`, which is the one place the six stages are
#: written. An organization's own pipeline carries its own defaults (`StageSpec`).
STAGE_DEFAULT_PROBABILITY: dict[str, int] = {
    stage.key: stage.default_probability for stage in DEFAULT_PIPELINE.stages
}


# ------------------------------------------------------------------ the pipeline's locks


def _pipeline_lock_key(organization_id: uuid.UUID) -> str:
    return f"deal.pipeline:{organization_id}"


async def lock_pipeline_shared(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Hold the organization's pipeline **shared** until the transaction ends.

    Every write that binds a deal to a stage (`create_deal`, `move_stage`) takes this, so any
    number of them run together, but none runs while a stage-configuration write holds the
    lock exclusively — the move cannot land in a stage an archive is removing. While the
    default is virtual there is no pipeline row to lock, hence an advisory lock keyed on the
    organization (`hashtextextended` collapses the key to the `bigint` Postgres takes; a hash
    collision only ever costs two organizations a moment of needless waiting). A transaction
    that takes this must never take `lock_pipeline_exclusive` afterwards: a lock upgrade can
    deadlock."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key, 0))"),
        {"key": _pipeline_lock_key(organization_id)},
    )


async def lock_pipeline_exclusive(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Hold the organization's pipeline **exclusively** until the transaction ends: the lock
    every stage-configuration write takes first (materialising the default is the first of
    them, R2.5 adds the rest). It waits for every in-flight stage-binding write and holds back
    new ones."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": _pipeline_lock_key(organization_id)},
    )


async def materialise_default_pipeline(
    session: AsyncSession, organization_id: uuid.UUID, actor_id: uuid.UUID | None
) -> PipelineSpec:
    """Store the default pipeline's six stages for an organization that has none, and return
    the stored pipeline. Idempotent: it takes the exclusive lock, **re-reads**, and writes only
    if nothing is stored, so two concurrent first writes materialise once.

    No route calls this before R2.5, and a `GET` never does: materialising from a read would
    write for every organization that merely opened a screen. It writes one audit row
    (`pipeline.materialised`, ids and a count) and **no event** — nothing about the pipeline
    changed, and every consumer already knows the default."""
    await lock_pipeline_exclusive(session, organization_id)
    spec = await selectors.load_pipeline_spec(session, organization_id)
    if spec.materialised:
        return spec

    created_by = str(actor_id) if actor_id is not None else None
    pipeline = Pipeline(
        organization_id=organization_id,
        key=DEFAULT_PIPELINE_KEY,
        name=DEFAULT_PIPELINE_NAME,
        is_default=True,
        created_by=created_by,
    )
    session.add(pipeline)
    await session.flush()  # the stages' composite foreign key needs the pipeline row first
    session.add_all(
        PipelineStage(
            organization_id=organization_id,
            pipeline_id=pipeline.id,
            key=stage.key,
            label=stage.label,
            position=stage.position,
            outcome=stage.outcome,
            default_probability=stage.default_probability,
            tone=stage.tone,
            created_by=created_by,
        )
        for stage in DEFAULT_PIPELINE.stages
    )
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=actor_id,
        action="pipeline.materialised",
        entity="pipeline",
        entity_id=pipeline.id,
        after={"pipeline_id": str(pipeline.id), "stages": len(DEFAULT_PIPELINE.stages)},
    )
    return await selectors.load_pipeline_spec(session, organization_id)


# ------------------------------------------------------------------ event payloads
#
# One builder per event, so each payload is built in one place and a producer-side test can
# hold it to its committed schema (`libs/contracts/events/crm.pipeline.deal.*`). Payloads carry
# ids, keys and enumerations — no free text beyond the deal's `name` and the one stage label
# (`to_label`) a stage-changed event names. `stage`, `from` and `to` are stage **keys** and never
# change meaning; a consumer tests the `*_outcome` field.


def _deal_state(deal: Deal) -> dict[str, Any]:
    """The fields all three deal events carry, from the row as it stands after the write."""
    return {
        "organization_id": str(deal.organization_id),
        "deal_id": str(deal.id),
        "name": deal.name,
        "value_minor": deal.value_minor,
        "currency": deal.currency,
        "owner_id": str(deal.owner_id),
        "probability": deal.probability,
        "closed_at": deal.closed_at.isoformat() if deal.closed_at is not None else None,
        # SP4 EV6-EV9: let an automation address the customer. The legacy `company_id` stays.
        "crm_company_id": str(deal.company_id),
        "crm_contact_id": (
            str(deal.primary_contact_id) if deal.primary_contact_id is not None else None
        ),
    }


def _created_payload(deal: Deal, stage_outcome: Outcome) -> dict[str, Any]:
    return {
        **_deal_state(deal),
        "stage": deal.stage,
        "stage_outcome": stage_outcome,
        "company_id": str(deal.company_id),
        "close_date": deal.close_date.isoformat(),
        "created_at": deal.created_at.isoformat(),
    }


def _updated_payload(deal: Deal, stage_outcome: Outcome, changed: list[str]) -> dict[str, Any]:
    """The full state after the patch, so a consumer converges without reading deal back. A
    stage move is `stage_changed`, never this event: the patch cannot touch `stage`."""
    return {
        **_deal_state(deal),
        "changed": changed,
        "stage": deal.stage,
        "stage_outcome": stage_outcome,
        "company_id": str(deal.company_id),
        "close_date": deal.close_date.isoformat(),
    }


def _stage_changed_payload(
    deal: Deal, *, before: str, from_outcome: Outcome, target: StageSpec
) -> dict[str, Any]:
    return {
        **_deal_state(deal),
        "from": before,
        "to": target.key,
        "from_outcome": from_outcome,
        "to_outcome": target.outcome,
        "to_label": target.label,
    }


#: The permission `:convert`'s internal call runs under — only to give the write path a
#: `ScopeContext`, not to gate anything: `require_service_token` on the internal route already
#: gated the call, the same shape customer-service's `lead_conversion_ctx` takes.
_LEAD_CONVERSION_PERMISSION = "crm.pipeline.deal.write"


def lead_conversion_ctx(organization_id: uuid.UUID, actor_id: uuid.UUID | None) -> ScopeContext:
    """The scope context `:convert`'s internal call runs under. ORG scope because there is no
    caller-owned row to narrow a brand-new deal to.

    `actor_id` is the human who asked lead-service to convert, carried in the service token's
    `sub` claim (`app/api/internal.py` checks the token names this organization before this is
    ever called) — the same shape customer-service's own `lead_conversion_ctx` takes. Falls
    back to the stable all-zero id when the token carried none."""
    principal = Principal(
        sub=actor_id if actor_id is not None else uuid.UUID(int=0),
        organization_id=organization_id,
        membership_id=None,
        perms=[f"{_LEAD_CONVERSION_PERMISSION}:ORG"],
        entitlements=["crm"],
    )
    return ScopeContext(
        principal=principal, scope=Scope.ORG, permission=_LEAD_CONVERSION_PERMISSION
    )


def service_ctx(organization_id: uuid.UUID) -> ScopeContext:
    """The scope context deal's own consumers write under (the aborted-conversion cleanup).

    `sub` is the stable all-zero id, so the write is attributable to "the platform"; no
    membership and no subject, so nothing OWN-scoped can ever run under it. ORG scope: the
    event names the record directly. The permission only gives the write path a context."""
    principal = Principal(
        sub=uuid.UUID(int=0),
        organization_id=organization_id,
        membership_id=None,
        perms=[f"{_LEAD_CONVERSION_PERMISSION}:ORG"],
        entitlements=["crm"],
    )
    return ScopeContext(
        principal=principal, scope=Scope.ORG, permission=_LEAD_CONVERSION_PERMISSION
    )


#: `(action, acting_as)` of the audit row lead's conversion writes when it creates a deal: `lead` is
#: the calling service's name here, not a stage key.
LEAD_CREATED_ROW = ("deal.created", "lead")


async def is_pristine_lead_deal(
    session: AsyncSession, organization_id: uuid.UUID, deal_id: uuid.UUID
) -> bool:
    """True iff the deal was created by lead's conversion and nothing has happened to it since:
    its audit history is exactly one row, `deal.created` with `acting_as = 'lead'`, and no
    activity names it. An id with no rows is False (callers handle absent and soft-deleted).

    The one predicate the aborted-conversion handler and the references probe share, so deal
    never deletes a deal the probe counted and the probe never discounts a deal deal keeps."""
    rows = await selectors.audit_rows_for_deal(session, organization_id, deal_id)
    if rows != [LEAD_CREATED_ROW]:
        return False
    return await selectors.count_activities_for_deal(session, organization_id, deal_id) == 0


# ------------------------------------------------------------------ stage resolution


def _entry_stage(spec: PipelineSpec) -> StageSpec:
    """Where a new deal opens: the first active open stage by position."""
    entry = spec.entry_stage()
    if entry is None:
        raise BusinessRuleProblem(
            "This pipeline has no active open stage to open a deal in",
            errors=[
                {
                    "code": "no_entry_stage",
                    "message": "This pipeline has no active open stage to open a deal in",
                }
            ],
        )
    return entry


def _target_stage(spec: PipelineSpec, key: str) -> StageSpec:
    """The stage a deal is being put into, which must exist and be active. A deal may *sit* in
    an archived stage, but nothing is put into one. Both refusals are 422 field errors on
    `stage`, never a 409: the importer reads a 409 containing "already" as a skipped row, and
    lead's client reads every deal-side 409 as a name conflict."""
    stage = spec.get(key)
    if stage is None:
        message = f"{key!r} is not a stage of this pipeline"
        raise ValidationProblem(
            message, errors=[{"field": "stage", "code": "unknown_stage", "message": message}]
        )
    if stage.archived:
        message = f"The stage {key!r} has been archived"
        raise ValidationProblem(
            message, errors=[{"field": "stage", "code": "stage_archived", "message": message}]
        )
    return stage


# ------------------------------------------------------------------ deals

#: `Deal.name` is `String(200)`.
DEAL_NAME_MAX = 200


async def unique_deal_name(session: AsyncSession, organization_id: uuid.UUID, base: str) -> str:
    """The first free name among `base`, `base (2)`, `base (3)`, ... for this organization.

    Deal names are unique among live deals, and a lead's conversion names its deal
    "<Company> -- <Lead name>" -- which two leads of one person at one company can share. Rather
    than push an id fragment into every name (it used to read "... . 68d1da6a"), the clash is
    resolved here, where the names live, with one query for the whole family. The numbered
    candidate is cut from the base so it still fits the column. A concurrent writer picking the
    same free number loses on the unique index and gets the ordinary 409, which lead-service
    reports as "a deal with this name already exists".
    """
    if len(base) > DEAL_NAME_MAX:
        base = base[:DEAL_NAME_MAX]
    stem = base[: DEAL_NAME_MAX - 12]
    taken = await selectors.deal_names_starting_with(session, organization_id, stem)
    if base not in taken:
        return base
    number = 2
    while True:
        suffix = f" ({number})"
        candidate = base[: DEAL_NAME_MAX - len(suffix)].rstrip() + suffix
        if candidate not in taken:
            return candidate
        number += 1


async def create_deal(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    name: str,
    company_id: uuid.UUID,
    owner_id: uuid.UUID,
    value_minor: int,
    close_date: date,
    currency: str = "INR",
    stage: str | None = None,
    probability: int | None = None,
    primary_contact_id: uuid.UUID | None = None,
    source: str = "",
    last_activity_at: datetime | None = None,
    deal_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
    request: Request | None = None,
    acting_as: str | None = None,
) -> Deal:
    """The core deal writer. Inserts the deal, records the audit row and stages the
    `created` event — all in the caller's transaction. `value_minor` is integer minor
    units of `currency`; no float is accepted anywhere on this path.

    `stage` is a key of the organization's pipeline; omitted, it is the pipeline's entry stage
    (the first active open stage by position). The write holds the pipeline's shared lock and
    reads the stages in the same transaction, so the stage it names cannot be archived under
    it. A deal created directly in a won or lost stage (an import, a seed) is closed on
    creation: `closed_at` is its `created_at`.

    `acting_as` names the calling *service* on the audit row when this write was not a
    person clicking a button but a service holding a token addressed to deal by name
    (`lead_conversion_ctx`'s caller) — `ctx.principal.sub` still carries the human who
    ultimately asked for it, when the caller has one to give."""
    await lock_pipeline_shared(session, ctx.organization_id)
    spec = await selectors.load_pipeline_spec(session, ctx.organization_id)
    target = _entry_stage(spec) if stage is None else _target_stage(spec, stage)
    if value_minor < 0:
        raise ConflictProblem("A deal's value cannot be negative")
    if await selectors.get_deal_by_name(session, ctx.organization_id, name):
        raise ConflictProblem("A deal with that name already exists")

    deal = Deal(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        name=name,
        company_id=company_id,
        primary_contact_id=primary_contact_id,
        value_minor=value_minor,
        currency=currency.upper(),
        stage=target.key,
        owner_id=owner_id,
        probability=probability if probability is not None else target.default_probability,
        close_date=close_date,
        source=source,
        last_activity_at=last_activity_at,
        closed_at=(created_at or utcnow()) if target.outcome != "open" else None,
    )
    if deal_id is not None:
        deal.id = deal_id
    if created_at is not None:
        deal.created_at = created_at
        deal.updated_at = created_at
    session.add(deal)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        acting_as=acting_as,
        action="deal.created",
        entity="deal",
        entity_id=deal.id,
        after={
            "name": name,
            "stage": target.key,
            "value_minor": value_minor,
            "currency": deal.currency,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DEAL_CREATED,
        aggregate_type="deal",
        aggregate_id=deal.id,
        payload=_created_payload(deal, target.outcome),
        actor=actor_for(ctx),
    )
    return deal


async def update_deal(
    session: AsyncSession,
    ctx: ScopeContext,
    deal: Deal,
    payload: DealPatch,
    *,
    request: Request | None = None,
) -> Deal:
    """Patch the plain deal fields. Deliberately never `stage` — `DealPatch` has no such
    field, so the transition rule in `move_stage` cannot be bypassed by a patch.
    `company_id` / `primary_contact_id` are cross-schema plain UUIDs and are not verified
    here for the same reason `create_deal` does not verify them. `owner_id` is different:
    it is the OWN-scope column, so re-checking the *current* owner on the route and then
    applying a *new* one unchecked would let an own-scoped rep hand their own deal to
    anyone — `ctx.assert_can_touch` re-applies the caller's scope to the incoming value
    exactly as `create_deal`'s route does, so an OWN-scoped caller may only reassign to
    themselves."""
    changes = payload.model_dump(exclude_unset=True)

    if "owner_id" in changes:
        ctx.assert_can_touch(owner_id=changes["owner_id"])

    if "name" in changes and changes["name"] != deal.name:
        existing = await selectors.get_deal_by_name(session, ctx.organization_id, changes["name"])
        if existing is not None and existing.id != deal.id:
            raise ConflictProblem(f"A deal named {changes['name']!r} already exists")

    if "value_minor" in changes and changes["value_minor"] < 0:
        raise ConflictProblem("A deal's value cannot be negative")

    if "currency" in changes:
        changes["currency"] = changes["currency"].upper()

    spec = await selectors.load_pipeline_spec(session, ctx.organization_id)
    before = {field: getattr(deal, field) for field in changes}
    for field, value in changes.items():
        setattr(deal, field, value)
    deal.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="deal.updated",
        entity="deal",
        entity_id=deal.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DEAL_UPDATED,
        aggregate_type="deal",
        aggregate_id=deal.id,
        payload=_updated_payload(deal, spec.outcome_of(deal.stage), sorted(changes)),
        actor=actor_for(ctx),
    )
    return deal


async def delete_deal(
    session: AsyncSession,
    ctx: ScopeContext,
    deal: Deal,
    *,
    request: Request | None = None,
    acting_as: str | None = None,
    reason: str | None = None,
) -> None:
    """Soft-delete a deal. Never a SQL DELETE: `activities.deal_id` is a real FK
    `ON DELETE CASCADE`, and a hard delete would cascade into the append-only timeline and
    hit its trigger. A soft delete leaves the activities exactly where they are.

    `acting_as` and `reason` land on the audit row when a consumer, not a person, deletes
    (the aborted-conversion cleanup names itself and the lead and generation it acted on)."""
    before_deleted_at = deal.deleted_at
    deal.deleted_at = utcnow()
    deal.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="deal.deleted",
        entity="deal",
        entity_id=deal.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": deal.deleted_at},
        reason=reason,
        acting_as=acting_as,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DEAL_DELETED,
        aggregate_type="deal",
        aggregate_id=deal.id,
        payload={"organization_id": str(ctx.organization_id), "deal_id": str(deal.id)},
        actor=actor_for(ctx),
    )


async def move_stage(
    session: AsyncSession,
    ctx: ScopeContext,
    deal: Deal,
    *,
    new_stage: str,
    probability: int | None = None,
    request: Request | None = None,
) -> Deal:
    """Move a deal to another stage, under the transition rule in `domain.can_transition`:
    open deals move freely between open stages and close either way; a closed deal is a
    record and moves nowhere.

    The target must be an active stage of the organization's pipeline (422 `unknown_stage`,
    422 `stage_archived`), and the move holds the pipeline's shared lock, so an archive cannot
    remove it mid-move. A move into a stage whose outcome is `won` or `lost` pins the
    probability — 100 and 0 — and a supplied figure that differs is a 422 `probability_pinned`
    (storing a won deal at 37 percent would corrupt every rollup); omitted, the pin applies. A
    move into an open stage takes `probability`, or the stage's default. The first move into a
    closing stage also sets `closed_at`, once, and nothing ever clears it."""
    await lock_pipeline_shared(session, ctx.organization_id)
    spec = await selectors.load_pipeline_spec(session, ctx.organization_id)
    target = _target_stage(spec, new_stage)
    if not domain.can_transition(deal.stage, new_stage, spec):
        raise ConflictProblem(f"A deal in {deal.stage!r} cannot move to {new_stage!r}")
    if target.outcome == "open":
        resolved_probability = (
            probability if probability is not None else target.default_probability
        )
    else:
        pin = CLOSING_PROBABILITY[target.outcome]
        if probability is not None and probability != pin:
            message = f"A deal moved to a {target.outcome} stage is pinned at {pin} percent"
            raise ValidationProblem(
                message,
                errors=[{"field": "probability", "code": "probability_pinned", "message": message}],
            )
        resolved_probability = pin

    before = deal.stage
    from_outcome = spec.outcome_of(before)
    deal.stage = new_stage
    deal.probability = resolved_probability
    if target.outcome != "open" and deal.closed_at is None:
        deal.closed_at = utcnow()
    deal.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="deal.stage_changed",
        entity="deal",
        entity_id=deal.id,
        before={"stage": before},
        after={"stage": new_stage, "probability": deal.probability},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DEAL_STAGE_CHANGED,
        aggregate_type="deal",
        aggregate_id=deal.id,
        payload=_stage_changed_payload(
            deal, before=before, from_outcome=from_outcome, target=target
        ),
        actor=actor_for(ctx),
    )
    return deal


# ------------------------------------------------------------------ activities


async def log_activity(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    activity_type: str,
    subject: str,
    owner_id: uuid.UUID,
    body: str | None = None,
    deal_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    direction: str | None = None,
    occurred_at: datetime | None = None,
    activity_id: uuid.UUID | None = None,
    request: Request | None = None,
) -> Activity:
    """Append one entry to the timeline. Append-only: this inserts and never updates, and
    a database trigger refuses UPDATE/DELETE on the table so that is a guarantee.

    A `deal_id` is validated within the organization — the FK would catch a bad id, but a
    cross-tenant guess must read as not-found, not as a constraint error — and the deal's
    `last_activity_at` staleness clock advances (monotonically) to `occurred_at`.
    """
    at = occurred_at or datetime.now(UTC)
    if at.tzinfo is None:
        # `ActivityIn` already reads an offset-less value as UTC for its future check
        # (`schemas.py`). Keep reading it that way here: a naive instant cannot be compared
        # with the deal's aware `last_activity_at` below, and it would reach the event with no
        # offset, where a consumer comparing it with its own aware clock would fail.
        at = at.replace(tzinfo=UTC)

    deal: Deal | None = None
    if deal_id is not None:
        deal = await selectors.get_deal(session, ctx.organization_id, deal_id)
        if deal is None:
            raise NotFoundProblem("That deal is not part of this organization")

    activity = Activity(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        activity_type=activity_type,
        subject=subject,
        body=body,
        deal_id=deal.id if deal else None,
        company_id=company_id,
        contact_id=contact_id,
        owner_id=owner_id,
        occurred_at=at,
        direction=direction,
    )
    if activity_id is not None:
        activity.id = activity_id
    session.add(activity)

    if deal is not None and (deal.last_activity_at is None or at > deal.last_activity_at):
        deal.last_activity_at = at
        deal.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="activity.logged",
        entity="activity",
        entity_id=activity.id,
        after={
            "type": activity_type,
            "subject": subject,
            "deal_id": str(deal_id) if deal_id else None,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ACTIVITY_LOGGED,
        aggregate_type="activity",
        aggregate_id=activity.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "activity_id": str(activity.id),
            "type": activity_type,
            "deal_id": str(deal_id) if deal_id else None,
            "contact_id": str(contact_id) if contact_id else None,
            "occurred_at": at.isoformat(),
        },
        actor=actor_for(ctx),
    )
    return activity
