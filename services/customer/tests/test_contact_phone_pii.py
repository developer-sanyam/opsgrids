"""A contact's real phone number: sealing it, opening it, and proving it does not leak.

The subject is the platform's sealed-envelope pattern applied to the CRM, so the assertions are
mostly about *absence*: that the raw number is not in the generic read, not in an audit row, not
in a log line, and not reachable without the dangerous permission and a second factor. A test
that only proved the happy path would pass against an implementation that leaked everywhere.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select

from app import selectors, services
from app.main import app
from app.models import AuditLog, Company, Contact
from platform_core.auth import Principal, Scope, get_principal
from platform_core.errors import ValidationProblem
from platform_core.pii import BULLET
from tests.helpers import ctx_for, principal_for

RAW = "+917976603446"

READ = "crm.book.contact.read"
WRITE = "crm.book.contact.write"
SENSITIVE = "crm.book.contact.read_sensitive"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


# --------------------------------------------------------------------- sealing

pytestmark = pytest.mark.integration


async def test_setting_a_number_seals_it_and_derives_the_mask(session, org_id, data) -> None:
    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        contact = await services.set_contact_phone(session, write, data["contact_a"], RAW)

    assert contact.phone_enc is not None
    # The mask is derived, not supplied, so the pair cannot disagree.
    assert contact.phone.endswith("3446")
    assert BULLET in contact.phone
    assert RAW not in contact.phone


async def test_the_sealed_bytes_are_not_the_number(session, org_id, data) -> None:
    """The obvious failure — storing plaintext in a column named `_enc` — asserted directly."""
    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        contact = await services.set_contact_phone(session, write, data["contact_a"], RAW)

    assert contact.phone_enc is not None
    assert RAW.encode() not in contact.phone_enc
    assert b"7976603446" not in contact.phone_enc


async def test_sealing_the_same_number_twice_gives_different_ciphertext(
    session, org_id, data
) -> None:
    """A fresh nonce per seal. Otherwise equal ciphertext is an equality oracle: anyone who can
    read the column learns which contacts share a phone number without decrypting anything."""
    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        a = await services.set_contact_phone(session, write, data["contact_a"], RAW)
        first = bytes(a.phone_enc or b"")
    async with session.begin():
        b = await services.set_contact_phone(session, write, data["contact_b"], RAW)
        second = bytes(b.phone_enc or b"")

    assert first != second


async def test_a_number_that_is_not_e164_never_reaches_the_column(session, org_id, data) -> None:
    write = ctx_for(org_id, WRITE, Scope.ORG)
    with pytest.raises(ValidationProblem):
        async with session.begin():
            await services.set_contact_phone(session, write, data["contact_a"], "7976603446")

    fresh = await selectors.get_contact(session, org_id, data["contact_a_id"])
    assert fresh is not None
    assert fresh.phone_enc is None


# --------------------------------------------------------------------- opening


async def test_unmasking_round_trips_the_exact_number(session, org_id, data) -> None:
    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        contact = await services.set_contact_phone(session, write, data["contact_a"], RAW)

    reveal = ctx_for(org_id, SENSITIVE, Scope.ORG)
    async with session.begin():
        revealed = await services.unmask_contact_phone(session, reveal, contact)

    assert revealed == RAW


async def test_a_contact_with_no_sealed_number_reveals_none(session, org_id, data) -> None:
    reveal = ctx_for(org_id, SENSITIVE, Scope.ORG)
    async with session.begin():
        revealed = await services.unmask_contact_phone(session, reveal, data["contact_a"])
    assert revealed is None


async def test_unmasking_writes_a_sensitive_read_audit_row(session, org_id, data) -> None:
    """Opening sealed PII is itself an auditable read. A revealed value must never exist
    without a record that it was revealed."""
    reveal = ctx_for(org_id, SENSITIVE, Scope.ORG)
    async with session.begin():
        await services.unmask_contact_phone(session, reveal, data["contact_a"])

    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "entity.sensitive_read")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].entity == "contact"
    assert rows[0].after == {"fields": ["phone"]}


async def test_the_audit_row_is_written_even_when_nothing_was_sealed(session, org_id, data) -> None:
    """The auditable act is the attempt, not whether a value happened to be there — otherwise
    probing for which contacts have numbers would be an unaudited read."""
    reveal = ctx_for(org_id, SENSITIVE, Scope.ORG)
    async with session.begin():
        revealed = await services.unmask_contact_phone(session, reveal, data["contact_a"])

    assert revealed is None
    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "entity.sensitive_read")))
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_no_audit_row_anywhere_contains_the_number(session, org_id, data) -> None:
    """The load-bearing one. Sealing a number is pointless if the audit trail — a table any
    reporting query can reach — carries it in plaintext."""
    write = ctx_for(org_id, WRITE, Scope.ORG)
    reveal = ctx_for(org_id, SENSITIVE, Scope.ORG)
    async with session.begin():
        contact = await services.set_contact_phone(session, write, data["contact_a"], RAW)
    async with session.begin():
        await services.unmask_contact_phone(session, reveal, contact)
    async with session.begin():
        await services.resolve_messaging_phone(
            session,
            organization_id=org_id,
            contact=contact,
            channel="whatsapp",
            service="notification",
        )

    rows = (await session.execute(select(AuditLog))).scalars().all()
    assert rows
    for row in rows:
        blob = f"{row.before} {row.after} {row.reason}"
        assert RAW not in blob
        assert "7976603446" not in blob


# --------------------------------------------------------------------- the messaging path


async def test_resolving_a_messaging_target_returns_the_number(session, org_id, data) -> None:
    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        contact = await services.set_contact_phone(session, write, data["contact_a"], RAW)

    async with session.begin():
        phone = await services.resolve_messaging_phone(
            session,
            organization_id=org_id,
            contact=contact,
            channel="whatsapp",
            service="notification",
        )
    assert phone == RAW


async def test_a_service_resolution_names_the_service_in_the_audit(session, org_id, data) -> None:
    """There is no principal on a service call, so `acting_as` is the only thing that can say
    which service read the number. An audit row attributing it to nobody is not an audit row."""
    async with session.begin():
        await services.resolve_messaging_phone(
            session,
            organization_id=org_id,
            contact=data["contact_a"],
            channel="whatsapp",
            service="notification",
        )

    row = (
        (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "contact.messaging_target_resolved")
            )
        )
        .scalars()
        .one()
    )
    assert row.acting_as == "notification"
    assert row.actor_id is None
    assert row.after == {"channel": "whatsapp", "resolved": False}


# --------------------------------------------------------------------- it does not leak


async def test_the_generic_contact_read_never_carries_the_raw_number(
    client, session, org_id, data
) -> None:
    """The requirement in one assertion: a raw number must not be reachable by reading a
    contact the ordinary way."""
    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        await services.set_contact_phone(session, write, data["contact_a"], RAW)

    with as_principal(principal_for(org_id, [f"{READ}:ORG"])):
        response = await client.get(f"/api/v1/contacts/{data['contact_a_id']}")

    assert response.status_code == 200
    body = response.text
    assert RAW not in body
    assert "7976603446" not in body
    assert "phone_enc" not in body


async def test_revealing_needs_the_dangerous_permission(client, org_id, data) -> None:
    with as_principal(principal_for(org_id, [f"{READ}:ORG"], amr=["otp"])):
        response = await client.post(f"/api/v1/contacts/{data['contact_a_id']}/phone:reveal")
    assert response.status_code == 403


async def test_revealing_needs_the_second_factor(client, org_id, data) -> None:
    """The permission alone is not enough. An ordinary session that has not stepped up must
    not open a customer's phone book."""
    with as_principal(principal_for(org_id, [f"{SENSITIVE}:ORG"])):
        response = await client.post(f"/api/v1/contacts/{data['contact_a_id']}/phone:reveal")
    assert response.status_code == 403


