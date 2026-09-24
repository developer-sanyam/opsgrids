"""`POST /engage/campaigns/{id}/test` — a real copy of a campaign, rendered exactly as recipients
would receive it, sent nowhere near the actual audience.

Real PostgreSQL as the app role, like the rest of this service's integration suite. The transport
is injected in every case: a test that opened a socket would be testing whoever owns that mailbox,
would fail on a train, and would send real mail to a real person every time the suite ran.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, services
from app.config import settings
from app.main import app
from app.transports import RecordingTransport, TransportRefused, TransportUnavailable
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.errors import (
    BusinessRuleProblem,
    NotFoundProblem,
    RateLimitedProblem,
)
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration

CAMPAIGNS = "/api/v1/engage/campaigns"
K_WRITE = "engage.campaign.campaign.write"
K_SEND = "engage.campaign.campaign.send"
ABSENT = uuid.UUID(int=0)


def _ctx(org_id: uuid.UUID, permission: str) -> ScopeContext:
    return ctx_for(org_id, permission, Scope.ORG)


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def caller(org_id: uuid.UUID, perms: list[str]) -> Principal:
    return principal_for(org_id, perms, entitlements=["engage"])


async def _seed_campaign(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    domain_status: str = "verified",
    sender_usable: bool = True,
    connected: bool = True,
) -> uuid.UUID:
    """A domain, a sender and a connection, written directly — the gates each of those passes
    through are `test_engage_identity`'s to prove; here they are preconditions for the campaign
    gate alone."""
    domain = models.EmailDomain(organization_id=org_id, domain="company.com", status=domain_status)
    async with session.begin():
        session.add(domain)
        await session.flush()
        sender = models.Sender(
            organization_id=org_id,
            domain_id=domain.id,
            name="News",
            address="news@company.com",
            verified_at=datetime.now(UTC) if sender_usable else None,
        )
        session.add(sender)
        if connected:
            session.add(
                models.EmailConnection(
                    organization_id=org_id,
                    kind="google",
                    host="smtp.gmail.com",
                    username="news@company.com",
                    status="connected",
                )
            )
        await session.flush()
        campaign = models.Campaign(
            organization_id=org_id,
            sender_id=sender.id,
            name="Autumn",
            subject="News",
            html_body="<p>Hello</p>",
            text_body="Hello",
            audience="all_sendable",
            status="draft",
        )
        session.add(campaign)
        await session.flush()
    return campaign.id


async def test_a_test_send_renders_the_prefixed_subject_and_a_real_looking_unsubscribe_link(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    campaign_id = await _seed_campaign(session, org_id)
    wire = RecordingTransport()
    async with session.begin():
        await services.send_campaign_test(
            session,
            _ctx(org_id, K_SEND),
            campaign_id=campaign_id,
            to_address="ops@company.com",
            transport=wire,
        )

    assert len(wire.sent) == 1
    sent = wire.sent[0]
    assert sent.subject == "[Test] News"
    assert sent.to_address == "ops@company.com"
    assert "unsubscribe" in sent.body.lower()
    assert sent.headers is not None
    assert "List-Unsubscribe" in sent.headers


async def test_a_test_send_does_not_require_a_verified_domain(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    """Unlike `request_send`'s gate: a test never reaches a real recipient, so it proves the
    content and the connection, not the domain's DNS."""
    campaign_id = await _seed_campaign(session, org_id, domain_status="pending")
    wire = RecordingTransport()
    async with session.begin():
        await services.send_campaign_test(
            session,
            _ctx(org_id, K_SEND),
            campaign_id=campaign_id,
            to_address="ops@company.com",
            transport=wire,
        )
    assert len(wire.sent) == 1


