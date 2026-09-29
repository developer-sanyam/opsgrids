"""Auto-assignment over HTTP (ADR-0024 §9; this build's own scope — F3 shipped manual-only,
"deferred, no phase number assigned in this MVP set", ADR-0025 §2). Mirrors
`test_fulfilment_tasks_write.py`'s shape: real status codes, a seed step's own success checked
before its id is trusted.

`test_fulfilment_pick_and_pack.py` is the regression proof this build's report names: it runs
unchanged against this branch, green, because auto-assignment defaults off
(`fulfilment_settings.auto_assignment_enabled`) and every call site here is additive — nothing in
`offer_task`'s existing behaviour changes for a plain `task.assign` caller with no live offer, or
for an org that never opts in.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.fulfilment import selectors as fulfilment_selectors
from app.fulfilment import services as fulfilment_services
from app.main import app
from app.models import SCHEMA, AssignmentOffer, AuditLog, FulfilmentSettings
from platform_core.auth import Scope, get_principal
from platform_core.db import get_session
from tests.fulfilment_fixtures import (
    TASK_ASSIGN_PERM,
    TASK_EXECUTE_PERM,
    TASK_OVERRIDE_PERM,
    TASK_READ_PERM,
    Floor,
    accept_order,
    build_floor,
)
from tests.helpers import as_principal, ctx_for, principal_for

pytestmark = pytest.mark.integration


@pytest.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id)


async def _seed_settings(
    session, org_id: uuid.UUID, *, auto_assignment_enabled: bool, offer_rounds: int = 3
) -> None:
    """No endpoint creates `fulfilment_settings` yet — the same direct-model-insert shape
    `test_orders_handover.py::_seed_settings` already uses for a table no service function
    writes."""
    async with session.begin():
        session.add(
            FulfilmentSettings(
                organization_id=org_id,
                warehouse_id=None,
                offer_timeout_seconds=300,
                offer_rounds=offer_rounds,
                auto_assignment_enabled=auto_assignment_enabled,
            )
        )


async def _set_online(session, org_id: uuid.UUID, worker_id: uuid.UUID) -> None:
    async with session.begin():
        worker = await fulfilment_selectors.get_worker(session, org_id, worker_id)
        assert worker is not None
        ctx = ctx_for(org_id, "operations.fulfilment.worker.write", Scope.ORG)
        await fulfilment_services.set_presence(session, ctx, worker, status="online")


async def _second_worker(
    session, org_id: uuid.UUID, floor: Floor, *, display_name: str, online: bool = True
) -> tuple[uuid.UUID, uuid.UUID]:
    """A second worker at the same warehouse — auto-assignment's own ranking/exclusion tests need
    more than one candidate. Returns `(employee_id, worker_id)`."""
    employee_id = uuid.uuid4()
    ctx = ctx_for(org_id, "operations.fulfilment.worker.write", Scope.ORG)
    async with session.begin():
        worker = await fulfilment_services.create_worker(
            session,
            ctx,
            employee_id=employee_id,
            warehouse_id=floor.shop.warehouse_id,
            display_name=display_name,
            is_active=True,
            skills=[],
            zone_location_ids=[],
        )
    if online:
        await _set_online(session, org_id, worker.id)
    return employee_id, worker.id


async def _accepted_order(session, org_id, floor: Floor, *, qty: str = "2"):  # type: ignore[no-untyped-def]
    return await accept_order(
        session,
        org_id,
        floor.shop,
        lines=[{"inventory_item_id": str(floor.shop.widget), "quantity": qty}],
    )


async def _create_tasks(client, org_id, order) -> list[dict]:  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            "/api/v1/operations/fulfilment/tasks",
            json={"order_id": str(order.id), "order_version": order.version},
        )
    assert response.status_code == 201, response.text
    return response.json()


async def _get_task(client, org_id, task_id: str) -> dict:  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [TASK_READ_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.get(f"/api/v1/operations/fulfilment/tasks/{task_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _decline(client, org_id, offer_id: str, worker_employee_id: uuid.UUID):  # type: ignore[no-untyped-def]
    principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=worker_employee_id)
    with as_principal(principal):
        return await client.post(f"/api/v1/operations/fulfilment/offers/{offer_id}/decline")


# ------------------------------------------------------------------ off by default (regression)


class TestAutoAssignmentOffByDefault:
    async def test_a_fresh_org_with_no_settings_row_behaves_exactly_like_f3(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _set_online(session, org_id, floor.worker_id)  # eligible in every other respect
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        assert len(tasks) == 1
        assert tasks[0]["status"] == "CREATED"
        assert tasks[0]["offers"] == []
        assert tasks[0]["auto_assign_failure_reason"] is None

    async def test_auto_assignment_explicitly_off_behaves_identically(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id, auto_assignment_enabled=False)
        await _set_online(session, org_id, floor.worker_id)
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        assert tasks[0]["status"] == "CREATED"
        assert tasks[0]["offers"] == []
        assert tasks[0]["auto_assign_failure_reason"] is None


# ------------------------------------------------------------------ auto-assignment on


class TestAutoAssignmentOn:
    async def test_creates_an_offer_to_the_eligible_worker(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id, auto_assignment_enabled=True)
        await _set_online(session, org_id, floor.worker_id)
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        task = tasks[0]
        assert task["status"] == "OFFERED"
        assert task["auto_assign_failure_reason"] is None
        assert len(task["offers"]) == 1
        offer = task["offers"][0]
        assert offer["worker_id"] == str(floor.worker_id)
        assert offer["state"] == "OFFERED"
        assert offer["score_breakdown"]["kind"] == "auto"

    async def test_no_eligible_worker_sets_a_visible_reason_and_stays_created(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id, auto_assignment_enabled=True)
        # `floor.worker_id` defaults to offline (`create_worker`'s own default) — nobody eligible.
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        task = tasks[0]
        assert task["status"] == "CREATED"
        assert task["offers"] == []
        assert task["auto_assign_failure_reason"] is not None
        assert "No eligible worker" in task["auto_assign_failure_reason"]

    async def test_an_inactive_worker_is_not_eligible(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id, auto_assignment_enabled=True)
        await _set_online(session, org_id, floor.worker_id)
        principal = principal_for(
            org_id, ["operations.fulfilment.worker.write:ORG"], subject_ref=uuid.uuid4()
        )
        with as_principal(principal):
            patched = await client.patch(
                f"/api/v1/operations/fulfilment/workers/{floor.worker_id}",
                json={"is_active": False},
            )
        assert patched.status_code == 200, patched.text
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        assert tasks[0]["status"] == "CREATED"
        assert tasks[0]["auto_assign_failure_reason"] is not None

    async def test_fewer_open_tasks_wins_the_ranking(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id, auto_assignment_enabled=True)
        await _set_online(session, org_id, floor.worker_id)

        first_order = await _accepted_order(session, org_id, floor, qty="1")
        first_tasks = await _create_tasks(client, org_id, first_order)
        assert first_tasks[0]["status"] == "OFFERED"
        assert first_tasks[0]["offers"][0]["worker_id"] == str(floor.worker_id)
        # `floor.worker_id` now holds one live offer — one open task.

        _, idle_worker_id = await _second_worker(session, org_id, floor, display_name="Idle Worker")

        second_order = await _accepted_order(session, org_id, floor, qty="1")
        second_tasks = await _create_tasks(client, org_id, second_order)
        assert second_tasks[0]["status"] == "OFFERED"
        assert second_tasks[0]["offers"][0]["worker_id"] == str(idle_worker_id)

    async def test_declining_re_offers_automatically_to_the_next_eligible_worker(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """ADR-0025 §2's "excluded from every later automatic round" — proven over HTTP, not only
        at the domain-function level."""
        await _seed_settings(session, org_id, auto_assignment_enabled=True)
        await _set_online(session, org_id, floor.worker_id)
        second_employee_id, second_worker_id = await _second_worker(
            session, org_id, floor, display_name="Second Worker"
        )
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        task = tasks[0]
        first_offer = task["offers"][0]
        assert first_offer["state"] == "OFFERED"
        chosen_is_floor_worker = first_offer["worker_id"] == str(floor.worker_id)
        chosen_employee_id = (
            floor.worker_employee_id if chosen_is_floor_worker else second_employee_id
        )
        other_worker_id = str(second_worker_id) if chosen_is_floor_worker else str(floor.worker_id)

        decline = await _decline(client, org_id, first_offer["id"], chosen_employee_id)
        assert decline.status_code == 200, decline.text

        refreshed = await _get_task(client, org_id, task["id"])
        assert refreshed["status"] == "OFFERED"
        # `PickTask.round` counts *failed* rounds, not offers made — one decline bumps it once.
        assert refreshed["round"] == 1
        live_offers = [o for o in refreshed["offers"] if o["state"] == "OFFERED"]
        assert len(live_offers) == 1
        assert live_offers[0]["worker_id"] == other_worker_id

    async def test_after_every_worker_declines_it_reports_no_eligible_worker_not_escalation(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """Two workers, five rounds budgeted: after both decline, round 3's auto-assign attempt
        finds every worker excluded — `auto_assign_failure_reason`, still `CREATED`, not yet
        `ESCALATED` (that only fires once the round *budget*, not the candidate pool, is spent)."""
        await _seed_settings(session, org_id, auto_assignment_enabled=True, offer_rounds=5)
        await _set_online(session, org_id, floor.worker_id)
        second_employee_id, _second_worker_id = await _second_worker(
            session, org_id, floor, display_name="Second Worker"
        )
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        task_id = tasks[0]["id"]

        for _ in range(2):
            current = await _get_task(client, org_id, task_id)
            live = next(o for o in current["offers"] if o["state"] == "OFFERED")
            worker_employee_id = (
                floor.worker_employee_id
                if live["worker_id"] == str(floor.worker_id)
                else second_employee_id
            )
            decline = await _decline(client, org_id, live["id"], worker_employee_id)
            assert decline.status_code == 200, decline.text

        final = await _get_task(client, org_id, task_id)
        assert final["status"] == "CREATED"
        assert final["round"] == 2
        assert final["auto_assign_failure_reason"] is not None
        assert final["offers"][-1]["state"] in ("DECLINED",)


# ------------------------------------------------------------------ manual override


class TestManualOverride:
    async def test_a_dispatcher_with_override_can_redirect_a_live_auto_assigned_offer(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id, auto_assignment_enabled=True)
        await _set_online(session, org_id, floor.worker_id)
        _, second_worker_id = await _second_worker(
            session, org_id, floor, display_name="Second Worker"
        )
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        task = tasks[0]
        auto_offer = task["offers"][0]
        assert auto_offer["score_breakdown"]["kind"] == "auto"
        redirect_to = (
            str(second_worker_id)
            if auto_offer["worker_id"] != str(second_worker_id)
            else str(floor.worker_id)
        )

        principal = principal_for(
            org_id, [TASK_ASSIGN_PERM, TASK_OVERRIDE_PERM], subject_ref=uuid.uuid4()
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{task['id']}/offers",
                json={"worker_id": redirect_to},
            )
        assert response.status_code == 201, response.text
        new_offer = response.json()
        assert new_offer["worker_id"] == redirect_to
        assert new_offer["state"] == "OFFERED"
        assert new_offer["score_breakdown"]["kind"] == "manual"
        # The task's own status never moved — it was already `OFFERED`, and stays `OFFERED`.
        refreshed = await _get_task(client, org_id, task["id"])
        assert refreshed["status"] == "OFFERED"
        assert refreshed["round"] == 0  # an override is not a failed round

        async with session.begin():
            old = (
                await session.execute(
                    select(AssignmentOffer).where(AssignmentOffer.id == uuid.UUID(auto_offer["id"]))
                )
            ).scalar_one()
        assert old.state == "WITHDRAWN"

        async with session.begin():
            audit = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org_id,
                        AuditLog.action == "pick_task.offer_overridden",
                        AuditLog.entity_id == uuid.UUID(task["id"]),
                    )
                )
            ).scalar_one()
        assert audit.before["worker_id"] == auto_offer["worker_id"]
        assert audit.after["worker_id"] == redirect_to

    async def test_a_plain_assigner_without_override_still_gets_the_existing_409(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """Regression proof at the HTTP layer: a `task.assign`-only caller sees exactly the F3
        behaviour a second offer attempt has always had — auto-assignment adds no new way around
        it for a caller who does not hold `task.override`."""
        await _seed_settings(session, org_id, auto_assignment_enabled=True)
        await _set_online(session, org_id, floor.worker_id)
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        task = tasks[0]
        assert task["status"] == "OFFERED"

        principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{task['id']}/offers",
                json={"worker_id": str(floor.worker_id)},
            )
        assert response.status_code == 409

    async def test_manual_offer_before_auto_assignment_ran_still_works_when_auto_assignment_is_off(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """Auto-assignment off (or unused) is byte-for-byte F3's existing manual flow — the
        dispatcher's own manual offer is the one and only path into `OFFERED`."""
        await _set_online(session, org_id, floor.worker_id)
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        assert tasks[0]["status"] == "CREATED"

        principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{tasks[0]['id']}/offers",
                json={"worker_id": str(floor.worker_id)},
            )
        assert response.status_code == 201, response.text
        offer = response.json()
        assert offer["score_breakdown"]["kind"] == "manual"