async def test_revealing_works_with_the_permission_and_the_step_up(
    client, session, org_id, data
) -> None:
    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        await services.set_contact_phone(session, write, data["contact_a"], RAW)

    with as_principal(principal_for(org_id, [f"{SENSITIVE}:ORG"], amr=["otp"])):
        response = await client.post(f"/api/v1/contacts/{data['contact_a_id']}/phone:reveal")
    assert response.status_code == 200
    assert response.json()["phone"] == RAW


async def test_an_own_grant_reveals_only_the_contacts_the_caller_owns(
    client, session, org_id, data
) -> None:
    """`CRM_SALES_REP` holds the reveal at OWN. A rep opens the phone of a contact they own and
    is refused a colleague's — the scope is re-applied to the one contact, not only to the
    list a rep can browse at ORG."""
    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        await services.set_contact_phone(session, write, data["contact_a"], RAW)
        await services.set_contact_phone(session, write, data["contact_b"], "+919812345771")

    rep = principal_for(org_id, [f"{SENSITIVE}:OWN"], subject_ref=data["subject_a"], amr=["otp"])
    with as_principal(rep):
        own = await client.post(f"/api/v1/contacts/{data['contact_a_id']}/phone:reveal")
        colleague = await client.post(f"/api/v1/contacts/{data['contact_b_id']}/phone:reveal")

    assert own.status_code == 200
    assert own.json()["phone"] == RAW
    assert colleague.status_code == 403
    assert "919812345771" not in colleague.text

    reads = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "entity.sensitive_read")))
        .scalars()
        .all()
    )
    assert [(r.entity_id, r.after) for r in reads] == [
        (data["contact_a_id"], {"fields": ["phone"]})
    ]


