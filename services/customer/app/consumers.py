"""Event consumers for customer. Runs as its own process (`bin/worker`), or folded into the API
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
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors, services
from app.clients import DealReferences, deal_references
from app.config import settings
from app.models import Company, Contact, ProcessedEvent
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("customer.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="customer.events")

#: Same shape as every service's `app.cron.WorkScope` — this service has no sweep of its own, so
#: it is defined here rather than imported.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


def _default_session_scope(organization_id: uuid.UUID) -> Any:
    return session_scope(organization_id=organization_id)


#: The transaction scope every handler below opens. Defaults to the process-wide engine — this
#: consumer's own connection pool when it runs standalone (`python -m app.consumers`,
#: `bin/worker`). `app/main.py` swaps it to a bulkhead scope (`platform_core.db.bulkhead_scope`)
#: before starting this consumer as a supervised background task inside the API process, on
#: `RUN_CONSUMERS_IN_API` — so a folded consumer's per-message transactions can neither take a
#: connection from request traffic nor be starved by it.
_session_scope: WorkScope = _default_session_scope


def use_session_scope(scope: WorkScope) -> None:
    """Point every handler above at `scope` instead of the default engine. Called once, before
    this consumer starts, by whichever entrypoint constructs it that way — never by a handler."""
    global _session_scope
    _session_scope = scope


async def contact_activity_logged(envelope: dict[str, Any]) -> None:
    """Advance a touched contact's `last_activity_at`.

    Bound to `crm.pipeline.activity.logged`, which deal-service publishes whenever a call,
    email, meeting, note or task is logged. Not every activity names a contact — the field
    is nullable on the timeline — so a payload with none is a no-op, not an error, and a
    contact that names another tenant's id (which cannot happen honestly, but a malformed
    or replayed message might try) resolves to nothing rather than touching a stranger's
    row, because the lookup is `organization_id`-scoped like every read here.
    """
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = uuid.UUID(payload["organization_id"])
    contact_id_raw = payload.get("contact_id")

    # The GUC is set here, per message, for the whole transaction: this process runs
    # NOBYPASSRLS, and the touch is a write against a forced tenant policy that only
    # passes because of this binding.
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("customer.activity_touch.duplicate", event_id=str(event_id))
            return

        if contact_id_raw is not None:
            contact = await selectors.get_contact(
                session, organization_id, uuid.UUID(contact_id_raw)
            )
            if contact is not None:
                at = datetime.fromisoformat(payload["occurred_at"])
                await services.touch_contact_activity(
                    session, services.service_ctx(organization_id), contact, at=at
                )

        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "customer.activity_touch.processed",
        event_id=str(event_id),
        organization_id=str(organization_id),
        contact_id=contact_id_raw,
    )


consumer.on("crm.pipeline.activity.logged")(contact_activity_logged)


# ------------------------------------------------------------------ crm.lead.conversion_aborted

#: Who the audit row of a cleanup delete names (`AuditLog.acting_as`); the owner's restore recipe
#: finds every cleanup by it (RUNBOOK, "CRM: aborted-conversion cleanup").
CLEANUP_ACTOR = "crm-conversion-cleanup"
_REASONS = frozenset({"status_changed", "owner_changed", "record_deleted"})


@dataclass(frozen=True)
class CleanupOutcome:
    """What the handler decided. `ignored` names why an event was not evaluated (`mode_off`,
    `no_generation`, `bad_generation`, `no_claim_id`, `bad_uuid`, `org_mismatch`,
    `unknown_reason`, `already_processed`). `contact` and `company` are `deleted` or
    `would_delete`, or why the record is kept: `absent`, `already_deleted`,
    `not_created_by_attempt`, `adopted` (a later conversion reused it), `touched`,
    `other_contacts` (the company still has a live contact), `referenced` (deal names it).
    `contact` is `None` when the lead carried no email."""

    mode: str
    ignored: str | None = None
    contact: str | None = None
    company: str | None = None


@dataclass(frozen=True)
class _Abort:
    event_id: uuid.UUID
    event_type: str
    organization_id: uuid.UUID
    lead_id: uuid.UUID
    generation: int
    company_id: uuid.UUID
    contact_id: uuid.UUID | None
    deal_id: uuid.UUID


def _parse(envelope: dict[str, Any]) -> _Abort | str:
    """The event with its ids re-derived from `(lead_id, generation)`, or the code it is ignored
    under. Customer's own copy of deal's rules (`import app` is ambiguous across services)."""
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
        for key in ("claim_id", "company_id", "deal_id"):
            uuid.UUID(str(payload[key]))
        has_contact = payload.get("contact_id") is not None
        if has_contact:
            uuid.UUID(str(payload["contact_id"]))
    except (KeyError, ValueError):
        return "bad_uuid"
    if str(envelope.get("organization_id")) != str(organization_id):
        return "org_mismatch"
    if payload.get("reason") not in _REASONS:
        return "unknown_reason"
    company_id, contact_id, deal_id = domain.conversion_ids(lead_id, generation)
    return _Abort(
        event_id=event_id,
        event_type=str(envelope.get("event_type")),
        organization_id=organization_id,
        lead_id=lead_id,
        generation=generation,
        company_id=company_id,
        contact_id=contact_id if has_contact else None,
        deal_id=deal_id,
    )


async def _provenance(
    session: AsyncSession, org: uuid.UUID, entity: str, row: Company | Contact | None
) -> str:
    """`deletable` as far as customer alone can tell, or why the record is kept."""
    if row is None:
        return "absent"
    if row.deleted_at is not None:
        return "already_deleted"
    rows = await selectors.audit_rows_for_entity(session, org, entity, row.id)
    if not rows or rows[0] != (f"{entity}.created", "lead"):
        return "not_created_by_attempt"
    if len(rows) > 1:
        # A later conversion's trace (the reuse marker or its phone row) is lead's; a person's
        # edit is not.
        return "adopted" if rows[1][1] == "lead" else "touched"
    if isinstance(row, Contact) and row.last_activity_at is not None:
        return "touched"
    return "deletable"


async def _decide(
    session: AsyncSession,
    abort: _Abort,
    company: Company | None,
    contact: Contact | None,
    refs: DealReferences | None,
) -> tuple[str | None, str]:
    """The contact's and the company's decision. `refs` is deal's answer, `None` before the probe:
    a record customer alone finds deletable stays `deletable` until the probe has answered."""
    org = abort.organization_id
    contact_code = None
    if abort.contact_id is not None:
        contact_code = await _provenance(session, org, "contact", contact)
        if (
            contact_code == "deletable"
            and refs is not None
            and (refs.contact_deal_count or refs.contact_activity_count)
        ):
            contact_code = "referenced"
    company_code = await _provenance(session, org, "company", company)
    if company_code == "deletable" and company is not None:
        # The derived contact counts as "another" contact unless it is being deleted too, and
        # `delete_company` refuses while any live contact remains.
        leaving = abort.contact_id if contact_code == "deletable" else None
        if await selectors.count_live_contacts_except(session, org, company.id, leaving):
            company_code = "other_contacts"
        elif refs is not None and (refs.company_deal_count or refs.company_activity_count):
            company_code = "referenced"
    return contact_code, company_code


def _shown(code: str | None) -> str | None:
    return "would_delete" if code == "deletable" else code


async def run_conversion_cleanup(envelope: dict[str, Any]) -> CleanupOutcome:
    """Soft-delete an aborted conversion attempt's orphan contact and company, when each provably
    is one (ADR-0038 D-2), in three phases so deal's probe runs with no transaction open:

    1. plan, in a read-only transaction: the derived records and every rule customer can check;
    2. probe deal (`clients.deal_references`) only if something looks deletable; a failure raises,
       so the message is redelivered and then dead-lettered rather than silently acknowledged;
    3. act (`on` only), in a new transaction: lock the company then the contact (the order a
       conversion reusing them locks in), re-verify every rule, delete the contact then the
       company, and record the event as processed whatever was decided.

    `log_only` re-reads the decision with deal's answer in a read-only transaction and writes
    nothing, not even the ledger row (one written during the soak would make a replay after the
    flip a no-op)."""
    mode = settings().crm_conversion_cleanup_mode
    if mode == "off":
        return CleanupOutcome(mode=mode, ignored="mode_off")
    abort = _parse(envelope)
    if isinstance(abort, str):
        log.warning(
            "crm.conversion_cleanup.ignored", event_id=str(envelope.get("event_id")), ignored=abort
        )
        return CleanupOutcome(mode=mode, ignored=abort)

    org = abort.organization_id
    ids: dict[str, Any] = {
        "event_id": str(abort.event_id),
        "organization_id": str(org),
        "lead_id": str(abort.lead_id),
        "generation": abort.generation,
        "company_id": str(abort.company_id),
        "contact_id": str(abort.contact_id) if abort.contact_id else None,
    }
    payload = envelope["payload"]
    if (payload["company_id"], payload["contact_id"]) != (ids["company_id"], ids["contact_id"]):
        log.info(
            "crm.conversion_cleanup.not_derived_id",
            payload_company_id=payload["company_id"],
            payload_contact_id=payload["contact_id"],
            **ids,
        )

    async def _read(session: AsyncSession, *, lock: bool) -> tuple[Company | None, Contact | None]:
        if lock:
            company = await selectors.get_company_for_update_any_state(
                session, org, abort.company_id
            )
        else:
            company = await selectors.get_company_by_id_any_state(session, org, abort.company_id)
        contact = None
        if abort.contact_id is not None:
            read_contact = (
                selectors.get_contact_for_update_any_state
                if lock
                else selectors.get_contact_by_id_any_state
            )
            contact = await read_contact(session, org, abort.contact_id)
        return company, contact

    async with _session_scope(org) as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        if await already_processed(session, ProcessedEvent, abort.event_id):
            return CleanupOutcome(mode=mode, ignored="already_processed")
        planned = await _decide(session, abort, *await _read(session, lock=False), None)

    refs = None
    if "deletable" in planned:
        refs = await deal_references(
            settings(),
            org,
            company_id=abort.company_id,
            contact_id=abort.contact_id,
            exclude_deal_id=abort.deal_id,
        )

    if mode == "log_only":
        async with _session_scope(org) as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            contact_code, company_code = await _decide(
                session, abort, *await _read(session, lock=False), refs
            )
        event = "would_apply" if "deletable" in (contact_code, company_code) else "would_keep"
        outcome = CleanupOutcome(
            mode=mode, contact=_shown(contact_code), company=_shown(company_code)
        )
        log.info(
            f"crm.conversion_cleanup.{event}",
            mode=mode,
            contact=outcome.contact,
            company=outcome.company,
            **ids,
        )
        return outcome

    async with _session_scope(org) as session:
        if await already_processed(session, ProcessedEvent, abort.event_id):
            return CleanupOutcome(mode=mode, ignored="already_processed")
        company, contact = await _read(session, lock=True)
        contact_code, company_code = await _decide(session, abort, company, contact, refs)
        # Nothing the probe did not see is deleted: a record that became deletable only after
        # the plan (another contact deleted meanwhile) keeps the plan's reason this time.
        if contact_code == "deletable" and planned[0] != "deletable":
            contact_code = planned[0]
        if company_code == "deletable" and planned[1] != "deletable":
            company_code = planned[1]
        reason = f"lead_conversion_aborted:{abort.lead_id}:g{abort.generation}"
        ctx = services.service_ctx(org)
        if contact_code == "deletable" and contact is not None:
            await services.delete_contact(
                session, ctx, contact, acting_as=CLEANUP_ACTOR, reason=reason
            )
            contact_code = "deleted"
        if company_code == "deletable" and company is not None:
            await services.delete_company(
                session, ctx, company, acting_as=CLEANUP_ACTOR, reason=reason
            )
            company_code = "deleted"
        mark_processed(session, ProcessedEvent, abort.event_id, abort.event_type)

    event = "applied" if "deleted" in (contact_code, company_code) else "kept"
    log.info(
        f"crm.conversion_cleanup.{event}",
        mode=mode,
        contact=contact_code,
        company=company_code,
        **ids,
    )
    return CleanupOutcome(mode=mode, contact=contact_code, company=company_code)


async def customer_conversion_aborted(envelope: dict[str, Any]) -> None:
    """The registered handler: the platform's `Handler` returns nothing, so the decision
    `run_conversion_cleanup` returns is for tests and logs only."""
    await run_conversion_cleanup(envelope)


consumer.on("crm.lead.conversion_aborted")(customer_conversion_aborted)


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
