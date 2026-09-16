"""Cross-service tests: does the slice 2 loop attendance <-> approval actually close.

Neither service's own suite can answer this. Attendance's suite (`test_regularization.py`,
`test_consumers.py`) only ever inspects its own outbox rows and feeds its own consumer a
hand-built envelope (`tests/helpers.py::approval_decided_envelope`). Approval's suite
(`test_routing.py`, `test_decisions.py`) only ever feeds its own consumer a hand-built envelope
shaped the way *approval* assumes attendance's payload looks
(`tests/helpers.py::regularization_requested_envelope` / `regularization_applied_envelope`). Both
are legitimate unit tests and neither proves attendance's real publish path actually satisfies
approval's real consumer, or the reverse.

**Why subprocess for attendance, in-process for approval.** CLAUDE.md: "`import app` is
ambiguous across services... one process can only ever hold one." This file's own pytest process
holds approval's `app` (`tests/conftest.py`'s `sys.path.insert`); attendance's `app` never enters
it. Every attendance-side write and every attendance-side consume runs in a separate Python
process, `services/attendance/tests/regularization_cli.py`, invoked as a one-shot subcommand the
same way slice 1's `test_cross_service_contract.py` runs `emit_events.py` and `bump_employee.py`.
Approval's own logic (`open_regularization_request`'s consumer, `decide_request` over its real
HTTP endpoint, `apply_regularization_outcome`'s consumer) runs directly in this process, the same
as every other approval test module.

Every envelope that crosses the boundary is built from a **real** outbox row — attendance's via
`tests.helpers.envelope_from_outbox_row` (parses `regularization_cli.py`'s printed JSON),
approval's via `platform_core.events.build_envelope` applied straight to the real `OutboxEvent`
ORM row `decide_request` and `open_regularization_request` just wrote — never a hand-written
dict. Only the transport differs from production (a direct subprocess/function call standing in
for the broker round trip); the shape never does.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.config import settings as approval_settings
from app.consumers import on_regularization_applied, on_regularization_requested
from app.main import app
from app.models import ApprovalRequest, ApprovalStep, OutboxEvent
from app.services import EVT_REQUEST_DECIDED
from platform_core.auth import Principal, get_principal
from platform_core.db import TENANT_GUC, dispose_engine, init_engine
from platform_core.events import build_envelope
from tests.helpers import envelope_from_outbox_row, principal_for, seed_person

pytestmark = pytest.mark.integration

ATTENDANCE_DIR = Path(__file__).resolve().parents[2] / "attendance"


# --------------------------------------------------------------------- subprocess plumbing


def _run_attendance_cli(
    args: list[str], *, stdin: str | None = None, timeout: float = 60.0
) -> list[dict[str, Any]]:
    """Run one `regularization_cli.py` subcommand and parse its stdout as newline-delimited
    JSON — the same shape `test_cross_service_contract.py`'s `_run_employee_script` uses."""
    result = subprocess.run(  # noqa: S603  (fixed argv, sys.executable, no shell — a sibling
        # service's own test helper, the same trust boundary as importing its code would be)
        [sys.executable, "-m", "tests.regularization_cli", *args],
        cwd=str(ATTENDANCE_DIR),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, (
        f"regularization_cli {args} exited {result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _raise_request(
    org_id: uuid.UUID,
    employee_id: uuid.UUID,
    *,
    location: str | None = None,
    requested_status: str = "half_day",
    reason: str = "left early",
) -> dict[str, Any]:
    """Attendance's real `check_in` + `request_regularization`. Returns the printed
    `{attendance_id, regularization_request_id, employee_id, requested_row}`."""
    args = [
        "raise",
        "--org",
        str(org_id),
        "--employee",
        str(employee_id),
        "--requested-status",
        requested_status,
        "--reason",
        reason,
    ]
    if location is not None:
        args += ["--location", location]
    return _run_attendance_cli(args)[0]


def _hr_override(
    org_id: uuid.UUID, attendance_id: uuid.UUID, *, to: str, reason: str = "HR override"
) -> dict[str, Any]:
    """Attendance's real `regularize_attendance`, ORG-scope, standing in for HR correcting the
    day directly while a regularization request is in flight."""
    return _run_attendance_cli(
        [
            "override",
            "--org",
            str(org_id),
            "--attendance-id",
            str(attendance_id),
            "--to",
            to,
            "--reason",
            reason,
        ]
    )[0]


def _apply_decisions(envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    """Feeds one or more `core.approval.request.decided` envelopes through attendance's real
    `on_approval_request_decided`, in order, inside one subprocess. Returns the final
    `{regularization, attendance, applied_rows}` state (attendance's own read model, not a raw
    table peek — see `regularization_cli.py`'s `apply-decision` docstring)."""
    return _run_attendance_cli(["apply-decision"], stdin=json.dumps(envelopes))[0]


# --------------------------------------------------------------------- approval-side plumbing


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumers open their own `session_scope`, which needs the process engine initialised
    — the app lifespan does this in production; here the fixture stands in for it."""
    init_engine(approval_settings())
    yield
    await dispose_engine()


def as_principal(principal: Principal) -> Any:
    class _Ctx:
        def __enter__(self) -> None:
            app.dependency_overrides[get_principal] = lambda: principal

        def __exit__(self, *exc: object) -> None:
            app.dependency_overrides.pop(get_principal, None)

    return _Ctx()


async def _decided_outbox_row(
    session: AsyncSession, org_id: uuid.UUID, approval_request_id: uuid.UUID
) -> OutboxEvent:
    async with session.begin():
        return (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.organization_id == org_id,
                        OutboxEvent.aggregate_id == approval_request_id,
                        OutboxEvent.event_type == EVT_REQUEST_DECIDED,
                    )
                )
            )
            .scalars()
            .one()
        )


async def _seed_manager_and_requester(
    session: AsyncSession, org_id: uuid.UUID, *, manager: uuid.UUID, requester: uuid.UUID
) -> None:
    async with session.begin():
        await seed_person(session, org_id, manager, display_name="Manager")
        await seed_person(
            session, org_id, requester, reporting_manager_id=manager, display_name="Requester"
        )


async def _open_via_consumer(
    session: AsyncSession, org_id: uuid.UUID, requested_row: dict[str, Any]
) -> ApprovalRequest:
    envelope = envelope_from_outbox_row(requested_row, producer="attendance")
    await on_regularization_requested(envelope)
    async with session.begin():
        return (
            (
                await session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.organization_id == org_id,
                        ApprovalRequest.source_ref
                        == uuid.UUID(requested_row["payload"]["regularization_request_id"]),
                    )
                )
            )
            .scalars()
            .one()
        )


# =========================================================== 1 & 2. the full round trip


class TestFullRoundTripApprove:
    async def test_a_manager_approving_regularizes_the_day_and_both_sides_settle(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        await _seed_manager_and_requester(session, org_id, manager=manager, requester=requester)

        raised = _raise_request(
            org_id, requester, location="MUM", requested_status="half_day", reason="left early"
        )
        approval_request = await _open_via_consumer(session, org_id, raised["requested_row"])
        request_id = approval_request.id
        assert approval_request.status == "pending"
        assert approval_request.requester_employee_id == requester

        async with session.begin():
            step = (
                (
                    await session.execute(
                        select(ApprovalStep).where(
                            ApprovalStep.organization_id == org_id,
                            ApprovalStep.request_id == request_id,
                            ApprovalStep.outcome == "open",
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert step.approver_employee_id == manager

        # The manager decides, over approval's real endpoint.
        with as_principal(principal_for(org_id, manager, ["core.approval.request.decide:OWN"])):
            decision = await client.post(
                f"/api/v1/approvals/requests/{request_id}/decision",
                json={"outcome": "approve"},
            )
        assert decision.status_code == 200
        assert decision.json()["status"] == "approved"

        decided_row = await _decided_outbox_row(session, org_id, request_id)
        decided_envelope = build_envelope(decided_row, producer="approval")

        applied = _apply_decisions([decided_envelope])
        # The attendance record's status actually changed.
        assert applied["attendance"]["status"] == "half_day"
        assert applied["regularization"]["status"] == "approved"
        assert applied["regularization"]["failure_reason"] is None
        assert len(applied["applied_rows"]) == 1
        assert applied["applied_rows"][0]["payload"]["outcome"] == "applied"

        applied_envelope = envelope_from_outbox_row(
            applied["applied_rows"][0], producer="attendance"
        )
        await on_regularization_applied(applied_envelope)

        async with session.begin():
            await session.refresh(approval_request)
        assert approval_request.application_state == "applied"
        assert approval_request.applied_at is not None

        # The requester's own view shows the terminal state.
        with as_principal(principal_for(org_id, requester, ["core.approval.request.read:OWN"])):
            detail = await client.get(f"/api/v1/approvals/requests/{request_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["status"] == "approved"
        assert body["application_state"] == "applied"


class TestFullRoundTripReject:
    async def test_a_rejection_is_still_reported_applied_with_no_correction_to_the_day(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        await _seed_manager_and_requester(session, org_id, manager=manager, requester=requester)

        raised = _raise_request(org_id, requester, requested_status="half_day", reason="left early")
        approval_request = await _open_via_consumer(session, org_id, raised["requested_row"])
        request_id = approval_request.id

        with as_principal(principal_for(org_id, manager, ["core.approval.request.decide:OWN"])):
            decision = await client.post(
                f"/api/v1/approvals/requests/{request_id}/decision",
                json={"outcome": "reject"},
            )
        assert decision.status_code == 200
        assert decision.json()["status"] == "rejected"

        decided_row = await _decided_outbox_row(session, org_id, request_id)
        applied = _apply_decisions([build_envelope(decided_row, producer="approval")])

        # No correction: the day never moved off its punched-in status.
        assert applied["attendance"]["status"] == "present"
        assert applied["regularization"]["status"] == "rejected"
        # But the decision is still reported as applied — the event means "I applied your
        # decision", not "I corrected the day" (ADR-0006 §12).
        assert applied["applied_rows"][0]["payload"]["outcome"] == "applied"

        await on_regularization_applied(
            envelope_from_outbox_row(applied["applied_rows"][0], producer="attendance")
        )
        async with session.begin():
            await session.refresh(approval_request)
        assert approval_request.status == "rejected"
        assert approval_request.application_state == "applied"


# =========================================================== 3. the failure path


class TestFailurePath:
    async def test_an_hr_override_between_request_and_decision_makes_the_correction_fail_not_raise(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """The "approved but not applied" state the screens surface, proved end to end: the
        record moves out from under a pending request, the approver still approves it (routing
        never re-checks the day), and attendance's consumer must refuse the now-illegal
        transition without raising — a raise here would redeliver once and then dead-letter a
        fact that is not a processing error."""
        requester, manager = uuid.uuid4(), uuid.uuid4()
        await _seed_manager_and_requester(session, org_id, manager=manager, requester=requester)

        raised = _raise_request(org_id, requester, requested_status="half_day", reason="left early")
        approval_request = await _open_via_consumer(session, org_id, raised["requested_row"])
        request_id = approval_request.id

        # HR regularizes the day directly while the request is still pending — `regularized` is
        # terminal, so `present -> half_day` (the request's own ask) is no longer legal by the
        # time the decision lands.
        _hr_override(org_id, uuid.UUID(raised["attendance_id"]), to="regularized")

        with as_principal(principal_for(org_id, manager, ["core.approval.request.decide:OWN"])):
            decision = await client.post(
                f"/api/v1/approvals/requests/{request_id}/decision",
                json={"outcome": "approve"},
            )
        assert decision.status_code == 200
        assert decision.json()["status"] == "approved"  # routing itself never re-checks the day

        decided_row = await _decided_outbox_row(session, org_id, request_id)
        applied = _apply_decisions([build_envelope(decided_row, producer="approval")])

        assert applied["attendance"]["status"] == "regularized"  # untouched by the failed apply
        assert applied["regularization"]["status"] == "failed"
        assert applied["regularization"]["failure_reason"]
        assert "regularized" in applied["regularization"]["failure_reason"]
        assert applied["applied_rows"][0]["payload"]["outcome"] == "failed"
        assert (
            applied["applied_rows"][0]["payload"]["failure_reason"]
            == (applied["regularization"]["failure_reason"])
        )

        await on_regularization_applied(
            envelope_from_outbox_row(applied["applied_rows"][0], producer="attendance")
        )
        async with session.begin():
            await session.refresh(approval_request)
        assert approval_request.status == "approved"  # the decision itself stands
        assert approval_request.application_state == "failed"
        assert (
            approval_request.application_failure_reason
            == applied["regularization"]["failure_reason"]
        )


# =========================================================== 4. convergence under redelivery


class TestConvergenceAcrossTheRealRoundTrip:
    async def test_the_same_requested_event_id_delivered_twice_opens_only_one_request(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        await _seed_manager_and_requester(session, org_id, manager=manager, requester=requester)
        raised = _raise_request(org_id, requester)
        envelope = envelope_from_outbox_row(raised["requested_row"], producer="attendance")

        await on_regularization_requested(envelope)
        await on_regularization_requested(dict(envelope))  # the identical envelope, redelivered

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(ApprovalRequest).where(
                            ApprovalRequest.organization_id == org_id,
                            ApprovalRequest.source_ref
                            == uuid.UUID(raised["regularization_request_id"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1

    async def test_a_republished_requested_fact_with_a_new_event_id_still_opens_only_one_request(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """`processed_events` only catches a repeated event *id*; the DB-level `ON CONFLICT (org,
        request_type, source_ref) DO NOTHING` is what catches a producer that re-published the
        same fact under a new one (ADR-0006 §4 rule 1) — proved here against a real outbox row
        with only its envelope id perturbed, not a synthetic payload."""
        requester, manager = uuid.uuid4(), uuid.uuid4()
        await _seed_manager_and_requester(session, org_id, manager=manager, requester=requester)
        raised = _raise_request(org_id, requester)
        first = envelope_from_outbox_row(raised["requested_row"], producer="attendance")
        redelivered = {**first, "event_id": str(uuid.uuid4())}

        await on_regularization_requested(first)
        await on_regularization_requested(redelivered)

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(ApprovalRequest).where(
                            ApprovalRequest.organization_id == org_id,
                            ApprovalRequest.source_ref
                            == uuid.UUID(raised["regularization_request_id"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1

    async def test_a_decided_event_delivered_twice_to_attendance_applies_and_publishes_once(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        await _seed_manager_and_requester(session, org_id, manager=manager, requester=requester)
        raised = _raise_request(org_id, requester, requested_status="half_day")
        approval_request = await _open_via_consumer(session, org_id, raised["requested_row"])
        request_id = approval_request.id

        with as_principal(principal_for(org_id, manager, ["core.approval.request.decide:OWN"])):
            await client.post(
                f"/api/v1/approvals/requests/{request_id}/decision",
                json={"outcome": "approve"},
            )
        decided_row = await _decided_outbox_row(session, org_id, request_id)
        decided_envelope = build_envelope(decided_row, producer="approval")

        applied = _apply_decisions([decided_envelope, decided_envelope])
        assert applied["attendance"]["status"] == "half_day"
        assert applied["regularization"]["status"] == "approved"
        # Exactly one `.applied` event published, not one per delivery.
        assert len(applied["applied_rows"]) == 1

    async def test_the_applied_event_delivered_twice_to_approval_records_the_state_once(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        await _seed_manager_and_requester(session, org_id, manager=manager, requester=requester)
        raised = _raise_request(org_id, requester, requested_status="half_day")
        approval_request = await _open_via_consumer(session, org_id, raised["requested_row"])
        request_id = approval_request.id

        with as_principal(principal_for(org_id, manager, ["core.approval.request.decide:OWN"])):
            await client.post(
                f"/api/v1/approvals/requests/{request_id}/decision",
                json={"outcome": "approve"},
            )
        decided_row = await _decided_outbox_row(session, org_id, request_id)
        applied = _apply_decisions([build_envelope(decided_row, producer="approval")])
        applied_envelope = envelope_from_outbox_row(
            applied["applied_rows"][0], producer="attendance"
        )

        await on_regularization_applied(applied_envelope)
        await on_regularization_applied(dict(applied_envelope))  # redelivered, same event id

        async with session.begin():
            await session.refresh(approval_request)
        assert approval_request.application_state == "applied"
        assert approval_request.applied_at is not None


# =========================================================== 5. cross-tenant isolation


class TestCrossTenantIsolation:
    async def test_a_decided_event_for_org_a_can_never_apply_to_org_bs_attendance_row(
        self, client, session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        await _seed_manager_and_requester(session, org_id, manager=manager, requester=requester)
        raised = _raise_request(org_id, requester, requested_status="half_day")
        approval_request = await _open_via_consumer(session, org_id, raised["requested_row"])
        request_id = approval_request.id

        with as_principal(principal_for(org_id, manager, ["core.approval.request.decide:OWN"])):
            await client.post(
                f"/api/v1/approvals/requests/{request_id}/decision",
                json={"outcome": "approve"},
            )
        decided_row = await _decided_outbox_row(session, org_id, request_id)
        genuine_envelope = build_envelope(decided_row, producer="approval")

        # A tampered copy of the *real* org A envelope, claiming org B — the exact scenario
        # CLAUDE.md warns about: the tenant GUC is transaction-local, so a pooled connection
        # that ends up bound to the wrong organization must find nothing, not another tenant's
        # row by the same primary key. A fresh event id: `processed_events` has no
        # `organization_id` column (it is a same-schema, id-only dedup ledger — real event ids
        # are fresh UUIDs, so this never collides in production), and reusing org A's real event
        # id here would make the *ledger* — not RLS — the reason the genuine delivery below saw
        # nothing, which would prove the wrong thing.
        tampered = {
            **genuine_envelope,
            "event_id": str(uuid.uuid4()),
            "organization_id": str(other_org),
            "payload": {**genuine_envelope["payload"], "organization_id": str(other_org)},
        }
        leaked = _apply_decisions([tampered])
        assert leaked["regularization"] is None
        assert leaked["attendance"] is None
        assert leaked["applied_rows"] == []

        # The genuine delivery, to the correct tenant, still applies normally afterwards — the
        # tampered attempt never touched org A's row.
        applied = _apply_decisions([genuine_envelope])
        assert applied["attendance"]["status"] == "half_day"
        assert applied["regularization"]["status"] == "approved"

    async def test_a_request_raised_in_org_a_never_appears_in_org_bs_inbox(
        self, session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        requester, manager = uuid.uuid4(), uuid.uuid4()
        await _seed_manager_and_requester(session, org_id, manager=manager, requester=requester)
        raised = _raise_request(org_id, requester)
        approval_request = await _open_via_consumer(session, org_id, raised["requested_row"])
        assert approval_request.status == "pending"

        # Same session, deliberately re-bound to `other_org` for one transaction — the tenant
        # GUC is transaction-local (CLAUDE.md), so this is exactly what a pooled connection
        # serving org B's next request would see. `manager` is reused verbatim in both
        # organizations (data model §6's own seeding discipline): the isolation must hold on the
        # organization_id column alone, not on the two ids happening to differ.
        async with session.begin():
            await session.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :org, true)"), {"org": str(other_org)}
            )
            rows, total = await selectors.list_inbox(
                session,
                organization_id=other_org,
                caller_id=manager,
                caller_active=True,
                is_org_decider=False,
                now=datetime.now(UTC),
                limit=20,
                offset=0,
            )
        assert rows == []
        assert total == 0


# =========================================================== 6. the unassigned path


class TestUnassignedPathEndToEnd:
    async def test_a_requester_whose_manager_is_unknown_opens_unassigned_org_decider_only(
        self, client, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        requester = uuid.uuid4()
        unknown_manager = uuid.uuid4()  # never seeded — dangling pointer, not absent entirely
        async with session.begin():
            await seed_person(
                session,
                org_id,
                requester,
                reporting_manager_id=unknown_manager,
                display_name="Requester",
            )

        raised = _raise_request(org_id, requester, requested_status="half_day")
        approval_request = await _open_via_consumer(session, org_id, raised["requested_row"])
        request_id = approval_request.id

        async with session.begin():
            step = (
                (
                    await session.execute(
                        select(ApprovalStep).where(
                            ApprovalStep.organization_id == org_id,
                            ApprovalStep.request_id == request_id,
                            ApprovalStep.outcome == "open",
                        )
                    )
                )
                .scalars()
                .one()
            )
        assert step.approver_employee_id is None
        assert step.reason == "unassigned"

        # The requester themselves cannot decide their own unassigned request, even though
        # nobody else is yet assigned to it.
        with as_principal(principal_for(org_id, requester, ["core.approval.request.decide:ORG"])):
            refused = await client.post(
                f"/api/v1/approvals/requests/{request_id}/decision",
                json={"outcome": "approve"},
            )
        assert refused.status_code == 403

        # An HR / decide:ORG holder, uninvolved in the chain, can.
        hr = uuid.uuid4()
        with as_principal(principal_for(org_id, hr, ["core.approval.request.decide:ORG"])):
            decided = await client.post(
                f"/api/v1/approvals/requests/{request_id}/decision",
                json={"outcome": "approve"},
            )
        assert decided.status_code == 200
        assert decided.json()["status"] == "approved"
        assert decided.json()["decided_by_employee_id"] == str(hr)
