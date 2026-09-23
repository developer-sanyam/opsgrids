"""Billing's half of the billing run: price one subscription period and record it, once.

subscription-service owns *when* a period is billed — it holds the lifecycle and the period
boundaries — and asks here over `POST /internal/v1/subscription-invoices` (ADR-0005 addendum,
2026-09-23). Billing owns *what is owed*: it reads the pinned plan version's prices from catalog,
the closed period's usage from metering and the tax rate from its own `tax_rates`, and writes the
invoice. The caller never sends a price or an amount.

The shape is **claim → call → record**, because no network call may run inside a database
transaction (a slow catalog must not hold a row lock, a pooled connection and a snapshot open for
its whole timeout):

1. **Read** (one short transaction, tenant-bound): has this period already been invoiced? If so,
   return that invoice and stop — the ordinary answer to a redelivery. The tax rate in force is
   read in the same transaction; `tax_rates` is local reference data.
2. **Call** (no transaction): catalog for the pinned version, metering for the usage window.
3. **Price** (pure): `domain.price_subscription_period`.
4. **Record** (one short transaction, tenant-bound): `services.record_subscription_invoice`,
   whose `ON CONFLICT DO NOTHING` against the per-period key makes a race between two callers
   land on one invoice and one `commerce.invoice.finalized`.

A crash anywhere before step 4 commits leaves nothing behind; a crash after it leaves the invoice,
which step 1 returns on the next ask. Either way the period is invoiced exactly once.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain, selectors, services
from app.config import Settings
from app.models import Invoice
from app.schemas import BillSubscriptionPeriodIn
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, set_tenant
from platform_core.errors import BusinessRuleProblem, UpstreamUnavailableProblem
from platform_core.internal_auth import mint_service_token
from platform_core.logging import correlation_id_ctx, get_logger

log = get_logger("billing.billing_run")


# --------------------------------------------------------------------- what the reads return


class CatalogPrice(BaseModel):
    """One price on the pinned version, as catalog's internal read returns it."""

    id: uuid.UUID
    kind: str
    amount_minor: int | None = None
    currency: str
    unit: str | None = None
    unit_price_micros: int | None = None
    meter_key: str | None = None


class CatalogVersion(BaseModel):
    """The pinned plan version — only the fields pricing needs."""

    id: uuid.UUID
    product_key: str | None = None
    currency: str
    prices: list[CatalogPrice] = Field(default_factory=list)


class Catalog(Protocol):
    async def get_plan_version(self, plan_version_id: uuid.UUID) -> CatalogVersion | None: ...


class Metering(Protocol):
    async def billable_usage(
        self, *, organization_id: uuid.UUID, period: domain.Period
    ) -> dict[str, int]: ...


# --------------------------------------------------------------------- HTTP clients


def _headers(settings: ServiceSettings, audience: str) -> dict[str, str]:
    headers = {
        # Minted per call, addressed to the callee by name, good for seconds.
        "X-Internal-Token": mint_service_token(settings, audience=audience),
        "X-Calling-Service": settings.service_name,
    }
    correlation = correlation_id_ctx.get()
    if correlation:
        headers["X-Correlation-ID"] = correlation
    return headers


