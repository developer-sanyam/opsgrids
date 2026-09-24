"""Event consumers for account. Runs as its own process.

This is where a product's catalogue reaches the organization that just bought it. When
product-service turns a product on — a hand activation (`core.product.activated`) or the
commerce projection driving the same row (`commerce.subscription.*`) — the consumer here
installs the product's **permission manifest** for that organization: the permissions join the
platform catalogue, the role templates are instantiated as org-owned system roles, and the
admin-most template is granted to the organization's owners. Without this, activation flips
the gateway's 402 gate while every request behind it still 403s — the product is paid for and
unusable, and nothing errors.

A bundle subscription switches on several products, named in the event's `entitled_products`,
and each is installed from its own manifest. The bundle's own key (`suite`) has no manifest and
is never installed: it is sold, not routed (docs/commerce-packaging.md).

The manifest comes from `libs/contracts/permissions/<product>.json`, exported from each
product's own `app/catalogue.py` at build time (`tools/export_permissions.py`). Data, not an
import: pulling another service's catalogue module into this process is exactly the coupling
the manifest exists to avoid.

Three properties are load-bearing, and each has a test:

* **The GUC is set per message.** This process runs as `opsgrids_account` (NOBYPASSRLS), and
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
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.catalogue import load_product_manifest
from app.config import settings
from app.models import ProcessedEvent
from platform_core.cache import init_redis
from platform_core.db import assert_app_role, init_engine, session_scope
from platform_core.events import EventConsumer, already_processed, mark_processed
from platform_core.logging import get_logger

log = get_logger("account.consumers")

consumer = EventConsumer(amqp_url=settings().rabbitmq_url, queue_name="account.events")

#: Same shape as `app.cron.WorkScope` — defined again here, not imported, because `app.consumers`
#: and `app.cron` are independent layers (the service anatomy, CLAUDE.md; `.importlinter`'s
#: `service-layers` contract refuses the import): a consumer that reached into the sweep module
#: would be the same accidental coupling the contract exists to catch, just in the other
#: direction from the one its own comment names.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


def _default_session_scope(organization_id: uuid.UUID) -> Any:
    return session_scope(organization_id=organization_id)


#: The transaction scope every handler below opens. Defaults to the process-wide engine — this
#: consumer's own connection pool when it runs standalone (`python -m app.consumers`,
#: `bin/worker`). `app/main.py` swaps it to a bulkhead scope (`platform_core.db.bulkhead_scope`)
#: before starting this consumer as a supervised background task inside the API process, on
#: `RUN_CONSUMERS_IN_API` — so a folded consumer's per-message transactions can neither take a
#: connection from request traffic nor be starved by it. Module-level and process-wide on
#: purpose, the same seam the sweeps already use (`app.cron.WorkScope`).
_session_scope: WorkScope = _default_session_scope


def use_session_scope(scope: WorkScope) -> None:
    """Point every handler above at `scope` instead of the default engine. Called once, before
    this consumer starts, by whichever entrypoint constructs it that way — never by a handler."""
    global _session_scope
    _session_scope = scope


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
    # Not a transition: subscription-service's one-off backfill re-announces a bundle that
    # predates `entitled_products`, so its members' manifests are installed at last.
    "commerce.subscription.entitlements_resynced",
)

#: Decided from the payload's `state`, not the event name, mirroring the projection in
#: product-service: a lifecycle event whose state has already moved on must not install as
#: though it had not.
_DE_ENTITLING_STATES = ("suspended", "cancelled")


def _products_to_install(payload: dict[str, Any]) -> list[str]:
    """Every product this event switches on, in order, de-duplicated.

    A bundle subscription names its members in `entitled_products`, and each member is its own
    manifest — `suite` has none, and never will: it is sold, not routed. An event without the
    field (a hand activation, or a subscription event from before it existed) speaks for the one
    product it names. A malformed value raises rather than guessing which products a customer
    paid for.
    """
    value = payload.get("entitled_products")
    if value is None:
        return [payload["product_key"]]
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"entitled_products must be a list of product keys, got {value!r}")
    keys: list[str] = []
    for key in value:
        if key not in keys:
            keys.append(key)
    return keys


async def install_registration_on_activation(envelope: dict[str, Any]) -> None:
    """Install the manifest of every product the event switches on, idempotently.

    One transaction for all of them, marked processed once: a trio installs three manifests or
    none, so a redelivery after a failure retries the whole set rather than skipping the members
    that happened to land.
    """
    event_id = uuid.UUID(envelope["event_id"])
    payload = envelope["payload"]
    organization_id = uuid.UUID(payload["organization_id"])
    product_keys = _products_to_install(payload)
    state = payload.get("state")

    # The GUC is set here, per message, for the whole transaction: the role and grant writes
    # below are NOBYPASSRLS writes against forced tenant policies and only pass because of
    # this binding.
    async with _session_scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            log.info("registration.event.duplicate", event_id=str(event_id))
            return
        if state in _DE_ENTITLING_STATES:
            mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])
            return

        summaries: list[dict[str, Any]] = []
        for product_key in product_keys:
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
                continue
            summaries.append(
                await services.install_product_registration(
                    session,
                    org_id=organization_id,
                    manifest=manifest,
                    acting_as="product-activation",
                )
            )
        mark_processed(session, ProcessedEvent, event_id, envelope["event_type"])

    for summary in summaries:
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
    cfg = settings()
    engine = init_engine(cfg)
    # Consumers run NOBYPASSRLS: the per-message tenant GUC only means something if
    # row-level security can bind this role. Started on the admin or outbox DSN by
    # mistake, RLS would be silently inert — refuse outside local (ADR-0002).
    await assert_app_role(engine, environment=cfg.environment)
    # Granting the admin role to an owner bumps the organization's permission version, and
    # that bump raises rather than degrading — losing it would leave stale tokens working.
    init_redis(cfg.redis_url)
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
