"""Event consumers for deal. Runs as its own process (`bin/worker`), or folded into the API
process on `RUN_CONSUMERS_IN_API` (`app/main.py`).

Every handler is idempotent: `already_processed` before acting, `mark_processed` in the
same transaction as its side effects. Redelivery is normal, not exceptional.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors, services
from app.config import settings
from app.models import Deal, ProcessedEvent
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("deal.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="deal.events")

#: Same shape as every service's `WorkScope`: one tenant-bound transaction per call.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


def _default_session_scope(organization_id: uuid.UUID) -> Any:
    return session_scope(organization_id=organization_id)


#: The transaction scope every handler opens: the process-wide engine when this consumer runs
#: standalone, a bulkhead engine when `app/main.py` folds it into the API process, so a message's
#: transaction neither takes a connection from request traffic nor is starved by it.
_session_scope: WorkScope = _default_session_scope


def use_session_scope(scope: WorkScope) -> None:
    """Point every handler at `scope` instead of the default engine. Called once, before this
    consumer starts, by whichever entrypoint constructs it that way, never by a handler."""
    global _session_scope
    _session_scope = scope


# ------------------------------------------------------------------ crm.lead.conversion_aborted

#: Who the audit row of a cleanup delete names (`AuditLog.acting_as`); the owner's restore recipe
#: finds every cleanup by it (RUNBOOK, "CRM: aborted-conversion cleanup").
CLEANUP_ACTOR = "crm-conversion-cleanup"
_REASONS = frozenset({"status_changed", "owner_changed", "record_deleted"})


@dataclass(frozen=True)
class CleanupOutcome:
    """What the handler decided. `ignored` names why an event was not evaluated at all
    (`mode_off`, `no_generation`, `bad_generation`, `no_claim_id`, `bad_uuid`, `org_mismatch`,
    `unknown_reason`, `already_processed`); `deal` is the decision about the derived deal
    (`deleted`, `would_delete`, `would_keep`'s reasons `absent`, `already_deleted`,
    `not_created_by_attempt`, `touched`)."""

    mode: str
    ignored: str | None = None
    deal: str | None = None


@dataclass(frozen=True)
class _Abort:
    event_id: uuid.UUID
    organization_id: uuid.UUID
    lead_id: uuid.UUID
    generation: int
    payload_deal_id: uuid.UUID | None


def _parse(envelope: dict[str, Any]) -> _Abort | str:
    """The event, or the code it is ignored under. Only R2.1's payload is acted on: it carries
    the pre-bump `generation` the attempt's ids were derived under and the attempt's `claim_id`."""
    payload = envelope.get("payload") or {}
    if "generation" not in payload:
        return "no_generation"
    generation = payload["generation"]
    # `type(...) is int`: a JSON boolean is an `int` subclass in Python and must not pass.
    if type(generation) is not int or generation < 0:
        return "bad_generation"
    if not payload.get("claim_id"):
        return "no_claim_id"
    try:
        event_id = uuid.UUID(str(envelope["event_id"]))
        organization_id = uuid.UUID(str(payload["organization_id"]))
        lead_id = uuid.UUID(str(payload["lead_id"]))
        uuid.UUID(str(payload["claim_id"]))
        raw_deal = payload.get("deal_id")
        payload_deal_id = uuid.UUID(str(raw_deal)) if raw_deal else None
    except (KeyError, ValueError):
        return "bad_uuid"
    if str(envelope.get("organization_id")) != str(organization_id):
        return "org_mismatch"
    if payload.get("reason") not in _REASONS:
        return "unknown_reason"
    return _Abort(event_id, organization_id, lead_id, generation, payload_deal_id)


async def _decide(session: AsyncSession, organization_id: uuid.UUID, deal: Deal | None) -> str:
    """`deletable`, or why the derived deal is kept."""
    if deal is None:
        return "absent"
    if deal.deleted_at is not None:
        return "already_deleted"
    if await services.is_pristine_lead_deal(session, organization_id, deal.id):
        return "deletable"
    first = await selectors.audit_rows_for_deal(session, organization_id, deal.id, limit=1)
    return "touched" if first == [("deal.created", "lead")] else "not_created_by_attempt"


async def run_conversion_cleanup(envelope: dict[str, Any]) -> CleanupOutcome:
    """Soft-delete an aborted conversion attempt's orphan deal, when it provably is one.

    The deal id is re-derived from `(lead_id, generation)`; the payload's `deal_id` is logged when
    it differs and never acted on. Deletable means live and `is_pristine_lead_deal`: created by
    lead's conversion and untouched since. No `stage_changed` is ever staged (ADR-0038 D-1):
    closing the deal as lost would distort the win rate and fire tenant automations.

    `log_only` reads in a read-only transaction and writes nothing, not even the ledger row (a row
    written during the soak would turn a replay after the flip into a no-op). `on` decides under
    the row lock and records the event as processed whatever it decided."""
    mode = settings().crm_conversion_cleanup_mode
    if mode == "off":
        return CleanupOutcome(mode=mode, ignored="mode_off")
    parsed = _parse(envelope)
    if isinstance(parsed, str):
        log.warning(
            "crm.conversion_cleanup.ignored",
            event_id=str(envelope.get("event_id")),
            ignored=parsed,
        )
        return CleanupOutcome(mode=mode, ignored=parsed)

    org = parsed.organization_id
    deal_id = domain.conversion_ids(parsed.lead_id, parsed.generation)[2]
    ids = {
        "event_id": str(parsed.event_id),
        "organization_id": str(org),
        "lead_id": str(parsed.lead_id),
        "generation": parsed.generation,
        "deal_id": str(deal_id),
    }
    if parsed.payload_deal_id is not None and parsed.payload_deal_id != deal_id:
        log.info(
            "crm.conversion_cleanup.not_derived_id",
            payload_deal_id=str(parsed.payload_deal_id),
            **ids,
        )

    if mode == "log_only":
        async with _session_scope(org) as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            deal = await selectors.get_deal_by_id_any_state(session, org, deal_id)
            decision = await _decide(session, org, deal)
        code = "would_delete" if decision == "deletable" else decision
        event = "would_apply" if decision == "deletable" else "would_keep"
        log.info(f"crm.conversion_cleanup.{event}", mode=mode, deal=code, **ids)
        return CleanupOutcome(mode=mode, deal=code)

    async with _session_scope(org) as session:
        if await already_processed(session, ProcessedEvent, parsed.event_id):
            log.info("crm.conversion_cleanup.duplicate", **ids)
            return CleanupOutcome(mode=mode, ignored="already_processed")
        deal = await selectors.get_deal_for_update_any_state(session, org, deal_id)
        decision = await _decide(session, org, deal)
        if decision == "deletable" and deal is not None:
            await services.delete_deal(
                session,
                services.service_ctx(org),
                deal,
                acting_as=CLEANUP_ACTOR,
                reason=f"lead_conversion_aborted:{parsed.lead_id}:g{parsed.generation}",
            )
            decision = "deleted"
        mark_processed(session, ProcessedEvent, parsed.event_id, envelope["event_type"])

    event = "applied" if decision == "deleted" else "kept"
    log.info(f"crm.conversion_cleanup.{event}", mode=mode, deal=decision, **ids)
    return CleanupOutcome(mode=mode, deal=decision)


async def deal_conversion_aborted(envelope: dict[str, Any]) -> None:
    """The registered handler: the platform's `Handler` returns nothing, so the decision
    `run_conversion_cleanup` returns is for tests and logs only."""
    await run_conversion_cleanup(envelope)


consumer.on("crm.lead.conversion_aborted")(deal_conversion_aborted)


async def main() -> None:
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if
    # row-level security can bind this role. Started on the admin or outbox DSN by
    # mistake, RLS would be silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
