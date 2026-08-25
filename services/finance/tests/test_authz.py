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
    # Compliance. Deliberately behind their own permissions: seeing the receivable book and
    # seeing what was reported to the tax authority are different questions, and an organization
    # may reasonably answer them for different people.
    (f"/api/v1/finance/invoices/{uuid.uuid4()}/filings", "finance.compliance.einvoice.read"),
    ("/api/v1/finance/gst/registration", "finance.compliance.registration.read"),
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
        for path, _ in ENDPOINTS:
            # The two per-invoice paths name a random uuid, so they resolve to 404, not 200.
            if "invoices/" in path:
                continue
            with as_principal(principal):
                res = await client.get(path)
            assert res.status_code == 200, path


#: A valid registration payload — the write these tests try to make without the grant for it.
REGISTRATION = {"gstin": "27AABCA9012D1Z5", "legal_name": "Atmora Industries Private Limited"}


class TestTheComplianceWrite:
    """The one write on the public finance surface, and the only permission in the catalogue
    marked dangerous. Reading the registration must not imply being able to change it: it decides
    which identity every future invoice is filed under."""

    async def test_a_reader_cannot_set_the_registration(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=[f"{p}:ORG" for p in FINANCE_READS])
        with as_principal(principal):
            res = await client.put("/api/v1/finance/gst/registration", json=REGISTRATION)
        assert res.status_code == 403

    async def test_an_own_grant_is_below_the_org_floor(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=["finance.compliance.registration.write:OWN"])
        with as_principal(principal):
            res = await client.put("/api/v1/finance/gst/registration", json=REGISTRATION)
        assert res.status_code == 403

    async def test_the_write_grant_sets_it(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=["finance.compliance.registration.write:ORG"])
        with as_principal(principal):
            res = await client.put("/api/v1/finance/gst/registration", json=REGISTRATION)
        assert res.status_code == 200
        assert res.json()["state_code"] == "27"

    async def test_an_unentitled_organization_is_402(self, client, org_id) -> None:
        principal = principal_for(
            org_id, perms=["finance.compliance.registration.write:ORG"], entitlements=[]
        )
        with as_principal(principal):
            res = await client.put("/api/v1/finance/gst/registration", json=REGISTRATION)
        assert res.status_code == 402


class TestTheInternalFilingApi:
    """Service-to-service, guarded by a short-lived token addressed to *this* service by name.

    Negative first, as everywhere else here: an internal API reachable without proof of caller is
    an internal API that is not internal.
    """

    PATHS = (
        "/internal/v1/einvoice/filings",
        "/internal/v1/einvoice/filings:cancel",
    )

    @pytest.mark.parametrize("path", PATHS)
    async def test_no_token_is_refused(self, client, org_id, path) -> None:
        body = {
            "organization_id": str(org_id),
            "invoice_id": str(uuid.uuid4()),
            "reason_code": "2",
        }
        res = await client.post(path, json=body)
        assert res.status_code in (401, 403)

    @pytest.mark.parametrize("path", PATHS)
    async def test_a_token_for_another_service_is_refused(self, client, org_id, path) -> None:
        """The audience binding is the whole point of the short-lived token: a credential minted
        for billing must not open finance's internal API."""
        from tests.helpers import service_token_headers

        body = {
            "organization_id": str(org_id),
            "invoice_id": str(uuid.uuid4()),
            "reason_code": "2",
        }
        res = await client.post(path, headers=service_token_headers(audience="billing"), json=body)
        assert res.status_code in (401, 403)

    @pytest.mark.parametrize("path", PATHS)
    async def test_a_user_principal_is_not_a_substitute_for_a_service_token(
        self, client, org_id, path
    ) -> None:
        """Holding every finance permission does not open the internal API — it is a different
        channel with a different credential, not a wider grant."""
        principal = principal_for(org_id, perms=[f"{p}:ORG" for p in FINANCE_READS])
        body = {
            "organization_id": str(org_id),
            "invoice_id": str(uuid.uuid4()),
            "reason_code": "2",
        }
        with as_principal(principal):
            res = await client.post(path, json=body)
        assert res.status_code in (401, 403)

    async def test_the_internal_api_is_absent_from_the_published_contract(self) -> None:
        """It must not appear to be public API, because the gateway does not route it."""
        paths = app.openapi()["paths"]
        assert not [p for p in paths if p.startswith("/internal/")]
