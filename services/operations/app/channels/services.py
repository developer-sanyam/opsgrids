"""Writes for channel connections, order import, inventory push and fulfilment push (Fulfilment
plan F11).

Two transaction shapes coexist here, and a caller must pick the right one, the same split
`app.orders.services`' own module docstring draws:

* **Connection and mapping CRUD** (`connect_channel`, `disconnect_channel`, `reconnect_channel`,
  `update_channel_config`, `upsert_sku_mapping`, `delete_sku_mapping`) take an **already-open**
  tenant-bound session (`SessionDep`/`get_tenant_session` from a route) and open no transaction
  of their own — the ordinary shape every write in this service uses.
* **Order import and the two push syncs** (`import_channel_order`, `sync_orders_for_channel`,
  `push_inventory_for_channel`, `push_fulfilment_update_for_order`, `retry_sync_log`) take the
  **raw, unwrapped** session and open their own transactions by hand, because each one makes a
  real outbound HTTP call (`ChannelClient`) that must never run under an open database
  transaction or a held row lock (`docs/io-outside-transactions.md`) — the same "claim → call →
  record" shape `order_services.accept_order` already uses for its own network call (the credit
  check), and for the identical reason.

`import_channel_order` is the one function worth reading closely: it drives a real order through
`app.orders.services.create_order` → `.place_order` → `.accept_order` — the actual F2 write path,
never a parallel table — and is idempotent and **resumable** on `ChannelOrderImport`'s unique
claim (`organization_id, channel_id, external_order_id`): a redelivered import (a re-poll that
lists an order already seen, or an explicit retry) that lands after the order was fully created
does nothing further; one that lands after the claim but before the order finished being placed
or accepted resumes from wherever `Order.status` says it actually got to, never re-running a step
already done and never creating a second order. See its own docstring.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.channels import registry, selectors, shopify
from app.channels.base import (
    ChannelAuthError,
    ChannelClient,
    ChannelFulfilmentUpdate,
    ChannelInventoryUpdate,
    ChannelOrder,
    ChannelRejected,
    ChannelUnavailable,
)
from app.channels.carrier import CarrierCredential
from app.channels.mock_provider import MockMarketplaceClient, parse_order
from app.channels.shopify import ShopifyClient
from app.config import settings
from app.inventory import quantity as qty_lib
from app.inventory import selectors as inv_selectors
from app.models import (
    AuditLog,
    Channel,
    ChannelOrderImport,
    ChannelSkuMapping,
    ChannelSyncLog,
    Order,
)
from app.orders import selectors as order_selectors
from app.orders import services as order_services
from platform_core.audit import record_audit
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.db import assert_tenant, set_tenant
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from platform_core.logging import get_logger
from platform_core.pii import mask, open_sealed, seal

log = get_logger("operations.channels")

#: The permission `service_ctx`'s system identity carries — an existing real permission, not a
#: bespoke "system" one, the same precedent `app.orders.services._ORDERS_SYSTEM_PERMISSION` and
#: `app.fulfilment.services._FULFILMENT_SYSTEM_PERMISSION` both set.
_CHANNELS_SYSTEM_PERMISSION = "operations.channels.channel.write"


def service_ctx(organization_id: uuid.UUID) -> ScopeContext:
    """A `ScopeContext` for code with no human caller — `app.channels.sweep.ChannelSyncSweep` and
    `app.consumers`' dispatch-event handlers, the same shape `app.orders.services.service_ctx`
    and `app.fulfilment.services.service_ctx` already establish."""
    principal = Principal(
        sub=uuid.UUID(int=0),
        organization_id=organization_id,
        membership_id=None,
        perms=[f"{_CHANNELS_SYSTEM_PERMISSION}:ORG"],
        entitlements=["operations"],
    )
    return ScopeContext(
        principal=principal, scope=Scope.ORG, permission=_CHANNELS_SYSTEM_PERMISSION
    )


async def _bind_tenant(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """The same by-hand tenancy chokepoint `app.orders.services._bind_tenant` uses, needed here
    for the same reason: import and sync open more than one transaction on one raw session."""
    await set_tenant(session, organization_id)
    if not settings().is_production:
        await assert_tenant(session, organization_id)


def _aad(organization_id: uuid.UUID, channel_id: uuid.UUID) -> bytes:
    """Binds a sealed credential to the exact row it was sealed for (`pii.seal`'s own docstring:
    "cannot be lifted into another row and decrypt there") — the same precedent
    `gst_portal_credentials` and an MFA factor both set."""
    return f"channel:{organization_id}:{channel_id}".encode()


# ------------------------------------------------------------------ connecting the client


def build_client(channel: Channel, *, api_key: str) -> ChannelClient:
    """Constructs the configured `ChannelClient` for `channel.provider`. Only the mock provider
    exists today (`app.domain.CHANNEL_PROVIDERS`); a real provider is a new branch here returning
    a new adapter, nothing else about this module changes."""
    if channel.provider == "mock_marketplace":
        base_url = channel.api_base_url or settings().mock_marketplace_api_base
        return MockMarketplaceClient(
            base_url=base_url, shop_id=channel.external_shop_id, api_key=api_key
        )
    if channel.provider == "shopify":
        token, _ = shopify.unpack_credential(api_key)
        return ShopifyClient(shop_domain=channel.external_shop_id, access_token=token)
    raise ValidationProblem(f"{channel.provider!r} is not a channel provider this build supports")


async def open_channel_credential(channel: Channel) -> str:
    """Opens this channel's sealed API key — **not** a sensitive read in ADR-0019 §3's sense: a
    background sync using its own connection's credential to do its own job, never returned to
    any caller or logged, is the same "not audited as a reveal" case finance's own worker
    credential already documents. Raises `ValidationProblem` if the connection holds no key at all
    (disconnected, or never fully connected)."""
    if channel.api_key_enc is None:
        raise ValidationProblem(f"channel {channel.id} holds no credential to sync with")
    return open_sealed(
        channel.api_key_enc, settings=settings(), aad=_aad(channel.organization_id, channel.id)
    )


async def open_carrier_credential(channel: Channel) -> CarrierCredential:
    """A carrier connection's packed credential (`CarrierCredential`), opened. Same
    not-a-reveal reasoning as `open_channel_credential`."""
    credential = CarrierCredential.unpack(await open_channel_credential(channel))
    if credential is None:
        raise ValidationProblem(f"channel {channel.id} holds no carrier credential")
    return credential


def _sealed_credential(
    organization_id: uuid.UUID,
    channel_id: uuid.UUID,
    *,
    provider: str,
    kind: str,
    api_key: str,
    api_secret: str | None = None,
    webhook_secret: str | None = None,
    webhook_token: str | None = None,
) -> tuple[bytes, str]:
    """`(ciphertext, mask)` for a connection's credential. A carrier's is packed with its second
    half and webhook token (kept across a reconnect, so the URL the customer registered keeps
    working); Shopify's holds the access token and the webhook secret together, and its mask
    shows only the token's tail; the mask is always of the account key alone."""
    if provider == "shopify":
        enc, _ = seal(
            shopify.pack_credential(api_key.strip(), webhook_secret),
            kind="secret",
            settings=settings(),
            aad=_aad(organization_id, channel_id),
        )
        return enc, "••••" + api_key.strip()[-2:]
    if webhook_secret:
        raise ValidationProblem(f"{provider!r} takes no webhook secret")
    plaintext = api_key
    if kind == "carrier":
        packed = CarrierCredential.new(api_key, api_secret or "")
        if webhook_token:
            packed = CarrierCredential(packed.key, packed.secret, webhook_token)
        plaintext = packed.pack()
    enc, _ = seal(
        plaintext, kind="secret", settings=settings(), aad=_aad(organization_id, channel_id)
    )
    return enc, mask(api_key, kind="secret")


