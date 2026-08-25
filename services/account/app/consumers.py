"""Event consumers for account. Runs as its own process.

This is where a product's catalogue reaches the organization that just bought it. When
product-service turns a product on — a hand activation (`core.product.activated`) or the
commerce projection driving the same row (`commerce.subscription.*`) — the consumer here
installs the product's **permission manifest** for that organization: the permissions join the
platform catalogue, the role templates are instantiated as org-owned system roles, and the
admin-most template is granted to the organization's owners. Without this, activation flips
the gateway's 402 gate while every request behind it still 403s — the product is paid for and
unusable, and nothing errors.

The manifest comes from `libs/contracts/permissions/<product>.json`, exported from each
product's own `app/catalogue.py` at build time (`tools/export_permissions.py`). Data, not an
import: pulling another service's catalogue module into this process is exactly the coupling
the manifest exists to avoid.

Three properties are load-bearing, and each has a test:

* **The GUC is set per message.** This process runs as `atmora_account` (NOBYPASSRLS), and
  roles, grants and the audit trail are under forced tenant policies.
  `session_scope(organization_id=...)` binds the tenant for the transaction, so the writes
  pass the policies' `WITH CHECK`. (`permissions` is global and deliberately un-policied.)
* **Every handler is idempotent.** Delivery is at-least-once; `already_processed` before
  acting, `mark_processed` in the same transaction as the install. A redelivered activation
  re-installs nothing and re-grants nobody.
* **A product with no manifest is recorded, not dead-lettered.** Not every product has a
  backend catalogue — a purely presentational one has nothing to register — and redelivery
  cannot conjure a file this deployment does not ship. The miss is logged at warning so a
  deploy-ordering gap (product activated before its manifest shipped) is visible; the fix is
  `tools/wire_product.py`, which drives this same install by hand.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app import services
from app.catalogue import load_product_manifest
from app.config import settings
from app.models import ProcessedEvent
from platform_core.cache import init_redis
from platform_core.db import init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("account.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="account.events")

#: Contract strings from product- and subscription-service — named here, not imported,
#: because one process may only ever hold one service's `app`.
EVT_PRODUCT_ACTIVATED = "core.product.activated"

#: The subscription moves that leave a product entitling. The de-entitling two (`suspended`,
#: `cancelled`) are not bound at all: suspension does not uninstall anything — roles and
#: permissions describe what exists, entitlement is the gateway's 402 gate — so there is
#: nothing for this consumer to do with them.
ENTITLING_SUBSCRIPTION_EVENTS = (
    "commerce.subscription.trial_started",
    "commerce.subscription.activated",
    "commerce.subscription.renewed",
    "commerce.subscription.upgraded",
    "commerce.subscription.downgraded",
    "commerce.subscription.past_due",
)

#: Decided from the payload's `state`, not the event name, mirroring the projection in
#: product-service: a lifecycle event whose state has already moved on must not install as
#: though it had not.
_DE_ENTITLING_STATES = ("suspended", "cancelled")


async def install_registration_on_activation(envelope: dict[str, Any]) -> None:
    """Install the activated product's manifest for the organization, idempotently."""
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = uuid.UUID(payload["organization_id"])
    product_key: str = payload["product_key"]
    state = payload.get("state")

    # The GUC is set here, per message, for the whole transaction: the role and grant writes
    # below are NOBYPASSRLS writes against forced tenant policies and only pass because of
    # this binding.
    async with session_scope(organization_id=organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("registration.event.duplicate", event_id=str(event_id))
            return
        if state in _DE_ENTITLING_STATES:
            mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
            return

        manifest = load_product_manifest(product_key)
        if manifest is None:
            log.warning(
                "registration.manifest.missing",
                event_id=str(event_id),
                organization_id=str(organization_id),
                product_key=product_key,
                hint="no permission manifest ships for this product; if one should, export "
                "it (make openapi) and re-run tools/wire_product.py for this organization",
            )
            mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
            return

        summary = await services.install_product_registration(
            session,
            org_id=organization_id,
            manifest=manifest,
            acting_as="product-activation",
        )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    log.info(
        "registration.installed",
        event_id=str(event_id),
        organization_id=str(organization_id),
        **summary,
    )


consumer.on(EVT_PRODUCT_ACTIVATED)(install_registration_on_activation)
for _event_type in ENTITLING_SUBSCRIPTION_EVENTS:
    consumer.on(_event_type)(install_registration_on_activation)


async def main() -> None:
    init_engine(settings())
    # Granting the admin role to an owner bumps the organization's permission version, and
    # that bump raises rather than degrading — losing it would leave stale tokens working.
    init_redis(settings().redis_url)
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
