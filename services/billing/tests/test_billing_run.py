"""The billing run's half in billing: price a subscription period, record it exactly once.

Against real PostgreSQL on the app role, with catalog and metering replaced by in-memory fakes —
fakes that also **assert no database transaction is open while they are called**, because the
rule this route exists to keep is claim → call → record: a slow sibling must never be waited on
while a row lock and a pooled connection are held.

What is pinned here, and why:

* **Money is integer and exact** — flat, per-seat and usage lines in INR, JPY (no minor unit)
  and KWD (three), and tax computed once on the subtotal, half-up, as its own line.
* **One invoice per period** — a repeat ask returns the same invoice and stages no second
  `commerce.invoice.finalized` (a second event is a second charge); two concurrent transactions
  racing the same period produce one row, enforced by the database.
* **Usage is billed once, for the closed period only**, and not at all on the first invoice after
  a trial.
* **Authorization** — no token is 401, a token from a service other than subscription is 403, and
  another organization's invoice under the same key is invisible.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import domain, services
from app.api.internal import get_billing_catalog, get_billing_metering
from app.billing_run import CatalogPrice, CatalogVersion
from app.config import settings
from app.main import app
from app.models import SCHEMA, Invoice, InvoiceLine, OutboxEvent, TaxRate
from platform_core.db import TENANT_GUC, dispose_outbox_engine, init_outbox_engine
from platform_core.internal_auth import mint_service_token
from platform_core.money import Money
from tests.helpers import customer_principal

pytestmark = pytest.mark.integration

PERIOD_START = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
PERIOD_END = PERIOD_START + timedelta(days=30)
USAGE_START = PERIOD_START - timedelta(days=30)


def _headers(caller: str = "subscription") -> dict[str, str]:
    cfg = settings()
    return {"X-Internal-Token": mint_service_token(cfg, audience=cfg.service_name, caller=caller)}


# --------------------------------------------------------------------- fakes


class FakeCatalog:
    """Catalog's plan-version read, in memory — and a tripwire for a transaction held open."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.versions: dict[uuid.UUID, CatalogVersion] = {}
        self.calls = 0

    async def get_plan_version(self, plan_version_id: uuid.UUID) -> CatalogVersion | None:
        self.calls += 1
        assert not self._session.in_transaction(), "catalog was called inside a transaction"
        return self.versions.get(plan_version_id)


class FakeMetering:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.usage: dict[str, int] = {}
        self.asked: list[domain.Period] = []

    async def billable_usage(
        self, *, organization_id: uuid.UUID, period: domain.Period
    ) -> dict[str, int]:
        assert not self._session.in_transaction(), "metering was called inside a transaction"
        self.asked.append(period)
        return dict(self.usage)


@pytest.fixture
def catalog(session: AsyncSession) -> FakeCatalog:
    return FakeCatalog(session)


@pytest.fixture
def metering(session: AsyncSession) -> FakeMetering:
    return FakeMetering(session)


@pytest.fixture
def wired(client: AsyncClient, catalog: FakeCatalog, metering: FakeMetering) -> AsyncClient:
    app.dependency_overrides[get_billing_catalog] = lambda: catalog
    app.dependency_overrides[get_billing_metering] = lambda: metering
    return client


def _version(
    *,
    currency: str = "INR",
    flat: int | None = 149900,
    per_seat: int | None = None,
    usage_micros: int | None = None,
    meter_key: str = "api_call",
) -> tuple[CatalogVersion, list[dict[str, Any]]]:
    """A pinned version and the subscription items that point at its prices."""
    prices: list[CatalogPrice] = []
    items: list[dict[str, Any]] = []
    if flat is not None:
        p = CatalogPrice(id=uuid.uuid4(), kind="flat", amount_minor=flat, currency=currency)
        prices.append(p)
        items.append({"price_id": str(p.id), "quantity": 1})
    if per_seat is not None:
        p = CatalogPrice(
            id=uuid.uuid4(), kind="per_seat", amount_minor=per_seat, currency=currency, unit="seat"
        )
        prices.append(p)
        items.append({"price_id": str(p.id), "quantity": 7})
    if usage_micros is not None:
        p = CatalogPrice(
            id=uuid.uuid4(),
            kind="usage",
            currency=currency,
            unit="call",
            unit_price_micros=usage_micros,
            meter_key=meter_key,
        )
        prices.append(p)
        items.append({"price_id": str(p.id), "quantity": 1})
    return CatalogVersion(
        id=uuid.uuid4(), product_key="hrms", currency=currency, prices=prices
    ), items


def _body(
    org_id: uuid.UUID,
    version: CatalogVersion,
    items: list[dict[str, Any]],
    *,
    subscription_id: uuid.UUID | None = None,
    usage: bool = True,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "organization_id": str(org_id),
        "subscription_id": str(subscription_id or uuid.uuid4()),
        "product_key": "hrms",
        "plan_version_id": str(version.id),
        "currency": version.currency,
        "period_start": PERIOD_START.isoformat(),
        "period_end": PERIOD_END.isoformat(),
        "items": items,
    }
    if usage:
        body["usage_period_start"] = USAGE_START.isoformat()
        body["usage_period_end"] = PERIOD_START.isoformat()
    return body


