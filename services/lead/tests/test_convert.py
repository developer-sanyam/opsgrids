"""Conversion: claim / call / record, idempotency, the PII round-trip, and the failure modes
that must not be confused with each other.

`clients.ensure_company_and_contact` / `clients.ensure_deal` are monkeypatched — this is
`services.convert_lead`'s own contract under test, not the customer/deal services' HTTP
surface (covered by `test_clients.py` on the caller side, and by each of those services' own
test suites on the callee side).

`services.convert_lead` now manages its own transactions (claim, then the two client calls
with none open, then record) — every test here calls it against the bare `session` fixture,
never wrapped in its own `session.begin()`, exactly as `RawSessionDep`'s route does.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import clients, domain, selectors, services
from app.config import settings
from app.models import SCHEMA, AuditLog, OutboxEvent
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
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
        result = await services.convert_lead(session, ctx, data["lead_a_id"])

        assert result == (company_id, contact_id, deal_id)
        async with session.begin():
            lead = await selectors.get_lead(session, org_id, data["lead_a_id"])
        assert lead is not None
        assert lead.status == "converted"
        assert lead.converted_company_id == company_id
        assert lead.converted_contact_id == contact_id
        assert lead.converted_deal_id == deal_id
        assert lead.converted_at is not None
        assert lead.conversion_claim_id is None
        assert lead.conversion_claimed_at is None
        assert len(calls) == 2

    async def test_the_deal_id_passed_to_deal_is_deterministic_from_the_lead_id(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list = []
        _fake_ensure_company_and_contact(monkeypatch, calls)
        _fake_ensure_deal(monkeypatch, calls)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        await services.convert_lead(session, ctx, data["lead_a_id"])

        _expected_company, _expected_contact, expected_deal = domain.conversion_ids(
            data["lead_a_id"]
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
        await services.convert_lead(session, ctx, data["lead_a_id"])

        async with session.begin():
            audits = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.action == "lead.converted",
                        AuditLog.entity_id == data["lead_a_id"],
                    )
                )
            ).scalar_one()
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.aggregate_id == data["lead_a_id"])
                    )
                )
                .scalars()
                .all()
            )
        assert audits == 1
        converted_events = [e for e in events if e.event_type == "crm.lead.converted"]
        assert len(converted_events) == 1

    async def test_no_transaction_is_open_during_the_network_calls(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of splitting `:convert` into claim / call / record: the two calls
        to customer's and deal's `/internal/v1` must run with no transaction open, so a slow
        upstream never holds a lock (or a whole connection) for anyone else reading this
        lead's row."""
        observed: dict[str, bool] = {}

        async def _fake_company(settings, organization_id, **kwargs):
            observed["company_call_in_txn"] = session.in_transaction()
            return uuid.uuid4(), None

        async def _fake_deal(settings, organization_id, **kwargs):
            observed["deal_call_in_txn"] = session.in_transaction()
            return uuid.uuid4()

        monkeypatch.setattr(clients, "ensure_company_and_contact", _fake_company)
        monkeypatch.setattr(clients, "ensure_deal", _fake_deal)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        await services.convert_lead(session, ctx, data["lead_a_id"])

        assert observed["company_call_in_txn"] is False
        assert observed["deal_call_in_txn"] is False


