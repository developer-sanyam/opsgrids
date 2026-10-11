"""The storefront enquiry form's WhatsApp opt-in, recorded on the consent ledger (ENGAGE-02).

`record_storefront_opt_in` runs on `storefront.enquiry.whatsapp_opted_in`, which carries the
enquiry id only. It fetches the buyer's number from storefront (a fake here), links it to the
Engage contact for the buyer's email — creating one, with **no** email attestation, if there is
none — and appends one `opted_in` whatsapp/marketing event with source `storefront_enquiry`.
Exactly once, however often it is delivered; nothing at all when storefront cannot answer.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select

from app import consent, consumers, services
from app.config import settings
from app.models import ConsentEvent, Contact
from app.storefront_optin import StorefrontOptIn, StorefrontUnavailable
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from tests.helpers import ctx_for, envelope_for

pytestmark = pytest.mark.integration

EVENT = "storefront.enquiry.whatsapp_opted_in"
PHONE = "+91 98765 43210"
EMAIL = "priya@shahtraders.example"
WHEN = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)


@pytest_asyncio.fixture
async def worker_engine(session) -> AsyncIterator[None]:
    init_engine(settings())
    yield
    await dispose_engine()


def _envelope(org_id: uuid.UUID, enquiry_id: uuid.UUID, **kw: object) -> dict:
    envelope = envelope_for(org_id, EVENT, {"enquiry_id": str(enquiry_id)}, **kw)
    envelope["occurred_at"] = WHEN.isoformat()
    return envelope


def _fetch(details: StorefrontOptIn | None, calls: list | None = None):
    async def fetch(organization_id: str, enquiry_id: str) -> StorefrontOptIn | None:
        if calls is not None:
            calls.append((organization_id, enquiry_id))
        return details

    return fetch


TICKED = StorefrontOptIn(name="Priya Shah", email=EMAIL, phone=PHONE, opted_in_at=WHEN)


async def _ledger(session, org_id: uuid.UUID) -> list[ConsentEvent]:
    async with session.begin():
        session.expire_all()
        return list(
            (
                await session.execute(
                    select(ConsentEvent)
                    .where(ConsentEvent.organization_id == org_id)
                    .order_by(ConsentEvent.created_at)
                )
            ).scalars()
        )


async def _contacts(session, org_id: uuid.UUID) -> list[Contact]:
    async with session.begin():
        session.expire_all()
        return list(
            (
                await session.execute(select(Contact).where(Contact.organization_id == org_id))
            ).scalars()
        )


async def test_a_ticked_enquiry_opts_the_buyer_in_and_reaches_a_contact(
    session, org_id, worker_engine
) -> None:
    enquiry_id = uuid.uuid4()
    await consumers.record_storefront_opt_in(_envelope(org_id, enquiry_id), _fetch(TICKED))

    [contact] = await _contacts(session, org_id)
    assert contact.name == "Priya Shah"
    contact_id = contact.id
    [event] = await _ledger(session, org_id)
    assert (event.channel, event.purpose, event.status, event.source) == (
        "whatsapp",
        "marketing",
        "opted_in",
        consent.SOURCE_STOREFRONT_ENQUIRY,
    )
    assert event.contact_id == contact_id
    assert event.occurred_at == WHEN
    assert str(enquiry_id) in (event.evidence or "")
    assert "98765" not in (event.evidence or "")
    async with session.begin():
        assert (await consent.decide(session, org_id, "whatsapp", "marketing", PHONE)).allowed
        # Creating the contact attests nothing about email: the buyer only agreed to WhatsApp.
        assert not (await consent.decide(session, org_id, "email", "marketing", EMAIL)).allowed


async def test_an_existing_contact_is_linked_not_duplicated(session, org_id, worker_engine) -> None:
    async with session.begin():
        existing = await services.create_contact(
            session, ctx_for(org_id, "engage.audience.contact.write", Scope.ORG), email=EMAIL
        )
    existing_id = existing.id

    await consumers.record_storefront_opt_in(_envelope(org_id, uuid.uuid4()), _fetch(TICKED))

    assert [c.id for c in await _contacts(session, org_id)] == [existing_id]
    whatsapp = [e for e in await _ledger(session, org_id) if e.channel == "whatsapp"]
    assert [e.contact_id for e in whatsapp] == [existing_id]


async def test_a_redelivered_event_records_once(session, org_id, worker_engine) -> None:
    envelope = _envelope(org_id, uuid.uuid4())
    calls: list = []
    await consumers.record_storefront_opt_in(envelope, _fetch(TICKED, calls))
    await consumers.record_storefront_opt_in(envelope, _fetch(TICKED, calls))

    assert len(await _ledger(session, org_id)) == 1
    assert len(calls) == 1


async def test_storefront_unavailable_writes_nothing_and_raises_for_a_retry(
    session, org_id, worker_engine
) -> None:
    async def down(organization_id: str, enquiry_id: str) -> StorefrontOptIn | None:
        raise StorefrontUnavailable("storefront returned 503")

    with pytest.raises(StorefrontUnavailable):
        await consumers.record_storefront_opt_in(_envelope(org_id, uuid.uuid4()), down)

    assert await _ledger(session, org_id) == []
    assert await _contacts(session, org_id) == []


async def test_an_enquiry_storefront_does_not_know_records_nothing(
    session, org_id, worker_engine
) -> None:
    await consumers.record_storefront_opt_in(_envelope(org_id, uuid.uuid4()), _fetch(None))

    assert await _ledger(session, org_id) == []


async def test_the_fetch_is_asked_for_the_envelopes_organization_only(
    session, org_id, worker_engine
) -> None:
    calls: list = []
    enquiry_id = uuid.uuid4()
    await consumers.record_storefront_opt_in(_envelope(org_id, enquiry_id), _fetch(TICKED, calls))

    assert calls == [(str(org_id), str(enquiry_id))]
