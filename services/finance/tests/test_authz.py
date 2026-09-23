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
from typing import ClassVar

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
    # Compliance. Deliberately behind their own permissions: seeing the receivable book, seeing
    # what was reported to the tax authority, and seeing what suppliers have claimed against this
    # business are three different questions, and an organization may reasonably answer them for
    # three different people.
    (f"/api/v1/finance/invoices/{uuid.uuid4()}/filings", "finance.compliance.einvoice.read"),
    ("/api/v1/finance/gst/registration", "finance.compliance.registration.read"),
    ("/api/v1/finance/ims/documents", "finance.compliance.ims.read"),
    (f"/api/v1/finance/ims/documents/{uuid.uuid4()}", "finance.compliance.ims.read"),
    # Returns, e-Way and withholding are three more compliance questions with three more answers:
    # what was reported for a period, which consignments may legally be on a road, and how much
    # has already been deducted from what this organization is owed.
    ("/api/v1/finance/gst/returns", "finance.compliance.returns.read"),
    (f"/api/v1/finance/gst/returns/{uuid.uuid4()}", "finance.compliance.returns.read"),
    (f"/api/v1/finance/gst/returns/{uuid.uuid4()}/diff", "finance.compliance.returns.read"),
    ("/api/v1/finance/eway-bills", "finance.compliance.eway.read"),
    (f"/api/v1/finance/invoices/{uuid.uuid4()}/eway-bills", "finance.compliance.eway.read"),
    (f"/api/v1/finance/invoices/{uuid.uuid4()}/eway", "finance.compliance.eway.read"),
    ("/api/v1/finance/gst/withholdings", "finance.compliance.withholding.read"),
    (
        "/api/v1/finance/gst/withholdings/summary?period=2026-08",
        "finance.compliance.withholding.read",
    ),
    ("/api/v1/finance/gst/withholding-rates", "finance.compliance.withholding.read"),
    # The ITC ledger: a fourth compliance question — what input tax credit does this business
    # actually hold — with its own read grant, since generating and filing (which post to it)
    # already have theirs.
    ("/api/v1/finance/gst/itc-ledger", "finance.compliance.itc.read"),
    ("/api/v1/finance/gst/itc-ledger/balance?period=2026-08", "finance.compliance.itc.read"),
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


def _names_a_random_id(path: str) -> bool:
    """Whether a listed path addresses one record by a uuid this suite made up.

    Those resolve to 404 rather than 200, which is the *right* answer — they name nothing — so the
    positive sweep below skips them and the negative sweeps above keep them, where a 403 must beat
    a 404 or the API is confirming which ids exist.
    """
    for segment in path.split("/"):
        try:
            uuid.UUID(segment)
        except ValueError:
            continue
        return True
    return False


class TestFullyGranted:
    async def test_a_fully_granted_caller_reads(self, client, org_id, book) -> None:
        principal = principal_for(org_id, perms=[f"{p}:ORG" for p in FINANCE_READS])
        for path, _ in ENDPOINTS:
            if _names_a_random_id(path):
                continue
            with as_principal(principal):
                res = await client.get(path)
            assert res.status_code == 200, path


#: A valid registration payload — the write these tests try to make without the grant for it.
REGISTRATION = {"gstin": "27AABCA9012D1Z5", "legal_name": "OpsGrids Industries Private Limited"}


class TestTheComplianceWrite:
    """The registration write — one of the two permissions in the catalogue marked dangerous.
    Reading the registration must not imply being able to change it: it decides which identity
    every future invoice is filed under."""

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


class TestTheImsDecision:
    """Accepting a supplier's document claims the input tax credit on it; rejecting one declines
    the credit and sends the document back. Money either way, which is why deciding is a separate
    permission from reading the queue and is likewise marked dangerous."""

    #: Any uuid will do: authorization is refused before the document is ever looked up, which is
    #: what a 403-beats-404 ordering means — otherwise the API confirms which ids exist.
    PATH = f"/api/v1/finance/ims/documents/{uuid.uuid4()}/decisions"
    DECISION: ClassVar[dict[str, str]] = {"action": "accept"}

    async def test_a_reader_cannot_decide(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=[f"{p}:ORG" for p in FINANCE_READS])
        with as_principal(principal):
            res = await client.post(self.PATH, json=self.DECISION)
        assert res.status_code == 403

    async def test_the_queue_read_grant_alone_cannot_decide(self, client, org_id) -> None:
        """The narrowest version of the same point: seeing the queue is not deciding it."""
        principal = principal_for(org_id, perms=["finance.compliance.ims.read:ORG"])
        with as_principal(principal):
            res = await client.post(self.PATH, json=self.DECISION)
        assert res.status_code == 403

    async def test_an_own_grant_is_below_the_org_floor(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=["finance.compliance.ims.decide:OWN"])
        with as_principal(principal):
            res = await client.post(self.PATH, json=self.DECISION)
        assert res.status_code == 403

    async def test_the_decide_grant_reaches_the_document_lookup(self, client, org_id) -> None:
        """Granted, so the refusal is now about the *document* — a 404 for an id that names
        nothing — rather than about the caller. Never a 403."""
        principal = principal_for(org_id, perms=["finance.compliance.ims.decide:ORG"])
        with as_principal(principal):
            res = await client.post(self.PATH, json=self.DECISION)
        assert res.status_code == 404

    async def test_an_unentitled_organization_is_402(self, client, org_id) -> None:
        principal = principal_for(
            org_id, perms=["finance.compliance.ims.decide:ORG"], entitlements=[]
        )
        with as_principal(principal):
            res = await client.post(self.PATH, json=self.DECISION)
        assert res.status_code == 402


