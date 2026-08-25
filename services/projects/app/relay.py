"""Outbox relay for projects. Runs as its own process.

Runs as `atmora_outbox`: BYPASSRLS, because it drains every organization's events in one
loop and under row-level security the app role would silently see nothing; privileged to read
and mark rows in `outbox` and nothing else, because the admin role owns the whole schema and a
bug here must not be able to write anything but the rows it marks as sent.
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.models import OutboxEvent
from platform_core.db import (
    assert_outbox_role,
    get_outbox_engine,
    get_outbox_sessionmaker,
    init_engine,
    init_outbox_engine,
)
from platform_core.events import OutboxRelay


async def main() -> None:
    cfg = settings()
    # The app engine is the fallback `get_outbox_sessionmaker` takes when no outbox role is
    # configured; `init_outbox_engine` logs `outbox.role.not_split` in that case, and the
    # assertion below turns the silent-stall configuration into a startup failure outside
    # local development.
    init_engine(cfg)
    init_outbox_engine(cfg)
    await assert_outbox_role(get_outbox_engine(), environment=cfg.environment)
    relay = OutboxRelay(
        amqp_url=cfg.rabbitmq_url,
        outbox_cls=OutboxEvent,
        session_factory=get_outbox_sessionmaker(),
        producer=cfg.service_name,
    )
    await relay.run()


if __name__ == "__main__":
    asyncio.run(main())
