"""HTTP surface for messaging analytics and the cost rate card (slice M1).

Mounted behind the gateway's `/api/v1/engage` prefix, so every path here begins
`/engage/analytics/...`. Every route resolves the actor, binds the tenant through the same
`get_tenant_session` chokepoint the rest of Engage uses, and checks a permission floored at ORG
before it touches data. Reads go through `app.m1_analytics`; no route builds a query.

Permissions are **reused**, not new (a new code needs a gated production role reconcile):

  * the three read routes take `engage.campaign.campaign.read` — delivery statistics, which
    every Engage viewer already holds;
  * overriding the rate card takes `engage.setup.whatsapp.write` — the WhatsApp setup grant only an
    administrator holds. The rates decide what every dashboard says the organization spends, so it
    is a settings-level change, not something anyone who can edit a contact should be able to do.

The date range is two inclusive **UTC** dates, `?from=YYYY-MM-DD&to=YYYY-MM-DD`; omitted, it is the
last 30 days ending today.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import m1_analytics
from app.m1_cost import DateWindow, RangeError, resolve_window
from app.m1_schemas import (
    HubOverview,
    PlatformRateCardIn,
    RateCardIn,
    RateCardOut,
    WhatsAppOverview,
)
from platform_core.auth import (
    Principal,
    Scope,
    ScopeContext,
    require,
    require_operator,
    require_product,
)
from platform_core.cache import enforce_rate_limit
from platform_core.db import get_session, get_tenant_session
from platform_core.errors import ValidationProblem

router = APIRouter(tags=["engage-analytics"])
#: The operator surface: fronted by admin-gateway as `staff_only`, never by app-gateway.
admin_router = APIRouter(prefix="/admin/engage", tags=["engage-analytics-admin"])

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
Entitled = Depends(require_product("engage"))

AnalyticsRead = Annotated[
    ScopeContext, Depends(require("engage.campaign.campaign.read", minimum=Scope.ORG))
]
RateCardWrite = Annotated[
    ScopeContext, Depends(require("engage.setup.whatsapp.write", minimum=Scope.ORG))
]
#: The platform rate table prices every tenant's dashboard, so it is a billing-staff change.
OperatorBilling = Annotated[Principal, Depends(require_operator(roles={"owner", "billing_ops"}))]
AppSessionDep = Annotated[AsyncSession, Depends(get_session)]

#: Each overview runs several grouped queries over up to a year of messages, so it is throttled per
#: organization. Generous for a dashboard that refreshes now and then, tight enough that a script
#: polling it cannot become the largest reader of the messages table.
OVERVIEW_LIMIT_PER_MINUTE = 60


async def throttle(ctx: ScopeContext, *, bucket: str = "engage.analytics") -> None:
    """Spend one call from the organization's per-minute budget for `bucket`. The automation
    stats route reuses this under its own bucket rather than growing a second limiter."""
    await enforce_rate_limit(
        f"{bucket}:{ctx.organization_id}",
        limit=OVERVIEW_LIMIT_PER_MINUTE,
        window_seconds=60,
    )


#: `from` is a Python keyword, so the parameter is named `date_from` and aliased on the wire.
DateFrom = Annotated[
    date | None,
    Query(alias="from", description="First UTC day, inclusive (YYYY-MM-DD)."),
]
DateTo = Annotated[
    date | None,
    Query(alias="to", description="Last UTC day, inclusive (YYYY-MM-DD)."),
]


def query_window(date_from: date | None, date_to: date | None) -> DateWindow:
    """The `?from=&to=` pair as a bounded window, or a 422 problem naming why it is not one."""
    try:
        return resolve_window(date_from, date_to)
    except RangeError as exc:
        raise ValidationProblem(
            str(exc), errors=[{"field": "from", "code": exc.code, "message": str(exc)}]
        ) from exc


@router.get(
    "/engage/analytics/whatsapp/overview",
    response_model=WhatsAppOverview,
    summary="WhatsApp delivery, replies, categories, failures and an estimated cost",
    description=(
        "Computed on demand from the message ledger. Buckets are **UTC calendar days**; `from` and "
        "`to` are inclusive UTC dates (default: the last 30 days). `cost` is an ESTIMATE "
        "(`estimated: true`) in integer minor units: each billable message is priced at the rate "
        "for its recipient's market (longest calling-code prefix) and the category Meta reported "
        "on its delivery receipt (the template's category until a receipt lands), rounded half-up "
        "per market and category. The rates are the organization's own override or, failing "
        "that, the platform card seeded from Meta's published INR list rates "
        "(`meta-inr-2026-10-01`); volume tiers and tax are not applied. `by_source` lists the most "
        "expensive automations and inbox conversations."
    ),
    dependencies=[Entitled],
)
async def whatsapp_overview(
    session: SessionDep,
    ctx: AnalyticsRead,
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> WhatsAppOverview:
    window = query_window(date_from, date_to)
    await throttle(ctx)
    return await m1_analytics.whatsapp_overview(session, ctx.organization_id, window)


@router.get(
    "/engage/analytics/overview",
    response_model=HubOverview,
    summary="The communication hub: both channels' numbers and one estimated cost",
    description=(
        "WhatsApp numbers computed here; email numbers come from the email analytics overview "
        "and are `null` when that module is not deployed. Same UTC-day range semantics and the "
        "same cost caveats as the WhatsApp overview: the cost is an estimate from Meta's list "
        "rates (or the organization's override), not an invoice."
    ),
    dependencies=[Entitled],
)
async def hub_overview(
    session: SessionDep,
    ctx: AnalyticsRead,
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> HubOverview:
    window = query_window(date_from, date_to)
    await throttle(ctx)
    return await m1_analytics.hub_overview(session, ctx.organization_id, window)


@router.get(
    "/engage/analytics/rate-card",
    response_model=RateCardOut,
    summary="The per-1000-message rates behind the cost estimate",
    description=(
        "The rate in force today for each market (`calling_code`, `''` = every other market) "
        "and message category, in minor units per 1000 messages. Unless the organization has "
        "entered its own rates with PUT, these are the platform's, seeded from Meta's published "
        "INR rate card effective 2026-10-01 (`meta-inr-2026-10-01`) and maintained by staff. "
        "`placeholder` is true only while a rate in force is an unverified placeholder."
    ),
    dependencies=[Entitled],
)
async def read_rate_card(session: SessionDep, ctx: AnalyticsRead) -> RateCardOut:
    today = datetime.now(UTC).date()
    return await m1_analytics.read_rate_card(session, ctx.organization_id, as_of=today)


@router.put(
    "/engage/analytics/rate-card",
    response_model=RateCardOut,
    summary="Override the rate card with the organization's real rates",
    description=(
        "Exactly one rate per category (marketing, utility, authentication, service), all in one "
        "ISO currency, integer minor units per 1000 messages. Takes effect from `effective_from` "
        "(a UTC date, default today); saving the same date again replaces it, a later date adds a "
        "price point and keeps the earlier ones. The organization's own rate always beats the "
        "platform default once in force. Audited."
    ),
    dependencies=[Entitled],
)
async def override_rate_card(
    body: RateCardIn, session: SessionDep, ctx: RateCardWrite, request: Request
) -> RateCardOut:
    today = datetime.now(UTC).date()
    effective_from = body.effective_from or today
    await m1_analytics.override_rate_card(
        session,
        ctx,
        rates={r.category: (r.amount_minor_per_1000_messages, r.currency) for r in body.rates},
        effective_from=effective_from,
        request=request,
    )
    return await m1_analytics.read_rate_card(session, ctx.organization_id, as_of=today)


@admin_router.put(
    "/whatsapp-rate-card",
    response_model=RateCardOut,
    summary="Staff: add or replace price points on the platform WhatsApp rate table",
    description=(
        "Operator only (`owner` or `billing_ops`). Upserts one platform row per market and "
        "category for `effective_from`, labelled `label` (e.g. `meta-inr-2027-01-01`, the source "
        "of the figures); rows not named are left alone and earlier price points are kept. Every "
        "tenant without its own override prices from this table. Audited under the platform "
        "tenant. Returns the card as a tenant with no override now sees it."
    ),
)
async def override_platform_rate_card(
    body: PlatformRateCardIn, session: AppSessionDep, operator: OperatorBilling, request: Request
) -> RateCardOut:
    async with session.begin():
        await m1_analytics.override_platform_rates(
            session,
            operator_id=operator.sub,
            rates=[
                m1_analytics.PlatformRate(
                    calling_code=r.calling_code,
                    market=r.market,
                    category=r.category,
                    amount_minor_per_1000_messages=r.amount_minor_per_1000_messages,
                    currency=r.currency,
                )
                for r in body.rates
            ],
            effective_from=body.effective_from,
            label=body.label,
            request=request,
        )
        return await m1_analytics.read_rate_card(
            session, m1_analytics.PLATFORM_ORG, as_of=datetime.now(UTC).date()
        )
