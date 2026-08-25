#!/usr/bin/env python3
"""Inspect and replay a consumer's dead-letter queue.

A handler that fails twice dead-letters the message (`platform_core.events.EventConsumer`).
The DLQ is durable, so nothing is lost — but nothing moves either, and a parked event in a
money path is an incident. This is the replay path: look at what is parked, then feed it
back through the main exchange so the consumer gets it again.

    python tools/dlq.py peek   --queue product.events [--limit 10]
    python tools/dlq.py replay --queue product.events [--limit 10] [--event-id <uuid>]

Replayed messages are fresh publishes, so they get the consumer's full retry budget again.
Handlers are idempotent by contract (`processed_events`), so replaying an event that somehow
half-succeeded is safe. **Replay after fixing the cause**, not instead of finding it — a
poison message will simply come straight back.

A caution on ordering: a replayed event is by definition old. Projections that fold state
carry their own out-of-order guards, but replay events in `peek` order (oldest first) anyway.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import aio_pika

from platform_core.events import EXCHANGE

DEFAULT_AMQP = "amqp://atmora:atmora@localhost:5673/"


def _summary(envelope: dict[str, Any]) -> str:
    return (
        f"{envelope.get('event_id')}  {envelope.get('event_type')}  "
        f"org={envelope.get('organization_id')}  occurred_at={envelope.get('occurred_at')}"
    )


async def _drain_dlq(
    *, amqp_url: str, queue_name: str, limit: int, replay: bool, event_id: str | None
) -> int:
    """Walk up to `limit` messages off `{queue_name}.dlq`; requeue what is not replayed."""
    connection = await aio_pika.connect_robust(amqp_url)
    handled = 0
    async with connection:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        dlq = await channel.declare_queue(f"{queue_name}.dlq", durable=True)
        print(f"{queue_name}.dlq: {dlq.declaration_result.message_count} message(s) parked")

        for _ in range(limit):
            message = await dlq.get(fail=False)
            if message is None:
                break
            try:
                envelope = json.loads(message.body)
            except Exception:
                # Undecodable is how it got here in the first place; leave it parked.
                print(f"  undecodable message ({len(message.body)} bytes) — left in place")
                await message.nack(requeue=True)
                continue

            wanted = event_id is None or envelope.get("event_id") == event_id
            if replay and wanted:
                await exchange.publish(
                    aio_pika.Message(
                        body=message.body,
                        content_type="application/json",
                        message_id=envelope.get("event_id"),
                        correlation_id=envelope.get("correlation_id"),
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    ),
                    routing_key=str(envelope.get("event_type")),
                )
                await message.ack()
                handled += 1
                print(f"  replayed  {_summary(envelope)}")
            else:
                await message.nack(requeue=True)
                if wanted:
                    handled += 1
                    print(f"  parked    {_summary(envelope)}")
    return handled


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["peek", "replay"])
    parser.add_argument("--queue", required=True, help="consumer queue name, e.g. product.events")
    parser.add_argument("--limit", type=int, default=10, help="messages to inspect (default 10)")
    parser.add_argument("--event-id", help="replay only this event id")
    parser.add_argument("--amqp-url", default=os.environ.get("RABBITMQ_URL", DEFAULT_AMQP))
    args = parser.parse_args()

    handled = asyncio.run(
        _drain_dlq(
            amqp_url=args.amqp_url,
            queue_name=args.queue,
            limit=args.limit,
            replay=args.command == "replay",
            event_id=args.event_id,
        )
    )
    verb = "replayed" if args.command == "replay" else "shown"
    print(f"{handled} message(s) {verb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
