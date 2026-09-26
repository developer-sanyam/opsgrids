"""Reads for WhatsApp templates. Every one filters on the organization, and the list one injects
the caller's scope through `ctx.apply()` like every list in `selectors.py`."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.models import ProviderAccount, ProviderTemplate
from platform_core.auth import ScopeContext

CHANNEL = "whatsapp"


def _like_pattern(fragment: str) -> str:
    """`fragment` as a contains-pattern with LIKE's own wildcards neutralised, so a search for
    `50%_off` finds that text instead of everything."""
    escaped = fragment.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def templates_stmt(
    ctx: ScopeContext,
    *,
    channel: str | None = CHANNEL,
    category: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> Select[Any]:
    stmt = select(ProviderTemplate).where(
        ProviderTemplate.organization_id == ctx.organization_id,
        ProviderTemplate.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=ProviderTemplate.created_by)
    if channel:
        stmt = stmt.where(ProviderTemplate.channel == channel)
    if category:
        stmt = stmt.where(ProviderTemplate.category == category)
    if status:
        stmt = stmt.where(ProviderTemplate.status == status)
    if q and q.strip():
        pattern = _like_pattern(q.strip()[:100])
        stmt = stmt.where(
            or_(
                ProviderTemplate.code.ilike(pattern, escape="\\"),
                ProviderTemplate.provider_name.ilike(pattern, escape="\\"),
                ProviderTemplate.preview_text.ilike(pattern, escape="\\"),
            )
        )
    return stmt.order_by(ProviderTemplate.code.asc(), ProviderTemplate.id.asc())


async def get_by_ref(session: AsyncSession, ctx: ScopeContext, ref: str) -> ProviderTemplate | None:
    """A template by the `id` the list returns (its code) or by its row uuid. Scoped to the
    caller: another organization's template is `None`, exactly like one that does not exist.

    A code is `[a-z0-9_]+` and a uuid has hyphens, so the two can never be confused.
    """
    stmt = templates_stmt(ctx)
    try:
        stmt = stmt.where(ProviderTemplate.id == uuid.UUID(ref))
    except ValueError:
        stmt = stmt.where(ProviderTemplate.code == ref)
    return (await session.execute(stmt.limit(1))).scalar_one_or_none()


async def all_whatsapp_rows(session: AsyncSession, org_id: uuid.UUID) -> list[ProviderTemplate]:
    """Every WhatsApp template row of the organization, **including soft-deleted ones**: the
    unique `(organization_id, code, channel)` still counts them, and a template that reappears at
    Meta should get its old row (and the rules pointing at its code) back."""
    stmt = select(ProviderTemplate).where(
        ProviderTemplate.organization_id == org_id, ProviderTemplate.channel == CHANNEL
    )
    return list((await session.execute(stmt)).scalars().all())


async def find_for_change(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    meta_template_id: str | None,
    name: str | None,
    language: str | None,
) -> ProviderTemplate | None:
    """The row a webhook is about, by Meta's id first and (name, language) second, locked for the
    update. Only ever this organization's."""
    base = select(ProviderTemplate).where(
        ProviderTemplate.organization_id == org_id,
        ProviderTemplate.channel == CHANNEL,
        ProviderTemplate.deleted_at.is_(None),
    )
    if meta_template_id:
        row = (
            await session.execute(
                base.where(ProviderTemplate.provider_template_id == meta_template_id)
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is not None:
            return row
    if name and language:
        return (
            await session.execute(
                base.where(
                    ProviderTemplate.provider_name == name, ProviderTemplate.language == language
                )
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
    return None


async def organizations_for_waba(directory: AsyncSession, waba_id: str) -> list[uuid.UUID]:
    """Every organization whose WhatsApp account names this WABA — on the cross-tenant,
    SELECT-only directory reader, because a webhook must be routed to a tenant *before* one is
    known. An unknown WABA is an empty list, and nothing is touched."""
    stmt = (
        select(ProviderAccount.organization_id)
        .where(
            ProviderAccount.waba_id == waba_id,
            ProviderAccount.channel == CHANNEL,
            ProviderAccount.deleted_at.is_(None),
        )
        .distinct()
    )
    return list((await directory.execute(stmt)).scalars().all())
