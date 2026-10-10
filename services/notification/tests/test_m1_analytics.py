"""Messaging analytics and cost (slice M1) against real PostgreSQL, as the app role.

The fixture is a small, hand-counted ledger, so every assertion is a number worked out on paper
rather than a re-run of the query under test. It deliberately includes the edges that decide
whether a bucket is right: the first and last instant of the window, the instant just outside it
on both sides, and a message sent just after midnight in India (which is still the previous day in
UTC — buckets are UTC days, and this test says so).

Window used throughout: 2026-09-10 .. 2026-09-12, inclusive, UTC.
"""

from __future__ import annotations

import contextlib
import sys
import types
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select, text

from app.m1_models import PLACEHOLDER_LABEL, MessagingRateCard
from app.main import app
from app.models import (
    AuditLog,
    Conversation,
    Delivery,
    InboundMessage,
    Message,
    ProviderAccount,
    ProviderTemplate,
    Rule,
)
from platform_core.auth import Principal, get_principal
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

BASE = "/api/v1/engage/analytics"
WA = f"{BASE}/whatsapp/overview"
HUB = f"{BASE}/overview"
CARD = f"{BASE}/rate-card"
RANGE = {"from": "2026-09-10", "to": "2026-09-12"}

READ = "engage.campaign.campaign.read"
WRITE = "engage.setup.whatsapp.write"
CONTACT_WRITE = "engage.audience.contact.write"


def utc(day: int, hour: int = 0, minute: int = 0, second: int = 0, micro: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, second, micro, tzinfo=UTC)


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def caller(org_id: uuid.UUID, perms: list[str], *, entitlements: list[str] | None = None):
    return principal_for(
        org_id,
        [f"{p}:ORG" for p in perms],
        entitlements=entitlements if entitlements is not None else ["engage"],
    )


def viewer(org_id: uuid.UUID) -> Principal:
    return caller(org_id, [READ])


def admin(org_id: uuid.UUID) -> Principal:
    return caller(org_id, [READ, WRITE])


# --------------------------------------------------------------------- the ledger


def message(
    org: uuid.UUID,
    state: str,
    created_at: datetime,
    *,
    template: str | None = None,
    category: str = "operational",
    channel: str = "whatsapp",
    direction: str = "outbound",
    error_code: str | None = None,
    conversation_id: uuid.UUID | None = None,
) -> Message:
    return Message(
        id=uuid.uuid4(),
        organization_id=org,
        channel=channel,
        category=category,
        to_address="+919999900001",
        body="seed",
        state=state,
        direction=direction,
        created_at=created_at,
        provider_template_code=template,
        provider_error_code=error_code,
        conversation_id=conversation_id,
    )


def conversation(
    org: uuid.UUID,
    tag: str,
    *,
    status: str = "open",
    assignee: uuid.UUID | None = None,
    created_at: datetime,
) -> Conversation:
    return Conversation(
        id=uuid.uuid4(),
        organization_id=org,
        channel="whatsapp",
        wa_address_enc=b"sealed",
        wa_address_masked=f"+91•••{tag}",
        wa_address_index=f"idx-{tag}-{uuid.uuid4()}",
        status=status,
        assignee_membership_id=assignee,
        created_at=created_at,
    )


def inbound(org: uuid.UUID, conversation_id: uuid.UUID, at: datetime) -> InboundMessage:
    return InboundMessage(
        organization_id=org,
        conversation_id=conversation_id,
        provider_message_id=f"wamid.{uuid.uuid4()}",
        type="text",
        body_masked="",
        raw_enc=b"sealed",
        occurred_at=at,
    )


def template(
    org: uuid.UUID, code: str, category: str, status: str, checked_at: datetime | None = None
) -> ProviderTemplate:
    return ProviderTemplate(
        organization_id=org,
        code=code,
        channel="whatsapp",
        provider_name=code,
        language="en",
        category=category,
        status=status,
        status_checked_at=checked_at,
    )


