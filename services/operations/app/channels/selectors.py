"""Reads for channel connections (Fulfilment plan F11), plus the ORM -> wire projection
(`app.selectors`'s own convention). A channel connection is a shared operational resource like a
shipment or an order — every permission in `app.catalogue`'s `channel` module is ORG-only, so
every statement here calls `ctx.apply(stmt, owner_col=None)`, the same enforcement point
`app.dispatch.selectors`'s own module docstring documents for the identical reason.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, ChannelOrderImport, ChannelSkuMapping, ChannelSyncLog
from app.schemas import ChannelOut, ChannelSkuMappingOut, ChannelSyncLogOut
from platform_core.auth import ScopeContext


def channels_stmt(ctx: ScopeContext, *, status: str | None = None) -> Select[Any]:
    stmt = select(Channel).where(
        Channel.organization_id == ctx.organization_id, Channel.deleted_at.is_(None)
    )
    stmt = ctx.apply(stmt, owner_col=None)
    if status:
        stmt = stmt.where(Channel.status == status)
    return stmt.order_by(Channel.created_at.desc())


async def get_channel(
    session: AsyncSession, org_id: uuid.UUID, channel_id: uuid.UUID, *, for_update: bool = False
) -> Channel | None:
    stmt = select(Channel).where(
        Channel.id == channel_id, Channel.organization_id == org_id, Channel.deleted_at.is_(None)
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


def sku_mappings_stmt(ctx: ScopeContext, *, channel_id: uuid.UUID) -> Select[Any]:
    stmt = select(ChannelSkuMapping).where(
        ChannelSkuMapping.organization_id == ctx.organization_id,
        ChannelSkuMapping.channel_id == channel_id,
        ChannelSkuMapping.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=None)
    return stmt.order_by(ChannelSkuMapping.external_sku)


async def get_sku_mapping(
    session: AsyncSession, org_id: uuid.UUID, mapping_id: uuid.UUID
) -> ChannelSkuMapping | None:
    stmt = select(ChannelSkuMapping).where(
        ChannelSkuMapping.id == mapping_id,
        ChannelSkuMapping.organization_id == org_id,
        ChannelSkuMapping.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def mappings_for_channel(
    session: AsyncSession, org_id: uuid.UUID, channel_id: uuid.UUID
) -> dict[str, ChannelSkuMapping]:
    """Every mapping this channel holds, keyed by its external SKU — what
    `app.channels.services.import_channel_order` and `.push_inventory_for_channel` both resolve
    against."""
    stmt = select(ChannelSkuMapping).where(
        ChannelSkuMapping.organization_id == org_id,
        ChannelSkuMapping.channel_id == channel_id,
        ChannelSkuMapping.deleted_at.is_(None),
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {row.external_sku: row for row in rows}


def sync_logs_stmt(
    ctx: ScopeContext,
    *,
    channel_id: uuid.UUID,
    sync_type: str | None = None,
    status: str | None = None,
) -> Select[Any]:
    stmt = select(ChannelSyncLog).where(
        ChannelSyncLog.organization_id == ctx.organization_id,
        ChannelSyncLog.channel_id == channel_id,
    )
    stmt = ctx.apply(stmt, owner_col=None)
    if sync_type:
        stmt = stmt.where(ChannelSyncLog.sync_type == sync_type)
    if status:
        stmt = stmt.where(ChannelSyncLog.status == status)
    return stmt.order_by(ChannelSyncLog.attempted_at.desc())


async def get_sync_log(
    session: AsyncSession, org_id: uuid.UUID, log_id: uuid.UUID
) -> ChannelSyncLog | None:
    stmt = select(ChannelSyncLog).where(
        ChannelSyncLog.id == log_id, ChannelSyncLog.organization_id == org_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_order_import_by_external_id(
    session: AsyncSession, org_id: uuid.UUID, channel_id: uuid.UUID, external_order_id: str
) -> ChannelOrderImport | None:
    stmt = select(ChannelOrderImport).where(
        ChannelOrderImport.organization_id == org_id,
        ChannelOrderImport.channel_id == channel_id,
        ChannelOrderImport.external_order_id == external_order_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_order_import_for_order(
    session: AsyncSession, org_id: uuid.UUID, order_id: uuid.UUID
) -> ChannelOrderImport | None:
    """Reverse lookup: which channel connection (if any) an order was imported through — what the
    dispatch-event consumer (`app.consumers`) resolves before pushing a tracking update."""
    stmt = select(ChannelOrderImport).where(
        ChannelOrderImport.organization_id == org_id, ChannelOrderImport.order_id == order_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ------------------------------------------------------------------ projections (ORM -> wire)


def channel_out(channel: Channel) -> ChannelOut:
    return ChannelOut(
        id=str(channel.id),
        provider=channel.provider,
        name=channel.name,
        status=channel.status,
        external_shop_id=channel.external_shop_id,
        api_key_masked=channel.api_key_masked,
        default_warehouse_id=str(channel.default_warehouse_id)
        if channel.default_warehouse_id
        else None,
        default_currency=channel.default_currency,
        auto_accept_orders=channel.auto_accept_orders,
        last_orders_synced_at=channel.last_orders_synced_at,
        last_inventory_synced_at=channel.last_inventory_synced_at,
        last_health_check_at=channel.last_health_check_at,
        consecutive_failures=channel.consecutive_failures,
        last_error_code=channel.last_error_code,
        last_error_message=channel.last_error_message,
        connected_at=channel.connected_at,
        disconnected_at=channel.disconnected_at,
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )


def sku_mapping_out(mapping: ChannelSkuMapping) -> ChannelSkuMappingOut:
    return ChannelSkuMappingOut(
        id=str(mapping.id),
        channel_id=str(mapping.channel_id),
        external_sku=mapping.external_sku,
        inventory_item_id=str(mapping.inventory_item_id),
        last_pushed_available_milli=mapping.last_pushed_available_milli,
        last_pushed_at=mapping.last_pushed_at,
        last_push_status=mapping.last_push_status,
        last_push_error=mapping.last_push_error,
    )


def sync_log_out(log: ChannelSyncLog) -> ChannelSyncLogOut:
    return ChannelSyncLogOut(
        id=str(log.id),
        channel_id=str(log.channel_id),
        sync_type=log.sync_type,
        status=log.status,
        external_ref=log.external_ref,
        detail=log.detail,
        error_code=log.error_code,
        error_message=log.error_message,
        duration_ms=log.duration_ms,
        attempted_at=log.attempted_at,
    )
