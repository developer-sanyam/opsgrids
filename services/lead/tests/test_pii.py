"""Lead email and phone are sealed, masked and blind-indexed — not stored plain (ADR-0017 /
CLAUDE.md's "PII is encrypted and masked by default"). These tests are the ones that would
fail if a future edit accidentally started storing the raw value somewhere it shouldn't.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app import services
from app.config import settings
from app.models import AuditLog
from platform_core.auth import Scope
from platform_core.errors import ConflictProblem
from platform_core.pii import open_sealed
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


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
