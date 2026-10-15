"""What the Finance customer import leans on server-side.

The import (ADR-0022) is the existing `POST /finance/customers` called once per row, so these are
properties of that endpoint: a phone is normalised (a bare Indian mobile becomes +91), sealed and
only ever returned masked; a GSTIN already held by a live customer is a 409 that says "already
exists", which the import reports as *skipped* — what makes uploading the same sheet twice safe.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app import domain, selectors, services
from app.schemas import CustomerCreate, CustomerUpdate
from platform_core.errors import BusinessRuleProblem, ConflictProblem
from tests.helpers import admin_ctx, principal_for

pytestmark = pytest.mark.integration


def gstin(state: str, pan: str = "AAACB1234C") -> str:
    body = f"{state}{pan}1Z"
    return body + domain.gstin_check_digit(body)


class TestPhoneNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("9876543210", "+919876543210"),
            ("098765 43210", "+919876543210"),
            ("91 98765 43210", "+919876543210"),
            ("+91 98765-43210", "+919876543210"),
            ("0044 20 7946 0958", "+442079460958"),
        ],
    )
    def test_accepted_forms(self, raw, expected) -> None:
        assert domain.normalise_phone(raw) == expected

    @pytest.mark.parametrize("raw", ["12345", "5876543210", "+0123", "phone"])
    def test_refused_forms_never_echo_the_number(self, raw) -> None:
        with pytest.raises(ValueError) as exc:
            domain.normalise_phone(raw)
        assert raw not in str(exc.value)


class TestPhoneOnCustomers:
    async def test_a_bare_mobile_is_sealed_as_plus_91_and_read_masked(
        self, client, admin_session, org_id
    ) -> None:
        res = await client.post(
            "/api/v1/finance/customers",
            json={"name": "Konkan Traders", "phone": "9876543210", "state_code": "27"},
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["phone"].endswith("3210") and "9876543210" not in res.text
        async with admin_session.begin():
            raw = (
                await admin_session.execute(
                    text("SELECT phone_enc FROM finance.customers WHERE id = :id"),
                    {"id": body["id"]},
                )
            ).scalar_one()
        assert b"9876543210" not in bytes(raw)

    async def test_an_unreadable_phone_is_refused(self, session, org_id) -> None:
        with pytest.raises(BusinessRuleProblem, match="phone number is not valid"):
            async with session.begin():
                await services.create_customer(
                    session, admin_ctx(org_id), CustomerCreate(name="X", phone="12345")
                )

    async def test_an_empty_phone_clears_it(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            customer = await services.create_customer(
                session, ctx, CustomerCreate(name="X", phone="+919876543210")
            )
        assert customer.phone_masked
        async with session.begin():
            fetched = await selectors.get_customer(session, org_id, customer.id)
            assert fetched is not None
            await services.update_customer(session, ctx, fetched, CustomerUpdate(phone=""))
        assert fetched.phone_masked == "" and fetched.phone_enc is None


class TestDuplicateGstin:
    async def test_a_second_customer_with_the_same_gstin_is_already_exists(
        self, client, org_id
    ) -> None:
        number = gstin("27")
        first = await client.post(
            "/api/v1/finance/customers", json={"name": "Konkan Traders", "gstin": number}
        )
        assert first.status_code == 201
        again = await client.post(
            "/api/v1/finance/customers", json={"name": "Konkan Traders Pvt", "gstin": number}
        )
        assert again.status_code == 409
        # The import engine reads exactly this sentence as "skipped: already exists".
        assert "already exists" in again.json()["detail"]

    async def test_another_tenant_holding_the_gstin_does_not_block(
        self, client, admin_session, org_id
    ) -> None:
        """The natural key is per organization: someone else's customer is invisible here."""
        import uuid

        other = uuid.uuid4()
        number = gstin("29")
        async with admin_session.begin():
            await admin_session.execute(
                text("SELECT set_config('app.organization_id', :o, true)"), {"o": str(other)}
            )
            await services.create_customer(
                admin_session, admin_ctx(other), CustomerCreate(name="Theirs", gstin=number)
            )
        res = await client.post("/api/v1/finance/customers", json={"name": "Ours", "gstin": number})
        assert res.status_code == 201

    async def test_editing_onto_another_customers_gstin_is_refused(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            await services.create_customer(
                session, ctx, CustomerCreate(name="A", gstin=gstin("27"))
            )
            b = await services.create_customer(session, ctx, CustomerCreate(name="B"))
        b_id = b.id
        with pytest.raises(ConflictProblem):
            async with session.begin():
                fetched = await selectors.get_customer(session, org_id, b_id)
                assert fetched is not None
                await services.update_customer(
                    session, ctx, fetched, CustomerUpdate(gstin=gstin("27"))
                )

    async def test_an_archived_customers_gstin_is_free_again(self, session, org_id) -> None:
        ctx = admin_ctx(org_id)
        async with session.begin():
            a = await services.create_customer(
                session, ctx, CustomerCreate(name="A", gstin=gstin("27"))
            )
            await services.archive_customer(session, ctx, a)
        async with session.begin():
            again = await services.create_customer(
                session, ctx, CustomerCreate(name="A again", gstin=gstin("27"))
            )
        assert again.gstin == gstin("27")


class TestImportAuthz:
    async def test_a_reader_cannot_create_with_a_phone(self, client, org_id) -> None:
        from app.main import app
        from platform_core.auth import get_principal

        app.dependency_overrides[get_principal] = lambda: principal_for(
            org_id, perms=["finance.billing.customer.read:ORG"]
        )
        res = await client.post(
            "/api/v1/finance/customers", json={"name": "X", "phone": "9876543210"}
        )
        assert res.status_code == 403
