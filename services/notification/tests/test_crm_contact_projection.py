"""CRM contacts copied into Engage's contact book (ADR-0038 D-15, amended 2026-10-16).

`project_crm_contact` runs on `crm.book.contact.created|updated|deleted` (ids only), reads the
state from customer (a fake here), and upserts the Engage copy. What matters: once per event, one
row per CRM contact, **no consent written ever**, a copy is read-only in Engage (409
`managed_in_crm`), a phone-only contact reaches the WhatsApp audience only after a real opt-in,
and a deleted CRM contact takes its copy and its campaign snapshots with it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app import consent, consumers, crm_contacts, selectors, services
from app.config import Settings, settings
from app.crm_contacts import CrmContact, CrmUnavailable
from app.models import (
    AuditLog,
    Campaign,
    CampaignRecipient,
    ConsentEvent,
    Contact,
    ProcessedEvent,
)
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from platform_core.errors import ConflictProblem
from tests.helpers import OPTED_IN, ctx_for, envelope_for

pytestmark = pytest.mark.integration

WRITE = "engage.audience.contact.write"
READ = "engage.audience.contact.read"
LIST_WRITE = "engage.audience.list.write"
CREATED = "crm.book.contact.created"
UPDATED = "crm.book.contact.updated"
DELETED = "crm.book.contact.deleted"
PHONE = "+917976603446"
EMAIL = "rajesh.khanna@northwind.example"
_REAL_CLIENT = httpx.AsyncClient


@pytest_asyncio.fixture
async def worker_engine(session) -> AsyncIterator[None]:
    init_engine(settings())
    yield
    await dispose_engine()


def _crm(crm_id: uuid.UUID, **kw: object) -> CrmContact:
    base: dict[str, object] = {
        "contact_id": crm_id,
        "company_id": uuid.uuid4(),
        "full_name": "Rajesh Khanna",
        "email": EMAIL,
        "phone": PHONE,
        "status": "customer",
        "deleted_at": None,
    }
    return CrmContact(**{**base, **kw})  # type: ignore[arg-type]


def _fetch(state: CrmContact | None, calls: list | None = None):
    async def fetch(organization_id: str, crm_contact_id: str) -> CrmContact | None:
        if calls is not None:
            calls.append((organization_id, crm_contact_id))
        return state

    return fetch


def _unreachable():
    async def fetch(organization_id: str, crm_contact_id: str) -> CrmContact | None:
        raise CrmUnavailable("down")

    return fetch


def _env(org_id: uuid.UUID, crm_id: uuid.UUID, event_type: str = CREATED, **kw: object) -> dict:
    return envelope_for(
        org_id,
        event_type,
        {"organization_id": str(org_id), "contact_id": str(crm_id)},
        **kw,  # type: ignore[arg-type]
    )


async def _rows(session, org_id: uuid.UUID) -> list[Contact]:
    async with session.begin():
        session.expire_all()
        return list(
            (
                await session.execute(select(Contact).where(Contact.organization_id == org_id))
            ).scalars()
        )


async def _count(session, model, org_id: uuid.UUID) -> int:
    async with session.begin():
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(model).where(model.organization_id == org_id)
                )
            ).scalar_one()
        )


async def _audience(session, org_id: uuid.UUID, channel: str) -> int:
    async with session.begin():
        return await selectors.count_audience(
            session, ctx_for(org_id, READ, Scope.ORG), channel=channel
        )


async def test_a_crm_contact_is_copied_with_its_status_and_no_consent(
    session, org_id, worker_engine
) -> None:
    crm_id = uuid.uuid4()
    state = _crm(crm_id)
    event = uuid.uuid4()
    outcome = await consumers.project_crm_contact(
        _env(org_id, crm_id, event_id=event), _fetch(state)
    )

    assert outcome == "projected"
    [row] = await _rows(session, org_id)
    assert (row.origin, row.crm_contact_id, row.name) == ("crm", crm_id, "Rajesh Khanna")
    assert row.attributes == {"crm_status": "customer", "crm_company_id": str(state.company_id)}
    assert row.email_masked != EMAIL and row.phone_masked and PHONE not in row.phone_masked
    assert row.email_index == selectors.email_index(EMAIL)
    assert row.phone_index == consent.index_for("whatsapp", PHONE)
    assert row.email_enc is not None and EMAIL.encode() not in row.email_enc
    assert row.phone_enc is not None and b"7976603446" not in row.phone_enc
    # Projected, never opted in: nothing on the ledger, nobody mailable or reachable on WhatsApp.
    assert await _count(session, ConsentEvent, org_id) == 0
    assert await _audience(session, org_id, "email") == 0
    assert await _audience(session, org_id, "whatsapp") == 0
    async with session.begin():
        assert await session.get(ProcessedEvent, uuid.uuid5(event, "crm-contact-projection"))
        # The raw event id stays free for the rule engine's own fold.
        assert await session.get(ProcessedEvent, event) is None


async def test_a_redelivered_event_is_a_no_op_and_an_update_edits_the_same_row(
    session, org_id, worker_engine
) -> None:
    crm_id = uuid.uuid4()
    calls: list = []
    event = uuid.uuid4()
    envelope = _env(org_id, crm_id, event_id=event)
    await consumers.project_crm_contact(envelope, _fetch(_crm(crm_id), calls))
    assert await consumers.project_crm_contact(envelope, _fetch(_crm(crm_id), calls)) == (
        "redelivered"
    )
    assert len(calls) == 1

    changed = _crm(crm_id, full_name="Rajesh K.", status="dormant")
    await consumers.project_crm_contact(_env(org_id, crm_id, UPDATED), _fetch(changed))
    [row] = await _rows(session, org_id)
    assert (row.name, row.attributes["crm_status"]) == ("Rajesh K.", "dormant")
    assert await _count(session, ConsentEvent, org_id) == 0


async def test_customer_unreachable_raises_and_marks_nothing(
    session, org_id, worker_engine
) -> None:
    crm_id = uuid.uuid4()
    event = uuid.uuid4()
    with pytest.raises(CrmUnavailable):
        await consumers.project_crm_contact(_env(org_id, crm_id, event_id=event), _unreachable())
    assert await _rows(session, org_id) == []
    async with session.begin():
        assert (
            await session.get(ProcessedEvent, uuid.uuid5(event, "crm-contact-projection")) is None
        )
    # The redelivery then succeeds.
    await consumers.project_crm_contact(_env(org_id, crm_id, event_id=event), _fetch(_crm(crm_id)))
    assert len(await _rows(session, org_id)) == 1


async def test_an_envelope_naming_another_organization_in_its_payload_is_dropped(
    session, org_id, worker_engine
) -> None:
    crm_id = uuid.uuid4()
    envelope = envelope_for(
        org_id, CREATED, {"organization_id": str(uuid.uuid4()), "contact_id": str(crm_id)}
    )
    calls: list = []
    assert await consumers.project_crm_contact(envelope, _fetch(_crm(crm_id), calls)) == "mismatch"
    assert calls == [] and await _rows(session, org_id) == []


async def test_a_copy_is_scoped_to_its_organization(session, org_id, worker_engine) -> None:
    crm_id, other_org = uuid.uuid4(), uuid.uuid4()
    await consumers.project_crm_contact(_env(org_id, crm_id), _fetch(_crm(crm_id)))
    async with session.begin():
        assert await selectors.contact_by_crm_id(session, org_id, crm_id) is not None
        # Another organization asking for the same CRM id sees nothing (row-level security).
        assert await selectors.contact_by_crm_id(session, other_org, crm_id) is None


async def test_a_copy_cannot_be_edited_or_deleted_in_engage(session, org_id, worker_engine) -> None:
    crm_id = uuid.uuid4()
    await consumers.project_crm_contact(_env(org_id, crm_id), _fetch(_crm(crm_id)))
    [row] = await _rows(session, org_id)
    engage_id = row.id
    ctx = ctx_for(org_id, WRITE, Scope.ORG)
    for call in (
        lambda s: services.update_contact(s, ctx, contact_id=engage_id, name="Hacked"),
        lambda s: services.delete_contact(s, ctx, contact_id=engage_id),
    ):
        with pytest.raises(ConflictProblem) as caught:
            async with session.begin():
                await call(session)
        assert caught.value.errors == [{"code": "managed_in_crm"}]
    assert [r.name for r in await _rows(session, org_id)] == ["Rajesh Khanna"]


async def test_import_skips_a_copy(session, org_id, worker_engine) -> None:
    crm_id = uuid.uuid4()
    await consumers.project_crm_contact(_env(org_id, crm_id), _fetch(_crm(crm_id)))
    async with session.begin():
        [(status, _contact, error)] = await services.import_contacts(
            session, ctx_for(org_id, WRITE, Scope.ORG), rows=[(EMAIL, "Overwritten", {})]
        )
    assert (status, error) == ("invalid", "managed_in_crm")
    assert (await _rows(session, org_id))[0].name == "Rajesh Khanna"


async def test_a_phone_only_contact_reaches_whatsapp_only_after_a_real_opt_in(
    session, org_id, worker_engine
) -> None:
    crm_id = uuid.uuid4()
    await consumers.project_crm_contact(
        _env(org_id, crm_id), _fetch(_crm(crm_id, email=None, status="lead"))
    )
    [row] = await _rows(session, org_id)
    assert (row.email_enc, row.email_index, row.email_masked) == (None, None, "")
    assert row.phone_index is not None
    assert await _audience(session, org_id, "whatsapp") == 0
    assert await _audience(session, org_id, "email") == 0

    async with session.begin():
        await consent.append(
            session,
            organization_id=org_id,
            subject=consent.subject_for("whatsapp", PHONE),
            purposes=(consent.MARKETING,),
            status=consent.OPTED_IN,
            source="web_form",
            evidence="consent form 12 (test)",
        )
    assert await _audience(session, org_id, "whatsapp") == 1
    assert await _audience(session, org_id, "email") == 0
    async with session.begin():
        ctx = ctx_for(org_id, READ, Scope.ORG)
        [recipient] = await selectors.whatsapp_recipients(session, ctx)
        assert recipient.contact_id == row.id

    # A STOP from the same number wins, as for any contact.
    async with session.begin():
        await consent.append(
            session,
            organization_id=org_id,
            subject=consent.subject_for("whatsapp", PHONE),
            purposes=(consent.MARKETING,),
            status=consent.OPTED_OUT,
            source="inbound_keyword",
        )
    assert await _audience(session, org_id, "whatsapp") == 0


async def test_a_contact_that_gains_an_email_is_still_not_mailable_without_an_opt_in(
    session, org_id, worker_engine
) -> None:
    crm_id = uuid.uuid4()
    await consumers.project_crm_contact(_env(org_id, crm_id), _fetch(_crm(crm_id, email=None)))
    await consumers.project_crm_contact(_env(org_id, crm_id, UPDATED), _fetch(_crm(crm_id)))
    [row] = await _rows(session, org_id)
    assert row.email_index == selectors.email_index(EMAIL)
    assert await _audience(session, org_id, "email") == 0


async def test_an_existing_engage_contact_is_linked_and_keeps_its_opt_in(
    session, org_id, worker_engine
) -> None:
    async with session.begin():
        existing = await services.create_contact(
            session, ctx_for(org_id, WRITE, Scope.ORG), email=EMAIL, name="R Khanna", **OPTED_IN
        )
    existing_id = existing.id
    crm_id = uuid.uuid4()
    await consumers.project_crm_contact(_env(org_id, crm_id), _fetch(_crm(crm_id)))

    [row] = await _rows(session, org_id)
    assert (row.id, row.origin, row.crm_contact_id) == (existing_id, "crm", crm_id)
    # The opt-in the contact already had is the ledger's, untouched: one event, still mailable.
    assert await _count(session, ConsentEvent, org_id) == 1
    assert await _audience(session, org_id, "email") == 1


async def test_a_second_crm_contact_with_the_same_email_is_a_conflict_not_a_merge(
    session, org_id, worker_engine
) -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    await consumers.project_crm_contact(_env(org_id, first), _fetch(_crm(first)))
    outcome = await consumers.project_crm_contact(
        _env(org_id, second), _fetch(_crm(second, full_name="Someone Else", phone=None))
    )
    assert outcome == "conflict"
    [row] = await _rows(session, org_id)
    assert (row.crm_contact_id, row.name) == (first, "Rajesh Khanna")
    async with session.begin():
        audit = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "engage.contact.projection_conflict")
                )
            )
            .scalars()
            .all()
        )
    assert len(audit) == 1
    assert EMAIL not in str(audit[0].after) and "Rajesh" not in str(audit[0].after)


@pytest.mark.parametrize("gone", ["deleted_row", "absent", "no_address"])
async def test_a_deleted_crm_contact_takes_its_copy_and_campaign_snapshots(
    session, org_id, worker_engine, gone
) -> None:
    crm_id = uuid.uuid4()
    await consumers.project_crm_contact(_env(org_id, crm_id), _fetch(_crm(crm_id)))
    [row] = await _rows(session, org_id)
    engage_id = row.id
    async with session.begin():
        sealed, masked = consent.pii.seal(EMAIL, kind="email", settings=settings())
        campaign = Campaign(organization_id=org_id, name="Autumn", sender_id=uuid.uuid4())
        session.add(campaign)
        await session.flush()
        session.add(
            CampaignRecipient(
                organization_id=org_id,
                campaign_id=campaign.id,
                contact_id=engage_id,
                email_enc=sealed,
                email_masked=masked,
                email_index=selectors.email_index(EMAIL),
                status="sent",
                error="",
            )
        )
    state = {
        "deleted_row": _crm(crm_id, deleted_at=datetime.now(UTC)),
        "absent": None,
        "no_address": _crm(crm_id, email=None, phone=None),
    }[gone]
    outcome = await consumers.project_crm_contact(_env(org_id, crm_id, DELETED), _fetch(state))

    assert outcome == "removed"
    assert await _rows(session, org_id) == []
    async with session.begin():
        session.expire_all()
        [snapshot] = (await session.execute(select(CampaignRecipient))).scalars().all()
        assert snapshot.erased_at is not None and snapshot.email_index == ""
        assert EMAIL not in snapshot.email_masked
        audit = (
            (await session.execute(select(AuditLog).where(AuditLog.entity_id == engage_id)))
            .scalars()
            .all()
        )
    assert audit and all(EMAIL not in str(a.before) + str(a.after) for a in audit)
    # Deleting something already gone is a no-op.
    assert (
        await consumers.project_crm_contact(_env(org_id, crm_id, DELETED), _fetch(None)) == "absent"
    )


async def test_the_crm_status_segment_narrows_the_audience(session, org_id, worker_engine) -> None:
    customer, lead = uuid.uuid4(), uuid.uuid4()
    await consumers.project_crm_contact(_env(org_id, customer), _fetch(_crm(customer)))
    await consumers.project_crm_contact(
        _env(org_id, lead),
        _fetch(_crm(lead, email="lead@northwind.example", phone=None, status="lead")),
    )
    ctx = ctx_for(org_id, "engage.audience.list.write", Scope.ORG)
    async with session.begin():
        for row in (await session.execute(select(Contact))).scalars():
            await consent.append(
                session,
                organization_id=org_id,
                subject=consent.subject_for("email", EMAIL)
                if row.crm_contact_id == customer
                else consent.subject_for("email", "lead@northwind.example"),
                purposes=(consent.MARKETING,),
                status=consent.OPTED_IN,
                source="web_form",
                evidence="form (test)",
            )
        seg = await services.create_segment(session, ctx, name="Leads", crm_status="lead")
        segment = await selectors.get_segment(session, org_id, seg.id)
        read = ctx_for(org_id, READ, Scope.ORG)
        assert await selectors.count_audience(session, read, channel="email") == 2
        assert await selectors.count_audience(session, read, channel="email", segment=segment) == 1


class TestCustomerClient:
    """The C53 client: ids only in the URL, the organization in the token, and 'could not ask'
    never collapses into 'the contact is gone'."""

    @staticmethod
    async def _call(handler, monkeypatch, crm_id: uuid.UUID) -> CrmContact | None:
        mock = httpx.MockTransport(handler)
        original = _REAL_CLIENT

        def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
            kwargs.pop("timeout", None)
            return original(transport=mock, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(httpx, "AsyncClient", patched)
        return await crm_contacts.fetch_crm_contact(
            Settings(customer_internal_url="http://customer.invalid"),
            str(uuid.uuid4()),
            str(crm_id),
        )

    async def test_parses_the_item(self, monkeypatch) -> None:
        crm_id = uuid.uuid4()
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["ids"] = request.url.params["ids"]
            seen["token"] = request.headers["X-Internal-Token"]
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "contact_id": str(crm_id),
                            "company_id": str(uuid.uuid4()),
                            "full_name": "A",
                            "email": None,
                            "phone": PHONE,
                            "status": "lead",
                            "updated_at": "2026-10-16T00:00:00Z",
                            "deleted_at": None,
                        }
                    ]
                },
            )

        found = await self._call(handler, monkeypatch, crm_id)
        assert found is not None and found.phone == PHONE
        assert seen["path"].endswith("/contacts:export") and seen["ids"] == str(crm_id)
        assert PHONE not in seen["path"] and seen["token"]

    async def test_an_empty_answer_is_gone_and_a_5xx_is_unavailable(self, monkeypatch) -> None:
        crm_id = uuid.uuid4()
        gone = await self._call(
            lambda r: httpx.Response(200, json={"items": []}), monkeypatch, crm_id
        )
        assert gone is None
        with pytest.raises(CrmUnavailable):
            await self._call(lambda r: httpx.Response(503), monkeypatch, crm_id)
        with pytest.raises(CrmUnavailable):
            await self._call(lambda r: httpx.Response(200, text="nope"), monkeypatch, crm_id)