async def seed_ledger(session, org: uuid.UUID) -> dict[str, uuid.UUID]:
    """The hand-counted ledger. Expected totals in the window:

    sent 7, delivered 3, read 1, failed 4, queued 1, suppressed 1
    by category: marketing 5, utility 1, authentication 0, service 1
    estimated cost at the placeholder rates: 392 + 12 + 0 + 0 = 404 paise
    replies 1 of 2 inbound
    """
    ids: dict[str, uuid.UUID] = {}
    conv_answered = conversation(org, "01", assignee=uuid.uuid4(), created_at=utc(11, 8, 30))
    conv_unanswered = conversation(org, "02", created_at=utc(11, 10, 30))
    conv_closed = conversation(
        org, "03", status="closed", created_at=datetime(2026, 8, 1, tzinfo=UTC)
    )
    ids["conv_answered"] = conv_answered.id

    day10 = [
        # First instant of the window (inclusive).
        message(org, "read", utc(10, 0, 0, 0), template="promo"),
        message(org, "delivered", utc(10, 12), template="promo"),
        # Last microsecond of the 10th.
        message(org, "sent", utc(10, 23, 59, 59, 999999), template="promo"),
        # 00:10 on the 11th in India is 18:40 on the 10th in UTC: still the 10th here.
        message(org, "sent", utc(10, 18, 40), template="promo"),
    ]
    failed_receipt = message(org, "failed", utc(11, 8), template="otp", error_code="131026")
    failed_body = message(org, "failed", utc(11, 8, 5), template="invoice")
    failed_http = message(org, "failed", utc(11, 8, 10), template="invoice")
    failed_none = message(org, "failed", utc(11, 8, 15), template="invoice")
    day11 = [
        failed_receipt,
        failed_body,
        failed_http,
        failed_none,
        # A free-text reply inside the service window: no template, so `service`.
        message(org, "delivered", utc(11, 9), conversation_id=conv_answered.id),
        # Template row is gone: falls back on the staging bucket.
        message(org, "sent", utc(11, 13), template="ghost", category="marketing"),
        message(org, "sent", utc(11, 14), template="ghost2", category="operational"),
    ]
    day12 = [
        message(org, "pending", utc(12, 6), template="promo"),
        message(org, "suppressed", utc(12, 7), template="promo"),
    ]
    outside = [
        message(org, "read", utc(9, 23, 59, 59, 999999), template="promo"),
        message(org, "read", utc(13, 0, 0, 0), template="promo"),
        # Not WhatsApp, and not outbound: never counted.
        message(org, "sent", utc(11, 9), template="promo", channel="email"),
        message(org, "sent", utc(11, 9), template="promo", direction="inbound"),
    ]

    deliveries = [
        # Two failed attempts: the *last* one is the reason. First was a 503, then Meta's refusal.
        Delivery(
            organization_id=org,
            message_id=failed_body.id,
            attempt_no=1,
            provider="whatsapp_cloud",
            outcome="failed",
            detail="503: upstream unavailable",
            at=utc(11, 8, 5),
        ),
        Delivery(
            organization_id=org,
            message_id=failed_body.id,
            attempt_no=2,
            provider="whatsapp_cloud",
            outcome="failed",
            detail=(
                '400: {"error":{"message":"Template name does not exist","type":'
                '"OAuthException","code":132001,"fbtrace_id":"x"}}'
            ),
            at=utc(11, 8, 6),
        ),
        Delivery(
            organization_id=org,
            message_id=failed_http.id,
            attempt_no=1,
            provider="whatsapp_cloud",
            outcome="failed",
            detail="503: upstream unavailable",
            at=utc(11, 8, 11),
        ),
    ]

    templates = [
        template(org, "promo", "marketing", "approved", utc(10, 9)),
        template(org, "otp", "authentication", "approved"),
        template(org, "invoice", "utility", "pending"),
        template(org, "old_tpl", "utility", "rejected", utc(11, 12)),
    ]
    rules = [
        Rule(
            organization_id=org,
            code="wa_active",
            event_type="crm.pipeline.deal.stage_changed",
            channel="whatsapp",
            active=True,
            created_at=utc(12, 7),
        ),
        Rule(
            organization_id=org,
            code="wa_inactive",
            event_type="crm.pipeline.deal.created",
            channel="whatsapp",
            active=False,
            created_at=datetime(2026, 8, 1, tzinfo=UTC),
        ),
        Rule(
            organization_id=org,
            code="mail_active",
            event_type="crm.pipeline.deal.stage_changed",
            channel="email",
            active=True,
            created_at=utc(12, 8),
        ),
    ]

    async with session.begin():
        session.add_all([conv_answered, conv_unanswered, conv_closed])
        await session.flush()
        session.add_all(day10 + day11 + day12 + outside)
        await session.flush()
        session.add_all(deliveries + templates + rules)
        session.add_all(
            [
                # Answers the free-text reply sent on the 11th: a reply.
                inbound(org, conv_answered.id, utc(11, 10)),
                # A conversation nobody wrote to in the window: inbound, but not a reply.
                inbound(org, conv_unanswered.id, utc(11, 11)),
                # Just outside the window on both sides: never counted.
                inbound(org, conv_answered.id, utc(9, 23, 59, 59, 999999)),
                inbound(org, conv_answered.id, utc(13, 0, 0, 0)),
            ]
        )
    return ids


