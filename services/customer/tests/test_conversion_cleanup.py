"""`crm.lead.conversion_aborted`, consumed to soft-delete an aborted attempt's orphan contact and
company, in three phases: plan (a read-only transaction), probe deal's references (no transaction
open), act (`on` only: a new transaction that re-verifies everything under the row locks).

A record is deletable only when its id is the one re-derived from `(lead_id, generation)`, it is
live, its audit history is exactly its own creation by lead, and nothing references it: the
contact has no activity and no deal names it; the company has no other live contact and no deal
or activity names it. The handler is called directly with a fake probe; reads go back through the
test's tenant-bound `session` after `expire_all()`.
"""

from __future__ import annotations

import copy
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import consumers, domain, selectors, services
from app.clients import DealReferences, ReferencesProbeUnavailable
from app.config import settings
from app.models import AuditLog, Company, Contact, OutboxEvent, ProcessedEvent
from app.schemas import CompanyPatch
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from tests.helpers import aborted_envelope, ctx_for

pytestmark = pytest.mark.integration

CLEANUP = "crm-conversion-cleanup"
ZERO = DealReferences(0, 0, 0, 0)


@pytest.fixture(autouse=True)
async def _engine() -> Any:
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


class Probe:
    """Stands in for `clients.deal_references`: records each call and answers `result`."""

    def __init__(self, result: DealReferences | Exception = ZERO) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []
        self.before: Any = None

    async def __call__(self, settings: Any, organization_id: uuid.UUID, **kwargs: Any) -> Any:
        self.calls.append({"organization_id": organization_id, **kwargs})
        if self.before is not None:
            await self.before()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Probe:
    fake = Probe()
    monkeypatch.setattr(consumers, "deal_references", fake)
    return fake


async def _owner(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    async with session.begin():
        owner = await services.create_owner(
            session,
            ctx_for(org_id, "crm.book.owner.write", Scope.ORG),
            full_name="Priya Rep",
            email=f"rep-{uuid.uuid4()}@example.com",
        )
    return owner.id


async def _attempt(
    session: AsyncSession,
    org_id: uuid.UUID,
    lead_id: uuid.UUID,
    generation: int = 0,
    *,
    email: str | None = "priya@stellar.example",
    name: str = "Stellar Robotics",
    phone: str | None = None,
    owner_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """What a conversion attempt at `(lead_id, generation)` leaves in the book."""
    owner_id = owner_id or await _owner(session, org_id)
    company_id, contact_id, _ = domain.conversion_ids(lead_id, generation)
    async with session.begin():
        company, contact = await services.ensure_company_and_contact_for_lead(
            session,
            services.lead_conversion_ctx(org_id, None),
            company_id=company_id,
            contact_id=contact_id,
            company_name=name,
            owner_id=owner_id,
            contact_full_name="Priya Sharma",
            contact_email=email,
            contact_raw_phone=phone,
        )
    return company.id, contact.id if contact else None


async def _live(session: AsyncSession, model: Any, entity_id: uuid.UUID | None) -> bool:
    session.expire_all()
    async with session.begin():
        row = (
            await session.execute(select(model).where(model.id == entity_id))
        ).scalar_one_or_none()
    return row is not None and row.deleted_at is None


async def _counts(session: AsyncSession) -> tuple[int, int, int]:
    """(audit rows not a creation, outbox rows not a creation, ledger rows)"""
    session.expire_all()
    async with session.begin():
        audit = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action.not_in(["company.created", "contact.created", "owner.created"])
                )
            )
        ).scalar()
        outbox = (
            await session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_type.not_like("%.created"))
            )
        ).scalar()
        ledger = (await session.execute(select(func.count()).select_from(ProcessedEvent))).scalar()
    return int(audit or 0), int(outbox or 0), int(ledger or 0)


# ------------------------------------------------------------------ ignore rules


def _without(key: str):  # type: ignore[no-untyped-def]
    return lambda envelope: envelope["payload"].pop(key)