async def load_client_for_channel(
    session: AsyncSession, ctx: ScopeContext, channel_id: uuid.UUID
) -> tuple[Channel, ChannelClient]:
    """Loads the channel, opens its credential and builds its client in one short, tenant-bound
    transaction — closed **before** any HTTP call is made
    (`docs/io-outside-transactions.md`). Every manual sync/retry/health-check route, and
    `app.channels.sweep.ChannelSyncSweep`, goes through this rather than repeating the
    read-then-open-then-build sequence. Raises `NotFoundProblem` for an unknown channel,
    `ValidationProblem` if it is not `connected`. The caller owns closing the returned client
    (`getattr(client, "aclose", None)`, since only some providers hold a real connection)."""
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        channel = await selectors.get_channel(session, ctx.organization_id, channel_id)
        if channel is None:
            raise NotFoundProblem("That channel is not part of this organization")
        if channel.status != "connected":
            raise ValidationProblem(f"Channel {channel.id} is not connected")
        api_key = await open_channel_credential(channel)
    return channel, build_client(channel, api_key=api_key)


# ------------------------------------------------------------------ connection CRUD


async def connect_channel(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    provider: str,
    name: str,
    external_shop_id: str,
    api_key: str,
    default_warehouse_id: uuid.UUID,
    default_currency: str,
    auto_accept_orders: bool,
    webhook_secret: str | None = None,
    kind: str = "sales",
    api_secret: str | None = None,
    request: Request | None = None,
) -> Channel:
    if provider not in domain.CHANNEL_PROVIDERS:
        raise ValidationProblem(f"{provider!r} is not a channel provider this build supports")
    if kind != domain.CHANNEL_PROVIDER_KINDS[provider]:
        raise ValidationProblem(
            f"{provider!r} is a {domain.CHANNEL_PROVIDER_KINDS[provider]} channel"
        )
    if not api_key.strip():
        raise ValidationProblem("A channel connection needs a credential")
    if provider in domain.CARRIER_NEEDS_SECRET and not (api_secret or "").strip():
        raise ValidationProblem(f"{provider} needs both an account email and a password")
    api_base_url = None
    if provider == "shopify":
        try:
            external_shop_id = shopify.validate_shop_domain(external_shop_id)
        except ValueError as exc:
            raise ValidationProblem(str(exc)) from None
        api_base_url = f"https://{external_shop_id}"
    warehouse = await inv_selectors.get_warehouse(
        session, ctx.organization_id, default_warehouse_id
    )
    if warehouse is None:
        raise NotFoundProblem("That warehouse is not part of this organization")

    # Generated here rather than left to the column's own client-side default, so the credential
    # can be sealed with its row's real id as AAD (`_aad`) in one pass — no insert-then-reseal.
    channel_id = uuid.uuid4()
    api_key_enc, api_key_masked = _sealed_credential(
        ctx.organization_id,
        channel_id,
        provider=provider,
        kind=kind,
        api_key=api_key,
        api_secret=api_secret,
        webhook_secret=webhook_secret,
    )
    channel = Channel(
        id=channel_id,
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        provider=provider,
        kind=kind,
        name=name,
        status="connected",
        external_shop_id=external_shop_id,
        api_base_url=api_base_url,
        api_key_enc=api_key_enc,
        api_key_masked=api_key_masked,
        default_warehouse_id=warehouse.id,
        default_currency=default_currency.upper(),
        auto_accept_orders=auto_accept_orders,
        connected_at=datetime.now(UTC),
    )
    session.add(channel)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="channel.connected",
        entity="channel",
        entity_id=channel.id,
        after={
            "provider": provider,
            "kind": kind,
            "name": name,
            "external_shop_id": external_shop_id,
            "default_warehouse_id": str(warehouse.id),
            "auto_accept_orders": auto_accept_orders,
        },
        request=request,
    )
    return channel


