"""Engage's audience: contacts, lists, membership and suppression.

Same shape as `test_engage_identity.py` — real PostgreSQL as the app role, because two things
here are database behaviour rather than Python behaviour: the row-level-security policies on
the four new tables, and `selectors.sendable_contacts`, the join a future campaign send will
read through. It is built and proven now so the suppression rule cannot be forgotten later.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select

from app import models, services, unsubscribe
from app.config import settings
from app.main import app
from platform_core.auth import Principal, Scope, ScopeContext, get_principal
from platform_core.errors import (
    ConflictProblem,
    NotFoundProblem,
    PayloadTooLargeProblem,
    ValidationProblem,
)
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration


def _ctx(org_id: uuid.UUID, permission: str) -> ScopeContext:
    return ctx_for(org_id, permission, Scope.ORG)


C_WRITE = "engage.audience.contact.write"
S_WRITE = "engage.audience.suppression.write"


# --------------------------------------------------------------------- row-level security


async def test_another_tenants_audience_is_invisible(session, admin_session, org_id) -> None:
    other = uuid.uuid4()
    async with admin_session.begin():
        their_contact = models.Contact(organization_id=other, email="them@theirs.example.com")
        admin_session.add(their_contact)
        await admin_session.flush()
        admin_session.add(models.ContactList(organization_id=other, name="Their list"))
        admin_session.add(
            models.Suppression(organization_id=other, contact_id=their_contact.id, reason="manual")
        )

    assert (await session.execute(select(models.Contact))).scalars().all() == []
    assert (await session.execute(select(models.ContactList))).scalars().all() == []
    assert (await session.execute(select(models.Suppression))).scalars().all() == []


async def test_writing_a_contact_into_another_tenant_is_refused(session) -> None:
    session.add(models.Contact(organization_id=uuid.uuid4(), email="planted@example.com"))
    with pytest.raises(Exception, match="row-level security"):
        await session.flush()
    await session.rollback()


async def test_one_contact_per_email_per_organization(session, org_id) -> None:
    from sqlalchemy.exc import IntegrityError

    session.add(models.Contact(organization_id=org_id, email="a@example.com"))
    await session.flush()
    session.add(models.Contact(organization_id=org_id, email="a@example.com"))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


# --------------------------------------------------------------------- the writers


async def test_creating_a_contact_normalizes_the_email(session, org_id) -> None:
    async with session.begin():
        contact = await services.create_contact(
            session, _ctx(org_id, C_WRITE), email="Jane@Example.com", name="Jane", attributes={}
        )
    assert contact.email == "jane@example.com"


async def test_creating_a_duplicate_contact_is_refused(session, org_id) -> None:
    async with session.begin():
        await services.create_contact(
            session, _ctx(org_id, C_WRITE), email="a@example.com", name="", attributes={}
        )
    with pytest.raises(ConflictProblem):
        async with session.begin():
            await services.create_contact(
                session, _ctx(org_id, C_WRITE), email="a@example.com", name="", attributes={}
            )


async def test_deleting_a_contact_hides_it_from_reads(session, org_id) -> None:
    async with session.begin():
        contact = await services.create_contact(
            session, _ctx(org_id, C_WRITE), email="gone@example.com", name="", attributes={}
        )
    async with session.begin():
        await services.delete_contact(session, _ctx(org_id, C_WRITE), contact_id=contact.id)

    from app import selectors

    assert await selectors.get_contact(session, org_id, contact.id) is None


async def test_bulk_import_is_idempotent_on_email(session, org_id) -> None:
    """A row whose address already exists updates instead of duplicating — the second import
    of the same file must not double the audience."""
    rows = [{"email": "A@Example.com", "name": "A", "attributes": {"k": "1"}}]
    async with session.begin():
        report_1 = await services.bulk_import_contacts(session, _ctx(org_id, C_WRITE), rows=rows)
    assert report_1[0]["status"] == "created"

    rows_again = [{"email": "a@example.com", "name": "A Updated", "attributes": {"k": "2"}}]
    async with session.begin():
        report_2 = await services.bulk_import_contacts(
            session, _ctx(org_id, C_WRITE), rows=rows_again
        )
    assert report_2[0]["status"] == "updated"

    from app import selectors

    contact = await selectors.get_contact_by_email(session, org_id, "a@example.com")
    assert contact is not None
    assert contact.name == "A Updated"
    assert contact.attributes["k"] == "2"

    all_contacts_stmt = select(models.Contact).where(models.Contact.organization_id == org_id)
    all_contacts = (await session.execute(all_contacts_stmt)).scalars().all()
    assert len(all_contacts) == 1


async def test_bulk_import_reports_a_bad_row_without_failing_the_batch(session, org_id) -> None:
    rows = [
        {"email": "good@example.com"},
        {"email": "not-an-email"},
    ]
    async with session.begin():
        report = await services.bulk_import_contacts(session, _ctx(org_id, C_WRITE), rows=rows)

    assert report[0]["status"] == "created"
    assert report[1]["status"] == "error"


async def test_bulk_import_over_the_cap_is_refused(session, org_id) -> None:
    from app import domain

    rows = [{"email": f"u{i}@example.com"} for i in range(domain.MAX_BULK_IMPORT_ROWS + 1)]
    with pytest.raises(PayloadTooLargeProblem):
        async with session.begin():
            await services.bulk_import_contacts(session, _ctx(org_id, C_WRITE), rows=rows)


async def test_list_membership_add_and_remove_are_idempotent(session, org_id) -> None:
    async with session.begin():
        contact = await services.create_contact(
            session, _ctx(org_id, C_WRITE), email="m@example.com", name="", attributes={}
        )
        contact_list = await services.create_contact_list(
            session, _ctx(org_id, C_WRITE), name="VIPs", description=""
        )

    async with session.begin():
        added = await services.add_list_members(
            session, _ctx(org_id, C_WRITE), list_id=contact_list.id, contact_ids=[contact.id]
        )
    assert added == [contact.id]

    # Adding the same contact again is a no-op, not an error.
    async with session.begin():
        added_again = await services.add_list_members(
            session, _ctx(org_id, C_WRITE), list_id=contact_list.id, contact_ids=[contact.id]
        )
    assert added_again == []

    async with session.begin():
        removed = await services.remove_list_members(
            session, _ctx(org_id, C_WRITE), list_id=contact_list.id, contact_ids=[contact.id]
        )
    assert removed == [contact.id]

    async with session.begin():
        removed_again = await services.remove_list_members(
            session, _ctx(org_id, C_WRITE), list_id=contact_list.id, contact_ids=[contact.id]
        )
    assert removed_again == []


async def test_adding_an_unknown_contact_to_a_list_is_a_404(session, org_id) -> None:
    async with session.begin():
        contact_list = await services.create_contact_list(
            session, _ctx(org_id, C_WRITE), name="Ghosts", description=""
        )
    with pytest.raises(NotFoundProblem):
        async with session.begin():
            await services.add_list_members(
                session, _ctx(org_id, C_WRITE), list_id=contact_list.id, contact_ids=[uuid.uuid4()]
            )


# --------------------------------------------------------------------- suppression


async def test_suppressing_a_contact_is_idempotent_and_keeps_the_first_reason(
    session, org_id
) -> None:
    async with session.begin():
        contact = await services.create_contact(
            session, _ctx(org_id, C_WRITE), email="s@example.com", name="", attributes={}
        )
    async with session.begin():
        first = await services.suppress_contact(
            session, _ctx(org_id, S_WRITE), contact_id=contact.id, reason="bounced"
        )
    async with session.begin():
        second = await services.suppress_contact(
            session, _ctx(org_id, S_WRITE), contact_id=contact.id, reason="manual"
        )
    assert first.id == second.id
    assert second.reason == "bounced"


async def test_an_unknown_suppression_reason_is_refused(session, org_id) -> None:
    async with session.begin():
        contact = await services.create_contact(
            session, _ctx(org_id, C_WRITE), email="r@example.com", name="", attributes={}
        )
    with pytest.raises(ValidationProblem):
        async with session.begin():
            await services.suppress_contact(
                session, _ctx(org_id, S_WRITE), contact_id=contact.id, reason="not-a-reason"
            )


async def test_sendable_contacts_excludes_suppressed_and_deleted(session, org_id) -> None:
    from app import selectors

    async with session.begin():
        sendable = await services.create_contact(
            session, _ctx(org_id, C_WRITE), email="ok@example.com", name="", attributes={}
        )
        suppressed = await services.create_contact(
            session, _ctx(org_id, C_WRITE), email="no@example.com", name="", attributes={}
        )
        deleted = await services.create_contact(
            session, _ctx(org_id, C_WRITE), email="gone@example.com", name="", attributes={}
        )
    async with session.begin():
        await services.suppress_contact(
            session, _ctx(org_id, S_WRITE), contact_id=suppressed.id, reason="manual"
        )
        await services.delete_contact(session, _ctx(org_id, C_WRITE), contact_id=deleted.id)

    stmt = selectors.sendable_contacts(_ctx(org_id, C_WRITE))
    rows = (await session.execute(stmt)).scalars().all()
    emails = {r.email for r in rows}
    assert emails == {"ok@example.com"}
    assert sendable.id in {r.id for r in rows}


async def test_sendable_contacts_can_be_scoped_to_one_list(session, org_id) -> None:
    from app import selectors

    async with session.begin():
        on_list = await services.create_contact(
            session, _ctx(org_id, C_WRITE), email="on@example.com", name="", attributes={}
        )
        off_list = await services.create_contact(
            session, _ctx(org_id, C_WRITE), email="off@example.com", name="", attributes={}
        )
        contact_list = await services.create_contact_list(
            session, _ctx(org_id, C_WRITE), name="Newsletter", description=""
        )
    async with session.begin():
        await services.add_list_members(
            session, _ctx(org_id, C_WRITE), list_id=contact_list.id, contact_ids=[on_list.id]
        )

    stmt = selectors.sendable_contacts(_ctx(org_id, C_WRITE), list_id=contact_list.id)
    rows = (await session.execute(stmt)).scalars().all()
    assert {r.email for r in rows} == {"on@example.com"}
    assert off_list.email not in {r.email for r in rows}


# --------------------------------------------------------------------- public unsubscribe


async def test_public_unsubscribe_suppresses_a_real_contact(client, session, org_id) -> None:
    async with session.begin():
        contact = await services.create_contact(
            session, _ctx(org_id, C_WRITE), email="u@example.com", name="", attributes={}
        )
    token = unsubscribe.sign(org_id, contact.id, settings=settings())

    response = await client.get("/api/v1/engage/public/unsubscribe", params={"token": token})

    assert response.status_code == 200
    assert response.json() == {"status": "unsubscribed", "already": False}

    from app import selectors

    row = await selectors.get_suppression(session, org_id, contact.id)
    assert row is not None
    assert row.reason == "unsubscribed"


async def test_public_unsubscribe_is_idempotent(client, session, org_id) -> None:
    async with session.begin():
        contact = await services.create_contact(
            session, _ctx(org_id, C_WRITE), email="twice@example.com", name="", attributes={}
        )
    token = unsubscribe.sign(org_id, contact.id, settings=settings())

    first = await client.get("/api/v1/engage/public/unsubscribe", params={"token": token})
    second = await client.get("/api/v1/engage/public/unsubscribe", params={"token": token})

    assert first.json() == {"status": "unsubscribed", "already": False}
    assert second.json() == {"status": "unsubscribed", "already": True}


async def test_public_unsubscribe_refuses_a_tampered_token(client) -> None:
    response = await client.get(
        "/api/v1/engage/public/unsubscribe", params={"token": "garbage.garbage"}
    )
    assert response.status_code == 422


async def test_public_unsubscribe_on_an_unknown_contact_still_answers_success(
    client, org_id
) -> None:
    """A token signed for a contact that never existed here (or was deleted) must not
    distinguish itself from a genuine unsubscribe — see `services.unsubscribe_public`."""
    token = unsubscribe.sign(org_id, uuid.uuid4(), settings=settings())
    response = await client.get("/api/v1/engage/public/unsubscribe", params={"token": token})
    assert response.status_code == 200
    assert response.json()["already"] is True


async def test_public_unsubscribe_binds_the_tenant_the_token_names(
    client, admin_session, org_id
) -> None:
    """There is no ambient caller organization on this route — none of `org_id`'s rows are
    touched at all. The tenant the write lands in is whichever one the token names, the same
    way the WhatsApp webhook binds the tenant `phone_number_id` resolves to rather than one
    read off a caller, because there is no caller to read one off."""
    other_org = uuid.uuid4()
    async with admin_session.begin():
        their_contact = models.Contact(organization_id=other_org, email="theirs@example.com")
        admin_session.add(their_contact)
        await admin_session.flush()
        their_contact_id = their_contact.id

    token = unsubscribe.sign(other_org, their_contact_id, settings=settings())
    response = await client.get("/api/v1/engage/public/unsubscribe", params={"token": token})
    assert response.status_code == 200

    async with admin_session.begin():
        row = (
            await admin_session.execute(
                select(models.Suppression).where(
                    models.Suppression.organization_id == other_org,
                    models.Suppression.contact_id == their_contact_id,
                )
            )
        ).scalar_one_or_none()
    assert row is not None
    assert row.reason == "unsubscribed"


# --------------------------------------------------------------------- the HTTP surface


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def engage_caller(org_id: uuid.UUID, perms: list[str]) -> Principal:
    return principal_for(org_id, perms, entitlements=["engage"])


CR = "engage.audience.contact.read"
CW = "engage.audience.contact.write"
SR = "engage.audience.suppression.read"
SW = "engage.audience.suppression.write"

CONTACTS = "/api/v1/engage/contacts"
LISTS = "/api/v1/engage/lists"
SUPPRESSIONS = "/api/v1/engage/suppressions"
ABSENT = uuid.UUID("22222222-2222-4222-8222-222222222222")

READ_ROUTES = [CONTACTS, LISTS, SUPPRESSIONS]

WRITE_ROUTES = [
    (CONTACTS, CR, {"email": "a@example.com"}),
    (f"{CONTACTS}/import", CR, {"rows": [{"email": "a@example.com"}]}),
    (LISTS, CR, {"name": "VIPs"}),
    (f"{LISTS}/{ABSENT}/members", CR, {"contact_ids": [str(ABSENT)]}),
    (SUPPRESSIONS, SR, {"contact_id": str(ABSENT), "reason": "manual"}),
]


@pytest.mark.parametrize("path", READ_ROUTES)
async def test_a_read_refuses_a_caller_without_the_permission(client, org_id, path) -> None:
    with as_principal(engage_caller(org_id, [])):
        response = await client.get(path)
    assert response.status_code == 403


@pytest.mark.parametrize(("path", "read_permission", "body"), WRITE_ROUTES)
async def test_a_write_refuses_a_caller_holding_only_the_read(
    client, org_id, path, read_permission, body
) -> None:
    with as_principal(engage_caller(org_id, [f"{read_permission}:ORG"])):
        response = await client.post(path, json=body)
    assert response.status_code == 403


@pytest.mark.parametrize("path", READ_ROUTES)
async def test_the_notification_entitlement_does_not_unlock_engage(client, org_id, path) -> None:
    """402, not 403: the permissions are fine, the subscription is not."""
    perms = [f"{CR}:ORG", f"{SR}:ORG"]
    with as_principal(principal_for(org_id, perms, entitlements=["notification"])):
        response = await client.get(path)
    assert response.status_code == 402


async def test_contact_read_does_not_grant_suppression_write(client, org_id) -> None:
    """The audience catalogue splits contact from suppression the same way the setup catalogue
    splits connection from domain — a caller who can browse the contact book must not, for free,
    be able to suppress someone."""
    with as_principal(engage_caller(org_id, [f"{CR}:ORG"])):
        response = await client.post(SUPPRESSIONS, json={"contact_id": str(ABSENT)})
    assert response.status_code == 403


async def test_deleting_a_contact_needs_the_write_permission(client, org_id) -> None:
    with as_principal(engage_caller(org_id, [f"{CR}:ORG"])):
        response = await client.delete(f"{CONTACTS}/{ABSENT}")
    assert response.status_code == 403


async def test_reading_and_writing_a_contact_end_to_end_over_http(client, org_id) -> None:
    with as_principal(engage_caller(org_id, [f"{CW}:ORG", f"{CR}:ORG"])):
        created = await client.post(CONTACTS, json={"email": "http@example.com", "name": "H"})
        assert created.status_code == 201
        contact_id = created.json()["id"]

        fetched = await client.get(f"{CONTACTS}/{contact_id}")
        assert fetched.status_code == 200
        assert fetched.json()["email"] == "http@example.com"

        listed = await client.get(CONTACTS)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1


async def test_a_narrower_scope_than_org_is_refused(client, org_id) -> None:
    """The audience is organization-wide, like the rest of Engage: OWN is not a rung here."""
    with as_principal(principal_for(org_id, [f"{CR}:OWN"], entitlements=["engage"])):
        response = await client.get(CONTACTS)
    assert response.status_code == 403
