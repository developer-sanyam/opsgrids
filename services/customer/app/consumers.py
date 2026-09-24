"""Event consumers for customer. Runs as its own process.

Every handler is idempotent: `already_processed` before acting, `mark_processed` in the
same transaction as its side effects. Redelivery is normal, not exceptional.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.models import ProcessedEvent
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
