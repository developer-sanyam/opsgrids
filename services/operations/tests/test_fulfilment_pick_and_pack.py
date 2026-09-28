"""Pick confirmation and packing over HTTP (ADR-0024 §6, ADR-0025 §2; plan F3) — the phase's three
remaining exit criteria:

* **#2** — a pick confirmation transfers the reservation rather than freeing it, proven under a
  concurrent second-order accept that tries to reserve the source bucket's now-smaller-looking
  stock and is refused (`TestReservationTransferConcurrency`), the same proof
  `test_ledger_reservation_transfer.py::TestConcurrencyProof` makes at the kernel level, now
  exercised through the real HTTP endpoints (offer/accept/pick-confirm, order-accept).
* **#3** — two workers confirming the same task's line concurrently: exactly one pick posts
  through the kernel; the second sees the task's already-updated state, not a duplicate ledger row
  (`TestConcurrentPickConfirmation`).
* **#4** — a short pick leaves the order's remaining lines pickable and the shorted line visibly
  flagged; it does not silently complete the task (`TestShortPick`).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.config import settings
from app.inventory import selectors as inv_selectors
from app.inventory.reconcile import reconcile_organization
from app.main import app
from app.models import SCHEMA, InventoryBalance, LedgerEntry, PickException, PickTask, ScanEvent
from app.orders import selectors as order_selectors
from app.orders import services as order_services
from platform_core.auth import Principal, Scope, get_principal
from platform_core.db import get_session
from tests.fulfilment_fixtures import (
    PACK_EXECUTE_PERM,
    PACK_READ_PERM,
    TASK_ASSIGN_PERM,
    TASK_EXECUTE_PERM,
    TASK_READ_PERM,
    Floor,
    accept_order,
    build_floor,
)
from tests.helpers import as_principal, ctx_for, principal_for

pytestmark = pytest.mark.integration


@pytest.fixture
async def floor(session, org_id) -> Floor:
    return await build_floor(session, org_id, stock=100)


async def _create_tasks(client, org_id, order) -> list[dict]:
    principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            "/api/v1/operations/fulfilment/tasks",
            json={"order_id": str(order.id), "order_version": order.version},
        )
    assert response.status_code == 201, response.text
    return response.json()


async def _offer(client, org_id, task_id: str, worker_id: uuid.UUID) -> dict:
    principal = principal_for(org_id, [TASK_ASSIGN_PERM], subject_ref=uuid.uuid4())
    with as_principal(principal):
        response = await client.post(
            f"/api/v1/operations/fulfilment/tasks/{task_id}/offers",
            json={"worker_id": str(worker_id)},
        )
    assert response.status_code == 201, response.text
    return response.json()


async def _accept_offer(client, org_id, offer_id: str, worker_employee_id: uuid.UUID) -> dict:
    principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=worker_employee_id)
    with as_principal(principal):
        response = await client.post(f"/api/v1/operations/fulfilment/offers/{offer_id}/accept")
    assert response.status_code == 200, response.text
    return response.json()


async def _task_ready_to_pick(
    session, client, org_id, floor: Floor, *, qty: str
) -> tuple[Any, dict]:
    order = await accept_order(
        session,
        org_id,
        floor.shop,
        lines=[{"inventory_item_id": floor.shop.widget, "quantity": qty}],
    )
    tasks = await _create_tasks(client, org_id, order)
    offer = await _offer(client, org_id, tasks[0]["id"], floor.worker_id)
    await _accept_offer(client, org_id, offer["id"], floor.worker_employee_id)
    return order, tasks[0]


async def _confirm_pick(  # type: ignore[no-untyped-def]
    client,
    org_id,
    task_id: str,
    line_id: str,
    worker_employee_id: uuid.UUID,
    *,
    body: dict | None = None,
):
    principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=worker_employee_id)
    with as_principal(principal):
        return await client.post(
            f"/api/v1/operations/fulfilment/tasks/{task_id}/lines/{line_id}/pick",
            json=body or {},
        )


async def _balance(session: AsyncSession, org_id, item_id, location_id) -> tuple[int, int]:
    async with session.begin():
        row = (
            await session.execute(
                select(InventoryBalance.quantity_milli, InventoryBalance.reserved_milli).where(
                    InventoryBalance.organization_id == org_id,
                    InventoryBalance.inventory_item_id == item_id,
                    InventoryBalance.location_id == location_id,
                    InventoryBalance.status == "AVAILABLE",
                )
            )
        ).one_or_none()
    return (row[0], row[1]) if row else (0, 0)


async def _assert_reconciles_clean(session, org_id) -> None:  # type: ignore[no-untyped-def]
    async with session.begin():
        report = await reconcile_organization(session, org_id)
    assert report.findings == [], [
        (f.kind, f.stored_milli, f.expected_milli) for f in report.findings
    ]


# ------------------------------------------------------------------ happy-path pick + F3 exit
# criterion #2 (concurrency)


class TestPickConfirmHappyPath:
    async def test_a_full_pick_transfers_the_reservation_and_completes_the_task(
        self, client, org_id, session, floor: Floor
    ) -> None:
        _order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="60")
        line_id = task["lines"][0]["id"]

        response = await _confirm_pick(
            client, org_id, task["id"], line_id, floor.worker_employee_id
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "PICKED"
        assert body["lines"][0]["status"] == "picked"
        assert body["lines"][0]["picked"] == "60"

        home_location_id = await _home_location(session, org_id, floor)
        home_qty, home_reserved = await _balance(
            session, org_id, floor.shop.widget, home_location_id
        )
        staging_qty, staging_reserved = await _balance(
            session, org_id, floor.shop.widget, floor.staging_location_id
        )
        assert home_qty == 40_000  # 100 - 60, milli-units
        assert home_reserved == 0  # the whole reservation moved with the stock
        assert staging_qty == 60_000
        assert staging_reserved == 60_000

        await _assert_reconciles_clean(session, org_id)

    async def test_cancelling_a_picked_task_is_refused(
        self, client, org_id, session, floor: Floor
    ) -> None:
        _order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        line_id = task["lines"][0]["id"]
        await _confirm_pick(client, org_id, task["id"], line_id, floor.worker_employee_id)

        principal = principal_for(
            org_id,
            ["operations.fulfilment.task.assign:ORG", "operations.fulfilment.task.override:ORG"],
            subject_ref=uuid.uuid4(),
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{task['id']}/cancel",
                json={"reason": "too late"},
            )
        assert response.status_code == 409

    async def test_a_second_confirm_on_an_already_picked_line_is_conflict(
        self, client, org_id, session, floor: Floor
    ) -> None:
        _order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        line_id = task["lines"][0]["id"]
        first = await _confirm_pick(client, org_id, task["id"], line_id, floor.worker_employee_id)
        assert first.status_code == 200
        second = await _confirm_pick(client, org_id, task["id"], line_id, floor.worker_employee_id)
        assert second.status_code == 409

    async def test_someone_elses_worker_cannot_confirm(
        self, client, org_id, session, floor: Floor
    ) -> None:
        _order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        line_id = task["lines"][0]["id"]
        response = await _confirm_pick(client, org_id, task["id"], line_id, uuid.uuid4())
        assert response.status_code == 403

    async def test_cross_tenant_pick_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        _order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        line_id = task["lines"][0]["id"]
        other = principal_for(uuid.uuid4(), [TASK_EXECUTE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{task['id']}/lines/{line_id}/pick", json={}
            )
        assert response.status_code == 404


async def _home_location(session, org_id, floor: Floor) -> uuid.UUID:
    async with session.begin():
        location = await inv_selectors.default_location(session, org_id, floor.shop.warehouse_id)
    assert location is not None
    return location.id


# ------------------------------------------------------------------ #2: concurrent second-order
# reserve proof


@pytest.fixture
async def maker(org_id):  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        settings().app_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        pool_size=20,
        max_overflow=0,
    )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def concurrent_client(maker):  # type: ignore[no-untyped-def]
    async def _session_override():  # type: ignore[no-untyped-def]
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_session, None)


def _principal_router(principals: dict[str, Principal]) -> Any:
    """FastAPI's dependency-override machinery introspects the *override's own* signature, not
    the original `get_principal`'s — an untyped `request` parameter here would be read as a
    required string query parameter named `request` (the 422 that cost an hour to track down),
    not the Starlette `Request` a real `get_principal` receives. The annotation is load-bearing."""

    def _dep(request: Request) -> Principal:
        key = request.headers.get("x-test-principal", "default")
        return principals[key]

    return _dep


class TestReservationTransferConcurrency:
    async def test_a_second_order_cannot_reserve_the_source_buckets_freed_looking_stock(
        self, client, org_id, session, maker, concurrent_client, floor: Floor
    ) -> None:
        """100 on hand. Order A (60) and order C (20) both reserve first — 20 truly free. Order
        A's pick (transferring its 60 to staging) races a second order D trying to reserve 21 —
        one more than the true 20 available. It is refused every time, whichever side the
        scheduler runs first (the balance's row lock decides, not the ordering)."""
        _order_a, task = await _task_ready_to_pick(session, client, org_id, floor, qty="60")
        await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "20"}],
        )  # order C — the 20 that stays reserved and must go on blocking order D throughout
        line_id = task["lines"][0]["id"]

        create_ctx = ctx_for(org_id, "operations.orders.order.create", Scope.ORG)
        write_ctx = ctx_for(org_id, "operations.orders.order.write", Scope.ORG)
        async with session.begin():
            order_d = await order_services.create_order(
                session,
                create_ctx,
                channel="manual",
                warehouse_id=floor.shop.warehouse_id,
                customer_id=None,
                customer_name="Order D",
                customer_phone="1",
                currency="INR",
                lines=[{"inventory_item_id": floor.shop.widget, "quantity": "21"}],
            )
        async with session.begin():
            locked = await order_selectors.get_order(session, org_id, order_d.id, for_update=True)
            assert locked is not None
            await order_services.place_order(session, write_ctx, locked, expected_version=0)

        worker_principal = principal_for(
            org_id, [TASK_EXECUTE_PERM], subject_ref=floor.worker_employee_id
        )
        manager_principal = principal_for(
            org_id, ["operations.orders.order.accept:ORG"], subject_ref=uuid.uuid4()
        )
        app.dependency_overrides[get_principal] = _principal_router(
            {"worker": worker_principal, "manager": manager_principal}
        )
        try:
            pick_response, accept_response = await asyncio.gather(
                concurrent_client.post(
                    f"/api/v1/operations/fulfilment/tasks/{task['id']}/lines/{line_id}/pick",
                    json={},
                    headers={"x-test-principal": "worker"},
                ),
                concurrent_client.post(
                    f"/api/v1/operations/orders/{order_d.id}/accept",
                    json={"version": 1},
                    headers={"x-test-principal": "manager"},
                ),
            )
        finally:
            app.dependency_overrides.pop(get_principal, None)

        assert pick_response.status_code == 200, pick_response.text
        assert accept_response.status_code == 409, accept_response.text

        home_qty, home_reserved = await _balance(
            session, org_id, floor.shop.widget, await _home_location(session, org_id, floor)
        )
        assert home_qty == 40_000  # 100 - order A's 60, milli-units
        assert home_reserved == 20_000  # only order C's, unchanged
        staging_qty, staging_reserved = await _balance(
            session, org_id, floor.shop.widget, floor.staging_location_id
        )
        assert (staging_qty, staging_reserved) == (60_000, 60_000)

        await _assert_reconciles_clean(session, org_id)


