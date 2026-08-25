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
REGISTRATION = {"gstin": "27AABCA9012D1Z5", "legal_name": "Atmora Industries Private Limited"}


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


class TestTheInternalFilingApi:
    """Service-to-service, guarded by a short-lived token addressed to *this* service by name.

    Negative first, as everywhere else here: an internal API reachable without proof of caller is
    an internal API that is not internal.
    """

    PATHS = (
        "/internal/v1/einvoice/filings",
        "/internal/v1/einvoice/filings:cancel",
        "/internal/v1/ims/sync",
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
