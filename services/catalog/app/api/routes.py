"""Customer HTTP surface for catalog — what a customer may be sold, never at what price.

Fronted by the product gateway at `/api/v1/plans` and `/api/v1/catalog`. These are platform
surfaces, like `/api/v1/products`: a customer browsing what they could upgrade to. The one
rule that carries weight is the **bespoke-visibility filter** in `selectors._audience_ok` — a
version restricted to one organization is returned to that organization and to nobody else,
which is what a global table has instead of row-level security.

Responses use the `Tenant*` schemas, which carry no `amount_minor`/`currency` — pricing is
negotiated per organization by staff, never a list price the client reads off this API. The
admin and console-internal APIs, which do need the amount, use `PlanOut`/`PlanVersionOut`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.config import settings
from app.models import SCHEMA, Plan, PlanVersion
from app.schemas import ServiceInfo, TenantPlanOut, TenantPlanVersionOut
from platform_core.auth import ScopeContext, require
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem

router = APIRouter(tags=["catalog"])

#: The tenancy chokepoint. The definitional tables are un-policied, so this does not gate the
#: read; it binds the transaction and gives the route the caller's organization for the filter.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]

#: Browsing plans is the same act as browsing the marketplace, so it reuses the marketplace's
#: permission — held by the roles that pick and pay for a plan (owner, administrator).
CatalogueRead = Annotated[ScopeContext, Depends(require("core.product.catalogue.read"))]


def _plan_out(plan: Plan, versions: list[PlanVersion]) -> TenantPlanOut:
    return TenantPlanOut(
        id=plan.id,
        product_key=plan.product_key,
        code=plan.code,
        name=plan.name,
        state=plan.state,
        sort_order=plan.sort_order,
        versions=[TenantPlanVersionOut.model_validate(v) for v in versions],
    )


@router.get("/catalog/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


@router.get("/plans", response_model=list[TenantPlanOut], summary="Plans a customer may be sold")
async def list_plans(
    session: SessionDep,
    ctx: CatalogueRead,
    product_key: Annotated[str, Query(description="The product whose plans to list")],
) -> list[TenantPlanOut]:
    """Published plans for a product, each with the versions this organization may see.

    A public version (`restricted_to_org_id` NULL) is shown to everyone; a bespoke version is
    shown only to the organization it was negotiated for. This is the filter that replaces
    row-level security on a global table, so it is covered by a negative-authz test.
    """
    pairs = await selectors.visible_plans(
        session, product_key=product_key, org_id=ctx.organization_id
    )
    return [_plan_out(plan, versions) for plan, versions in pairs]


@router.get(
    "/catalog/plans/{plan_id}",
    response_model=TenantPlanOut,
    summary="One plan with its visible versions",
)
async def get_plan(plan_id: uuid.UUID, session: SessionDep, ctx: CatalogueRead) -> TenantPlanOut:
    result = await selectors.get_visible_plan(session, plan_id, org_id=ctx.organization_id)
    if result is None:
        raise NotFoundProblem("No such plan")
    plan, versions = result
    return _plan_out(plan, versions)
