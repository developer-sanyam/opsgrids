"""Permanent conditions in a signed webhook are recorded and answered 200 — never retried (S16).

Razorpay redelivers any non-2xx for days. A signed webhook that can *never* succeed — a `captured`
for a charge already `failed`, a payment for another amount or currency than the charge, a charge
that is not this organization's — used to be answered 4xx and rolled back whole, so the provider
retried it forever and the authentic evidence was kept nowhere (the inbox insert rolled back with
it). Now the inbox row is kept, stating why (`ignored` / `conflict` and a stable reason code), an
audit row `payment.webhook.ignored` is written, the state is left exactly as it was, and the answer
is 200. Only a bad signature, an unidentifiable body and a *transient* failure are non-2xx — and the
transient one must be, so the provider retries it.

State is asserted from the database on a separate BYPASSRLS session, never from the shared test
session's identity map, which would happily show a rolled-back attribute as still set.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app import services
from app.main import app
from app.providers.razorpay import RazorpayProvider
from tests.helpers import razorpay_webhook_body, seed_charge, signed_post

pytestmark = pytest.mark.integration

WEBHOOK = "/api/v1/webhooks/razorpay"


@pytest_asyncio.fixture
async def tolerant(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """A client that answers what the server would — a 500 is a 500, not an exception in the test.
    Built after `client` so the app's dependency overrides (the shared session, the test provider)
    are in place."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --------------------------------------------------------------------------- reading the truth


