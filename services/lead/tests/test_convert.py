"""Conversion: idempotency, the PII round-trip, and the failure modes that must not be
confused with each other. `clients.ensure_company_and_contact` / `clients.ensure_deal` are
monkeypatched — this is `services.convert_lead`'s own contract under test, not the customer/deal
services' HTTP surface (covered by `test_clients.py` on the caller side, and by each of those
services' own test suites on the callee side).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app import clients, domain, services
from app.models import AuditLog, OutboxEvent
from platform_core.auth import Scope
from platform_core.errors import ConflictProblem, UpstreamUnavailableProblem, ValidationProblem
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def _fake_ensure_company_and_contact(monkeypatch, calls, *, company_id=None, contact_id=None):
    company_id = company_id or uuid.uuid4()

    async def _fake(settings, organization_id, **kwargs):
        calls.append(("company", kwargs))
        return company_id, contact_id

    monkeypatch.setattr(clients, "ensure_company_and_contact", _fake)
    return company_id, contact_id


def _fake_ensure_deal(monkeypatch, calls, *, deal_id=None):
    deal_id = deal_id or uuid.uuid4()

    async def _fake(settings, organization_id, **kwargs):
        calls.append(("deal", kwargs))
        return deal_id

    monkeypatch.setattr(clients, "ensure_deal", _fake)
    return deal_id


class TestConvertHappyPath:
    async def test_convert_stores_the_returned_ids_and_marks_the_lead_converted(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list = []
        company_id, contact_id = _fake_ensure_company_and_contact(monkeypatch, calls)
        deal_id = _fake_ensure_deal(monkeypatch, calls)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.OWN, subject_ref=data["owner_a"])
        async with session.begin():
            result = await services.convert_lead(session, ctx, data["lead_a"])

        assert result == (company_id, contact_id, deal_id)
        assert data["lead_a"].status == "converted"
        assert data["lead_a"].converted_company_id == company_id
        assert data["lead_a"].converted_contact_id == contact_id
        assert data["lead_a"].converted_deal_id == deal_id
        assert data["lead_a"].converted_at is not None
        assert len(calls) == 2

    async def test_the_deal_id_passed_to_deal_is_deterministic_from_the_lead_id(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list = []
        _fake_ensure_company_and_contact(monkeypatch, calls)
        _fake_ensure_deal(monkeypatch, calls)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        async with session.begin():
            await services.convert_lead(session, ctx, data["lead_a"])

        _expected_company, _expected_contact, expected_deal = domain.conversion_ids(
            data["lead_a"].id
        )
        deal_call = next(kwargs for name, kwargs in calls if name == "deal")
        assert deal_call["deal_id"] == expected_deal

    async def test_converting_stages_an_audit_row_and_an_outbox_event(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list = []
        _fake_ensure_company_and_contact(monkeypatch, calls)
        _fake_ensure_deal(monkeypatch, calls)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        async with session.begin():
            await services.convert_lead(session, ctx, data["lead_a"])

        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "lead.converted", AuditLog.entity_id == data["lead_a"].id)
            )
        ).scalar_one()
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.aggregate_id == data["lead_a"].id)
                )
            )
            .scalars()
            .all()
        )
        assert audits == 1
        converted_events = [e for e in events if e.event_type == "crm.lead.converted"]
        assert len(converted_events) == 1


class TestConvertIdempotency:
    async def test_converting_twice_returns_the_same_ids_without_calling_out_again(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-0017: the same lead converted twice is one company, one deal."""
        calls: list = []
        company_id, contact_id = _fake_ensure_company_and_contact(monkeypatch, calls)
        deal_id = _fake_ensure_deal(monkeypatch, calls)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        async with session.begin():
            first = await services.convert_lead(session, ctx, data["lead_a"])
        assert len(calls) == 2

        async with session.begin():
            second = await services.convert_lead(session, ctx, data["lead_a"])

        assert second == first == (company_id, contact_id, deal_id)
        # The replay short-circuits before either client call — no second attempt to reach
        # customer-service or deal-service.
        assert len(calls) == 2

    async def test_converting_twice_writes_only_one_audit_row(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list = []
        _fake_ensure_company_and_contact(monkeypatch, calls)
        _fake_ensure_deal(monkeypatch, calls)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        async with session.begin():
            await services.convert_lead(session, ctx, data["lead_a"])
        async with session.begin():
            await services.convert_lead(session, ctx, data["lead_a"])

        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "lead.converted", AuditLog.entity_id == data["lead_a"].id)
            )
        ).scalar_one()
        assert audits == 1


class TestConvertRefusals:
    async def test_an_unqualified_lead_cannot_convert(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.convert_lead(session, ctx, data["lead_b"])  # still "new"

    async def test_a_lead_with_no_owner_refuses_with_validation_problem(
        self, session, org_id, data
    ) -> None:
        lead = data["lead_a"]
        async with session.begin():
            lead.owner_id = None
            await session.flush()

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        with pytest.raises(ValidationProblem):
            async with session.begin():
                await services.convert_lead(session, ctx, lead)

    async def test_an_unknown_owner_on_customers_side_becomes_a_validation_problem(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _refuse(settings, organization_id, **kwargs):
            raise clients.OwnerUnknown("nope")

        monkeypatch.setattr(clients, "ensure_company_and_contact", _refuse)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        with pytest.raises(ValidationProblem):
            async with session.begin():
                await services.convert_lead(session, ctx, data["lead_a"])

    async def test_an_unreachable_target_becomes_upstream_unavailable(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fail(settings, organization_id, **kwargs):
            raise clients.ConversionUnavailable("down")

        monkeypatch.setattr(clients, "ensure_company_and_contact", _fail)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        with pytest.raises(UpstreamUnavailableProblem):
            async with session.begin():
                await services.convert_lead(session, ctx, data["lead_a"])


class TestConvertAuditsTheSensitiveRead:
    async def test_opening_the_leads_own_sealed_email_and_phone_is_audited(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list = []
        _fake_ensure_company_and_contact(monkeypatch, calls)
        _fake_ensure_deal(monkeypatch, calls)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        async with session.begin():
            await services.convert_lead(session, ctx, data["lead_a"])

        sensitive = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity == "lead",
                        AuditLog.entity_id == data["lead_a"].id,
                        AuditLog.action == "entity.sensitive_read",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(sensitive) == 1
        assert set(sensitive[0].after["fields"]) == {"email", "phone"}

    async def test_the_raw_email_and_phone_reach_customer_service(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The lead was created with a raw email/phone (`tests/conftest.py`'s `data` fixture);
        `:convert` must forward the *plaintext*, not the mask, or customer-service would seal
        the bullet characters themselves."""
        calls: list = []
        _fake_ensure_company_and_contact(monkeypatch, calls)
        _fake_ensure_deal(monkeypatch, calls)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        async with session.begin():
            await services.convert_lead(session, ctx, data["lead_a"])

        company_call = next(kwargs for name, kwargs in calls if name == "company")
        assert company_call["contact_email"] == "priya@stellar.example"
        assert company_call["contact_raw_phone"] == "+919876543210"
