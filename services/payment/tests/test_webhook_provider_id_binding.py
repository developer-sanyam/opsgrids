"""A webhook about a *different* payment never drives the charge; one about none yet binds (m-8).

`_record_provider_ids` used to audit a payment id that differed from the one the charge already
held (`payment.charge.provider_id_mismatch`) and then carry on — so the same webhook still moved
the charge's state and staged `commerce.payment.*`. An event about payment Q is not evidence about
the charge that holds payment P. Now it is kept as a `conflict` / `provider_id_conflict` (error log,
audit, 200) with the state untouched. The other direction is unchanged: a charge that holds no
payment id yet learns it from the first verified webhook that names one (a provider call that
outlived its timeout was never finalised) and is driven as before.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.providers.razorpay import RazorpayProvider
from tests.helpers import (
    audit_rows,
    charge_row,
    edited_body,
    emitted,
    force_charge,
    ignored_audits,
    inbox_rows,
    post_webhook,
    seed_charge,
    webhook_body,
)

pytestmark = pytest.mark.integration


class TestADifferentPaymentIdDoesNotDriveTheCharge:
    @pytest.mark.parametrize("event", ["payment.failed", "payment.captured", "payment.authorized"])
    async def test_it_is_a_conflict_and_changes_nothing(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        event: str,
    ) -> None:
        charge = await seed_charge(client, org_id, key=f"holder-{event}")
        before = await charge_row(admin_session, charge["id"])
        audits_before = len(await audit_rows(admin_session, org_id))

        with capture_logs() as logs:
            res = await post_webhook(
                client,
                provider,
                webhook_body(
                    charge, org_id, event=event, provider_payment_id="pay_a_different_payment"
                ),
                "evt_q",
            )

        assert res.status_code == 200, res.text
        assert res.json()["ignored_reason"] == "provider_id_conflict"
        assert res.json()["reconciled"] is False
        # State, ids and events: exactly as they were.
        assert await charge_row(admin_session, charge["id"]) == before
        assert await emitted(admin_session, org_id) == []
        # Kept as evidence, audited, and at error — an operator should look at the other payment.
        (row,) = await inbox_rows(admin_session, org_id)
        assert row["disposition"] == "conflict"
        assert row["disposition_reason"] == "provider_id_conflict"
        (audit,) = ignored_audits(await audit_rows(admin_session, org_id))
        assert audit["after"]["charge_provider_payment_id"] == charge["provider_payment_id"]
        assert audit["after"]["asserted_provider_payment_id"] == "pay_a_different_payment"
        assert len(await audit_rows(admin_session, org_id)) == audits_before + 1
        (line,) = [e for e in logs if e["event"] == "payment.webhook.ignored"]
        assert line["log_level"] == "error" and line["reason"] == "provider_id_conflict"

    async def test_the_same_payment_id_still_drives_it(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """The control: the payment the charge holds is the one that moves it."""
        charge = await seed_charge(client, org_id, key="same-payment")
        res = await post_webhook(
            client, provider, webhook_body(charge, org_id, event="payment.captured"), "evt_p"
        )
        assert res.json()["reconciled"] is True and res.json()["ignored_reason"] is None
        assert (await charge_row(admin_session, charge["id"]))["state"] == "captured"

    async def test_an_event_that_names_no_payment_still_drives_it(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """Nothing to compare is not a conflict: an event whose payment entity has no id is
        matched by the stamped charge id alone, as it always was."""
        charge = await seed_charge(client, org_id, key="no-payment-id")
        body = edited_body(
            webhook_body(charge, org_id, event="payment.failed", error_description="x"),
            lambda b: b["payload"]["payment"]["entity"].pop("id"),
        )
        res = await post_webhook(client, provider, body, "evt_no_id")
        assert res.json()["reconciled"] is True
        assert (await charge_row(admin_session, charge["id"]))["state"] == "failed"


class TestAChargeWithNoPaymentIdYetBindsTheFirstOne:
    @pytest.mark.parametrize("event", ["payment.failed", "payment.captured"])
    async def test_the_first_verified_id_is_bound_and_the_charge_is_driven(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        event: str,
    ) -> None:
        """A provider call that outlived its timeout was never finalised, so the charge holds no
        payment id; the webhook is the first the platform hears of it, and binds it — once, with
        the one `payment.charge.created` audit row — before driving the state."""
        charge = await seed_charge(client, org_id, key=f"unbound-{event}")
        await force_charge(
            admin_session,
            charge["id"],
            state="created",
            provider_payment_id=None,
            provider_order_id=None,
        )

        res = await post_webhook(
            client,
            provider,
            webhook_body(
                charge,
                org_id,
                event=event,
                provider_payment_id="pay_first_heard_of",
                order_id="order_first_heard_of",
                error_description="declined",
            ),
            "evt_first",
        )

        assert res.status_code == 200 and res.json()["reconciled"] is True
        stored = await charge_row(admin_session, charge["id"])
        assert stored["provider_payment_id"] == "pay_first_heard_of"
        assert stored["provider_order_id"] == "order_first_heard_of"
        assert stored["state"] == event.removeprefix("payment.")
        actions = [a["action"] for a in await audit_rows(admin_session, org_id)]
        assert actions.count("payment.charge.created") == 2  # the seed's, and this webhook's
        assert await emitted(admin_session, org_id) == [f"commerce.{event}"]

    async def test_once_bound_a_later_different_id_is_a_conflict(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """Both directions in one charge: the first id binds, and the second, different one — the
        same charge, later — is refused."""
        charge = await seed_charge(client, org_id, key="bind-then-conflict")
        await force_charge(
            admin_session,
            charge["id"],
            state="created",
            provider_payment_id=None,
            provider_order_id=None,
        )
        first = await post_webhook(
            client,
            provider,
            webhook_body(
                charge, org_id, event="payment.authorized", provider_payment_id="pay_first"
            ),
            "evt_1",
        )
        assert first.json()["ignored_reason"] is None
        assert (await charge_row(admin_session, charge["id"]))["provider_payment_id"] == "pay_first"

        second: Any = await post_webhook(
            client,
            provider,
            webhook_body(
                charge,
                org_id,
                event="payment.captured",
                provider_payment_id="pay_second",
                created_at=1_700_000_500,
            ),
            "evt_2",
        )

        assert second.json()["ignored_reason"] == "provider_id_conflict"
        stored = await charge_row(admin_session, charge["id"])
        assert stored["provider_payment_id"] == "pay_first" and stored["state"] == "authorized"
