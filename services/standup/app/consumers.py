"""Event consumers for standup. Runs as its own process.

Every handler is idempotent: `already_processed` before acting, `mark_processed` in the
same transaction as its side effects. Redelivery is normal, not exceptional.
"""

from __future__ import annotations

import asyncio

from app.config import settings
from platform_core.db import init_engine
from platform_core.events import EventConsumer

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="standup.events")


async def main() -> None:
    init_engine(settings())
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