def _generation(value: object):  # type: ignore[no-untyped-def]
    return lambda envelope: envelope["payload"].__setitem__("generation", value)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (_without("generation"), "no_generation"),
        (_generation("1"), "bad_generation"),
        (_generation(1.5), "bad_generation"),
        (_generation(-1), "bad_generation"),
        (_generation(True), "bad_generation"),
        (_generation(None), "bad_generation"),
        (_without("claim_id"), "no_claim_id"),
        (lambda e: e["payload"].__setitem__("company_id", "nope"), "bad_uuid"),
        (lambda e: e.__setitem__("organization_id", str(uuid.uuid4())), "org_mismatch"),
        (lambda e: e["payload"].__setitem__("reason", "cancelled"), "unknown_reason"),
        (None, "mode_off"),
    ],
)
async def test_customer_ignores_every_event_the_deal_handler_ignores(
    session: AsyncSession,
    admin_session: AsyncSession,
    org_id: uuid.UUID,
    mode,
    probe: Probe,
    mutate,
    code: str,
) -> None:
    probe.result = AssertionError("the probe must not be called")
    lead_id = uuid.uuid4()
    mine = await _attempt(session, org_id, lead_id)
    other = uuid.uuid4()
    theirs = await _attempt(admin_session, other, uuid.uuid4())
    envelope = aborted_envelope(organization_id=org_id, lead_id=lead_id)
    if mutate is None:
        mode("off")
    else:
        mutate(envelope)

    outcome = await consumers.run_conversion_cleanup(envelope)

    assert outcome.ignored == code
    assert probe.calls == []
    assert await _live(session, Company, mine[0]) and await _live(session, Contact, mine[1])
    async with admin_session.begin():
        assert (await admin_session.get(Company, theirs[0])).deleted_at is None  # type: ignore[union-attr]
    assert await _counts(session) == (0, 0, 0)


# ------------------------------------------------------------------ phases


async def test_the_probe_runs_with_no_transaction_open(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_scopes = 0
    inner = consumers._session_scope

    @asynccontextmanager
    async def counting(organization_id: uuid.UUID):  # type: ignore[no-untyped-def]
        nonlocal open_scopes
        open_scopes += 1
        try:
            async with inner(organization_id) as s:
                yield s
        finally:
            open_scopes -= 1

    async def assert_closed() -> None:
        assert open_scopes == 0

    monkeypatch.setattr(consumers, "_session_scope", counting)
    probe.before = assert_closed
    lead_id = uuid.uuid4()
    await _attempt(session, org_id, lead_id)

    await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )

    assert len(probe.calls) == 1


@pytest.mark.parametrize("why", ["timeout", "connection", "500", "403"])
async def test_a_probe_failure_raises_so_the_message_is_redelivered(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe, why: str
) -> None:
    probe.result = ReferencesProbeUnavailable(why)
    lead_id = uuid.uuid4()
    company_id, contact_id = await _attempt(session, org_id, lead_id)

    with pytest.raises(ReferencesProbeUnavailable):
        await consumers.customer_conversion_aborted(
            aborted_envelope(organization_id=org_id, lead_id=lead_id)
        )

    assert await _live(session, Company, company_id) and await _live(session, Contact, contact_id)
    assert await _counts(session) == (0, 0, 0)


async def test_log_only_runs_the_probe_and_writes_nothing(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    mode("log_only")
    lead_id = uuid.uuid4()
    company_id, contact_id = await _attempt(session, org_id, lead_id)

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )

    assert len(probe.calls) == 1
    assert (outcome.contact, outcome.company) == ("would_delete", "would_delete")
    assert await _live(session, Company, company_id) and await _live(session, Contact, contact_id)
    assert await _counts(session) == (0, 0, 0)


async def test_off_does_not_call_the_probe(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    mode("off")
    lead_id = uuid.uuid4()
    await _attempt(session, org_id, lead_id)
    await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )
    assert probe.calls == []


async def test_nothing_to_delete_never_calls_the_probe(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    outcome = await consumers.run_conversion_cleanup(aborted_envelope(organization_id=org_id))
    assert probe.calls == []
    assert (outcome.contact, outcome.company) == ("absent", "absent")
    assert (await _counts(session))[2] == 1


async def test_the_act_phase_reverifies_under_locks(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    lead_id = uuid.uuid4()
    company_id, _contact_id = await _attempt(session, org_id, lead_id, email=None)

    async def edit_between_phases() -> None:
        async with admin_session.begin():
            company = await admin_session.get(Company, company_id)
            await services.update_company(
                admin_session,
                ctx_for(org_id, "crm.book.company.write", Scope.ORG),
                company,
                CompanyPatch(industry="Robotics"),
            )

    probe.before = edit_between_phases
    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id, with_contact=False)
    )

    assert outcome.company == "touched"
    assert await _live(session, Company, company_id)


async def test_the_probe_receives_the_derived_ids_and_the_derived_deal_as_the_exclusion(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    lead_id = uuid.uuid4()
    await _attempt(session, org_id, lead_id, 2)
    company_id, contact_id, deal_id = domain.conversion_ids(lead_id, 2)

    await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id, generation=2)
    )

    assert probe.calls == [
        {
            "organization_id": org_id,
            "company_id": company_id,
            "contact_id": contact_id,
            "exclude_deal_id": deal_id,
        }
    ]


# ------------------------------------------------------------------ provenance


