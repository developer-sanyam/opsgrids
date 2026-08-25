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

    state_row = await _get_or_create_state(session, organization_id)
    states = dict(state_row.subscription_states)
    applied, from_state = domain.apply_subscription_transition(
        states,
        product_key=product_key,
        to_state=to_state,
        plan_version_id=str(plan_version_id) if plan_version_id else None,
        amount_minor=amount_minor,
        currency=currency,
        trial_ends_at=trial_ends_at,
        occurred_at=envelope.get("occurred_at"),
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