# --------------------------------------------------------------------- WhatsApp overview


async def test_metrics_match_the_hand_counted_ledger(client, session, org_id) -> None:
    await seed_ledger(session, org_id)
    with as_principal(viewer(org_id)):
        response = await client.get(WA, params=RANGE)
    assert response.status_code == 200, response.text
    body = response.json()

    assert (body["sent"], body["delivered"], body["read"], body["failed"]) == (7, 3, 1, 4)
    assert body["queued"] == 1
    assert body["suppressed"] == 1
    assert body["replies"] == 1
    assert body["inbound_messages"] == 2

    assert body["status_breakdown"] == [
        {"status": "read", "count": 1},
        {"status": "delivered", "count": 2},
        {"status": "sent", "count": 4},
        {"status": "failed", "count": 4},
    ]
    assert body["by_category"] == [
        {"category": "marketing", "messages": 5},
        {"category": "utility", "messages": 1},
        {"category": "authentication", "messages": 0},
        {"category": "service", "messages": 1},
    ]
    assert sum(c["messages"] for c in body["by_category"]) == body["sent"]


async def test_series_is_dense_and_buckets_by_utc_day(client, session, org_id) -> None:
    await seed_ledger(session, org_id)
    with as_principal(viewer(org_id)):
        body = (await client.get(WA, params=RANGE)).json()
    assert body["series"] == [
        # 10th: the window's first instant, the IST-just-after-midnight message (18:40Z) and the
        # last microsecond of the day all land here.
        {"date": "2026-09-10", "sent": 4, "delivered": 2, "read": 1, "failed": 0},
        {"date": "2026-09-11", "sent": 3, "delivered": 1, "read": 0, "failed": 4},
        {"date": "2026-09-12", "sent": 0, "delivered": 0, "read": 0, "failed": 0},
    ]
    # Every series column adds up to the headline number.
    for key in ("sent", "delivered", "read", "failed"):
        assert sum(point[key] for point in body["series"]) == body[key]


async def test_days_with_no_messages_are_present_as_zeros(client, session, org_id) -> None:
    await seed_ledger(session, org_id)
    with as_principal(viewer(org_id)):
        body = (await client.get(WA, params={"from": "2026-09-08", "to": "2026-09-14"})).json()
    assert [p["date"] for p in body["series"]] == [f"2026-09-{d:02d}" for d in range(8, 15)]
    # The two `read` messages just outside 10..12 now fall inside.
    assert body["read"] == 3
    assert body["series"][0] == {
        "date": "2026-09-08",
        "sent": 0,
        "delivered": 0,
        "read": 0,
        "failed": 0,
    }


async def test_window_edges_are_inclusive_at_the_start_and_exclusive_after_the_end(
    client, session, org_id
) -> None:
    await seed_ledger(session, org_id)
    with as_principal(viewer(org_id)):
        just_the_9th = (
            await client.get(WA, params={"from": "2026-09-09", "to": "2026-09-09"})
        ).json()
        just_the_13th = (
            await client.get(WA, params={"from": "2026-09-13", "to": "2026-09-13"})
        ).json()
        just_the_10th = (
            await client.get(WA, params={"from": "2026-09-10", "to": "2026-09-10"})
        ).json()
    # 23:59:59.999999 on the 9th belongs to the 9th; midnight on the 13th belongs to the 13th.
    assert just_the_9th["read"] == 1 and just_the_9th["sent"] == 1
    assert just_the_13th["read"] == 1 and just_the_13th["sent"] == 1
    # The 10th, on its own, is exactly the four day-10 rows.
    assert (just_the_10th["sent"], just_the_10th["delivered"], just_the_10th["read"]) == (4, 2, 1)


async def test_failures_are_grouped_by_the_last_recorded_reason(client, session, org_id) -> None:
    await seed_ledger(session, org_id)
    with as_principal(viewer(org_id)):
        body = (await client.get(WA, params=RANGE)).json()
    reasons = {r["code"]: r for r in body["failures_by_reason"]}
    assert set(reasons) == {"131026", "132001", "http_503", "dispatch_failed"}
    assert all(r["count"] == 1 for r in reasons.values())
    # From a receipt's error code; from the *last* attempt's refusal body (not the earlier 503);
    # from an HTTP status alone; and the honest fallback.
    assert reasons["131026"]["label"] == "Message undeliverable"
    assert reasons["132001"]["label"] == "Template does not exist"
    assert "503" in reasons["http_503"]["label"]
    assert reasons["dispatch_failed"]["label"]
    assert sum(r["count"] for r in reasons.values()) == body["failed"]