async def _outbox_types(session: AsyncSession) -> list[str]:
    session.expire_all()
    async with session.begin():
        stmt = select(OutboxEvent.event_type)
        return [t for t in (await session.execute(stmt)).scalars() if t.endswith(".deleted")]


async def test_a_company_and_contact_created_by_the_attempt_are_soft_deleted_contact_first(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    lead_id = uuid.uuid4()
    company_id, contact_id = await _attempt(session, org_id, lead_id, 1)

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id, generation=1)
    )

    assert (outcome.contact, outcome.company) == ("deleted", "deleted")
    assert not await _live(session, Contact, contact_id)
    assert not await _live(session, Company, company_id)
    async with session.begin():
        rows = (
            await session.execute(
                select(AuditLog.action, AuditLog.acting_as, AuditLog.reason).where(
                    AuditLog.action.like("%.deleted")
                )
            )
        ).all()
    reason = f"lead_conversion_aborted:{lead_id}:g1"
    assert sorted(rows) == [
        ("company.deleted", CLEANUP, reason),
        ("contact.deleted", CLEANUP, reason),
    ]
    # One transaction stamps both rows with the same `now()`, so the order is not observable here;
    # contact-first is enforced by `delete_company`, which refuses while a live contact remains.
    assert sorted(await _outbox_types(session)) == [
        "crm.book.company.deleted",
        "crm.book.contact.deleted",
    ]
    assert (await _counts(session))[2] == 1


async def test_a_company_reused_by_name_is_kept(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    owner = await _owner(session, org_id)
    lead_id = uuid.uuid4()
    company_id, _ = await _attempt(session, org_id, lead_id, email=None, owner_id=owner)
    await _attempt(session, org_id, uuid.uuid4(), email=None, owner_id=owner)  # adopts by name

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id, with_contact=False)
    )

    assert outcome.company == "adopted"
    assert await _live(session, Company, company_id)


@pytest.mark.parametrize(
    ("first_phone", "second_phone"),
    [(None, None), (None, "+919876543210"), ("+919876543210", "+919988776655")],
)
async def test_a_contact_reused_by_email_is_kept(
    session: AsyncSession,
    org_id: uuid.UUID,
    mode,
    probe: Probe,
    first_phone: str | None,
    second_phone: str | None,
) -> None:
    owner = await _owner(session, org_id)
    lead_id = uuid.uuid4()
    _, contact_id = await _attempt(session, org_id, lead_id, phone=first_phone, owner_id=owner)
    await _attempt(
        session, org_id, uuid.uuid4(), name="Other Co", phone=second_phone, owner_id=owner
    )

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )

    assert outcome.contact == "adopted"
    assert outcome.company == "other_contacts"
    assert await _live(session, Contact, contact_id)


async def test_a_company_edited_after_creation_is_kept(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    lead_id = uuid.uuid4()
    company_id, _ = await _attempt(session, org_id, lead_id, email=None)
    async with session.begin():
        company = await selectors.get_company(session, org_id, company_id)
        await services.update_company(
            session,
            ctx_for(org_id, "crm.book.company.write", Scope.ORG),
            company,
            CompanyPatch(industry="Robotics"),
        )

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id, with_contact=False)
    )

    assert outcome.company == "touched"
    assert await _live(session, Company, company_id)


async def test_a_contact_with_activity_is_kept(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    lead_id = uuid.uuid4()
    company_id, contact_id = await _attempt(session, org_id, lead_id)
    async with session.begin():
        contact = await selectors.get_contact(session, org_id, contact_id)
        await services.touch_contact_activity(
            session, services.service_ctx(org_id), contact, at=datetime.now(UTC)
        )

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )

    assert outcome.contact == "touched"
    assert outcome.company == "other_contacts"
    assert await _live(session, Contact, contact_id) and await _live(session, Company, company_id)


async def test_a_company_with_another_live_contact_is_kept(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    lead_id = uuid.uuid4()
    owner = await _owner(session, org_id)
    company_id, contact_id = await _attempt(session, org_id, lead_id, owner_id=owner)
    async with session.begin():
        await services.create_contact(
            session,
            ctx_for(org_id, "crm.book.contact.write", Scope.ORG),
            full_name="Second Person",
            email="second@stellar.example",
            company_id=company_id,
            owner_id=owner,
        )

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )

    assert outcome.contact == "deleted"
    assert outcome.company == "other_contacts"
    assert not await _live(session, Contact, contact_id)
    assert await _live(session, Company, company_id)