async def reveal_webhook_setup(
    session: AsyncSession, ctx: ScopeContext, channel: Channel, *, request: Request | None = None
) -> tuple[str, str]:
    """`(public path, token)` the customer registers at their carrier so tracking reaches us. The
    token authenticates inbound calls, so every reveal writes an audit row (never the token)."""
    if channel.kind != "carrier":
        raise ValidationProblem("Only a carrier connection has a tracking webhook")
    credential = await open_carrier_credential(channel)
    slug = next((s for s, p in registry.WEBHOOK_SLUGS.items() if p == channel.provider), None)
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="channel.webhook_setup.revealed",
        entity="channel",
        entity_id=channel.id,
        after={"provider": channel.provider},
        request=request,
    )
    path = f"/api/v1/webhooks/operations/{slug or channel.provider}?connection={channel.id}"
    return path, credential.webhook_token


async def disconnect_channel(
    session: AsyncSession, ctx: ScopeContext, channel: Channel, *, request: Request | None = None
) -> Channel:
    if channel.status == "disconnected":
        raise ConflictProblem(f"Channel {channel.id} is already disconnected")
    channel.status = "disconnected"
    channel.disconnected_at = datetime.now(UTC)
    channel.updated_by = str(ctx.principal.sub)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="channel.disconnected",
        entity="channel",
        entity_id=channel.id,
        after={"status": "disconnected"},
        request=request,
    )
    return channel


async def reconnect_channel(
    session: AsyncSession,
    ctx: ScopeContext,
    channel: Channel,
    *,
    api_key: str,
    api_secret: str | None = None,
    webhook_secret: str | None = None,
    request: Request | None = None,
) -> Channel:
    """Rotates the credential and clears an `error` status — the only way a connection leaves
    `error` (ADR-0019-style: a bad credential is fixed by supplying a new one, never by retrying
    the old one). Also the normal path back from a deliberate `disconnect`."""
    if not api_key.strip():
        raise ValidationProblem("A channel connection needs a credential")
    webhook_token = None
    if channel.kind == "carrier":
        try:
            webhook_token = (await open_carrier_credential(channel)).webhook_token
        except ValidationProblem:
            webhook_token = None
    channel.api_key_enc, channel.api_key_masked = _sealed_credential(
        ctx.organization_id,
        channel.id,
        provider=channel.provider,
        kind=channel.kind,
        api_key=api_key,
        api_secret=api_secret,
        webhook_secret=webhook_secret,
        webhook_token=webhook_token,
    )
    channel.status = "connected"
    channel.connected_at = datetime.now(UTC)
    channel.disconnected_at = None
    channel.consecutive_failures = 0
    channel.last_error_code = None
    channel.last_error_message = None
    channel.updated_by = str(ctx.principal.sub)
    channel.updated_at = datetime.now(UTC)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="channel.reconnected",
        entity="channel",
        entity_id=channel.id,
        after={"status": "connected"},
        request=request,
    )
    return channel


async def update_channel_config(
    session: AsyncSession,
    ctx: ScopeContext,
    channel: Channel,
    *,
    name: str | None = None,
    default_warehouse_id: uuid.UUID | None = None,
    auto_accept_orders: bool | None = None,
    request: Request | None = None,
) -> Channel:
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if name is not None and name != channel.name:
        before["name"], after["name"] = channel.name, name
        channel.name = name
    if default_warehouse_id is not None and default_warehouse_id != channel.default_warehouse_id:
        warehouse = await inv_selectors.get_warehouse(
            session, ctx.organization_id, default_warehouse_id
        )
        if warehouse is None:
            raise NotFoundProblem("That warehouse is not part of this organization")
        before["default_warehouse_id"] = (
            str(channel.default_warehouse_id) if channel.default_warehouse_id else None
        )
        after["default_warehouse_id"] = str(warehouse.id)
        channel.default_warehouse_id = warehouse.id
    if auto_accept_orders is not None and auto_accept_orders != channel.auto_accept_orders:
        before["auto_accept_orders"], after["auto_accept_orders"] = (
            channel.auto_accept_orders,
            auto_accept_orders,
        )
        channel.auto_accept_orders = auto_accept_orders
    if not after:
        return channel
    channel.updated_by = str(ctx.principal.sub)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="channel.configured",
        entity="channel",
        entity_id=channel.id,
        before=before,
        after=after,
        request=request,
    )
    return channel


