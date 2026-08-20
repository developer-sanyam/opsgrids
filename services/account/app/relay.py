"""Outbox relay for account. Runs as its own process.

Must run as a role with BYPASSRLS: it drains every organization's events in one loop, so
under row-level security as the app role it would silently see nothing.
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.models import OutboxEvent
from platform_core.db import get_sessionmaker, init_engine
from platform_core.events import OutboxRelay


async def main() -> None:
    cfg = settings()
    init_engine(cfg)
    relay = OutboxRelay(
        amqp_url=cfg.rabbitmq_url,
        outbox_cls=OutboxEvent,
        session_factory=get_sessionmaker(),
        producer=cfg.service_name,
    )
    await relay.run()


if __name__ == "__main__":
    asyncio.run(main())
