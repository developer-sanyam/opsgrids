"""Reads for slice W1. Every query filters on `organization_id`; RLS is the net, not the filter."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InboundMessage, Message, ProviderTemplate
from app.w1_models import ConnectionHealth, SetupRun, WhatsAppNumber


async def get_run(
    session: AsyncSession,
    organization_id: uuid.UUID,
    setup_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> SetupRun | None:
    stmt = select(SetupRun).where(
        SetupRun.organization_id == organization_id, SetupRun.id == setup_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def run_by_code_hash(
    session: AsyncSession, organization_id: uuid.UUID, code_hash: str
) -> SetupRun | None:
    stmt = select(SetupRun).where(
        SetupRun.organization_id == organization_id, SetupRun.code_hash == code_hash
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def running_runs(session: AsyncSession, organization_id: uuid.UUID) -> list[SetupRun]:
    stmt = (
        select(SetupRun)
        .where(SetupRun.organization_id == organization_id, SetupRun.status == "running")
        .order_by(SetupRun.created_at.desc())
        .limit(20)
    )
    return list((await session.execute(stmt)).scalars().all())


async def numbers(session: AsyncSession, organization_id: uuid.UUID) -> list[WhatsAppNumber]:
    """The organization's cached numbers: the default first, then by display number. A WABA holds
    a handful (Meta's own ceiling is 25), so this is bounded without paging."""
    stmt = (
        select(WhatsAppNumber)
        .where(WhatsAppNumber.organization_id == organization_id)
        .order_by(
            WhatsAppNumber.is_default.desc(),
            WhatsAppNumber.display_phone_number,
            WhatsAppNumber.id,
        )
        .limit(100)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_number(
    session: AsyncSession, organization_id: uuid.UUID, number_id: uuid.UUID
) -> WhatsAppNumber | None:
    stmt = select(WhatsAppNumber).where(
        WhatsAppNumber.organization_id == organization_id, WhatsAppNumber.id == number_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def number_by_phone_id(
    session: AsyncSession,
    organization_id: uuid.UUID,
    phone_number_id: str,
    *,
    for_update: bool = False,
) -> WhatsAppNumber | None:
    stmt = select(WhatsAppNumber).where(
        WhatsAppNumber.organization_id == organization_id,
        WhatsAppNumber.phone_number_id == phone_number_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_health(session: AsyncSession, organization_id: uuid.UUID) -> ConnectionHealth | None:
    stmt = select(ConnectionHealth).where(ConnectionHealth.organization_id == organization_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def last_webhook_event_at(
    session: AsyncSession, organization_id: uuid.UUID
) -> datetime | None:
    """The newest thing Meta has told us for this organization: an inbound message, or a delivery
    or read receipt on one of its messages. Two aggregate reads, each on an org-leading index or a
    small filtered scan; never a row per message."""
    inbound = (
        await session.execute(
            select(func.max(InboundMessage.occurred_at)).where(
                InboundMessage.organization_id == organization_id
            )
        )
    ).scalar_one_or_none()
    receipts = (
        await session.execute(
            select(func.max(func.greatest(Message.delivered_at, Message.read_at))).where(
                Message.organization_id == organization_id, Message.channel == "whatsapp"
            )
        )
    ).scalar_one_or_none()
    candidates = [t for t in (inbound, receipts) if t is not None]
    return max(candidates) if candidates else None


async def provider_template(
    session: AsyncSession, organization_id: uuid.UUID, name: str, language: str
) -> ProviderTemplate | None:
    """The synced WhatsApp template Meta knows as `name` in `language`, if we hold it."""
    stmt = select(ProviderTemplate).where(
        ProviderTemplate.organization_id == organization_id,
        ProviderTemplate.channel == "whatsapp",
        ProviderTemplate.provider_name == name,
        ProviderTemplate.language == language,
        ProviderTemplate.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalars().first()
