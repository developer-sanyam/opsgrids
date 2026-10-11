"""Every `engage.*` event, built through the real function that publishes it, validated against
the JSON Schema committed for it under `libs/contracts/events/`.

This is the deeper half of CI's "Contracts — events validate against their schemas" job: the
static half — every schema is valid JSON Schema, and one exists for every `engage.*` event
notification declares — is `libs/contracts/tests/test_event_schemas.py`, which needs no database
and runs in that job directly. Checking a real *payload* against its schema needs a transaction
to build one through, so that check lives here instead and runs as part of this service's
ordinary sharded test run. A schema drifting from the payload the code actually emits is a bug
either way; the payload here comes from the same `services.py` functions the routes call — never
a hand-written dict that could quietly diverge from them.

Real PostgreSQL as the app role, like the rest of this service's integration suite: staging an
event is a database write (`platform_core.events.publish_event` adds an outbox row), and reading
the payload back the way a consumer would is what proves the schema describes what actually
lands on the wire, not what a docstring claims it does.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app import models, services
from app.transports import RecordingTransport
from platform_core.auth import Scope
from platform_core.events import validate_event_schema
from tests.helpers import OPTED_IN, ctx_for
from tests.test_engage_identity import FakeResolver

pytestmark = pytest.mark.integration

#: `services.py`, `tests/`, `notification`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"


def _ctx(org_id: uuid.UUID, permission: str) -> Any:
    return ctx_for(org_id, permission, Scope.ORG)


async def _last_payload(session: Any, event_type: str) -> dict[str, Any]:
    stmt = (
        select(models.OutboxEvent)
        .where(models.OutboxEvent.event_type == event_type)
        .order_by(models.OutboxEvent.created_at.desc())
    )
    async with session.begin():
        row = (await session.execute(stmt)).scalars().first()
        assert row is not None, f"no {event_type} event was staged"
        return dict(row.payload)


async def _validate(session: Any, event_type: str, *, version: int = 1) -> None:
    payload = await _last_payload(session, event_type)
    validate_event_schema(event_type, version, payload, contracts_root=CONTRACTS_ROOT)


# --------------------------------------------------------------------- setup: the connection


async def test_connection_created_and_replaced_match_their_schemas(session, org_id) -> None:
    async with session.begin():
        await services.create_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="google",
            username="info@company.com",
            secret="app-password",  # pragma: allowlist secret
        )
    await _validate(session, services.EVT_CONNECTION_CREATED)

    async with session.begin():
        await services.replace_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="smtp",
            username="ops@company.com",
            secret="rotated",  # pragma: allowlist secret
            host="mail.company.com",
            port=587,
            resolver=FakeResolver({("mail.company.com", "A"): ["8.8.8.8"]}),
        )
    await _validate(session, services.EVT_CONNECTION_REPLACED)


# --------------------------------------------------------------------- setup: whatsapp


async def test_whatsapp_created_and_replaced_match_their_schemas(
    session, org_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This test is about the event payload, not about proving a token against Meta's Graph API
    # (`test_whatsapp_provider.py` owns that), the webhook-subscribe call that follows (`w1_graph`'s
    # own tests own that), or the cross-tenant uniqueness check (which needs the directory role's
    # own DSN, not the tenant session this test already has bound).
    async def _pass(*args: object, **kwargs: object) -> None:
        return None

    async def _subscribed(*args: object, **kwargs: object) -> bool:
        return True

    monkeypatch.setattr(services.whatsapp_provider, "verify_ownership", _pass)
    monkeypatch.setattr(services.w1_services, "subscribe_waba", _subscribed)

    async with session.begin():
        await services.create_whatsapp_connection(
            session,
            _ctx(org_id, "engage.setup.whatsapp.write"),
            directory=session,
            waba_id="waba-1",
            phone_number_id="phone-1",
            display_number="+15551234567",
            access_token="token-1",  # pragma: allowlist secret
        )
    await _validate(session, services.EVT_WHATSAPP_CONNECTION_CREATED)

    async with session.begin():
        await services.replace_whatsapp_connection(
            session,
            _ctx(org_id, "engage.setup.whatsapp.write"),
            directory=session,
            waba_id="waba-1",
            phone_number_id="phone-2",
            display_number="+15557654321",
            access_token="token-2",  # pragma: allowlist secret
        )
    await _validate(session, services.EVT_WHATSAPP_CONNECTION_REPLACED)


# --------------------------------------------------------------------- setup: domains & senders


#: What a Google Workspace connection needs published to verify — the same shape
#: `test_engage_identity.py` uses, kept local so this file's fixtures do not depend on another
#: test module's private helpers.
_GOOGLE_PUBLISHED = {
    ("company.com", "TXT"): ["v=spf1 include:_spf.google.com ~all"],
    ("google._domainkey.company.com", "TXT"): ["v=DKIM1; k=rsa; p=AAA"],
}


async def test_domain_created_and_verified_match_their_schemas(session, org_id) -> None:
    ctx = _ctx(org_id, "engage.setup.domain.write")
    async with session.begin():
        await services.create_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="google",
            username="info@company.com",
            secret="app-password",  # pragma: allowlist secret
        )
        row = await services.create_domain(session, ctx, domain="company.com")
    await _validate(session, services.EVT_DOMAIN_CREATED)

    async with session.begin():
        await services.verify_domain(
            session, ctx, domain_id=row.id, resolver=FakeResolver(_GOOGLE_PUBLISHED)
        )
    await _validate(session, services.EVT_DOMAIN_VERIFIED)


async def test_sender_created_and_verified_match_their_schemas(session, org_id) -> None:
    domain_ctx = _ctx(org_id, "engage.setup.domain.write")
    async with session.begin():
        await services.create_connection(
            session,
            _ctx(org_id, "engage.setup.connection.write"),
            kind="google",
            username="info@company.com",
            secret="app-password",  # pragma: allowlist secret
        )
        domain = await services.create_domain(session, domain_ctx, domain="company.com")
    async with session.begin():
        await services.verify_domain(
            session, domain_ctx, domain_id=domain.id, resolver=FakeResolver(_GOOGLE_PUBLISHED)
        )

    async with session.begin():
        sender = await services.create_sender(
            session,
            _ctx(org_id, "engage.setup.sender.write"),
            domain_id=domain.id,
            name="Sales",
            address="sales@company.com",
        )
    await _validate(session, services.EVT_SENDER_CREATED)

    wire = RecordingTransport()
    async with session.begin():
        await services.send_test(
            session,
            _ctx(org_id, "engage.setup.sender.write"),
            sender_id=sender.id,
            to_address="ops@company.com",
            transport=wire,
        )
    await _validate(session, services.EVT_SENDER_VERIFIED)


# --------------------------------------------------------------------- campaigns


async def test_campaign_send_requested_and_cancelled_match_their_schemas(session, org_id) -> None:
    domain = models.EmailDomain(organization_id=org_id, domain="company.com", status="verified")
    async with session.begin():
        session.add(domain)
        await session.flush()
        sender = models.Sender(
            organization_id=org_id,
            domain_id=domain.id,
            name="News",
            address="news@company.com",
            verified_at=datetime.now(UTC),
        )
        session.add(sender)
        session.add(
            models.EmailConnection(
                organization_id=org_id,
                kind="google",
                host="smtp.gmail.com",
                username="news@company.com",
                daily_cap=2000,
            )
        )
    sender_id = sender.id

    write = _ctx(org_id, "engage.audience.contact.write")
    async with session.begin():
        await services.create_contact(session, write, email="a@example.com", **OPTED_IN)

    campaign_write = _ctx(org_id, "engage.campaign.campaign.write")
    async with session.begin():
        campaign = await services.create_campaign(
            session,
            campaign_write,
            fields={
                "name": "Autumn",
                "sender_id": sender_id,
                "subject": "News",
                "html_body": "<p>Hello</p>",
                "text_body": "Hello",
                "audience": "all_sendable",
                "list_id": None,
                "scheduled_at": None,
            },
        )
    campaign_id = campaign.id

    async with session.begin():
        await services.request_send(
            session, _ctx(org_id, "engage.campaign.campaign.send"), campaign_id=campaign_id
        )
    await _validate(session, services.EVT_CAMPAIGN_SEND_REQUESTED)

    async with session.begin():
        await services.cancel_campaign(session, campaign_write, campaign_id=campaign_id)
    await _validate(session, services.EVT_CAMPAIGN_CANCELLED)