# ------------------------------------------------------------------ concurrency


class TestConcurrentAutoAssignment:
    async def test_two_orders_created_concurrently_with_one_worker_never_double_offers_one_task(
        self, org_id, session, floor: Floor
    ) -> None:
        """Two tasks becoming eligible for auto-assignment at once, sharing the one eligible
        worker, must not crash or leave a task with two live offers —
        `uq_assignment_offers_org_task_live` is the invariant that actually matters (this build's
        own concurrency note); a worker legitimately ending up with two live offers, one per task,
        is the expected, documented outcome, not a bug."""
        await _seed_settings(session, org_id, auto_assignment_enabled=True)
        await _set_online(session, org_id, floor.worker_id)

        order_a = await _accepted_order(session, org_id, floor, qty="1")
        order_b = await _accepted_order(session, org_id, floor, qty="1")

        principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
        engine = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
            pool_size=20,
            max_overflow=0,
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)

        async def _session_override():  # type: ignore[no-untyped-def]
            async with maker() as s:
                yield s

        app.dependency_overrides[get_session] = _session_override
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            async with (
                AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c1,
                AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2,
            ):
                result_a, result_b = await asyncio.gather(
                    c1.post(
                        "/api/v1/operations/fulfilment/tasks",
                        json={"order_id": str(order_a.id), "order_version": order_a.version},
                    ),
                    c2.post(
                        "/api/v1/operations/fulfilment/tasks",
                        json={"order_id": str(order_b.id), "order_version": order_b.version},
                    ),
                    return_exceptions=True,
                )
        finally:
            app.dependency_overrides.pop(get_session, None)
            app.dependency_overrides.pop(get_principal, None)
            await engine.dispose()

        assert not isinstance(result_a, BaseException), repr(result_a)
        assert not isinstance(result_b, BaseException), repr(result_b)
        assert result_a.status_code == 201, result_a.text
        assert result_b.status_code == 201, result_b.text

        for result in (result_a, result_b):
            task = result.json()[0]
            assert task["status"] == "OFFERED"
            live_offers = [o for o in task["offers"] if o["state"] == "OFFERED"]
            assert len(live_offers) == 1
            assert live_offers[0]["worker_id"] == str(floor.worker_id)

        async with session.begin():
            live_count = (
                (
                    await session.execute(
                        select(AssignmentOffer).where(
                            AssignmentOffer.organization_id == org_id,
                            AssignmentOffer.worker_id == floor.worker_id,
                            AssignmentOffer.state == "OFFERED",
                        )
                    )
                )
                .scalars()
                .all()
            )
        # Both tasks legitimately offered to the one eligible worker — two different tasks, two
        # live offers, never two live offers on the *same* task (the partial unique index would
        # have refused that, and the assertions above already confirm one each).
        assert len(live_count) == 2