async def test_a_test_send_requires_a_usable_sender(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    campaign_id = await _seed_campaign(session, org_id, sender_usable=False)
    with pytest.raises(BusinessRuleProblem, match="has not passed a test send"):
        async with session.begin():
            await services.send_campaign_test(
                session,
                _ctx(org_id, K_SEND),
                campaign_id=campaign_id,
                to_address="ops@company.com",
                transport=RecordingTransport(),
            )


async def test_a_test_send_requires_a_connected_provider(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    campaign_id = await _seed_campaign(session, org_id, connected=False)
    with pytest.raises(BusinessRuleProblem, match="Connect an email provider"):
        async with session.begin():
            await services.send_campaign_test(
                session,
                _ctx(org_id, K_SEND),
                campaign_id=campaign_id,
                to_address="ops@company.com",
                transport=RecordingTransport(),
            )


async def test_an_unknown_campaign_is_not_found(session: AsyncSession, org_id: uuid.UUID) -> None:
    with pytest.raises(NotFoundProblem):
        async with session.begin():
            await services.send_campaign_test(
                session,
                _ctx(org_id, K_SEND),
                campaign_id=ABSENT,
                to_address="ops@company.com",
                transport=RecordingTransport(),
            )


async def test_a_refused_test_send_carries_no_provider_words(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    campaign_id = await _seed_campaign(session, org_id)
    wire = RecordingTransport(
        fail_with=TransportRefused("535 5.7.8 Username and Password not accepted")
    )
    with pytest.raises(BusinessRuleProblem) as refusal:
        async with session.begin():
            await services.send_campaign_test(
                session,
                _ctx(org_id, K_SEND),
                campaign_id=campaign_id,
                to_address="ops@company.com",
                transport=wire,
            )
    assert "535" not in str(refusal.value)


async def test_an_unavailable_provider_is_not_the_customers_mistake(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    campaign_id = await _seed_campaign(session, org_id)
    wire = RecordingTransport(fail_with=TransportUnavailable("[Errno 60] timed out"))
    from platform_core.errors import UpstreamUnavailableProblem

    with pytest.raises(UpstreamUnavailableProblem) as outage:
        async with session.begin():
            await services.send_campaign_test(
                session,
                _ctx(org_id, K_SEND),
                campaign_id=campaign_id,
                to_address="ops@company.com",
                transport=wire,
            )
    assert "Errno" not in str(outage.value)


async def test_a_test_send_never_writes_a_campaign_recipient_row(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    """A real-looking unsubscribe link naming nobody real — proven at the row level: nothing in
    `campaign_recipients` is created by this route."""
    from sqlalchemy import func, select

    campaign_id = await _seed_campaign(session, org_id)
    async with session.begin():
        await services.send_campaign_test(
            session,
            _ctx(org_id, K_SEND),
            campaign_id=campaign_id,
            to_address="ops@company.com",
            transport=RecordingTransport(),
        )
    async with session.begin():
        count = (
            await session.execute(
                select(func.count())
                .select_from(models.CampaignRecipient)
                .where(models.CampaignRecipient.campaign_id == campaign_id)
            )
        ).scalar_one()
    assert count == 0


class TestTheCampaignTestSendRateLimit:
    async def test_a_burst_is_refused_past_the_limit(
        self, session: AsyncSession, org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = settings()
        monkeypatch.setattr(cfg, "engage_test_send_rate_limit_per_minute", 1)
        campaign_id = await _seed_campaign(session, org_id)

        async with session.begin():
            await services.send_campaign_test(
                session,
                _ctx(org_id, K_SEND),
                campaign_id=campaign_id,
                to_address="ops@company.com",
                transport=RecordingTransport(),
            )

        with pytest.raises(RateLimitedProblem):
            async with session.begin():
                await services.send_campaign_test(
                    session,
                    _ctx(org_id, K_SEND),
                    campaign_id=campaign_id,
                    to_address="ops2@company.com",
                    transport=RecordingTransport(),
                )

    async def test_the_limit_is_keyed_per_organization_and_route(
        self, session: AsyncSession, org_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A distinct key from the sender test-send route's own — otherwise pressing "send test"
        on a campaign would spend a budget a sender test already has a claim on."""
        seen_keys: list[str] = []
        original = services.enforce_rate_limit

        async def _spy(key: str, **kwargs: Any) -> None:
            seen_keys.append(key)
            await original(key, **kwargs)

        monkeypatch.setattr(services, "enforce_rate_limit", _spy)
        campaign_id = await _seed_campaign(session, org_id)

        async with session.begin():
            await services.send_campaign_test(
                session,
                _ctx(org_id, K_SEND),
                campaign_id=campaign_id,
                to_address="ops@company.com",
                transport=RecordingTransport(),
            )

        assert seen_keys == [f"engage.campaign_test_send:{org_id}"]


# --------------------------------------------------------------------- the HTTP surface


async def test_the_route_requires_the_send_permission_not_just_write(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
) -> None:
    """Drafting must not be enough to preview a send: a drafter without send rights should not
    be able to mail themselves a copy under a fixed name."""
    campaign_id = await _seed_campaign(session, org_id)
    with as_principal(caller(org_id, [f"{K_WRITE}:ORG"])):
        response = await client.post(
            f"{CAMPAIGNS}/{campaign_id}/test", json={"to_address": "ops@company.com"}
        )
    assert response.status_code == 403


async def test_the_route_refuses_a_caller_with_no_permission_at_all(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    with as_principal(caller(org_id, [])):
        response = await client.post(
            f"{CAMPAIGNS}/{ABSENT}/test", json={"to_address": "ops@company.com"}
        )
    assert response.status_code == 403


async def test_another_organizations_campaign_is_not_found(
    client: AsyncClient, session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID
) -> None:
    other = uuid.uuid4()
    domain = models.EmailDomain(
        organization_id=other, domain="theirs.example.com", status="verified"
    )
    async with admin_session.begin():
        admin_session.add(domain)
        await admin_session.flush()
        sender = models.Sender(
            organization_id=other,
            domain_id=domain.id,
            name="Theirs",
            address="news@theirs.example.com",
            verified_at=datetime.now(UTC),
        )
        admin_session.add(sender)
        await admin_session.flush()
        campaign = models.Campaign(
            organization_id=other,
            sender_id=sender.id,
            name="Theirs",
            subject="Theirs",
            html_body="",
            text_body="Hello",
            audience="all_sendable",
            status="draft",
        )
        admin_session.add(campaign)
        await admin_session.flush()
        campaign_id = campaign.id

    with as_principal(caller(org_id, [f"{K_SEND}:ORG"])):
        response = await client.post(
            f"{CAMPAIGNS}/{campaign_id}/test", json={"to_address": "ops@company.com"}
        )
    assert response.status_code == 404
