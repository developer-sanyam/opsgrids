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
from datetime import datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import (
    account_client,
    catalog_client,
    delegation,
    directory_client,
    pagination,
    pricing,
    selectors,
    services,
)
from app.config import settings
from app.delegation import DelegationConflict, DelegationError
from app.directory_client import DirectoryLookupError
from app.models import SCHEMA
from app.schemas import (
    AuditListOut,
    AuditOut,
    LifecycleCountsOut,
    MetricsOut,
    MetricsOverviewOut,
    MetricsSeriesOut,
    OrganizationPricingOut,
    OrganizationsOut,
    OrgDetailOut,
    OrgMemberOut,
    OrgMembersOut,
    OverrideIn,
    OverrideOut,
    SeriesPointOut,
    ServiceInfo,
    SetPricingIn,
    SetPricingOut,
    SubscriptionOut,
    SubscriptionsOut,
    UsageListOut,
    UsageOut,
)
from platform_core.auth import Principal, require_operator
from platform_core.db import get_directory_session, get_session
from platform_core.errors import (
    ConflictProblem,
    NotFoundProblem,
    UpstreamUnavailableProblem,
    ValidationProblem,
)
from platform_core.logging import get_logger

log = get_logger("console.routes")

router = APIRouter(tags=["console"])