async def _count(admin_session: AsyncSession, model: Any, **where: Any) -> int:
    admin_session.expunge_all()
    async with admin_session.begin():
        stmt = select(func.count()).select_from(model)
        for key, value in where.items():
            stmt = stmt.where(getattr(model, key) == value)
        return int((await admin_session.execute(stmt)).scalar_one())


# --------------------------------------------------------------------- pure pricing


class TestPricing:
    def _price(self, **kw: Any) -> domain.PriceSpec:
        return domain.PriceSpec(id=uuid.uuid4(), currency=kw.pop("currency", "INR"), **kw)

    def _run(
        self,
        prices: list[domain.PriceSpec],
        items: list[domain.ItemSpec],
        *,
        currency: str = "INR",
        usage: dict[str, int] | None = None,
        with_usage_period: bool = True,
        tax: domain.TaxSpec | None = None,
    ) -> list[domain.LineDraft]:
        return domain.price_subscription_period(
            currency=currency,
            plan_version_id=uuid.uuid4(),
            prices=prices,
            items=items,
            period=domain.Period(PERIOD_START, PERIOD_END),
            usage_period=domain.Period(USAGE_START, PERIOD_START) if with_usage_period else None,
            usage=usage,
            tax=tax,
        )

    def test_flat_and_per_seat_are_amount_times_quantity(self) -> None:
        flat = self._price(kind="flat", amount_minor=149900)
        seat = self._price(kind="per_seat", amount_minor=29900, unit="seat")
        lines = self._run(
            [flat, seat],
            [domain.ItemSpec(flat.id, 1), domain.ItemSpec(seat.id, 7)],
        )
        assert [line.amount_minor for line in lines] == [149900, 7 * 29900]
        assert all(isinstance(line.amount_minor, int) for line in lines)
        assert lines[1].quantity == Decimal(7)

    def test_usage_is_priced_from_micros_and_rounded_once_half_up(self) -> None:
        # 0.0125 INR per call = 12_500 micros; 1_001 calls = 12.5125 INR = 1251.25 paise -> 1251.
        usage = self._price(kind="usage", unit_price_micros=12_500, meter_key="api_call")
        lines = self._run([usage], [domain.ItemSpec(usage.id, 1)], usage={"api_call": 1_001})
        assert len(lines) == 1
        assert lines[0].amount_minor == 1251
        assert lines[0].quantity == Decimal(1001)
        assert lines[0].unit_price_micros == 12_500

    def test_no_usage_period_means_no_usage_line(self) -> None:
        """The first invoice after a trial: trial usage is not billed."""
        usage = self._price(kind="usage", unit_price_micros=12_500, meter_key="api_call")
        lines = self._run(
            [usage], [domain.ItemSpec(usage.id, 1)], usage=None, with_usage_period=False
        )
        assert lines == []

    def test_jpy_has_no_minor_unit_and_kwd_has_three(self) -> None:
        jpy = self._price(kind="usage", unit_price_micros=1_500_000, meter_key="m", currency="JPY")
        # 1.5 JPY per unit x 3 units = 4.5 JPY -> 5 (half-up, zero decimal places).
        [line] = self._run([jpy], [domain.ItemSpec(jpy.id, 1)], currency="JPY", usage={"m": 3})
        assert line.amount_minor == 5
        kwd = self._price(kind="usage", unit_price_micros=1_234, meter_key="m", currency="KWD")
        # 0.001234 KWD x 1000 = 1.234 KWD = 1234 fils exactly.
        [line] = self._run([kwd], [domain.ItemSpec(kwd.id, 1)], currency="KWD", usage={"m": 1000})
        assert line.amount_minor == 1234

    def test_tax_is_one_exclusive_line_on_the_subtotal_and_the_lines_foot(self) -> None:
        flat = self._price(kind="flat", amount_minor=99999)
        tax = domain.TaxSpec(code="gst", name="GST", rate_bps=1800)
        lines = self._run([flat], [domain.ItemSpec(flat.id, 1)], tax=tax)
        # 18% of 99999 = 17999.82 -> 18000 (half-up).
        assert [line.amount_minor for line in lines] == [99999, 18000]
        assert lines[1].description == "GST 18.00%"
        domain.assert_foots(Money(117999, "INR"), lines)

    def test_zero_quantity_writes_no_line_and_no_tax(self) -> None:
        seat = self._price(kind="per_seat", amount_minor=29900)
        tax = domain.TaxSpec(code="gst", name="GST", rate_bps=1800)
        assert self._run([seat], [domain.ItemSpec(seat.id, 0)], tax=tax) == []

    def test_an_item_off_the_pin_is_refused_not_skipped(self) -> None:
        flat = self._price(kind="flat", amount_minor=100)
        with pytest.raises(domain.PricingError):
            self._run([flat], [domain.ItemSpec(uuid.uuid4(), 1)])

    def test_a_price_in_another_currency_is_refused(self) -> None:
        flat = self._price(kind="flat", amount_minor=100, currency="USD")
        with pytest.raises(domain.PricingError):
            self._run([flat], [domain.ItemSpec(flat.id, 1)])

    def test_the_key_is_stable_across_timezones(self) -> None:
        sid = uuid.uuid4()
        ist = PERIOD_START.astimezone(datetime.now().astimezone().tzinfo)
        assert domain.subscription_invoice_key(sid, PERIOD_START) == (
            domain.subscription_invoice_key(sid, ist)
        )