async def test_cost_is_an_estimate_in_integer_minor_units_at_the_placeholder_rates(
    client, session, org_id
) -> None:
    await seed_ledger(session, org_id)
    with as_principal(viewer(org_id)):
        body = (await client.get(WA, params=RANGE)).json()
    cost = body["cost"]
    assert cost["estimated"] is True
    assert cost["total"] == {"amount_minor": 404, "currency": "INR"}
    lines = {c["category"]: c for c in cost["by_category"]}
    # 5 x 78460 / 1000 = 392.3 -> 392, and 1 x 11500 / 1000 = 11.5 -> 12 (half up).
    assert lines["marketing"]["amount"] == {"amount_minor": 392, "currency": "INR"}
    assert lines["utility"]["amount"] == {"amount_minor": 12, "currency": "INR"}
    assert lines["authentication"]["amount"]["amount_minor"] == 0
    assert lines["service"]["amount"]["amount_minor"] == 0
    assert lines["service"]["messages"] == 1
    assert sum(c["amount"]["amount_minor"] for c in cost["by_category"]) == 404
    assert cost["rate_card"] == {
        "source": "platform_default",
        "placeholder": True,
        "version": PLACEHOLDER_LABEL,
    }
    # No float anywhere: every amount is a JSON integer.
    assert isinstance(cost["total"]["amount_minor"], int)
    assert all(type(c["amount"]["amount_minor"]) is int for c in cost["by_category"])


async def test_totals_and_recent_activity(client, session, org_id) -> None:
    await seed_ledger(session, org_id)
    with as_principal(viewer(org_id)):
        body = (await client.get(WA, params=RANGE)).json()
    assert body["totals"] == {
        "active_automations": 1,  # the inactive rule and the email rule do not count
        "approved_templates": 2,
        "pending_templates": 1,
        "active_conversations": 2,  # the closed one does not count
        "unassigned_conversations": 1,
    }
    kinds = [(a["kind"], a["text"]) for a in body["recent_activity"]]
    assert kinds == [
        ("automation_created", "wa_active"),
        ("template_rejected", "old_tpl (en)"),
        ("conversation_started", "+91•••02"),
        ("conversation_started", "+91•••01"),
        ("template_approved", "promo (en)"),
    ]
    # Only the masked number ever appears in an activity row.
    assert "9999900001" not in str(body)


async def test_an_empty_organization_returns_a_full_zeroed_response(client, org_id) -> None:
    with as_principal(viewer(org_id)):
        body = (await client.get(WA, params=RANGE)).json()
    assert (body["sent"], body["delivered"], body["read"], body["failed"], body["replies"]) == (
        0,
    ) * 5
    assert len(body["series"]) == 3
    assert body["failures_by_reason"] == []
    assert body["recent_activity"] == []
    assert body["cost"]["total"] == {"amount_minor": 0, "currency": "INR"}
    assert [c["messages"] for c in body["by_category"]] == [0, 0, 0, 0]


async def test_omitting_the_range_defaults_to_the_last_thirty_days(client, org_id) -> None:
    with as_principal(viewer(org_id)):
        body = (await client.get(WA)).json()
    today = datetime.now(UTC).date()
    assert len(body["series"]) == 30
    assert body["series"][-1]["date"] == today.isoformat()


# --------------------------------------------------------------------- tenant isolation


async def test_another_organizations_data_never_leaks_in(
    client, session, admin_session, org_id
) -> None:
    other = uuid.uuid4()
    await seed_ledger(session, org_id)
    # A second tenant with a bigger ledger, in the same window, written as the admin role.
    async with admin_session.begin():
        conv = conversation(other, "99", created_at=utc(11, 8))
        admin_session.add(conv)
        await admin_session.flush()
        admin_session.add_all(
            [message(other, "read", utc(11, 9), template="promo") for _ in range(25)]
            + [message(other, "failed", utc(11, 10), template="promo", error_code="190")]
            + [inbound(other, conv.id, utc(11, 12))]
            + [template(other, "promo", "authentication", "approved", utc(11, 9))]
            + [
                MessagingRateCard(
                    organization_id=other,
                    channel="whatsapp",
                    category=category,
                    amount_minor_per_1000_messages=1,
                    currency="USD",
                    effective_from=date(2026, 1, 1),
                    label="organization",
                    is_placeholder=False,
                )
                for category in ("marketing", "utility", "authentication", "service")
            ]
        )
    with as_principal(viewer(org_id)):
        body = (await client.get(WA, params=RANGE)).json()
        card = (await client.get(CARD)).json()
    assert (body["sent"], body["delivered"], body["read"], body["failed"]) == (7, 3, 1, 4)
    assert "190" not in {r["code"] for r in body["failures_by_reason"]}
    assert body["inbound_messages"] == 2
    assert body["totals"]["active_conversations"] == 2
    assert body["cost"]["total"] == {"amount_minor": 404, "currency": "INR"}
    # Neither the other tenant's USD override nor its template category reaches this tenant.
    assert {r["currency"] for r in card["rates"]} == {"INR"}
    assert card["placeholder"] is True

    # And the reverse: the other organization sees only its own.
    with as_principal(viewer(other)):
        theirs = (await client.get(WA, params=RANGE)).json()
    assert theirs["read"] == 25
    assert theirs["failed"] == 1
    assert theirs["cost"]["total"]["currency"] == "USD"


