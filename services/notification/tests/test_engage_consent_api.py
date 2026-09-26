"""The consent ledger's HTTP surface — record an event, read a subject's history, open one address.

Real PostgreSQL as the app role, like the rest of the integration suite. Authorization is proven
as negative assertions (a caller missing the permission, the entitlement or the step-up is turned
away), masking is proven by reading the response body rather than trusting the assembler, and
isolation is proven by seeding a second organization and asking for it.

**No new permission codes.** Reading and recording ride `engage.audience.contact.read`/`.write`;
unmasking rides `engage.inbox.message.unmask` with its OTP step-up.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import consent, models, services
from app.main import app
from app.models import AuditLog, ConsentEvent
from platform_core.auth import Principal, Scope, get_principal
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration

BASE = "/api/v1/engage/consent/events"
LOOKUP = f"{BASE}:lookup"

C_READ = "engage.audience.contact.read"
C_WRITE = "engage.audience.contact.write"
M_UNMASK = "engage.inbox.message.unmask"
BOTH = [f"{C_READ}:ORG", f"{C_WRITE}:ORG"]

ADDRESS = "very.identifiable.person@example.com"
PHONE = "+919812345678"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def caller(
    org_id: uuid.UUID,
    perms: list[str],
    *,
    amr: list[str] | None = None,
    entitlements: list[str] | None = None,
) -> Principal:
    return principal_for(
        org_id,
        perms,
        entitlements=entitlements if entitlements is not None else ["engage"],
        amr=amr,
    )


def body(**over: object) -> dict:
    payload: dict = {
        "channel": "email",
        "purpose": "marketing",
        "status": "opted_in",
        "source": "web_form",
        "evidence": "signup form submission 88",
        "address": ADDRESS,
    }
    payload.update(over)
    return {k: v for k, v in payload.items() if v is not None}


async def _events(session, org_id: uuid.UUID) -> list[ConsentEvent]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(ConsentEvent)
                    .where(ConsentEvent.organization_id == org_id)
                    .order_by(ConsentEvent.created_at, ConsentEvent.id)
                )
            ).scalars()
        )


async def _seed_event(
    session,
    org_id: uuid.UUID,
    *,
    channel: str = "email",
    purpose: str = "marketing",
    status: str = "opted_in",
    address: str = ADDRESS,
    source: str = "web_form",
    when: datetime | None = None,
) -> ConsentEvent:
    subject = consent.subject_for(channel, address)
    async with session.begin():
        rows = await consent.append(
            session,
            organization_id=org_id,
            subject=subject,
            purposes=(purpose,),
            status=status,
            source=source,
            evidence="seeded",
            occurred_at=when,
        )
    return rows[0]


# --------------------------------------------------------------------- record


async def test_recording_an_event_returns_it_masked_with_the_effective_decision(
    client, session, org_id
) -> None:
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=body())
    assert response.status_code == 201, response.text
    payload = response.json()
    assert ADDRESS not in response.text
    assert "very.identifiable" not in response.text
    event = payload["event"]
    assert event["address"] != ADDRESS
    assert (event["channel"], event["purpose"], event["status"], event["source"]) == (
        "email",
        "marketing",
        "opted_in",
        "web_form",
    )
    assert event["evidence"] == "signup form submission 88"
    assert event["recorded_by"] is not None
    assert payload["effective"] == {"allowed": True, "reason": "opted_in"}

    rows = await _events(session, org_id)
    assert len(rows) == 1
    assert rows[0].organization_id == org_id
    assert rows[0].subject_masked == event["address"]


async def test_recording_writes_an_audit_row_that_carries_the_mask_only(
    client, session, org_id
) -> None:
    with as_principal(caller(org_id, BOTH)):
        assert (await client.post(BASE, json=body())).status_code == 201
    async with session.begin():
        audits = list(
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "engage.consent.recorded")
                )
            ).scalars()
        )
    assert len(audits) == 1
    assert ADDRESS not in str(audits[0].after)
    assert audits[0].entity == "consent_event"


@pytest.mark.parametrize("missing", ["source", "evidence", "channel", "purpose", "status"])
async def test_a_required_field_is_required(client, org_id, missing) -> None:
    payload = body()
    del payload[missing]
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "override",
    [
        {"evidence": ""},
        {"evidence": "ok"},
        {"evidence": "x" * 501},
        {"source": "inbound_keyword"},
        {"source": "unsubscribe_link"},
        {"source": "legacy_import"},
        {"source": "bounce"},
        {"source": "made_up"},
        {"channel": "pigeon"},
        {"purpose": "spam"},
        {"status": "maybe"},
        {"address": None, "contact_id": None},
        {"idempotency_key": "short"},
        {"occurred_at": "2026-09-01T10:00:00"},  # naive: no timezone
        {"surprise": "field"},
    ],
)
async def test_the_body_is_validated_and_unknown_fields_are_refused(
    client, session, org_id, override
) -> None:
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=body(**override))
    assert response.status_code == 422, response.text
    assert await _events(session, org_id) == []


async def test_an_address_that_is_not_one_for_the_channel_is_refused(client, org_id) -> None:
    with as_principal(caller(org_id, BOTH)):
        not_an_email = await client.post(BASE, json=body(address="nope"))
        no_country_code = await client.post(BASE, json=body(channel="whatsapp", address="98123"))
    assert not_an_email.status_code == 422
    assert no_country_code.status_code == 422


async def test_an_event_dated_in_the_future_is_refused(client, session, org_id) -> None:
    """A future-dated event would be "latest" until the date arrived, outranking any real refusal
    recorded in the meantime."""
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=body(occurred_at=future))
    assert response.status_code == 422
    assert await _events(session, org_id) == []


async def test_an_event_may_be_dated_in_the_past(client, session, org_id) -> None:
    earlier = datetime.now(UTC) - timedelta(days=30)
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=body(occurred_at=earlier.isoformat()))
    assert response.status_code == 201, response.text
    rows = await _events(session, org_id)
    assert abs((rows[0].occurred_at - earlier).total_seconds()) < 1


async def test_evidence_may_not_contain_the_address(client, session, org_id) -> None:
    """The column is not sealed: a reference belongs there, never the person."""
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(
            BASE, json=body(evidence=f"confirmed by phone with {ADDRESS.upper()}")
        )
    assert response.status_code == 422
    assert await _events(session, org_id) == []


async def test_a_whatsapp_marketing_opt_in_takes_effect(client, session, org_id) -> None:
    payload = body(channel="whatsapp", address=PHONE, source="checkout", evidence="order 1042")
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=payload)
    assert response.status_code == 201, response.text
    assert PHONE not in response.text
    assert response.json()["effective"] == {"allowed": True, "reason": "opted_in"}


async def test_recording_an_opt_out_shows_it_in_effect(client, org_id) -> None:
    with as_principal(caller(org_id, BOTH)):
        await client.post(BASE, json=body())
        response = await client.post(
            BASE, json=body(status="opted_out", source="admin", evidence="asked by phone, call 12")
        )
    assert response.json()["effective"] == {"allowed": False, "reason": "opted_out"}


async def test_an_opt_in_does_not_lift_a_legacy_suppression_and_says_so(
    client, session, org_id
) -> None:
    """Recording that somebody said yes is not the same as it taking effect: nothing that was
    blocked before the ledger may be unblocked by adding a row to it."""
    from app import selectors
    from app.config import settings
    from platform_core import pii

    sealed, masked = pii.seal(ADDRESS, kind="email", settings=settings())
    async with session.begin():
        session.add(
            models.Suppression(
                organization_id=org_id,
                email_enc=sealed,
                email_masked=masked,
                email_index=selectors.email_index(ADDRESS),
                reason="unsubscribed",
            )
        )
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=body())
    assert response.status_code == 201
    assert response.json()["effective"] == {"allowed": False, "reason": "suppressed_unsubscribed"}


# --------------------------------------------------------------------- record by contact


async def _contact(session, org_id: uuid.UUID, email: str = ADDRESS) -> models.Contact:
    write = ctx_for(org_id, C_WRITE, Scope.ORG)
    async with session.begin():
        return await services.create_contact(session, write, email=email)


async def test_an_email_event_can_name_a_contact_instead_of_an_address(
    client, session, org_id
) -> None:
    contact = await _contact(session, org_id)
    contact_id = contact.id
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(
            BASE,
            json=body(address=None, contact_id=str(contact_id), status="opted_out", source="admin"),
        )
    assert response.status_code == 201, response.text
    assert response.json()["event"]["contact_id"] == str(contact_id)
    assert response.json()["effective"] == {"allowed": False, "reason": "opted_out"}
    assert ADDRESS not in response.text
    # The event is about the contact's own address: the campaign audience now excludes them.
    ctx = ctx_for(org_id, C_WRITE, Scope.ORG)
    from app import selectors

    async with session.begin():
        assert await selectors.sendable_contacts(session, ctx) == []


async def test_a_contact_and_an_address_must_agree(client, session, org_id) -> None:
    contact = await _contact(session, org_id)
    contact_id = contact.id
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(
            BASE, json=body(contact_id=str(contact_id), address="someone.else@example.com")
        )
    assert response.status_code == 422


async def test_a_contact_id_only_names_an_email_subject(client, session, org_id) -> None:
    contact = await _contact(session, org_id)
    contact_id = contact.id
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(
            BASE, json=body(channel="whatsapp", address=None, contact_id=str(contact_id))
        )
    assert response.status_code == 422


async def test_an_unknown_contact_is_not_found(client, org_id) -> None:
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=body(address=None, contact_id=str(uuid.uuid4())))
    assert response.status_code == 404


async def test_another_organizations_contact_is_not_found(
    client, session, admin_session, org_id
) -> None:
    other = uuid.uuid4()
    async with admin_session.begin():
        other_contact = await services.create_contact(
            admin_session, ctx_for(other, C_WRITE, Scope.ORG), email="theirs@example.com"
        )
    other_contact_id = other_contact.id
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(
            BASE, json=body(address=None, contact_id=str(other_contact_id))
        )
    assert response.status_code == 404
    assert await _events(session, org_id) == []


# --------------------------------------------------------------------- idempotency


async def test_a_retry_under_the_same_key_returns_the_original_event(
    client, session, org_id
) -> None:
    payload = body(idempotency_key="retry-key-0001")
    with as_principal(caller(org_id, BOTH)):
        first = await client.post(BASE, json=payload)
        second = await client.post(BASE, json=payload)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["event"]["id"] == first.json()["event"]["id"]
    assert len(await _events(session, org_id)) == 1
    async with session.begin():
        audits = list(
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "engage.consent.recorded")
                )
            ).scalars()
        )
    assert len(audits) == 1


async def test_a_key_reused_for_a_different_event_is_a_conflict(client, session, org_id) -> None:
    with as_principal(caller(org_id, BOTH)):
        first = await client.post(BASE, json=body(idempotency_key="retry-key-0002"))
        clash = await client.post(
            BASE, json=body(idempotency_key="retry-key-0002", status="opted_out", source="admin")
        )
    assert first.status_code == 201
    assert clash.status_code == 409
    assert len(await _events(session, org_id)) == 1


# --------------------------------------------------------------------- history


async def test_the_history_is_masked_newest_first_and_paged(client, session, org_id) -> None:
    now = datetime.now(UTC)
    await _seed_event(session, org_id, status="opted_in", when=now - timedelta(days=3))
    await _seed_event(session, org_id, status="opted_out", when=now - timedelta(days=2))
    await _seed_event(session, org_id, status="opted_in", when=now - timedelta(days=1))
    with as_principal(caller(org_id, [f"{C_READ}:ORG"])):
        everything = await client.get(BASE)
        page = await client.get(BASE, params={"size": 2, "page": 1})
    assert everything.status_code == 200, everything.text
    assert ADDRESS not in everything.text
    assert [e["status"] for e in everything.json()["items"]] == [
        "opted_in",
        "opted_out",
        "opted_in",
    ]
    assert everything.json()["total"] == 3
    assert len(page.json()["items"]) == 2
    assert page.json()["pages"] == 2


async def test_the_history_can_be_filtered(client, session, org_id) -> None:
    await _seed_event(session, org_id, channel="email", purpose="marketing", status="opted_in")
    await _seed_event(session, org_id, channel="email", purpose="transactional", status="opted_out")
    await _seed_event(
        session, org_id, channel="whatsapp", purpose="marketing", status="opted_in", address=PHONE
    )
    with as_principal(caller(org_id, [f"{C_READ}:ORG"])):
        by_channel = await client.get(BASE, params={"channel": "whatsapp"})
        by_purpose = await client.get(BASE, params={"purpose": "transactional"})
        by_status = await client.get(BASE, params={"status": "opted_out"})
        bad = await client.get(BASE, params={"status": "maybe"})
    assert [e["channel"] for e in by_channel.json()["items"]] == ["whatsapp"]
    assert [e["purpose"] for e in by_purpose.json()["items"]] == ["transactional"]
    assert [e["status"] for e in by_status.json()["items"]] == ["opted_out"]
    assert bad.status_code == 422


async def test_a_contacts_history_covers_events_recorded_before_and_beside_the_contact(
    client, session, org_id
) -> None:
    """The subject is the address, so a contact's history is every email event for it — including
    one an operator recorded by address and one the unsubscribe link wrote."""
    await _seed_event(session, org_id, address="theirs@example.com", status="opted_out")
    contact = await _contact(session, org_id)
    contact_id = contact.id
    await _seed_event(session, org_id, address=ADDRESS, status="opted_out", source="admin")
    with as_principal(caller(org_id, [f"{C_READ}:ORG"])):
        response = await client.get(BASE, params={"contact_id": str(contact_id)})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    # The attestation written when the contact was added, and the operator's later opt-out —
    # and nothing about the other address.
    assert [(e["status"], e["source"]) for e in items] == [
        ("opted_out", "admin"),
        ("opted_in", "admin"),
    ]


async def test_an_unknown_contact_history_is_not_found(client, org_id) -> None:
    with as_principal(caller(org_id, [f"{C_READ}:ORG"])):
        response = await client.get(BASE, params={"contact_id": str(uuid.uuid4())})
    assert response.status_code == 404


async def test_lookup_by_address_returns_only_that_subjects_events(client, session, org_id) -> None:
    await _seed_event(session, org_id, address=ADDRESS, status="opted_in")
    await _seed_event(session, org_id, address=ADDRESS, status="opted_out", source="admin")
    await _seed_event(session, org_id, address="other@example.com", status="opted_in")
    await _seed_event(
        session, org_id, channel="whatsapp", address=PHONE, purpose="marketing", status="opted_in"
    )
    with as_principal(caller(org_id, [f"{C_READ}:ORG"])):
        response = await client.post(
            LOOKUP, json={"channel": "email", "address": f"  {ADDRESS.upper()} "}
        )
        other_channel = await client.post(LOOKUP, json={"channel": "sms", "address": PHONE})
        invalid = await client.post(LOOKUP, json={"channel": "email", "address": "nope"})
    assert response.status_code == 200, response.text
    assert ADDRESS not in response.text
    assert [e["status"] for e in response.json()["items"]] == ["opted_out", "opted_in"]
    assert other_channel.json()["items"] == []
    assert invalid.status_code == 422


# --------------------------------------------------------------------- unmask


async def test_unmask_opens_the_address_only_with_the_permission_and_the_step_up(
    client, session, org_id
) -> None:
    event = await _seed_event(session, org_id)
    event_id = event.id
    path = f"{BASE}/{event_id}:unmask"

    with as_principal(caller(org_id, [f"{M_UNMASK}:ORG"])):
        no_step_up = await client.post(path)
    assert no_step_up.status_code == 403

    with as_principal(caller(org_id, BOTH, amr=["otp"])):
        no_permission = await client.post(path)
    assert no_permission.status_code == 403

    with as_principal(caller(org_id, [f"{M_UNMASK}:ORG"], amr=["otp"])):
        revealed = await client.post(path)
    assert revealed.status_code == 200, revealed.text
    assert revealed.json() == {"event_id": str(event_id), "address": ADDRESS}


async def test_unmasking_writes_an_audit_row_every_time(client, session, org_id) -> None:
    event = await _seed_event(session, org_id)
    event_id = event.id
    with as_principal(caller(org_id, [f"{M_UNMASK}:ORG"], amr=["otp"])):
        await client.post(f"{BASE}/{event_id}:unmask")
        await client.post(f"{BASE}/{event_id}:unmask")
    async with session.begin():
        rows = list(
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "entity.sensitive_read")
                )
            ).scalars()
        )
    assert len(rows) == 2
    assert {r.entity for r in rows} == {"consent_event"}
    assert all(ADDRESS not in str(r.after) for r in rows)


async def test_a_refused_unmask_writes_no_audit_row_and_reveals_nothing(
    client, session, org_id
) -> None:
    event = await _seed_event(session, org_id)
    event_id = event.id
    with as_principal(caller(org_id, [f"{M_UNMASK}:ORG"])):
        refused = await client.post(f"{BASE}/{event_id}:unmask")
    assert refused.status_code == 403
    assert ADDRESS not in refused.text
    async with session.begin():
        rows = list(
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "entity.sensitive_read")
                )
            ).scalars()
        )
    assert rows == []


async def test_unmasking_an_unknown_event_is_not_found(client, org_id) -> None:
    with as_principal(caller(org_id, [f"{M_UNMASK}:ORG"], amr=["otp"])):
        response = await client.post(f"{BASE}/{uuid.uuid4()}:unmask")
    assert response.status_code == 404


# --------------------------------------------------------------------- negative authorization

ROUTES = [
    ("POST", BASE, "record"),
    ("GET", BASE, "list"),
    ("POST", LOOKUP, "lookup"),
    ("POST", f"{BASE}/{uuid.uuid4()}:unmask", "unmask"),
]


async def _call(client, method: str, path: str, kind: str):
    if kind == "record":
        return await client.post(path, json=body())
    if kind == "lookup":
        return await client.post(path, json={"channel": "email", "address": ADDRESS})
    return await client.request(method, path)


@pytest.mark.parametrize(("method", "path", "kind"), ROUTES)
async def test_every_route_refuses_a_caller_with_no_permission(
    client, org_id, method, path, kind
) -> None:
    with as_principal(caller(org_id, [], amr=["otp"])):
        response = await _call(client, method, path, kind)
    assert response.status_code == 403


@pytest.mark.parametrize(("method", "path", "kind"), ROUTES)
async def test_every_route_refuses_a_caller_without_the_engage_entitlement(
    client, org_id, method, path, kind
) -> None:
    """402, not 403: the permissions are fine, the subscription is not. `notification` is a
    different product and must not stand in for `engage`."""
    perms = [f"{C_READ}:ORG", f"{C_WRITE}:ORG", f"{M_UNMASK}:ORG"]
    for entitlements in ([], ["notification"]):
        with as_principal(caller(org_id, perms, amr=["otp"], entitlements=entitlements)):
            response = await _call(client, method, path, kind)
        assert response.status_code == 402


async def test_reading_the_history_does_not_grant_recording_and_vice_versa(client, org_id) -> None:
    with as_principal(caller(org_id, [f"{C_READ}:ORG"])):
        assert (await client.post(BASE, json=body())).status_code == 403
    with as_principal(caller(org_id, [f"{C_WRITE}:ORG"])):
        assert (await client.get(BASE)).status_code == 403
        assert (
            await client.post(LOOKUP, json={"channel": "email", "address": ADDRESS})
        ).status_code == 403


async def test_a_suppression_or_inbox_grant_is_not_a_consent_grant(client, org_id) -> None:
    other = [
        "engage.audience.suppression.read:ORG",
        "engage.audience.suppression.write:ORG",
        "engage.inbox.conversation.read:ORG",
        "engage.inbox.message.reply:ORG",
    ]
    with as_principal(caller(org_id, other, amr=["otp"])):
        assert (await client.get(BASE)).status_code == 403
        assert (await client.post(BASE, json=body())).status_code == 403
        assert (await client.post(f"{BASE}/{uuid.uuid4()}:unmask")).status_code == 403


@pytest.mark.parametrize(("method", "path", "kind"), ROUTES)
async def test_a_grant_narrower_than_the_organization_is_refused(
    client, org_id, method, path, kind
) -> None:
    """Every Engage permission is floored at ORG: a consent history is the organization's, never
    one person's own, so an OWN grant is refused rather than silently narrowed to nothing."""
    perms = [f"{C_READ}:OWN", f"{C_WRITE}:OWN", f"{M_UNMASK}:OWN"]
    with as_principal(caller(org_id, perms, amr=["otp"])):
        response = await _call(client, method, path, kind)
    assert response.status_code == 403


