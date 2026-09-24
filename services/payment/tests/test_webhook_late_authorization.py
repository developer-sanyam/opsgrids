"""A bank's late authorisation: the SAME payment goes failed -> authorized -> captured (M-1).

Razorpay can report a payment `payment.failed` and later, when the issuing bank authorises it after
all, `payment.authorized` and then `payment.captured`. The platform acts on the failure at once —
`commerce.payment.failed` opens dunning, then suspension. Before this, the `authorized` step was a
silent no-op and the capture was answered 200 `illegal_transition` and lost: the customer had paid,
was suspended, and was debited again by the dunning retry.

Now `failed -> captured` is applied when the capture names **the payment the charge already holds**
(in addition to D3's platform-decided failures), audited `payment.charge.captured_after_provider_
failure`, and announced with `commerce.payment.captured` exactly as a normal capture is —
subscription's recovery is bound to every capture. The `authorized` step is an error-level
`payment.webhook.late_authorization`, so an operator sees it before the capture arrives.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.providers.razorpay import RazorpayProvider
from tests.helpers import (
    audit_rows,
    charge_row,
    emitted,
    force_charge,
    inbox_rows,
    post_webhook,
    rows,
    seed_charge,
    webhook_body,
)

pytestmark = pytest.mark.integration


def _errors(logs: list[dict[str, object]], event: str) -> list[dict[str, object]]:
    return [e for e in logs if e["event"] == event and e["log_level"] == "error"]


class TestFailedThenAuthorizedThenCaptured:
    async def test_the_same_payment_ends_captured_with_one_captured_event(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        subscription_id = uuid.uuid4()
        charge = await seed_charge(
            client, org_id, key="late-auth", subscription_id=subscription_id, product_key="hrms"
        )
        failed = webhook_body(
            charge,
            org_id,
            event="payment.failed",
            error_description="bank_timeout",
            subscription_id=subscription_id,
            product_key="hrms",
        )
        assert (await post_webhook(client, provider, failed, "evt_f")).json()["reconciled"] is True

        # --- the authorization: no state change, but not silent ---------------------------
        with capture_logs() as logs:
            auth = await post_webhook(
                client,
                provider,
                webhook_body(charge, org_id, event="payment.authorized", created_at=1_700_000_100),
                "evt_a",
            )
        assert auth.status_code == 200
        assert auth.json()["reconciled"] is False and auth.json()["ignored_reason"] is None
        (alarm,) = _errors(logs, "payment.webhook.late_authorization")
        assert alarm["charge_id"] == charge["id"] and alarm["organization_id"] == str(org_id)
        assert (await charge_row(admin_session, charge["id"]))["state"] == "failed"
        assert await emitted(admin_session, org_id) == ["commerce.payment.failed"]
        assert "payment.webhook.late_authorization" in [
            a["action"] for a in await audit_rows(admin_session, org_id)
        ]

        # --- the capture: the money moved, so the record follows it ----------------------
        with capture_logs() as logs:
            cap = await post_webhook(
                client,
                provider,
                webhook_body(
                    charge,
                    org_id,
                    event="payment.captured",
                    created_at=1_700_000_200,
                    subscription_id=subscription_id,
                    product_key="hrms",
                ),
                "evt_c",
            )
        assert cap.status_code == 200, cap.text
        assert cap.json()["ignored_reason"] is None and cap.json()["reconciled"] is True
        assert len(_errors(logs, "payment.charge.captured_after_provider_failure")) == 1

        stored = await charge_row(admin_session, charge["id"])
        assert stored["state"] == "captured" and stored["failure_reason"] is None
        assert stored["provider_payment_id"] == charge["provider_payment_id"]

        # One failed, one captured — exactly as a normal capture would emit it, and nothing else:
        # no second failure (dunning's retry is subscription's to cancel on this capture).
        assert await emitted(admin_session, org_id) == [
            "commerce.payment.failed",
            "commerce.payment.captured",
        ]
        (captured_event,) = await rows(
            admin_session,
            "SELECT payload FROM payment.outbox WHERE organization_id = :o "
            "AND event_type = 'commerce.payment.captured'",
            o=org_id,
        )
        payload = captured_event["payload"]
        assert payload["state"] == "captured" and payload["failure_reason"] is None
        assert payload["subscription_id"] == str(subscription_id)
        assert payload["product_key"] == "hrms"
        assert payload["provider_payment_id"] == charge["provider_payment_id"]
        # No second debit path: the same single charge, no new one staged.
        (count,) = await rows(
            admin_session,
            "SELECT count(*) AS n FROM payment.charges WHERE organization_id = :o",
            o=org_id,
        )
        assert count["n"] == 1

        actions = [a["action"] for a in await audit_rows(admin_session, org_id)]
        assert actions.count("payment.charge.captured_after_provider_failure") == 1
        (audit,) = [
            a
            for a in await audit_rows(admin_session, org_id)
            if a["action"] == "payment.charge.captured_after_provider_failure"
        ]
        assert audit["before"] == {"state": "failed", "failure_reason": "bank_timeout"}
        assert audit["after"]["state"] == "captured"
        assert [r["disposition"] for r in await inbox_rows(admin_session, org_id)] == [
            "processed"
        ] * 3

    async def test_a_replay_of_the_capture_is_a_duplicate_and_changes_nothing(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="late-auth-replay")
        failed = webhook_body(charge, org_id, event="payment.failed", error_description="x")
        await post_webhook(client, provider, failed, "evt_f")
        capture = webhook_body(charge, org_id, event="payment.captured", created_at=1_700_000_200)
        assert (await post_webhook(client, provider, capture, "evt_c")).json()["reconciled"]

        again = await post_webhook(client, provider, capture, "evt_c")
        # ...and under a fresh delivery id (a redelivery Razorpay numbered differently): the charge
        # is already captured, so it is a no-op on state, not a second event.
        fresh = await post_webhook(client, provider, capture, "evt_c_fresh")

        assert again.json()["duplicate"] is True and again.json()["reconciled"] is False
        assert fresh.json()["duplicate"] is False and fresh.json()["reconciled"] is False
        assert await emitted(admin_session, org_id) == [
            "commerce.payment.failed",
            "commerce.payment.captured",
        ]
        actions = [a["action"] for a in await audit_rows(admin_session, org_id)]
        assert actions.count("payment.charge.captured_after_provider_failure") == 1

    async def test_the_capture_may_arrive_without_the_authorization_in_between(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="late-auth-direct")
        await post_webhook(
            client,
            provider,
            webhook_body(charge, org_id, event="payment.failed", error_description="x"),
            "evt_f",
        )
        cap = await post_webhook(
            client, provider, webhook_body(charge, org_id, event="payment.captured"), "evt_c"
        )
        assert cap.json()["reconciled"] is True
        assert (await charge_row(admin_session, charge["id"]))["state"] == "captured"


class TestTheAuthorizationAlarmIsSpecific:
    async def test_an_authorization_that_follows_a_capture_is_ordinary_reordering(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """Webhooks are not ordered: an `authorized` delivered after the `captured` is noise, not a
        problem, and must not raise an error-level alarm an operator would learn to ignore."""
        charge = await seed_charge(client, org_id, key="reordered")
        await post_webhook(
            client, provider, webhook_body(charge, org_id, event="payment.captured"), "evt_c"
        )
        with capture_logs() as logs:
            auth = await post_webhook(
                client,
                provider,
                webhook_body(charge, org_id, event="payment.authorized", created_at=1_600_000_000),
                "evt_a",
            )
        assert auth.status_code == 200 and auth.json()["ignored_reason"] is None
        assert not [e for e in logs if e["log_level"] in ("warning", "error")]
        assert (await charge_row(admin_session, charge["id"]))["state"] == "captured"

    @pytest.mark.parametrize("reason", ["attempts_exhausted", "lookup_unavailable"])
    async def test_an_authorization_for_a_charge_the_platform_failed_alarms_too(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        reason: str,
    ) -> None:
        charge = await seed_charge(client, org_id, key=f"late-auth-{reason}")
        await force_charge(admin_session, charge["id"], state="failed", failure_reason=reason)

        with capture_logs() as logs:
            await post_webhook(
                client, provider, webhook_body(charge, org_id, event="payment.authorized"), "evt_a"
            )

        assert len(_errors(logs, "payment.webhook.late_authorization")) == 1
        stored = await charge_row(admin_session, charge["id"])
        assert stored["state"] == "failed" and stored["failure_reason"] == reason

    async def test_the_charge_of_a_different_payment_is_a_conflict_not_a_late_authorization(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="late-auth-other")
        await post_webhook(
            client,
            provider,
            webhook_body(charge, org_id, event="payment.failed", error_description="x"),
            "evt_f",
        )
        with capture_logs() as logs:
            res = await post_webhook(
                client,
                provider,
                webhook_body(
                    charge, org_id, event="payment.authorized", provider_payment_id="pay_other"
                ),
                "evt_a",
            )
        assert res.json()["ignored_reason"] == "provider_id_conflict"
        assert not _errors(logs, "payment.webhook.late_authorization")


async def test_the_late_capture_changes_no_column_beyond_state_and_failure_reason(
    client: AsyncClient,
    provider: RazorpayProvider,
    admin_session: AsyncSession,
    org_id: uuid.UUID,
) -> None:
    """The amount, currency, invoice and subscription a charge carries are what the recovery reads;
    a late capture must move the state and clear the failure reason and touch nothing else."""
    charge = await seed_charge(client, org_id, key="late-auth-columns", amount_minor=4900)
    await post_webhook(
        client,
        provider,
        webhook_body(
            charge, org_id, event="payment.failed", amount_minor=4900, error_description="x"
        ),
        "evt_f",
    )
    before = (
        await rows(
            admin_session,
            "SELECT amount_minor, currency, invoice_id, subscription_id, product_key, "
            "idempotency_key, provider_order_id FROM payment.charges WHERE id = :i",
            i=uuid.UUID(charge["id"]),
        )
    )[0]

    await post_webhook(
        client,
        provider,
        webhook_body(charge, org_id, event="payment.captured", amount_minor=4900),
        "evt_c",
    )

    after = (
        await rows(
            admin_session,
            "SELECT amount_minor, currency, invoice_id, subscription_id, product_key, "
            "idempotency_key, provider_order_id FROM payment.charges WHERE id = :i",
            i=uuid.UUID(charge["id"]),
        )
    )[0]
    assert after == before