# ------------------------------------------------------------------ order status guard
# (security review MAJOR M1)


async def _hold_order(  # type: ignore[no-untyped-def]
    client, org_id, order_id, version: int, *, reason: str = "customer asked to wait"
):
    principal = principal_for(
        org_id, ["operations.orders.order.write:ORG"], subject_ref=uuid.uuid4()
    )
    with as_principal(principal):
        response = await client.post(
            f"/api/v1/operations/orders/{order_id}/hold",
            json={"version": version, "reason": reason},
        )
    assert response.status_code == 200, response.text
    return response.json()


async def _release_order(client, org_id, order_id, version: int):  # type: ignore[no-untyped-def]
    principal = principal_for(
        org_id, ["operations.orders.order.write:ORG"], subject_ref=uuid.uuid4()
    )
    with as_principal(principal):
        response = await client.post(
            f"/api/v1/operations/orders/{order_id}/release", json={"version": version}
        )
    assert response.status_code == 200, response.text
    return response.json()


class TestOrderStatusGuard:
    """Security review MAJOR M1: `_fail_round`'s auto-assignment hook must never fail its own
    caller. Before the fix, declining/releasing a task on a held order 409'd (the hook's own
    `offer_task` call raised `ON_HOLD`), and the offer-expiry consumer would dead-letter for the
    same reason, leaving an expired offer stuck OFFERED forever."""

    async def test_declining_a_task_on_a_held_order_still_succeeds(
        self, client, org_id, session, floor: Floor
    ) -> None:
        await _seed_settings(session, org_id, auto_assignment_enabled=True)
        await _set_online(session, org_id, floor.worker_id)
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        task = tasks[0]
        assert task["status"] == "OFFERED"  # auto-assigned to the one online worker
        offer_id = task["offers"][0]["id"]

        held = await _hold_order(client, org_id, order.id, order.version)
        assert held["status"] == "ON_HOLD"

        # Before the fix: 409, because the hook's own re-offer attempt raised into this call.
        decline = await _decline(client, org_id, offer_id, floor.worker_employee_id)
        assert decline.status_code == 200, decline.text

        task_after = await _get_task(client, org_id, task["id"])
        assert task_after["status"] == "CREATED"
        # No new live offer was created while the order is on hold — the guard inside
        # `attempt_auto_assign` no-ops rather than attempting one.
        assert task_after["offers"][-1]["state"] == "DECLINED"
        assert not any(o["state"] == "OFFERED" for o in task_after["offers"])

        # Releasing the hold and re-creating a task-worthy state still lets auto-assignment work
        # normally afterward — this was never broken, just proving the guard doesn't overreach.
        released = await _release_order(client, org_id, order.id, held["version"])
        assert released["status"] == "IN_FULFILMENT"

    async def test_a_held_orders_offer_expiring_does_not_dead_letter(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """The expiry path is the same hook, reached through `expire_offer` instead of
        `decline_offer` — proven directly at the service layer since driving a real expiry over
        HTTP needs the scheduler's own timing."""
        await _seed_settings(session, org_id, auto_assignment_enabled=True)
        await _set_online(session, org_id, floor.worker_id)
        order = await _accepted_order(session, org_id, floor)
        tasks = await _create_tasks(client, org_id, order)
        task_id = uuid.UUID(tasks[0]["id"])
        offer_id = uuid.UUID(tasks[0]["offers"][0]["id"])

        await _hold_order(client, org_id, order.id, order.version)

        ctx = ctx_for(org_id, "operations.fulfilment.task.assign", Scope.ORG)
        async with session.begin():
            # Does not raise — before the fix, this call would propagate the hook's own
            # `ConflictProblem` ("Order ... is on hold") straight out of `expire_offer`.
            fired = await fulfilment_services.expire_offer(session, ctx, offer_id=offer_id)
        assert fired is True

        async with session.begin():
            task = await fulfilment_selectors.get_pick_task(session, org_id, task_id)
            assert task is not None
            assert task.status == "CREATED"