async def test_an_own_grant_with_no_subject_ref_reveals_nothing(
    client, session, org_id, data
) -> None:
    """What OWN means today: `owner_id` compared with `subject_ref`, which is the membership's
    employee link (ADR-0008). A principal with no link has nothing to compare, so the reveal is
    refused even for a contact whose `owner_id` is a rep that person *is* — CRM's owner ids are
    directory ids generated in this service, and nothing gives them the value of an employee
    link. ADR-0008 Q5 moves CRM's OWN onto the membership; this test changes with it."""
    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        await services.set_contact_phone(session, write, data["contact_a"], RAW)

    unlinked = principal_for(org_id, [f"{SENSITIVE}:OWN"], subject_ref=None, amr=["otp"])
    with as_principal(unlinked):
        response = await client.post(f"/api/v1/contacts/{data['contact_a_id']}/phone:reveal")

    assert response.status_code == 403
    assert RAW not in response.text
    assert RAW.removeprefix("+") not in response.text


async def test_the_write_surface_does_not_read_the_number_back(client, org_id, data) -> None:
    """Setting a number returns the masked contact, so the write route cannot be used as a
    read-back oracle by a caller who lacks `read_sensitive`."""
    with as_principal(principal_for(org_id, [f"{WRITE}:ORG"])):
        response = await client.put(
            f"/api/v1/contacts/{data['contact_a_id']}/phone",
            json={"raw_phone": RAW},
        )
    assert response.status_code == 200
    assert RAW not in response.text
    assert response.json()["phone"].endswith("3446")


async def test_another_tenants_sealed_number_is_invisible(session, admin_session, org_id) -> None:
    """Row-level security covers the new column because the policy is per table, not per
    column — asserted rather than assumed."""
    other = uuid.uuid4()
    async with admin_session.begin():
        # `contacts.company_id` is a real foreign key inside this schema, so the other tenant
        # needs a company of its own — a fabricated id would fail the constraint rather than
        # the policy, and prove nothing about isolation.
        company = Company(
            organization_id=other,
            name="Elsewhere Ltd",
            domain="elsewhere.example.com",
            industry="Other",
            size_band="1-10",
            country="IN",
            owner_id=uuid.uuid4(),
            status="customer",
        )
        admin_session.add(company)
        await admin_session.flush()
        admin_session.add(
            Contact(
                organization_id=other,
                full_name="Someone Else",
                email="someone@elsewhere.example.com",
                phone="•••••• 9999",
                phone_enc=b"\x01not-a-real-envelope",
                company_id=company.id,
                owner_id=uuid.uuid4(),
                status="customer",
            )
        )

    rows = (await session.execute(select(Contact))).scalars().all()
    assert all(r.organization_id == org_id for r in rows)