class TestTheReturnWrites:
    """Two writes, two permissions, and the split is the point.

    Generating a return states what this platform believes about its own book and can be repeated;
    filing it makes a claim to a government and is terminal. A finance analyst can reasonably hold
    the first without the second, so holding `generate` must not reach `file` — which is what the
    middle tests here assert in both directions.
    """

    GENERATE = "/api/v1/finance/gst/returns"
    FILE = f"/api/v1/finance/gst/returns/{uuid.uuid4()}/filing"
    PERIOD: ClassVar[dict[str, str]] = {"period": "2026-08"}
    ARN: ClassVar[dict[str, str]] = {"arn": "AA270826000001B"}

    async def test_a_reader_cannot_generate(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=[f"{p}:ORG" for p in FINANCE_READS])
        with as_principal(principal):
            res = await client.post(self.GENERATE, json=self.PERIOD)
        assert res.status_code == 403

    async def test_a_reader_cannot_file(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=[f"{p}:ORG" for p in FINANCE_READS])
        with as_principal(principal):
            res = await client.post(self.FILE, json=self.ARN)
        assert res.status_code == 403

    async def test_generating_does_not_imply_filing(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=["finance.compliance.returns.generate:ORG"])
        with as_principal(principal):
            res = await client.post(self.FILE, json=self.ARN)
        assert res.status_code == 403

    async def test_filing_does_not_imply_generating(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=["finance.compliance.returns.file:ORG"])
        with as_principal(principal):
            res = await client.post(self.GENERATE, json=self.PERIOD)
        assert res.status_code == 403

    async def test_an_own_grant_is_below_the_org_floor(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=["finance.compliance.returns.generate:OWN"])
        with as_principal(principal):
            res = await client.post(self.GENERATE, json=self.PERIOD)
        assert res.status_code == 403

    async def test_the_file_grant_reaches_the_return_lookup(self, client, org_id) -> None:
        """Granted, so the refusal is now about the *return* — a 404 for an id that names nothing
        — rather than about the caller. Never a 403."""
        principal = principal_for(org_id, perms=["finance.compliance.returns.file:ORG"])
        with as_principal(principal):
            res = await client.post(self.FILE, json=self.ARN)
        assert res.status_code == 404

    async def test_an_unentitled_organization_is_402(self, client, org_id) -> None:
        principal = principal_for(
            org_id, perms=["finance.compliance.returns.generate:ORG"], entitlements=[]
        )
        with as_principal(principal):
            res = await client.post(self.GENERATE, json=self.PERIOD)
        assert res.status_code == 402


