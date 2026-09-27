"""Two organizations naming one WABA: a customer's opt-out and WABA-level events reach only the
organization that actually owns the number / holds a connection.

`account_update`, template status and quality events name only the WABA; a `user_preferences`
change additionally names the number the customer messaged (`metadata.phone_number_id`), and that
number alone decides whose customer they are.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import func, select

from app.models import ConsentEvent, ProviderAccount, ProviderTemplate
from platform_core.config import get_settings

pytestmark = pytest.mark.integration

SECRET = "ci-only-app-secret"
URL = "/api/v1/webhooks/whatsapp"
WABA = "waba-shared"
PHONE_A = "1001"
PHONE_B = "2002"
WA_ID = "16505551234"


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "ci-only-verify-token")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _post(client, payload: dict[str, Any]):
    body = json.dumps(payload).encode()
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        URL,
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={digest}"},
    )


def _payload(field: str, value: dict[str, Any], waba: str = WABA) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {"id": waba, "time": 1_750_263_773, "changes": [{"field": field, "value": value}]}
        ],
    }


def _stop(phone_number_id: str | None) -> dict:
    value: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "contacts": [{"wa_id": WA_ID}],
        "user_preferences": [
            {
                "wa_id": WA_ID,
                "category": "marketing_messages",
                "value": "stop",
                "timestamp": 1_750_263_773,
            }
        ],
    }
    if phone_number_id is not None:
        value["metadata"] = {"display_phone_number": "1555", "phone_number_id": phone_number_id}
    return value


async def _account(
    admin_session,
    org: uuid.UUID,
    *,
    phone: str | None,
    status: str = "connected",
    channel: str = "whatsapp",
    waba: str = WABA,
    template: str | None = None,
) -> None:
    async with admin_session.begin():
        admin_session.add(
            ProviderAccount(
                organization_id=org,
                channel=channel,
                waba_id=waba,
                phone_number_id=phone,
                display_number="+15550783881",
                status=status,
            )
        )
        if template:
            admin_session.add(
                ProviderTemplate(
                    organization_id=org,
                    code="order_a",
                    channel="whatsapp",
                    provider_name="order_a",
                    provider_template_id=template,
                    language="en_US",
                    category="utility",
                    status="pending",
                    variables=[],
                    preview_text="Hi {{1}}",
                    history=[{"status": "pending", "at": "2026-09-26T05:00:00+00:00"}],
                )
            )


async def _opt_out_events(admin_session, org: uuid.UUID) -> int:
    async with admin_session.begin():
        return int(
            (
                await admin_session.execute(
                    select(func.count())
                    .select_from(ConsentEvent)
                    .where(
                        ConsentEvent.organization_id == org,
                        ConsentEvent.source == "meta_user_preference",
                    )
                )
            ).scalar_one()
        )


async def _template_status(admin_session, org: uuid.UUID) -> str:
    async with admin_session.begin():
        return (
            await admin_session.execute(
                select(ProviderTemplate.status).where(ProviderTemplate.organization_id == org)
            )
        ).scalar_one()


async def test_a_stop_is_applied_only_to_the_org_that_owns_the_number(
    client, admin_session
) -> None:
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    await _account(admin_session, org_a, phone=PHONE_A)
    await _account(admin_session, org_b, phone=PHONE_B)

    assert (await _post(client, _payload("user_preferences", _stop(PHONE_A)))).status_code == 200

    assert await _opt_out_events(admin_session, org_a) == 1
    assert await _opt_out_events(admin_session, org_b) == 0


async def test_a_stop_does_not_reach_a_disconnected_org_on_the_same_waba(
    client, admin_session
) -> None:
    org_a, gone = uuid.uuid4(), uuid.uuid4()
    await _account(admin_session, org_a, phone=PHONE_A)
    await _account(admin_session, gone, phone=None, status="disconnected")

    await _post(client, _payload("user_preferences", _stop(PHONE_A)))

    assert await _opt_out_events(admin_session, org_a) == 1
    assert await _opt_out_events(admin_session, gone) == 0


@pytest.mark.parametrize("phone", [None, "", "9999"], ids=["missing", "empty", "unknown"])
async def test_a_stop_without_a_known_number_writes_nothing(
    client, admin_session, phone: str | None
) -> None:
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    await _account(admin_session, org_a, phone=PHONE_A)
    await _account(admin_session, org_b, phone=PHONE_B)

    response = await _post(client, _payload("user_preferences", _stop(phone)))

    assert response.status_code == 200
    assert await _opt_out_events(admin_session, org_a) == 0
    assert await _opt_out_events(admin_session, org_b) == 0


async def test_a_stop_whose_number_belongs_to_another_waba_writes_nothing(
    client, admin_session
) -> None:
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    await _account(admin_session, org_a, phone=PHONE_A)
    await _account(admin_session, org_b, phone=PHONE_B, waba="waba-other")

    await _post(client, _payload("user_preferences", _stop(PHONE_B)))

    assert await _opt_out_events(admin_session, org_a) == 0
    assert await _opt_out_events(admin_session, org_b) == 0


async def test_a_stop_on_a_disconnected_account_that_kept_its_number_writes_nothing(
    client, admin_session
) -> None:
    """A legacy disconnected row can still carry a routing number; it is not a connection."""
    org_a = uuid.uuid4()
    await _account(admin_session, org_a, phone=PHONE_A, status="disconnected")

    await _post(client, _payload("user_preferences", _stop(PHONE_A)))

    assert await _opt_out_events(admin_session, org_a) == 0


async def test_a_stop_on_another_channel_writes_nothing(client, admin_session) -> None:
    org_a = uuid.uuid4()
    await _account(admin_session, org_a, phone=PHONE_A, channel="email")

    await _post(client, _payload("user_preferences", _stop(PHONE_A)))

    assert await _opt_out_events(admin_session, org_a) == 0


async def test_a_waba_level_event_is_not_fanned_out_to_a_disconnected_org(
    client, admin_session
) -> None:
    live, gone = uuid.uuid4(), uuid.uuid4()
    await _account(admin_session, live, phone=PHONE_A, template="555")
    await _account(admin_session, gone, phone=None, status="disconnected", template="555")

    response = await _post(
        client,
        _payload(
            "message_template_status_update",
            {
                "event": "APPROVED",
                "message_template_id": 555,
                "message_template_name": "order_a",
                "message_template_language": "en_US",
                "reason": "NONE",
            },
        ),
    )

    assert response.status_code == 200
    assert await _template_status(admin_session, live) == "approved"
    assert await _template_status(admin_session, gone) == "pending"