@pytest.mark.parametrize(
    "refs",
    [DealReferences(1, 0, 0, 0), DealReferences(0, 0, 1, 0)],
    ids=["a-live-deal", "an-activity"],
)
async def test_a_company_referenced_in_deal_is_kept(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe, refs: DealReferences
) -> None:
    """Also the DD-20 case: a touched orphan deal that deal keeps is counted by the probe."""
    probe.result = refs
    lead_id = uuid.uuid4()
    company_id, _contact_id = await _attempt(session, org_id, lead_id)

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )

    assert outcome.company == "referenced"
    assert outcome.contact == "deleted"
    assert await _live(session, Company, company_id)


@pytest.mark.parametrize(
    "refs", [DealReferences(0, 1, 0, 0), DealReferences(0, 0, 0, 1)], ids=["deal", "activity"]
)
async def test_a_contact_referenced_by_a_kept_deal_is_kept(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe, refs: DealReferences
) -> None:
    """DP-2.4-4: the contact needs its own probe counts at zero, and a company whose derived
    contact is kept is kept too (`delete_company` would refuse it)."""
    probe.result = refs
    lead_id = uuid.uuid4()
    company_id, contact_id = await _attempt(session, org_id, lead_id)

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )

    assert outcome.contact == "referenced"
    assert outcome.company == "other_contacts"
    assert await _live(session, Contact, contact_id) and await _live(session, Company, company_id)


async def test_an_id_that_is_not_the_derived_id_is_never_touched(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    """The payload names records at other ids (what customer *returned*): only the derived ids
    are considered, and here nothing lives at them."""
    company_id, contact_id = await _attempt(session, org_id, uuid.uuid4())
    envelope = aborted_envelope(
        organization_id=org_id,
        returned={"company_id": company_id, "contact_id": contact_id},  # type: ignore[dict-item]
    )

    outcome = await consumers.run_conversion_cleanup(envelope)

    assert (outcome.contact, outcome.company) == ("absent", "absent")
    assert await _live(session, Company, company_id) and await _live(session, Contact, contact_id)


async def test_absent_and_already_deleted_are_no_ops(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    lead_id = uuid.uuid4()
    await _attempt(session, org_id, lead_id)
    envelope = aborted_envelope(organization_id=org_id, lead_id=lead_id)
    await consumers.run_conversion_cleanup(envelope)
    before = await _counts(session)

    again = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )

    assert (again.contact, again.company) == ("already_deleted", "already_deleted")
    after = await _counts(session)
    assert after[:2] == before[:2]


async def test_a_lead_without_an_email_deletes_only_the_company(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    lead_id = uuid.uuid4()
    _company_id, contact_id = await _attempt(session, org_id, lead_id, email=None)
    assert contact_id is None

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id, with_contact=False)
    )

    assert (outcome.contact, outcome.company) == (None, "deleted")
    assert probe.calls[0]["contact_id"] is None


async def test_redelivery_is_idempotent(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    lead_id = uuid.uuid4()
    await _attempt(session, org_id, lead_id)
    envelope = aborted_envelope(organization_id=org_id, lead_id=lead_id)

    await consumers.run_conversion_cleanup(envelope)
    second = await consumers.run_conversion_cleanup(copy.deepcopy(envelope))

    assert second.ignored == "already_processed"
    assert await _counts(session) == (2, 2, 1)


async def test_another_organizations_rows_at_the_derived_ids_are_untouched(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe
) -> None:
    other = uuid.uuid4()
    lead_id = uuid.uuid4()
    company_id, contact_id = await _attempt(admin_session, other, lead_id)

    outcome = await consumers.run_conversion_cleanup(
        aborted_envelope(organization_id=org_id, lead_id=lead_id)
    )

    assert (outcome.contact, outcome.company) == ("absent", "absent")
    async with admin_session.begin():
        assert (await admin_session.get(Company, company_id)).deleted_at is None  # type: ignore[union-attr]
        assert (await admin_session.get(Contact, contact_id)).deleted_at is None  # type: ignore[union-attr]


async def test_a_handler_error_leaves_no_ledger_row(
    session: AsyncSession, org_id: uuid.UUID, mode, probe: Probe, monkeypatch: pytest.MonkeyPatch
) -> None:
    lead_id = uuid.uuid4()
    company_id, contact_id = await _attempt(session, org_id, lead_id)

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("delete failed")

    monkeypatch.setattr(services, "delete_company", boom)
    with pytest.raises(RuntimeError):
        await consumers.customer_conversion_aborted(
            aborted_envelope(organization_id=org_id, lead_id=lead_id)
        )

    assert await _live(session, Contact, contact_id) and await _live(session, Company, company_id)
    assert await _counts(session) == (0, 0, 0)


def test_the_consumer_binds_the_aborted_event_beside_the_activity_event() -> None:
    assert sorted(consumers.consumer._handlers) == [
        "crm.lead.conversion_aborted",
        "crm.pipeline.activity.logged",
    ]