async def upsert_sku_mapping(
    session: AsyncSession,
    ctx: ScopeContext,
    channel: Channel,
    *,
    external_sku: str,
    inventory_item_id: uuid.UUID,
    request: Request | None = None,
) -> ChannelSkuMapping:
    item = await inv_selectors.get_inventory_item(session, ctx.organization_id, inventory_item_id)
    if item is None:
        raise NotFoundProblem("That inventory item is not part of this organization")
    existing = await selectors.mappings_for_channel(session, ctx.organization_id, channel.id)
    mapping = existing.get(external_sku)
    if mapping is not None:
        mapping.inventory_item_id = item.id
        mapping.updated_by = str(ctx.principal.sub)
        action = "channel.sku_mapping.updated"
    else:
        mapping = ChannelSkuMapping(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            channel_id=channel.id,
            external_sku=external_sku,
            inventory_item_id=item.id,
        )
        session.add(mapping)
        action = "channel.sku_mapping.created"
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=action,
        entity="channel_sku_mapping",
        entity_id=mapping.id,
        after={"external_sku": external_sku, "inventory_item_id": str(item.id), "sku": item.sku},
        request=request,
    )
    return mapping


async def delete_sku_mapping(
    session: AsyncSession,
    ctx: ScopeContext,
    mapping: ChannelSkuMapping,
    *,
    request: Request | None = None,
) -> None:
    mapping.deleted_at = datetime.now(UTC)
    mapping.updated_by = str(ctx.principal.sub)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="channel.sku_mapping.deleted",
        entity="channel_sku_mapping",
        entity_id=mapping.id,
        after={"external_sku": mapping.external_sku},
        request=request,
    )


# ------------------------------------------------------------------ sync outcomes and health


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


async def _record_outcome(
    session: AsyncSession,
    ctx: ScopeContext,
    channel_id: uuid.UUID,
    *,
    sync_type: str,
    ok: bool,
    external_ref: str,
    detail: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
    auth_failed: bool = False,
    counts_toward_health: bool = True,
) -> None:
    """Writes one `ChannelSyncLog` row and, unless `counts_toward_health` says this outcome is a
    data problem rather than a provider-health one (a rejected import over an unmapped SKU, say),
    updates the connection's own failure streak and, past `domain.CHANNEL_MAX_CONSECUTIVE_FAILURES`
    or on any `ChannelAuthError`, its `status`. Must run inside an already-open, tenant-bound
    transaction — every caller in this module opens one around it.
    """
    session.add(
        ChannelSyncLog(
            organization_id=ctx.organization_id,
            channel_id=channel_id,
            sync_type=sync_type,
            status="success" if ok else "failed",
            external_ref=external_ref,
            detail=detail,
            error_code=error_code,
            error_message=error_message,
            duration_ms=duration_ms,
        )
    )
    if not counts_toward_health:
        await session.flush()
        return
    channel = await selectors.get_channel(session, ctx.organization_id, channel_id, for_update=True)
    if channel is None:
        await session.flush()
        return
    if ok:
        channel.consecutive_failures = 0
        # An auth failure needs a human to supply a fresh credential (`reconnect_channel`) before
        # this connection is trusted again — a success alone does not clear it (it cannot: the
        # credential that just worked is a *different* one). A threshold-tripped `error`, by
        # contrast, was never about the credential, so the next clean call is enough to restore it
        # (class docstring: "error... until it is reconnected" describes the auth case only).
        if channel.status == "error" and channel.last_error_code != "auth_error":
            channel.status = "connected"
        channel.last_error_code = None
        channel.last_error_message = None
    else:
        channel.consecutive_failures += 1
        channel.last_error_code = error_code
        channel.last_error_message = error_message
        if auth_failed:
            channel.status = "error"
            channel.last_error_code = "auth_error"
        elif channel.status == "connected" and domain.channel_should_suspend(
            channel.consecutive_failures - 1
        ):
            channel.status = "error"
    await session.flush()


async def _log_failure(
    session: AsyncSession,
    ctx: ScopeContext,
    channel_id: uuid.UUID,
    *,
    sync_type: str,
    external_ref: str,
    error_code: str,
    error_message: str,
    duration_ms: int,
    auth_failed: bool = False,
    detail: dict[str, Any] | None = None,
) -> None:
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        await _record_outcome(
            session,
            ctx,
            channel_id,
            sync_type=sync_type,
            ok=False,
            external_ref=external_ref,
            error_code=error_code,
            error_message=error_message,
            duration_ms=duration_ms,
            auth_failed=auth_failed,
            detail=detail,
        )


