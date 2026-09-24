"""Webhook identity, end to end (S14) — one org, many events, and a body with no `id`.

A real Razorpay body is `{entity, account_id, event, contains, payload, created_at}`: **no
top-level `id`**. The per-delivery event id is the `X-Razorpay-Event-Id` header. The adapter used
to read the id from the body and default it to `''`, so `(organization_id, '')` was the dedupe key
of every event an organization ever received: the first landed, and every later one — the capture,
the failure — was dropped as a "duplicate", answered 200 (so the provider stopped retrying) and
never reconciled. The suite's own bodies synthesised an `id`, which is why nothing noticed.

Every test here posts the **real shape** (`body_id=False`), because a test of event identity
against a body Razorpay never sends is the test that let this through.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Charge, OutboxEvent, ProviderEvent
from app.providers.razorpay import RazorpayProvider
from tests.helpers import razorpay_webhook_body, seed_charge, signed_post

pytestmark = pytest.mark.integration

WEBHOOK = "/api/v1/webhooks/razorpay"


def _real_body(
    charge: dict[str, Any],
    org_id: uuid.UUID,
    *,
    event: str,
    created_at: int | None = 1_700_000_000,
    **extra: Any,
) -> bytes:
    """A Razorpay-shaped body for `charge`: no top-level `id`, a `created_at`, real `notes`."""
    return razorpay_webhook_body(
        event=event,
        provider_payment_id=charge["provider_payment_id"],
        organization_id=org_id,
        charge_id=uuid.UUID(charge["id"]),
        body_id=False,
        created_at=created_at,
        **extra,
    )


async def _inbox(session: AsyncSession) -> list[ProviderEvent]:
    async with session.begin():
        rows = (
            (await session.execute(select(ProviderEvent).order_by(ProviderEvent.received_at)))
            .scalars()
            .all()
        )
        return list(rows)


async def _count(session: AsyncSession, model: Any, *where: Any) -> int:
    async with session.begin():
        return (
            await session.execute(select(func.count()).select_from(model).where(*where))
        ).scalar_one()


class TestTwoEventsForOneOrganization:
    async def test_two_different_events_are_both_processed(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        """The defect, reproduced: `payment.authorized` and then `payment.captured` for one
        charge, each with its own `X-Razorpay-Event-Id` and neither with a body `id`. Keyed on the
        empty string the second collapsed onto the first and was dropped as a duplicate."""
        charge = await seed_charge(client, org_id, key="two-events")

        first = await client.post(
            WEBHOOK,
            **signed_post(
                provider,
                _real_body(charge, org_id, event="payment.authorized", created_at=1_700_000_001),
                event_id="evt_authorized_1",
            ),
        )
        second = await client.post(
            WEBHOOK,
            **signed_post(
                provider,
                _real_body(charge, org_id, event="payment.captured", created_at=1_700_000_002),
                event_id="evt_captured_2",
            ),
        )

        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["provider_event_id"] == "evt_authorized_1"
        assert second.json()["provider_event_id"] == "evt_captured_2"
        assert second.json()["duplicate"] is False, "the second event was dropped as a duplicate"
        assert second.json()["reconciled"] is True

        inbox = await _inbox(session)
        assert [row.provider_event_id for row in inbox] == ["evt_authorized_1", "evt_captured_2"]
        async with session.begin():
            stored = await session.get(Charge, uuid.UUID(charge["id"]))
        assert stored is not None and stored.state == "captured"
        assert (
            await _count(
                session, OutboxEvent, OutboxEvent.event_type == "commerce.payment.captured"
            )
            == 1
        )

    async def test_the_capture_and_the_failure_of_two_charges_are_both_processed(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        """Two charges, two outcomes, one organization: the second charge's failure is the event
        that suspends a subscription, and it must not be swallowed by the first's capture."""
        paid = await seed_charge(client, org_id, key="paid")
        declined = await seed_charge(client, org_id, key="declined")

        captured = await client.post(
            WEBHOOK,
            **signed_post(
                provider, _real_body(paid, org_id, event="payment.captured"), event_id="evt_p"
            ),
        )
        failed = await client.post(
            WEBHOOK,
            **signed_post(
                provider,
                _real_body(declined, org_id, event="payment.failed", error_description="declined"),
                event_id="evt_d",
            ),
        )

        assert captured.json()["reconciled"] is True
        assert failed.json()["reconciled"] is True and failed.json()["duplicate"] is False
        assert (
            await _count(session, OutboxEvent, OutboxEvent.event_type == "commerce.payment.failed")
            == 1
        )