# --------------------------------------------------------------------- range validation


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-09-12", "to": "2026-09-10"},  # inverted
        {"from": "2025-09-01", "to": "2026-09-12"},  # longer than 366 days
        {"from": "not-a-date", "to": "2026-09-12"},
        {"from": "2026-13-01", "to": "2026-09-12"},
        {"from": "2026-02-30"},
        {"to": "9999-12-31"},  # the day after the last day does not exist
        {"to": "0001-01-01"},  # neither does the default start, 29 days before the first
    ],
)
@pytest.mark.parametrize("path", [WA, HUB])
async def test_a_bad_range_is_a_422_problem(client, org_id, path, params) -> None:
    with as_principal(viewer(org_id)):
        response = await client.get(path, params=params)
    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_an_inverted_range_names_the_problem(client, org_id) -> None:
    with as_principal(viewer(org_id)):
        response = await client.get(WA, params={"from": "2026-09-12", "to": "2026-09-10"})
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "range_inverted"


async def test_the_longest_allowed_range_is_accepted(client, org_id) -> None:
    with as_principal(viewer(org_id)):
        response = await client.get(WA, params={"from": "2026-01-01", "to": "2026-12-31"})
    assert response.status_code == 200
    assert len(response.json()["series"]) == 365


# --------------------------------------------------------------------- negative authorization