async def _call_and_log_failure(
    session: AsyncSession,
    ctx: ScopeContext,
    channel_id: uuid.UUID,
    *,
    sync_type: str,
    external_ref: str,
    exc: Exception,
    t0: float,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Classifies `exc` (one of the three `ChannelClient` exception shapes) and logs it. Returns
    `False` always — every call site uses it as `return await _call_and_log_failure(...)`."""
    if isinstance(exc, ChannelAuthError):
        await _log_failure(
            session,
            ctx,
            channel_id,
            sync_type=sync_type,
            external_ref=external_ref,
            error_code="auth_error",
            error_message=str(exc),
            duration_ms=_ms(t0),
            auth_failed=True,
            detail=detail,
        )
    elif isinstance(exc, ChannelRejected):
        await _log_failure(
            session,
            ctx,
            channel_id,
            sync_type=sync_type,
            external_ref=external_ref,
            error_code=exc.code,
            error_message=exc.message,
            duration_ms=_ms(t0),
            detail=detail,
        )
    else:
        await _log_failure(
            session,
            ctx,
            channel_id,
            sync_type=sync_type,
            external_ref=external_ref,
            error_code="unavailable",
            error_message=str(exc),
            duration_ms=_ms(t0),
            detail=detail,
        )
    return False


# ------------------------------------------------------------------ order import (F2 write path)


@dataclass(slots=True)
class ImportResult:
    order: Order | None
    created: bool
    rejected_reason: str | None


async def import_channel_order(
    session: AsyncSession,
    ctx: ScopeContext,
    channel_id: uuid.UUID,
    external_order: ChannelOrder,
    *,
    request: Request | None = None,
) -> ImportResult:
    """Idempotent, resumable import of one external order through the real F2 write path. See
    module docstring for the transaction shape and the resume semantics; in short:

    1. **Claim** `(organization_id, channel_id, external_order_id)` via `INSERT ... ON CONFLICT DO
       NOTHING` — the exclusive right to create this order. A concurrent or repeated call that
       loses the claim reads the existing row instead and resumes from there.
    2. If the claim (or the existing row) has no `order_id` yet and no `rejected_reason`, resolve
       every line's `ChannelSkuMapping` and call `app.orders.services.create_order`. An order line
       naming a SKU with no mapping is a **permanent** rejection (`rejected_reason`), never
       retried — the mapping has to be added first, and an admin does that from the sync log.
    3. `place_order`, if the order is still `DRAFT` (re-checked under its own lock — a concurrent
       resumer never places twice).
    4. `accept_order`, if the connection's `auto_accept_orders` is on and the order is still
       `PLACED` (same re-check). A `ConflictProblem` here (another resumer already advanced it)
       is treated as success, not failure — the order got where it needed to go either way.
    """
    t0 = time.monotonic()

    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        claim_stmt = (
            pg_insert(ChannelOrderImport)
            .values(
                organization_id=ctx.organization_id,
                channel_id=channel_id,
                external_order_id=external_order.external_order_id,
                order_id=None,
                rejected_reason=None,
                raw_payload=external_order.raw or None,
            )
            .on_conflict_do_nothing(
                index_elements=["organization_id", "channel_id", "external_order_id"]
            )
            .returning(ChannelOrderImport.id)
        )
        claimed_id = (await session.execute(claim_stmt)).scalar_one_or_none()
        won_claim = claimed_id is not None
        if won_claim:
            row = await session.get(ChannelOrderImport, claimed_id)
        else:
            row = await selectors.get_order_import_by_external_id(
                session, ctx.organization_id, channel_id, external_order.external_order_id
            )
        if row is None:  # pragma: no cover - defensive; the insert or the lookup always finds one
            raise ConflictProblem("Could not claim this external order for import")

        if row.rejected_reason is not None:
            await _record_outcome(
                session,
                ctx,
                channel_id,
                sync_type="order_import",
                ok=False,
                external_ref=external_order.external_order_id,
                error_code="rejected",
                error_message=row.rejected_reason,
                duration_ms=_ms(t0),
                counts_toward_health=False,
            )
            return ImportResult(order=None, created=False, rejected_reason=row.rejected_reason)

        order_id = row.order_id
        default_warehouse_id: uuid.UUID | None = None
        if order_id is None:
            channel = await selectors.get_channel(session, ctx.organization_id, channel_id)
            if channel is None:
                raise NotFoundProblem("That channel is not part of this organization")
            default_warehouse_id = channel.default_warehouse_id
            if default_warehouse_id is None:  # pragma: no cover - connect_channel requires one
                raise ConflictProblem(f"Channel {channel_id} has no default warehouse configured")
            mapping = await selectors.mappings_for_channel(session, ctx.organization_id, channel_id)
            missing = sorted(
                {ln.external_sku for ln in external_order.lines if ln.external_sku not in mapping}
            )
            if not external_order.lines:
                reason = "the external order carries no lines"
            elif missing:
                reason = f"no SKU mapping for: {', '.join(missing)}"
            else:
                reason = ""
            if reason:
                row.rejected_reason = reason
                await session.flush()
                await _record_outcome(
                    session,
                    ctx,
                    channel_id,
                    sync_type="order_import",
                    ok=False,
                    external_ref=external_order.external_order_id,
                    error_code="unmapped_sku",
                    error_message=reason,
                    duration_ms=_ms(t0),
                    counts_toward_health=False,
                )
                return ImportResult(order=None, created=False, rejected_reason=reason)

            lines = [
                {
                    "inventory_item_id": mapping[ln.external_sku].inventory_item_id,
                    "quantity": str(ln.quantity),
                    "selling_price_minor": ln.unit_price_minor,
                }
                for ln in external_order.lines
            ]
            try:
                created_order = await order_services.create_order(
                    session,
                    ctx,
                    channel="marketplace",
                    warehouse_id=default_warehouse_id,
                    customer_id=None,
                    customer_name=external_order.customer_name,
                    customer_phone=external_order.customer_phone,
                    currency=external_order.currency,
                    lines=lines,
                    request=request,
                )
            except ValidationProblem as exc:
                row.rejected_reason = str(exc)
                await session.flush()
                await _record_outcome(
                    session,
                    ctx,
                    channel_id,
                    sync_type="order_import",
                    ok=False,
                    external_ref=external_order.external_order_id,
                    error_code="invalid_order",
                    error_message=str(exc),
                    duration_ms=_ms(t0),
                    counts_toward_health=False,
                )
                return ImportResult(order=None, created=False, rejected_reason=str(exc))
            row.order_id = created_order.id
            await session.flush()
            order_id = created_order.id

    # --- place, if still DRAFT (re-checked under lock: a concurrent resumer never double-places)
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        order = await order_selectors.get_order(
            session, ctx.organization_id, order_id, for_update=True
        )
        if order is None:  # pragma: no cover - defensive
            raise ConflictProblem("The imported order could not be reloaded")
        if order.status == "DRAFT":
            await order_services.place_order(
                session, ctx, order, expected_version=order.version, request=request
            )

    # --- accept, if configured and still PLACED (re-checked fresh; a lost race is not a failure)
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        order = await order_selectors.get_order(session, ctx.organization_id, order_id)
        auto_accept = False
        channel = await selectors.get_channel(session, ctx.organization_id, channel_id)
        if channel is not None:
            auto_accept = channel.auto_accept_orders
        should_accept = auto_accept and order is not None and order.status == "PLACED"
        expected_version = order.version if order is not None else 0

    if should_accept:
        try:
            await order_services.accept_order(
                session, ctx, order_id, expected_version=expected_version, request=request
            )
        except ConflictProblem:
            # Another resumer (or a genuinely concurrent redelivery) already advanced this order
            # past `PLACED` between the read above and this call — the order still ends up
            # accepted or beyond, which is the outcome this step exists for, so this is not an
            # import failure.
            log.info(
                "channels.order_import.accept_race",
                channel_id=str(channel_id),
                external_order_id=external_order.external_order_id,
            )

    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        order = await order_selectors.get_order(session, ctx.organization_id, order_id)
        if order is None:  # pragma: no cover - defensive
            raise ConflictProblem("The imported order could not be reloaded")
        await _record_outcome(
            session,
            ctx,
            channel_id,
            sync_type="order_import",
            ok=True,
            external_ref=external_order.external_order_id,
            detail={
                "order_id": str(order.id),
                "order_number": order.number,
                "status": order.status,
            },
            duration_ms=_ms(t0),
        )
    return ImportResult(order=order, created=won_claim, rejected_reason=None)


# ------------------------------------------------------------------ scheduled/manual sync


@dataclass(slots=True)
class SyncSummary:
    attempted: int = 0
    succeeded: int = 0
    rejected: int = 0


async def sync_orders_for_channel(
    session: AsyncSession,
    ctx: ScopeContext,
    channel_id: uuid.UUID,
    client: ChannelClient,
    *,
    request: Request | None = None,
) -> SyncSummary:
    """Polls `client.list_new_orders` from this connection's own bookmark and imports every order
    it returns. Never runs under an open transaction while the HTTP call is in flight
    (`docs/io-outside-transactions.md`)."""
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        channel = await selectors.get_channel(session, ctx.organization_id, channel_id)
        if channel is None:
            raise NotFoundProblem("That channel is not part of this organization")
        cursor = channel.last_order_cursor

    t0 = time.monotonic()
    try:
        page = await client.list_new_orders(cursor=cursor)
    except (ChannelAuthError, ChannelUnavailable, ChannelRejected) as exc:
        await _call_and_log_failure(
            session, ctx, channel_id, sync_type="order_import", external_ref="", exc=exc, t0=t0
        )
        return SyncSummary()

    summary = SyncSummary()
    for external_order in page.orders:
        summary.attempted += 1
        result = await import_channel_order(
            session, ctx, channel_id, external_order, request=request
        )
        if result.rejected_reason is not None:
            summary.rejected += 1
        else:
            summary.succeeded += 1

    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        channel = await selectors.get_channel(
            session, ctx.organization_id, channel_id, for_update=True
        )
        if channel is not None:
            if page.next_cursor is not None:
                channel.last_order_cursor = page.next_cursor
            channel.last_orders_synced_at = datetime.now(UTC)
            await session.flush()
        await _record_outcome(
            session,
            ctx,
            channel_id,
            sync_type="order_import",
            ok=True,
            external_ref="",
            detail={
                "page_orders": len(page.orders),
                "attempted": summary.attempted,
                "succeeded": summary.succeeded,
                "rejected": summary.rejected,
            },
            duration_ms=_ms(t0),
        )
    return summary


async def push_inventory_for_channel(
    session: AsyncSession,
    ctx: ScopeContext,
    channel_id: uuid.UUID,
    client: ChannelClient,
    *,
    request: Request | None = None,
) -> SyncSummary:
    """Pushes every mapped SKU whose `InventoryItem.available_milli` has moved since it was last
    pushed — never everything on every tick, so a hot SKU during a picking wave does not flood the
    provider with redundant calls. `available_milli` is exactly ADR-0014 §2's "available to
    promise": on-hand less reserved, so a channel is never handed a number this platform's own
    reservation system has already promised elsewhere (F11 plan item 4's own requirement)."""
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        mappings = await selectors.mappings_for_channel(session, ctx.organization_id, channel_id)
        dirty: list[tuple[ChannelSkuMapping, int]] = []
        for mapping in mappings.values():
            item = await inv_selectors.get_inventory_item(
                session, ctx.organization_id, mapping.inventory_item_id
            )
            if item is None:
                continue
            if item.available_milli != mapping.last_pushed_available_milli:
                dirty.append((mapping, item.available_milli))
        if not dirty:
            return SyncSummary()
        mapping_ids = [m.id for m, _ in dirty]

    t0 = time.monotonic()
    updates = [
        ChannelInventoryUpdate(
            external_sku=mapping.external_sku, available_qty=Decimal(qty_lib.format_qty(milli))
        )
        for mapping, milli in dirty
    ]
    try:
        await client.push_inventory(updates)
    except (ChannelAuthError, ChannelUnavailable, ChannelRejected) as exc:
        async with session.begin():
            await _bind_tenant(session, ctx.organization_id)
            rows = (
                await session.execute(
                    select(ChannelSkuMapping).where(ChannelSkuMapping.id.in_(mapping_ids))
                )
            ).scalars()
            for failed_mapping in rows:
                failed_mapping.last_push_status = "failed"
                failed_mapping.last_push_error = str(exc)
            await session.flush()
        await _call_and_log_failure(
            session,
            ctx,
            channel_id,
            sync_type="inventory_push",
            external_ref=",".join(sorted(m.external_sku for m, _ in dirty))[:120],
            exc=exc,
            t0=t0,
        )
        return SyncSummary(attempted=len(dirty))

    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        now = datetime.now(UTC)
        rows = (
            await session.execute(
                select(ChannelSkuMapping).where(ChannelSkuMapping.id.in_(mapping_ids))
            )
        ).scalars()
        by_id = {row.id: row for row in rows}
        for mapping, milli in dirty:
            row = by_id.get(mapping.id)
            if row is None:
                continue
            row.last_pushed_available_milli = milli
            row.last_pushed_at = now
            row.last_push_status = "ok"
            row.last_push_error = None
        await session.flush()
        await _record_outcome(
            session,
            ctx,
            channel_id,
            sync_type="inventory_push",
            ok=True,
            external_ref=",".join(sorted(m.external_sku for m, _ in dirty))[:120],
            detail={"skus_pushed": len(dirty)},
            duration_ms=_ms(t0),
        )
        channel = await selectors.get_channel(
            session, ctx.organization_id, channel_id, for_update=True
        )
        if channel is not None:
            channel.last_inventory_synced_at = now
            await session.flush()
    return SyncSummary(attempted=len(dirty), succeeded=len(dirty))


async def push_fulfilment_update_for_order(
    session: AsyncSession,
    ctx: ScopeContext,
    order_id: uuid.UUID,
    *,
    status: str,
    tracking_number: str | None,
    carrier: str | None,
    occurred_at: datetime,
    request: Request | None = None,
) -> bool:
    """If `order_id` was imported through a connected channel, pushes this tracking fact to it.
    Returns whether a channel push was attempted — `False` for an ordinary phone/manual/sales/
    walk-in order is a no-op, not a failure. Called from `app.consumers`, bound to
    `operations.dispatch.shipment.*` (F11 plan item 5), and from `retry_sync_log` to replay a
    failed push. The push payload (including `order_id`) is written into the `ChannelSyncLog`
    row's own `detail` on **both** the success and the failure path — that is what lets a retry
    replay the exact fact that failed with no second read of `app.dispatch`'s own tables
    (`app/channels/__init__.py`'s module-boundary rule: `channels` never imports `dispatch`)."""
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        link = await selectors.get_order_import_for_order(session, ctx.organization_id, order_id)
        if link is None or link.order_id is None:
            return False
        channel = await selectors.get_channel(session, ctx.organization_id, link.channel_id)
        if channel is None or channel.status != "connected":
            return False
        channel_id = channel.id
        external_order_id = link.external_order_id
        api_key = await open_channel_credential(channel)
        client = build_client(channel, api_key=api_key)

    push_detail = {
        "order_id": str(order_id),
        "status": status,
        "tracking_number": tracking_number,
        "carrier": carrier,
        "occurred_at": occurred_at.isoformat(),
    }
    t0 = time.monotonic()
    try:
        await client.push_fulfilment_update(
            ChannelFulfilmentUpdate(
                external_order_id=external_order_id,
                status=status,
                tracking_number=tracking_number,
                carrier=carrier,
                occurred_at=occurred_at,
            )
        )
    except (ChannelAuthError, ChannelUnavailable, ChannelRejected) as exc:
        await _call_and_log_failure(
            session,
            ctx,
            channel_id,
            sync_type="fulfilment_push",
            external_ref=external_order_id,
            exc=exc,
            t0=t0,
            detail=push_detail,
        )
        return True
    finally:
        aclose = getattr(client, "aclose", None)
        if aclose is not None:
            await aclose()

    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        await _record_outcome(
            session,
            ctx,
            channel_id,
            sync_type="fulfilment_push",
            ok=True,
            external_ref=external_order_id,
            detail=push_detail,
            duration_ms=_ms(t0),
        )
    return True


async def check_channel_health(
    session: AsyncSession, ctx: ScopeContext, channel_id: uuid.UUID, client: ChannelClient
) -> bool:
    """A synchronous "can this connection still talk to the provider" probe — what the admin UI's
    "test connection" action calls, and what a sweep could run on a schedule of its own (not
    wired to one today: order and inventory sync already prove liveness on every tick they do
    anything, so a dedicated health-check sweep is deferred until a connection with nothing else
    to sync needs one)."""
    t0 = time.monotonic()
    try:
        health = await client.check_health()
    except (ChannelAuthError, ChannelUnavailable) as exc:
        return await _call_and_log_failure(
            session, ctx, channel_id, sync_type="health_check", external_ref="", exc=exc, t0=t0
        )
    async with session.begin():
        await _bind_tenant(session, ctx.organization_id)
        await _record_outcome(
            session,
            ctx,
            channel_id,
            sync_type="health_check",
            ok=health.healthy,
            external_ref="",
            detail={"detail": health.detail},
            duration_ms=_ms(t0),
            error_code=None if health.healthy else "unhealthy",
            error_message=None if health.healthy else health.detail,
        )
        channel = await selectors.get_channel(
            session, ctx.organization_id, channel_id, for_update=True
        )
        if channel is not None:
            channel.last_health_check_at = datetime.now(UTC)
            await session.flush()
    return health.healthy


# ------------------------------------------------------------------ retry (admin UI action)


async def retry_sync_log(
    session: AsyncSession,
    ctx: ScopeContext,
    channel_id: uuid.UUID,
    client: ChannelClient,
    log_entry: ChannelSyncLog,
    *,
    request: Request | None = None,
) -> bool:
    """Re-attempts the action a failed `ChannelSyncLog` row represents, from **current** data —
    not a raw HTTP replay, because the underlying facts (an item's `available_milli`, a
    shipment's current status) may have moved on since the failure. Returns whether the retry
    succeeded. Raises `ValidationProblem` for a log entry with nothing left to retry (a
    `health_check`, or a since-resolved `order_import` rejection with no stored payload)."""
    if log_entry.sync_type == "order_import":
        async with session.begin():
            await _bind_tenant(session, ctx.organization_id)
            link = await selectors.get_order_import_by_external_id(
                session, ctx.organization_id, channel_id, log_entry.external_ref
            )
        if link is None or link.order_id is not None:
            raise ValidationProblem("This import already has a real order; nothing to retry")
        if not link.raw_payload:
            raise ValidationProblem("This import's original payload was not kept; re-poll instead")
        # A retry of an `unmapped_sku`/`invalid_order` rejection needs the row's own claim
        # cleared first, or `import_channel_order` will just re-read the same `rejected_reason`
        # it already holds — the admin UI's retry is meant for "I just added the missing mapping",
        # so the claim is reopened here, under lock, immediately before resuming it.
        async with session.begin():
            await _bind_tenant(session, ctx.organization_id)
            row = await selectors.get_order_import_by_external_id(
                session, ctx.organization_id, channel_id, log_entry.external_ref
            )
            if row is not None:
                row.rejected_reason = None
                await session.flush()
        external_order = parse_order(dict(link.raw_payload))
        result = await import_channel_order(
            session, ctx, channel_id, external_order, request=request
        )
        return result.rejected_reason is None

    if log_entry.sync_type == "inventory_push":
        summary = await push_inventory_for_channel(
            session, ctx, channel_id, client, request=request
        )
        return summary.attempted == 0 or summary.succeeded == summary.attempted

    if log_entry.sync_type == "fulfilment_push":
        # Replays the **exact fact that failed** — `log_entry.detail` is the push payload itself
        # (`push_fulfilment_update_for_order` stores it on both the success and the failure log),
        # never a fresh read of the shipment's *current* status. That keeps this module inside its
        # own dependency rule (`channels` imports `orders` and `inventory`, never `dispatch` —
        # `app/channels/__init__.py`'s own docstring, `.importlinter`'s `channels-boundary`
        # contract): the one place tracking facts reach this module is the outbox/consumer round
        # trip, and a retry of an already-received fact needs no second look at `dispatch`'s own
        # tables to replay it.
        detail = log_entry.detail or {}
        order_id_str = str(detail.get("order_id") or "")
        if not order_id_str:
            raise ValidationProblem("This log entry has no stored payload to retry")
        return await push_fulfilment_update_for_order(
            session,
            ctx,
            uuid.UUID(order_id_str),
            status=str(detail.get("status", "")),
            tracking_number=detail.get("tracking_number"),
            carrier=detail.get("carrier"),
            occurred_at=datetime.fromisoformat(str(detail["occurred_at"]))
            if detail.get("occurred_at")
            else datetime.now(UTC),
            request=request,
        )

    if log_entry.sync_type == "health_check":
        return await check_channel_health(session, ctx, channel_id, client)

    raise ValidationProblem(f"{log_entry.sync_type!r} is not a retryable sync type")


def shipment_status_to_channel_status(shipment_status: str) -> str:
    """`Shipment.status` (`app.domain.SHIPMENT_TRANSITIONS`' vocabulary, owned by `app.dispatch`)
    -> the tracking status a channel is told. Lives here, not in `app.dispatch`, because this
    module never imports `app.dispatch` (its own boundary rule) — `app.consumers`, which already
    imports both, is the one place this mapping is applied, translating an
    `operations.dispatch.shipment.*` event's own status string before calling
    `push_fulfilment_update_for_order`."""
    return {
        "IN_TRANSIT": "dispatched",
        "DELIVERED": "delivered",
        "FAILED": "failed",
        "RETURNED": "returned",
    }.get(shipment_status, shipment_status.lower())
