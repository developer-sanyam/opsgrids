"""Reads for the product service.

`entitlements_for` is the one that matters. Everything the gateway does with a 402 traces back
to the list it returns, and auth-service stamps that list into every token it mints.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activation, ActivationSource, Product


def products_stmt(*, include_unavailable: bool = True) -> Select[Any]:
    """The marketplace. Global — every organization sees the whole catalogue.

    That is the point of a marketplace: it lists what you do not have yet. Filtering it to
    what a customer already owns would leave nothing to discover.
    """
    stmt = select(Product).order_by(Product.sort_order, Product.name)
    if not include_unavailable:
        stmt = stmt.where(Product.status == "available")
    return stmt


async def list_products(session: AsyncSession, *, available_only: bool = False) -> list[Product]:
    stmt = products_stmt(include_unavailable=not available_only)
    return list((await session.execute(stmt)).scalars().all())


async def get_product(session: AsyncSession, key: str) -> Product | None:
    return (await session.execute(select(Product).where(Product.key == key))).scalar_one_or_none()


async def list_activations(session: AsyncSession, org_id: uuid.UUID) -> list[Activation]:
    """Every activation this organization has ever had, deactivated ones included.

    Deliberately: "you used to have this" is what lets a client offer reactivation rather than
    presenting a product the customer already configured once as though it were new.
    """
    stmt = (
        select(Activation)
        .where(Activation.organization_id == org_id)
        .order_by(Activation.product_key)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_activation(
    session: AsyncSession, org_id: uuid.UUID, product_key: str
) -> Activation | None:
    stmt = select(Activation).where(
        Activation.organization_id == org_id, Activation.product_key == product_key
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def entitlements_for(session: AsyncSession, org_id: uuid.UUID) -> list[str]:
    """Product keys this organization may currently use.

    Called by auth-service when it mints a token, so this list becomes the `ent` claim and,
    one hop later, the difference between a 200 and a 402 at the gateway.

    Expiry is evaluated per row rather than trusted from `status`, so a trial that lapsed a
    minute ago stops entitling now rather than whenever some nightly job next runs. Platform
    products are always included: every customer has an organization and a profile whatever
    they bought, and the gateway treats a route with no product the same way.
    """
    activations = await list_activations(session, org_id)
    live = {a.product_key for a in activations if a.entitles}

    platform = set(
        (await session.execute(select(Product.key).where(Product.is_platform.is_(True))))
        .scalars()
        .all()
    )
    return sorted(live | platform)


# ------------------------------------------------------------------ activation sources


async def sources_for(
    session: AsyncSession, org_id: uuid.UUID, product_key: str, *, fresh: bool = False
) -> list[ActivationSource]:
    """Every source of one activation — what `_recompute` ORs together. `fresh` overwrites rows
    this session has already loaded, for a read taken after waiting on the activation lock, which
    must see what committed while it waited (the hand-activation gate)."""
    stmt = (
        select(ActivationSource)
        .where(
            ActivationSource.organization_id == org_id,
            ActivationSource.product_key == product_key,
        )
        .order_by(ActivationSource.source_product_key)
    )
    if fresh:
        stmt = stmt.execution_options(populate_existing=True)
    return list((await session.execute(stmt)).scalars().all())


async def sources_from(
    session: AsyncSession, org_id: uuid.UUID, source_product_key: str, *, fresh: bool = False
) -> list[ActivationSource]:
    """Every activation one subscription feeds, tombstones included — the projection's diff and
    its per-source ordering watermark both read this. `fresh` overwrites rows this session has
    already loaded, for the read taken after the locks, which must see what committed while it
    waited."""
    stmt = (
        select(ActivationSource)
        .where(
            ActivationSource.organization_id == org_id,
            ActivationSource.source_product_key == source_product_key,
        )
        .order_by(ActivationSource.product_key)
    )
    if fresh:
        stmt = stmt.execution_options(populate_existing=True)
    return list((await session.execute(stmt)).scalars().all())


async def live_sources_by_product(session: AsyncSession, org_id: uuid.UUID) -> dict[str, list[str]]:
    """For each activated product, the keys of the sources currently entitling it.

    What lets a client say "included in your Suite" rather than offering to sell a product the
    customer already has through a bundle. One query for the whole organization, filtered on it
    explicitly — RLS is the net, not the licence to skip the filter.
    """
    stmt = (
        select(ActivationSource)
        .where(ActivationSource.organization_id == org_id)
        .order_by(ActivationSource.product_key, ActivationSource.source_product_key)
    )
    live: dict[str, list[str]] = {}
    for source in (await session.execute(stmt)).scalars().all():
        if source.is_live:
            live.setdefault(source.product_key, []).append(source.source_product_key)
    return live