async def _rows(admin: AsyncSession, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with admin.begin():
        return [dict(r) for r in (await admin.execute(text(sql), params)).mappings().all()]


async def _inbox(admin: AsyncSession, org: uuid.UUID) -> list[dict[str, Any]]:
    return await _rows(
        admin,
        "SELECT provider_event_id, event_name, charge_id, disposition, disposition_reason, id "
        "FROM payment.provider_events WHERE organization_id = :org "
        "ORDER BY received_at, provider_event_id",
        org=org,
    )


async def _audits(admin: AsyncSession, org: uuid.UUID) -> list[dict[str, Any]]:
    return await _rows(
        admin,
        "SELECT action, entity, entity_id, acting_as, after FROM payment.audit_logs "
        "WHERE organization_id = :org ORDER BY at, action",
        org=org,
    )


async def _emitted(admin: AsyncSession, org: uuid.UUID) -> list[str]:
    rows = await _rows(
        admin,
        "SELECT event_type FROM payment.outbox WHERE organization_id = :org ORDER BY created_at",
        org=org,
    )
    return [r["event_type"] for r in rows]


async def _charge(admin: AsyncSession, charge_id: str) -> dict[str, Any]:
    rows = await _rows(
        admin,
        "SELECT state, failure_reason, provider_payment_id, provider_order_id "
        "FROM payment.charges WHERE id = :id",
        id=uuid.UUID(charge_id),
    )
    return rows[0]


async def _force(admin: AsyncSession, charge_id: str, **columns: Any) -> None:
    """Put a charge in a state the API alone would take a sweep to reach."""
    sets = ", ".join(f"{name} = :{name}" for name in columns)
    async with admin.begin():
        await admin.execute(
            text(f"UPDATE payment.charges SET {sets} WHERE id = :id"),  # noqa: S608 - test-owned names
            {**columns, "id": uuid.UUID(charge_id)},
        )


def _ignored(audits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in audits if a["action"] == "payment.webhook.ignored"]


# --------------------------------------------------------------------------- building bodies


def _body(
    charge: dict[str, Any],
    org_id: uuid.UUID,
    *,
    event: str,
    created_at: int = 1_700_000_000,
    **kw: Any,
) -> bytes:
    kw.setdefault("provider_payment_id", charge["provider_payment_id"])
    return razorpay_webhook_body(
        event=event,
        organization_id=org_id,
        charge_id=uuid.UUID(charge["id"]),
        body_id=False,
        created_at=created_at,
        **kw,
    )


def _edited(body: bytes, edit: Any) -> bytes:
    """`body` with `edit(parsed_dict)` applied — used to remove or corrupt one field of an otherwise
    genuine body. The caller signs the result, so it is authentic as far as the service can tell."""
    parsed = json.loads(body)
    edit(parsed)
    return json.dumps(parsed).encode()


async def _post(client: AsyncClient, provider: RazorpayProvider, body: bytes, event_id: str) -> Any:
    return await client.post(WEBHOOK, **signed_post(provider, body, event_id=event_id))


# --------------------------------------------------------------------------- the tests


class TestAnIllegalTransitionIsKeptNotRetried:
    async def test_a_failure_after_a_capture_is_recorded_as_a_conflict_with_no_state_change(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """The trace from the review: charge already terminal, then a signed webhook that says the
        opposite. It was a 422 (`IllegalChargeTransition`), rolled back whole, retried for days."""
        charge = await seed_charge(client, org_id, key="captured-then-failed")
        assert (
            await _post(client, provider, _body(charge, org_id, event="payment.captured"), "evt_1")
        ).json()["reconciled"] is True

        late = await _post(
            client,
            provider,
            _body(charge, org_id, event="payment.failed", error_description="late failure"),
            "evt_2",
        )

        assert late.status_code == 200, (
            "a permanent condition must not be a status the provider retries"
        )
        ack = late.json()
        assert ack["ignored_reason"] == "illegal_transition"
        assert ack["reconciled"] is False and ack["duplicate"] is False

        # State: exactly as the capture left it. No failure event, no second capture event.
        stored = await _charge(admin_session, charge["id"])
        assert stored["state"] == "captured" and stored["failure_reason"] is None
        assert await _emitted(admin_session, org_id) == ["commerce.payment.captured"]

        # Evidence: the authentic event is in the inbox, stating why nothing came of it...
        inbox = await _inbox(admin_session, org_id)
        assert [r["provider_event_id"] for r in inbox] == ["evt_1", "evt_2"]
        kept = inbox[1]
        assert kept["disposition"] == "conflict"
        assert kept["disposition_reason"] == "illegal_transition"
        assert str(kept["charge_id"]) == charge["id"]
        # ...and in the audit trail, naming the states and nothing else.
        audits = _ignored(await _audits(admin_session, org_id))
        assert len(audits) == 1
        assert audits[0]["entity"] == "provider_event" and audits[0]["entity_id"] == kept["id"]
        assert audits[0]["acting_as"] == "razorpay-webhook"
        after = audits[0]["after"]
        assert after["reason"] == "illegal_transition" and after["disposition"] == "conflict"
        assert after["charge_state"] == "captured" and after["asserted_state"] == "failed"
        assert after["provider_event_id"] == "evt_2" and after["event_name"] == "payment.failed"
        assert str(after["charge_id"]) == charge["id"]

    async def test_a_capture_after_a_failure_the_provider_reported_is_kept_and_not_applied(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="failed-then-captured")
        failed = _body(charge, org_id, event="payment.failed", error_description="card_declined")
        assert (await _post(client, provider, failed, "evt_f")).json()["reconciled"] is True

        late = await _post(
            client, provider, _body(charge, org_id, event="payment.captured"), "evt_c"
        )

        assert late.status_code == 200
        assert late.json()["ignored_reason"] == "illegal_transition"
        stored = await _charge(admin_session, charge["id"])
        assert stored["state"] == "failed" and stored["failure_reason"] == "card_declined"
        assert await _emitted(admin_session, org_id) == ["commerce.payment.failed"]
        assert (await _inbox(admin_session, org_id))[1]["disposition"] == "conflict"

    @pytest.mark.parametrize("reason", ["attempts_exhausted", "lookup_unavailable"])
    async def test_a_capture_after_a_local_failure_is_still_legal_and_is_processed(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        reason: str,
    ) -> None:
        """D3's rule is kept: the platform failed this charge on its own judgement, the provider
        says it captured — the money moved, the record follows it. The one legal way back from
        `failed`, and it must not have been swept up in the new conflict handling."""
        charge = await seed_charge(client, org_id, key=f"local-{reason}")
        await _force(admin_session, charge["id"], state="failed", failure_reason=reason)

        response = await _post(
            client, provider, _body(charge, org_id, event="payment.captured"), "evt_late"
        )

        assert response.status_code == 200 and response.json()["reconciled"] is True
        assert response.json()["ignored_reason"] is None
        stored = await _charge(admin_session, charge["id"])
        assert stored["state"] == "captured" and stored["failure_reason"] is None
        assert (await _inbox(admin_session, org_id))[0]["disposition"] == "processed"
        actions = [a["action"] for a in await _audits(admin_session, org_id)]
        assert "payment.charge.captured_after_local_failure" in actions
        assert "payment.webhook.ignored" not in actions

    async def test_the_attempt_is_rolled_back_whole_not_half_applied(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """Reconciliation records the provider's payment and Order ids *before* it decides the
        transition is illegal. Those writes belong to the attempt the SAVEPOINT rolled back: a
        conflicting event must not leave the ids it asserted (or the audit row about them) on the
        charge."""
        charge = await seed_charge(client, org_id, key="no-partial-write")
        await _force(
            admin_session,
            charge["id"],
            state="failed",
            failure_reason="card_declined",
            provider_payment_id=None,
            provider_order_id=None,
        )

        audits_before = Counter(a["action"] for a in await _audits(admin_session, org_id))

        body = _body(
            charge,
            org_id,
            event="payment.captured",
            provider_payment_id="pay_asserted_by_a_conflict",
            order_id="order_asserted_by_a_conflict",
        )
        response = await _post(client, provider, body, "evt_partial")

        assert response.status_code == 200
        assert response.json()["ignored_reason"] == "illegal_transition"
        stored = await _charge(admin_session, charge["id"])
        assert stored["provider_payment_id"] is None and stored["provider_order_id"] is None
        assert stored["state"] == "failed"
        # The only audit row the attempt leaves is the conflict itself: no
        # `payment.charge.created` for an id that was never recorded.
        audits_after = Counter(a["action"] for a in await _audits(admin_session, org_id))
        assert audits_after - audits_before == Counter({"payment.webhook.ignored": 1})


class TestAnUnknownChargeIsKeptNotRetried:
    async def test_a_charge_that_is_not_ours_is_ignored_and_recorded(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        stranger = {"id": str(uuid.uuid4()), "provider_payment_id": "pay_not_ours"}

        response = await _post(
            client, provider, _body(stranger, org_id, event="payment.captured"), "evt_stranger"
        )

        assert response.status_code == 200
        assert response.json()["ignored_reason"] == "unknown_charge"
        assert response.json()["reconciled"] is False
        inbox = await _inbox(admin_session, org_id)
        assert len(inbox) == 1
        assert inbox[0]["disposition"] == "ignored"
        assert inbox[0]["disposition_reason"] == "unknown_charge"
        assert inbox[0]["charge_id"] is None
        assert len(_ignored(await _audits(admin_session, org_id))) == 1
        assert await _emitted(admin_session, org_id) == []

    async def test_another_tenants_charge_is_unknown_and_recorded_under_the_named_tenant(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        """A callback naming organization B for organization A's charge: A's charge is invisible
        under B's binding, so it is an unknown charge — recorded in *B's* inbox, touching nothing
        of A's."""
        charge = await seed_charge(client, org_id, key="cross-tenant")

        response = await _post(
            client, provider, _body(charge, org_b, event="payment.failed"), "evt_cross"
        )

        assert response.status_code == 200
        assert response.json()["ignored_reason"] == "unknown_charge"
        assert (await _charge(admin_session, charge["id"]))["state"] == "authorized"
        assert await _inbox(admin_session, org_id) == []
        (row,) = await _inbox(admin_session, org_b)
        assert row["disposition_reason"] == "unknown_charge"
        assert await _emitted(admin_session, org_id) == []


class TestAPaymentThatIsNotTheChargeIsKeptNotRetried:
    async def test_another_amount_is_a_conflict_and_changes_nothing(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="amount", amount_minor=1000)
        before = await _charge(admin_session, charge["id"])

        response = await _post(
            client,
            provider,
            _body(charge, org_id, event="payment.captured", amount_minor=999),
            "evt_amount",
        )

        assert response.status_code == 200
        assert response.json()["ignored_reason"] == "amount_mismatch"
        assert await _charge(admin_session, charge["id"]) == before
        assert await _emitted(admin_session, org_id) == []
        (row,) = await _inbox(admin_session, org_id)
        assert row["disposition"] == "conflict" and row["disposition_reason"] == "amount_mismatch"
        (audit,) = _ignored(await _audits(admin_session, org_id))
        # Integers and codes only: the amounts an operator needs, none of the body.
        assert audit["after"]["charge_amount_minor"] == 1000
        assert audit["after"]["asserted_amount_minor"] == 999

    async def test_another_currency_is_a_conflict_and_changes_nothing(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="currency", currency="USD")
        before = await _charge(admin_session, charge["id"])

        response = await _post(
            client,
            provider,
            _body(charge, org_id, event="payment.captured", currency="EUR"),
            "evt_currency",
        )

        assert response.status_code == 200
        assert response.json()["ignored_reason"] == "currency_mismatch"
        assert await _charge(admin_session, charge["id"]) == before
        (row,) = await _inbox(admin_session, org_id)
        assert row["disposition"] == "conflict" and row["disposition_reason"] == "currency_mismatch"
        (audit,) = _ignored(await _audits(admin_session, org_id))
        assert audit["after"]["charge_currency"] == "USD"
        assert audit["after"]["asserted_currency"] == "EUR"

    async def test_a_failure_for_another_amount_does_not_fail_the_charge(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """A failure event is held to the same identity check: a `payment.failed` that is not this
        charge's payment must not suspend anyone's subscription."""
        charge = await seed_charge(client, org_id, key="amount-failed", amount_minor=4900)

        response = await _post(
            client,
            provider,
            _body(charge, org_id, event="payment.failed", amount_minor=100),
            "evt_amount_failed",
        )

        assert response.json()["ignored_reason"] == "amount_mismatch"
        assert (await _charge(admin_session, charge["id"]))["state"] == "authorized"
        assert "commerce.payment.failed" not in await _emitted(admin_session, org_id)

    async def test_an_agreeing_payment_reconciles_whatever_the_currency_s_case(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        charge = await seed_charge(client, org_id, key="agree", amount_minor=2500, currency="INR")

        response = await _post(
            client,
            provider,
            _body(charge, org_id, event="payment.captured", amount_minor=2500, currency="inr"),
            "evt_agree",
        )

        assert response.json()["reconciled"] is True and response.json()["ignored_reason"] is None
        assert (await _inbox(admin_session, org_id))[0]["disposition"] == "processed"

    async def test_a_body_that_omits_the_amount_and_currency_is_not_a_mismatch(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        """What the provider does not say cannot contradict the charge; refusing a genuine capture
        because a field was missing would be its own way to lose money."""
        charge = await seed_charge(client, org_id, key="omitted")
        body = _edited(
            _body(charge, org_id, event="payment.captured"),
            lambda b: [b["payload"]["payment"]["entity"].pop(k) for k in ("amount", "currency")],
        )

        response = await _post(client, provider, body, "evt_omitted")

        assert response.json()["reconciled"] is True

    async def test_a_provider_id_another_charge_holds_is_a_conflict_not_a_409(
        self,
        client: AsyncClient,
        tolerant: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
    ) -> None:
        """Contradictory ids: the webhook for charge B asserts the payment id charge A already
        holds. The unique index refuses it — an `IntegrityError` the platform answers 409, which
        the provider retries for days. It is as permanent as any other contradiction."""
        a = await seed_charge(client, org_id, key="holder")
        b = await seed_charge(client, org_id, key="claimant")
        await _force(
            admin_session,
            b["id"],
            state="created",
            provider_payment_id=None,
            provider_order_id=None,
        )
        a_before = await _charge(admin_session, a["id"])
        b_before = await _charge(admin_session, b["id"])

        body = _body(
            b, org_id, event="payment.captured", provider_payment_id=a["provider_payment_id"]
        )
        response = await _post(tolerant, provider, body, "evt_collision")

        assert response.status_code == 200, response.text
        assert response.json()["ignored_reason"] == "provider_id_conflict"
        assert await _charge(admin_session, a["id"]) == a_before
        assert await _charge(admin_session, b["id"]) == b_before
        assert await _emitted(admin_session, org_id) == []
        (row,) = await _inbox(admin_session, org_id)
        assert row["disposition"] == "conflict"
        assert row["disposition_reason"] == "provider_id_conflict"
        assert len(_ignored(await _audits(admin_session, org_id))) == 1


class TestAnUnattributableWebhook:
    @pytest.mark.parametrize(
        "corrupt",
        [
            lambda entity: entity["notes"].pop("organization_id"),
            lambda entity: entity.update(notes=[]),
            lambda entity: entity["notes"].update(organization_id="not-a-uuid"),
            lambda entity: entity["notes"].update(organization_id=12345),
        ],
        ids=["no-org", "notes-is-a-list", "org-is-not-a-uuid", "org-is-not-a-string"],
    )
    async def test_it_is_answered_200_and_writes_nothing(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        corrupt: Any,
    ) -> None:
        """A payment made on the same Razorpay account outside this platform carries none of our
        notes. There is no tenant to record it under, and the provider redelivering it can never
        make it attributable: a 422 (or, for a malformed org id, a 500) only starts a retry storm
        that risks the endpoint for everyone."""
        charge = await seed_charge(client, org_id, key="unattributable")
        body = _edited(
            _body(charge, org_id, event="payment.captured"),
            lambda b: corrupt(b["payload"]["payment"]["entity"]),
        )

        with capture_logs() as logs:
            response = await _post(client, provider, body, "evt_outside")

        assert response.status_code == 200, response.text
        assert response.json()["ignored_reason"] == "unattributable"
        assert response.json()["provider_event_id"] == "evt_outside"
        assert (await _charge(admin_session, charge["id"]))["state"] == "authorized"
        assert await _inbox(admin_session, org_id) == []
        assert _ignored(await _audits(admin_session, org_id)) == []
        # Logged for an operator with the event's id and name and nothing else.
        (entry,) = [e for e in logs if e["event"] == "payment.webhook.unattributable"]
        assert set(entry) <= {"event", "log_level", "provider_event_id", "event_name"}
        assert entry["provider_event_id"] == "evt_outside"
        assert entry["event_name"] == "payment.captured"


class TestARedeliveryOfAnIgnoredEvent:
    @pytest.mark.parametrize("scenario", ["unknown_charge", "conflict"])
    async def test_it_is_deduplicated_like_any_other(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        scenario: str,
    ) -> None:
        """The provider redelivers freely; an ignored event must not be re-audited, re-logged or
        re-recorded each time. The first delivery kept its row, so the second is a duplicate."""
        if scenario == "unknown_charge":
            subject = {"id": str(uuid.uuid4()), "provider_payment_id": "pay_nobody"}
            body = _body(subject, org_id, event="payment.captured")
        else:
            subject = await seed_charge(client, org_id, key="redeliver-conflict")
            body = _body(subject, org_id, event="payment.captured", amount_minor=1)

        first = await _post(client, provider, body, "evt_again")
        second = await _post(client, provider, body, "evt_again")
        third = await _post(client, provider, body, "evt_again")

        assert first.json()["ignored_reason"] is not None and first.json()["duplicate"] is False
        for redelivery in (second, third):
            assert redelivery.status_code == 200
            assert redelivery.json()["duplicate"] is True
            assert redelivery.json()["ignored_reason"] is None
        assert len(await _inbox(admin_session, org_id)) == 1
        assert len(_ignored(await _audits(admin_session, org_id))) == 1


class TestATransientFailureIsRetryable:
    @pytest.mark.parametrize("failing_step", ["_resolve_charge", "_reconcile_charge"])
    async def test_a_database_outage_is_a_503_and_leaves_nothing_behind(
        self,
        client: AsyncClient,
        tolerant: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        failing_step: str,
    ) -> None:
        """Only a bad signature and a transient failure may be non-2xx — and the transient one
        *must* be, so the provider retries it. The transaction rolls back whole (the inbox row
        written inside the SAVEPOINT included), so the event id is not burned and the redelivery
        lands as if it were the first."""
        charge = await seed_charge(client, org_id, key=f"outage-{failing_step}")
        body = _body(charge, org_id, event="payment.captured")

        async def unreachable(*_args: Any, **_kwargs: Any) -> None:
            raise OperationalError(
                "SELECT 1", {}, ConnectionResetError("server closed the connection unexpectedly")
            )

        with monkeypatch.context() as patch:
            patch.setattr(services, failing_step, unreachable)
            down = await _post(tolerant, provider, body, "evt_outage")

        assert down.status_code == 503, down.text
        assert down.json()["type"].endswith("/upstream-unavailable")
        assert "server closed" not in down.text and "SELECT" not in down.text
        assert await _inbox(admin_session, org_id) == []
        assert _ignored(await _audits(admin_session, org_id)) == []
        assert (await _charge(admin_session, charge["id"]))["state"] == "authorized"

        # The provider retries — the database is back — and it is processed as a first delivery.
        back = await _post(tolerant, provider, body, "evt_outage")
        assert back.status_code == 200
        assert back.json()["duplicate"] is False and back.json()["reconciled"] is True
        assert (await _charge(admin_session, charge["id"]))["state"] == "captured"

    async def test_only_a_verdict_is_swallowed_a_bug_is_still_a_500(
        self,
        client: AsyncClient,
        tolerant: AsyncClient,
        provider: RazorpayProvider,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The permanent-condition handling is one typed exception, not a blanket `except`: an
        unexpected error must not be recorded as `ignored` and answered 200, which would turn a
        defect into silently lost payments."""
        charge = await seed_charge(client, org_id, key="a-bug")

        async def broken(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("a defect")

        monkeypatch.setattr(services, "_reconcile_charge", broken)
        response = await _post(
            tolerant, provider, _body(charge, org_id, event="payment.captured"), "evt_bug"
        )

        assert response.status_code == 500
        assert await _inbox(admin_session, org_id) == []
        assert _ignored(await _audits(admin_session, org_id)) == []


class TestOpsCanSeeIgnoredWebhooks:
    async def test_a_conflict_is_logged_at_error_and_an_ignored_event_at_warning(
        self,
        client: AsyncClient,
        provider: RazorpayProvider,
        org_id: uuid.UUID,
    ) -> None:
        """The counter an operator alerts on: one structured line per ignored webhook with a stable
        event name and reason code — `error` for a conflict (money may have moved), `warning` for
        a charge that is simply not ours — carrying identifiers and never the body."""
        charge = await seed_charge(client, org_id, key="ops")
        stranger = {"id": str(uuid.uuid4()), "provider_payment_id": "pay_elsewhere"}

        with capture_logs() as logs:
            await _post(
                client,
                provider,
                _body(charge, org_id, event="payment.captured", amount_minor=1),
                "evt_conflict",
            )
            await _post(
                client, provider, _body(stranger, org_id, event="payment.captured"), "evt_unknown"
            )

        entries = {
            e["provider_event_id"]: e for e in logs if e["event"] == "payment.webhook.ignored"
        }
        conflict, unknown = entries["evt_conflict"], entries["evt_unknown"]
        assert conflict["log_level"] == "error" and conflict["reason"] == "amount_mismatch"
        assert conflict["disposition"] == "conflict"
        assert unknown["log_level"] == "warning" and unknown["reason"] == "unknown_charge"
        assert unknown["disposition"] == "ignored"
        for entry in entries.values():
            assert entry["organization_id"] == str(org_id)
            assert entry["event_name"] == "payment.captured"
            leaked = {"payload", "raw", "body", "notes", "signature", "failure_reason"}
            assert not leaked & set(entry), "a log line for an ignored webhook must carry no body"