#: The ADR-0003 addendum's role matrix, named once per read shape rather than repeated per route.
#: `owner` holds everything; every set below includes it.
OperatorReadAny = Annotated[
    Principal, Depends(require_operator(roles={"owner", "billing_ops", "support"}))
]
OperatorBilling = Annotated[Principal, Depends(require_operator(roles={"owner", "billing_ops"}))]
OperatorAudit = Annotated[Principal, Depends(require_operator(roles={"owner", "support"}))]
#: The contract's `console.pricing.read`/`console.pricing.write`, implemented as operator roles
#: rather than the tenant permission grammar — ADR-0003's addendum is explicit that operator roles
#: "never touch `pv` or a membership's grants", and console has no `app/catalogue.py` of its own
#: for `tools/export_permissions.py` to read (that tool only discovers per-*tenant-product*
#: catalogues). Reuses the existing role dependencies rather than adding new ones: read is
#: `OperatorReadAny` (owner, billing_ops, support — any operator role admits identity/financial
#: reads); write is `OperatorBilling` (owner, billing_ops), the same gate the override endpoint
#: uses, since setting a client's price is a billing-shaped act.

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
    session: DirectorySessionDep, operator: OperatorReadAny, client: DelegationClientDep
) -> OrganizationsOut:
    # The call goes first, before this request's directory session has run a query: no database
    # connection is held while account-service answers (docs/io-outside-transactions.md).
    # Best-effort: a member-count outage must not blank a list that otherwise renders fine off
    # console's own schema (`account_client.member_counts`'s own docstring).
    counts: dict[str, int] | None
    try:
        counts = await account_client.member_counts(settings(), client=client)
    except account_client.AccountLookupError as exc:
        log.warning("console.organizations.member_counts_unavailable", error=str(exc)[:300])
        counts = None

    states = await selectors.all_org_states(session)
    metrics = await selectors.platform_metrics(session)

    return OrganizationsOut(
        organizations=[
            selectors.to_org_state_out(
                s,
                # Account answered and this organization is not in its counts: it has no
                # members, which is a real 0. Only an unreachable account is `null` (unknown).
                user_count=None if counts is None else counts.get(str(s.organization_id), 0),
            )
            for s in states
        ],
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

    The directory half is fetched from account-service's service-to-service route, never
    `/internal/v1`, which no individual caller's permissions gate at all
    (`app/directory_client.py`). MINOR 7 of the 2026-09-24 security review: this forwards this
    request's own operator bearer token onward rather than asserting the operator's identity in
    a service token — account-service verifies the token itself. Every read here writes an
    audit row bound to the target organization, per the ADR-0003 addendum: an operator looking
    at one customer's detail is exactly the kind of act that must leave a trace.
    """
    operator_token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    try:
        directory = await directory_client.get_organization(
            settings(), organization_id, operator_token=operator_token, client=client
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
        commercial=selectors.to_org_state_out(commercial) if commercial is not None else None,
    )


@router.get(
    "/admin/organizations/{organization_id}/users",
    response_model=OrgMembersOut,
    summary="One organization's members — staff-only, email masked, delegated to account-service",
)
async def get_organization_members(
    organization_id: uuid.UUID,
    request: Request,
    app_session: AppSessionDep,
    client: DelegationClientDep,
    operator: OperatorReadAny,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 25,
) -> OrgMembersOut:
    """The Users tab: id, name, a masked email, status, joined/last-active and roles by name,
    one page at a time. Console writes no PII of its own — every field here is delegated to
    account-service's own staff-only member read (`app/directory_client.py`), which does the
    masking. Same forwarded-operator-token shape as `get_organization_detail`, and the same
    "every staff read writes an audit row" rule, its own action so `/admin/audit` can tell a
    member-list read apart from a plain detail read.
    """
    operator_token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    try:
        page_data = await directory_client.list_members(
            settings(),
            organization_id,
            operator_token=operator_token,
            client=client,
            page=page,
            size=size,
        )
    except DirectoryLookupError as exc:
        raise UpstreamUnavailableProblem(str(exc)) from exc
    if page_data is None:
        raise NotFoundProblem("No such organization")

    await services.record_organization_members_read(
        app_session,
        organization_id=organization_id,
        actor_id=operator.sub,
        operator_label=f"operator:{operator.sub}",
        request=request,
    )

    return OrgMembersOut(
        members=[OrgMemberOut.model_validate(m) for m in page_data["items"]],
        total=int(page_data["total"]),
        page=int(page_data["page"]),
        size=int(page_data["size"]),
        pages=int(page_data["pages"]),
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


#: Bounded the same way every other admin list here already is (`recent_audit`'s 200): a chart
#: has no use for a decade of monthly points, and an unbounded value would mean replaying the
#: entire revenue-events table further than any caller actually asked for.
MAX_SERIES_MONTHS = 36


@router.get(
    "/admin/metrics/series",
    response_model=MetricsSeriesOut,
    summary="Monthly MRR, new organizations, trials, conversions and churn",
)
async def metrics_series(
    session: DirectorySessionDep,
    operator: OperatorBilling,
    months: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_SERIES_MONTHS,
            description="How many trailing calendar months to return, ending with the current one.",
        ),
    ] = 12,
) -> MetricsSeriesOut:
    data = await selectors.metrics_series(session, months=months)

    def _points(values: dict[str, int | None]) -> list[SeriesPointOut]:
        return [SeriesPointOut(period=period, value=values[period]) for period in data["periods"]]

    return MetricsSeriesOut(
        currency=data["currency"],
        mrr_minor=_points(data["mrr_minor"]),
        new_organizations=_points(data["new_organizations"]),
        trials_started=_points(data["trials_started"]),
        trial_conversions=_points(data["trial_conversions"]),
        churned=_points(data["churned"]),
    )


@router.get(
    "/admin/subscriptions",
    response_model=SubscriptionsOut,
    summary="Every organization's subscriptions, flattened from their snapshots",
)
async def list_subscriptions(
    session: DirectorySessionDep, operator: OperatorBilling, client: DelegationClientDep
) -> SubscriptionsOut:
    states = await selectors.all_org_states(session)

    # Display names come from catalog in a couple of calls total, never one per row: the batch
    # plan-version names read, and the public flat plans (whose plan name is the product's display
    # name). Best-effort, like `list_organizations`' member counts — a name outage must not blank a
    # list that otherwise renders fine off console's own schema.
    version_ids: list[uuid.UUID] = []
    for state in states:
        for entry in state.subscription_states.values():
            try:
                version_ids.append(uuid.UUID(str(entry["plan_version_id"])))
            except (KeyError, ValueError):
                continue  # a tombstone or an entry with no pin has no plan to name
    plan_names: dict[uuid.UUID, dict[str, Any]] = {}
    product_names: dict[str, str] = {}
    if version_ids:
        try:
            plan_names = await catalog_client.get_plan_version_names(
                settings(), version_ids, client=client
            )
            product_names = {
                str(p["product_key"]): str(p["product_name"])
                for p in await catalog_client.get_public_flat_plans(
                    settings(),
                    client=client,
                    request_timeout=catalog_client.ENRICHMENT_TIMEOUT_SECONDS,
                )
            }
        except catalog_client.CatalogError as exc:
            log.warning("console.subscriptions.names_unavailable", error=str(exc)[:300])

    rows: list[SubscriptionOut] = []
    for state in states:
        for product_key, entry in sorted(state.subscription_states.items()):
            raw_version = entry.get("plan_version_id")
            try:
                named = plan_names.get(uuid.UUID(str(raw_version)))
            except ValueError:
                named = None
            rows.append(
                SubscriptionOut(
                    organization_id=state.organization_id,
                    name=state.name,
                    slug=state.slug,
                    product_key=product_key,
                    product_name=product_names.get(product_key),
                    state=str(entry.get("state")),
                    plan_version_id=raw_version,
                    plan_name=named["plan_name"] if named else None,
                    amount_minor=int(entry.get("amount_minor", 0)),
                    currency=entry.get("currency"),
                    trial_ends_at=entry.get("trial_ends_at"),
                )
            )
    return SubscriptionsOut(subscriptions=rows)


#: The page size every admin list here defaults and caps to — audit's own long-standing "latest
#: 200" made real as a page rather than a hardcoded slice, and usage's new one now that it is no
#: longer read in full every call.
DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 500

OrganizationIdFilter = Annotated[
    uuid.UUID | None,
    Query(description="Narrow the list to one organization's rows."),
]
ListLimit = Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)]
ListCursor = Annotated[
    str | None,
    Query(description="The `next_cursor` from a previous page. Omit for the first page."),
]


@router.get(
    "/admin/usage",
    response_model=UsageListOut,
    summary="Every organization's usage rollups, by meter and period",
)
async def list_usage(
    session: DirectorySessionDep,
    operator: OperatorBilling,
    organization_id: OrganizationIdFilter = None,
    cursor: ListCursor = None,
    limit: ListLimit = DEFAULT_LIST_LIMIT,
) -> UsageListOut:
    after = None
    if cursor is not None:
        raw_org, meter_key, period = pagination.decode_cursor(cursor, expected_parts=3)
        after = (uuid.UUID(raw_org), meter_key, period)
    rows = await selectors.all_usage(
        session, organization_id=organization_id, limit=limit, after=after
    )
    next_cursor = (
        pagination.encode_cursor(str(rows[-1].organization_id), rows[-1].meter_key, rows[-1].period)
        if len(rows) == limit
        else None
    )
    return UsageListOut(usage=[UsageOut.model_validate(r) for r in rows], next_cursor=next_cursor)


@router.get(
    "/admin/audit",
    response_model=AuditListOut,
    summary="Recent operator-override audit rows, optionally narrowed to one organization",
)
async def list_audit(
    session: DirectorySessionDep,
    operator: OperatorAudit,
    organization_id: OrganizationIdFilter = None,
    cursor: ListCursor = None,
    limit: ListLimit = DEFAULT_LIST_LIMIT,
) -> AuditListOut:
    after = None
    if cursor is not None:
        raw_at, raw_id = pagination.decode_cursor(cursor, expected_parts=2)
        after = (datetime.fromisoformat(raw_at), uuid.UUID(raw_id))
    rows = await selectors.recent_audit(
        session, organization_id=organization_id, limit=limit, after=after
    )
    next_cursor = (
        pagination.encode_cursor(rows[-1].at.isoformat(), str(rows[-1].id))
        if len(rows) == limit
        else None
    )
    return AuditListOut(audit=[AuditOut.model_validate(r) for r in rows], next_cursor=next_cursor)


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
    """Suspend or reactivate a subscription, or grant comp credit, by calling the owning
    service's internal API.

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
            operator_id=str(operator.sub),
        )
    except DelegationError as exc:
        raise ValidationProblem(str(exc)) from exc

    try:
        result = await delegation.deliver(cfg, spec, client=client)
    except DelegationConflict as exc:
        # The target is not in a state this override applies to — the operator's picture of it is
        # stale, which is a conflict they can act on, not an outage.
        raise ConflictProblem(str(exc)) from exc
    except DelegationError as exc:
        # The owning service refused or was unreachable. 503, not 500: the console did its part;
        # the downstream is the thing that is unavailable.
        raise UpstreamUnavailableProblem(str(exc)) from exc

    entity = "credit" if payload.action == "comp_grant" else "subscription"
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