class TestTheInternalFilingApi:
    """Service-to-service, guarded by a short-lived token addressed to *this* service by name.

    Negative first, as everywhere else here: an internal API reachable without proof of caller is
    an internal API that is not internal.
    """

    PATHS = (
        "/internal/v1/einvoice/filings",
        "/internal/v1/einvoice/filings:cancel",
        "/internal/v1/ims/sync",
        "/internal/v1/eway/bills",
        "/internal/v1/eway/bills:cancel",
    )

    @staticmethod
    def _body(org_id) -> dict:
        """One body that satisfies all three routes' schemas, so a refusal is unambiguously about
        the credential rather than about a missing field."""
        return {
            "organization_id": str(org_id),
            "invoice_id": str(uuid.uuid4()),
            "reason_code": "2",
            "period": "2026-08",
            "distance_km": 100,
            "vehicle_no": "MH12AB1234",
        }

    @pytest.mark.parametrize("path", PATHS)
    async def test_no_token_is_refused(self, client, org_id, path) -> None:
        res = await client.post(path, json=self._body(org_id))
        assert res.status_code in (401, 403)

    @pytest.mark.parametrize("path", PATHS)
    async def test_a_token_for_another_service_is_refused(self, client, org_id, path) -> None:
        """The audience binding is the whole point of the short-lived token: a credential minted
        for billing must not open finance's internal API."""
        from tests.helpers import service_token_headers

        res = await client.post(
            path, headers=service_token_headers(audience="billing"), json=self._body(org_id)
        )
        assert res.status_code in (401, 403)

    @pytest.mark.parametrize("path", PATHS)
    async def test_a_user_principal_is_not_a_substitute_for_a_service_token(
        self, client, org_id, path
    ) -> None:
        """Holding every finance permission does not open the internal API — it is a different
        channel with a different credential, not a wider grant."""
        principal = principal_for(org_id, perms=[f"{p}:ORG" for p in FINANCE_READS])
        with as_principal(principal):
            res = await client.post(path, json=self._body(org_id))
        assert res.status_code in (401, 403)

    async def test_the_internal_api_is_absent_from_the_published_contract(self) -> None:
        """It must not appear to be public API, because the gateway does not route it."""
        paths = app.openapi()["paths"]
        assert not [p for p in paths if p.startswith("/internal/")]


#: A registration is required before an invoice or a purchase can be raised — see the tests'
#: own setup, which grants the writer every read/write it needs and registers first.
_REGISTRATION = {"gstin": "27AABCA9012D1Z5", "legal_name": "OpsGrids Industries Private Limited"}


class TestTheCustomerWrites:
    """`finance.billing.customer.write` — the only billing permission that is *not* dangerous:
    a customer is a directory record, not a document."""

    CREATE_BODY: ClassVar[dict[str, str]] = {
        "name": "Authz Test Co",
        "contact_name": "",
        "email": "",
        "city": "",
        "state": "",
    }

    async def test_a_reader_cannot_create_a_customer(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=[f"{p}:ORG" for p in FINANCE_READS])
        with as_principal(principal):
            res = await client.post("/api/v1/finance/customers", json=self.CREATE_BODY)
        assert res.status_code == 403

    async def test_an_own_grant_is_below_the_org_floor(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=["finance.billing.customer.write:OWN"])
        with as_principal(principal):
            res = await client.post("/api/v1/finance/customers", json=self.CREATE_BODY)
        assert res.status_code == 403

    async def test_the_write_grant_creates_one(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=["finance.billing.customer.write:ORG"])
        with as_principal(principal):
            res = await client.post("/api/v1/finance/customers", json=self.CREATE_BODY)
        assert res.status_code == 201

    async def test_patch_and_delete_need_the_same_grant(self, client, org_id) -> None:
        admin = principal_for(org_id, perms=["finance.billing.customer.write:ORG"])
        with as_principal(admin):
            created = await client.post("/api/v1/finance/customers", json=self.CREATE_BODY)
        customer_id = created.json()["id"]

        reader = principal_for(org_id, perms=[f"{p}:ORG" for p in FINANCE_READS])
        with as_principal(reader):
            patch_res = await client.patch(
                f"/api/v1/finance/customers/{customer_id}", json={"city": "Pune"}
            )
            delete_res = await client.delete(f"/api/v1/finance/customers/{customer_id}")
        assert patch_res.status_code == 403
        assert delete_res.status_code == 403

    async def test_an_unentitled_organization_is_402(self, client, org_id) -> None:
        principal = principal_for(
            org_id, perms=["finance.billing.customer.write:ORG"], entitlements=[]
        )
        with as_principal(principal):
            res = await client.post("/api/v1/finance/customers", json=self.CREATE_BODY)
        assert res.status_code == 402


