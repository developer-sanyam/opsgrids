"""Template status and quality webhooks (slice W2), driven over the real receive route.

The properties that matter: a signed body for a WABA this organization did not connect never
touches it; a redelivery changes nothing; a batch carrying several WABAs folds each into its own
organization only; one bad item is parked instead of failing the batch.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import w2_webhooks
from app.models import InboundDeadLetter, ProcessedEvent, ProviderAccount, ProviderTemplate
from platform_core.config import get_settings

pytestmark = pytest.mark.integration

SECRET = "ci-only-app-secret"
URL = "/api/v1/webhooks/whatsapp"


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "ci-only-verify-token")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _headers(body: bytes) -> dict[str, str]:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={digest}"}


def _post(client, payload: dict[str, Any]):
    body = json.dumps(payload).encode()
    return client.post(URL, content=body, headers=_headers(body))


def _entry(waba_id: str, field: str, value: dict[str, Any], time: int = 1_750_263_773) -> dict:
    return {"id": waba_id, "time": time, "changes": [{"field": field, "value": value}]}


def _payload(*entries: dict) -> dict:
    return {"object": "whatsapp_business_account", "entry": list(entries)}


def _status(
    *, meta_id: str = "555", event: str = "APPROVED", reason: str = "NONE", name: str = "order_a"
) -> dict:
    return {
        "event": event,
        "message_template_id": int(meta_id),
        "message_template_name": name,
        "message_template_language": "en_US",
        "reason": reason,
    }


async def _seed(
    admin_session: AsyncSession,
    org_id: uuid.UUID,
    *,
    waba_id: str,
    phone_number_id: str,
    meta_id: str = "555",
    name: str = "order_a",
    status: str = "pending",
) -> None:
    async with admin_session.begin():
        admin_session.add(
            ProviderAccount(
                organization_id=org_id,
                channel="whatsapp",
                waba_id=waba_id,
                phone_number_id=phone_number_id,
                display_number="+15550783881",
                status="connected",
            )
        )
        admin_session.add(
            ProviderTemplate(
                organization_id=org_id,
                code=name,
                channel="whatsapp",
                provider_name=name,
                provider_template_id=meta_id,
                language="en_US",
                category="utility",
                status=status,
                variables=[],
                preview_text="Hi {{1}}",
                history=[{"status": status, "at": "2026-09-26T05:00:00+00:00", "source": "submit"}],
            )
        )


async def _template(admin_session: AsyncSession, org_id: uuid.UUID, name: str = "order_a"):
    """A snapshot of the row as it is in the database *now*: `populate_existing` so a row the
    session loaded earlier is refreshed, and a copy so a later read cannot mutate what an earlier
    one returned (which would make any before/after comparison pass vacuously)."""
    async with admin_session.begin():
        row = (
            await admin_session.execute(
                select(ProviderTemplate)
                .where(ProviderTemplate.organization_id == org_id, ProviderTemplate.code == name)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        return SimpleNamespace(
            status=row.status,
            status_reason=row.status_reason,
            quality=row.quality,
            history=copy.deepcopy(row.history),
        )


async def _processed_count(admin_session: AsyncSession) -> int:
    async with admin_session.begin():
        return (
            await admin_session.execute(select(func.count()).select_from(ProcessedEvent))
        ).scalar_one()


async def test_a_status_update_moves_the_template_and_records_the_change(
    client, admin_session, org_id
) -> None:
    await _seed(admin_session, org_id, waba_id="waba-a", phone_number_id="1001")
    response = await _post(
        client, _payload(_entry("waba-a", "message_template_status_update", _status()))
    )
    assert response.status_code == 200
    row = await _template(admin_session, org_id)
    assert row.status == "approved"
    assert [(h["status"], h["source"]) for h in row.history] == [
        ("pending", "submit"),
        ("approved", "meta_webhook"),
    ]


async def test_a_rejection_carries_its_reason_and_an_approval_clears_it(
    client, admin_session, org_id
) -> None:
    await _seed(admin_session, org_id, waba_id="waba-a", phone_number_id="1001")
    await _post(
        client,
        _payload(
            _entry(
                "waba-a",
                "message_template_status_update",
                _status(event="REJECTED", reason="INCORRECT_CATEGORY"),
                time=1_750_263_800,
            )
        ),
    )
    rejected = await _template(admin_session, org_id)
    assert rejected.status == "rejected" and rejected.status_reason == "INCORRECT_CATEGORY"

    await _post(
        client,
        _payload(
            _entry(
                "waba-a",
                "message_template_status_update",
                _status(event="REINSTATED"),
                1_750_263_900,
            )
        ),
    )
    approved = await _template(admin_session, org_id)
    assert approved.status == "approved" and approved.status_reason is None


async def test_redelivering_the_same_body_changes_nothing(client, admin_session, org_id) -> None:
    await _seed(admin_session, org_id, waba_id="waba-a", phone_number_id="1001")
    payload = _payload(_entry("waba-a", "message_template_status_update", _status()))
    first = await _post(client, payload)
    after_first = await _template(admin_session, org_id)
    processed = await _processed_count(admin_session)
    second = await _post(client, payload)
    third = await _post(client, payload)
    after_third = await _template(admin_session, org_id)

    assert first.status_code == second.status_code == third.status_code == 200
    assert after_third.history == after_first.history
    assert len(after_third.history) == 2
    assert await _processed_count(admin_session) == processed == 1


async def test_a_signed_payload_for_another_waba_never_touches_this_org(
    client, admin_session, org_id
) -> None:
    other_org = uuid.uuid4()
    await _seed(admin_session, org_id, waba_id="waba-a", phone_number_id="1001")
    await _seed(
        admin_session, other_org, waba_id="waba-b", phone_number_id="2002", status="pending"
    )
    # Same Meta template id and name in both organizations: only the WABA says whose it is.
    response = await _post(
        client,
        _payload(
            _entry(
                "waba-b", "message_template_status_update", _status(event="REJECTED", reason="SPAM")
            )
        ),
    )
    assert response.status_code == 200

    mine = await _template(admin_session, org_id)
    theirs = await _template(admin_session, other_org)
    assert mine.status == "pending" and mine.status_reason is None
    assert [h["source"] for h in mine.history] == ["submit"]
    assert theirs.status == "rejected" and theirs.status_reason == "SPAM"


async def test_an_unknown_waba_touches_nothing_and_is_acknowledged(
    client, admin_session, org_id
) -> None:
    await _seed(admin_session, org_id, waba_id="waba-a", phone_number_id="1001")
    response = await _post(
        client, _payload(_entry("waba-unknown", "message_template_status_update", _status()))
    )
    assert response.status_code == 200
    assert (await _template(admin_session, org_id)).status == "pending"
    assert await _processed_count(admin_session) == 0


async def test_one_body_batching_two_wabas_folds_each_into_its_own_org(
    client, admin_session, org_id
) -> None:
    other_org = uuid.uuid4()
    await _seed(admin_session, org_id, waba_id="waba-a", phone_number_id="1001", meta_id="555")
    await _seed(admin_session, other_org, waba_id="waba-b", phone_number_id="2002", meta_id="666")
    response = await _post(
        client,
        _payload(
            _entry(
                "waba-a", "message_template_status_update", _status(meta_id="555", event="APPROVED")
            ),
            _entry(
                "waba-b", "message_template_status_update", _status(meta_id="666", event="PAUSED")
            ),
        ),
    )
    assert response.status_code == 200
    assert (await _template(admin_session, org_id)).status == "approved"
    assert (await _template(admin_session, other_org)).status == "paused"


async def test_an_id_from_another_org_in_the_same_waba_body_cannot_reach_it(
    client, admin_session, org_id
) -> None:
    """The change names waba-a, so it reaches only org A — and within org A only the template
    Meta's id or (name, language) names. A template id that exists only in org B is not found."""
    other_org = uuid.uuid4()
    await _seed(admin_session, org_id, waba_id="waba-a", phone_number_id="1001", meta_id="555")
    await _seed(
        admin_session,
        other_org,
        waba_id="waba-b",
        phone_number_id="2002",
        meta_id="666",
        name="b_only",
    )
    await _post(
        client,
        _payload(
            _entry(
                "waba-a",
                "message_template_status_update",
                _status(meta_id="666", event="DISABLED", name="b_only"),
            )
        ),
    )
    assert (await _template(admin_session, other_org, "b_only")).status == "pending"
    assert (await _template(admin_session, org_id)).status == "pending"


