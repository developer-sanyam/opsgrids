"""Writes for console — folding the transition stream into projections, and recording overrides.

Two kinds of write live here, and both obey the same tenancy rule as everywhere else on the
platform.

**The folds** are called by the consumer, one event at a time, inside a transaction the consumer
has already bound to the event's organization (`session_scope(organization_id=...)`, ADR-0002 §2).
So an org-A event can only ever write org-A's projection row — the RLS `WITH CHECK` on each
projection binds exactly as it would for a customer's own write, and a fold that somehow named
another organization would be refused rather than silently landing. The folds do not open or commit
their own transaction; the consumer owns it, and stages `mark_processed` in the same one so a
redelivered event is a no-op (`consumers.py`).

**Recording an override** is the "bypass for the lookup only, then narrow" pattern (ADR-0002,
ADR-0003 §2): the endpoint resolves the target and delegates the *write* to the owning service, then
records console's own audit row bound to the **target** organization — a real tenant write under the
app role with the GUC set to that org, never a cross-schema reach.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.models import AuditLog, OrgCommercialState, RevenueEvent, UsageSummary
from app.price import PriceResolver
from platform_core.audit import record_audit
from platform_core.db import set_tenant


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp from an event envelope; None passes through."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _occurred_at(envelope: dict[str, Any]) -> datetime:
    return _parse_dt(envelope.get("occurred_at")) or datetime.now(UTC)


async def _get_or_create_state(
    session: AsyncSession, organization_id: uuid.UUID
) -> OrgCommercialState:
    """The org's snapshot row, created empty on first sight.

    The read runs under the tenant GUC the consumer bound, so it returns this org's row and no
    other; the insert's `WITH CHECK` binds to the same org.
    """
    existing = (
        await session.execute(
            select(OrgCommercialState).where(OrgCommercialState.organization_id == organization_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = OrgCommercialState(
        organization_id=organization_id,
        mrr_minor=0,
        active_products=[],
        subscription_states={},
        locked=False,
    )
    session.add(row)
    await session.flush()
    return row


async def _fold_subscription_state(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    product_key: str,
    plan_version_id: str | None,
    to_state: str,
    trial_ends_at: str | None,
    amount_minor: int,
    currency: str | None,
    occurred_at: str | None,
) -> tuple[bool, str | None]:
    """Apply one subscription transition to the org snapshot alone — the arithmetic shared by
    the live event fold (`fold_subscription_event`, below, which also appends the revenue-log
    row for the transition it just observed) and the backfill's snapshot fold
    (`backfill_subscription_snapshot`, which does not: a backfill is establishing a baseline,
    not observing a discrete transition — see that function's own docstring for why appending
    one there would break re-run idempotency)."""
    state_row = await _get_or_create_state(session, organization_id)
    states = dict(state_row.subscription_states)
    applied, from_state = domain.apply_subscription_transition(
        states,
        product_key=product_key,
        to_state=to_state,
        plan_version_id=plan_version_id,
        amount_minor=amount_minor,
        currency=currency,
        trial_ends_at=trial_ends_at,
        occurred_at=occurred_at,
    )
    if applied:
        snapshot = domain.recompute_snapshot(states)

        # Reassign (not mutate) so SQLAlchemy sees the JSONB change.
        state_row.subscription_states = states
        state_row.mrr_minor = snapshot["mrr_minor"]
        state_row.currency = snapshot["currency"]
        state_row.active_products = snapshot["active_products"]
        state_row.locked = snapshot["locked"]
        state_row.trial_ends_at = _parse_dt(snapshot["trial_ends_at"])
    return applied, from_state


async def fold_subscription_event(
    session: AsyncSession, envelope: dict[str, Any], *, resolve_price: PriceResolver
) -> None:
    """Fold one `commerce.subscription.*` transition into the org snapshot and the revenue log.

    Payload (documented, from subscription-service; not imported): ``organization_id``,
    ``product_key``, ``plan_version_id``, ``state``, ``trial_ends_at``. The pinned price the state
    is worth is resolved from catalog (`price.resolve_plan_price`); MRR is the running sum of the
    org's active subscriptions at that price (ADR-0004 §4).
    """
    payload = envelope["payload"]
    organization_id = uuid.UUID(str(payload["organization_id"]))
    product_key = str(payload["product_key"])
    plan_version_id = payload.get("plan_version_id")
    to_state = str(payload["state"])
    trial_ends_at = payload.get("trial_ends_at")

    # Only resolve a price for a state that could pay or entitle — no point calling catalog to
    # price a cancellation. `(0, None)` when unresolved, which is the producer-not-merged path.
    amount_minor, currency = 0, None
    if plan_version_id and to_state in domain.ENTITLED_STATES:
        amount_minor, currency = await resolve_price(str(plan_version_id))

    applied, from_state = await _fold_subscription_state(
        session,
        organization_id,
        product_key=product_key,
        plan_version_id=str(plan_version_id) if plan_version_id else None,
        to_state=to_state,
        trial_ends_at=trial_ends_at,
        amount_minor=amount_minor,
        currency=currency,
        occurred_at=envelope.get("occurred_at"),
    )

    session.add(
        RevenueEvent(
            organization_id=organization_id,
            kind=str(envelope["event_type"]),
            amount_minor=amount_minor if applied and to_state in domain.MRR_STATES else 0,
            currency=currency,
            occurred_at=_occurred_at(envelope),
            meta={
                # A stale transition (out-of-order delivery, DLQ replay) is recorded — the
                # revenue log is the record of what arrived — but folds nothing, and is
                # marked so churn/conversion reads can exclude it.
                "from_state": from_state,
                "to_state": to_state,
                "product_key": product_key,
                "plan_version_id": str(plan_version_id) if plan_version_id else None,
                "stale": not applied,
            },
        )
    )


async def fold_org_event(session: AsyncSession, envelope: dict[str, Any]) -> None:
    """Fold `core.org.{created,updated}` into the org snapshot's `name`/`slug`/`country`/
    `org_created_at`.

    Payload (documented, from account-service; not imported — see `domain.ORG_EVENTS`):
    `core.org.created` carries `organization_id`, `slug`, `name`, `country`, `created_at`;
    `core.org.updated` carries `organization_id`, `changed`, and `name`/`country` when each is
    `"in changed"` (`null` otherwise — `slug` and `created_at` are immutable once set, so neither
    ever appears on an update). Ordering follows `domain.apply_org_identity`: the event's own
    `occurred_at` must advance the projection's stored `identity_synced_at`, so a redelivered
    create arriving after a later rename cannot resurrect the old name.
    """
    payload = envelope["payload"]
    organization_id = uuid.UUID(str(payload["organization_id"]))
    state_row = await _get_or_create_state(session, organization_id)
    applied, name, slug, country, org_created_at = domain.apply_org_identity(
        current_name=state_row.name,
        current_slug=state_row.slug,
        current_country=state_row.country,
        current_created_at=(
            state_row.org_created_at.isoformat() if state_row.org_created_at else None
        ),
        current_synced_at=(
            state_row.identity_synced_at.isoformat() if state_row.identity_synced_at else None
        ),
        name=payload.get("name"),
        slug=payload.get("slug"),
        country=payload.get("country"),
        created_at=payload.get("created_at"),
        occurred_at=envelope.get("occurred_at"),
    )
    created_dt = _parse_dt(org_created_at)
    if applied:
        state_row.name = name
        state_row.slug = slug
        state_row.country = country
        state_row.org_created_at = created_dt
        state_row.identity_synced_at = _occurred_at(envelope)
    elif country != state_row.country or created_dt != state_row.org_created_at:
        # A stale replay never overwrites, but it does fill a gap (`domain.apply_org_identity`):
        # `identity_synced_at` and the name/slug stay exactly as they were.
        state_row.country = country
        state_row.org_created_at = created_dt


async def fold_invoice_event(session: AsyncSession, envelope: dict[str, Any]) -> None:
    """Fold `commerce.invoice.{finalized,voided}` into the revenue log.

    Payload (documented, from billing-service): ``organization_id``, invoice totals, period. A void
    is recorded as the negative of its total, so the running revenue reads correctly without
    rewriting the finalize row (append-only, ADR-0000 §9).
    """
    payload = envelope["payload"]
    organization_id = uuid.UUID(str(payload.get("organization_id") or payload["org_id"]))
    kind = str(envelope["event_type"])
    total = int(payload.get("total_minor", 0))
    if kind.endswith("voided"):
        total = -total
    session.add(
        RevenueEvent(
            organization_id=organization_id,
            kind=kind,
            amount_minor=total,
            currency=payload.get("currency"),
            occurred_at=_occurred_at(envelope),
            meta={
                "invoice_number": payload.get("number"),
                "period_start": payload.get("period_start"),
                "period_end": payload.get("period_end"),
            },
        )
    )


async def fold_credit_event(session: AsyncSession, envelope: dict[str, Any]) -> None:
    """Fold `commerce.credit.{granted,consumed,reversed}` into the revenue log (comp/credit view).

    Payload (documented, from billing-service): ``organization_id``, ``amount_minor``, window. Sign
    is normalised by kind so a comp/credit report sums correctly: a grant is positive, a consumption
    or a reversal is negative (ADR-0004 §3, balance is a SUM over signed entries).
    """
    payload = envelope["payload"]
    organization_id = uuid.UUID(str(payload.get("organization_id") or payload["org_id"]))
    kind = str(envelope["event_type"])
    amount = abs(int(payload.get("amount_minor", 0)))
    if kind.endswith(("consumed", "reversed")):
        amount = -amount
    session.add(
        RevenueEvent(
            organization_id=organization_id,
            kind=kind,
            amount_minor=amount,
            currency=payload.get("currency"),
            occurred_at=_occurred_at(envelope),
            meta={
                "reason": payload.get("reason"),
                "valid_from": payload.get("valid_from"),
                "valid_until": payload.get("valid_until"),
            },
        )
    )


async def fold_usage_event(session: AsyncSession, envelope: dict[str, Any]) -> None:
    """Fold `commerce.usage.recorded` into the per-period usage rollup.

    Payload (documented, from metering-service): ``organization_id``, ``meter_key``, ``quantity``.
    Increments the `(org, meter_key, period)` bucket; the consumer's idempotency guard means a
    redelivered event does not double-count.
    """
    payload = envelope["payload"]
    organization_id = uuid.UUID(str(payload["organization_id"]))
    meter_key = str(payload["meter_key"])
    quantity = int(payload.get("quantity", 0))
    period = domain.period_bucket(_occurred_at(envelope))

    stmt = (
        pg_insert(UsageSummary)
        .values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            meter_key=meter_key,
            period=period,
            quantity=quantity,
        )
        .on_conflict_do_update(
            index_elements=["organization_id", "meter_key", "period"],
            set_={"quantity": UsageSummary.quantity + quantity},
        )
    )
    await session.execute(stmt)


# --------------------------------------------------------------------- backfill


async def backfill_org_identity(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    name: str,
    slug: str,
    source_updated_at: datetime,
    country: str | None = None,
    org_created_at: datetime | None = None,
) -> bool:
    """The backfill's identity fold — `app/backfill.py`'s discovery-time counterpart to
    `fold_org_event`'s live-event one, sharing the same ordering guard
    (`domain.apply_org_identity`) so a real `core.org.updated` that lands after this snapshot was
    taken always wins, and re-running this against an unchanged organization is a no-op: the
    second run's `source_updated_at` is the same instant already stored in
    `identity_synced_at`, which the guard reads as not advancing. `country` and `org_created_at`
    are optional so this stays callable exactly as before for a caller that only has name/slug —
    account's own directory row always has both, but nothing here requires it. Returns whether
    anything changed, for the backfill's summary — including a run that only fills a gap in
    `country`/`org_created_at` on a row synced before those columns existed (see below).
    """
    state_row = await _get_or_create_state(session, organization_id)
    applied, new_name, new_slug, new_country, new_created_at = domain.apply_org_identity(
        current_name=state_row.name,
        current_slug=state_row.slug,
        current_country=state_row.country,
        current_created_at=(
            state_row.org_created_at.isoformat() if state_row.org_created_at else None
        ),
        current_synced_at=(
            state_row.identity_synced_at.isoformat() if state_row.identity_synced_at else None
        ),
        name=name,
        slug=slug,
        country=country,
        created_at=org_created_at.isoformat() if org_created_at else None,
        occurred_at=source_updated_at.isoformat(),
    )
    created_dt = _parse_dt(new_created_at)
    if applied:
        state_row.name = new_name
        state_row.slug = new_slug
        state_row.country = new_country
        state_row.org_created_at = created_dt
        state_row.identity_synced_at = source_updated_at
        return True
    if new_country != state_row.country or created_dt != state_row.org_created_at:
        # Re-run after `country`/`org_created_at` were added to the projection: this run's
        # `source_updated_at` ties the one already stored, so the ordering guard calls it stale
        # and `applied` is False — but a stale source may still fill a gap, and that is exactly
        # what makes the re-run useful. Name/slug and `identity_synced_at` are left alone.
        state_row.country = new_country
        state_row.org_created_at = created_dt
        return True
    return False


async def backfill_subscription_snapshot(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    product_key: str,
    plan_version_id: str | None,
    state: str,
    trial_ends_at: str | None,
    amount_minor: int,
    currency: str | None,
    source_updated_at: datetime,
) -> bool:
    """The backfill's per-subscription fold: the same ordering guard and snapshot arithmetic as
    the live event fold (`_fold_subscription_state`), stamped with **the subscription's own
    `updated_at`** rather than an event's `occurred_at`.

    Deliberately does not append to `revenue_events` the way `fold_subscription_event` does: a
    backfill snapshot is not a transition it observed, it is the current state as of discovery,
    and appending a synthetic row for it — every run, since there is no processed-event ledger
    entry to make a repeat a no-op the way a real redelivered event gets — would grow the
    append-only revenue log by one meaningless row per organization every time this script runs.
    The MRR/entitlement/lock snapshot this writes is real and immediate; the transition history
    for a subscription this old starts at its next genuine event, same as subscription-service's
    own `app/backfill.py` does not fabricate history it cannot resolve.
    """
    applied, _from_state = await _fold_subscription_state(
        session,
        organization_id,
        product_key=product_key,
        plan_version_id=plan_version_id,
        to_state=state,
        trial_ends_at=trial_ends_at,
        amount_minor=amount_minor,
        currency=currency,
        occurred_at=source_updated_at.isoformat(),
    )
    return applied


async def record_override(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    operator_label: str,
    action: str,
    entity: str,
    entity_id: uuid.UUID | None,
    reason: str | None,
    after: dict[str, Any] | None,
    request: Request | None,
) -> None:
    """Record console's own audit row for an override, bound to the **target** organization.

    Opens a tenant transaction bound to the target org and stages one `audit_logs` row — a real
    tenant write under the app role, the "then narrow" half of ADR-0002's bypass-then-narrow. The
    delegated write into the owning service has already happened; this is console's local trace of
    who did it, and it is the only thing console writes for an override. `acting_as` names the
    operator, because an impersonated action with no named human is not an audit trail.
    """
    async with session.begin():
        await set_tenant(session, organization_id)
        record_audit(
            session,
            AuditLog,
            organization_id=organization_id,
            actor_id=actor_id,
            acting_as=operator_label,
            action=f"override.{action}",
            entity=entity,
            entity_id=entity_id,
            after=after,
            reason=reason,
            request=request,
        )


async def record_bespoke_price(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    operator_label: str,
    product_key: str,
    plan_version_id: uuid.UUID,
    amount_minor: int,
    currency: str,
    billing_interval: str,
    applied_to_subscription_id: uuid.UUID | None,
    effective: str,
    reason: str,
    idempotency_key: str | None,
    request_fingerprint: str,
    request: Request | None,
    apply_error: str | None = None,
) -> None:
    """Record a staff-set bespoke price, bound to the **target** organization — the "record" step
    of claim/call/record (docs/io-outside-transactions.md, `app/pricing.py`) for
    `POST .../pricing`. `catalog.bespoke_price.created` is always written;
    `subscription.migrated_to_bespoke` only when a live subscription was actually re-pinned.
    `after` on the first row carries the idempotency key and the request's own fingerprint, which
    is what makes a repeat of the same `Idempotency-Key` a lookup rather than a second call to
    catalog and subscription (`selectors.find_pricing_change_by_idempotency_key`).

    `apply_error` (fix round 1, MAJOR-4) is set only when the price was created but the re-pin
    was refused for a reason that is not the ordinary "no subscription"/"apply_to_subscription
    was false" shape — a billing-period claim in flight (`BillingCatchingUp`), so far. Recorded
    as `after["applied"] = False` and `after["reason"]` — the latter is this JSON blob's own key,
    not the audit row's `reason` *column* (which always holds the staff's own stated reason for
    the price, `reason` above, regardless of whether the re-pin applied).
    """
    async with session.begin():
        await set_tenant(session, organization_id)
        record_audit(
            session,
            AuditLog,
            organization_id=organization_id,
            actor_id=actor_id,
            acting_as=operator_label,
            action="catalog.bespoke_price.created",
            entity="plan_version",
            entity_id=plan_version_id,
            after={
                "product_key": product_key,
                "amount_minor": amount_minor,
                "currency": currency,
                "billing_interval": billing_interval,
                "applied_to_subscription_id": (
                    str(applied_to_subscription_id) if applied_to_subscription_id else None
                ),
                "applied": applied_to_subscription_id is not None,
                "effective": effective,
                "idempotency_key": idempotency_key,
                "request_fingerprint": request_fingerprint,
                **({"reason": apply_error} if apply_error else {}),
            },
            reason=reason,
            request=request,
        )
        if applied_to_subscription_id is not None:
            record_audit(
                session,
                AuditLog,
                organization_id=organization_id,
                actor_id=actor_id,
                acting_as=operator_label,
                action="subscription.migrated_to_bespoke",
                entity="subscription",
                entity_id=applied_to_subscription_id,
                after={
                    "product_key": product_key,
                    "plan_version_id": str(plan_version_id),
                    "effective": effective,
                },
                reason=reason,
                request=request,
            )


async def record_organization_read(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    operator_label: str,
    request: Request | None,
) -> None:
    """Record that an operator read one organization's detail, bound to that organization.

    Same bypass-then-narrow shape as `record_override`: the read itself ran on the directory
    session, cross-tenant; this is the "then narrow" half — a real tenant write under the app
    role, GUC bound to the organization the read was *about*, so the row lands where an
    administrator of that organization could in principle be shown it. `acting_as` names the
    operator, per ADR-0003's "every staff read of an org's detail writes an audit row".
    """
    async with session.begin():
        await set_tenant(session, organization_id)
        record_audit(
            session,
            AuditLog,
            organization_id=organization_id,
            actor_id=actor_id,
            acting_as=operator_label,
            action="organization.viewed",
            entity="organization",
            entity_id=organization_id,
            request=request,
        )