# --------------------------------------------------------------------- cross-tenant


async def test_another_organizations_events_are_invisible_on_every_read(
    client, session, admin_session, org_id
) -> None:
    other = uuid.uuid4()
    other_event = await _seed_event(admin_session, other, address=ADDRESS, source="checkout")
    other_event_id = other_event.id

    with as_principal(caller(org_id, [f"{C_READ}:ORG", f"{M_UNMASK}:ORG"], amr=["otp"])):
        listed = await client.get(BASE)
        looked_up = await client.post(LOOKUP, json={"channel": "email", "address": ADDRESS})
        unmasked = await client.post(f"{BASE}/{other_event_id}:unmask")
    assert listed.json()["items"] == []
    assert listed.json()["total"] == 0
    assert looked_up.json()["items"] == []
    assert unmasked.status_code == 404
    assert ADDRESS not in listed.text + looked_up.text + unmasked.text

    async with admin_session.begin():
        audits = list(
            (
                await admin_session.execute(
                    select(AuditLog).where(AuditLog.action == "entity.sensitive_read")
                )
            ).scalars()
        )
    assert audits == []


async def test_recording_in_one_organization_does_not_touch_anothers_ledger_or_decisions(
    client, session, admin_session, org_id
) -> None:
    other = uuid.uuid4()
    with as_principal(caller(org_id, BOTH)):
        assert (await client.post(BASE, json=body())).status_code == 201

    async with admin_session.begin():
        theirs = list(
            (
                await admin_session.execute(
                    select(ConsentEvent).where(ConsentEvent.organization_id == other)
                )
            ).scalars()
        )
        decision = await consent.decide(admin_session, other, "email", "marketing", ADDRESS)
    assert theirs == []
    assert decision == consent.ConsentDecision(False, "no_opt_in")


async def test_the_body_cannot_name_an_organization(client, session, org_id) -> None:
    """Tenancy comes from the caller's token, never the request body."""
    with as_principal(caller(org_id, BOTH)):
        response = await client.post(BASE, json=body(organization_id=str(uuid.uuid4())))
    assert response.status_code == 422
    assert await _events(session, org_id) == []