ROUTES = [
    ("GET", WA, None),
    ("GET", HUB, None),
    ("GET", CARD, None),
    (
        "PUT",
        CARD,
        {
            "rates": [
                {"category": c, "amount_minor_per_1000_messages": 1, "currency": "INR"}
                for c in ("marketing", "utility", "authentication", "service")
            ]
        },
    ),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_a_caller_with_no_permission_is_refused(client, org_id, method, path, body) -> None:
    with as_principal(caller(org_id, [])):
        response = await client.request(method, path, json=body)
    assert response.status_code == 403


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_a_caller_without_the_engage_entitlement_is_refused(
    client, org_id, method, path, body
) -> None:
    with as_principal(caller(org_id, [READ, WRITE], entitlements=["notification"])):
        response = await client.request(method, path, json=body)
    # 402, not 403: the permissions are fine, the subscription is not.
    assert response.status_code == 402


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_an_unrelated_engage_permission_does_not_open_analytics(
    client, org_id, method, path, body
) -> None:
    unrelated = caller(org_id, ["engage.inbox.conversation.read", "engage.setup.whatsapp.read"])
    with as_principal(unrelated):
        response = await client.request(method, path, json=body)
    assert response.status_code == 403


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_a_narrower_scope_than_org_is_refused(client, org_id, method, path, body) -> None:
    own_only = principal_for(org_id, [f"{READ}:OWN", f"{WRITE}:OWN"], entitlements=["engage"])
    with as_principal(own_only):
        response = await client.request(method, path, json=body)
    assert response.status_code == 403


async def test_a_reader_cannot_override_the_rate_card(client, session, org_id) -> None:
    with as_principal(viewer(org_id)):
        response = await client.put(CARD, json=ROUTES[3][2])
    assert response.status_code == 403
    rows = (
        await session.execute(
            select(MessagingRateCard).where(MessagingRateCard.organization_id == org_id)
        )
    ).all()
    assert rows == []


async def test_a_contact_editor_cannot_override_the_rate_card(client, session, org_id) -> None:
    """The rates decide every cost figure the organization sees, so the grant that edits contacts
    (and the viewer's read) must not reach them: only the WhatsApp setup grant does."""
    contact_editor = caller(org_id, [READ, CONTACT_WRITE, "engage.campaign.campaign.write"])
    with as_principal(contact_editor):
        response = await client.put(CARD, json=ROUTES[3][2])
        assert (await client.get(CARD)).status_code == 200  # reading stays open to a reader
    assert response.status_code == 403
    rows = (
        await session.execute(
            select(MessagingRateCard).where(MessagingRateCard.organization_id == org_id)
        )
    ).all()
    assert rows == []


# --------------------------------------------------------------------- the rate card


def card_body(*, marketing: int = 90000, currency: str = "INR", effective_from: str | None = None):
    body: dict[str, Any] = {
        "rates": [
            {
                "category": "marketing",
                "amount_minor_per_1000_messages": marketing,
                "currency": currency,
            },
            {"category": "utility", "amount_minor_per_1000_messages": 20000, "currency": currency},
            {
                "category": "authentication",
                "amount_minor_per_1000_messages": 15000,
                "currency": currency,
            },
            {"category": "service", "amount_minor_per_1000_messages": 0, "currency": currency},
        ]
    }
    if effective_from is not None:
        body["effective_from"] = effective_from
    return body


async def test_the_shipped_defaults_are_labelled_placeholders(client, org_id) -> None:
    with as_principal(viewer(org_id)):
        response = await client.get(CARD)
    assert response.status_code == 200
    body = response.json()
    assert body["placeholder"] is True
    assert body["estimated"] is True
    assert body["version"] == PLACEHOLDER_LABEL
    assert "PLACEHOLDER" in body["note"]
    rates = {r["category"]: r for r in body["rates"]}
    assert {c: r["amount_minor_per_1000_messages"] for c, r in rates.items()} == {
        "marketing": 78460,
        "utility": 11500,
        "authentication": 11500,
        "service": 0,
    }
    assert all(r["source"] == "platform_default" and r["placeholder"] for r in rates.values())
    assert all(r["currency"] == "INR" for r in rates.values())


async def test_an_override_takes_precedence_and_is_no_longer_a_placeholder(
    client, session, org_id
) -> None:
    with as_principal(admin(org_id)):
        put = await client.put(CARD, json=card_body())
        assert put.status_code == 200, put.text
        got = (await client.get(CARD)).json()
    assert put.json() == got
    assert got["placeholder"] is False
    assert got["version"] == "organization"
    rates = {r["category"]: r for r in got["rates"]}
    assert rates["marketing"]["amount_minor_per_1000_messages"] == 90000
    assert rates["marketing"]["source"] == "organization"
    assert all(r["source"] == "organization" for r in rates.values())


async def test_the_override_prices_the_estimate_from_its_effective_date(
    client, session, org_id
) -> None:
    await seed_ledger(session, org_id)
    today = datetime.now(UTC).date()
    with as_principal(admin(org_id)):
        # Effective today: a window that ended on 2026-09-12 predates it, so it is still priced
        # at the placeholder default; a window that runs up to today is priced at the override.
        assert (
            await client.put(CARD, json=card_body(effective_from=today.isoformat()))
        ).status_code == 200
        old_window = (await client.get(WA, params=RANGE)).json()
        through_today = (
            await client.get(WA, params={"from": "2026-09-10", "to": today.isoformat()})
        ).json()
    assert old_window["cost"]["total"]["amount_minor"] == 404
    assert old_window["cost"]["rate_card"]["placeholder"] is True
    # The range now also reaches the promo message sent at midnight on the 13th, so marketing is
    # 6 x 90000/1000 = 540, and utility 1 x 20000/1000 = 20: 560.
    assert through_today["cost"]["total"] == {"amount_minor": 560, "currency": "INR"}
    assert through_today["cost"]["rate_card"] == {
        "source": "organization",
        "placeholder": False,
        "version": "organization",
    }


async def test_a_future_dated_override_leaves_the_default_in_force(client, org_id) -> None:
    future = (datetime.now(UTC).date() + timedelta(days=30)).isoformat()
    with as_principal(admin(org_id)):
        assert (await client.put(CARD, json=card_body(effective_from=future))).status_code == 200
        got = (await client.get(CARD)).json()
    assert got["placeholder"] is True
    assert all(r["source"] == "platform_default" for r in got["rates"])


async def test_saving_the_same_day_twice_replaces_it_and_a_new_day_adds_a_price_point(
    client, session, org_id
) -> None:
    today = datetime.now(UTC).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    with as_principal(admin(org_id)):
        assert (await client.put(CARD, json=card_body(marketing=90000))).status_code == 200
        assert (await client.put(CARD, json=card_body(marketing=95000))).status_code == 200
        assert (
            await client.put(CARD, json=card_body(marketing=70000, effective_from=yesterday))
        ).status_code == 200
        got = (await client.get(CARD)).json()
    marketing = next(r for r in got["rates"] if r["category"] == "marketing")
    assert marketing["amount_minor_per_1000_messages"] == 95000  # today's row, replaced once
    rows = (
        (
            await session.execute(
                select(MessagingRateCard).where(
                    MessagingRateCard.organization_id == org_id,
                    MessagingRateCard.category == "marketing",
                )
            )
        )
        .scalars()
        .all()
    )
    # One row for today (replaced, not duplicated) and one for yesterday: history is kept.
    assert sorted(r.effective_from for r in rows) == [today - timedelta(days=1), today]


async def test_an_override_is_audited(client, session, org_id) -> None:
    with as_principal(admin(org_id)):
        assert (await client.put(CARD, json=card_body())).status_code == 200
    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.organization_id == org_id)))
        .scalars()
        .all()
    )
    assert [r.action for r in rows] == ["messaging_rate_card.overridden"]


