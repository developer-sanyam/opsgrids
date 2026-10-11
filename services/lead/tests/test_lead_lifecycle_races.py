"""A `:convert` in flight and an ordinary `PATCH` are not the same request, and nothing used
to stop them touching the same lead at the same time. Two repros this covers:

- **Disqualify during in-flight convert.** `claim_conversion` commits (status still
  "qualified", a claim taken) before the network calls to customer/deal even start —
  deliberately, so no transaction is held across them. That window used to be wide open to an
  ordinary `PATCH`, which read the lead unlocked, saw "qualified", and happily moved it to
  "disqualified" while the claim it never saw was still live. `finalize_conversion` would then
  come back and overwrite that disqualification with "converted" anyway.
- **PATCH read-before-finalize commit-after.** Even past the lease window, an *unlocked* read
  in `update_lead` racing a genuinely concurrent `finalize_conversion` is a plain lost update:
  PATCH reads "qualified" a moment before finalize commits "converted", then PATCH's own write
  lands a moment after and silently erases it. `update_lead` now takes the same row lock
  `finalize_conversion` does, so whichever of the two gets there first now genuinely blocks
  the other until it commits, instead of both acting on a stale "before".

`finalize_conversion`'s own `status == "qualified"` check is the last line behind both: even a
caller these two guards did not anticipate (an admin tool, a bulk script) cannot make it write
"converted" over anything else.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import clients, domain, selectors, services
from app.config import settings
from app.models import SCHEMA, AuditLog, OutboxEvent
from app.schemas import LeadPatch
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from platform_core.errors import ConflictProblem
from platform_core.events import validate_event_schema
from platform_core.models import utcnow
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


class TestPatchCannotOutrunAnInFlightConvert:
    async def test_disqualifying_a_lead_mid_conversion_is_refused(
        self, session, org_id, data
    ) -> None:
        convert_ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        write_ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)

        claim = await services.claim_conversion(session, convert_ctx, data["lead_a_id"])
        assert not claim.already_converted

        with pytest.raises(ConflictProblem, match="conversion is in progress"):
            async with session.begin():
                await services.update_lead(
                    session, write_ctx, data["lead_a_id"], LeadPatch(status="disqualified")
                )

        # The refused patch did not touch the claim -- the attempt that took it can still land.
        await services.finalize_conversion(
            session,
            convert_ctx,
            lead_id=data["lead_a_id"],
            claim_id=claim.claim_id,
            owner_id=claim.owner_id,
            company_id=claim.company_id,
            contact_id=claim.contact_id,
            deal_id=claim.deal_id,
        )
        async with session.begin():
            lead = await selectors.get_lead(session, org_id, data["lead_a_id"])
        assert lead is not None
        assert lead.status == "converted"

    async def test_reassigning_the_owner_mid_conversion_is_also_refused(
        self, session, org_id, data
    ) -> None:
        convert_ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        write_ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)

        claim = await services.claim_conversion(session, convert_ctx, data["lead_a_id"])

        with pytest.raises(ConflictProblem, match="conversion is in progress"):
            async with session.begin():
                await services.update_lead(
                    session, write_ctx, data["lead_a_id"], LeadPatch(owner_id=uuid.uuid4())
                )

        await services.fail_conversion(
            session, convert_ctx, lead_id=data["lead_a_id"], claim_id=claim.claim_id, reason="test"
        )

    async def test_a_plain_field_edit_mid_conversion_is_not_blocked(
        self, session, org_id, data
    ) -> None:
        """The lease only guards `status`/`owner_id` -- a note added while a conversion is in
        flight has no bearing on the conversion and should not have to wait for it."""
        convert_ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        write_ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)

        claim = await services.claim_conversion(session, convert_ctx, data["lead_a_id"])

        async with session.begin():
            updated = await services.update_lead(
                session, write_ctx, data["lead_a_id"], LeadPatch(notes="left a voicemail")
            )
        assert updated.notes == "left a voicemail"

        await services.fail_conversion(
            session, convert_ctx, lead_id=data["lead_a_id"], claim_id=claim.claim_id, reason="test"
        )


class TestConvertedIsTerminalEvenWithoutTheLiteralStatus:
    async def test_a_converted_lead_refuses_any_further_status_or_owner_patch(
        self, session, org_id, data
    ) -> None:
        write_ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        convert_ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)

        claim = await services.claim_conversion(session, convert_ctx, data["lead_a_id"])
        await services.finalize_conversion(
            session,
            convert_ctx,
            lead_id=data["lead_a_id"],
            claim_id=claim.claim_id,
            owner_id=claim.owner_id,
            company_id=claim.company_id,
            contact_id=claim.contact_id,
            deal_id=claim.deal_id,
        )

        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.update_lead(
                    session, write_ctx, data["lead_a_id"], LeadPatch(owner_id=uuid.uuid4())
                )


class TestFinalizeRefusesToOverwriteAMovedStatus:
    async def test_finalize_aborts_when_something_else_moved_the_status_off_qualified(
        self, session, org_id, data
    ) -> None:
        """Neither `update_lead`'s lease check nor its row lock is the only thing standing
        between a live claim and a wrongly-finalized conversion -- this is the last-resort
        check inside `finalize_conversion` itself, exercised here by moving the row's status
        the way nothing in this service's own write path ever legitimately would (a raw
        UPDATE, standing in for a hand-run script or a future caller these guards did not
        anticipate)."""
        convert_ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)

        claim = await services.claim_conversion(session, convert_ctx, data["lead_a_id"])

        async with session.begin():
            await session.execute(
                text('UPDATE "lead"."leads" SET status = :status WHERE id = :id'),
                {"status": "disqualified", "id": data["lead_a_id"]},
            )

        with pytest.raises(ConflictProblem, match="status changed"):
            await services.finalize_conversion(
                session,
                convert_ctx,
                lead_id=data["lead_a_id"],
                claim_id=claim.claim_id,
                owner_id=claim.owner_id,
                company_id=claim.company_id,
                contact_id=claim.contact_id,
                deal_id=claim.deal_id,
            )

        async with session.begin():
            lead = await selectors.get_lead(session, org_id, data["lead_a_id"])
            aborted = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.action == "lead.conversion_aborted",
                        AuditLog.entity_id == data["lead_a_id"],
                    )
                )
            ).scalar_one()
        assert lead is not None
        assert lead.status == "disqualified"
        assert lead.converted_company_id is None
        assert lead.conversion_claim_id is None
        assert aborted == 1


CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

#: The two writes this file uses to stand in for "a writer these guards did not anticipate".
_DISQUALIFY = 'UPDATE "lead"."leads" SET status = \'disqualified\' WHERE id = :id'
_REQUALIFY = 'UPDATE "lead"."leads" SET status = \'qualified\' WHERE id = :id'
_DISQUALIFY_AND_REASSIGN = (
    'UPDATE "lead"."leads" SET status = \'disqualified\', owner_id = :owner WHERE id = :id'
)


class TestAnAbortedConversionLeavesATrail:
    """m7, fix round 3. When `finalize_conversion` refuses to write "converted" the records
    customer and deal already created for the attempt are still there, so the abort has to say
    which — in the audit row, and as `crm.lead.conversion_aborted` for whatever may later clean
    them up — and it has to notice a reassignment as well as a status change."""

    async def _abort(self, session, org_id, lead_id, *, sql: str, params: dict, match: str):
        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        claim = await services.claim_conversion(session, ctx, lead_id)
        async with session.begin():
            await session.execute(text(sql), {"id": lead_id, **params})
        with pytest.raises(ConflictProblem, match=match):
            await services.finalize_conversion(
                session,
                ctx,
                lead_id=lead_id,
                claim_id=claim.claim_id,
                owner_id=claim.owner_id,
                company_id=claim.company_id,
                contact_id=claim.contact_id,
                deal_id=claim.deal_id,
            )
        return claim

    async def _trail(self, session, org_id, lead_id):
        async with session.begin():
            audit = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "lead.conversion_aborted",
                        AuditLog.entity_id == lead_id,
                    )
                )
            ).scalar_one()
            event = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "crm.lead.conversion_aborted",
                        OutboxEvent.aggregate_id == lead_id,
                    )
                )
            ).scalar_one()
            lead = await selectors.get_lead(session, org_id, lead_id)
        return audit, event, lead

    async def test_a_status_abort_names_the_orphaned_records_and_stages_the_event(
        self, session, org_id, data
    ) -> None:
        claim = await self._abort(
            session,
            org_id,
            data["lead_a_id"],
            sql='UPDATE "lead"."leads" SET status = \'disqualified\' WHERE id = :id',
            params={},
            match="status changed",
        )
        audit, event, lead = await self._trail(session, org_id, data["lead_a_id"])

        expected = {
            "company_id": str(claim.company_id),
            "contact_id": str(claim.contact_id),
            "deal_id": str(claim.deal_id),
        }
        assert {k: audit.after[k] for k in expected} == expected
        assert event.payload["reason"] == "status_changed"
        assert {k: event.payload[k] for k in expected} == expected
        validate_event_schema(
            "crm.lead.conversion_aborted", 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )
        assert lead is not None
        assert lead.conversion_claim_id is None
        assert lead.converted_company_id is None

    async def test_a_reassignment_between_claim_and_finalize_aborts_the_same_way(
        self, session, org_id, data
    ) -> None:
        new_owner = uuid.uuid4()
        claim = await self._abort(
            session,
            org_id,
            data["lead_a_id"],
            sql='UPDATE "lead"."leads" SET owner_id = :owner WHERE id = :id',
            params={"owner": new_owner},
            match="reassigned",
        )
        audit, event, lead = await self._trail(session, org_id, data["lead_a_id"])

        assert event.payload["reason"] == "owner_changed"
        assert audit.after["deal_id"] == str(claim.deal_id)
        validate_event_schema(
            "crm.lead.conversion_aborted", 1, dict(event.payload), contracts_root=CONTRACTS_ROOT
        )
        assert lead is not None
        assert lead.status == "qualified"
        assert lead.owner_id == new_owner
        assert lead.conversion_claim_id is None
        assert lead.converted_company_id is None

    async def _requalify(self, session, lead_id) -> None:
        """Put the lead back to `qualified`, the way a rep who re-qualified it would, so a retry
        has something to claim."""
        async with session.begin():
            await session.execute(text(_REQUALIFY), {"id": lead_id})

    async def _takeover_then_abort(self, session, org_id, lead_id):
        """Attempt A claims; its lease runs out; attempt B takes the claim over and then aborts
        because the status moved under it. Returns both claims -- A still holds its dead one."""
        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        claim_a = await services.claim_conversion(session, ctx, lead_id)
        async with session.begin():
            await session.execute(
                text('UPDATE "lead"."leads" SET conversion_claimed_at = :aged WHERE id = :id'),
                {
                    "aged": utcnow() - timedelta(seconds=domain.CONVERSION_LEASE_SECONDS + 5),
                    "id": lead_id,
                },
            )
        claim_b = await services.claim_conversion(session, ctx, lead_id)
        async with session.begin():
            await session.execute(text(_DISQUALIFY), {"id": lead_id})
        with pytest.raises(ConflictProblem, match="status changed"):
            await services.finalize_conversion(
                session,
                ctx,
                lead_id=lead_id,
                claim_id=claim_b.claim_id,
                owner_id=claim_b.owner_id,
                company_id=claim_b.company_id,
                contact_id=claim_b.contact_id,
                deal_id=claim_b.deal_id,
            )
        return claim_a, claim_b

    async def test_a_status_abort_bumps_the_generation_and_names_it(
        self, session, org_id, data
    ) -> None:
        claim = await self._abort(
            session, org_id, data["lead_a_id"], sql=_DISQUALIFY, params={}, match="status changed"
        )
        audit, _event, lead = await self._trail(session, org_id, data["lead_a_id"])

        assert claim.generation == 0
        assert lead is not None
        assert lead.conversion_generation == 1
        # The audit row names the generation the attempt's ids were derived under, the
        # generation the lead now derives from, and the claim that ended.
        assert audit.after["aborted_generation"] == 0
        assert audit.after["conversion_generation"] == 1
        assert audit.after["claim_id"] == str(claim.claim_id)

    async def test_an_owner_abort_bumps_exactly_once_and_names_the_old_generation(
        self, session, org_id, data
    ) -> None:
        claim = await self._abort(
            session,
            org_id,
            data["lead_a_id"],
            sql='UPDATE "lead"."leads" SET owner_id = :owner WHERE id = :id',
            params={"owner": uuid.uuid4()},
            match="reassigned",
        )
        audit, _event, lead = await self._trail(session, org_id, data["lead_a_id"])

        assert claim.generation == 0
        assert lead is not None
        assert lead.conversion_generation == 1  # not 2: the old owner_changed bump is not doubled
        assert audit.after["aborted_generation"] == 0
        assert audit.after["conversion_generation"] == 1
        assert audit.after["claim_id"] == str(claim.claim_id)

    async def test_an_abort_where_status_and_owner_both_moved_bumps_once_and_reports_status_changed(
        self, session, org_id, data
    ) -> None:
        await self._abort(
            session,
            org_id,
            data["lead_a_id"],
            sql=_DISQUALIFY_AND_REASSIGN,
            params={"owner": uuid.uuid4()},
            match="status changed",
        )
        audit, event, lead = await self._trail(session, org_id, data["lead_a_id"])

        # `finalize_conversion` reports one reason (status wins) and bumps once, whichever
        # moved: what makes the retry's ids disjoint is the bump, not the reason.
        assert event.payload["reason"] == "status_changed"
        assert lead is not None
        assert lead.conversion_generation == 1
        assert audit.after["aborted_generation"] == 0
        assert audit.after["conversion_generation"] == 1

    async def test_a_second_abort_bumps_again_from_the_new_generation(
        self, session, org_id, data
    ) -> None:
        lead_id = data["lead_a_id"]
        first = await self._abort(
            session, org_id, lead_id, sql=_DISQUALIFY, params={}, match="status changed"
        )
        await self._requalify(session, lead_id)
        second = await self._abort(
            session, org_id, lead_id, sql=_DISQUALIFY, params={}, match="status changed"
        )

        async with session.begin():
            lead = await selectors.get_lead(session, org_id, lead_id)
            audits = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.action == "lead.conversion_aborted",
                            AuditLog.entity_id == lead_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert (first.generation, second.generation) == (0, 1)
        assert lead is not None
        assert lead.conversion_generation == 2
        assert sorted(a.after["aborted_generation"] for a in audits) == [0, 1]
        assert sorted(a.after["conversion_generation"] for a in audits) == [1, 2]

    @pytest.mark.parametrize(
        ("sql", "params", "match"),
        [
            pytest.param(_DISQUALIFY, {}, "status changed", id="status"),
            pytest.param(
                'UPDATE "lead"."leads" SET owner_id = :owner WHERE id = :id',
                {"owner": uuid.uuid4()},
                "reassigned",
                id="owner",
            ),
        ],
    )
    async def test_a_retried_convert_after_any_abort_derives_different_ids(
        self, session, org_id, data, sql, params, match
    ) -> None:
        lead_id = data["lead_a_id"]
        first = await self._abort(session, org_id, lead_id, sql=sql, params=params, match=match)
        await self._requalify(session, lead_id)
        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        retry = await services.claim_conversion(session, ctx, lead_id)

        first_ids = (first.company_id, first.contact_id, first.deal_id)
        retry_ids = (retry.company_id, retry.contact_id, retry.deal_id)
        assert (first.generation, retry.generation) == (0, 1)
        assert set(retry_ids).isdisjoint(first_ids)
        assert retry_ids == domain.conversion_ids(lead_id, 1)
        assert retry.claim_id != first.claim_id

    async def test_a_stale_claim_stages_no_second_event_and_bumps_nothing(
        self, session, org_id, data
    ) -> None:
        lead_id = data["lead_a_id"]
        claim_a, claim_b = await self._takeover_then_abort(session, org_id, lead_id)
        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)

        # A finally answers, still holding the claim B took over: a 409, and nothing written.
        with pytest.raises(ConflictProblem, match="taken over"):
            await services.finalize_conversion(
                session,
                ctx,
                lead_id=lead_id,
                claim_id=claim_a.claim_id,
                owner_id=claim_a.owner_id,
                company_id=claim_a.company_id,
                contact_id=claim_a.contact_id,
                deal_id=claim_a.deal_id,
            )

        async with session.begin():
            lead = await selectors.get_lead(session, org_id, lead_id)
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.event_type == "crm.lead.conversion_aborted",
                            OutboxEvent.aggregate_id == lead_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            aborted_audits = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.action == "lead.conversion_aborted",
                        AuditLog.entity_id == lead_id,
                    )
                )
            ).scalar_one()
        assert claim_a.claim_id != claim_b.claim_id
        assert lead is not None
        assert lead.conversion_generation == 1  # B's abort bumped once; A's late call did not
        assert len(events) == 1
        assert aborted_audits == 1


class TestUpdateLeadNeverHoldsTheRowAcrossTheDirectoryCall:
    """Fix round 4, finding 4: `update_lead` used to lock the lead's row and *then* ask
    customer-service whether the new owner is active, so a hanging customer-service froze every
    other PATCH — and `:convert`'s claim and finalize — on that lead for as long as the call
    took. The scope check and the directory call now run before `get_lead_for_update`."""

    async def test_the_leads_row_is_free_while_the_new_owner_is_being_validated(
        self, session, org_id, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)
        rep_a, rep_b = uuid.uuid4(), uuid.uuid4()
        async with session.begin():
            lead = await services.create_lead(
                session,
                write,
                full_name="Lock probe",
                company=None,
                email=None,
                phone=None,
                source="form",
                owner_id=rep_a,
                notes="",
            )
        lead_id = lead.id

        directory_call_started = asyncio.Event()
        release_directory_call = asyncio.Event()

        async def _slow_directory(cfg, organization_id, owner_ids):  # type: ignore[no-untyped-def]
            directory_call_started.set()
            await release_directory_call.wait()
            return []

        monkeypatch.setattr(clients, "invalid_owner_ids", _slow_directory)

        cfg = settings()
        engine_a = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
        engine_b = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
        maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
        maker_b = async_sessionmaker(engine_b, expire_on_commit=False)

        def _bind(org: uuid.UUID):
            def _listener(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

            return _listener

        outcome: dict[str, str] = {}
        try:
            async with maker_a() as session_a, maker_b() as session_b:
                sa_event.listen(session_a.sync_session, "after_begin", _bind(org_id))
                sa_event.listen(session_b.sync_session, "after_begin", _bind(org_id))

                async def _patch() -> None:
                    async with session_a.begin():
                        await services.update_lead(
                            session_a, write, lead_id, LeadPatch(owner_id=rep_b)
                        )

                async def _probe() -> None:
                    await directory_call_started.wait()
                    try:
                        async with session_b.begin():
                            await session_b.execute(
                                text("SELECT id FROM leads WHERE id = :i FOR UPDATE NOWAIT"),
                                {"i": lead_id},
                            )
                        outcome["lead_row"] = "free"
                    except DBAPIError as exc:
                        outcome["lead_row"] = f"locked: {type(exc.orig).__name__}"

                task_a = asyncio.create_task(_patch())
                await asyncio.wait_for(_probe(), timeout=15.0)
                release_directory_call.set()
                await asyncio.wait_for(task_a, timeout=15.0)
        finally:
            await engine_a.dispose()
            await engine_b.dispose()

        assert outcome["lead_row"] == "free"

        # The fixture's session cached this lead when it created it; the patch ran on another.
        session.expire_all()
        async with session.begin():
            patched = await selectors.get_lead(session, org_id, lead_id)
        assert patched is not None
        assert patched.owner_id == rep_b


class TestPatchVersusFinalizeConcurrency:
    async def test_a_patch_racing_finalize_never_loses_the_committed_conversion(
        self, org_id, data
    ) -> None:
        """Two real sessions: one carries a claim through to `finalize_conversion`, the other
        tries to disqualify the same lead at (as close to) the same moment as asyncio can make
        it. Before `update_lead` took the same row lock `finalize_conversion` does, a PATCH
        whose read landed a moment before finalize's commit could still write a moment after
        it, quietly erasing a conversion a client had already been told succeeded. Whichever
        of the two actually gets the row lock first, the other must act on what the winner
        left behind, not on what it read before losing the race for the lock."""
        cfg = settings()
        engine_a = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
        engine_b = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
        maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
        maker_b = async_sessionmaker(engine_b, expire_on_commit=False)

        def _bind(org: uuid.UUID):
            def _listener(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

            return _listener

        convert_ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)
        write_ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)

        try:
            async with maker_a() as session_a, maker_b() as session_b:
                sa_event.listen(session_a.sync_session, "after_begin", _bind(org_id))
                sa_event.listen(session_b.sync_session, "after_begin", _bind(org_id))

                claim = await services.claim_conversion(session_a, convert_ctx, data["lead_a_id"])

                async def _finalize() -> None:
                    await services.finalize_conversion(
                        session_a,
                        convert_ctx,
                        lead_id=data["lead_a_id"],
                        claim_id=claim.claim_id,
                        owner_id=claim.owner_id,
                        company_id=claim.company_id,
                        contact_id=claim.contact_id,
                        deal_id=claim.deal_id,
                    )

                async def _patch() -> Exception | None:
                    try:
                        async with session_b.begin():
                            await services.update_lead(
                                session_b,
                                write_ctx,
                                data["lead_a_id"],
                                LeadPatch(status="disqualified"),
                            )
                    except ConflictProblem as exc:
                        return exc
                    return None

                results = await asyncio.gather(_finalize(), _patch(), return_exceptions=True)
        finally:
            await engine_a.dispose()
            await engine_b.dispose()

        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, ConflictProblem):
                raise result

        # The patch must never have won outright -- a live claim (or, failing that, an
        # already-converted lead) refuses it every time, whichever transaction the lock
        # happened to favour.
        patch_outcome = results[1]
        assert isinstance(patch_outcome, ConflictProblem)

        check_engine = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
        try:
            check_maker = async_sessionmaker(check_engine, expire_on_commit=False)
            async with check_maker() as check:
                sa_event.listen(check.sync_session, "after_begin", _bind(org_id))
                async with check.begin():
                    lead = await selectors.get_lead(check, org_id, data["lead_a_id"])
        finally:
            await check_engine.dispose()

        assert lead is not None
        assert lead.status == "converted"
        assert lead.converted_company_id == claim.company_id


class TestIdentityMapCannotServeAStaleClaim:
    async def test_a_takeover_survives_a_stale_identity_map_cache(
        self, session, org_id, data
    ) -> None:
        """`claim_conversion`, `finalize_conversion` and `fail_conversion` are separate
        transactions run on the *same* session -- so once this session has fetched a lead's
        row once, SQLAlchemy's identity map is holding a Python object for it. Without
        `populate_existing=True` on the locked fetch, a second locked read for the same row
        would hand back that cached object exactly as it was after the *first* read, not
        refreshed from the row Postgres just re-locked -- so an attempt whose own claim went
        stale minutes ago could still see its own belief reflected back at it instead of the
        takeover that actually happened.

        The fixture's `session` is what holds that reference here, across every step below --
        a fresh session per step (as a real takeover across two separate requests would use)
        would never touch the same identity-map entry twice and so could never reproduce this."""
        ctx = ctx_for(org_id, "crm.lead.lead.convert", Scope.ORG)

        claim_a = await services.claim_conversion(session, ctx, data["lead_a_id"])

        # Age the claim past its lease with a *raw* UPDATE -- exactly as real time passing
        # would, this never touches the ORM object this session already cached for this row.
        aged = utcnow().replace(year=utcnow().year - 1)
        async with session.begin():
            await session.execute(
                text('UPDATE "lead"."leads" SET conversion_claimed_at = :aged WHERE id = :id'),
                {"aged": aged, "id": data["lead_a_id"]},
            )

        # If the identity map served claim_a's cached (fresh-looking) timestamp back here
        # instead of the row Postgres just locked, this would incorrectly raise "already being
        # converted" -- claim_a's lease reads as still live from a cache that never learned it
        # was aged out from under it.
        claim_b = await services.claim_conversion(session, ctx, data["lead_a_id"])
        assert claim_b.claim_id != claim_a.claim_id

        await services.finalize_conversion(
            session,
            ctx,
            lead_id=data["lead_a_id"],
            claim_id=claim_b.claim_id,
            owner_id=claim_b.owner_id,
            company_id=claim_b.company_id,
            contact_id=claim_b.contact_id,
            deal_id=claim_b.deal_id,
        )

        # A, still carrying its own now-stale claim_id, must lose on both of its own remaining
        # moves -- neither can be allowed to re-open or corrupt what B already finished.
        with pytest.raises(ConflictProblem):
            await services.finalize_conversion(
                session,
                ctx,
                lead_id=data["lead_a_id"],
                claim_id=claim_a.claim_id,
                owner_id=claim_a.owner_id,
                company_id=claim_a.company_id,
                contact_id=claim_a.contact_id,
                deal_id=claim_a.deal_id,
            )
        await services.fail_conversion(
            session, ctx, lead_id=data["lead_a_id"], claim_id=claim_a.claim_id, reason="stale"
        )

        async with session.begin():
            lead = await selectors.get_lead(session, org_id, data["lead_a_id"])
            converted_audits = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.action == "lead.converted",
                        AuditLog.entity_id == data["lead_a_id"],
                    )
                )
            ).scalar_one()
            converted_events = (
                await session.execute(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.event_type == "crm.lead.converted",
                        OutboxEvent.aggregate_id == data["lead_a_id"],
                    )
                )
            ).scalar_one()

        assert lead is not None
        assert lead.status == "converted"
        assert lead.converted_company_id == claim_b.company_id
        # B's claim is intact in the sense that matters: it is the one, and only, conversion
        # on record -- A's later calls neither reopened nor duplicated it.
        assert converted_audits == 1
        assert converted_events == 1
        assert lead.conversion_claim_id is None
        assert lead.conversion_claimed_at is None
