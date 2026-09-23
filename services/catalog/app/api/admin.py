"""Operator HTTP surface for catalog — authoring plans, versions and prices.

Fronted by the **operator** gateway at `/api/v1/admin/catalog`, which is `staff_only`: a
non-staff caller is refused with the same 404 an unknown path gets, so the existence of the
operator surface is never confirmed to whoever is probing (mirrors admin-gateway's staff
routes). This module is defence in depth behind that gate:

* `platform_core.auth.require_operator` refuses any token that did not arrive through the
  operator gateway — it checks the audience the operator gateway stamps, and returns **404**,
  not 403, for the same reason the gateway does. A stolen product (`app`-audience) token is
  inert here. Every route below additionally demands the `owner` operator role (ADR-0003
  addendum): authoring the commercial catalogue that prices every customer is not something
  `billing_ops` or `support` may do, so a caller with the right audience but the wrong role
  gets a plain 403 rather than the 404 the audience check gives.
* Authoring is a **platform** act, not a tenant one, so `get_operator_session` binds the
  transaction to the platform sentinel organization rather than to the caller's own. The
  definitional tables are un-policied and do not need it; catalog's `audit_logs` and `outbox`
  do, and this is the tenant those platform-level rows belong to (ADR-0002 §6).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors, services
from app.models import PLATFORM_ORG, Plan, PlanVersion
from app.registry import ProductRegistry
from app.schemas import PlanCreateIn, PlanOut, PlanPatchIn, PlanVersionOut, VersionCreateIn
from platform_core.auth import Principal, require_operator
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant

router = APIRouter(prefix="/admin/catalog", tags=["catalog-admin"])

#: Catalog writes — plans, versions, publishing — are `owner`-only (ADR-0003 addendum): the
#: commercial catalogue prices every customer on the platform, so `billing_ops` and `support`
#: get read access elsewhere but not a pen to write with here.
OperatorDep = Annotated[Principal, Depends(require_operator(roles={"owner"}))]


async def get_operator_session(
    principal: OperatorDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> AsyncIterator[AsyncSession]:
    """Open the request's transaction bound to the platform sentinel organization.

    Not `get_tenant_session`: that binds to the caller's own organization, and a staff operator
    is not acting inside one tenant. The definitional tables are un-policied so the binding does
    not gate them; it exists so catalog's policied `audit_logs`/`outbox` rows — which record a
    platform act — satisfy their `WITH CHECK` under the same sentinel they are stamped with.
    """
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, PLATFORM_ORG)
        if not settings.is_production:
            await assert_tenant(session, PLATFORM_ORG)
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_operator_session)]


def get_product_registry(request: Request) -> ProductRegistry:
    """The product-registry client, assembled once in the app lifespan. A dependency so a test
    can substitute a fake and never make a real HTTP call."""
    return request.app.state.product_registry  # type: ignore[no-any-return]


RegistryDep = Annotated[ProductRegistry, Depends(get_product_registry)]


def _plan_out(plan: Plan, versions: list[PlanVersion]) -> PlanOut:
    return PlanOut(
        id=plan.id,
        product_key=plan.product_key,
        code=plan.code,
        name=plan.name,
        state=plan.state,
        sort_order=plan.sort_order,
        versions=[PlanVersionOut.model_validate(v) for v in versions],
    )


@router.post(
    "/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED, summary="Create a plan"
)
async def create_plan(
    payload: PlanCreateIn, session: SessionDep, operator: OperatorDep, request: Request
) -> PlanOut:
    plan = await services.create_plan(session, operator, payload, request=request)
    return _plan_out(plan, [])


@router.patch("/plans/{plan_id}", response_model=PlanOut, summary="Edit a plan's name or ordering")
async def update_plan(
    plan_id: uuid.UUID,
    payload: PlanPatchIn,
    session: SessionDep,
    operator: OperatorDep,
    request: Request,
) -> PlanOut:
    plan = await services.require_plan(session, plan_id)
    plan = await services.update_plan(session, operator, plan, payload, request=request)
    versions = await selectors.get_plan_versions(session, plan.id)
    return _plan_out(plan, versions)


@router.post("/plans/{plan_id}/retire", response_model=PlanOut, summary="Retire a plan")
async def retire_plan(
    plan_id: uuid.UUID, session: SessionDep, operator: OperatorDep, request: Request
) -> PlanOut:
    plan = await services.require_plan(session, plan_id)
    plan = await services.retire_plan(session, operator, plan, request=request)
    versions = await selectors.get_plan_versions(session, plan.id)
    return _plan_out(plan, versions)


@router.post(
    "/plans/{plan_id}/versions",
    response_model=PlanVersionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Draft a new plan version (clone-to-draft; bespoke when restricted_to_org_id set)",
)
async def create_version(
    plan_id: uuid.UUID,
    payload: VersionCreateIn,
    session: SessionDep,
    operator: OperatorDep,
    registry: RegistryDep,
    request: Request,
) -> PlanVersionOut:
    plan = await services.require_plan(session, plan_id)
    version = await services.create_version(
        session, operator, plan, payload, request=request, registry=registry
    )
    fresh = await selectors.get_version(session, version.id)
    return PlanVersionOut.model_validate(fresh)


@router.post(
    "/versions/{version_id}/publish",
    response_model=PlanVersionOut,
    summary="Publish a draft version — from here it is immutable",
)
async def publish_version(
    version_id: uuid.UUID, session: SessionDep, operator: OperatorDep, request: Request
) -> PlanVersionOut:
    version = await services.require_version(session, version_id)
    version = await services.publish_version(session, operator, version, request=request)
    fresh = await selectors.get_version(session, version.id)
    return PlanVersionOut.model_validate(fresh)