async def test_a_quality_update_sets_the_rating(client, admin_session, org_id) -> None:
    await _seed(admin_session, org_id, waba_id="waba-a", phone_number_id="1001", status="approved")
    response = await _post(
        client,
        _payload(
            _entry(
                "waba-a",
                "message_template_quality_update",
                {
                    "previous_quality_score": "GREEN",
                    "new_quality_score": "RED",
                    "message_template_id": 555,
                    "message_template_name": "order_a",
                    "message_template_language": "en_US",
                },
            )
        ),
    )
    assert response.status_code == 200
    row = await _template(admin_session, org_id)
    assert row.quality == "low" and row.status == "approved"


async def test_a_body_for_a_template_we_do_not_hold_is_acknowledged(
    client, admin_session, org_id
) -> None:
    await _seed(admin_session, org_id, waba_id="waba-a", phone_number_id="1001")
    response = await _post(
        client,
        _payload(
            _entry(
                "waba-a",
                "message_template_status_update",
                _status(meta_id="424242", name="made_in_meta_console"),
            )
        ),
    )
    assert response.status_code == 200
    assert (await _template(admin_session, org_id)).status == "pending"


async def test_a_stale_status_older_than_the_last_check_is_ignored(
    client, admin_session, org_id
) -> None:
    await _seed(admin_session, org_id, waba_id="waba-a", phone_number_id="1001", status="approved")
    async with admin_session.begin():
        row = (
            await admin_session.execute(
                select(ProviderTemplate).where(ProviderTemplate.organization_id == org_id)
            )
        ).scalar_one()
        from datetime import UTC, datetime

        row.status_checked_at = datetime.now(UTC)
    old = 1_600_000_000
    await _post(
        client,
        _payload(
            _entry("waba-a", "message_template_status_update", _status(event="REJECTED"), old)
        ),
    )
    assert (await _template(admin_session, org_id)).status == "approved"


