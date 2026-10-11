"""WhatsApp marketing opt-in capture from the contact view (ENGAGE-02).

Staff record a contact's WhatsApp marketing opt-in — the number, how it was given and an
optional reference — through the existing `POST /engage/consent/events`, read the contact's
WhatsApp standing through the existing `GET`, and withdraw it without retyping a number they only
ever see masked. No new route and no new permission: the negative-authorization tests in
`test_engage_consent_api.py` already cover every route used here; this file adds the cases that
are new to the WhatsApp-by-contact shape.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest

from app import consent, selectors, services
from app.main import app
from platform_core.auth import Principal, Scope, get_principal
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration

BASE = "/api/v1/engage/consent/events"
C_READ = "engage.audience.contact.read"
C_WRITE = "engage.audience.contact.write"
BOTH = [f"{C_READ}:ORG", f"{C_WRITE}:ORG"]
PHONE = "+919812345678"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def caller(org_id: uuid.UUID, perms: list[str]) -> Principal:
    return principal_for(org_id, perms, entitlements=["engage"])


async def _contact(session, org_id: uuid.UUID, email: str = "asha@example.com"):
    async with session.begin():
        return await services.create_contact(
            session, ctx_for(org_id, C_WRITE, Scope.ORG), email=email
        )


def opt_in(contact_id: uuid.UUID, **over: object) -> dict:
    payload: dict = {
        "channel": "whatsapp",
        "purpose": "marketing",
        "status": "opted_in",
        "source": "in_person",
        "evidence": "met at the trade fair stall",
        "address": PHONE,
        "contact_id": str(contact_id),
    }
    payload.update(over)
    return {k: v for k, v in payload.items() if v is not None}


def withdraw(contact_id: uuid.UUID) -> dict:
    return {
        "channel": "whatsapp",
        "purpose": "marketing",
        "status": "opted_out",
        "source": "phone_call",
        "evidence": "asked to stop on a call",
        "contact_id": str(contact_id),
    }


# --------------------------------------------------------------------- record


@pytest.mark.parametrize("source", ["in_person", "phone_call", "web_form"])
async def test_staff_record_a_contacts_whatsapp_opt_in_with_its_number(
    client, session, org_id, source
) -> None:
    contact = await _contact(session, org_id)
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=opt_in(contact.id, source=source))
    assert response.status_code == 201, response.text
    assert PHONE not in response.text
    assert "9812345678" not in response.text
    event = response.json()["event"]
    assert event["contact_id"] == str(contact.id)
    assert (event["channel"], event["status"], event["source"]) == ("whatsapp", "opted_in", source)
    assert response.json()["effective"] == {"allowed": True, "reason": "opted_in"}
    async with session.begin():
        assert (await consent.decide(session, org_id, "whatsapp", "marketing", PHONE)).allowed


async def test_an_opt_in_naming_a_contact_still_needs_the_number(client, session, org_id) -> None:
    contact = await _contact(session, org_id)
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=opt_in(contact.id, address=None))
    assert response.status_code == 422


async def test_a_number_without_a_country_code_is_refused(client, session, org_id) -> None:
    contact = await _contact(session, org_id)
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=opt_in(contact.id, address="9812345678"))
    assert response.status_code == 422


async def test_the_storefront_source_cannot_be_claimed_by_a_caller(client, session, org_id) -> None:
    contact = await _contact(session, org_id)
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=opt_in(contact.id, source="storefront_enquiry"))
    assert response.status_code == 422


async def test_another_organizations_contact_cannot_be_linked_to_a_number(
    client, session, admin_session, org_id
) -> None:
    other = uuid.uuid4()
    async with admin_session.begin():
        theirs = await services.create_contact(
            admin_session, ctx_for(other, C_WRITE, Scope.ORG), email="theirs@example.com"
        )
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=opt_in(theirs.id))
        listed = await client.get(
            BASE, params={"contact_id": str(theirs.id), "channel": "whatsapp"}
        )
        withdrawn = await client.post(BASE, json=withdraw(theirs.id))
    assert response.status_code == listed.status_code == withdrawn.status_code == 404
    async with session.begin():
        assert not (await consent.decide(session, org_id, "whatsapp", "marketing", PHONE)).allowed


# --------------------------------------------------------------------- standing


async def test_a_contacts_whatsapp_history_includes_a_stop_that_named_no_contact(
    client, session, org_id
) -> None:
    """The contact view's standing is the newest event about the contact's number, so a STOP
    the customer sent — which the inbox records against the number, not the contact — shows."""
    contact = await _contact(session, org_id)
    with as_principal(caller(org_id, BOTH)):
        assert (await client.post(BASE, json=opt_in(contact.id))).status_code == 201
    async with session.begin():
        await consent.append(
            session,
            organization_id=org_id,
            subject=consent.subject_for("whatsapp", PHONE),
            purposes=("marketing",),
            status="opted_out",
            source=consent.SOURCE_INBOUND_KEYWORD,
        )
        # Somebody else's number, never linked to this contact.
        await consent.append(
            session,
            organization_id=org_id,
            subject=consent.subject_for("whatsapp", "+14155550100"),
            purposes=("marketing",),
            status="opted_in",
            source="admin",
        )
    with as_principal(caller(org_id, [f"{C_READ}:ORG"])):
        response = await client.get(
            BASE,
            params={"contact_id": str(contact.id), "channel": "whatsapp", "purpose": "marketing"},
        )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [(e["status"], e["source"]) for e in items] == [
        ("opted_out", "inbound_keyword"),
        ("opted_in", "in_person"),
    ]
    assert PHONE not in response.text


async def test_a_contact_with_no_whatsapp_number_has_an_empty_whatsapp_history(
    client, session, org_id
) -> None:
    contact = await _contact(session, org_id)
    with as_principal(caller(org_id, [f"{C_READ}:ORG"])):
        response = await client.get(
            BASE, params={"contact_id": str(contact.id), "channel": "whatsapp"}
        )
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_without_a_channel_a_contacts_history_is_still_their_email(
    client, session, org_id
) -> None:
    contact = await _contact(session, org_id)
    with as_principal(caller(org_id, BOTH)):
        await client.post(BASE, json=opt_in(contact.id))
        response = await client.get(BASE, params={"contact_id": str(contact.id)})
    assert [e["channel"] for e in response.json()["items"]] == ["email"]


# --------------------------------------------------------------------- withdraw


async def test_withdrawing_opts_out_the_number_on_record_without_retyping_it(
    client, session, org_id
) -> None:
    contact = await _contact(session, org_id)
    with as_principal(caller(org_id, BOTH)):
        await client.post(BASE, json=opt_in(contact.id))
        response = await client.post(BASE, json=withdraw(contact.id))
    assert response.status_code == 201, response.text
    assert response.json()["event"]["contact_id"] == str(contact.id)
    assert response.json()["effective"] == {"allowed": False, "reason": "opted_out"}
    async with session.begin():
        decision = await consent.decide(session, org_id, "whatsapp", "marketing", PHONE)
        audience = await selectors.sendable_contacts(session, ctx_for(org_id, C_READ, Scope.ORG))
    assert not decision.allowed
    # Email is a different subject and is untouched.
    assert [c.id for c in audience] == [contact.id]


async def test_withdrawing_with_no_number_on_record_is_refused(client, session, org_id) -> None:
    contact = await _contact(session, org_id)
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=withdraw(contact.id))
    assert response.status_code == 422


async def test_a_reader_cannot_record_or_withdraw(client, session, org_id) -> None:
    contact_id = (await _contact(session, org_id)).id
    with as_principal(caller(org_id, [f"{C_READ}:ORG"])):
        assert (await client.post(BASE, json=opt_in(contact_id))).status_code == 403
        assert (await client.post(BASE, json=withdraw(contact_id))).status_code == 403