class HttpCatalog:
    """catalog's `GET /internal/v1/plan-versions/{id}`, the same read subscription pins with."""

    def __init__(self, base_url: str, settings: ServiceSettings, *, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._settings = settings
        self._timeout = timeout

    async def get_plan_version(self, plan_version_id: uuid.UUID) -> CatalogVersion | None:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as c:
                response = await c.get(
                    f"/internal/v1/plan-versions/{plan_version_id}",
                    headers=_headers(self._settings, "catalog"),
                )
        except httpx.HTTPError as exc:
            raise UpstreamUnavailableProblem("catalog is unavailable; cannot price") from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise UpstreamUnavailableProblem(f"catalog answered {response.status_code}")
        return CatalogVersion.model_validate(response.json())


class HttpMetering:
    """metering's `GET /internal/v1/organizations/{org}/usage`, billable counts per meter."""

    def __init__(self, base_url: str, settings: ServiceSettings, *, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._settings = settings
        self._timeout = timeout

    async def billable_usage(
        self, *, organization_id: uuid.UUID, period: domain.Period
    ) -> dict[str, int]:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as c:
                response = await c.get(
                    f"/internal/v1/organizations/{organization_id}/usage",
                    params={
                        "period_start": period.start.isoformat(),
                        "period_end": period.end.isoformat(),
                    },
                    headers=_headers(self._settings, "metering"),
                )
        except httpx.HTTPError as exc:
            raise UpstreamUnavailableProblem("metering is unavailable; cannot price") from exc
        if response.status_code >= 400:
            raise UpstreamUnavailableProblem(f"metering answered {response.status_code}")
        body: dict[str, Any] = response.json()
        return {
            str(line["meter_key"]): int(line["billable_quantity"])
            for line in body.get("meters", [])
        }


# --------------------------------------------------------------------- the orchestration


async def _bind(session: AsyncSession, organization_id: uuid.UUID, settings: Settings) -> None:
    await set_tenant(session, organization_id)
    if not settings.is_production:
        await assert_tenant(session, organization_id)


async def bill_subscription_period(
    session: AsyncSession,
    payload: BillSubscriptionPeriodIn,
    *,
    catalog: Catalog,
    metering: Metering,
    settings: Settings,
    request: Request | None = None,
) -> tuple[Invoice, bool]:
    """Invoice one subscription period, once. Returns `(invoice, created)`.

    `session` must arrive with **no transaction open**: this function opens two short ones of its
    own and makes its network calls between them.
    """
    period = domain.Period(payload.period_start, payload.period_end)
    key = domain.subscription_invoice_key(payload.subscription_id, period.start)
    currency = payload.currency.upper()

    # 1. Read: already invoiced? And the tax rate in force on the period's first day.
    tax: domain.TaxSpec | None = None
    async with session.begin():
        await _bind(session, payload.organization_id, settings)
        existing = await selectors.get_invoice_by_key(session, key)
        if existing is not None:
            log.info(
                "billing_run.invoice.exists",
                organization_id=str(payload.organization_id),
                subscription_id=str(payload.subscription_id),
                invoice_id=str(existing.id),
            )
            return existing, False
        if settings.billing_tax_code:
            rate = await selectors.effective_tax_rate(
                session,
                code=settings.billing_tax_code,
                jurisdiction=settings.billing_tax_jurisdiction,
                on=period.start.date(),
            )
            if rate is None:
                # Configured to tax and nothing in force: refusing is the only safe answer.
                # Invoicing untaxed would under-collect a statutory amount the platform owes.
                raise BusinessRuleProblem(
                    f"no {settings.billing_tax_code} rate is in force on "
                    f"{period.start.date().isoformat()}; add one to tax_rates"
                )
            tax = domain.TaxSpec(code=rate.code, name=rate.name, rate_bps=int(rate.rate_bps))

    # 2. Call — no transaction is open on `session` from here until the record step.
    version = await catalog.get_plan_version(payload.plan_version_id)
    if version is None:
        raise BusinessRuleProblem(f"plan version {payload.plan_version_id} does not exist")
    if version.currency.upper() != currency:
        raise BusinessRuleProblem(
            f"plan version is in {version.currency.upper()}, the subscription in {currency}"
        )
    if version.product_key is not None and version.product_key != payload.product_key:
        raise BusinessRuleProblem(
            f"plan version belongs to {version.product_key!r}, not {payload.product_key!r}"
        )

    usage_period: domain.Period | None = None
    usage: dict[str, int] | None = None
    if payload.usage_period_start is not None and payload.usage_period_end is not None:
        usage_period = domain.Period(payload.usage_period_start, payload.usage_period_end)
        if any(p.kind == domain.PRICE_USAGE for p in version.prices):
            usage = await metering.billable_usage(
                organization_id=payload.organization_id, period=usage_period
            )

    # 3. Price — pure.
    try:
        lines = domain.price_subscription_period(
            currency=currency,
            plan_version_id=version.id,
            prices=[
                domain.PriceSpec(
                    id=p.id,
                    kind=p.kind,
                    currency=p.currency,
                    amount_minor=p.amount_minor,
                    unit=p.unit,
                    unit_price_micros=p.unit_price_micros,
                    meter_key=p.meter_key,
                )
                for p in version.prices
            ],
            items=[
                domain.ItemSpec(price_id=i.price_id, quantity=i.quantity) for i in payload.items
            ],
            period=period,
            usage_period=usage_period,
            usage=usage,
            tax=tax,
        )
    except domain.PricingError as exc:
        raise BusinessRuleProblem(str(exc)) from exc

    # 4. Record — one short transaction, idempotent at the database.
    async with session.begin():
        await _bind(session, payload.organization_id, settings)
        invoice, created = await services.record_subscription_invoice(
            session,
            organization_id=payload.organization_id,
            subscription_id=payload.subscription_id,
            product_key=payload.product_key,
            currency=currency,
            period=period,
            lines=lines,
            request=request,
        )
    log.info(
        "billing_run.invoice.recorded" if created else "billing_run.invoice.raced",
        organization_id=str(payload.organization_id),
        subscription_id=str(payload.subscription_id),
        invoice_id=str(invoice.id),
        total_minor=invoice.total_minor,
        currency=invoice.currency,
        period_start=_iso(period.start),
    )
    return invoice, created


def _iso(at: datetime) -> str:
    return at.isoformat()