# --------------------------------------------------------------------- finding by number
#
# The inbound half: an arriving WhatsApp message carries a number, and the contact who sent it
# has to be findable or every conversation creates a duplicate customer. The sealed column
# cannot answer that — a fresh nonce per seal means there is no equality to match on — so the
# blind index does, and these assert it matches without decrypting and without leaking.


async def test_a_contact_is_findable_by_number(session, org_id, data) -> None:
    from app.config import settings
    from platform_core.pii import blind_index

    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        await services.set_contact_phone(session, write, data["contact_a"], RAW)

    index = blind_index(RAW, kind="phone", settings=settings())
    found = await selectors.get_contact_by_phone_index(session, org_id, index)
    assert found is not None
    assert found.id == data["contact_a_id"]


async def test_the_index_is_written_beside_the_sealed_value(session, org_id, data) -> None:
    """Same statement, so the two cannot disagree — an index pointing at a number the row no
    longer holds would match the wrong contact."""
    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        contact = await services.set_contact_phone(session, write, data["contact_a"], RAW)
    assert contact.phone_index is not None
    assert contact.phone_enc is not None


async def test_the_stored_index_is_not_the_number(session, org_id, data) -> None:
    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        contact = await services.set_contact_phone(session, write, data["contact_a"], RAW)
    assert contact.phone_index is not None
    assert "7976603446" not in contact.phone_index
    assert RAW not in contact.phone_index


async def test_a_number_nobody_holds_finds_nothing(session, org_id, data) -> None:
    """A stranger messaging the business. Ordinary, not an error."""
    from app.config import settings
    from platform_core.pii import blind_index

    index = blind_index("+15550101234", kind="phone", settings=settings())
    assert await selectors.get_contact_by_phone_index(session, org_id, index) is None


async def test_the_lookup_cannot_cross_a_tenant(session, org_id, data) -> None:
    """An inbound message on one tenant's number must only ever match that tenant's contacts,
    or a conversation would attach to a stranger's customer record."""
    from app.config import settings
    from platform_core.pii import blind_index

    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        await services.set_contact_phone(session, write, data["contact_a"], RAW)

    index = blind_index(RAW, kind="phone", settings=settings())
    assert await selectors.get_contact_by_phone_index(session, uuid.uuid4(), index) is None


async def test_finding_a_contact_writes_no_sensitive_read(session, org_id, data) -> None:
    """The reason the index is worth having. A *search* is not a read of somebody's number, so
    matching must not produce the audit row that decrypting would — otherwise every inbound
    message would log a PII access nobody performed."""
    from app.config import settings
    from platform_core.pii import blind_index

    write = ctx_for(org_id, WRITE, Scope.ORG)
    async with session.begin():
        await services.set_contact_phone(session, write, data["contact_a"], RAW)

    # Counted rather than cleared: `audit_logs` is append-only and the app role has no DELETE
    # on it, which is the control working as intended. So the assertion is that the lookup adds
    # nothing to whatever the write already recorded.
    before = len((await session.execute(select(AuditLog))).scalars().all())

    index = blind_index(RAW, kind="phone", settings=settings())
    await selectors.get_contact_by_phone_index(session, org_id, index)

    after = (await session.execute(select(AuditLog))).scalars().all()
    assert len(after) == before
    assert not [r for r in after if r.action == "entity.sensitive_read"]
