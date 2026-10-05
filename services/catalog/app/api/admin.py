"""Operator HTTP surface for catalog — reading and authoring plans, versions and prices.

Fronted by the **operator** gateway at `/api/v1/admin/catalog`, which is `staff_only`: a
non-staff caller is refused with the same 404 an unknown path gets, so the existence of the
operator surface is never confirmed to whoever is probing (mirrors admin-gateway's staff
routes). This module is defence in depth behind that gate:

* `platform_core.auth.require_operator` refuses any token that did not arrive through the
  operator gateway — it checks the audience the operator gateway stamps, and returns **404**,
  not 403, for the same reason the gateway does. A stolen product (`app`-audience) token is
  inert here. A token whose session never proved its second factor gets 403.
* **Reads** (`GET`) are `owner` and `billing_ops`. Not `support`: the catalogue includes every
  audience-of-one version, so reading it is reading every customer's negotiated price.
* **Writes** are `owner`-only (ADR-0003 addendum): authoring the commercial catalogue that prices
  every customer is not something `billing_ops` or `support` may do, so a caller with the right
  audience but the wrong role gets a plain 403 rather than the 404 the audience check gives.
  Every write takes a `reason` (kept on the audit row) and a **required** `Idempotency-Key`
  claimed before the change (`services.claim_idempotency_key`): a retried request replays the
  first answer, the same key on a different request is a 409, and no key is a 422.
* Authoring is a **platform** act, not a tenant one, so the session dependencies bind the
  transaction to the platform sentinel organization rather than to the caller's own. The
  definitional tables are un-policied and do not need it; catalog's `audit_logs`, `outbox` and
  `idempotency_keys` do, and this is the tenant those platform-level rows belong to (ADR-0002 §6).

Spec: docs/superpowers/specs/2026-10-06-admin-plans-page-design.md; decision: ADR-0029.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors, services
from app.models import PLATFORM_ORG, Plan
from app.registry import ProductRegistry
from app.schemas import (
    PRODUCT_KEY_PATTERN,
    AdminPlanCreateIn,
    AdminPlanPatchIn,
    AdminVersionCreateIn,
    Audience,
    DeletedVersionOut,
    PlanDetailOut,
    PlanListItemOut,
    PlanListOut,
    PlanVersionOut,
    PublishIn,
    ReasonIn,
    VersionCountsOut,
    VersionSummaryOut,
)
from platform_core.auth import Principal, require_operator
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant

router = APIRouter(prefix="/admin/catalog", tags=["catalog-admin"])

#: Catalog writes — plans, versions, publishing — are `owner`-only (ADR-0003 addendum): the
#: commercial catalogue prices every customer on the platform, so `billing_ops` and `support`
#: do not get a pen to write with here.
OperatorDep = Annotated[Principal, Depends(require_operator(roles={"owner"}))]

#: Catalog reads are `owner` and `billing_ops` (owner decision, 2026-10-06). `support` is left
#: out deliberately: the catalogue holds every customer's negotiated (bespoke) price.
OperatorReadDep = Annotated[Principal, Depends(require_operator(roles={"owner", "billing_ops"}))]

#: Required on every write. 8 to 128 characters — the console sends a UUID per dialog open.
IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        description="Required. Retrying with the same key replays the first answer (with an "
        "`Idempotent-Replayed: true` header) instead of changing anything twice; reusing it for "
        "a different request is a 409. Only a change that happened is recorded — a refused "
        "request can be retried under the same key.",
    ),
]

#: The header a replayed answer carries, so a client (and a log line) can tell a replay apart.
REPLAYED_HEADER = "Idempotent-Replayed"


@asynccontextmanager
async def _platform_transaction(session: AsyncSession, request: Request) -> AsyncIterator[None]:
    """The request's one transaction, bound to the platform sentinel. A context manager, so an
    exception from the route reaches `session.begin()` and rolls everything back with it."""
    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, PLATFORM_ORG)
        if not settings.is_production:
            await assert_tenant(session, PLATFORM_ORG)
        yield


async def get_operator_session(
    principal: OperatorDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> AsyncIterator[AsyncSession]:
    """Open the request's transaction bound to the platform sentinel organization.

    Not `get_tenant_session`: that binds to the caller's own organization, and a staff operator
    is not acting inside one tenant. The definitional tables are un-policied so the binding does
    not gate them; it exists so catalog's policied `audit_logs`/`outbox`/`idempotency_keys` rows
    — which record a platform act — satisfy their `WITH CHECK` under the same sentinel.
    """
    async with _platform_transaction(session, request):
        yield session


async def get_operator_read_session(
    principal: OperatorReadDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> AsyncIterator[AsyncSession]:
    """The same binding for the owner-and-billing_ops reads. Nothing is written through it."""
    async with _platform_transaction(session, request):
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_operator_session, scope="function")]
ReadSessionDep = Annotated[AsyncSession, Depends(get_operator_read_session, scope="function")]


def get_product_registry(request: Request) -> ProductRegistry:
    """The product-registry client, assembled once in the app lifespan. A dependency so a test
    can substitute a fake and never make a real HTTP call."""
    return request.app.state.product_registry  # type: ignore[no-any-return]


RegistryDep = Annotated[ProductRegistry, Depends(get_product_registry)]

ProductKeyQuery = Annotated[str | None, Query(pattern=PRODUCT_KEY_PATTERN)]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]


# --------------------------------------------------------------------- shaping


async def _plan_rows(session: AsyncSession, plans: list[Plan]) -> list[PlanListItemOut]:
    ids = [p.id for p in plans]
    counts = await selectors.version_counts(session, ids)
    latest = await selectors.latest_version_numbers(session, ids)
    current = await selectors.current_public_versions(session, ids)
    return [
        PlanListItemOut(
            id=p.id,
            product_key=p.product_key,
            code=p.code,
            name=p.name,
            state=p.state,
            sort_order=p.sort_order,
            created_at=p.created_at,
            counts=VersionCountsOut(**counts.get(p.id, {})),
            current=PlanVersionOut.model_validate(current[p.id]) if p.id in current else None,
            latest_version_no=latest.get(p.id, 0),
        )
        for p in plans
    ]


async def _plan_row(session: AsyncSession, plan: Plan) -> PlanListItemOut:
    await session.refresh(plan)
    (row,) = await _plan_rows(session, [plan])
    return row


async def _version_out(session: AsyncSession, version_id: uuid.UUID) -> PlanVersionOut:
    fresh = await services.require_version(session, version_id)
    return PlanVersionOut.model_validate(fresh)


async def _idempotent(
    session: AsyncSession,
    operator: Principal,
    *,
    key: str,
    action: str,
    target: uuid.UUID | None,
    body: BaseModel,
    status_code: int,
    work: Callable[[], Awaitable[BaseModel]],
) -> Any:
    """Claim the key, do the work once, record its answer; or replay the recorded answer."""
    fingerprint = domain.request_fingerprint(
        action, str(target) if target is not None else None, body.model_dump(mode="json")
    )
    claim = await services.claim_idempotency_key(
        session, operator, key=key, action=action, fingerprint=fingerprint
    )
    if claim.claim_id is None:
        return JSONResponse(
            content=claim.replay_body,
            status_code=claim.replay_status or status.HTTP_200_OK,
            headers={REPLAYED_HEADER: "true"},
        )
    result = await work()
    await services.complete_idempotency_key(
        session, claim.claim_id, status_code=status_code, response=result.model_dump(mode="json")
    )
    return result


# --------------------------------------------------------------------- reads (owner, billing_ops)


@router.get(
    "/plans",
    response_model=PlanListOut,
    summary="List plans, one keyset page at a time (public audience by default)",
)
async def list_plans(
    session: ReadSessionDep,
    _operator: OperatorReadDep,
    product_key: ProductKeyQuery = None,
    audience: Audience = "public",
    state: Literal["active", "retired"] | None = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> PlanListOut:
    plans, next_cursor = await selectors.list_plans_page(
        session,
        product_key=product_key,
        audience=audience,
        state=state,
        cursor=cursor,
        limit=limit,
    )
    return PlanListOut(items=await _plan_rows(session, plans), next_cursor=next_cursor)


@router.get(
    "/plans/{plan_id}",
    response_model=PlanDetailOut,
    summary="One plan with a page of its versions (summaries; open a version for its sheet)",
)
async def get_plan(
    plan_id: uuid.UUID,
    session: ReadSessionDep,
    _operator: OperatorReadDep,
    audience: Audience = "public",
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> PlanDetailOut:
    plan = await services.require_plan(session, plan_id)
    counts = await selectors.version_counts(session, [plan.id])
    latest = await selectors.latest_version_numbers(session, [plan.id])
    versions, next_cursor = await selectors.version_summaries_page(
        session, plan.id, audience=audience, cursor=cursor, limit=limit
    )
    return PlanDetailOut(
        id=plan.id,
        product_key=plan.product_key,
        code=plan.code,
        name=plan.name,
        state=plan.state,
        sort_order=plan.sort_order,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        counts=VersionCountsOut(**counts.get(plan.id, {})),
        latest_version_no=latest.get(plan.id, 0),
        versions=[VersionSummaryOut.model_validate(v) for v in versions],
        next_cursor=next_cursor,
    )


@router.get(
    "/versions/{version_id}",
    response_model=PlanVersionOut,
    summary="One version with its full sheet: prices, features and seat classes",
)
async def get_version(
    version_id: uuid.UUID, session: ReadSessionDep, _operator: OperatorReadDep
) -> PlanVersionOut:
    return await _version_out(session, version_id)


# --------------------------------------------------------------------- writes (owner only)


@router.post(
    "/plans",
    response_model=PlanListItemOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a plan under a registered product",
)
async def create_plan(
    payload: AdminPlanCreateIn,
    session: SessionDep,
    operator: OperatorDep,
    registry: RegistryDep,
    idempotency_key: IdempotencyKeyHeader,
    request: Request,
) -> Any:
    async def work() -> PlanListItemOut:
        plan = await services.create_plan(
            session, operator, payload, request=request, reason=payload.reason, registry=registry
        )
        return await _plan_row(session, plan)

    return await _idempotent(
        session,
        operator,
        key=idempotency_key,
        action="plan.create",
        target=None,
        body=payload,
        status_code=status.HTTP_201_CREATED,
        work=work,
    )


@router.patch(
    "/plans/{plan_id}",
    response_model=PlanListItemOut,
    summary="Edit a plan's name or ordering (never its prices — that is a new version)",
)
async def update_plan(
    plan_id: uuid.UUID,
    payload: AdminPlanPatchIn,
    session: SessionDep,
    operator: OperatorDep,
    idempotency_key: IdempotencyKeyHeader,
    request: Request,
) -> Any:
    async def work() -> PlanListItemOut:
        plan = await services.require_plan_for_update(session, plan_id)
        plan = await services.update_plan(
            session, operator, plan, payload, request=request, reason=payload.reason
        )
        return await _plan_row(session, plan)

    return await _idempotent(
        session,
        operator,
        key=idempotency_key,
        action="plan.update",
        target=plan_id,
        body=payload,
        status_code=status.HTTP_200_OK,
        work=work,
    )


@router.post(
    "/plans/{plan_id}/retire",
    response_model=PlanListItemOut,
    summary="Retire a plan: stop offering it; existing pins keep billing",
)
async def retire_plan(
    plan_id: uuid.UUID,
    payload: ReasonIn,
    session: SessionDep,
    operator: OperatorDep,
    idempotency_key: IdempotencyKeyHeader,
    request: Request,
) -> Any:
    async def work() -> PlanListItemOut:
        plan = await services.require_plan_for_update(session, plan_id)
        plan = await services.retire_plan(
            session, operator, plan, request=request, reason=payload.reason
        )
        return await _plan_row(session, plan)

    return await _idempotent(
        session,
        operator,
        key=idempotency_key,
        action="plan.retire",
        target=plan_id,
        body=payload,
        status_code=status.HTTP_200_OK,
        work=work,
    )


@router.post(
    "/plans/{plan_id}/reactivate",
    response_model=PlanListItemOut,
    summary="Reactivate a retired plan: offer it again",
)
async def reactivate_plan(
    plan_id: uuid.UUID,
    payload: ReasonIn,
    session: SessionDep,
    operator: OperatorDep,
    idempotency_key: IdempotencyKeyHeader,
    request: Request,
) -> Any:
    async def work() -> PlanListItemOut:
        plan = await services.require_plan_for_update(session, plan_id)
        plan = await services.reactivate_plan(
            session, operator, plan, request=request, reason=payload.reason
        )
        return await _plan_row(session, plan)

    return await _idempotent(
        session,
        operator,
        key=idempotency_key,
        action="plan.reactivate",
        target=plan_id,
        body=payload,
        status_code=status.HTTP_200_OK,
        work=work,
    )


@router.post(
    "/plans/{plan_id}/versions",
    response_model=PlanVersionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Draft a new plan version from a full sheet (bespoke when restricted_to_org_id set)",
)
async def create_version(
    plan_id: uuid.UUID,
    payload: AdminVersionCreateIn,
    session: SessionDep,
    operator: OperatorDep,
    registry: RegistryDep,
    idempotency_key: IdempotencyKeyHeader,
    request: Request,
) -> Any:
    async def work() -> PlanVersionOut:
        plan = await services.require_plan(session, plan_id)
        version = await services.draft_version(
            session,
            operator,
            plan,
            payload,
            request=request,
            registry=registry,
            reason=payload.reason,
        )
        return await _version_out(session, version.id)

    return await _idempotent(
        session,
        operator,
        key=idempotency_key,
        action="version.create",
        target=plan_id,
        body=payload,
        status_code=status.HTTP_201_CREATED,
        work=work,
    )


@router.post(
    "/versions/{version_id}/publish",
    response_model=PlanVersionOut,
    summary="Publish a draft version — validated, then immutable for good",
)
async def publish_version(
    version_id: uuid.UUID,
    payload: PublishIn,
    session: SessionDep,
    operator: OperatorDep,
    registry: RegistryDep,
    idempotency_key: IdempotencyKeyHeader,
    request: Request,
) -> Any:
    async def work() -> PlanVersionOut:
        version = await services.require_version_for_update(session, version_id)
        version = await services.publish_version(
            session,
            operator,
            version,
            request=request,
            reason=payload.reason,
            registry=registry,
            allow_zero_amounts=payload.allow_zero_amounts,
        )
        return await _version_out(session, version.id)

    return await _idempotent(
        session,
        operator,
        key=idempotency_key,
        action="version.publish",
        target=version_id,
        body=payload,
        status_code=status.HTTP_200_OK,
        work=work,
    )


@router.post(
    "/versions/{version_id}/withdraw",
    response_model=PlanVersionOut,
    summary="Withdraw a published version (published -> grandfathered); existing pins keep it",
)
async def withdraw_version(
    version_id: uuid.UUID,
    payload: ReasonIn,
    session: SessionDep,
    operator: OperatorDep,
    idempotency_key: IdempotencyKeyHeader,
    request: Request,
) -> Any:
    async def work() -> PlanVersionOut:
        version = await services.require_version_for_update(session, version_id)
        version = await services.withdraw_version(
            session, operator, version, request=request, reason=payload.reason
        )
        return await _version_out(session, version.id)

    return await _idempotent(
        session,
        operator,
        key=idempotency_key,
        action="version.withdraw",
        target=version_id,
        body=payload,
        status_code=status.HTTP_200_OK,
        work=work,
    )


@router.delete(
    "/versions/{version_id}",
    response_model=DeletedVersionOut,
    summary="Delete a draft version (never a published or grandfathered one)",
)
async def delete_draft_version(
    version_id: uuid.UUID,
    payload: ReasonIn,
    session: SessionDep,
    operator: OperatorDep,
    idempotency_key: IdempotencyKeyHeader,
    request: Request,
) -> Any:
    async def work() -> DeletedVersionOut:
        version = await services.require_version_for_update(session, version_id)
        gone = await services.delete_draft_version(
            session, operator, version, request=request, reason=payload.reason
        )
        return DeletedVersionOut(**gone)

    return await _idempotent(
        session,
        operator,
        key=idempotency_key,
        action="version.delete",
        target=version_id,
        body=payload,
        status_code=status.HTTP_200_OK,
        work=work,
    )