# --------------------------------------------------------------------- the route


class TestBillSubscriptionPeriod:
    async def test_creates_a_finalized_invoice_priced_off_the_pin(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        metering: FakeMetering,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        version, items = _version(flat=149900, per_seat=29900, usage_micros=12_500)
        catalog.versions[version.id] = version
        metering.usage = {"api_call": 1_001}

        r = await wired.post(
            "/internal/v1/subscription-invoices",
            json=_body(org_id, version, items),
            headers=_headers(),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["created"] is True
        invoice = body["invoice"]
        # A draft until subscription has recorded the claim and asks for finalize (round 2, M5).
        assert invoice["state"] == "draft"
        assert invoice["currency"] == "INR"
        amounts = [line["amount_minor"] for line in invoice["lines"]]
        assert sorted(amounts) == sorted([149900, 7 * 29900, 1251])
        assert invoice["total_minor"] == sum(amounts)
        # Usage was asked for the closed period, exactly.
        assert metering.asked == [domain.Period(USAGE_START, PERIOD_START)]

        events = await _count(admin_session, OutboxEvent, event_type="commerce.invoice.finalized")
        assert events == 0, "a draft announces nothing — payment cannot charge it"

    async def test_asking_twice_returns_the_one_invoice_and_one_event(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        version, items = _version()
        catalog.versions[version.id] = version
        body = _body(org_id, version, items)

        first = await wired.post(
            "/internal/v1/subscription-invoices", json=body, headers=_headers()
        )
        second = await wired.post(
            "/internal/v1/subscription-invoices", json=body, headers=_headers()
        )
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["created"] is False
        assert second.json()["invoice"]["id"] == first.json()["invoice"]["id"]
        # The repeat never reached catalog: an invoiced period is answered from billing alone.
        assert catalog.calls == 1
        assert await _count(admin_session, Invoice) == 1
        assert (
            await _count(admin_session, OutboxEvent, event_type="commerce.invoice.finalized") == 0
        )

    async def test_usage_is_billed_once_even_if_the_period_is_asked_again(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        metering: FakeMetering,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        version, items = _version(flat=None, usage_micros=1_000_000)
        catalog.versions[version.id] = version
        metering.usage = {"api_call": 40}
        body = _body(org_id, version, items)
        await wired.post("/internal/v1/subscription-invoices", json=body, headers=_headers())
        # Late usage for the same closed period arrives, and the period is asked again.
        metering.usage = {"api_call": 45}
        again = await wired.post(
            "/internal/v1/subscription-invoices", json=body, headers=_headers()
        )
        assert again.json()["invoice"]["total_minor"] == 40 * 100
        admin_session.expunge_all()
        async with admin_session.begin():
            usage_lines = (
                await admin_session.execute(
                    select(func.count())
                    .select_from(InvoiceLine)
                    .where(InvoiceLine.unit_price_micros.is_not(None))
                )
            ).scalar_one()
        assert usage_lines == 1

    async def test_the_first_invoice_after_a_trial_bills_no_usage(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        metering: FakeMetering,
        org_id: uuid.UUID,
    ) -> None:
        version, items = _version(flat=50000, usage_micros=1_000_000)
        catalog.versions[version.id] = version
        metering.usage = {"api_call": 999}
        r = await wired.post(
            "/internal/v1/subscription-invoices",
            json=_body(org_id, version, items, usage=False),
            headers=_headers(),
        )
        assert r.status_code == 201
        assert r.json()["invoice"]["total_minor"] == 50000
        assert metering.asked == []

    async def test_tax_uses_the_rate_in_force_on_the_period_start(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async with admin_session.begin():
            admin_session.add_all(
                [
                    TaxRate(
                        code="gst",
                        jurisdiction="IN",
                        name="GST",
                        rate_bps=1200,
                        effective_from=date(2025, 1, 1),
                    ),
                    TaxRate(
                        code="gst",
                        jurisdiction="IN",
                        name="GST",
                        rate_bps=1800,
                        effective_from=date(2026, 4, 1),
                    ),
                    # Not yet in force on the period's first day.
                    TaxRate(
                        code="gst",
                        jurisdiction="IN",
                        name="GST",
                        rate_bps=2800,
                        effective_from=date(2026, 12, 1),
                    ),
                ]
            )
        monkeypatch.setattr(app.state.settings, "billing_tax_code", "gst")
        monkeypatch.setattr(app.state.settings, "billing_tax_jurisdiction", "IN")
        version, items = _version(flat=100000)
        catalog.versions[version.id] = version

        r = await wired.post(
            "/internal/v1/subscription-invoices",
            json=_body(org_id, version, items),
            headers=_headers(),
        )
        assert r.status_code == 201, r.text
        invoice = r.json()["invoice"]
        assert invoice["total_minor"] == 118000
        assert any(line["description"] == "GST 18.00%" for line in invoice["lines"])

    async def test_configured_tax_with_no_rate_in_force_is_refused(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(app.state.settings, "billing_tax_code", "gst")
        monkeypatch.setattr(app.state.settings, "billing_tax_jurisdiction", "IN")
        version, items = _version()
        catalog.versions[version.id] = version
        r = await wired.post(
            "/internal/v1/subscription-invoices",
            json=_body(org_id, version, items),
            headers=_headers(),
        )
        assert r.status_code == 422
        assert await _count(admin_session, Invoice) == 0

    async def test_a_currency_that_disagrees_with_the_pin_is_refused(
        self, wired: AsyncClient, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        version, items = _version(currency="INR")
        catalog.versions[version.id] = version
        body = _body(org_id, version, items)
        body["currency"] = "USD"
        r = await wired.post("/internal/v1/subscription-invoices", json=body, headers=_headers())
        assert r.status_code == 422

    async def test_a_usage_window_reaching_into_the_billed_period_is_refused(
        self, wired: AsyncClient, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        version, items = _version()
        catalog.versions[version.id] = version
        body = _body(org_id, version, items)
        body["usage_period_end"] = (PERIOD_START + timedelta(days=1)).isoformat()
        r = await wired.post("/internal/v1/subscription-invoices", json=body, headers=_headers())
        assert r.status_code == 422


class TestAuthorization:
    async def test_no_service_token_is_401(
        self, wired: AsyncClient, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        version, items = _version()
        catalog.versions[version.id] = version
        r = await wired.post(
            "/internal/v1/subscription-invoices", json=_body(org_id, version, items)
        )
        assert r.status_code == 401
        assert catalog.calls == 0

    async def test_a_token_from_another_service_is_403(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        version, items = _version()
        catalog.versions[version.id] = version
        r = await wired.post(
            "/internal/v1/subscription-invoices",
            json=_body(org_id, version, items),
            headers=_headers(caller="payment"),
        )
        assert r.status_code == 403
        assert catalog.calls == 0
        assert await _count(admin_session, Invoice) == 0

    async def test_a_token_addressed_to_another_service_is_401(
        self, wired: AsyncClient, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        cfg = settings()
        token = mint_service_token(cfg, audience="payment", caller="subscription")
        version, items = _version()
        catalog.versions[version.id] = version
        r = await wired.post(
            "/internal/v1/subscription-invoices",
            json=_body(org_id, version, items),
            headers={"X-Internal-Token": token},
        )
        assert r.status_code == 401

    async def test_the_customer_route_cannot_reach_it(self, wired: AsyncClient) -> None:
        """A customer token through the product gateway has no path to the internal router."""
        r = await wired.post("/api/v1/billing/subscription-invoices", json={})
        assert r.status_code in (401, 404, 405)


class TestIsolationAndRaces:
    async def test_another_tenants_invoice_under_the_same_key_is_invisible(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        """Org B holds an invoice for the same subscription id and period. Org A's ask must not
        be answered with B's invoice — the key is unique per organization and RLS hides B's row."""
        version, items = _version()
        catalog.versions[version.id] = version
        subscription_id = uuid.uuid4()
        async with admin_session.begin():
            admin_session.add(
                Invoice(
                    organization_id=org_b,
                    subscription_id=subscription_id,
                    number="B-1",
                    state="finalized",
                    currency="INR",
                    total_minor=1,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                    finalized_at=PERIOD_START,
                    idempotency_key=domain.subscription_invoice_key(subscription_id, PERIOD_START),
                )
            )
        r = await wired.post(
            "/internal/v1/subscription-invoices",
            json=_body(org_id, version, items, subscription_id=subscription_id),
            headers=_headers(),
        )
        assert r.status_code == 201
        assert r.json()["invoice"]["total_minor"] == 149900
        assert r.json()["invoice"]["number"] != "B-1"

    async def test_two_racing_transactions_record_one_invoice(
        self, session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Two replicas priced the same period and reach the record step together. The second
        insert waits on the unique index, then conflicts and reads the first one's row."""
        cfg = settings()
        engine = create_async_engine(
            cfg.app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        subscription_id = uuid.uuid4()
        period = domain.Period(PERIOD_START, PERIOD_END)
        line = domain.LineDraft(
            description="Subscription", quantity=Decimal(1), amount_minor=5000, currency="INR"
        )
        a_inserted = asyncio.Event()

        async def record(first: bool) -> tuple[uuid.UUID, bool]:
            async with maker() as s, s.begin():
                await s.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :o, true)"), {"o": str(org_id)}
                )
                if not first:
                    await a_inserted.wait()
                invoice, created = await services.record_subscription_invoice(
                    s,
                    organization_id=org_id,
                    subscription_id=subscription_id,
                    product_key="hrms",
                    currency="INR",
                    key=domain.subscription_invoice_key(subscription_id, period.start),
                    period=period,
                    lines=[line],
                )
                if first:
                    a_inserted.set()
                    # Hold the transaction open so the second insert has to wait on the index.
                    await asyncio.sleep(0.3)
                return invoice.id, created

        try:
            (a_id, a_created), (b_id, b_created) = await asyncio.gather(record(True), record(False))
        finally:
            await engine.dispose()
        assert a_created is True
        assert b_created is False
        assert a_id == b_id
        assert await _count(admin_session, Invoice) == 1
        assert (
            await _count(admin_session, OutboxEvent, event_type="commerce.invoice.finalized") == 0
        )

    async def test_two_racing_finalizes_finalize_once_and_the_second_is_idempotent(
        self, session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Two replicas finalize the same draft together (review round 3, m4). The second must
        wait on the row, see it finalized, and answer `(invoice, False)` — not read a stale
        draft, stage a second `commerce.invoice.finalized` (a second charge), or trip the freeze
        trigger into a 500."""
        cfg = settings()
        subscription_id = uuid.uuid4()
        period = domain.Period(PERIOD_START, PERIOD_END)
        key = domain.subscription_invoice_key(subscription_id, period.start)
        async with session.begin():
            await services.record_subscription_invoice(
                session,
                organization_id=org_id,
                subscription_id=subscription_id,
                product_key="hrms",
                currency="INR",
                key=key,
                period=period,
                lines=[
                    domain.LineDraft(
                        description="Subscription",
                        quantity=Decimal(1),
                        amount_minor=5000,
                        currency="INR",
                    )
                ],
            )
        engine = create_async_engine(
            cfg.app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        a_finalized = asyncio.Event()
        b_read = asyncio.Event()

        async def finalize(first: bool) -> tuple[str, bool]:
            async with maker() as s, s.begin():
                await s.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :o, true)"), {"o": str(org_id)}
                )
                if not first:
                    await a_finalized.wait()
                    b_read.set()
                invoice, changed = await services.finalize_subscription_invoice(
                    s, organization_id=org_id, key=key, product_key="hrms", kind="period"
                )
                if first:
                    a_finalized.set()
                    # Hold the transaction so the second finalize reads while this is uncommitted.
                    await b_read.wait()
                    await asyncio.sleep(0.3)
                return invoice.state, changed

        try:
            (a_state, a_changed), (b_state, b_changed) = await asyncio.gather(
                finalize(True), finalize(False)
            )
        finally:
            await engine.dispose()
        assert (a_state, a_changed) == ("finalized", True)
        assert (b_state, b_changed) == ("finalized", False)
        assert (
            await _count(admin_session, OutboxEvent, event_type="commerce.invoice.finalized") == 1
        )

    async def test_the_customer_reads_the_run_invoice(
        self, wired: AsyncClient, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        from platform_core.auth import get_principal

        version, items = _version()
        catalog.versions[version.id] = version
        body = _body(org_id, version, items)
        made = await wired.post("/internal/v1/subscription-invoices", json=body, headers=_headers())
        app.dependency_overrides[get_principal] = lambda: customer_principal(org_id)
        try:
            # A draft is not a document the customer owes yet: invisible in the list and by id.
            assert (await wired.get("/api/v1/invoices")).json() == []
            draft_id = made.json()["invoice"]["id"]
            assert (await wired.get(f"/api/v1/invoices/{draft_id}")).status_code == 404
            await wired.post(
                "/internal/v1/subscription-invoices:finalize",
                json=_finalize_body(body),
                headers=_headers(),
            )
            mine = await wired.get("/api/v1/invoices")
        finally:
            app.dependency_overrides.pop(get_principal, None)
        assert mine.status_code == 200
        [invoice] = mine.json()
        assert invoice["total_minor"] == 149900
        assert invoice["number"].startswith("INV-20261001-")


# --------------------------------------------------------------------- review fixes


def _price(amount: int, *, currency: str = "INR") -> CatalogPrice:
    return CatalogPrice(id=uuid.uuid4(), kind="flat", amount_minor=amount, currency=currency)


def _proration_body(
    org_id: uuid.UUID,
    new: CatalogVersion,
    old: CatalogVersion,
    *,
    change_id: uuid.UUID,
    effective_at: datetime,
    subscription_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "organization_id": str(org_id),
        "subscription_id": str(subscription_id or uuid.uuid4()),
        "kind": "proration",
        "change_id": str(change_id),
        "product_key": "hrms",
        "plan_version_id": str(new.id),
        "currency": "INR",
        "period_start": effective_at.isoformat(),
        "period_end": PERIOD_END.isoformat(),
        "full_period_start": PERIOD_START.isoformat(),
        "items": [{"price_id": str(p.id), "quantity": 1} for p in new.prices],
        "from_plan_version_id": str(old.id),
        "from_items": [{"price_id": str(p.id), "quantity": 1} for p in old.prices],
    }


class TestProrationPricing:
    def test_the_upgrade_charge_is_the_prorated_difference_in_integer_minor_units(self) -> None:
        new, old = _price(200000), _price(100000)
        lines = domain.price_proration(
            currency="INR",
            new_plan_version_id=uuid.uuid4(),
            new_prices=[
                domain.PriceSpec(id=new.id, kind="flat", currency="INR", amount_minor=200000)
            ],
            new_items=[domain.ItemSpec(new.id, 1)],
            old_plan_version_id=uuid.uuid4(),
            old_prices=[
                domain.PriceSpec(id=old.id, kind="flat", currency="INR", amount_minor=100000)
            ],
            old_items=[domain.ItemSpec(old.id, 1)],
            full_period=domain.Period(PERIOD_START, PERIOD_END),
            effective_at=PERIOD_START + timedelta(days=10),
            tax=domain.TaxSpec(code="gst", name="GST", rate_bps=1800),
        )
        # 20 of 30 days remain: 200000 -> 133333 and 100000 -> 66667 by largest remainder, so the
        # difference is 66666; GST 18% of it is 11999.88 -> 12000.
        assert [line.amount_minor for line in lines] == [66666, 12000]
        assert all(isinstance(line.amount_minor, int) for line in lines)

    def test_a_change_that_nets_to_nothing_bills_nothing(self) -> None:
        a = _price(100000)
        lines = domain.price_proration(
            currency="INR",
            new_plan_version_id=uuid.uuid4(),
            new_prices=[
                domain.PriceSpec(id=a.id, kind="flat", currency="INR", amount_minor=100000)
            ],
            new_items=[domain.ItemSpec(a.id, 1)],
            old_plan_version_id=uuid.uuid4(),
            old_prices=[
                domain.PriceSpec(id=a.id, kind="flat", currency="INR", amount_minor=100000)
            ],
            old_items=[domain.ItemSpec(a.id, 1)],
            full_period=domain.Period(PERIOD_START, PERIOD_END),
            effective_at=PERIOD_START + timedelta(days=3),
            tax=None,
        )
        assert lines == []

    def test_keys_differ_by_kind_and_numbers_by_key(self) -> None:
        sid, change = uuid.uuid4(), uuid.uuid4()
        period_key = domain.subscription_invoice_key(sid, PERIOD_START)
        close_key = domain.subscription_invoice_key(sid, PERIOD_START, kind="close")
        proration_key = domain.subscription_invoice_key(
            sid, PERIOD_START, kind="proration", change_id=change
        )
        assert close_key == f"{period_key}:close"
        assert proration_key == f"subscription:{sid}:proration:{change}"
        numbers = {
            domain.subscription_invoice_number(k, PERIOD_START)
            for k in (period_key, close_key, proration_key)
        }
        assert len(numbers) == 3


class TestProrationAndCloseRoute:
    async def test_an_upgrade_is_billed_its_prorated_difference_once(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        new = CatalogVersion(
            id=uuid.uuid4(), product_key="hrms", currency="INR", prices=[_price(200000)]
        )
        old = CatalogVersion(
            id=uuid.uuid4(), product_key="hrms", currency="INR", prices=[_price(100000)]
        )
        catalog.versions[new.id] = new
        catalog.versions[old.id] = old
        change_id = uuid.uuid4()
        body = _proration_body(
            org_id, new, old, change_id=change_id, effective_at=PERIOD_START + timedelta(days=10)
        )
        first = await wired.post(
            "/internal/v1/subscription-invoices", json=body, headers=_headers()
        )
        again = await wired.post(
            "/internal/v1/subscription-invoices", json=body, headers=_headers()
        )
        assert first.status_code == 201, first.text
        assert again.status_code == 200
        invoice = first.json()["invoice"]
        assert invoice["total_minor"] == 66666
        assert again.json()["invoice"]["id"] == invoice["id"]
        admin_session.expunge_all()
        async with admin_session.begin():
            key = (await admin_session.execute(select(Invoice.idempotency_key))).scalar_one()
        assert key.endswith(f":proration:{change_id}")

    async def test_a_proration_without_its_change_is_refused(
        self, wired: AsyncClient, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        new = CatalogVersion(
            id=uuid.uuid4(), product_key="hrms", currency="INR", prices=[_price(2)]
        )
        body = _proration_body(
            org_id, new, new, change_id=uuid.uuid4(), effective_at=PERIOD_START + timedelta(days=1)
        )
        del body["change_id"]
        r = await wired.post("/internal/v1/subscription-invoices", json=body, headers=_headers())
        assert r.status_code == 422

    async def test_a_closing_period_bills_usage_and_nothing_recurring(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        metering: FakeMetering,
        org_id: uuid.UUID,
    ) -> None:
        version, items = _version(flat=149900, usage_micros=1_000_000)
        catalog.versions[version.id] = version
        metering.usage = {"api_call": 7}
        close_end = PERIOD_START + timedelta(days=9)
        body = _body(org_id, version, items, usage=False)
        body.update(
            kind="close",
            period_end=close_end.isoformat(),
            usage_period_start=PERIOD_START.isoformat(),
            usage_period_end=close_end.isoformat(),
        )
        r = await wired.post("/internal/v1/subscription-invoices", json=body, headers=_headers())
        assert r.status_code == 201, r.text
        invoice = r.json()["invoice"]
        assert [line["amount_minor"] for line in invoice["lines"]] == [700]
        assert metering.asked == [domain.Period(PERIOD_START, close_end)]


class TestVoidByKey:
    async def test_a_superseded_period_invoice_is_voided_by_its_key(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        version, items = _version()
        catalog.versions[version.id] = version
        body = _body(org_id, version, items)
        made = await wired.post("/internal/v1/subscription-invoices", json=body, headers=_headers())
        void_body = {
            "organization_id": body["organization_id"],
            "subscription_id": body["subscription_id"],
            "kind": "period",
            "period_start": body["period_start"],
            "reason": "superseded period",
        }
        first = await wired.post(
            "/internal/v1/subscription-invoices:void", json=void_body, headers=_headers()
        )
        again = await wired.post(
            "/internal/v1/subscription-invoices:void", json=void_body, headers=_headers()
        )
        assert first.status_code == 200, first.text
        assert first.json() == {"voided": True, "invoice_id": made.json()["invoice"]["id"]}
        assert again.json()["voided"] is True
        admin_session.expunge_all()
        async with admin_session.begin():
            state = (await admin_session.execute(select(Invoice.state))).scalar_one()
        assert state == "void"
        # A draft is voided silently: nothing downstream ever learned it existed (round 2, M5).
        assert await _count(admin_session, OutboxEvent, event_type="commerce.invoice.voided") == 0
        assert (
            await _count(admin_session, OutboxEvent, event_type="commerce.invoice.finalized") == 0
        )

    async def test_nothing_to_void_says_so(self, wired: AsyncClient, org_id: uuid.UUID) -> None:
        r = await wired.post(
            "/internal/v1/subscription-invoices:void",
            json={
                "organization_id": str(org_id),
                "subscription_id": str(uuid.uuid4()),
                "kind": "period",
                "period_start": PERIOD_START.isoformat(),
                "reason": "superseded period",
            },
            headers=_headers(),
        )
        assert r.status_code == 200
        assert r.json() == {"voided": False, "invoice_id": None}

    async def test_only_subscription_may_void(self, wired: AsyncClient, org_id: uuid.UUID) -> None:
        body = {
            "organization_id": str(org_id),
            "subscription_id": str(uuid.uuid4()),
            "kind": "period",
            "period_start": PERIOD_START.isoformat(),
            "reason": "x",
        }
        assert (
            await wired.post("/internal/v1/subscription-invoices:void", json=body)
        ).status_code == 401
        assert (
            await wired.post(
                "/internal/v1/subscription-invoices:void",
                json=body,
                headers=_headers(caller="payment"),
            )
        ).status_code == 403


class TestNumbersAndScoping:
    async def test_a_hand_invoice_holding_the_derived_number_does_not_block_the_run(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        version, items = _version()
        catalog.versions[version.id] = version
        subscription_id = uuid.uuid4()
        key = domain.subscription_invoice_key(subscription_id, PERIOD_START)
        taken = domain.subscription_invoice_number(key, PERIOD_START)
        async with session.begin():
            session.add(
                Invoice(
                    organization_id=org_id,
                    number=taken,
                    state="finalized",
                    currency="INR",
                    total_minor=1,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                    finalized_at=PERIOD_START,
                )
            )
        r = await wired.post(
            "/internal/v1/subscription-invoices",
            json=_body(org_id, version, items, subscription_id=subscription_id),
            headers=_headers(),
        )
        assert r.status_code == 201, r.text
        assert r.json()["invoice"]["number"] == f"{taken}-2"

    async def test_the_key_lookup_filters_the_organization_even_without_rls(
        self, admin_session: AsyncSession, org_id: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """On the BYPASSRLS admin session only the explicit filter keeps org B's invoice out."""
        from app import selectors

        key = domain.subscription_invoice_key(uuid.uuid4(), PERIOD_START)
        async with admin_session.begin():
            admin_session.add(
                Invoice(
                    organization_id=org_b,
                    number="B-KEY",
                    state="finalized",
                    currency="INR",
                    total_minor=1,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                    finalized_at=PERIOD_START,
                    idempotency_key=key,
                )
            )
        async with admin_session.begin():
            assert await selectors.get_invoice_by_key(admin_session, org_id, key) is None
            assert await selectors.get_invoice_by_key(admin_session, org_b, key) is not None


# ------------------------------------------------------------ round 2: finalize after record


def _finalize_body(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "organization_id": body["organization_id"],
        "subscription_id": body["subscription_id"],
        "kind": body.get("kind", "period"),
        "period_start": body["period_start"],
        "change_id": body.get("change_id"),
        "product_key": body.get("product_key"),
    }


class TestFinalizeAfterRecord:
    async def test_finalize_freezes_the_draft_and_announces_it_exactly_once(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        version, items = _version()
        catalog.versions[version.id] = version
        body = _body(org_id, version, items)
        await wired.post("/internal/v1/subscription-invoices", json=body, headers=_headers())
        first = await wired.post(
            "/internal/v1/subscription-invoices:finalize",
            json=_finalize_body(body),
            headers=_headers(),
        )
        again = await wired.post(
            "/internal/v1/subscription-invoices:finalize",
            json=_finalize_body(body),
            headers=_headers(),
        )
        assert first.status_code == 200, first.text
        assert first.json()["finalized"] is True
        assert first.json()["invoice"]["state"] == "finalized"
        assert first.json()["invoice"]["finalized_at"] is not None
        assert again.json()["finalized"] is False
        assert (
            await _count(admin_session, OutboxEvent, event_type="commerce.invoice.finalized") == 1
        )
        # And frozen: the only legal move from here is void.
        admin_session.expunge_all()
        with pytest.raises(DBAPIError):
            async with admin_session.begin():
                await admin_session.execute(
                    text("UPDATE billing.invoices SET total_minor = 1 WHERE state = 'finalized'")
                )

    async def test_a_finalized_run_invoice_voids_with_an_event(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        version, items = _version()
        catalog.versions[version.id] = version
        body = _body(org_id, version, items)
        await wired.post("/internal/v1/subscription-invoices", json=body, headers=_headers())
        await wired.post(
            "/internal/v1/subscription-invoices:finalize",
            json=_finalize_body(body),
            headers=_headers(),
        )
        r = await wired.post(
            "/internal/v1/subscription-invoices:void",
            json={**_finalize_body(body), "reason": "superseded period"},
            headers=_headers(),
        )
        assert r.json()["voided"] is True
        assert await _count(admin_session, OutboxEvent, event_type="commerce.invoice.voided") == 1

    async def test_a_voided_draft_cannot_be_finalized(
        self, wired: AsyncClient, catalog: FakeCatalog, org_id: uuid.UUID
    ) -> None:
        version, items = _version()
        catalog.versions[version.id] = version
        body = _body(org_id, version, items)
        await wired.post("/internal/v1/subscription-invoices", json=body, headers=_headers())
        await wired.post(
            "/internal/v1/subscription-invoices:void",
            json={**_finalize_body(body), "reason": "superseded period"},
            headers=_headers(),
        )
        r = await wired.post(
            "/internal/v1/subscription-invoices:finalize",
            json=_finalize_body(body),
            headers=_headers(),
        )
        assert r.status_code == 409

    async def test_finalizing_nothing_is_404_and_only_subscription_may(
        self, wired: AsyncClient, org_id: uuid.UUID
    ) -> None:
        body = {
            "organization_id": str(org_id),
            "subscription_id": str(uuid.uuid4()),
            "kind": "period",
            "period_start": PERIOD_START.isoformat(),
        }
        url = "/internal/v1/subscription-invoices:finalize"
        assert (await wired.post(url, json=body, headers=_headers())).status_code == 404
        assert (await wired.post(url, json=body)).status_code == 401
        assert (
            await wired.post(url, json=body, headers=_headers(caller="payment"))
        ).status_code == 403

    async def test_a_proration_that_nets_to_nothing_writes_no_invoice(
        self,
        wired: AsyncClient,
        catalog: FakeCatalog,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        a = CatalogVersion(
            id=uuid.uuid4(), product_key="hrms", currency="INR", prices=[_price(100000)]
        )
        b = CatalogVersion(
            id=uuid.uuid4(), product_key="hrms", currency="INR", prices=[_price(100000)]
        )
        catalog.versions[a.id] = a
        catalog.versions[b.id] = b
        body = _proration_body(
            org_id, b, a, change_id=uuid.uuid4(), effective_at=PERIOD_START + timedelta(days=4)
        )
        r = await wired.post("/internal/v1/subscription-invoices", json=body, headers=_headers())
        assert r.status_code == 200, r.text
        assert r.json() == {"created": False, "invoice": None}
        assert await _count(admin_session, Invoice) == 0


# --------------------------------------------------------------------- drafts probe (round 3, m6)


async def test_the_drafts_probe_counts_stale_drafts_and_names_nobody(
    wired: AsyncClient,
    catalog: FakeCatalog,
    admin_session: AsyncSession,
    org_id: uuid.UUID,
) -> None:
    """A run draft the run never finalized is a period nobody is charged for, and nothing
    errors. `/health/billing-drafts` counts it once it is older than an hour — cross-tenant, on
    the outbox role's column grant, with no id in the answer."""
    version, items = _version()
    catalog.versions[version.id] = version
    for _ in range(2):
        r = await wired.post(
            "/internal/v1/subscription-invoices",
            json=_body(org_id, version, items),
            headers=_headers(),
        )
        assert r.status_code == 201, r.text
    async with admin_session.begin():
        oldest = (
            await admin_session.execute(select(Invoice).where(Invoice.state == "draft").limit(1))
        ).scalar_one()
        oldest.created_at = datetime.now(UTC) - timedelta(hours=2)

    init_outbox_engine(settings())
    try:
        r = await wired.get("/health/billing-drafts")
    finally:
        await dispose_outbox_engine()
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"drafts", "drafts_stale", "oldest_draft_seconds"}
    assert body["drafts"] == 2
    assert body["drafts_stale"] == 1
    assert body["oldest_draft_seconds"] >= 2 * 3600 - 60