async def test_one_organizations_override_never_changes_anothers_estimate(
    client, session, admin_session, org_id
) -> None:
    other = uuid.uuid4()
    await seed_ledger(session, org_id)
    with as_principal(admin(other)):
        assert (await client.put(CARD, json=card_body(marketing=1_000_000))).status_code == 200
    with as_principal(viewer(org_id)):
        body = (await client.get(WA, params=RANGE)).json()
        card = (await client.get(CARD)).json()
    assert body["cost"]["total"]["amount_minor"] == 404
    assert card["placeholder"] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b["rates"].pop(),  # three categories
        lambda b: b["rates"].append(dict(b["rates"][0])),  # five
        lambda b: b["rates"].__setitem__(
            1, dict(b["rates"][0])
        ),  # marketing twice, utility missing
        lambda b: b["rates"][0].update(currency="USD"),  # two currencies
        lambda b: b["rates"][0].update(currency="inr"),  # not ISO upper-case
        lambda b: b["rates"][0].update(currency="RUPEE"),
        lambda b: b["rates"][0].update(amount_minor_per_1000_messages=-1),
        lambda b: b["rates"][0].update(amount_minor_per_1000_messages=1.5),  # a float
        lambda b: b["rates"][0].update(amount_minor_per_1000_messages="lots"),
        lambda b: b["rates"][0].update(category="promotional"),
        lambda b: b["rates"][0].update(surprise=True),  # unknown field on a rate
        lambda b: b.update(surprise=True),  # unknown field on the body
        lambda b: b.update(effective_from="soon"),
    ],
)
async def test_a_bad_rate_card_body_is_a_422_and_writes_nothing(
    client, session, org_id, mutate
) -> None:
    body = card_body()
    mutate(body)
    with as_principal(admin(org_id)):
        response = await client.put(CARD, json=body)
    assert response.status_code == 422, response.text
    count = (
        await session.execute(
            text(
                "SELECT count(*) FROM notification.messaging_rate_cards WHERE organization_id = :o"
            ).bindparams(o=org_id)
        )
    ).scalar_one()
    assert count == 0


async def test_an_override_in_another_currency_prices_the_estimate_in_it(
    client, session, org_id
) -> None:
    await seed_ledger(session, org_id)
    today = datetime.now(UTC).date().isoformat()
    with as_principal(admin(org_id)):
        assert (
            await client.put(CARD, json=card_body(currency="USD", effective_from="2026-09-01"))
        ).status_code == 200
        body = (await client.get(WA, params=RANGE)).json()
    assert body["cost"]["total"] == {"amount_minor": 470, "currency": "USD"}
    assert today  # the override predates the window, so it applies to it


async def test_the_platform_default_rows_cannot_be_written_by_a_tenant(session, org_id) -> None:
    """RLS lets a tenant *read* the nil-tenant defaults and nothing more."""
    from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

    async with session.begin():
        visible = (
            await session.execute(
                text(
                    "SELECT count(*) FROM notification.messaging_rate_cards "
                    "WHERE organization_id = '00000000-0000-0000-0000-000000000000'"
                )
            )
        ).scalar_one()
        assert visible == 4
        updated = await session.execute(
            text(
                "UPDATE notification.messaging_rate_cards SET amount_minor_per_1000_messages = 0 "
                "WHERE organization_id = '00000000-0000-0000-0000-000000000000'"
            )
        )
        assert updated.rowcount == 0  # the row is not visible to UPDATE

    with pytest.raises((DBAPIError, IntegrityError, ProgrammingError)):
        async with session.begin():
            await session.execute(
                text(
                    "INSERT INTO notification.messaging_rate_cards "
                    "(id, organization_id, channel, category, amount_minor_per_1000_messages, "
                    "currency, effective_from, label, is_placeholder) VALUES "
                    "(gen_random_uuid(), '00000000-0000-0000-0000-000000000000', 'whatsapp', "
                    "'marketing', 1, 'INR', '2027-01-01', 'x', false)"
                )
            )


# --------------------------------------------------------------------- the hub


