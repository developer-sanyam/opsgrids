"""The tenant's payment-provider connection (ADR-0024 §8): sealed at rest, masked on every read,
audited on every change, and reachable only by an operations admin of the same organization.

Negative authorization and cross-tenant cases are here for every route: a UI gate hides a button;
these prove the server refuses.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.collections import services as collection_services
from app.models import MerchantAccount
from platform_core.pii import PiiError
from tests.collections_support import (
    ACCOUNT,
    MERCHANT_KEY_ID,
    MERCHANT_KEY_SECRET,
    MERCHANT_WEBHOOK_SECRET,
    READ,
    RECON,
    RECONCILIATION,
    WRITE,
    as_principal,
    connect,
    principal_for,
)
from tests.helpers import audit_rows, rows

pytestmark = pytest.mark.integration


class TestConnect:
    async def test_connecting_seals_the_secrets_and_only_ever_shows_masks(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        saved = await connect(client, org_id)
        assert saved["configured"] is True
        assert saved["key_id"] == MERCHANT_KEY_ID
        assert saved["mode"] == "test"
        assert saved["key_secret_masked"] == "••••0001"
        assert saved["webhook_secret_masked"] == "••••0001"
        assert saved["webhook_path"] == (
            f"/api/v1/webhooks/razorpay/collections?account={saved['id']}"
        )
        assert "payment.captured" in saved["webhook_events"]
        assert MERCHANT_KEY_SECRET not in str(saved)
        assert MERCHANT_WEBHOOK_SECRET not in str(saved)

        with as_principal(principal_for(org_id, [READ])):
            read = (await client.get(ACCOUNT)).json()
        assert read["id"] == saved["id"] and read["key_secret_masked"] == "••••0001"
        assert MERCHANT_KEY_SECRET not in str(read)

        stored = await rows(
            admin_session,
            "SELECT key_secret_enc, webhook_secret_enc FROM payment.merchant_accounts "
            "WHERE organization_id = :org",
            org=org_id,
        )
        assert len(stored) == 1
        for column in ("key_secret_enc", "webhook_secret_enc"):
            ciphertext = bytes(stored[0][column])
            assert MERCHANT_KEY_SECRET.encode() not in ciphertext
            assert MERCHANT_WEBHOOK_SECRET.encode() not in ciphertext

        audits = [
            a for a in await audit_rows(admin_session, org_id) if a["entity"] == "merchant_account"
        ]
        assert [a["action"] for a in audits] == ["payment.merchant_account.connected"]
        assert "secret" not in str(audits[0]["after"]).lower()

    async def test_saving_again_rotates_in_place_and_keeps_the_webhook_url(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        first = await connect(client, org_id)
        second = await connect(
            client, org_id, key_secret="rotated-key-secret-9999", webhook_secret="rotated-hook-8888"
        )
        assert second["id"] == first["id"]
        assert second["webhook_path"] == first["webhook_path"]
        assert second["key_secret_masked"] == "••••9999"
        assert second["rotated_at"] is not None
        actions = [a["action"] for a in await audit_rows(admin_session, org_id)]
        assert actions.count("payment.merchant_account.rotated") == 1

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("key_id", "not-a-razorpay-key"),
            ("key_id", "rzp_live_LiveKeyNotAllowed1"),
            ("key_secret", "x7Qp2"),
            ("webhook_secret", "has spaces in it"),
        ],
    )
    async def test_bad_or_live_credentials_are_refused(
        self, client: AsyncClient, org_id: uuid.UUID, field: str, value: str
    ) -> None:
        body = {
            "key_id": MERCHANT_KEY_ID,
            "key_secret": MERCHANT_KEY_SECRET,
            "webhook_secret": MERCHANT_WEBHOOK_SECRET,
            field: value,
        }
        with as_principal(principal_for(org_id, [READ, WRITE])):
            response = await client.put(ACCOUNT, json=body)
        assert response.status_code == 422, response.text
        assert value not in response.text or field == "key_id"

    async def test_removing_wipes_the_secrets(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await connect(client, org_id)
        with as_principal(principal_for(org_id, [READ, WRITE])):
            assert (await client.delete(ACCOUNT)).status_code == 204
            assert (await client.get(ACCOUNT)).json()["configured"] is False
            assert (await client.delete(ACCOUNT)).status_code == 404
        stored = await rows(
            admin_session,
            "SELECT status, key_secret_enc, webhook_secret_enc, deleted_at "
            "FROM payment.merchant_accounts WHERE organization_id = :org",
            org=org_id,
        )
        assert stored[0]["status"] == "removed"
        assert stored[0]["key_secret_enc"] is None and stored[0]["webhook_secret_enc"] is None
        assert stored[0]["deleted_at"] is not None


class TestSealingIsBoundToTheRow:
    def test_a_ciphertext_moved_to_another_account_does_not_open(self, org_id: uuid.UUID) -> None:
        account_id = uuid.uuid4()
        sealed = collection_services._seal(org_id, account_id, "key_secret", MERCHANT_KEY_SECRET)
        genuine = MerchantAccount(
            id=account_id, organization_id=org_id, key_secret_enc=sealed, status="active"
        )
        assert collection_services._open(genuine, "key_secret") == MERCHANT_KEY_SECRET
        elsewhere = MerchantAccount(
            id=uuid.uuid4(), organization_id=uuid.uuid4(), key_secret_enc=sealed, status="active"
        )
        with pytest.raises(PiiError):
            collection_services._open(elsewhere, "key_secret")
        # Nor does it open as the *other* secret of the same row.
        swapped = MerchantAccount(
            id=account_id, organization_id=org_id, webhook_secret_enc=sealed, status="active"
        )
        with pytest.raises(PiiError):
            collection_services._open(swapped, "webhook_secret")


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path", "perms"),
        [
            ("GET", ACCOUNT, []),
            ("PUT", ACCOUNT, [READ]),
            ("DELETE", ACCOUNT, [READ]),
            ("GET", RECONCILIATION, [READ, WRITE]),
            # An OWN-scoped grant is below every route's ORG floor.
            ("GET", ACCOUNT, ["operations.payments.merchant_account.read:OWN"]),
        ],
    )
    async def test_every_route_refuses_a_caller_without_its_permission(
        self, client: AsyncClient, org_id: uuid.UUID, method: str, path: str, perms: list[str]
    ) -> None:
        body = (
            {
                "key_id": MERCHANT_KEY_ID,
                "key_secret": MERCHANT_KEY_SECRET,
                "webhook_secret": MERCHANT_WEBHOOK_SECRET,
            }
            if method == "PUT"
            else None
        )
        with as_principal(principal_for(org_id, perms)):
            response = await client.request(method, path, json=body)
        assert response.status_code == 403, response.text

    async def test_an_organization_without_operations_is_refused(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, [READ, WRITE, RECON], entitlements=["crm"])):
            assert (await client.get(ACCOUNT)).status_code == 402

    async def test_another_organization_can_neither_see_nor_remove_it(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        await connect(client, org_id)
        with as_principal(principal_for(org_b, [READ, WRITE, RECON])):
            assert (await client.get(ACCOUNT)).json()["configured"] is False
            assert (await client.delete(ACCOUNT)).status_code == 404
            assert (await client.get(RECONCILIATION)).status_code == 409
        still = await rows(
            admin_session,
            "SELECT status FROM payment.merchant_accounts WHERE organization_id = :org",
            org=org_id,
        )
        assert still == [{"status": "active"}]
