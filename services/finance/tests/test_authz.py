"""Authorization on every finance endpoint — written as negative assertions.

Proving a call is *refused* is worth more than proving one succeeds: the success path is the one
exercised by hand anyway. Finance is an ORG-level product, so every read route floors at ORG —
the two things that must be refused are a caller who lacks the permission, and a caller whose
grant is narrower than ORG (a leftover OWN grant must not read a company's whole book). Both are
checked here, per endpoint, alongside the entitlement gate (402, not 403).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest

from app.main import app
from platform_core.auth import Principal, get_principal
from tests.helpers import FINANCE_READS, principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


# (path, the permission it requires)
ENDPOINTS = [
    ("/api/v1/finance/invoices", "finance.billing.invoice.read"),
    (f"/api/v1/finance/invoices/{uuid.uuid4()}", "finance.billing.invoice.read"),
    ("/api/v1/finance/payments", "finance.billing.payment.read"),
    ("/api/v1/finance/customers", "finance.billing.customer.read"),
    ("/api/v1/finance/overview", "finance.reports.summary.read"),
    ("/api/v1/finance/ledger", "finance.reports.ledger.read"),
]


class TestMissingPermission:
    @pytest.mark.parametrize("path,perm", ENDPOINTS)
    async def test_no_grant_is_refused(self, client, org_id, path, perm) -> None:
        """Entitled to finance, but holding no permission at all — every read is 403."""
        principal = principal_for(org_id, perms=[])
        with as_principal(principal):
            res = await client.get(path)
        assert res.status_code == 403

    @pytest.mark.parametrize("path,perm", ENDPOINTS)
    async def test_only_invoice_grant_reaches_only_invoices(
        self, client, org_id, path, perm
    ) -> None:
        """A caller holding only invoice.read reads invoices and nothing else."""
        principal = principal_for(org_id, perms=["finance.billing.invoice.read:ORG"])
        with as_principal(principal):
            res = await client.get(path)
        if perm == "finance.billing.invoice.read":
            # Reached: 200 for the list, 404 for the random-uuid detail — never 403.
            assert res.status_code != 403
        else:
            assert res.status_code == 403


class TestScopeFloor:
    async def test_an_own_grant_is_below_the_org_floor(self, client, org_id) -> None:
        """Finance has no OWN reading of a company's books — an OWN grant is refused."""
        principal = principal_for(org_id, perms=["finance.billing.invoice.read:OWN"])
        with as_principal(principal):
            res = await client.get("/api/v1/finance/invoices")
        assert res.status_code == 403


class TestEntitlement:
    @pytest.mark.parametrize("path,perm", ENDPOINTS)
    async def test_not_entitled_is_402(self, client, org_id, path, perm) -> None:
        """Every permission held, but the organization has not activated finance — 402, not 403,
        so the client offers an upgrade rather than an error."""
        principal = principal_for(
            org_id, perms=[f"{p}:ORG" for p in FINANCE_READS], entitlements=[]
        )
        with as_principal(principal):
            res = await client.get(path)
        assert res.status_code == 402


class TestFullyGranted:
    async def test_a_fully_granted_caller_reads(self, client, org_id, book) -> None:
        principal = principal_for(org_id, perms=[f"{p}:ORG" for p in FINANCE_READS])
        with as_principal(principal):
            for path, _ in ENDPOINTS[:1] + ENDPOINTS[2:]:  # skip the random-uuid detail path
                res = await client.get(path)
                assert res.status_code == 200, path
