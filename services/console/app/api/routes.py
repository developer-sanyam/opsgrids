"""The operator staff read API — cross-tenant views, and overrides that delegate.

Fronted by the **operator** gateway at `/api/v1/admin/*`, every route `staff_only`: a non-staff
caller is refused with the same 404 an unknown path gets, and a product-audience token is rejected
at signature-verification time before any route is consulted (ADR-0003 §2). This module is defence
in depth behind that gate:

* `platform_core.auth.require_operator(roles={...})` refuses any token that did not arrive through
  the operator gateway — it checks the audience the operator gateway stamps and returns **404**,
  not 403, for the same reason the gateway does. A stolen product token is inert here even if it
  somehow reached this port. A caller who *is* staff but lacks the role a given route demands gets a
  plain **403** instead — the ADR-0003 addendum's role matrix, applied per route below because "any
  operator" stopped being fine-grained enough once overrides, catalog authoring and the audit trail
  all sat behind the same audience gate.
* The reads run on the **directory session**: BYPASSRLS, SELECT-only, resolving every organization's
  projections at once. There is no single tenant to bind, so they cannot run on the application role
  (which reads nothing without a tenant GUC) — the cross-tenant reader ADR-0002 §2/§3 created.
* An override never writes another service's schema. It delegates the write to the owning service's
  internal API under a service token, then records console's own audit row bound to the target
  organization (ADR-0003 §2). The org-detail composite is a different shape again: it forwards the
  caller's *own* token to account-service's staff-only directory route rather than minting a service
  credential of its own (`app/directory_client.py`), and records an audit row for the read itself.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import delegation, directory_client, selectors, services
from app.config import settings
from app.delegation import DelegationError
from app.directory_client import DirectoryLookupError
from app.models import SCHEMA
from app.schemas import (
    AuditListOut,
    AuditOut,
    LifecycleCountsOut,
    MetricsOut,
    MetricsOverviewOut,
    OrganizationsOut,
    OrgDetailOut,
    OrgStateOut,
    OverrideIn,
    OverrideOut,
    ServiceInfo,
    SubscriptionOut,
    SubscriptionsOut,
    UsageListOut,
    UsageOut,
)
from platform_core.auth import Principal, require_operator
from platform_core.db import get_directory_session, get_session
from platform_core.errors import (
    NotFoundProblem,
    UnauthorizedProblem,
    UpstreamUnavailableProblem,
    ValidationProblem,
)

router = APIRouter(tags=["console"])

#: The ADR-0003 addendum's role matrix, named once per read shape rather than repeated per route.
#: `owner` holds everything; every set below includes it.
OperatorReadAny = Annotated[
    Principal, Depends(require_operator(roles={"owner", "billing_ops", "support"}))
]
OperatorBilling = Annotated[Principal, Depends(require_operator(roles={"owner", "billing_ops"}))]
OperatorAudit = Annotated[Principal, Depends(require_operator(roles={"owner", "support"}))]

#: Cross-tenant reader: BYPASSRLS, SELECT-only, no tenant binding — the operator sees every org.
DirectorySessionDep = Annotated[AsyncSession, Depends(get_directory_session)]

#: The app-role session an override's or a detail-read's local audit write runs on (bound to the
#: target org inside `services.record_override` / `services.record_organization_read`). Not the
#: directory session: that one is SELECT-only.
AppSessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_delegation_client() -> AsyncIterator[httpx.AsyncClient]:
    """The HTTP client an override delegates through, and the org-detail composite calls
    account-service on. Overridden in tests with a mock transport."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        yield client


DelegationClientDep = Annotated[httpx.AsyncClient, Depends(get_delegation_client)]


