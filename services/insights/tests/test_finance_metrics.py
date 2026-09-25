"""The four finance metrics I2 lands (`docs/insights/metrics.md` §4.1) through
`GET /api/v1/insights/summary`: real values, per-currency grouping, comparisons, the gate, and
cross-tenant isolation. `tests/test_access.py` already covers the gate mechanics generically;
this file proves the same gate on real finance data, plus the definitions themselves.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import InvoiceFacts, PaymentFacts, PurchaseFacts
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _invoice(
    organization_id: uuid.UUID,
    *,
    number: str,
    document_type: str = "invoice",
    status: str = "sent",
    issued_date: date,
    currency: str = "INR",
    total_minor: int,
) -> InvoiceFacts:
    return InvoiceFacts(
        organization_id=organization_id,
        id=uuid.uuid4(),
        number=number,
        document_type=document_type,
        status=status,
        issued_date=issued_date,
        due_date=issued_date,
        currency=currency,
        subtotal_minor=total_minor,
        tax_minor=0,
        total_minor=total_minor,
        customer_ref=uuid.uuid4(),
        source_created_at=datetime(
            issued_date.year, issued_date.month, issued_date.day, tzinfo=UTC
        ),
        source_updated_at=datetime(
            issued_date.year, issued_date.month, issued_date.day, tzinfo=UTC
        ),
    )


def _payment(
    organization_id: uuid.UUID, *, received_date: date, currency: str = "INR", amount_minor: int
) -> PaymentFacts:
    return PaymentFacts(
        organization_id=organization_id,
        id=uuid.uuid4(),
        reference=f"UTR-{uuid.uuid4().hex[:8]}",
        received_date=received_date,
        method="bank_transfer",
        currency=currency,
        amount_minor=amount_minor,
        source_created_at=datetime(
            received_date.year, received_date.month, received_date.day, tzinfo=UTC
        ),
        source_updated_at=datetime(
            received_date.year, received_date.month, received_date.day, tzinfo=UTC
        ),
    )


def _purchase(
    organization_id: uuid.UUID,
    *,
    document_type: str = "invoice",
    document_date: date,
    currency: str = "INR",
    taxable_minor: int,
    total_minor: int,
) -> PurchaseFacts:
    return PurchaseFacts(
        organization_id=organization_id,
        id=uuid.uuid4(),
        number=f"BILL-{uuid.uuid4().hex[:8]}",
        document_type=document_type,
        document_date=document_date,
        currency=currency,
        taxable_minor=taxable_minor,
        tax_minor=0,
        total_minor=total_minor,
        source_created_at=datetime(
            document_date.year, document_date.month, document_date.day, tzinfo=UTC
        ),
        source_updated_at=datetime(
            document_date.year, document_date.month, document_date.day, tzinfo=UTC
        ),
    )


FINANCE_PRINCIPAL_KW = {
    "entitlements": ["finance"],
    "perms": [
        "finance.reports.summary.read:ORG",
        "finance.billing.invoice.read:ORG",
        "finance.billing.payment.read:ORG",
        "finance.billing.purchase.read:ORG",
    ],
}


class TestRevenue:
    async def test_revenue_nets_credit_notes_and_excludes_draft_and_void(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with session.begin():
            session.add_all(
                [
                    _invoice(
                        org_id,
                        number="INV-1",
                        issued_date=date(2026, 9, 5),
                        total_minor=100000,
                    ),
                    _invoice(
                        org_id,
                        number="CN-1",
                        document_type="credit_note",
                        issued_date=date(2026, 9, 10),
                        total_minor=-20000,
                    ),
                    _invoice(
                        org_id,
                        number="DRAFT-1",
                        status="draft",
                        issued_date=date(2026, 9, 12),
                        total_minor=999999,
                    ),
                    _invoice(
                        org_id,
                        number="VOID-1",
                        status="void",
                        issued_date=date(2026, 9, 12),
                        total_minor=999999,
                    ),
                    # Outside the window — must not be counted.
                    _invoice(
                        org_id,
                        number="AUG-1",
                        issued_date=date(2026, 8, 20),
                        total_minor=500000,
                    ),
                ]
            )
        with as_principal(principal_for(org_id, **FINANCE_PRINCIPAL_KW)):
            response = await client.get(
                "/api/v1/insights/summary",
                params={
                    "metrics": "revenue",
                    "period": "custom",
                    "from": "2026-09-01",
                    "to": "2026-09-25",
                    "compare": "none",
                },
            )
        assert response.status_code == 200
        metric = response.json()["metrics"]["revenue"]
        assert metric["state"] == "ok"
        assert metric["scope"] == "ORG"
        assert metric["currency"] == "INR"
        assert metric["value"] == 80000  # 100000 - 20000
        assert metric["other_currencies"] == []
        # `response_model_exclude_none=True` drops a `None` comparison entirely (compare=none).
        assert metric.get("comparison") is None

    async def test_other_currencies_are_listed_never_summed_in(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with session.begin():
            session.add_all(
                [
                    _invoice(
                        org_id, number="INV-INR", issued_date=date(2026, 9, 5), total_minor=100000
                    ),
                    _invoice(
                        org_id,
                        number="INV-USD",
                        issued_date=date(2026, 9, 6),
                        currency="USD",
                        total_minor=5000,
                    ),
                ]
            )
        with as_principal(principal_for(org_id, **FINANCE_PRINCIPAL_KW)):
            response = await client.get(
                "/api/v1/insights/summary",
                params={
                    "metrics": "revenue",
                    "period": "custom",
                    "from": "2026-09-01",
                    "to": "2026-09-25",
                    "compare": "none",
                },
            )
        metric = response.json()["metrics"]["revenue"]
        assert metric["value"] == 100000
        assert metric["currency"] == "INR"
        assert metric["other_currencies"] == [{"currency": "USD", "value": 5000}]

    async def test_comparison_delta_against_the_previous_period(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with session.begin():
            session.add_all(
                [
                    _invoice(
                        org_id,
                        number="THIS-MONTH",
                        issued_date=date(2026, 9, 5),
                        total_minor=150000,
                    ),
                    _invoice(
                        org_id,
                        number="LAST-MONTH",
                        issued_date=date(2026, 8, 5),
                        total_minor=100000,
                    ),
                ]
            )
        with as_principal(principal_for(org_id, **FINANCE_PRINCIPAL_KW)):
            # "this_month" (the default), so the comparison is metrics.md §2's "same elapsed
            # span" rule: 1-25 Sep against 1-25 Aug — a `custom` window would instead compare
            # against the 25 days immediately before it, which is not "last month" at all.
            response = await client.get(
                "/api/v1/insights/summary",
                params={"metrics": "revenue", "compare": "previous_period"},
            )
        metric = response.json()["metrics"]["revenue"]
        assert metric["value"] == 150000
        assert metric["comparison"]["state"] == "ok"
        assert metric["comparison"]["value"] == 100000
        assert metric["comparison"]["delta_pct"] == 50.0

    async def test_no_baseline_when_the_previous_period_is_zero(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with session.begin():
            session.add(
                _invoice(org_id, number="ONLY-THIS", issued_date=date(2026, 9, 5), total_minor=1)
            )
        with as_principal(principal_for(org_id, **FINANCE_PRINCIPAL_KW)):
            response = await client.get(
                "/api/v1/insights/summary",
                params={"metrics": "revenue", "compare": "previous_period"},
            )
        comparison = response.json()["metrics"]["revenue"]["comparison"]
        assert comparison["state"] == "no_baseline"
        # `response_model_exclude_none=True` drops a `None` delta_pct entirely.
        assert comparison.get("delta_pct") is None


class TestInvoicesIssued:
    async def test_counts_only_invoices_not_credit_notes(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with session.begin():
            session.add_all(
                [
                    _invoice(org_id, number="A", issued_date=date(2026, 9, 1), total_minor=1000),
                    _invoice(org_id, number="B", issued_date=date(2026, 9, 2), total_minor=2000),
                    _invoice(
                        org_id,
                        number="CN",
                        document_type="credit_note",
                        issued_date=date(2026, 9, 3),
                        total_minor=-500,
                    ),
                ]
            )
        with as_principal(principal_for(org_id, **FINANCE_PRINCIPAL_KW)):
            response = await client.get(
                "/api/v1/insights/summary",
                params={
                    "metrics": "invoices_issued",
                    "period": "custom",
                    "from": "2026-09-01",
                    "to": "2026-09-25",
                    "compare": "none",
                },
            )
        metric = response.json()["metrics"]["invoices_issued"]
        assert metric["state"] == "ok"
        assert metric["unit"] == "count"
        assert metric["value"] == 2


class TestPaymentsReceived:
    async def test_sums_amount_minor_in_window(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with session.begin():
            session.add_all(
                [
                    _payment(org_id, received_date=date(2026, 9, 10), amount_minor=70000),
                    _payment(org_id, received_date=date(2026, 9, 20), amount_minor=30000),
                    _payment(org_id, received_date=date(2026, 8, 20), amount_minor=999999),
                ]
            )
        with as_principal(principal_for(org_id, **FINANCE_PRINCIPAL_KW)):
            response = await client.get(
                "/api/v1/insights/summary",
                params={
                    "metrics": "payments_received",
                    "period": "custom",
                    "from": "2026-09-01",
                    "to": "2026-09-25",
                    "compare": "none",
                },
            )
        metric = response.json()["metrics"]["payments_received"]
        assert metric["value"] == 100000


class TestPurchasesRecorded:
    async def test_a_credit_note_reduces_spend(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with session.begin():
            session.add_all(
                [
                    _purchase(
                        org_id,
                        document_date=date(2026, 9, 5),
                        taxable_minor=50000,
                        total_minor=59000,
                    ),
                    _purchase(
                        org_id,
                        document_type="credit_note",
                        document_date=date(2026, 9, 8),
                        taxable_minor=10000,
                        total_minor=11800,
                    ),
                ]
            )
        with as_principal(principal_for(org_id, **FINANCE_PRINCIPAL_KW)):
            response = await client.get(
                "/api/v1/insights/summary",
                params={
                    "metrics": "purchases_recorded",
                    "period": "custom",
                    "from": "2026-09-01",
                    "to": "2026-09-25",
                    "compare": "none",
                },
            )
        metric = response.json()["metrics"]["purchases_recorded"]
        assert metric["value"] == 59000 - 11800


class TestGates:
    async def test_missing_entitlement_is_not_active(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, perms=["finance.reports.summary.read:ORG"])):
            response = await client.get("/api/v1/insights/summary", params={"metrics": "revenue"})
        assert response.json()["metrics"]["revenue"] == {
            "state": "not_active",
            "product_key": "finance",
        }

    async def test_missing_permission_is_no_access(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, entitlements=["finance"])):
            response = await client.get("/api/v1/insights/summary", params={"metrics": "revenue"})
        assert response.json()["metrics"]["revenue"] == {"state": "no_access"}

    async def test_an_own_scoped_grant_is_no_access_never_a_narrowed_org_read(
        self, client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """Every finance metric is ORG only (`docs/insights/metrics.md` §4.1) — an OWN grant
        (nonsensical for finance, but the gate must still refuse it) gets `no_access`, never a
        computed value (`app/access.py`'s `own_capable=False` rule)."""
        async with session.begin():
            session.add(_invoice(org_id, number="X", issued_date=date(2026, 9, 1), total_minor=100))
        with as_principal(
            principal_for(
                org_id,
                entitlements=["finance"],
                perms=["finance.reports.summary.read:OWN"],
                subject_ref=uuid.uuid4(),
            )
        ):
            response = await client.get("/api/v1/insights/summary", params={"metrics": "revenue"})
        assert response.json()["metrics"]["revenue"] == {"state": "no_access"}


class TestCrossTenantIsolation:
    async def test_another_organizations_facts_never_appear_in_the_total(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        async with session.begin():
            session.add(
                _invoice(org_id, number="MINE", issued_date=date(2026, 9, 5), total_minor=1000)
            )
        async with admin_session.begin():
            admin_session.add(
                _invoice(
                    other_org, number="THEIRS", issued_date=date(2026, 9, 5), total_minor=999999
                )
            )
        with as_principal(principal_for(org_id, **FINANCE_PRINCIPAL_KW)):
            response = await client.get(
                "/api/v1/insights/summary",
                params={
                    "metrics": "revenue",
                    "period": "custom",
                    "from": "2026-09-01",
                    "to": "2026-09-25",
                    "compare": "none",
                },
            )
        assert response.json()["metrics"]["revenue"]["value"] == 1000
