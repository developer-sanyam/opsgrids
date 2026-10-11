"""Replaying parked webhook items after a fix. A one-off, operator-run recovery step.

`InboundDeadLetter` only ever holds a **permanent** failure — `services.is_permanent_fold_error`
is what decided that redelivering it would never reach a different outcome, so `app/api/
webhooks.py` parked it rather than asking Meta to retry forever. "Permanent" describes the item at
the moment it failed, not for all time: a column gets widened, a bug gets patched, an operator
fixes whatever made the value invalid — and once that happens, the row is exactly as re-playable
as the moment it arrived, because `raw_enc` sealed the provider's own object rather than
discarding it. This is the one place that reopens it.

Never automatic. A cron job that kept retrying a permanent error would just be Meta's own retry
loop moved into this service; replay only ever runs because an operator decided the underlying
cause is actually fixed, which is a judgement call this tool cannot make for them. Every attempt —
successful or not — writes its own `AuditLog` row, because this is data recovery running outside
any request a customer or Meta ever made, and "we tried again and it still failed" is itself worth
keeping. `--operator` is required and named on that row (`acting_as`) for the same reason
`console`'s own override audit names the human behind an impersonated write: a recovery action
with no named operator is not an audit trail, it is a log line.

Each row replays inside its **own** `session_scope` — not one shared transaction for the whole
batch. Postgres aborts a transaction outright on the first statement that raises inside it, so a
single shared transaction would have meant one bad row (an unparseable payload, a `PiiError` on a
corrupted `raw_enc`) poisoning every row queued after it, `AuditLog` writes for the good rows
included. Within one row's own transaction, the fold itself still runs under a `SAVEPOINT`
(`session.begin_nested()`), and the audit row is written *outside* it: a savepoint failure rolls
back only the fold's own work, so the audit insert that follows lands in a transaction that is
still healthy, and a row that fails to replay is audited exactly as reliably as one that succeeds.

A dedupe conflict on `inbound_messages` (`services.is_inbound_dedupe_conflict`) during a replay is
treated as success, the same as it is at the original webhook route: the message this row names
already exists — another replay of the same row, or the original webhook, won the race after this
row was parked — so there is nothing left to fold, only `processed_events` and the row's own
deletion to finish.

Usage, from `services/notification`:

    uv run python -m app.replay_dead_letters --org <organization-id> \\
        --operator <email-or-staff-id> [--id <dead-letter-id>]

Replays every parked row for the organization, or just the one row named by `--id`. A row that
replays successfully is deleted; one that fails again is left in place, its own audit row
recording the type of the failure it hit this time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import services, webhooks
from app.config import settings
from app.models import AuditLog, InboundDeadLetter, ProcessedEvent
from platform_core import pii
from platform_core.audit import record_audit
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.logging import get_logger

log = get_logger("notification.replay_dead_letters")

#: The `acting_as` prefix an operator-run recovery tool stamps its audit rows with — the same
#: `operator:<label>` shape `product/app/launch.py` uses for its own hand-run CLI, so an audit
#: reader can recognise "a human ran a recovery tool" the same way across services. `<label>` is
#: whatever `--operator` named: an email address or a staff id, never inferred from the
#: environment, because this tool has no request and no token to read an identity off of.
_OPERATOR_ACTING_AS_PREFIX: Final = "operator:"


def _acting_as(operator: str) -> str:
    return f"{_OPERATOR_ACTING_AS_PREFIX}{operator}"


async def _replay_row(session: AsyncSession, row: InboundDeadLetter) -> tuple[bool, str | None]:
    """Re-run one parked item's fold under its own `SAVEPOINT`, then delete it. Returns
    `(succeeded, error_type)` — `error_type` is `None` on success, and otherwise the failing
    exception's type name (or a fixed label for a row this tool cannot even parse), for the audit
    row the caller writes either way.

    Unsealing `raw_enc` runs **inside** this function's own handling, not before it: a corrupted
    ciphertext or a key rotated out from under a still-parked row raises `PiiError`, and that must
    be one failed row's own audited outcome, not an exception that unwinds out of the caller and
    takes every row queued after this one down with it.
    """
    try:
        raw = json.loads(pii.open_sealed(row.raw_enc, settings=settings()))

        # Each branch closes over its own already-narrowed, non-`None` parsed value rather than
        # re-testing `row.kind` inside the `SAVEPOINT` below — a plain string comparison is not a
        # discriminated union mypy can correlate across two separate `if`s, and re-checking it
        # would leave `inbound`/`update` typed as possibly `None` at the point they are used.
        if row.kind == "inbound":
            inbound = webhooks.inbound_from_raw(raw)
            if inbound is None:
                log.warning("replay.unparseable", dead_letter_id=str(row.id), kind=row.kind)
                return False, "unparseable"
            dedupe_id, event_type = inbound.dedupe_id, "whatsapp.message.inbound"

            async def _fold() -> None:
                await services.fold_inbound_message(
                    session, organization_id=row.organization_id, inbound=inbound
                )
        elif row.kind == "status":
            update = webhooks.status_from_raw(raw)
            if update is None:
                log.warning("replay.unparseable", dead_letter_id=str(row.id), kind=row.kind)
                return False, "unparseable"
            dedupe_id, event_type = update.dedupe_id, f"whatsapp.status.{update.status}"

            async def _fold() -> None:
                await services.fold_status(
                    session,
                    organization_id=row.organization_id,
                    provider_message_id=update.provider_message_id,
                    status=update.status,
                    occurred_at=update.occurred_at,
                    error_code=update.error_code,
                    error_detail=update.error_detail,
                    pricing_category=update.pricing_category,
                    billable=update.billable,
                )
        else:
            log.warning("replay.unknown_kind", dead_letter_id=str(row.id), kind=row.kind)
            return False, "unknown_kind"

        try:
            # The SAVEPOINT: a fold that raises must not abort the outer transaction this row's
            # `session_scope` owns, because the audit row below is written in that same
            # transaction and has to survive to be committed either way.
            async with session.begin_nested():
                await _fold()
                session.add(ProcessedEvent(event_id=dedupe_id, event_type=event_type))
                await session.delete(row)
            return True, None
        except Exception as exc:
            if not services.is_inbound_dedupe_conflict(exc):
                raise
            # The message this row names already exists — another replay of the same row, or the
            # original webhook, won the race after this row was parked. Nothing left to fold;
            # still success, cleared out under its own fresh savepoint since the first one rolled
            # back the moment the conflict raised.
            async with session.begin_nested():
                await session.execute(
                    pg_insert(ProcessedEvent)
                    .values(event_id=dedupe_id, event_type=event_type)
                    .on_conflict_do_nothing(index_elements=[ProcessedEvent.event_id])
                )
                await session.delete(row)
            return True, None
    except Exception as exc:
        log.error("replay.failed", dead_letter_id=str(row.id), error_type=type(exc).__name__)
        return False, type(exc).__name__


async def _replay_one(
    *, organization_id: uuid.UUID, dead_letter_id: uuid.UUID, operator: str
) -> bool:
    """Replay one row, entirely inside its own transaction, and audit the attempt regardless of
    outcome. A row already gone by the time this runs (replayed or removed concurrently) is not a
    failure — there is nothing left to replay or to audit a second time."""
    async with session_scope(organization_id=organization_id) as session:
        row = await session.get(InboundDeadLetter, dead_letter_id)
        if row is None:
            return False

        succeeded, error_type = await _replay_row(session, row)
        after: dict[str, object] = {"succeeded": succeeded, "kind": row.kind}
        if error_type is not None:
            after["error_type"] = error_type
        record_audit(
            session,
            AuditLog,
            organization_id=organization_id,
            actor_id=None,
            acting_as=_acting_as(operator),
            action="inbound_dead_letter.replayed",
            entity="inbound_dead_letter",
            entity_id=row.id,
            after=after,
        )
    return succeeded


async def replay(
    *, organization_id: uuid.UUID, operator: str, dead_letter_id: uuid.UUID | None = None
) -> int:
    """Replay every parked dead letter for one organization, or just `dead_letter_id`. Returns the
    number replayed successfully.

    `operator` is required and never defaulted — see the module docstring for why an unattributed
    recovery action is not acceptable here. Ids are loaded up front, in their own short
    transaction, and then each row is replayed through `_replay_one`'s own `session_scope`: a row
    that fails does not stop, or roll back, any other row's replay or audit.
    """
    if not operator or not operator.strip():
        raise ValueError("operator is required")

    async with session_scope(organization_id=organization_id) as session:
        stmt = select(InboundDeadLetter.id).where(
            InboundDeadLetter.organization_id == organization_id
        )
        if dead_letter_id is not None:
            stmt = stmt.where(InboundDeadLetter.id == dead_letter_id)
        ids = (await session.execute(stmt)).scalars().all()

    replayed = 0
    for row_id in ids:
        succeeded = await _replay_one(
            organization_id=organization_id, dead_letter_id=row_id, operator=operator
        )
        if succeeded:
            replayed += 1

    log.info(
        "replay.completed",
        organization_id=str(organization_id),
        attempted=len(ids),
        replayed=replayed,
    )
    return replayed


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay parked WhatsApp webhook items after a permanent-error fix."
    )
    parser.add_argument("--org", required=True, help="Organization id to replay dead letters for")
    parser.add_argument(
        "--operator",
        required=True,
        help="Who is running this replay — an email address or a staff id. Recorded on every "
        "audit row this writes.",
    )
    parser.add_argument("--id", required=False, help="Replay only this dead-letter row's id")
    args = parser.parse_args()

    operator = args.operator.strip()
    if not operator:
        parser.error("--operator must not be blank")

    cfg = settings()
    engine = init_engine(cfg)
    # The same guard every worker entrypoint runs: started on the admin or outbox DSN by mistake,
    # row-level security would be silently inert and this tool could read or rewrite a row under
    # the wrong organization's policy (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)

    organization_id = uuid.UUID(args.org)
    dead_letter_id = uuid.UUID(args.id) if args.id else None
    replayed = await replay(
        organization_id=organization_id, operator=operator, dead_letter_id=dead_letter_id
    )
    print(f"replayed {replayed} dead letter(s) for organization {organization_id}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