@router.get("/console/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


@router.get(
    "/admin/organizations",
    response_model=OrganizationsOut,
    summary="Every organization's commercial state, with platform MRR/trial/churn metrics",
)
async def list_organizations(
    session: DirectorySessionDep, operator: OperatorReadAny
) -> OrganizationsOut:
    states = await selectors.all_org_states(session)
    metrics = await selectors.platform_metrics(session)
    return OrganizationsOut(
        organizations=[OrgStateOut.model_validate(s) for s in states],
        metrics=MetricsOut(**metrics),
    )


@router.get(
    "/admin/organizations/{organization_id}",
    response_model=OrgDetailOut,
    summary="One organization's detail — commercial projection composed with directory identity",
)
async def get_organization_detail(
    organization_id: uuid.UUID,
    request: Request,
    session: DirectorySessionDep,
    app_session: AppSessionDep,
    client: DelegationClientDep,
    operator: OperatorReadAny,
) -> OrgDetailOut:
    """Compose console's own commercial snapshot with account-service's directory record.

    The directory half is fetched from account-service's `staff_only` route, authorized by
    *this request's own token* forwarded verbatim — never `/internal/v1`, which no individual
    caller's permissions gate at all (`app/directory_client.py`). Every read here writes an
    audit row bound to the target organization, per the ADR-0003 addendum: an operator looking
    at one customer's detail is exactly the kind of act that must leave a trace.
    """
    authorization = request.headers.get("authorization")
    if not authorization:
        # Cannot happen through the gateway (it never strips this header) or a direct bearer
        # call — only if something stripped it upstream. Refused rather than silently
        # delegating with no credential.
        raise UnauthorizedProblem("Missing bearer token")

    try:
        directory = await directory_client.get_organization(
            settings(), organization_id, authorization=authorization, client=client
        )
    except DirectoryLookupError as exc:
        raise UpstreamUnavailableProblem(str(exc)) from exc
    if directory is None:
        raise NotFoundProblem("No such organization")

    commercial = await selectors.get_org_state(session, organization_id)

    await services.record_organization_read(
        app_session,
        organization_id=organization_id,
        actor_id=operator.sub,
        operator_label=f"operator:{operator.sub}",
        request=request,
    )

    return OrgDetailOut(
        organization_id=organization_id,
        slug=str(directory.get("slug", "")),
        name=str(directory.get("name", "")),
        status=str(directory.get("status", "unknown")),
        country=directory.get("country"),
        created_at=directory["created_at"],
        commercial=OrgStateOut.model_validate(commercial) if commercial is not None else None,
    )


@router.get(
    "/admin/metrics/overview",
    response_model=MetricsOverviewOut,
    summary="Platform metrics: org counts by lifecycle, MRR, trials and churn",
)
async def metrics_overview(
    session: DirectorySessionDep, operator: OperatorBilling
) -> MetricsOverviewOut:
    metrics = await selectors.platform_metrics(session)
    lifecycle = await selectors.lifecycle_counts(session)
    return MetricsOverviewOut(
        **metrics,
        organization_count=sum(lifecycle.values()),
        organizations_by_lifecycle=LifecycleCountsOut(**lifecycle),
    )


@router.get(
    "/admin/subscriptions",
    response_model=SubscriptionsOut,
    summary="Every organization's subscriptions, flattened from their snapshots",
)
async def list_subscriptions(
    session: DirectorySessionDep, operator: OperatorBilling
) -> SubscriptionsOut:
    states = await selectors.all_org_states(session)
    rows: list[SubscriptionOut] = []
    for state in states:
        for product_key, entry in sorted(state.subscription_states.items()):
            rows.append(
                SubscriptionOut(
                    organization_id=state.organization_id,
                    product_key=product_key,
                    state=str(entry.get("state")),
                    plan_version_id=entry.get("plan_version_id"),
                    amount_minor=int(entry.get("amount_minor", 0)),
                    currency=entry.get("currency"),
                    trial_ends_at=entry.get("trial_ends_at"),
                )
            )
    return SubscriptionsOut(subscriptions=rows)


@router.get(
    "/admin/usage",
    response_model=UsageListOut,
    summary="Every organization's usage rollups, by meter and period",
)
async def list_usage(session: DirectorySessionDep, operator: OperatorBilling) -> UsageListOut:
    rows = await selectors.all_usage(session)
    return UsageListOut(usage=[UsageOut.model_validate(r) for r in rows])


@router.get(
    "/admin/audit",
    response_model=AuditListOut,
    summary="Recent operator-override audit rows across every organization",
)
async def list_audit(session: DirectorySessionDep, operator: OperatorAudit) -> AuditListOut:
    rows = await selectors.recent_audit(session)
    return AuditListOut(audit=[AuditOut.model_validate(r) for r in rows])


@router.post(
    "/admin/overrides",
    response_model=OverrideOut,
    summary="Apply an operator override — delegated to the owning service, never written here",
)
async def create_override(
    payload: OverrideIn,
    operator: OperatorBilling,
    session: AppSessionDep,
    client: DelegationClientDep,
    request: Request,
) -> OverrideOut:
    """Suspend a subscription or grant comp credit, by calling the owning service's internal API.

    Console never writes another service's schema (ADR-0003 §2). It resolves which service owns the
    write, delegates under a service token addressed to that service by name, and records its own
    audit row bound to the target organization.
    """
    cfg = settings()
    try:
        spec = delegation.resolve_spec(
            cfg,
            action=payload.action,
            organization_id=payload.organization_id,
            subscription_id=payload.subscription_id,
            amount_minor=payload.amount_minor,
            currency=payload.currency,
            reason=payload.reason,
        )
    except DelegationError as exc:
        raise ValidationProblem(str(exc)) from exc

    try:
        result = await delegation.deliver(cfg, spec, client=client)
    except DelegationError as exc:
        # The owning service refused or was unreachable. 503, not 500: the console did its part;
        # the downstream is the thing that is unavailable.
        raise UpstreamUnavailableProblem(str(exc)) from exc

    entity = "subscription" if payload.action == "suspend" else "credit"
    await services.record_override(
        session,
        organization_id=payload.organization_id,
        actor_id=operator.sub,
        operator_label=f"operator:{operator.sub}",
        action=payload.action,
        entity=entity,
        entity_id=payload.subscription_id,
        reason=payload.reason,
        after={"delegated_to": spec.audience, "result": result},
        request=request,
    )
    return OverrideOut(
        action=payload.action,
        organization_id=payload.organization_id,
        delegated_to=spec.audience,
        result=result,
    )
