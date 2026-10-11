"""WhatsApp cost from Meta's per-message pricing (ENGAGE-10).

Three halves:

* the receipt fold records the `pricing` object Meta puts on a status webhook (category and
  billable flag) on the message it concerns;
* the cost read prices each billable message by **the recipient's market** (longest calling-code
  prefix in the rate table) and **the category Meta billed it under**, falling back to the
  template's category only for a message no receipt has priced yet;
* the platform rate table is data, seeded from Meta's published INR card (effective 2026-10-01),
  and only an operator (staff) can change it.

Money is integer paise throughout: 0.8631 rupees per message is 86310 paise per 1000.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import m1_cost, services, webhooks
from app.m1_models import META_RATE_LABEL, PLATFORM_ORG_ID, MessagingRateCard
from app.main import app
from app.models import AuditLog, Message, ProviderAccount
from platform_core.auth import OPERATOR_AUDIENCE, Principal, get_principal
from platform_core.config import get_settings
from tests.helpers import principal_for

BASE = "/api/v1/engage/analytics"
WA = f"{BASE}/whatsapp/overview"
CARD = f"{BASE}/rate-card"
STAFF_CARD = "/api/v1/admin/engage/whatsapp-rate-card"
READ = "engage.campaign.campaign.read"

#: A window after the seeded card's effective date, so the Meta rates are in force for it.
DAY = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
RANGE = {"from": "2026-10-05", "to": "2026-10-05"}

INDIA = "+919999900001"
USA = "+14155550100"
KAZAKHSTAN = "+77011234567"
RUSSIA = "+74951234567"
NOWHERE = "+6799912345"  # Fiji: a "Rest of Asia Pacific" market, unpriced here, so "Other".


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def viewer(org_id: uuid.UUID) -> Principal:
    return principal_for(org_id, [f"{READ}:ORG"], entitlements=["engage"])


def operator(*, roles: list[str] | None = None, amr: list[str] | None = None) -> Principal:
    return Principal(
        sub=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        audience=OPERATOR_AUDIENCE,
        roles=roles if roles is not None else ["owner"],
        amr=amr if amr is not None else ["pwd", "otp"],
    )


def wa_message(
    org: uuid.UUID,
    to: str,
    *,
    state: str = "delivered",
    pricing_category: str | None = None,
    billable: bool | None = None,
    template: str | None = "promo",
    category: str = "marketing",
    rule_code: str | None = None,
    conversation_id: uuid.UUID | None = None,
    created_at: datetime = DAY,
) -> Message:
    return Message(
        id=uuid.uuid4(),
        organization_id=org,
        channel="whatsapp",
        category=category,
        to_address=to,
        body="seed",
        state=state,
        direction="outbound",
        created_at=created_at,
        provider_template_code=template,
        pricing_category=pricing_category,
        billable=billable,
        rule_code=rule_code,
        conversation_id=conversation_id,
    )


# --------------------------------------------------------------------- pure


def test_the_pricing_object_on_a_status_is_parsed() -> None:
    raw = {
        "id": "wamid.P1",
        "status": "sent",
        "timestamp": "1790000000",
        "recipient_id": "919999900001",
        "pricing": {
            "billable": True,
            "pricing_model": "PMP",
            "category": "marketing",
            "type": "regular",
        },
    }
    update = webhooks.status_from_raw(raw)
    assert update is not None
    assert (update.pricing_category, update.billable) == ("marketing", True)


def test_a_status_without_pricing_carries_none() -> None:
    update = webhooks.status_from_raw({"id": "wamid.P2", "status": "read", "timestamp": "1"})
    assert update is not None
    assert (update.pricing_category, update.billable) == (None, None)


def test_a_free_service_message_is_not_billable() -> None:
    raw = {
        "id": "wamid.P3",
        "status": "sent",
        "timestamp": "1",
        "pricing": {"billable": False, "category": "service", "type": "free_customer_service"},
    }
    update = webhooks.status_from_raw(raw)
    assert update is not None
    assert (update.pricing_category, update.billable) == ("service", False)


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        ("+919999900001", "91"),
        ("919999900001", "91"),
        ("+14155550100", "1"),
        ("+77011234567", "77"),  # Kazakhstan sits inside +7
        ("+74951234567", "7"),  # Russia is the rest of +7
        ("+6799912345", ""),  # no market of its own: Other
        ("", ""),
    ],
)
def test_the_market_is_the_longest_calling_code_prefix(number: str, expected: str) -> None:
    codes = {"91", "1", "7", "76", "77", ""}
    assert m1_cost.market_code(number, codes) == expected


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        ("marketing", "marketing"),
        ("marketing_lite", "marketing"),
        ("authentication_international", "authentication"),
        ("utility", "utility"),
        ("service", "service"),
        ("referral_conversion", "service"),
    ],
)
def test_metas_categories_fold_onto_the_four_priced_ones(meta: str, expected: str) -> None:
    assert m1_cost.billing_category(meta) == expected


# --------------------------------------------------------------------- the receipt fold


@pytest.mark.integration
async def test_the_fold_records_pricing_even_when_the_status_is_not_progress(
    session, org_id
) -> None:
    """`delivered` can arrive after `read`; its pricing is still the fact Meta billed on."""
    async with session.begin():
        session.add(
            Message(
                organization_id=org_id,
                channel="whatsapp",
                category="operational",
                to_address=INDIA,
                body="x",
                state="read",
                provider_message_id="wamid.F1",
            )
        )
    async with session.begin():
        await services.fold_status(
            session,
            organization_id=org_id,
            provider_message_id="wamid.F1",
            status="delivered",
            occurred_at=datetime.now(UTC) - timedelta(hours=1),
            pricing_category="utility",
            billable=True,
        )
    async with session.begin():
        row = (await session.execute(select(Message))).scalars().one()
    assert (row.state, row.pricing_category, row.billable) == ("read", "utility", True)


SECRET = "ci-only-app-secret"
PHONE_NUMBER_ID = "106540352242999"


@pytest.fixture
def _webhook_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "ci-only-verify-token")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.integration
async def test_a_status_webhook_with_pricing_lands_on_the_message(
    client, session, org_id, _webhook_secret
) -> None:
    async with session.begin():
        session.add(
            ProviderAccount(
                organization_id=org_id,
                channel="whatsapp",
                waba_id="waba-9",
                phone_number_id=PHONE_NUMBER_ID,
                display_number="+15550783881",
                status="connected",
            )
        )
        session.add(
            Message(
                organization_id=org_id,
                channel="whatsapp",
                category="operational",
                to_address=INDIA,
                body="x",
                state="sent",
                provider_message_id="wamid.H1",
            )
        )
    status = {
        "id": "wamid.H1",
        "status": "delivered",
        "timestamp": str(int(datetime.now(UTC).timestamp())),
        "recipient_id": "919999900001",
        "pricing": {"billable": True, "pricing_model": "PMP", "category": "marketing"},
    }
    body = json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "waba-9",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": PHONE_NUMBER_ID},
                                "statuses": [status],
                            },
                        }
                    ],
                }
            ],
        }
    ).encode()
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    response = await client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={digest}"},
    )
    assert response.status_code == 200
    async with session.begin():
        row = (await session.execute(select(Message))).scalars().one()
    assert (row.state, row.pricing_category, row.billable) == ("delivered", "marketing", True)


# --------------------------------------------------------------------- the cost read


@pytest.mark.integration
async def test_cost_prices_each_message_by_its_market_and_metas_category(
    client, session, org_id
) -> None:
    async with session.begin():
        session.add_all(
            [
                # India marketing at 86310 per 1000, two of them: 172.62 -> 173 (half up).
                wa_message(org_id, INDIA, pricing_category="marketing", billable=True),
                wa_message(org_id, INDIA, pricing_category="marketing", billable=True),
                # North America marketing: 183130 per 1000 -> 183.
                wa_message(org_id, USA, pricing_category="marketing", billable=True),
                # Meta says utility although the template says marketing: Meta wins. 11500 -> 12.
                wa_message(org_id, INDIA, pricing_category="utility", billable=True),
                # Meta says it was free: no charge, whatever the category.
                wa_message(
                    org_id, INDIA, pricing_category="utility", billable=False, template="u1"
                ),
                # No receipt has priced this one yet: the template's category estimates it.
                wa_message(org_id, KAZAKHSTAN),
                # Other, priced from the "Other" row: 442480 -> 442.
                wa_message(org_id, NOWHERE, pricing_category="marketing", billable=True),
            ]
        )
    with as_principal(viewer(org_id)):
        body = (await client.get(WA, params=RANGE)).json()
    cost = body["cost"]
    lines = {c["category"]: c for c in cost["by_category"]}
    # marketing: India 2 x 86310 = 172620 -> 172.62, US 183130, KZ 442480, Other 442480, each
    # rounded half-up per (market, category): 173 + 183 + 442 + 442.
    assert lines["marketing"]["amount"] == {"amount_minor": 1240, "currency": "INR"}
    assert lines["utility"]["amount"] == {"amount_minor": 12, "currency": "INR"}
    assert cost["total"] == {"amount_minor": 1252, "currency": "INR"}
    # The counts are every sent message in the category, billable or not.
    assert lines["utility"]["messages"] == 2
    assert lines["marketing"]["messages"] == 5
    assert cost["priced_by_meta"] == 6
    assert cost["rate_card"] == {
        "source": "platform_default",
        "placeholder": False,
        "version": META_RATE_LABEL,
    }


@pytest.mark.integration
async def test_cost_is_broken_down_by_automation_and_by_conversation(
    client, session, org_id
) -> None:
    thread = uuid.uuid4()
    async with session.begin():
        session.add_all(
            [
                wa_message(
                    org_id, INDIA, pricing_category="marketing", billable=True, rule_code="diwali"
                ),
                wa_message(
                    org_id, INDIA, pricing_category="marketing", billable=True, rule_code="diwali"
                ),
                wa_message(
                    org_id,
                    USA,
                    pricing_category="utility",
                    billable=True,
                    rule_code="order_shipped",
                    template="ship",
                ),
                wa_message(
                    org_id,
                    INDIA,
                    pricing_category="utility",
                    billable=True,
                    conversation_id=thread,
                    template="reopen",
                ),
                # A free reply in the same thread costs nothing and is not a cost line.
                wa_message(
                    org_id,
                    INDIA,
                    pricing_category="service",
                    billable=False,
                    conversation_id=thread,
                    template=None,
                ),
            ]
        )
    with as_principal(viewer(org_id)):
        body = (await client.get(WA, params=RANGE)).json()
    sources = {(s["kind"], s["key"]): s for s in body["cost"]["by_source"]}
    assert sources[("automation", "diwali")]["amount"]["amount_minor"] == 173
    assert sources[("automation", "diwali")]["messages"] == 2
    # 24910 per 1000 -> 24.91 -> 25.
    assert sources[("automation", "order_shipped")]["amount"]["amount_minor"] == 25
    assert sources[("conversation", str(thread))]["amount"]["amount_minor"] == 12
    assert sources[("conversation", str(thread))]["messages"] == 1
    # Ordered most expensive first.
    amounts = [s["amount"]["amount_minor"] for s in body["cost"]["by_source"]]
    assert amounts == sorted(amounts, reverse=True)


@pytest.mark.integration
async def test_another_organizations_messages_never_enter_the_cost(
    client, session, admin_session, org_id
) -> None:
    other = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            wa_message(other, INDIA, pricing_category="marketing", billable=True, rule_code="x")
        )
    with as_principal(viewer(org_id)):
        body = (await client.get(WA, params=RANGE)).json()
    assert body["cost"]["total"]["amount_minor"] == 0
    assert body["cost"]["by_source"] == []


# --------------------------------------------------------------------- the rate table


@pytest.mark.integration
async def test_the_seeded_card_is_metas_inr_rates_not_a_placeholder(client, org_id) -> None:
    with as_principal(viewer(org_id)):
        card = (await client.get(CARD)).json()
    assert card["placeholder"] is False
    assert card["version"] == META_RATE_LABEL
    india = {
        r["category"]: r["amount_minor_per_1000_messages"]
        for r in card["rates"]
        if r["calling_code"] == "91"
    }
    assert india == {"marketing": 86310, "utility": 11500, "authentication": 11500, "service": 0}
    other = next(
        r for r in card["rates"] if r["calling_code"] == "" and r["category"] == "marketing"
    )
    assert other["market"] == "Other"
    assert other["effective_from"] == "2026-10-01"
    assert all(r["currency"] == "INR" for r in card["rates"])


def staff_body(amount: int = 99000, *, effective_from: str = "2026-10-01") -> dict:
    """A price for Fiji, which the seeded card leaves to "Other", so a test that writes it never
    disturbs the seeded rows every other test reads."""
    return {
        "effective_from": effective_from,
        "label": "meta-inr-test",
        "rates": [
            {
                "calling_code": "679",
                "market": "Fiji",
                "category": "marketing",
                "amount_minor_per_1000_messages": amount,
                "currency": "INR",
            }
        ],
    }


@pytest.fixture
async def platform_cleanup(admin_session):
    """Platform rows are migration data, not truncated between tests: remove what a test added."""
    yield
    platform = uuid.UUID(PLATFORM_ORG_ID)
    async with admin_session.begin():
        await admin_session.execute(
            MessagingRateCard.__table__.delete().where(
                MessagingRateCard.organization_id == platform,
                MessagingRateCard.calling_code == "679",
            )
        )
        await admin_session.execute(
            AuditLog.__table__.delete().where(AuditLog.organization_id == platform)
        )


def _fiji(card: dict) -> list[dict]:
    return [r for r in card["rates"] if r["calling_code"] == "679"]


@pytest.mark.integration
async def test_staff_can_add_a_platform_rate_and_every_tenant_prices_with_it(
    client, session, org_id, platform_cleanup
) -> None:
    async with session.begin():
        session.add(wa_message(org_id, NOWHERE, pricing_category="marketing", billable=True))
    with as_principal(operator(roles=["billing_ops"])):
        response = await client.put(STAFF_CARD, json=staff_body())
    assert response.status_code == 200, response.text
    with as_principal(viewer(org_id)):
        card = (await client.get(CARD)).json()
        cost = (await client.get(WA, params=RANGE)).json()["cost"]
    assert [(r["market"], r["amount_minor_per_1000_messages"]) for r in _fiji(card)] == [
        ("Fiji", 99000)
    ]
    # Fiji now has its own price instead of "Other": 99000 per 1000 -> 99.
    assert cost["total"]["amount_minor"] == 99


@pytest.mark.integration
async def test_a_staff_change_is_audited_under_the_platform_tenant(
    client, admin_session, platform_cleanup
) -> None:
    with as_principal(operator()):
        assert (await client.put(STAFF_CARD, json=staff_body(77000))).status_code == 200
        # Same day again replaces the price point rather than adding a second one.
        assert (await client.put(STAFF_CARD, json=staff_body(78000))).status_code == 200
    async with admin_session.begin():
        actions = (
            (
                await admin_session.execute(
                    select(AuditLog.action).where(
                        AuditLog.organization_id == uuid.UUID(PLATFORM_ORG_ID)
                    )
                )
            )
            .scalars()
            .all()
        )
        rows = (
            (
                await admin_session.execute(
                    select(MessagingRateCard).where(
                        MessagingRateCard.organization_id == uuid.UUID(PLATFORM_ORG_ID),
                        MessagingRateCard.calling_code == "679",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert actions == ["messaging_rate_card.platform_updated"] * 2
    assert [(r.effective_from, r.amount_minor_per_1000_messages) for r in rows] == [
        (date(2026, 10, 1), 78000)
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("principal", "status"),
    [
        # A tenant token, even an all-powerful one, does not learn the route exists.
        (
            lambda org: principal_for(
                org, ["engage.setup.whatsapp.write:ORG"], entitlements=["engage"]
            ),
            404,
        ),
        (lambda org: operator(roles=["support"]), 403),
        (lambda org: operator(amr=["pwd"]), 403),
    ],
)
async def test_only_billing_staff_can_change_the_platform_rates(
    client, org_id, principal, status, platform_cleanup
) -> None:
    with as_principal(principal(org_id)):
        response = await client.put(STAFF_CARD, json=staff_body(1))
    assert response.status_code == status
    with as_principal(viewer(org_id)):
        card = (await client.get(CARD)).json()
    assert _fiji(card) == []


@pytest.mark.integration
@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b["rates"][0].update(calling_code="+91"),
        lambda b: b["rates"][0].update(calling_code="12345"),
        lambda b: b["rates"][0].update(amount_minor_per_1000_messages=-1),
        lambda b: b["rates"][0].update(amount_minor_per_1000_messages=1.5),
        lambda b: b["rates"][0].update(currency="inr"),
        lambda b: b["rates"][0].update(category="promo"),
        lambda b: b.update(rates=[]),
        lambda b: b.pop("effective_from"),
    ],
)
async def test_a_bad_staff_body_is_a_422(client, mutate, platform_cleanup) -> None:
    body = staff_body()
    mutate(body)
    with as_principal(operator()):
        response = await client.put(STAFF_CARD, json=body)
    assert response.status_code == 422, response.text