@router.get(
    "/admin/organizations/{organization_id}/pricing",
    response_model=OrganizationPricingOut,
    summary="Every product's price for one organization — current, public, and history",
)
async def get_organization_pricing(
    organization_id: uuid.UUID,
    request: Request,
    directory_session: DirectorySessionDep,
    client: DelegationClientDep,
    operator: OperatorReadAny,
) -> OrganizationPricingOut:
    """Compose catalog's public flat prices and subscription's live subscriptions with console's
    own bespoke-price audit history (`app/pricing.py`). 404 only when the organization itself does
    not exist — an org with no subscription for a product still lists that product, `subscription`
    and `current_price` null, per the contract.
    """
    operator_token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    try:
        directory = await directory_client.get_organization(
            settings(), organization_id, operator_token=operator_token, client=client
        )
    except DirectoryLookupError as exc:
        raise UpstreamUnavailableProblem(str(exc)) from exc
    if directory is None:
        raise NotFoundProblem("No such organization")

    return await pricing.build_pricing_view(
        settings(), directory_session, organization_id, client=client
    )


@router.post(
    "/admin/organizations/{organization_id}/pricing",
    response_model=SetPricingOut,
    status_code=status.HTTP_201_CREATED,
    summary="Set a client's price for a product — a bespoke plan version, optionally applied now",
)
async def set_organization_pricing(
    organization_id: uuid.UUID,
    payload: SetPricingIn,
    request: Request,
    app_session: AppSessionDep,
    directory_session: DirectorySessionDep,
    client: DelegationClientDep,
    operator: OperatorBilling,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            max_length=255,
            description=(
                "Optional, at most 255 characters. A retried request with the same key and the "
                "same body returns the first attempt's result instead of repeating the "
                "catalog/subscription calls; the same key with a different body is a 409."
            ),
        ),
    ] = None,
) -> SetPricingOut:
    """Clone-and-publish a bespoke flat price at catalog and, by default, re-pin the
    organization's live subscription to it.

    **`effective`, decided by subscription's own existing change-plan rule (`_change_plan`,
    ADR-0005 addendum §4) — this endpoint never overrides it:**

    * `"now"` — the subscription is on the new price immediately: it was trialling (no charge),
      or it was `active` and the new price is a paid *upgrade* (charged now, prorated for the
      rest of the period).
    * `"next_period"` — the subscription was `active` and the new price is a paid *downgrade*:
      subscription's existing scheduled-change mechanism applies it at `current_period_end`, so
      as not to take back time already paid for.
    * `"not_subscribed"` — the organization has no subscription for this product,
      `apply_to_subscription` was `false`, or the live subscription is `past_due`, `suspended` or
      `cancelled` (subscription refuses a change-plan outright in those states — there is no
      fourth `effective` value for that refusal, so it is folded into this one; the bespoke price
      is still created and published regardless).

    `Idempotency-Key` is an optional header; a retried request with the same key and the same
    body returns the first attempt's result rather than repeating the catalog/subscription calls.
    The same key with a different body is a 409.
    """
    idempotency_key = idempotency_key or None
    operator_token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()

    try:
        directory = await directory_client.get_organization(
            settings(), organization_id, operator_token=operator_token, client=client
        )
    except DirectoryLookupError as exc:
        raise UpstreamUnavailableProblem(str(exc)) from exc
    if directory is None:
        raise NotFoundProblem("No such organization")

    return await pricing.set_organization_pricing(
        settings(),
        organization_id=organization_id,
        payload=payload,
        idempotency_key=idempotency_key,
        operator=operator,
        operator_token=operator_token,
        directory_session=directory_session,
        app_session=app_session,
        client=client,
        request=request,
    )