class TestARedelivery:
    async def test_a_true_redelivery_with_the_same_header_is_deduplicated(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="redelivery")
        delivery = signed_post(
            provider, _real_body(charge, org_id, event="payment.captured"), event_id="evt_same"
        )

        first = await client.post(WEBHOOK, **delivery)
        second = await client.post(WEBHOOK, **delivery)

        assert first.json()["duplicate"] is False and first.json()["reconciled"] is True
        assert second.json()["duplicate"] is True and second.json()["reconciled"] is False
        assert len(await _inbox(session)) == 1
        assert (
            await _count(
                session, OutboxEvent, OutboxEvent.event_type == "commerce.payment.captured"
            )
            == 1
        )

    async def test_the_delivery_id_not_the_body_tells_two_deliveries_apart(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        """Same body under two different `X-Razorpay-Event-Id`s are two deliveries: both are
        recorded, and the second — an outcome the charge already has — is a no-op, not a second
        event."""
        charge = await seed_charge(client, org_id, key="two-ids")
        body = _real_body(charge, org_id, event="payment.captured")

        first = await client.post(WEBHOOK, **signed_post(provider, body, event_id="evt_a"))
        second = await client.post(WEBHOOK, **signed_post(provider, body, event_id="evt_b"))

        assert first.json()["reconciled"] is True
        assert second.json()["duplicate"] is False and second.json()["reconciled"] is False
        assert len(await _inbox(session)) == 2
        assert (
            await _count(
                session, OutboxEvent, OutboxEvent.event_type == "commerce.payment.captured"
            )
            == 1
        )


class TestNoHeader:
    async def test_a_missing_header_falls_back_to_a_stable_digest(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="no-header")
        body = _real_body(charge, org_id, event="payment.captured")

        first = await client.post(WEBHOOK, **signed_post(provider, body))
        # Rebuilt from scratch: the id depends on what the body says, not on which bytes object.
        rebuilt = _real_body(charge, org_id, event="payment.captured")
        second = await client.post(WEBHOOK, **signed_post(provider, rebuilt))

        derived = first.json()["provider_event_id"]
        assert derived.startswith("derived:") and len(derived) > len("derived:")
        assert second.json()["provider_event_id"] == derived, "the digest must be deterministic"
        assert first.json()["duplicate"] is False and first.json()["reconciled"] is True
        assert second.json()["duplicate"] is True
        assert [row.provider_event_id for row in await _inbox(session)] == [derived]

    async def test_distinct_events_get_distinct_digests_so_neither_is_dropped(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        """The S14 property without a header at all: the authorization and the capture of one
        payment differ in event name and timestamp, so they are two events."""
        charge = await seed_charge(client, org_id, key="no-header-two")

        a = await client.post(
            WEBHOOK,
            **signed_post(
                provider,
                _real_body(charge, org_id, event="payment.authorized", created_at=100),
            ),
        )
        b = await client.post(
            WEBHOOK,
            **signed_post(
                provider, _real_body(charge, org_id, event="payment.captured", created_at=105)
            ),
        )

        assert a.json()["provider_event_id"] != b.json()["provider_event_id"]
        assert b.json()["duplicate"] is False and b.json()["reconciled"] is True
        assert len(await _inbox(session)) == 2

    async def test_a_body_id_is_still_honoured_when_there_is_no_header(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="body-id")
        body = razorpay_webhook_body(
            event="payment.captured",
            provider_payment_id=charge["provider_payment_id"],
            organization_id=org_id,
            charge_id=uuid.UUID(charge["id"]),
            event_id="evt_in_the_body",
        )
        response = await client.post(WEBHOOK, **signed_post(provider, body))
        assert response.json()["provider_event_id"] == "evt_in_the_body"

    async def test_a_webhook_with_no_id_and_nothing_to_derive_one_from_is_refused(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        """No header, no body `id`, no `created_at`: nothing tells this event from any other, and
        keying it on `''` is the defect. Refused 400 with no state change — no inbox row, no
        audit, no event, the charge exactly as it was."""
        charge = await seed_charge(client, org_id, key="unidentifiable")
        body = _real_body(charge, org_id, event="payment.captured", created_at=None)

        response = await client.post(WEBHOOK, **signed_post(provider, body))

        assert response.status_code == 400
        assert response.json()["type"].endswith("/invalid-webhook")
        assert await _inbox(session) == []
        assert await _count(session, AuditLog, AuditLog.action.like("payment.webhook.%")) == 0
        assert (
            await _count(
                session, OutboxEvent, OutboxEvent.event_type == "commerce.payment.captured"
            )
            == 0
        )
        async with session.begin():
            stored = await session.get(Charge, uuid.UUID(charge["id"]))
        assert stored is not None and stored.state == "authorized"

    async def test_the_refusal_leaves_the_door_open_for_the_same_event_with_an_id(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="refused-then-ok")
        body = _real_body(charge, org_id, event="payment.captured", created_at=None)
        assert (await client.post(WEBHOOK, **signed_post(provider, body))).status_code == 400

        retry = await client.post(WEBHOOK, **signed_post(provider, body, event_id="evt_now"))

        assert retry.status_code == 200 and retry.json()["reconciled"] is True


class TestAnUnusableHeader:
    @pytest.mark.parametrize(
        "header",
        ["x" * 200, "derived:" + "0" * 64],
        ids=["oversized", "claims-the-derived-namespace"],
    )
    async def test_it_is_not_the_key_and_is_not_an_error(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
        header: str,
    ) -> None:
        """A header the column cannot hold used to be a `StringDataRightTruncation` — a 500 the
        provider retries for days. It is now treated as absent: the body's own fields identify the
        event, and the header never becomes (or collides with) a key."""
        charge = await seed_charge(client, org_id, key="odd-header")
        body = _real_body(charge, org_id, event="payment.captured")

        response = await client.post(WEBHOOK, **signed_post(provider, body, event_id=header))

        assert response.status_code == 200, response.text
        used = response.json()["provider_event_id"]
        assert used != header
        assert used.startswith("derived:") and len(used) <= 120
        assert response.json()["reconciled"] is True


class TestTheSignatureComesFirst:
    async def test_a_tampered_body_is_refused_whatever_the_header_says(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="tampered")
        body = _real_body(charge, org_id, event="payment.captured")
        delivery = signed_post(provider, body, event_id="evt_t")
        delivery["content"] = body + b" "  # one byte after the HMAC was taken

        response = await client.post(WEBHOOK, **delivery)

        assert response.status_code == 400
        assert response.json()["type"].endswith("/invalid-webhook-signature")
        assert await _inbox(session) == []

    async def test_the_signature_is_judged_before_the_event_id_is(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        """A wrong signature on a body that also has no usable id is a *signature* refusal: the
        order of checks is not observable as a way to probe what the body contained."""
        body = _real_body(
            {"provider_payment_id": "pay_x", "id": str(uuid.uuid4())},
            org_id,
            event="payment.captured",
            created_at=None,
        )
        response = await client.post(WEBHOOK, **signed_post(provider, body, signature="0" * 64))
        assert response.status_code == 400
        assert response.json()["type"].endswith("/invalid-webhook-signature")

    @pytest.mark.parametrize("body", [b"this is not json", b"[1, 2, 3]", b'"a string"', b"null"])
    async def test_a_signed_body_that_is_not_an_object_is_refused_not_a_crash(
        self,
        client: AsyncClient,
        session: AsyncSession,
        provider: RazorpayProvider,
        body: bytes,
    ) -> None:
        response = await client.post(WEBHOOK, **signed_post(provider, body, event_id="evt_junk"))
        assert response.status_code == 400
        assert response.json()["type"].endswith("/invalid-webhook")
        assert await _inbox(session) == []


class TestTheDatabaseHoldsTheLine:
    """Whatever the service does, the inbox refuses an empty key and a repeated one."""

    _INSERT = (
        "INSERT INTO payment.provider_events "
        "(id, organization_id, provider, provider_event_id, event_name, payload) "
        "VALUES (gen_random_uuid(), :org, 'razorpay', :eid, 'payment.captured', '{}'::jsonb)"
    )

    async def test_an_empty_event_id_is_refused_by_the_database(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        with pytest.raises(IntegrityError, match="provider_event_id_not_empty"):
            async with session.begin():
                await session.execute(text(self._INSERT), {"org": str(org_id), "eid": ""})

    async def test_the_key_is_unique_per_organization_and_only_per_organization(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
        admin_session: AsyncSession,
    ) -> None:
        async with session.begin():
            await session.execute(text(self._INSERT), {"org": str(org_id), "eid": "evt_once"})
        with pytest.raises(IntegrityError, match="uq_provider_events_org_event_id"):
            async with session.begin():
                await session.execute(text(self._INSERT), {"org": str(org_id), "eid": "evt_once"})

        # The same delivery id under another organization is a different key — the unique index is
        # per tenant, so one tenant's event can never suppress another's. (Inserted on the admin
        # role: the app session is bound to `org_id`, and RLS would refuse a row for `org_b`.)
        async with admin_session.begin():
            await admin_session.execute(text(self._INSERT), {"org": str(org_b), "eid": "evt_once"})
