"""`GET /engage/setup` — the get-started checklist a fresh organization's first screen reads.

Five steps, each folded from the same rows every other Engage screen already reads. Real
PostgreSQL as the app role, like the rest of this service's integration suite.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, selectors
from app.main import app
from platform_core.auth import Principal, Scope, get_principal
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration

SETUP = "/api/v1/engage/setup"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def caller(org_id: uuid.UUID, perms: list[str]) -> Principal:
    return principal_for(org_id, perms, entitlements=["engage"])


def _step(steps: list[dict], name: str) -> dict:
    return next(s for s in steps if s["step"] == name)


async def test_a_fresh_organization_has_everything_to_do(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    ctx = ctx_for(org_id, "engage.setup.connection.read", Scope.ORG)
    async with session.begin():
        steps = await selectors.setup_checklist(session, ctx)

    assert [s["step"] for s in steps] == ["connection", "domain", "sender", "contacts", "campaign"]
    assert _step(steps, "connection")["status"] == "todo"
    assert _step(steps, "domain")["status"] == "blocked"
    assert _step(steps, "sender")["status"] == "blocked"
    assert _step(steps, "contacts")["status"] == "todo"
    assert _step(steps, "campaign")["status"] == "blocked"


async def test_a_connected_organization_can_start_on_a_domain(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    ctx = ctx_for(org_id, "engage.setup.connection.read", Scope.ORG)
    async with session.begin():
        session.add(
            models.EmailConnection(
                organization_id=org_id, kind="google", username="info@company.com"
            )
        )
    async with session.begin():
        steps = await selectors.setup_checklist(session, ctx)

    assert _step(steps, "connection")["status"] == "done"
    assert _step(steps, "domain")["status"] == "todo"
    assert _step(steps, "sender")["status"] == "blocked"


async def test_every_step_done_once_a_campaign_has_sent(
    session: AsyncSession, org_id: uuid.UUID
) -> None:
    ctx = ctx_for(org_id, "engage.setup.connection.read", Scope.ORG)
    async with session.begin():
        session.add(
            models.EmailConnection(
                organization_id=org_id, kind="google", username="info@company.com"
            )
        )
        domain = models.EmailDomain(organization_id=org_id, domain="company.com", status="verified")
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
        contact = models.Contact(
            organization_id=org_id,
            email_enc=b"x",
            email_masked="n***@company.com",
            email_index="idx",
        )
        session.add(contact)
        await session.flush()
        session.add(
            models.Campaign(
                organization_id=org_id,
                sender_id=sender.id,
                name="Autumn",
                subject="News",
                html_body="",
                text_body="Hello",
                audience="all_sendable",
                status="sent",
            )
        )

    async with session.begin():
        steps = await selectors.setup_checklist(session, ctx)

    assert {s["step"]: s["status"] for s in steps} == {
        "connection": "done",
        "domain": "done",
        "sender": "done",
        "contacts": "done",
        "campaign": "done",
    }


async def test_the_route_returns_the_checklist(client: AsyncClient, org_id: uuid.UUID) -> None:
    with as_principal(caller(org_id, ["engage.setup.connection.read:ORG"])):
        response = await client.get(SETUP)
    assert response.status_code == 200
    body = response.json()
    assert [s["step"] for s in body["steps"]] == [
        "connection",
        "domain",
        "sender",
        "contacts",
        "campaign",
    ]


async def test_the_route_refuses_a_caller_without_the_permission(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    with as_principal(caller(org_id, [])):
        response = await client.get(SETUP)
    assert response.status_code == 403


async def test_the_notification_entitlement_does_not_unlock_the_checklist(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    with as_principal(
        principal_for(org_id, ["engage.setup.connection.read:ORG"], entitlements=["notification"])
    ):
        response = await client.get(SETUP)
    assert response.status_code == 402


async def test_another_organizations_progress_is_invisible(
    client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
) -> None:
    other = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            models.EmailConnection(
                organization_id=other, kind="google", username="theirs@example.com"
            )
        )

    with as_principal(caller(org_id, ["engage.setup.connection.read:ORG"])):
        response = await client.get(SETUP)

    assert response.status_code == 200
    steps = response.json()["steps"]
    assert _step(steps, "connection")["status"] == "todo"
