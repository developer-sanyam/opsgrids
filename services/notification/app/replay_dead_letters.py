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
keeping.

Usage, from `services/notification`:

    uv run python -m app.replay_dead_letters --org <organization-id> [--id <dead-letter-id>]

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
from sqlalchemy.ext.asyncio import AsyncSession

from app import services, webhooks
from app.config import settings
from app.models import AuditLog, InboundDeadLetter
from platform_core import pii
from platform_core.audit import record_audit
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.logging import get_logger

log = get_logger("notification.replay_dead_letters")

#: `acting_as` on the audit row this writes. An operator running this by hand, not a request or
#: a consumer, so the actor is the tool itself rather than a person the platform can name from a
#: token.
REPLAY_ACTOR: Final = "system:replay_dead_letters"


async def _replay_row(session: AsyncSession, row: InboundDeadLetter) -> bool:
    """Re-run one parked item's fold. Returns whether it succeeded — the caller audits and, on
    success, deletes the row either way, so a repeated failure is visible rather than silently
    re-attempted next time someone runs this."""
    raw = json.loads(pii.open_sealed(row.raw_enc, settings=settings()))
    try:
        if row.kind == "inbound":
            inbound = webhooks.inbound_from_raw(raw)
            if inbound is None:
                log.warning("replay.unparseable", dead_letter_id=str(row.id), kind=row.kind)
                return False
            await services.fold_inbound_message(
                session, organization_id=row.organization_id, inbound=inbound
            )
        elif row.kind == "status":
            update = webhooks.status_from_raw(raw)
            if update is None:
                log.warning("replay.unparseable", dead_letter_id=str(row.id), kind=row.kind)
                return False
            await services.fold_status(
                session,
                organization_id=row.organization_id,
                provider_message_id=update.provider_message_id,
                status=update.status,
                occurred_at=update.occurred_at,
                error_code=update.error_code,
                error_detail=update.error_detail,
            )
        else:
            log.warning("replay.unknown_kind", dead_letter_id=str(row.id), kind=row.kind)
            return False
    except Exception as exc:
        log.error("replay.failed", dead_letter_id=str(row.id), error_type=type(exc).__name__)
        return False
    return True


async def replay(*, organization_id: uuid.UUID, dead_letter_id: uuid.UUID | None = None) -> int:
    """Replay every parked dead letter for one organization, or just `dead_letter_id`. Returns
    the number replayed successfully."""
    replayed = 0
    async with session_scope(organization_id=organization_id) as session:
        stmt = select(InboundDeadLetter).where(InboundDeadLetter.organization_id == organization_id)
        if dead_letter_id is not None:
            stmt = stmt.where(InboundDeadLetter.id == dead_letter_id)
        rows = (await session.execute(stmt)).scalars().all()

        for row in rows:
            succeeded = await _replay_row(session, row)
            record_audit(
                session,
                AuditLog,
                organization_id=organization_id,
                actor_id=None,
                acting_as=REPLAY_ACTOR,
                action="inbound_dead_letter.replayed",
                entity="inbound_dead_letter",
                entity_id=row.id,
                after={"succeeded": succeeded, "kind": row.kind},
            )
            if succeeded:
                await session.delete(row)
                replayed += 1

    log.info(
        "replay.completed",
        organization_id=str(organization_id),
        attempted=len(rows),
        replayed=replayed,
    )
    return replayed


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay parked WhatsApp webhook items after a permanent-error fix."
    )
    parser.add_argument("--org", required=True, help="Organization id to replay dead letters for")
    parser.add_argument("--id", required=False, help="Replay only this dead-letter row's id")
    args = parser.parse_args()

    cfg = settings()
    engine = init_engine(cfg)
    # The same guard every worker entrypoint runs: started on the admin or outbox DSN by mistake,
    # row-level security would be silently inert and this tool could read or rewrite a row under
    # the wrong organization's policy (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)

    organization_id = uuid.UUID(args.org)
    dead_letter_id = uuid.UUID(args.id) if args.id else None
    replayed = await replay(organization_id=organization_id, dead_letter_id=dead_letter_id)
    print(f"replayed {replayed} dead letter(s) for organization {organization_id}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