async def test_an_unsigned_body_is_refused_and_changes_nothing(
    client, admin_session, org_id
) -> None:
    await _seed(admin_session, org_id, waba_id="waba-a", phone_number_id="1001")
    body = json.dumps(
        _payload(_entry("waba-a", "message_template_status_update", _status()))
    ).encode()
    response = await client.post(URL, content=body, headers={"Content-Type": "application/json"})
    wrong = await client.post(
        URL, content=body, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64}
    )
    assert response.status_code == 401 and wrong.status_code == 401
    assert (await _template(admin_session, org_id)).status == "pending"


async def test_number_quality_reaches_the_registered_numbers_cache(
    client, admin_session, org_id
) -> None:
    await _seed(admin_session, org_id, waba_id="waba-a", phone_number_id="1001")
    seen: list[dict[str, Any]] = []

    async def handler(session, **kwargs: Any) -> None:
        seen.append(kwargs)

    w2_webhooks.register_number_quality_handler(handler)
    try:
        response = await _post(
            client,
            _payload(
                _entry(
                    "waba-a",
                    "phone_number_quality_update",
                    {
                        "display_phone_number": "15550783881",
                        "event": "DOWNGRADE",
                        "current_limit": "TIER_1K",
                    },
                )
            ),
        )
    finally:
        w2_webhooks.register_number_quality_handler(None)
    assert response.status_code == 200
    assert seen == [
        {
            "organization_id": org_id,
            "waba_id": "waba-a",
            "display_phone_number": "15550783881",
            "event": "DOWNGRADE",
            "current_limit": "TIER_1K",
        }
    ]


async def test_number_quality_and_account_updates_are_acknowledged_without_a_handler(
    client, admin_session, org_id
) -> None:
    await _seed(admin_session, org_id, waba_id="waba-a", phone_number_id="1001")
    response = await _post(
        client,
        _payload(
            _entry("waba-a", "phone_number_quality_update", {"event": "UPGRADE"}),
            _entry("waba-a", "account_update", {"event": "VERIFIED_ACCOUNT"}),
        ),
    )
    assert response.status_code == 200


async def test_one_unfoldable_item_is_parked_and_the_rest_of_the_batch_still_folds(
    client, admin_session, org_id
) -> None:
    other_org = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            ProviderAccount(
                organization_id=org_id,
                channel="whatsapp",
                waba_id="waba-a",
                phone_number_id="1001",
                display_number="+1",
                status="connected",
            )
        )
        # No Meta id yet, so the webhook will try to store one — and this one cannot fit.
        admin_session.add(
            ProviderTemplate(
                organization_id=org_id,
                code="order_a",
                channel="whatsapp",
                provider_name="order_a",
                language="en_US",
                category="utility",
                status="pending",
                variables=[],
                preview_text="x",
            )
        )
    await _seed(admin_session, other_org, waba_id="waba-b", phone_number_id="2002", meta_id="666")
    response = await _post(
        client,
        _payload(
            _entry(
                "waba-a",
                "message_template_status_update",
                _status(meta_id="9" * 300, event="APPROVED"),
            ),
            _entry(
                "waba-b", "message_template_status_update", _status(meta_id="666", event="APPROVED")
            ),
        ),
    )
    assert response.status_code == 200
    assert (await _template(admin_session, other_org)).status == "approved"
    assert (await _template(admin_session, org_id)).status == "pending"
    async with admin_session.begin():
        parked = (
            (
                await admin_session.execute(
                    select(InboundDeadLetter).where(InboundDeadLetter.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
    assert [p.kind for p in parked] == ["template"]
    assert "9" * 50 not in parked[0].error


def test_extract_changes_ignores_what_it_does_not_understand() -> None:
    assert w2_webhooks.extract_changes({}) == []
    assert w2_webhooks.extract_changes({"entry": "nope"}) == []
    assert w2_webhooks.extract_changes({"entry": [{"changes": [{"field": "messages"}]}]}) == []
    assert (
        w2_webhooks.extract_changes(
            {"entry": [{"id": "w", "changes": [{"field": "message_template_status_update"}]}]}
        )
        == []
    )
