"""A signed body filed under a delivery id it does not own cannot swallow the real event (m-1).

`X-Razorpay-Event-Id` is not covered by Razorpay's HMAC — only the body is. Someone holding *any*
genuine signed body for an organization (an intercepted delivery) could file it under the id of a
delivery they expect next, and the real event, arriving under that id, would be acked `duplicate`
and never applied. The duplicate path now checks that the stored row for a delivery id is *this
event*: it compares the identity derived from the stored signed body with the one derived from this
delivery's. A true redelivery matches and stays a plain duplicate; a different event is re-keyed on
its own derived id (error-logged) and processed.

What remains is documented in the ADR as an accepted residual: replaying an intercepted body under
fresh ids is state-safe (the charge's state is idempotent) but adds inbox rows and log lines.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app import domain, services
from app.providers.razorpay import RazorpayProvider
from tests.helpers import (
    audit_rows,
    charge_row,
    edited_body,
    emitted,
    ignored_audits,
    inbox_rows,
    post_webhook,
    seed_charge,
    webhook_body,
)

pytestmark = pytest.mark.integration


def _reused(logs: list[dict[str, object]]) -> list[dict[str, object]]:
    return [e for e in logs if e["event"] == "payment.webhook.event_id_reused"]


class TestAReusedDeliveryId:
    async def test_cannot_swallow_a_different_event(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        x = await seed_charge(client, org_id, key="reuse-x")
        y = await seed_charge(client, org_id, key="reuse-y")
        # Any genuine signed body for the organization, filed under the id of the delivery to come.
        stale = webhook_body(x, org_id, event="payment.authorized")
        pre = await post_webhook(client, provider, stale, "evt_Y")
        assert pre.json()["duplicate"] is False

        real = webhook_body(y, org_id, event="payment.captured", created_at=1_700_000_500)
        with capture_logs() as logs:
            res = await post_webhook(client, provider, real, "evt_Y")

        assert res.status_code == 200
        assert res.json()["duplicate"] is False and res.json()["reconciled"] is True
        assert res.json()["provider_event_id"].startswith("derived:")
        (line,) = _reused(logs)
        assert line["log_level"] == "error"
        assert line["provider_event_id"] == "evt_Y" and line["event_name"] == "payment.captured"
        assert line["organization_id"] == str(org_id)
        assert (await charge_row(admin_session, y["id"]))["state"] == "captured"
        assert await emitted(admin_session, org_id) == ["commerce.payment.captured"]

    async def test_a_true_redelivery_stays_a_plain_duplicate_on_its_header_id(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="reuse-true")
        body = webhook_body(charge, org_id, event="payment.captured")
        first = await post_webhook(client, provider, body, "evt_Z")
        assert first.json()["reconciled"] is True

        with capture_logs() as logs:
            again = await post_webhook(client, provider, body, "evt_Z")

        assert again.json()["duplicate"] is True and again.json()["reconciled"] is False
        assert again.json()["provider_event_id"] == "evt_Z"
        assert not _reused(logs), "a true redelivery is not a reuse and must not page"

    async def test_every_later_delivery_of_the_re_keyed_event_is_a_plain_duplicate(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        x = await seed_charge(client, org_id, key="rekey-x")
        y = await seed_charge(client, org_id, key="rekey-y")
        await post_webhook(
            client, provider, webhook_body(x, org_id, event="payment.authorized"), "evt_Y"
        )
        real = webhook_body(y, org_id, event="payment.captured", created_at=1_700_000_500)
        first = await post_webhook(client, provider, real, "evt_Y")
        derived = first.json()["provider_event_id"]

        # Redelivered under the same (reused) header, and again with no header at all.
        under_header = await post_webhook(client, provider, real, "evt_Y")
        headerless = await post_webhook(client, provider, real, None)

        assert under_header.json()["duplicate"] is True
        assert under_header.json()["provider_event_id"] == derived
        assert headerless.json()["duplicate"] is True
        assert headerless.json()["provider_event_id"] == derived
        assert await emitted(admin_session, org_id) == ["commerce.payment.captured"]
        assert len(await inbox_rows(admin_session, org_id)) == 2  # the stale one, and the re-keyed


class TestTheAcceptedResidual:
    """What the reuse check deliberately does **not** stop (ADR-0004, accepted residual): a body
    intercepted in flight can be replayed under *fresh* delivery ids. These pin the half that must
    stay true — it is safe for state and money — and document the half that is accepted: it adds
    inbox rows and, for a conflicting body, repeats an audit row and an error line. Bounding that
    would take a per-body uniqueness the design does not have."""

    async def test_a_replayed_capture_under_fresh_ids_is_state_idempotent(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="residual-capture")
        body = webhook_body(charge, org_id, event="payment.captured")
        first = await post_webhook(client, provider, body, "evt_real")
        assert first.json()["reconciled"] is True

        for i in range(3):
            replay = await post_webhook(client, provider, body, f"evt_forged_{i}")
            assert replay.status_code == 200
            assert replay.json()["duplicate"] is False and replay.json()["reconciled"] is False

        # Safe: one capture, one event, one audit row for it.
        assert (await charge_row(admin_session, charge["id"]))["state"] == "captured"
        assert await emitted(admin_session, org_id) == ["commerce.payment.captured"]
        actions = [a["action"] for a in await audit_rows(admin_session, org_id)]
        assert actions.count("payment.charge.captured") == 1
        # Accepted: every replay is a full inbox row of its own.
        assert len(await inbox_rows(admin_session, org_id)) == 4

    async def test_a_replayed_conflict_under_fresh_ids_repeats_its_audit_and_error_line(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="residual-conflict")
        body = webhook_body(charge, org_id, event="payment.captured", amount_minor=1)

        with capture_logs() as logs:
            for i in range(3):
                await post_webhook(client, provider, body, f"evt_x{i}")

        pages = [
            e for e in logs if e["event"] == "payment.webhook.ignored" and e["log_level"] == "error"
        ]
        assert len(pages) == 3  # accepted noise...
        assert len(ignored_audits(await audit_rows(admin_session, org_id))) == 3
        # ...and still no state change and no event, however many times it is replayed.
        assert (await charge_row(admin_session, charge["id"]))["state"] == "authorized"
        assert await emitted(admin_session, org_id) == []


class TestTheIdentityIsStableThroughJsonb:
    """The comparison reads the stored body back out of JSONB, which reorders keys and reformats
    numbers. The identity must not depend on any of that."""

    @staticmethod
    def _rich(body: bytes) -> bytes:
        def enrich(parsed: dict[str, Any]) -> None:
            entity = parsed["payload"]["payment"]["entity"]
            entity.update(
                {
                    "description": "café ☕ — 日本語 é\U0001f600",
                    "fee": 23.10,
                    "tax": 3.5e0,
                    "huge": 12345678901234567890,
                    "exp": 1e2,
                    "nested": {"z": [1, 2.50, {"deep": None, "ü": 0.1}], "a": {"b": {"c": [True]}}},
                }
            )
            parsed["contains"] = ["payment"]
            parsed["zzz_last"] = 1
            parsed["aaa_first"] = {"k": 1.0}

        return edited_body(body, enrich)

    async def test_a_body_with_nested_numbers_and_unicode_is_a_plain_duplicate_on_redelivery(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="jsonb-rich")
        body = self._rich(webhook_body(charge, org_id, event="payment.captured"))
        first = await post_webhook(client, provider, body, "evt_rich")
        assert first.json()["reconciled"] is True

        with capture_logs() as logs:
            again = await post_webhook(client, provider, body, "evt_rich")

        assert again.json()["duplicate"] is True
        assert again.json()["provider_event_id"] == "evt_rich"
        assert not _reused(logs), "JSONB re-serialisation changed the derived identity"

    async def test_the_stored_body_derives_the_same_identity_as_the_bytes_that_were_sent(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        # A unicode payment id and a top-level timestamp: the identity's own inputs, round-tripped.
        stranger = {"id": str(uuid.uuid4()), "provider_payment_id": "pay_café☕日本"}
        body = self._rich(webhook_body(stranger, org_id, event="payment.captured"))
        await post_webhook(client, provider, body, "evt_uni")

        (row,) = await inbox_rows(admin_session, org_id)
        sent = domain.derive_event_id(provider.parse_webhook(body=body))
        assert sent is not None
        assert services._identity_of(provider, row["payload"]) == sent
        # ...and the redelivery of that unknown-charge event is a plain duplicate too.
        again = await post_webhook(client, provider, body, "evt_uni")
        assert again.json()["duplicate"] is True and again.json()["provider_event_id"] == "evt_uni"

    async def test_a_stored_body_that_is_not_an_event_is_not_evidence_of_a_reuse(
        self, provider: RazorpayProvider
    ) -> None:
        assert services._identity_of(provider, {}) is None
        assert services._identity_of(provider, {"event": "payment.captured"}) is None
