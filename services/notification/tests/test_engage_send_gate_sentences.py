"""The sentences the campaign send gate refuses in, pinned against what the UI keys on.

`POST /engage/campaigns/{id}/send` answers `422` with **one** unmet precondition in a sentence
written to be shown, and nothing else: the problem document carries no machine code. The
Engage app (`frontend/product-engage/src/lib/sendGate.ts`) therefore chooses the next step it
offers — "Open Senders", "Add a subject" — by matching a lower-cased substring of that prose.

That is a contract, and until this file it was an unwritten one: the frontend test copies the
sentences, so it cannot notice the service rewording one, and the service tests assert only a
word or two (`"test send" in detail`). A reword would silently drop a shortcut in production and
nothing would go red. This file makes the coupling explicit in both directions:

- every gate `request_send` and `_check_public_base` can raise is driven through the real route,
  and both the exact sentence and the substring the UI matches on are pinned;
- the substrings the UI matches on are read out of `sendGate.ts` and must equal the ones pinned
  here, so a key added on one side and not the other fails too.

The long-term fix is not a better match: it is a stable `code` on the `422` problem document that
the UI switches on and the prose stops being load-bearing. Until that lands, this is the guard.

**These tests truncate the tenant tables** (every test in this service does, through the `session`
fixture) and rewrite rows directly, so they must only ever run against an isolated database. The
guard below refuses to run against `opsgrids` itself.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, update

from app import models, services
from app.config import settings
from app.main import app
from platform_core.auth import Principal, Scope, get_principal
from platform_core.testing import is_shared_dev_database
from tests.helpers import OPTED_IN, ctx_for, principal_for

pytestmark = pytest.mark.integration

CAMPAIGNS = "/api/v1/engage/campaigns"
K_WRITE = "engage.campaign.campaign.write"
K_SEND = "engage.campaign.campaign.send"
L_WRITE = "engage.audience.list.write"
C_WRITE = "engage.audience.contact.write"
ADDRESS = "news@company.com"

#: `frontend/product-engage/src/lib/sendGate.ts`, from this file: tests/ -> notification/ ->
#: services/ -> the repository root.
SEND_GATE_TS = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "product-engage"
    / "src"
    / "lib"
    / "sendGate.ts"
)


@pytest.fixture(autouse=True)
def _only_an_isolated_database() -> None:
    if is_shared_dev_database(settings().database_url):
        pytest.fail(
            "This module's fixtures truncate the tenant tables; refusing to run against the "
            "shared `opsgrids` database. Point DATABASE_URL at an isolated one."
        )


@contextlib.contextmanager
def _as(org_id: uuid.UUID) -> Iterator[None]:
    principal: Principal = principal_for(
        org_id, [f"{K_WRITE}:ORG", f"{K_SEND}:ORG"], entitlements=["engage"]
    )
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


async def _identity(
    session,
    org_id: uuid.UUID,
    *,
    tested: bool = True,
    domain_status: str = "verified",
    connected: bool = True,
) -> uuid.UUID:
    """A domain, a sender and (optionally) a connection: the preconditions the gate checks, written
    directly. The gates themselves are tested in test_engage_identity; here they are arranged to
    fail one at a time."""
    async with session.begin():
        domain = models.EmailDomain(
            organization_id=org_id, domain="company.com", status=domain_status
        )
        session.add(domain)
        await session.flush()
        sender = models.Sender(
            organization_id=org_id,
            domain_id=domain.id,
            name="News",
            address=ADDRESS,
            verified_at=datetime.now(UTC) if tested else None,
        )
        session.add(sender)
        if connected:
            session.add(
                models.EmailConnection(
                    organization_id=org_id,
                    kind="google",
                    host="smtp.gmail.com",
                    username=ADDRESS,
                    daily_cap=2000,
                )
            )
    return sender.id


async def _contact(session, org_id: uuid.UUID, email: str = "reader@example.com") -> None:
    """One sendable contact — a precondition every gate below except the audience ones needs
    satisfied so it fails on its own gate rather than on the "nobody to email" one."""
    async with session.begin():
        await services.create_contact(
            session, ctx_for(org_id, C_WRITE, Scope.ORG), email=email, **OPTED_IN
        )


async def _draft(
    session,
    org_id: uuid.UUID,
    sender_id: uuid.UUID,
    *,
    subject: str = "News",
    html: str = "<p>Hello</p>",
    text: str = "Hello",
    list_id: uuid.UUID | None = None,
) -> uuid.UUID:
    async with session.begin():
        row = await services.create_campaign(
            session,
            ctx_for(org_id, K_WRITE, Scope.ORG),
            fields={
                "name": "Autumn",
                "sender_id": sender_id,
                "subject": subject,
                "html_body": html,
                "text_body": text,
                "audience": "list" if list_id else "all_sendable",
                "list_id": list_id,
                "scheduled_at": None,
            },
        )
    return row.id


Arrange = Callable[..., Awaitable[uuid.UUID]]


async def _blank_subject(session, org_id, monkeypatch) -> uuid.UUID:
    return await _draft(session, org_id, await _identity(session, org_id), subject="   ")


async def _blank_body(session, org_id, monkeypatch) -> uuid.UUID:
    return await _draft(session, org_id, await _identity(session, org_id), html="", text="")


async def _sender_gone(session, org_id, monkeypatch) -> uuid.UUID:
    sender_id = await _identity(session, org_id)
    campaign_id = await _draft(session, org_id, sender_id)
    async with session.begin():
        await session.execute(delete(models.Sender).where(models.Sender.id == sender_id))
    return campaign_id


async def _sender_untested(session, org_id, monkeypatch) -> uuid.UUID:
    return await _draft(session, org_id, await _identity(session, org_id, tested=False))


async def _domain_lapsed(session, org_id, monkeypatch) -> uuid.UUID:
    sender_id = await _identity(session, org_id)
    campaign_id = await _draft(session, org_id, sender_id)
    async with session.begin():
        await session.execute(
            update(models.EmailDomain)
            .where(models.EmailDomain.organization_id == org_id)
            .values(status="pending")
        )
    return campaign_id


async def _no_connection(session, org_id, monkeypatch) -> uuid.UUID:
    return await _draft(session, org_id, await _identity(session, org_id, connected=False))


async def _list_gone(session, org_id, monkeypatch) -> uuid.UUID:
    sender_id = await _identity(session, org_id)
    async with session.begin():
        the_list = await services.create_list(
            session, ctx_for(org_id, L_WRITE, Scope.ORG), name="VIP", description=""
        )
    campaign_id = await _draft(session, org_id, sender_id, list_id=the_list.id)
    # A list a campaign points at cannot be deleted through the API (the foreign key and
    # `delete_list` both refuse), so the only way for it to vanish is out from under the service.
    async with session.begin():
        await session.execute(
            update(models.ContactList)
            .where(models.ContactList.id == the_list.id)
            .values(deleted_at=datetime.now(UTC))
        )
    return campaign_id


async def _no_sendable_contacts(session, org_id, monkeypatch) -> uuid.UUID:
    return await _draft(session, org_id, await _identity(session, org_id))


async def _no_sendable_contacts_in_list(session, org_id, monkeypatch) -> uuid.UUID:
    sender_id = await _identity(session, org_id)
    async with session.begin():
        the_list = await services.create_list(
            session, ctx_for(org_id, L_WRITE, Scope.ORG), name="Empty", description=""
        )
    return await _draft(session, org_id, sender_id, list_id=the_list.id)


async def _no_unsubscribe_key(session, org_id, monkeypatch) -> uuid.UUID:
    monkeypatch.setattr(services, "unsubscribe_key", lambda: None)
    sender_id = await _identity(session, org_id)
    await _contact(session, org_id)
    return await _draft(session, org_id, sender_id)


async def _public_base_unset(session, org_id, monkeypatch) -> uuid.UUID:
    sender_id = await _identity(session, org_id)
    await _contact(session, org_id)
    campaign_id = await _draft(session, org_id, sender_id)
    # A real environment with a real signing secret — so the unsubscribe-key gate passes — and the
    # default `localhost` API base, which is the misconfiguration `_check_public_base` exists for.
    misconfigured = services.settings().model_copy(
        update={
            "environment": "production",
            "engage_unsubscribe_secret": SecretStr("a-real-signing-secret-not-a-dev-one"),
            "engage_public_api_base": "http://localhost:8080/api/v1",
        }
    )
    monkeypatch.setattr(services, "settings", lambda: misconfigured)
    return campaign_id


@dataclass(frozen=True)
class Gate:
    name: str
    arrange: Arrange
    #: The whole sentence, exactly as the service says it.
    sentence: str
    #: The lower-cased substring `sendGate.ts` matches on to choose a next step.
    key: str


GATES = [
    Gate("no subject", _blank_subject, "Add a subject before sending.", "add a subject"),
    Gate("no body", _blank_body, "Add a body before sending.", "add a body"),
    Gate(
        "sender gone",
        _sender_gone,
        "The campaign's sender no longer exists. Choose another.",
        "sender no longer exists",
    ),
    Gate(
        "sender untested",
        _sender_untested,
        f"{ADDRESS} has not passed a test send. Verify the sender before sending.",
        "has not passed a test send",
    ),
    Gate(
        "domain lapsed",
        _domain_lapsed,
        f"The domain behind {ADDRESS} is no longer verified. Re-verify its DNS records before "
        "sending.",
        "no longer verified",
    ),
    Gate(
        "no connection",
        _no_connection,
        "Connect an email provider before sending a campaign.",
        "connect an email provider",
    ),
    Gate(
        "list gone",
        _list_gone,
        "The campaign's list no longer exists.",
        "list no longer exists",
    ),
    Gate(
        "no sendable contacts, all_sendable audience",
        _no_sendable_contacts,
        "You have no contacts who can be emailed.",
        "no contacts who can be emailed",
    ),
    Gate(
        "no sendable contacts, list audience",
        _no_sendable_contacts_in_list,
        "This list has no contacts who can be emailed.",
        "no contacts who can be emailed",
    ),
    Gate(
        "unsubscribe links unconfigured",
        _no_unsubscribe_key,
        "Unsubscribe links are not configured for this environment, so campaigns cannot be sent.",
        "unsubscribe links are not configured",
    ),
    Gate(
        "public base unset",
        _public_base_unset,
        "ENGAGE_PUBLIC_API_BASE is not a public https address in this environment, so campaigns "
        "cannot be sent.",
        "engage_public_api_base",
    ),
]


@pytest.mark.parametrize("gate", GATES, ids=[g.name for g in GATES])
async def test_each_gate_refuses_in_exactly_the_sentence_the_ui_matches(
    gate: Gate, client, session, org_id, monkeypatch
):
    campaign_id = await gate.arrange(session, org_id, monkeypatch)

    with _as(org_id):
        response = await client.post(f"{CAMPAIGNS}/{campaign_id}/send")

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    # The whole sentence, and — separately, so the failure says which contract broke — the
    # substring the frontend switches on.
    assert detail == gate.sentence
    assert gate.key in detail.lower()
    # A refusal is not a state change: the campaign is exactly the draft it was.
    async with session.begin():
        row = await session.get(models.Campaign, campaign_id, populate_existing=True)
        assert row is not None
        assert row.status == "draft"


def test_the_ui_matches_on_exactly_the_keys_pinned_here():
    source = SEND_GATE_TS.read_text(encoding="utf-8")
    keyed = set(re.findall(r'text\.includes\("([^"]+)"\)', source))

    # Nothing the UI keys on is unpinned, and nothing pinned here is a key the UI dropped: a
    # substring added on one side alone would be a shortcut that never fires, or a sentence
    # nothing guards.
    assert keyed == {gate.key for gate in GATES}


def test_every_key_is_lower_case_because_the_ui_lower_cases_before_matching():
    assert all(gate.key == gate.key.lower() for gate in GATES)