def stub_email_module(monkeypatch: pytest.MonkeyPatch, result: Any, calls: list[tuple]) -> None:
    async def email_overview(session, organization_id, date_from, date_to):
        calls.append((organization_id, date_from, date_to))
        return result

    module = types.ModuleType("app.e1_analytics")
    module.email_overview = email_overview  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.e1_analytics", module)


async def test_the_hub_passes_the_window_to_the_email_overview_and_returns_its_result(
    client, session, org_id, monkeypatch
) -> None:
    await seed_ledger(session, org_id)
    calls: list[tuple] = []
    email = {"sent": 12, "delivered": 10, "opened": 4, "clicked": 1, "failed": 2, "series": []}
    stub_email_module(monkeypatch, email, calls)
    with as_principal(viewer(org_id)):
        response = await client.get(HUB, params=RANGE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert calls == [(org_id, date(2026, 9, 10), date(2026, 9, 12))]
    assert body["email"] == email
    assert body["whatsapp"]["sent"] == 7
    assert body["whatsapp"]["cost"]["total"]["amount_minor"] == 404


async def test_the_hub_accepts_a_pydantic_result_from_the_email_overview(
    client, org_id, monkeypatch
) -> None:
    class EmailOverview(BaseModel):
        sent: int
        opened: int

    stub_email_module(monkeypatch, EmailOverview(sent=3, opened=1), [])
    with as_principal(viewer(org_id)):
        body = (await client.get(HUB, params=RANGE)).json()
    assert body["email"] == {"sent": 3, "opened": 1}


async def test_the_hub_degrades_to_email_null_when_the_email_module_cannot_be_imported(
    client, session, org_id, monkeypatch
) -> None:
    await seed_ledger(session, org_id)
    # `None` in `sys.modules` makes the import raise `ImportError`, whether or not the module
    # exists in this checkout.
    monkeypatch.setitem(sys.modules, "app.e1_analytics", None)
    with as_principal(viewer(org_id)):
        response = await client.get(HUB, params=RANGE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] is None
    # The WhatsApp half is untouched by the missing module.
    assert body["whatsapp"]["sent"] == 7
    assert body["costs"]["total"] == {"amount_minor": 404, "currency": "INR"}


async def test_an_error_inside_the_email_overview_is_not_swallowed(
    client, org_id, monkeypatch
) -> None:
    async def email_overview(session, organization_id, date_from, date_to):
        raise RuntimeError("boom")

    module = types.ModuleType("app.e1_analytics")
    module.email_overview = email_overview  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.e1_analytics", module)
    with as_principal(viewer(org_id)), pytest.raises(RuntimeError, match="boom"):
        await client.get(HUB, params=RANGE)


async def test_the_hub_reports_which_channels_are_connected(
    client, session, org_id, monkeypatch
) -> None:
    monkeypatch.setitem(sys.modules, "app.e1_analytics", None)
    async with session.begin():
        session.add(
            ProviderAccount(
                organization_id=org_id,
                channel="whatsapp",
                waba_id="waba-1",
                phone_number_id=f"pn-{uuid.uuid4()}",
                display_number="+91 99999 00001",
                status="connected",
            )
        )
    with as_principal(viewer(org_id)):
        body = (await client.get(HUB, params=RANGE)).json()
    channels = {c["key"]: c for c in body["channels"]}
    assert channels["whatsapp"] == {
        "key": "whatsapp",
        "connected": True,
        "detail": "+91 99999 00001",
    }
    assert channels["email"]["connected"] is False
    assert channels["sms"]["connected"] is False
    assert channels["push"]["connected"] is False
    assert [c["key"] for c in body["channels"]] == ["whatsapp", "email", "sms", "push"]
    assert body["costs"]["estimated"] is True
    assert {c["channel"] for c in body["costs"]["by_channel"]} == {"whatsapp", "email"}


# --------------------------------------------------------------------- rate limiting


async def test_the_overview_routes_are_rate_limited_per_organization(client, org_id) -> None:
    from app.api.m1_analytics import OVERVIEW_LIMIT_PER_MINUTE

    statuses: list[int] = []
    with as_principal(viewer(org_id)):
        # A fixed one-minute window can roll over mid-test, so allow two windows' worth of calls
        # before giving up; the loop stops at the first refusal.
        for _ in range(2 * OVERVIEW_LIMIT_PER_MINUTE + 2):
            response = await client.get(WA, params=RANGE)
            statuses.append(response.status_code)
            if response.status_code == 429:
                break
    assert statuses[-1] == 429, statuses
    assert set(statuses[:-1]) == {200}
    assert response.headers["retry-after"] == "60"

    # Another organization's budget is its own.
    with as_principal(viewer(uuid.uuid4())):
        assert (await client.get(WA, params=RANGE)).status_code == 200