class TestConvertIdempotency:
    async def test_converting_twice_returns_the_same_ids_without_calling_out_again(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-0017: the same lead converted twice is one company, one deal."""
        calls: list = []
        company_id, contact_id = _fake_ensure_company_and_contact(monkeypatch, calls)
        deal_id = _fake_ensure_deal(monkeypatch, calls)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        first = await services.convert_lead(session, ctx, data["lead_a_id"])
        assert len(calls) == 2

        second = await services.convert_lead(session, ctx, data["lead_a_id"])

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
        await services.convert_lead(session, ctx, data["lead_a_id"])
        await services.convert_lead(session, ctx, data["lead_a_id"])

        async with session.begin():
            audits = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.action == "lead.converted",
                        AuditLog.entity_id == data["lead_a_id"],
                    )
                )
            ).scalar_one()
        assert audits == 1


class TestConvertRefusals:
    async def test_an_unqualified_lead_cannot_convert(self, session, org_id, data) -> None:
        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        with pytest.raises(ConflictProblem):
            await services.convert_lead(session, ctx, data["lead_b_id"])  # still "new"

    async def test_a_lead_with_no_owner_refuses_with_validation_problem(
        self, session, org_id, data
    ) -> None:
        async with session.begin():
            lead = await selectors.get_lead(session, org_id, data["lead_a_id"])
            assert lead is not None
            lead.owner_id = None
            await session.flush()

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        with pytest.raises(ValidationProblem):
            await services.convert_lead(session, ctx, data["lead_a_id"])

    async def test_an_unknown_owner_on_customers_side_becomes_a_validation_problem(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _refuse(settings, organization_id, **kwargs):
            raise clients.OwnerUnknown("nope")

        monkeypatch.setattr(clients, "ensure_company_and_contact", _refuse)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        with pytest.raises(ValidationProblem):
            await services.convert_lead(session, ctx, data["lead_a_id"])

        # The failed attempt cleared its own claim — a retry is not blocked by the lease.
        async with session.begin():
            lead = await selectors.get_lead(session, org_id, data["lead_a_id"])
        assert lead is not None
        assert lead.conversion_claim_id is None
        assert lead.status == "qualified"

    async def test_a_permanent_rejection_becomes_a_validation_problem(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _reject(settings, organization_id, **kwargs):
            raise clients.ConversionRejected("nope")

        monkeypatch.setattr(clients, "ensure_company_and_contact", _reject)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        with pytest.raises(ValidationProblem):
            await services.convert_lead(session, ctx, data["lead_a_id"])

    async def test_an_unreachable_target_becomes_upstream_unavailable(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fail(settings, organization_id, **kwargs):
            raise clients.ConversionUnavailable("down")

        monkeypatch.setattr(clients, "ensure_company_and_contact", _fail)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        with pytest.raises(UpstreamUnavailableProblem):
            await services.convert_lead(session, ctx, data["lead_a_id"])

    async def test_a_failed_conversion_writes_a_conversion_failed_audit_row(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fail(settings, organization_id, **kwargs):
            raise clients.ConversionUnavailable("down")

        monkeypatch.setattr(clients, "ensure_company_and_contact", _fail)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        with pytest.raises(UpstreamUnavailableProblem):
            await services.convert_lead(session, ctx, data["lead_a_id"])

        async with session.begin():
            row = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity == "lead",
                        AuditLog.entity_id == data["lead_a_id"],
                        AuditLog.action == "lead.conversion_failed",
                    )
                )
            ).scalar_one()
        assert row.after["reason"]


class TestConvertClaimLease:
    async def test_a_live_claim_refuses_a_second_attempt_with_409(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        # Claim it directly, without finishing the conversion — simulates an attempt still
        # in flight (mid network-call) when a second one arrives.
        await services.claim_conversion(session, ctx, data["lead_a_id"])

        with pytest.raises(ConflictProblem, match="already being converted"):
            await services.claim_conversion(session, ctx, data["lead_a_id"])

    async def test_a_stale_claim_is_taken_over_rather_than_refused(
        self, session, org_id, data
    ) -> None:
        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        first = await services.claim_conversion(session, ctx, data["lead_a_id"])

        # Age the claim past the lease by hand — the same effect as time actually passing.
        async with session.begin():
            lead = await selectors.get_lead(session, org_id, data["lead_a_id"])
            assert lead is not None
            from datetime import UTC, datetime, timedelta

            lead.conversion_claimed_at = datetime.now(UTC) - timedelta(
                seconds=domain.CONVERSION_LEASE_SECONDS + 5
            )
            await session.flush()

        second = await services.claim_conversion(session, ctx, data["lead_a_id"])
        assert second.claim_id != first.claim_id
        # The ids offered to customer/deal are still deterministic from the lead's own id —
        # a takeover does not change what the conversion becomes.
        assert second.company_id == first.company_id
        assert second.deal_id == first.deal_id


class TestConvertAuditsTheSensitiveRead:
    async def test_opening_the_leads_own_sealed_email_and_phone_is_audited(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list = []
        _fake_ensure_company_and_contact(monkeypatch, calls)
        _fake_ensure_deal(monkeypatch, calls)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        await services.convert_lead(session, ctx, data["lead_a_id"])

        async with session.begin():
            sensitive = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.entity == "lead",
                            AuditLog.entity_id == data["lead_a_id"],
                            AuditLog.action == "entity.sensitive_read",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(sensitive) == 1
        assert set(sensitive[0].after["fields"]) == {"email", "phone"}

    async def test_the_sensitive_read_audit_row_survives_a_downstream_failure(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The finding this fixes: the audit row for opening a lead's PII used to live in the
        same transaction as the network calls that followed it, so a failure after the PII had
        already left this process rolled the audit row back too. It is now committed by
        `claim_conversion`, before either call happens."""

        async def _fail(settings, organization_id, **kwargs):
            raise clients.ConversionUnavailable("down")

        monkeypatch.setattr(clients, "ensure_company_and_contact", _fail)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        with pytest.raises(UpstreamUnavailableProblem):
            await services.convert_lead(session, ctx, data["lead_a_id"])

        async with session.begin():
            sensitive = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.entity == "lead",
                            AuditLog.entity_id == data["lead_a_id"],
                            AuditLog.action == "entity.sensitive_read",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(sensitive) == 1

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
        await services.convert_lead(session, ctx, data["lead_a_id"])

        company_call = next(kwargs for name, kwargs in calls if name == "company")
        assert company_call["contact_email"] == "priya@stellar.example"
        assert company_call["contact_raw_phone"] == "+919876543210"


class TestConvertConcurrency:
    async def test_two_concurrent_converts_of_the_same_lead_produce_one_converted_audit_row(
        self, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two real sessions, both attempting `:convert` on the same lead at once. The
        claim/lease pair must serialize them: exactly one succeeds through to
        `finalize_conversion` and exactly one `lead.converted` audit row and one
        `crm.lead.converted` outbox event exist afterward, whichever attempt "wins"."""

        def _connect_args() -> dict:
            return {"server_settings": {"search_path": f"{SCHEMA},public"}}

        cfg = settings()
        engine_a = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
        engine_b = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
        maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
        maker_b = async_sessionmaker(engine_b, expire_on_commit=False)

        def _bind(org: uuid.UUID):
            def _listener(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

            return _listener

        calls: list = []

        async def _fake_company(settings, organization_id, **kwargs):
            calls.append("company")
            await asyncio.sleep(0.05)
            return uuid.uuid4(), None

        async def _fake_deal(settings, organization_id, **kwargs):
            calls.append("deal")
            return uuid.uuid4()

        monkeypatch.setattr(clients, "ensure_company_and_contact", _fake_company)
        monkeypatch.setattr(clients, "ensure_deal", _fake_deal)

        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)

        try:
            async with maker_a() as session_a, maker_b() as session_b:
                sa_event.listen(session_a.sync_session, "after_begin", _bind(org_id))
                sa_event.listen(session_b.sync_session, "after_begin", _bind(org_id))

                results = await asyncio.gather(
                    services.convert_lead(session_a, ctx, data["lead_a_id"]),
                    services.convert_lead(session_b, ctx, data["lead_a_id"]),
                    return_exceptions=True,
                )
        finally:
            await engine_a.dispose()
            await engine_b.dispose()

        # One of the two either succeeded outright or was refused with a 409 (the live-lease
        # case) — both are correct outcomes; what must never happen is two conversions.
        outcomes = [r for r in results if not isinstance(r, BaseException)]
        refusals = [r for r in results if isinstance(r, ConflictProblem)]
        assert len(outcomes) + len(refusals) == 2
        for r in results:
            if isinstance(r, BaseException) and not isinstance(r, ConflictProblem):
                raise r

        admin_engine = create_async_engine(cfg.admin_dsn, connect_args=_connect_args())
        try:
            admin_maker = async_sessionmaker(admin_engine, expire_on_commit=False)
            async with admin_maker() as check:
                audits = (
                    await check.execute(
                        select(func.count())
                        .select_from(AuditLog)
                        .where(
                            AuditLog.action == "lead.converted",
                            AuditLog.entity_id == data["lead_a_id"],
                        )
                    )
                ).scalar_one()
                events = (
                    await check.execute(
                        select(func.count())
                        .select_from(OutboxEvent)
                        .where(
                            OutboxEvent.aggregate_id == data["lead_a_id"],
                            OutboxEvent.event_type == "crm.lead.converted",
                        )
                    )
                ).scalar_one()
        finally:
            await admin_engine.dispose()

        assert audits == 1
        assert events == 1
