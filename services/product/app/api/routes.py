"""HTTP surface for product — the marketplace, and what an organization has turned on."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors, services
from app.config import settings
from app.models import SCHEMA, Activation
from app.schemas import ActivateIn, ActivationOut, ProductOut, ServiceInfo
from platform_core.auth import Scope, ScopeContext, require
from platform_core.db import get_tenant_session

router = APIRouter(tags=["product"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

CatalogueRead = Annotated[ScopeContext, Depends(require("core.product.catalogue.read"))]
ActivationRead = Annotated[ScopeContext, Depends(require("core.product.activation.read"))]
#: Activating a product is an organization-wide act with a bill attached, so the floor is ORG
#: rather than whatever scope the caller happens to hold.
ActivationWrite = Annotated[
    ScopeContext,
    Depends(require("core.product.activation.write", minimum=Scope.ORG)),
]


@router.get("/product/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


@router.get("/products", response_model=list[ProductOut], summary="The marketplace")
async def list_products(
    session: SessionDep,
    ctx: CatalogueRead,
    available_only: Annotated[bool, Query(description="Hide beta and unreleased")] = False,
) -> list[ProductOut]:
    """Every product on the platform, not only the ones this customer has.

    That is what makes it a marketplace. `status` distinguishes what can be bought now from
    what is coming, so a client never offers an activation that would be refused.
    """
    products = await selectors.list_products(session, available_only=available_only)
    return [ProductOut.model_validate(p) for p in products]


@router.get(
    "/products/activations",
    response_model=list[ActivationOut],
    summary="What this organization has activated",
)
async def list_activations(session: SessionDep, ctx: ActivationRead) -> list[ActivationOut]:
    activations = await selectors.list_activations(session, ctx.organization_id)
    entitled_by = await selectors.live_sources_by_product(session, ctx.organization_id)
    out = []
    for activation in activations:
        row = ActivationOut.model_validate(activation)
        row.entitled_by = entitled_by.get(activation.product_key, [])
        out.append(row)
    return out


async def _with_sources(
    session: AsyncSession, ctx: ScopeContext, activation: Activation
) -> ActivationOut:
    """One activation with what currently entitles it, as the list route reports it."""
    live = await selectors.live_sources_by_product(session, ctx.organization_id)
    out = ActivationOut.model_validate(activation)
    out.entitled_by = live.get(activation.product_key, [])
    return out


@router.post(
    "/products/activations",
    response_model=ActivationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Activate a product",
)
async def activate(
    payload: ActivateIn, session: SessionDep, ctx: ActivationWrite, request: Request
) -> ActivationOut:
    activation = await services.activate(
        session,
        ctx,
        product_key=payload.product_key,
        trial_days=payload.trial_days,
        request=request,
    )
    return await _with_sources(session, ctx, activation)


@router.delete(
    "/products/activations/{product_key}",
    response_model=ActivationOut,
    summary="Deactivate a product",
)
async def deactivate(
    product_key: str, session: SessionDep, ctx: ActivationWrite, request: Request
) -> ActivationOut:
    activation = await services.deactivate(session, ctx, product_key=product_key, request=request)
    return await _with_sources(session, ctx, activation)
