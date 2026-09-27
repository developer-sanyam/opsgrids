"""Lead email and phone are sealed, masked and blind-indexed — not stored plain (ADR-0017 /
CLAUDE.md's "PII is encrypted and masked by default"). These tests are the ones that would
fail if a future edit accidentally started storing the raw value somewhere it shouldn't.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import func, select

from app import clients, services
from app.config import settings
from app.main import app
from app.models import AuditLog, Lead, OutboxEvent
from platform_core.auth import Principal, Scope, get_principal
from platform_core.errors import ConflictProblem
from platform_core.pii import PiiError, open_sealed
from tests.helpers import ctx_for, principal_for

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


class TestSealing:
    async def test_the_raw_email_and_phone_are_never_stored_plain(self, session, org_id) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            lead = await services.create_lead(
                session,
                write,
                full_name="Test Lead",
                company=None,
                email="test.lead@example.com",
                phone="+15551234567",
                source="manual",
                owner_id=None,
                notes="",
            )

        assert lead.email_enc is not None
        assert lead.phone_enc is not None
        assert b"test.lead@example.com" not in lead.email_enc
        assert b"+15551234567" not in lead.phone_enc
        assert lead.email_masked != "test.lead@example.com"
        assert lead.phone_masked != "+15551234567"
        assert "•" in lead.email_masked
        assert "•" in lead.phone_masked

    async def test_the_sealed_value_opens_back_to_the_original(self, session, org_id) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            lead = await services.create_lead(
                session,
                write,
                full_name="Test Lead",
                company=None,
                email="round.trip@example.com",
                phone="+15559876543",
                source="manual",
                owner_id=None,
                notes="",
            )

        assert open_sealed(lead.email_enc, settings=settings()) == "round.trip@example.com"
        assert open_sealed(lead.phone_enc, settings=settings()) == "+15559876543"

    async def test_a_lead_with_no_contact_details_seals_nothing(self, session, org_id) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            lead = await services.create_lead(
                session,
                write,
                full_name="No Details",
                company=None,
                email=None,
                phone=None,
                source="manual",
                owner_id=None,
                notes="",
            )
        assert lead.email_enc is None
        assert lead.phone_enc is None
        assert lead.email_masked is None
        assert lead.phone_masked is None


class TestDedup:
    async def test_a_repeat_email_in_the_same_organization_refuses(self, session, org_id) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            await services.create_lead(
                session,
                write,
                full_name="First",
                company=None,
                email="dup@example.com",
                phone=None,
                source="form",
                owner_id=None,
                notes="",
            )

        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.create_lead(
                    session,
                    write,
                    full_name="Second",
                    company=None,
                    email="dup@example.com",
                    phone=None,
                    source="form",
                    owner_id=None,
                    notes="",
                )

    async def test_the_dedup_check_reads_no_sensitive_read_audit_row(self, session, org_id) -> None:
        """Matching on the blind index decrypts nothing, so a lookup that finds a match must
        not itself be an audited PII read."""
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            await services.create_lead(
                session,
                write,
                full_name="First",
                company=None,
                email="quiet@example.com",
                phone=None,
                source="form",
                owner_id=None,
                notes="",
            )
        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.create_lead(
                    session,
                    write,
                    full_name="Second",
                    company=None,
                    email="quiet@example.com",
                    phone=None,
                    source="form",
                    owner_id=None,
                    notes="",
                )

        sensitive = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "entity.sensitive_read")
            )
        ).scalar_one()
        assert sensitive == 0


# --------------------------------------------------------------------- revealing (the audited path)
#
# `POST /leads/{id}:reveal` is the only route that opens a lead's sealed phone or email. What is
# refused, and to whom, is `test_authz.py::TestRevealAuthorization`; these are the other half —
# what a permitted reveal returns, and what it must leave behind (and must not).

REVEAL = "crm.lead.lead.read_sensitive"
RAW_PHONE = "+919876543210"
RAW_EMAIL = "priya@stellar.example"


def _reveal_path(lead_id: uuid.UUID) -> str:
    return f"/api/v1/leads/{lead_id}:reveal"


def _stepped_up(org_id: uuid.UUID, *, scope: str = "ORG") -> Principal:
    return principal_for(org_id, [f"{REVEAL}:{scope}"], subject_ref=None, amr=["otp"])


async def _sensitive_reads(admin_session, org_id: uuid.UUID) -> list[AuditLog]:
    """The **committed** `entity.sensitive_read` rows, oldest first. Read on a separate BYPASSRLS
    connection, so a row the request only staged and never committed is not in the result."""
    async with admin_session.begin():
        rows = (
            (
                await admin_session.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "entity.sensitive_read",
                    )
                    .order_by(AuditLog.at, AuditLog.id)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


async def _outbox_count(admin_session) -> int:
    async with admin_session.begin():
        return (
            await admin_session.execute(select(func.count()).select_from(OutboxEvent))
        ).scalar_one()


class TestReveal:
    async def test_the_phone_reveals_to_exactly_what_was_sealed(self, client, org_id, data) -> None:
        with as_principal(_stepped_up(org_id)):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
        assert response.status_code == 200
        assert response.json() == {
            "lead_id": str(data["lead_a_id"]),
            "field": "phone",
            "value": RAW_PHONE,
        }

    async def test_the_email_reveals_to_exactly_what_was_sealed(self, client, org_id, data) -> None:
        with as_principal(_stepped_up(org_id)):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "email"})
        assert response.status_code == 200
        assert response.json() == {
            "lead_id": str(data["lead_a_id"]),
            "field": "email",
            "value": RAW_EMAIL,
        }

    async def test_the_response_is_never_cached(self, client, org_id, data) -> None:
        with as_principal(_stepped_up(org_id)):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"

    async def test_the_ordinary_reads_still_carry_only_the_mask(self, client, org_id, data) -> None:
        """Revealing one lead's phone changes nothing about how it is shown everywhere else."""
        with as_principal(_stepped_up(org_id)):
            await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
        with as_principal(principal_for(org_id, ["crm.lead.lead.read:ORG"])):
            response = await client.get(f"/api/v1/leads/{data['lead_a_id']}")
        assert RAW_PHONE not in response.text
        assert RAW_EMAIL not in response.text
        assert "•" in response.json()["phone"]

    async def test_every_reveal_writes_exactly_one_committed_audit_row_naming_the_field(
        self, client, org_id, data, admin_session
    ) -> None:
        assert await _sensitive_reads(admin_session, org_id) == []

        principal = _stepped_up(org_id)
        with as_principal(principal):
            for position, field in enumerate(("phone", "phone", "email"), start=1):
                # A repeat is a second read, not a no-op.
                response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": field})
                assert response.status_code == 200
                # Committed before the response came back: a separate connection already sees
                # exactly one more row than the reveals that came before this one.
                committed = await _sensitive_reads(admin_session, org_id)
                assert len(committed) == position
                assert committed[-1].after == {"fields": [field]}

        rows = await _sensitive_reads(admin_session, org_id)
        assert [row.after for row in rows] == [
            {"fields": ["phone"]},
            {"fields": ["phone"]},
            {"fields": ["email"]},
        ]
        for row in rows:
            assert row.entity == "lead"
            assert row.entity_id == data["lead_a_id"]
            assert row.actor_id == principal.sub
            assert row.organization_id == org_id
            assert row.before is None
            assert row.request_id  # ties the row to the request, and so to a log line

    async def test_no_audit_row_and_no_event_carries_the_value(
        self, client, org_id, data, admin_session
    ) -> None:
        """The load-bearing one. Sealing a value is pointless if a table any reporting query can
        reach holds it in plaintext — so after revealing both fields, look at *everything* this
        service wrote."""
        with as_principal(_stepped_up(org_id)):
            for field in ("phone", "email"):
                response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": field})
                assert response.status_code == 200

        async with admin_session.begin():
            audit = (await admin_session.execute(select(AuditLog))).scalars().all()
            outbox = (await admin_session.execute(select(OutboxEvent))).scalars().all()
        assert audit
        for row in audit:
            blob = json.dumps([row.before, row.after, row.reason, row.action], default=str)
            assert RAW_PHONE not in blob
            assert RAW_PHONE.removeprefix("+") not in blob
            assert RAW_EMAIL not in blob
        for event in outbox:
            blob = json.dumps(event.payload, default=str)
            assert RAW_PHONE.removeprefix("+") not in blob
            assert RAW_EMAIL not in blob

    async def test_a_reveal_emits_no_event(self, client, org_id, data, admin_session) -> None:
        before = await _outbox_count(admin_session)
        with as_principal(_stepped_up(org_id)):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
        assert response.status_code == 200
        assert await _outbox_count(admin_session) == before

    async def test_a_lead_with_nothing_on_file_is_not_found_and_leaves_no_row(
        self, client, org_id, data, admin_session
    ) -> None:
        """`lead_b` was captured with neither a phone nor an email."""
        with as_principal(_stepped_up(org_id)):
            phone = await client.post(_reveal_path(data["lead_b_id"]), json={"field": "phone"})
            email = await client.post(_reveal_path(data["lead_b_id"]), json={"field": "email"})
        assert phone.status_code == 404
        assert phone.json()["detail"] == "No phone number is on file for this lead"
        assert email.status_code == 404
        assert email.json()["detail"] == "No email address is on file for this lead"
        # Nothing was disclosed, so nothing is recorded as disclosed.
        assert await _sensitive_reads(admin_session, org_id) == []

    async def test_the_not_found_says_nothing_about_the_other_field(
        self, client, session, org_id
    ) -> None:
        """A lead with an email and no phone: asking for the phone learns only that there is no
        phone — not that there is an email."""
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        async with session.begin():
            lead = await services.create_lead(
                session,
                write,
                full_name="Email Only",
                company=None,
                email="only.email@example.com",
                phone=None,
                source="manual",
                owner_id=None,
                notes="",
            )
        lead_id = lead.id

        with as_principal(_stepped_up(org_id)):
            phone = await client.post(_reveal_path(lead_id), json={"field": "phone"})
            email = await client.post(_reveal_path(lead_id), json={"field": "email"})

        assert phone.status_code == 404
        assert "email" not in phone.text.lower()
        assert "only.email@example.com" not in phone.text
        assert email.status_code == 200
        assert email.json()["value"] == "only.email@example.com"

    async def test_a_converted_lead_still_reveals_its_own_copy(
        self, client, session, org_id, data, admin_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Conversion seals a *second* copy on customer-service's contact; the lead keeps its
        own, so the reveal keeps working (the UI may prefer the contact, but is not forced to)."""

        async def _company(settings, organization_id, **kwargs):  # type: ignore[no-untyped-def]
            return uuid.uuid4(), uuid.uuid4()

        async def _deal(settings, organization_id, **kwargs):  # type: ignore[no-untyped-def]
            return uuid.uuid4()

        monkeypatch.setattr(clients, "ensure_company_and_contact", _company)
        monkeypatch.setattr(clients, "ensure_deal", _deal)
        convert = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        await services.convert_lead(session, convert, data["lead_a_id"])
        before = await _sensitive_reads(admin_session, org_id)  # the conversion's own read

        with as_principal(_stepped_up(org_id)):
            response = await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})

        assert response.status_code == 200
        assert response.json()["value"] == RAW_PHONE
        after = await _sensitive_reads(admin_session, org_id)
        assert len(after) == len(before) + 1
        assert after[-1].after == {"fields": ["phone"]}

    async def test_an_envelope_that_will_not_open_reveals_nothing_and_records_nothing(
        self, client, session, org_id, data, admin_session
    ) -> None:
        """A sealed value damaged, or sealed under a key that has since been retired, is a
        server fault: the value never leaves, and there is no audit row for a read that did not
        happen. (It surfaces as a bare 500 to a real client; the test client re-raises it.)"""
        async with admin_session.begin():
            await admin_session.execute(
                Lead.__table__.update()
                .where(Lead.id == data["lead_a_id"])
                .values(phone_enc=b"\x01" + b"\x00" * 40)
            )
        # The test client shares this session with the fixture that seeded the lead, so its
        # identity map still holds the row as it was sealed. A real request has a session of
        # its own and reads what the table holds now.
        session.expunge_all()

        with as_principal(_stepped_up(org_id)), pytest.raises(PiiError):
            await client.post(_reveal_path(data["lead_a_id"]), json={"field": "phone"})
        assert await _sensitive_reads(admin_session, org_id) == []