class TestTheInvoiceIssue:
    """Dangerous: a B2B/export/SEZ document stages a real IRP filing request on the outbox in
    the same transaction as the write."""

    BODY: ClassVar[dict] = {
        "customer_id": None,  # filled in per test, once a customer exists
        "issued_date": "2026-01-10",
        "due_date": "2026-01-25",
        "tax_rate_pct": 18,
        "lines": [{"description": "Widgets", "quantity": 1, "unit_price_minor": 1000}],
    }

    async def _customer_id(self, client, org_id) -> str:
        admin = principal_for(org_id, perms=["finance.billing.customer.write:ORG"])
        with as_principal(admin):
            res = await client.post(
                "/api/v1/finance/customers",
                json={
                    "name": "Issuer Co",
                    "contact_name": "",
                    "email": "",
                    "city": "",
                    "state": "",
                },
            )
        return res.json()["id"]

    async def test_a_reader_cannot_issue(self, client, org_id) -> None:
        customer_id = await self._customer_id(client, org_id)
        principal = principal_for(org_id, perms=[f"{p}:ORG" for p in FINANCE_READS])
        with as_principal(principal):
            res = await client.post(
                "/api/v1/finance/invoices", json={**self.BODY, "customer_id": customer_id}
            )
        assert res.status_code == 403

    async def test_an_own_grant_is_below_the_org_floor(self, client, org_id) -> None:
        customer_id = await self._customer_id(client, org_id)
        principal = principal_for(org_id, perms=["finance.billing.invoice.issue:OWN"])
        with as_principal(principal):
            res = await client.post(
                "/api/v1/finance/invoices", json={**self.BODY, "customer_id": customer_id}
            )
        assert res.status_code == 403

    async def test_the_write_grant_issues_one(self, client, org_id) -> None:
        customer_id = await self._customer_id(client, org_id)
        admin = principal_for(org_id, perms=["finance.compliance.registration.write:ORG"])
        with as_principal(admin):
            await client.put("/api/v1/finance/gst/registration", json=_REGISTRATION)
        principal = principal_for(org_id, perms=["finance.billing.invoice.issue:ORG"])
        with as_principal(principal):
            res = await client.post(
                "/api/v1/finance/invoices", json={**self.BODY, "customer_id": customer_id}
            )
        assert res.status_code == 201

    async def test_an_unentitled_organization_is_402(self, client, org_id) -> None:
        customer_id = await self._customer_id(client, org_id)
        principal = principal_for(
            org_id, perms=["finance.billing.invoice.issue:ORG"], entitlements=[]
        )
        with as_principal(principal):
            res = await client.post(
                "/api/v1/finance/invoices", json={**self.BODY, "customer_id": customer_id}
            )
        assert res.status_code == 402


class TestTheRecordPayment:
    async def test_a_reader_cannot_record_a_payment(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=[f"{p}:ORG" for p in FINANCE_READS])
        with as_principal(principal):
            res = await client.post(
                "/api/v1/finance/payments",
                json={
                    "reference": "PMT-AUTHZ-1",
                    "customer_id": str(uuid.uuid4()),
                    "received_date": "2026-01-10",
                    "method": "upi",
                    "allocations": [{"invoice_id": str(uuid.uuid4()), "amount_minor": 100}],
                },
            )
        assert res.status_code == 403

    async def test_an_own_grant_is_below_the_org_floor(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=["finance.billing.payment.record:OWN"])
        with as_principal(principal):
            res = await client.post(
                "/api/v1/finance/payments",
                json={
                    "reference": "PMT-AUTHZ-2",
                    "customer_id": str(uuid.uuid4()),
                    "received_date": "2026-01-10",
                    "method": "upi",
                    "allocations": [{"invoice_id": str(uuid.uuid4()), "amount_minor": 100}],
                },
            )
        assert res.status_code == 403

    async def test_the_write_grant_reaches_the_customer_lookup(self, client, org_id) -> None:
        """Granted, so the refusal is now about the payload — a 404 for a customer that names
        nothing — rather than about the caller. Never a 403."""
        principal = principal_for(org_id, perms=["finance.billing.payment.record:ORG"])
        with as_principal(principal):
            res = await client.post(
                "/api/v1/finance/payments",
                json={
                    "reference": "PMT-AUTHZ-3",
                    "customer_id": str(uuid.uuid4()),
                    "received_date": "2026-01-10",
                    "method": "upi",
                    "allocations": [{"invoice_id": str(uuid.uuid4()), "amount_minor": 100}],
                },
            )
        assert res.status_code == 404


class TestThePurchaseWrite:
    async def test_a_reader_cannot_record_a_purchase(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=["finance.billing.purchase.read:ORG"])
        with as_principal(principal):
            res = await client.post(
                "/api/v1/finance/purchases",
                json={
                    "supplier_gstin": "27AAECO9012H1ZG",
                    "number": "SUP-1",
                    "document_date": "2026-01-05",
                    "taxable_minor": 1000,
                    "tax_rate_pct": 18,
                },
            )
        assert res.status_code == 403

    async def test_an_own_grant_is_below_the_org_floor(self, client, org_id) -> None:
        principal = principal_for(org_id, perms=["finance.billing.purchase.write:OWN"])
        with as_principal(principal):
            res = await client.post(
                "/api/v1/finance/purchases",
                json={
                    "supplier_gstin": "27AAECO9012H1ZG",
                    "number": "SUP-1",
                    "document_date": "2026-01-05",
                    "taxable_minor": 1000,
                    "tax_rate_pct": 18,
                },
            )
        assert res.status_code == 403