# ------------------------------------------------------------------ #3: concurrent pick
# confirmation on the SAME line


class TestConcurrentPickConfirmation:
    async def test_exactly_one_confirmation_posts_through_the_kernel(
        self, client, org_id, session, maker, concurrent_client, floor: Floor
    ) -> None:
        _order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        line_id = task["lines"][0]["id"]
        worker_principal = principal_for(
            org_id, [TASK_EXECUTE_PERM], subject_ref=floor.worker_employee_id
        )
        app.dependency_overrides[get_principal] = lambda: worker_principal
        try:

            async def _one():  # type: ignore[no-untyped-def]
                return await concurrent_client.post(
                    f"/api/v1/operations/fulfilment/tasks/{task['id']}/lines/{line_id}/pick",
                    json={},
                )

            first, second = await asyncio.gather(_one(), _one())
        finally:
            app.dependency_overrides.pop(get_principal, None)

        statuses = sorted([first.status_code, second.status_code])
        assert statuses == [200, 409], (first.text, second.text)

        async with session.begin():
            entries = (
                (
                    await session.execute(
                        select(LedgerEntry).where(
                            LedgerEntry.organization_id == org_id,
                            LedgerEntry.inventory_item_id == floor.shop.widget,
                            LedgerEntry.location_id == floor.staging_location_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        # Exactly one relocation posted to staging for this pick — not doubled.
        assert len(entries) == 1
        assert entries[0].quantity_milli == 10_000

        async with session.begin():
            row = (
                await session.execute(select(PickTask).where(PickTask.id == uuid.UUID(task["id"])))
            ).scalar_one()
        assert row.status == "PICKED"

        await _assert_reconciles_clean(session, org_id)


# ------------------------------------------------------------------ #4: short pick


class TestShortPick:
    async def test_a_short_pick_raises_an_exception_and_does_not_complete_the_task(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[
                {"inventory_item_id": floor.shop.widget, "quantity": "10"},
                {"inventory_item_id": floor.shop.gizmo, "quantity": "5"},
            ],
        )
        tasks = await _create_tasks(client, org_id, order)
        task = tasks[0]
        offer = await _offer(client, org_id, task["id"], floor.worker_id)
        await _accept_offer(client, org_id, offer["id"], floor.worker_employee_id)

        widget_line = next(
            line for line in task["lines"] if line["inventory_item_id"] == str(floor.shop.widget)
        )
        response = await _confirm_pick(
            client,
            org_id,
            task["id"],
            widget_line["id"],
            floor.worker_employee_id,
            body={"quantity": "6", "reason": "short_pick", "note": "only 6 on the shelf"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "EXCEPTION"  # visibly flagged, not silently completed
        picked_line = next(line for line in body["lines"] if line["id"] == widget_line["id"])
        assert picked_line["picked"] == "6"
        assert picked_line["short"] == "4"
        assert picked_line["status"] == "short"
        assert len(body["exceptions"]) == 1
        assert body["exceptions"][0]["reason"] == "short_pick"

        gizmo_line = next(
            line for line in task["lines"] if line["inventory_item_id"] == str(floor.shop.gizmo)
        )
        # The order's remaining line is still pickable — resolving the exception resumes picking.
        principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=floor.worker_employee_id)
        with as_principal(principal):
            resolve = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{task['id']}"
                f"/exceptions/{body['exceptions'][0]['id']}/resolve",
                json={"note": "manager approved the short"},
            )
        assert resolve.status_code == 200, resolve.text
        assert resolve.json()["status"] == "IN_PROGRESS"

        final = await _confirm_pick(
            client, org_id, task["id"], gizmo_line["id"], floor.worker_employee_id
        )
        assert final.status_code == 200, final.text
        assert final.json()["status"] == "PICKED"  # settled: one picked, one formally shorted

    async def test_a_short_pick_without_a_reason_is_422(
        self, client, org_id, session, floor: Floor
    ) -> None:
        _order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        line_id = task["lines"][0]["id"]
        response = await _confirm_pick(
            client, org_id, task["id"], line_id, floor.worker_employee_id, body={"quantity": "4"}
        )
        assert response.status_code == 422

    async def test_a_short_pick_releases_the_shortfall_back_to_available_stock(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """Security review MAJOR 1 #3: the quantity that was never picked must not stay reserved
        at the source bin indefinitely — it is released back to available stock (through the
        kernel's own `release` path) the moment the shortfall is recorded."""
        order = await accept_order(
            session,
            org_id,
            floor.shop,
            lines=[{"inventory_item_id": floor.shop.widget, "quantity": "10"}],
        )
        tasks = await _create_tasks(client, org_id, order)
        task = tasks[0]
        offer = await _offer(client, org_id, task["id"], floor.worker_id)
        await _accept_offer(client, org_id, offer["id"], floor.worker_employee_id)
        line_id = task["lines"][0]["id"]

        response = await _confirm_pick(
            client,
            org_id,
            task["id"],
            line_id,
            floor.worker_employee_id,
            body={"quantity": "6", "reason": "short_pick", "note": "only 6 on the shelf"},
        )
        assert response.status_code == 200, response.text

        home_location_id = await _home_location(session, org_id, floor)
        home_qty, home_reserved = await _balance(
            session, org_id, floor.shop.widget, home_location_id
        )
        assert home_qty == 94_000  # 100 - 6 picked, milli-units — the shortfall never moved
        assert home_reserved == 0  # the shortfall's phantom reservation is released, not stuck

        await _assert_reconciles_clean(session, org_id)


async def _resolve_exception(  # type: ignore[no-untyped-def]
    client, org_id, task_id: str, exception_id: str, worker_employee_id, *, note: str = ""
):
    principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=worker_employee_id)
    with as_principal(principal):
        return await client.post(
            f"/api/v1/operations/fulfilment/tasks/{task_id}/exceptions/{exception_id}/resolve",
            json={"note": note},
        )


class TestExceptionOwnershipAndCompletion:
    """Security review BLOCKER 1 (`resolve_exception` never checked ownership) and MAJOR 1 #1
    (resolving the last open exception must re-run the settle -> PICKED -> order-ready tail)."""

    async def _shorted_task(  # type: ignore[no-untyped-def]
        self, client, org_id, session, floor: Floor, *, second_line: bool = True
    ):
        """A task with a short-picked widget line and, unless `second_line=False`, an untouched
        gizmo line too — so resolving the widget's exception alone does not, by itself, settle the
        whole task (that scenario has its own test, below)."""
        lines = [{"inventory_item_id": floor.shop.widget, "quantity": "10"}]
        if second_line:
            lines.append({"inventory_item_id": floor.shop.gizmo, "quantity": "5"})
        order = await accept_order(session, org_id, floor.shop, lines=lines)
        tasks = await _create_tasks(client, org_id, order)
        task = tasks[0]
        offer = await _offer(client, org_id, task["id"], floor.worker_id)
        await _accept_offer(client, org_id, offer["id"], floor.worker_employee_id)
        widget_line = next(
            line for line in task["lines"] if line["inventory_item_id"] == str(floor.shop.widget)
        )
        response = await _confirm_pick(
            client,
            org_id,
            task["id"],
            widget_line["id"],
            floor.worker_employee_id,
            body={"quantity": "6", "reason": "short_pick", "note": "only 6 on the shelf"},
        )
        assert response.status_code == 200, response.text
        exception_id = response.json()["exceptions"][0]["id"]
        return order, task, exception_id

    async def test_a_caller_with_no_relationship_to_the_task_cannot_resolve_its_exception(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """`task.execute:OWN` alone — no live offer on this task — must never resolve someone
        else's exception. The security review's proof-of-concept: any holder of the permission,
        pointed at any task id, could resolve it."""
        _order, task, exception_id = await self._shorted_task(client, org_id, session, floor)
        stranger_employee_id = uuid.uuid4()  # never offered this task at all
        response = await _resolve_exception(
            client, org_id, task["id"], exception_id, stranger_employee_id
        )
        assert response.status_code == 403, response.text

        async with session.begin():
            row = (
                await session.execute(
                    select(PickException).where(PickException.id == uuid.UUID(exception_id))
                )
            ).scalar_one()
        assert row.status == "open"  # untouched by the refused attempt

    async def test_a_reader_without_execute_cannot_resolve(
        self, client, org_id, session, floor: Floor
    ) -> None:
        _order, task, exception_id = await self._shorted_task(client, org_id, session, floor)
        principal = principal_for(org_id, [TASK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{task['id']}"
                f"/exceptions/{exception_id}/resolve",
                json={},
            )
        assert response.status_code == 403

    async def test_the_owning_worker_can_resolve_their_own_exception(
        self, client, org_id, session, floor: Floor
    ) -> None:
        _order, task, exception_id = await self._shorted_task(client, org_id, session, floor)
        response = await _resolve_exception(
            client, org_id, task["id"], exception_id, floor.worker_employee_id
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "IN_PROGRESS"

    async def test_resolving_the_last_exception_completes_the_task_and_readies_the_order(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """MAJOR 1 #1: a short-pick then a resolve on a task's only line must move the task all
        the way to `PICKED` and the order on to `READY` in the same call — not leave it sitting
        `IN_PROGRESS` forever."""
        order, task, exception_id = await self._shorted_task(
            client, org_id, session, floor, second_line=False
        )
        response = await _resolve_exception(
            client, org_id, task["id"], exception_id, floor.worker_employee_id
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "PICKED"

        principal = principal_for(
            org_id, ["operations.orders.order.read:ORG"], subject_ref=uuid.uuid4()
        )
        with as_principal(principal):
            order_response = await client.get(f"/api/v1/operations/orders/{order.id}")
        assert order_response.status_code == 200
        assert order_response.json()["status"] == "READY"

    async def test_cross_tenant_resolve_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        _order, task, exception_id = await self._shorted_task(client, org_id, session, floor)
        other = principal_for(uuid.uuid4(), [TASK_EXECUTE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{task['id']}"
                f"/exceptions/{exception_id}/resolve",
                json={},
            )
        assert response.status_code == 404


# ------------------------------------------------------------------ scan (security review
# BLOCKER 2)


async def _scan(  # type: ignore[no-untyped-def]
    client,
    org_id,
    task_id: str,
    worker_employee_id,
    *,
    barcode: str = "SCAN-1",
    location_id=None,
    inventory_item_id=None,
):
    principal = principal_for(org_id, [TASK_EXECUTE_PERM], subject_ref=worker_employee_id)
    body: dict[str, Any] = {"barcode": barcode}
    if location_id is not None:
        body["location_id"] = str(location_id)
    if inventory_item_id is not None:
        body["inventory_item_id"] = str(inventory_item_id)
    with as_principal(principal):
        return await client.post(f"/api/v1/operations/fulfilment/tasks/{task_id}/scan", json=body)


class TestScan:
    async def test_happy_path_scan_starts_the_task(
        self, client, org_id, session, floor: Floor
    ) -> None:
        _order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        response = await _scan(
            client,
            org_id,
            task["id"],
            floor.worker_employee_id,
            location_id=floor.staging_location_id,
            inventory_item_id=floor.shop.widget,
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "IN_PROGRESS"

    async def test_a_reader_cannot_scan(self, client, org_id, session, floor: Floor) -> None:
        _order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        principal = principal_for(org_id, [TASK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{task['id']}/scan", json={"barcode": "X"}
            )
        assert response.status_code == 403

    async def test_someone_elses_worker_cannot_scan(
        self, client, org_id, session, floor: Floor
    ) -> None:
        _order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        response = await _scan(client, org_id, task["id"], uuid.uuid4())
        assert response.status_code == 403

    async def test_cross_tenant_scan_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        _order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        other = principal_for(uuid.uuid4(), [TASK_EXECUTE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                f"/api/v1/operations/fulfilment/tasks/{task['id']}/scan", json={"barcode": "X"}
            )
        assert response.status_code == 404

    async def test_another_orgs_location_id_is_refused_indistinguishably_from_nonexistent(
        self, client, org_id, session, admin_session, floor: Floor
    ) -> None:
        """Security review BLOCKER 2: the scan body's `location_id` must be looked up org-scoped,
        never inserted straight into `scan_events` — a cross-org id and a genuinely nonexistent
        one must refuse with the exact same status and shape (no existence oracle)."""
        _order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        other_floor = await build_floor(admin_session, uuid.uuid4())

        cross_org = await _scan(
            client,
            org_id,
            task["id"],
            floor.worker_employee_id,
            location_id=other_floor.staging_location_id,
        )
        nonexistent = await _scan(
            client, org_id, task["id"], floor.worker_employee_id, location_id=uuid.uuid4()
        )
        assert cross_org.status_code == 404, cross_org.text
        assert nonexistent.status_code == 404, nonexistent.text
        # Same shape, no existence oracle — only the per-request correlation id may differ.
        assert {k: v for k, v in cross_org.json().items() if k != "request_id"} == {
            k: v for k, v in nonexistent.json().items() if k != "request_id"
        }

        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(ScanEvent).where(ScanEvent.organization_id == org_id)
                    )
                )
                .scalars()
                .all()
            )
        assert rows == []  # neither refused attempt wrote a scan event

    async def test_another_orgs_item_id_is_refused_indistinguishably_from_nonexistent(
        self, client, org_id, session, admin_session, floor: Floor
    ) -> None:
        _order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        other_floor = await build_floor(admin_session, uuid.uuid4())

        cross_org = await _scan(
            client,
            org_id,
            task["id"],
            floor.worker_employee_id,
            inventory_item_id=other_floor.shop.widget,
        )
        nonexistent = await _scan(
            client, org_id, task["id"], floor.worker_employee_id, inventory_item_id=uuid.uuid4()
        )
        assert cross_org.status_code == 404, cross_org.text
        assert nonexistent.status_code == 404, nonexistent.text
        assert {k: v for k, v in cross_org.json().items() if k != "request_id"} == {
            k: v for k, v in nonexistent.json().items() if k != "request_id"
        }

    async def test_an_item_not_on_this_task_is_refused(
        self, client, org_id, session, floor: Floor
    ) -> None:
        """The bonus half of BLOCKER 2: an item that is real, in this org, but not one of this
        task's own lines must not be accepted either."""
        _order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        response = await _scan(
            client,
            org_id,
            task["id"],
            floor.worker_employee_id,
            inventory_item_id=floor.shop.gizmo,  # a real item, never ordered on this task
        )
        assert response.status_code == 404, response.text


# ------------------------------------------------------------------ packaging


class TestPackaging:
    async def test_happy_path_confirms_a_package_and_relocates_to_dock(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        line_id = task["lines"][0]["id"]
        confirmed = await _confirm_pick(
            client, org_id, task["id"], line_id, floor.worker_employee_id
        )
        assert confirmed.status_code == 200, confirmed.text

        principal = principal_for(org_id, [PACK_EXECUTE_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/fulfilment/packages",
                json={
                    "order_id": str(order.id),
                    "destination_location_id": str(floor.dock_location_id),
                    "lines": [{"pick_task_line_id": line_id, "quantity": "10"}],
                },
            )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "PACKED"
        assert body["lines"][0]["quantity"] == "10"

        staging_qty, staging_reserved = await _balance(
            session, org_id, floor.shop.widget, floor.staging_location_id
        )
        assert (staging_qty, staging_reserved) == (0, 0)
        dock_qty, dock_reserved = await _balance(
            session, org_id, floor.shop.widget, floor.dock_location_id
        )
        assert (dock_qty, dock_reserved) == (10_000, 10_000)

        await _assert_reconciles_clean(session, org_id)

    async def test_packing_more_than_picked_is_conflict(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        line_id = task["lines"][0]["id"]
        await _confirm_pick(client, org_id, task["id"], line_id, floor.worker_employee_id)

        principal = principal_for(org_id, [PACK_EXECUTE_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/fulfilment/packages",
                json={
                    "order_id": str(order.id),
                    "destination_location_id": str(floor.dock_location_id),
                    "lines": [{"pick_task_line_id": line_id, "quantity": "11"}],
                },
            )
        assert response.status_code == 409

    async def test_a_reader_cannot_confirm_a_package(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        line_id = task["lines"][0]["id"]
        await _confirm_pick(client, org_id, task["id"], line_id, floor.worker_employee_id)
        principal = principal_for(org_id, [PACK_READ_PERM], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.post(
                "/api/v1/operations/fulfilment/packages",
                json={
                    "order_id": str(order.id),
                    "destination_location_id": str(floor.dock_location_id),
                    "lines": [{"pick_task_line_id": line_id, "quantity": "10"}],
                },
            )
        assert response.status_code == 403

    async def test_cross_tenant_package_confirm_is_not_found(
        self, client, org_id, session, floor: Floor
    ) -> None:
        order, task = await _task_ready_to_pick(session, client, org_id, floor, qty="10")
        line_id = task["lines"][0]["id"]
        await _confirm_pick(client, org_id, task["id"], line_id, floor.worker_employee_id)
        other = principal_for(uuid.uuid4(), [PACK_EXECUTE_PERM], subject_ref=uuid.uuid4())
        with as_principal(other):
            response = await client.post(
                "/api/v1/operations/fulfilment/packages",
                json={
                    "order_id": str(order.id),
                    "destination_location_id": str(floor.dock_location_id),
                    "lines": [{"pick_task_line_id": line_id, "quantity": "10"}],
                },
            )
        assert response.status_code == 404
