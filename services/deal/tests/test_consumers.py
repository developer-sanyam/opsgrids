"""`crm.lead.conversion_aborted`, consumed to soft-delete an aborted attempt's orphan deal.

The handler re-derives the attempt's deal id from `(lead_id, generation)` and never acts on the id
the payload names; it deletes only a live deal that `services.is_pristine_lead_deal` proves is the
attempt's own and untouched since. `log_only` (the default) evaluates and writes nothing, not even
the ledger row; `off` ignores the event. The handler is called directly (never through the AMQP
loop); reads go back through the test's tenant-bound `session` after `expire_all()`.
"""

from __future__ import annotations

import copy
import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app import consumers, domain, services
from app.config import Settings, settings
from app.models import AuditLog, Deal, OutboxEvent, ProcessedEvent
from app.schemas import DealPatch
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from tests.helpers import aborted_envelope, ctx_for

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumer's `session_scope` needs the process engine; the app lifespan does this in
    production."""
    init_engine(settings())
    yield
    await dispose_engine()


@pytest.fixture
def mode(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def _set(value: str) -> None:
        cfg = settings().model_copy(update={"crm_conversion_cleanup_mode": value})
        monkeypatch.setattr(consumers, "settings", lambda: cfg)

    _set("on")
    return _set


async def _seed_orphan(
    session: AsyncSession,
    org_id: uuid.UUID,
    lead_id: uuid.UUID,
    generation: int,
    *,
    acting_as: str | None = "lead",
    deal_id: uuid.UUID | None = None,
) -> uuid.UUID:
    deal_id = deal_id or domain.conversion_ids(lead_id, generation)[2]
    async with session.begin():
        await services.create_deal(
            session,
            services.lead_conversion_ctx(org_id, None)
            if acting_as
            else ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG),
            name=f"Orphan {uuid.uuid4()}",
            company_id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
            value_minor=0,
            close_date=date(2026, 12, 31),
            deal_id=deal_id,
            acting_as=acting_as,
        )
    return deal_id


async def _state(session: AsyncSession, deal_id: uuid.UUID) -> dict[str, Any]:
    session.expire_all()
    async with session.begin():
        deal = (await session.execute(select(Deal).where(Deal.id == deal_id))).scalar_one_or_none()
        audit = list(
            (
                await session.execute(
                    select(AuditLog.action, AuditLog.acting_as, AuditLog.reason).where(
                        AuditLog.entity_id == deal_id
                    )
                )
            ).all()
        )
        outbox = list(
            (
                await session.execute(
                    select(OutboxEvent.event_type).where(OutboxEvent.aggregate_id == deal_id)
                )
            ).scalars()
        )
        ledger = (await session.execute(select(func.count()).select_from(ProcessedEvent))).scalar()
    return {
        "live": deal is not None and deal.deleted_at is None,
        "audit": audit,
        "outbox": outbox,
        "ledger": ledger,
    }


# ------------------------------------------------------------------ ignore rules


def _without(key: str):  # type: ignore[no-untyped-def]
    def mutate(envelope: dict) -> None:
        envelope["payload"].pop(key)

    return mutate


def _set_generation(value: object):  # type: ignore[no-untyped-def]
    def mutate(envelope: dict) -> None:
        envelope["payload"]["generation"] = value

    return mutate


def _org_mismatch(envelope: dict) -> None:
    envelope["organization_id"] = str(uuid.uuid4())


def _unknown_reason(envelope: dict) -> None:
    envelope["payload"]["reason"] = "cancelled"


def _bad_uuid(envelope: dict) -> None:
    envelope["payload"]["lead_id"] = "not-a-uuid"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (_without("generation"), "no_generation"),
        (_without("claim_id"), "no_claim_id"),
        (_set_generation("1"), "bad_generation"),
        (_set_generation(1.5), "bad_generation"),
        (_set_generation(-1), "bad_generation"),
        (_set_generation(True), "bad_generation"),
        (_set_generation(None), "bad_generation"),
        (_org_mismatch, "org_mismatch"),
        (_unknown_reason, "unknown_reason"),
        (_bad_uuid, "bad_uuid"),
    ],
)
async def test_an_event_breaking_a_rule_is_ignored_and_acknowledged(
    session: AsyncSession, org_id: uuid.UUID, mode, mutate, code: str
) -> None:
    lead_id = uuid.uuid4()
    deal_id = await _seed_orphan(session, org_id, lead_id, 1)
    envelope = aborted_envelope(organization_id=org_id, lead_id=lead_id, generation=1)
    mutate(envelope)

    outcome = await consumers.run_conversion_cleanup(envelope)

    assert outcome.ignored == code
    state = await _state(session, deal_id)
    assert state["live"] is True
    assert [a[0] for a in state["audit"]] == ["deal.created"]
    assert state["ledger"] == 0


async def test_an_envelope_whose_org_disagrees_with_its_payload_touches_neither_tenant(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID, mode
) -> None:
    other = uuid.uuid4()
    lead_id = uuid.uuid4()
    mine = await _seed_orphan(session, org_id, lead_id, 1)
    theirs = await _seed_orphan(admin_session, other, uuid.uuid4(), 1)
    envelope = aborted_envelope(organization_id=org_id, lead_id=lead_id, generation=1)
    envelope["organization_id"] = str(other)

    outcome = await consumers.run_conversion_cleanup(envelope)

    assert outcome.ignored == "org_mismatch"
    assert (await _state(session, mine))["live"] is True
    async with admin_session.begin():
        row = await admin_session.get(Deal, theirs)
        assert row is not None and row.deleted_at is None


async def test_mode_off_acknowledges_and_changes_nothing(
    session: AsyncSession, org_id: uuid.UUID, mode
) -> None:
    mode("off")
    lead_id = uuid.uuid4()
    deal_id = await _seed_orphan(session, org_id, lead_id, 0)
    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )
    assert outcome.ignored == "mode_off"
    state = await _state(session, deal_id)
    assert state["live"] is True and state["ledger"] == 0


# ------------------------------------------------------------------ log_only


async def test_log_only_logs_would_apply_and_writes_nothing_not_even_the_ledger(
    session: AsyncSession, org_id: uuid.UUID, mode
) -> None:
    mode("log_only")
    lead_id = uuid.uuid4()
    deal_id = await _seed_orphan(session, org_id, lead_id, 1)

    with capture_logs() as logs:
        outcome = await consumers.run_conversion_cleanup(
            aborted_envelope(organization_id=org_id, lead_id=lead_id, generation=1)
        )

    assert outcome.deal == "would_delete"
    [line] = [e for e in logs if e["event"] == "crm.conversion_cleanup.would_apply"]
    assert set(line) <= {
        "event",
        "log_level",
        "event_id",
        "organization_id",
        "lead_id",
        "generation",
        "deal_id",
        "deal",
        "mode",
    }
    state = await _state(session, deal_id)
    assert state["live"] is True
    assert [a[0] for a in state["audit"]] == ["deal.created"]
    assert state["outbox"] == ["crm.pipeline.deal.created"]
    assert state["ledger"] == 0


# ------------------------------------------------------------------ on


async def test_a_pristine_orphan_deal_is_soft_deleted_with_an_audit_row_naming_the_consumer(
    session: AsyncSession, org_id: uuid.UUID, mode
) -> None:
    lead_id = uuid.uuid4()
    deal_id = await _seed_orphan(session, org_id, lead_id, 1)

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id, generation=1)
    )

    assert outcome.deal == "deleted"
    state = await _state(session, deal_id)
    assert state["live"] is False
    deleted = [a for a in state["audit"] if a[0] == "deal.deleted"]
    assert deleted == [
        ("deal.deleted", "crm-conversion-cleanup", f"lead_conversion_aborted:{lead_id}:g1")
    ]
    assert state["outbox"].count("crm.pipeline.deal.deleted") == 1
    assert "crm.pipeline.deal.stage_changed" not in state["outbox"]
    assert state["ledger"] == 1


async def _touch_with_activity(session: AsyncSession, org_id: uuid.UUID, deal_id: uuid.UUID):  # type: ignore[no-untyped-def]
    await services.log_activity(
        session,
        ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG),
        activity_type="note",
        subject="Called",
        owner_id=uuid.uuid4(),
        deal_id=deal_id,
    )


async def _move(session: AsyncSession, org_id: uuid.UUID, deal_id: uuid.UUID):  # type: ignore[no-untyped-def]
    deal = await session.get(Deal, deal_id)
    await services.move_stage(
        session, ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG), deal, new_stage="proposal"
    )


async def _edit(session: AsyncSession, org_id: uuid.UUID, deal_id: uuid.UUID):  # type: ignore[no-untyped-def]
    deal = await session.get(Deal, deal_id)
    await services.update_deal(
        session,
        ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG),
        deal,
        DealPatch(value_minor=10),
    )


@pytest.mark.parametrize("touch", [_touch_with_activity, _move, _edit])
async def test_a_touched_deal_is_kept(
    session: AsyncSession, org_id: uuid.UUID, mode, touch
) -> None:
    lead_id = uuid.uuid4()
    deal_id = await _seed_orphan(session, org_id, lead_id, 0)
    async with session.begin():
        await touch(session, org_id, deal_id)

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )

    assert outcome.deal == "touched"
    state = await _state(session, deal_id)
    assert state["live"] is True
    assert state["ledger"] == 1


async def test_a_deal_not_created_by_lead_is_kept(
    session: AsyncSession, org_id: uuid.UUID, mode
) -> None:
    lead_id = uuid.uuid4()
    deal_id = await _seed_orphan(session, org_id, lead_id, 0, acting_as=None)
    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )
    assert outcome.deal == "not_created_by_attempt"
    assert (await _state(session, deal_id))["live"] is True


async def test_an_absent_deal_is_a_no_op(session: AsyncSession, org_id: uuid.UUID, mode) -> None:
    outcome = await consumers.run_conversion_cleanup(aborted_envelope(organization_id=org_id))
    assert outcome.deal == "absent"
    session.expire_all()
    async with session.begin():
        assert (
            await session.execute(select(func.count()).select_from(ProcessedEvent))
        ).scalar() == 1


async def test_an_already_deleted_deal_is_a_no_op(
    session: AsyncSession, org_id: uuid.UUID, mode
) -> None:
    lead_id = uuid.uuid4()
    deal_id = await _seed_orphan(session, org_id, lead_id, 0)
    async with session.begin():
        deal = await session.get(Deal, deal_id)
        await services.delete_deal(
            session, ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG), deal
        )
    before = await _state(session, deal_id)

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )

    assert outcome.deal == "already_deleted"
    after = await _state(session, deal_id)
    assert after["audit"] == before["audit"]
    assert after["outbox"] == before["outbox"]


async def test_a_live_deal_at_the_payloads_id_but_not_the_derived_id_is_never_touched(
    session: AsyncSession, org_id: uuid.UUID, mode
) -> None:
    lead_id = uuid.uuid4()
    elsewhere = await _seed_orphan(session, org_id, lead_id, 0, deal_id=uuid.uuid4())
    envelope = aborted_envelope(
        organization_id=org_id, lead_id=lead_id, returned={"deal_id": elsewhere}
    )

    with capture_logs() as logs:
        outcome = await consumers.run_conversion_cleanup(envelope)

    assert outcome.deal == "absent"
    assert any(e["event"] == "crm.conversion_cleanup.not_derived_id" for e in logs)
    assert (await _state(session, elsewhere))["live"] is True


async def test_the_derived_id_at_the_events_generation_is_used_not_the_current_one(
    session: AsyncSession, org_id: uuid.UUID, mode
) -> None:
    lead_id = uuid.uuid4()
    gen0 = await _seed_orphan(session, org_id, lead_id, 0)
    gen1 = await _seed_orphan(session, org_id, lead_id, 1)

    await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id, generation=1)
    )

    assert (await _state(session, gen0))["live"] is True
    assert (await _state(session, gen1))["live"] is False


async def test_redelivery_is_idempotent(session: AsyncSession, org_id: uuid.UUID, mode) -> None:
    lead_id = uuid.uuid4()
    deal_id = await _seed_orphan(session, org_id, lead_id, 0)
    envelope = aborted_envelope(organization_id=org_id, lead_id=lead_id)

    first = await consumers.run_conversion_cleanup(envelope)
    second = await consumers.run_conversion_cleanup(copy.deepcopy(envelope))

    assert first.deal == "deleted"
    assert second.ignored == "already_processed"
    state = await _state(session, deal_id)
    assert [a[0] for a in state["audit"]].count("deal.deleted") == 1
    assert state["outbox"].count("crm.pipeline.deal.deleted") == 1
    assert state["ledger"] == 1


async def test_a_new_event_id_for_the_same_lead_and_generation_changes_nothing(
    session: AsyncSession, org_id: uuid.UUID, mode
) -> None:
    lead_id = uuid.uuid4()
    deal_id = await _seed_orphan(session, org_id, lead_id, 0)
    await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )
    before = await _state(session, deal_id)

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )

    assert outcome.deal == "already_deleted"
    after = await _state(session, deal_id)
    assert after["audit"] == before["audit"]
    assert after["outbox"] == before["outbox"]


async def test_another_organizations_deal_at_the_derived_id_is_untouched(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID, mode
) -> None:
    other = uuid.uuid4()
    lead_id = uuid.uuid4()
    theirs = await _seed_orphan(admin_session, other, lead_id, 0)

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )

    assert outcome.deal == "absent"
    async with admin_session.begin():
        row = await admin_session.get(Deal, theirs)
        assert row is not None and row.deleted_at is None


async def test_a_handler_error_leaves_no_ledger_row(
    session: AsyncSession, org_id: uuid.UUID, mode, monkeypatch: pytest.MonkeyPatch
) -> None:
    lead_id = uuid.uuid4()
    deal_id = await _seed_orphan(session, org_id, lead_id, 0)

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("delete failed")

    monkeypatch.setattr(services, "delete_deal", boom)
    with pytest.raises(RuntimeError):
        await consumers.deal_conversion_aborted(
            aborted_envelope(organization_id=org_id, lead_id=lead_id)
        )

    state = await _state(session, deal_id)
    assert state["live"] is True
    assert state["ledger"] == 0


def test_the_consumer_binds_exactly_the_aborted_event() -> None:
    assert list(consumers.consumer._handlers) == ["crm.lead.conversion_aborted"]


def test_the_default_mode_is_log_only() -> None:
    assert Settings.model_fields["crm_conversion_cleanup_mode"].default == "log_only"
