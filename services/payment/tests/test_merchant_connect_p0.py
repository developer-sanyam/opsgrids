"""Merchant-connect, Rollout P0 (ADR-0032): the fixes that must land **before any second collections
provider value is accepted**, with no behaviour change for a Razorpay merchant.

* **Provider and mode are fixed for the life of a `merchant_accounts` row.** `set_merchant_account`
  used to overwrite `provider`, `key_id` and `mode` on the live row: a PUT naming another provider
  would have repointed every earlier charge — each names its account by `merchant_account_id` — at
  keys of a provider where its Orders do not exist. Switching is now remove, then connect (409
  otherwise). A key id may still change on rotation, within the same provider and mode.
* **`_account_for` asserts `account.provider == charge.provider`**, behind the immutability.
* **The collections wire is per provider**, with the legacy `razorpay` meaning Razorpay live; the
  two OpenAPI literals stay compatible. No second provider value is added.
* **`key_secret` / `webhook_secret` are never audited**, at any depth.
* **Unique indexes are provider-scoped** (a widening, no backfill): two providers' id spaces are
  unrelated.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from platform_core.audit import REDACTED, redact
from tests.collections_support import (
    ACCOUNT,
    MERCHANT_KEY_ID,
    MERCHANT_KEY_SECRET,
    MERCHANT_WEBHOOK_SECRET,
    WRITE,
    as_principal,
    checkout,
    connect,
    internal_base,
    operations_token,
    principal_for,
)
from tests.helpers import audit_rows, rows

pytestmark = pytest.mark.integration

LIVE_KEY_ID = "rzp_live_MerchantLive001"
REPO = Path(__file__).resolve().parents[3]


async def _account(admin: AsyncSession, org: uuid.UUID) -> dict[str, Any]:
    found = await rows(
        admin,
        "SELECT id, provider, key_id, mode, status, rotated_at FROM payment.merchant_accounts "
        "WHERE organization_id = :org AND deleted_at IS NULL",
        org=org,
    )
    return found[0]


async def _put(client: AsyncClient, org: uuid.UUID, **over: Any) -> Any:
    body = {
        "provider": "razorpay",
        "key_id": MERCHANT_KEY_ID,
        "key_secret": MERCHANT_KEY_SECRET,
        "webhook_secret": MERCHANT_WEBHOOK_SECRET,
        **over,
    }
    with as_principal(principal_for(org, [WRITE])):
        return await client.put(ACCOUNT, json=body)


class TestProviderAndModeAreImmutable:
    async def test_a_put_that_would_change_the_providers_mode_is_a_409_and_changes_nothing(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        saved = await connect(client, org_id)
        monkeypatch.setattr(settings(), "collections_allow_live_keys", True)
        before = await _account(admin_session, org_id)

        response = await _put(client, org_id, key_id=LIVE_KEY_ID)

        assert response.status_code == 409, response.text
        assert "cannot be changed" in response.json()["detail"]
        assert await _account(admin_session, org_id) == before
        assert before["id"] == uuid.UUID(saved["id"]) and before["mode"] == "test"
        actions = [a["action"] for a in await audit_rows(admin_session, org_id)]
        assert "payment.merchant_account.rotated" not in actions

    async def test_a_row_held_under_another_provider_is_never_overwritten_by_a_put(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The body can only say `razorpay` today, so the dangerous input is the *row*: one that is
        held under another provider must refuse a PUT instead of being rewritten into Razorpay's."""
        await connect(client, org_id)
        async with admin_session.begin():
            from sqlalchemy import text

            await admin_session.execute(
                text(
                    "UPDATE payment.merchant_accounts SET provider = 'cashfree' "
                    "WHERE organization_id = :o"
                ),
                {"o": org_id},
            )
        response = await _put(client, org_id)
        assert response.status_code == 409
        assert (await _account(admin_session, org_id))["provider"] == "cashfree"

    async def test_a_key_id_may_change_on_rotation_within_the_same_provider_and_mode(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        first = await connect(client, org_id)
        rotated = await _put(client, org_id, key_id="rzp_test_RotatedKey001")
        assert rotated.status_code == 200, rotated.text
        row = await _account(admin_session, org_id)
        assert row["key_id"] == "rzp_test_RotatedKey001" and row["rotated_at"] is not None
        assert str(row["id"]) == first["id"]  # the row, and so the webhook URL, are kept

    async def test_switching_is_remove_then_connect_and_makes_a_new_row(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        first = await connect(client, org_id)
        monkeypatch.setattr(settings(), "collections_allow_live_keys", True)
        with as_principal(principal_for(org_id, [WRITE])):
            assert (await client.delete(ACCOUNT)).status_code == 204
        second = await _put(client, org_id, key_id=LIVE_KEY_ID)
        assert second.status_code == 200, second.text
        assert second.json()["id"] != first["id"] and second.json()["mode"] == "live"

    async def test_no_second_collections_provider_value_is_accepted(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        assert (await _put(client, org_id, provider="cashfree")).status_code == 422


class TestAChargeIsOnlyEverAskedAboutThroughItsOwnProvider:
    async def test_a_collection_taken_through_another_provider_is_a_409_not_a_misbuilt_adapter(
        self,
        client: AsyncClient,
        merchant_providers: Any,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        engines: None,
    ) -> None:
        await connect(client, org_id)
        order_id = uuid.uuid4()
        opened = (await checkout(client, org_id, order_id)).json()
        charge_id = opened["collection"]["id"]
        async with admin_session.begin():
            from sqlalchemy import text

            await admin_session.execute(
                text("UPDATE payment.charges SET provider = 'cashfree' WHERE id = :id"),
                {"id": uuid.UUID(charge_id)},
            )
        response = await client.post(
            f"{internal_base(org_id, order_id)}/charges/{charge_id}/sync",
            headers=operations_token(),
        )
        assert response.status_code == 409, response.text
        assert "different payment provider" in response.json()["detail"]


class TestTheCollectionsWireIsPerProvider:
    def test_the_per_provider_reading_and_its_legacy_value(self) -> None:
        from app.config import Settings

        assert Settings(collections_wire="razorpay").collections_wire_for("razorpay") == "live"
        assert Settings(collections_wire="stub").collections_wire_for("razorpay") == "stub"
        assert Settings(collections_wire="razorpay").collections_wire_for("cashfree") == "stub"

    @pytest.mark.parametrize("service", ["payment", "operations"])
    def test_both_openapi_literals_are_unchanged_and_so_compatible(self, service: str) -> None:
        """A tenant-facing contract: `wire` stays `stub | razorpay` in payment's and operations'
        documents, so a client built against either keeps working."""
        document = json.loads((REPO / f"libs/contracts/openapi/{service}.json").read_text())
        literals = [
            props["wire"]["enum"]
            for props in (
                s.get("properties", {}) for s in document["components"]["schemas"].values()
            )
            if "wire" in props and "enum" in props["wire"]
        ]
        assert literals, f"{service} no longer publishes a `wire` literal"
        assert all(enum == ["stub", "razorpay"] for enum in literals)


class TestSecretsAreNeverAudited:
    @pytest.mark.parametrize("key", ["key_secret", "webhook_secret", "secret_key", "KEY_SECRET"])
    def test_the_merchant_secret_names_are_redacted_at_any_depth(self, key: str) -> None:
        shown = redact(
            {key: "hunter2-value", "keep": "visible", "deep": [{"x": {key: "deep-secret"}}]}
        )
        assert shown[key] == REDACTED
        assert shown["deep"][0]["x"][key] == REDACTED
        assert shown["keep"] == "visible"
        assert "hunter2" not in json.dumps(shown) and "deep-secret" not in json.dumps(shown)

    async def test_connecting_leaves_no_secret_in_any_audit_row(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await connect(client, org_id)
        await _put(
            client, org_id, key_secret="rotated-secret-9999", webhook_secret="rotated-hook-8888"
        )
        text = json.dumps(await audit_rows(admin_session, org_id), default=str)
        for secret in (
            MERCHANT_KEY_SECRET,
            MERCHANT_WEBHOOK_SECRET,
            "rotated-secret-9999",
            "rotated-hook-8888",
        ):
            assert secret not in text


class TestUniqueIndexesAreProviderScoped:
    async def _insert_charge(
        self,
        admin: AsyncSession,
        org: uuid.UUID,
        provider: str,
        *,
        payment: str,
        order: str,
        key: str,
    ) -> None:
        from sqlalchemy import text

        async with admin.begin():
            await admin.execute(
                text(
                    "INSERT INTO payment.charges (id, organization_id, provider, "
                    "provider_payment_id, provider_order_id, amount_minor, currency, state, "
                    "idempotency_key, flow, payee) VALUES (:id, :org, :p, :pay, :ord, 100, 'INR', "
                    "'created', :key, 'debit', 'platform')"
                ),
                {
                    "id": uuid.uuid4(),
                    "org": org,
                    "p": provider,
                    "pay": payment,
                    "ord": order,
                    "key": key,
                },
            )

    async def test_the_same_provider_ids_at_two_providers_are_two_charges_and_at_one_are_a_conflict(
        self, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        await self._insert_charge(
            admin_session, org_id, "razorpay", payment="p-1", order="o-1", key="k1"
        )
        await self._insert_charge(
            admin_session, org_id, "cashfree", payment="p-1", order="o-1", key="k2"
        )
        with pytest.raises(IntegrityError, match="uq_charges_org_provider_payment_id"):
            await self._insert_charge(
                admin_session, org_id, "razorpay", payment="p-1", order="o-2", key="k3"
            )
        with pytest.raises(IntegrityError, match="uq_charges_org_provider_order_id"):
            await self._insert_charge(
                admin_session, org_id, "cashfree", payment="p-2", order="o-1", key="k4"
            )

    async def test_the_webhook_inbox_key_is_per_provider(
        self, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        from sqlalchemy import text

        async def insert(provider: str, event_id: str) -> None:
            async with admin_session.begin():
                await admin_session.execute(
                    text(
                        "INSERT INTO payment.provider_events (id, organization_id, provider, "
                        "provider_event_id, event_name, payload) "
                        "VALUES (:id, :org, :p, :e, 'x', '{}'::jsonb)"
                    ),
                    {"id": uuid.uuid4(), "org": org_id, "p": provider, "e": event_id},
                )

        await insert("razorpay", "evt_same")
        await insert("cashfree", "evt_same")  # another provider's id space: a different event
        with pytest.raises(IntegrityError, match="uq_provider_events_org_event_id"):
            await insert("razorpay", "evt_same")

    async def test_the_constraint_and_index_names_are_kept(
        self, admin_session: AsyncSession
    ) -> None:
        found = await rows(
            admin_session,
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'payment' "
            "AND indexname IN ('uq_charges_org_provider_payment_id', "
            "'uq_charges_org_provider_order_id', "
            "'uq_mandates_org_provider_mandate_id', 'uq_refunds_org_provider_refund_id', "
            "'uq_provider_events_org_event_id')",
        )
        defs = {r["indexname"]: r["indexdef"] for r in found}
        assert len(defs) == 5
        for name, definition in defs.items():
            assert "provider" in definition.split("(", 1)[1], name
        assert (
            "(organization_id, provider, provider_event_id)"
            in defs["uq_provider_events_org_event_id"]
        )
