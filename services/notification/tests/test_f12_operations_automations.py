"""F12 (operational automation): operations' new alert events, run through the real automation
engine (slice A1) — the same engine `docs/fulfilment/plan.md` and `e42c9ae1`'s order-lifecycle
wiring already use, extended here rather than duplicated (see this branch's own report).

Real PostgreSQL as the app role (row-level security is live) and the real consumer entry point
(`consumers.fold`), driven with the envelopes the relay would deliver — exactly
`test_a1_automations.py`'s own shape, applied to `operations.inventory.item.low_stock`,
`operations.fulfilment.task.delayed` and `operations.fulfilment.task.escalated` instead of
`support.desk.ticket.created`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from itertools import pairwise
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app import a1_services, consumers, domain, services
from app.a1_models import AutomationRun, AutomationRunStep
from app.config import settings
from app.models import SCHEMA, Message
from app.targets import TargetUnavailable
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from platform_core.errors import ConflictProblem
from tests.helpers import ctx_for, envelope_for

pytestmark = pytest.mark.integration

WRITE = "engage.campaign.campaign.write"
SEND = "engage.campaign.campaign.send"
LOW_STOCK = "operations.inventory.item.low_stock"
TASK_DELAYED = "operations.fulfilment.task.delayed"
TASK_ESCALATED = "operations.fulfilment.task.escalated"
A1_TABLES = ("a1_run_steps", "a1_runs", "a1_versions", "a1_automations")


@pytest_asyncio.fixture(autouse=True)
async def _clean(session: Any) -> AsyncIterator[None]:
    """The shared fixture truncates the older tables; these are ours."""
    admin = create_async_engine(
        settings().admin_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    async with admin.begin() as conn:
        tables = ", ".join(f'"{SCHEMA}"."{t}"' for t in A1_TABLES)
        await conn.execute(text(f"TRUNCATE {tables}"))
    await admin.dispose()
    yield


@pytest_asyncio.fixture
async def worker_engine(session: Any) -> AsyncIterator[None]:
    init_engine(settings())
    yield
    await dispose_engine()


def _ctx(org_id: uuid.UUID, *perms: str) -> Any:
    from platform_core.auth import ScopeContext
    from tests.helpers import principal_for

    principal = principal_for(org_id, [f"{p}:ORG" for p in perms], entitlements=["engage"])
    return ScopeContext(principal=principal, scope=Scope.ORG, permission=perms[0])


def _alert_send_node(*, variable_map: dict[str, str], **over: Any) -> dict[str, Any]:
    config = {
        "channel": "email",
        "template": {"ref": "alert"},
        "recipient_path": "payload.alert_contact.email",
        "variable_map": variable_map,
    }
    config.update(over)
    return {"id": "send", "type": "send_message", "config": config}


def _linear(trigger: str, *middle: dict[str, Any]) -> dict[str, Any]:
    nodes = [
        {"id": "t", "type": "trigger", "config": {"event_type": trigger}},
        *middle,
        {"id": "end", "type": "end", "config": {}},
    ]
    edges = [{"from": a["id"], "to": b["id"]} for a, b in pairwise(nodes)]
    return {"nodes": nodes, "edges": edges}


async def _template(session: Any, org_id: uuid.UUID, code: str = "alert") -> None:
    async with session.begin():
        await services.upsert_template(
            session,
            ctx_for(org_id, "notification.delivery.template.write", Scope.ORG),
            code=code,
            channel="email",
            subject="Operations alert",
            body="{{summary}}",
        )


async def _go_live(
    session: Any, org_id: uuid.UUID, graph: dict[str, Any], name: str = "Alert"
) -> uuid.UUID:
    ctx = _ctx(org_id, WRITE, SEND)
    async with session.begin():
        row = await a1_services.create_automation(
            session, ctx, name=name, description=None, graph=graph
        )
        await a1_services.publish(session, ctx, automation_id=row.id)
    return row.id


async def _runs(session: Any, org_id: uuid.UUID) -> list[AutomationRun]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(AutomationRun)
                    .where(AutomationRun.organization_id == org_id)
                    .order_by(AutomationRun.started_at)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )


async def _steps(session: Any, org_id: uuid.UUID, run_id: uuid.UUID) -> list[AutomationRunStep]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(AutomationRunStep)
                    .where(AutomationRunStep.run_id == run_id)
                    .order_by(AutomationRunStep.seq)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )


async def _messages(session: Any, org_id: uuid.UUID) -> list[Message]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(Message).where(
                        Message.organization_id == org_id,
                        Message.rule_code.like("automation:%"),
                    )
                )
            )
            .scalars()
            .all()
        )


def _low_stock_event(org_id: uuid.UUID, **over: Any) -> dict[str, Any]:
    body = {
        "alert_id": str(uuid.uuid4()),
        "inventory_item_id": str(uuid.uuid4()),
        "sku": "WID-1",
        "name": "Widget",
        "warehouse": "Main",
        "uom": "ea",
        "on_hand": 2,
        "reorder_point": 10,
        "alert_contact": {"email": "ops@example.com", "phone": None},
        "summary": "Widget is low",
    }
    body.update(over)
    return envelope_for(org_id, LOW_STOCK, body)


def _task_delayed_event(org_id: uuid.UUID, **over: Any) -> dict[str, Any]:
    body = {
        "task_id": str(uuid.uuid4()),
        "order_id": str(uuid.uuid4()),
        "warehouse_id": str(uuid.uuid4()),
        "status": "IN_PROGRESS",
        "age_minutes": 90.0,
        "sla_minutes": 60,
        "alert_contact": {"email": "ops@example.com", "phone": None},
        "summary": "Task running long",
    }
    body.update(over)
    return envelope_for(org_id, TASK_DELAYED, body)


def _task_escalated_event(org_id: uuid.UUID, **over: Any) -> dict[str, Any]:
    body = {
        "task_id": str(uuid.uuid4()),
        "round": 3,
        "warehouse_id": str(uuid.uuid4()),
        "alert_contact": {"email": "ops@example.com", "phone": None},
        "summary": "Task escalated",
    }
    body.update(over)
    return envelope_for(org_id, TASK_ESCALATED, body)


# --------------------------------------------------------------------- trigger catalogue


def test_the_new_operations_events_are_subscribable() -> None:
    """The trigger vocabulary an automation may be built against (`e42c9ae1`'s own precedent:
    adding the routing key here is the whole of what wires a new product's events into the
    engine — no new consumer code)."""
    for event_type in (
        LOW_STOCK,
        TASK_DELAYED,
        "operations.dispatch.shipment.delayed",
        TASK_ESCALATED,
        "operations.fulfilment.pick.exception_raised",
        "operations.dispatch.shipment.failed",
        "operations.inventory.inconsistency_detected",
    ):
        assert event_type in domain.SUBSCRIBABLE_EVENTS


# --------------------------------------------------------------------- end-to-end firing


async def test_low_stock_event_runs_the_flow_and_stages_one_message(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    await _go_live(
        session,
        org_id,
        _linear(LOW_STOCK, _alert_send_node(variable_map={"summary": "payload.summary"})),
    )

    await consumers.fold(_low_stock_event(org_id))

    (run,) = await _runs(session, org_id)
    assert run.state == "completed"
    steps = await _steps(session, org_id, run.id)
    assert [(s.type, s.state) for s in steps] == [
        ("trigger", "done"),
        ("send_message", "done"),
        ("end", "done"),
    ]
    (message,) = await _messages(session, org_id)
    assert message.to_address == "ops@example.com"
    assert message.body == "Widget is low"


async def test_task_escalated_event_runs_the_flow(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    await _go_live(
        session,
        org_id,
        _linear(TASK_ESCALATED, _alert_send_node(variable_map={"summary": "payload.summary"})),
        name="Escalation",
    )

    await consumers.fold(_task_escalated_event(org_id))

    (run,) = await _runs(session, org_id)
    assert run.state == "completed"
    (message,) = await _messages(session, org_id)
    assert message.body == "Task escalated"


async def test_a_redelivered_operations_event_starts_no_second_run(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    """`ProcessedEvent` drops the redelivery at the ledger; the same source event under a *new*
    delivery id still cannot start a second run, the net under the ledger `a1_runs`'s own unique
    constraint provides."""
    await _template(session, org_id)
    await _go_live(
        session,
        org_id,
        _linear(TASK_DELAYED, _alert_send_node(variable_map={"summary": "payload.summary"})),
    )
    envelope = _task_delayed_event(org_id)

    await consumers.fold(envelope)
    await consumers.fold(envelope)  # the ledger drops it

    async with session.begin():
        again = await a1_services.start_runs(session, organization_id=org_id, envelope=envelope)
    assert again == []
    assert len(await _runs(session, org_id)) == 1
    assert len(await _messages(session, org_id)) == 1


async def test_a_transient_failure_on_an_operations_alert_retries_then_recovers(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    """A contact-lookup outage on the send step parks the run with backoff
    (`a1_pure.retry_delay_seconds`) and gives up after `a1_pure.MAX_ATTEMPTS`, the same behaviour
    `test_a1_automations.py::test_a_transient_failure_retries_with_backoff_then_gives_up` proves
    for a product's own event — reused here, not reimplemented, for one of F12's own triggers."""
    await _template(session, org_id)
    node = _alert_send_node(
        variable_map={"summary": "payload.summary"},
        recipient_path="payload.contact_id",
        recipient_kind="contact_id",
    )
    await _go_live(session, org_id, _linear(TASK_DELAYED, node))

    async def down(_org: str, _contact: str, _channel: str) -> str | None:
        raise TargetUnavailable

    envelope = _task_delayed_event(org_id, contact_id=str(uuid.uuid4()))
    await consumers.fold(envelope, down)

    (run,) = await _runs(session, org_id)
    assert run.state == "waiting" and run.next_wake_at is not None

    for _ in range(6):
        (run,) = await _runs(session, org_id)
        if run.state != "waiting":
            break
        wake = envelope_for(
            org_id,
            a1_services.EVT_RUN_WAKE,
            {"organization_id": str(org_id), "run_id": str(run.id), "wake_token": run.wake_token},
        )
        await consumers.wake_automation_run(wake, down)
    (run,) = await _runs(session, org_id)
    assert run.state == "failed" and run.failure_code == "contact_lookup_unavailable"
    (step,) = [s for s in await _steps(session, org_id, run.id) if s.type == "send_message"]
    assert step.attempt == 4 and step.state == "failed"

    # And a retry recovers it once the dependency is back (the same
    # `test_a_permanent_failure_fails_the_run_and_retry_recovers_it` shape) — a resolver that
    # now answers instead of raising.
    async def up(_org: str, _contact: str, _channel: str) -> str | None:
        return "ops@example.com"

    async with session.begin():
        await a1_services.retry_run(session, _ctx(org_id, WRITE), run_id=run.id, resolve_target=up)
    (run,) = await _runs(session, org_id)
    assert run.state == "completed" and run.failure_code is None
    assert len(await _messages(session, org_id)) == 1
    with pytest.raises(ConflictProblem):  # only failed runs retry
        async with session.begin():
            await a1_services.retry_run(session, _ctx(org_id, WRITE), run_id=run.id)


# --------------------------------------------------------------------- loop prevention


async def test_an_automations_own_action_cannot_become_another_automations_trigger(
    session: Any, org_id: uuid.UUID
) -> None:
    """The structural guard `domain.py`'s own `SUBSCRIBABLE_EVENTS` docstring documents ("binding
    `#` would include this service's own events, so a rule could stage a message about staging a
    message"): `create_task`'s own contracted event
    (`notification.automation.task_requested`) is never in `SUBSCRIBABLE_EVENTS`, so an automation
    cannot be published with it as a trigger — the two-automation cycle the testing brief asks to
    confirm is stopped literally cannot be authored, not merely detected at run time."""
    assert a1_services.EVT_TASK_REQUESTED not in domain.SUBSCRIBABLE_EVENTS
    graph = _linear(a1_services.EVT_TASK_REQUESTED)
    ctx = _ctx(org_id, WRITE, SEND)
    async with session.begin():
        row = await a1_services.create_automation(
            session, ctx, name="Would-cycle", description=None, graph=graph
        )
        with pytest.raises(a1_services.PublishProblem) as excinfo:
            await a1_services.publish(session, ctx, automation_id=row.id)
    codes = {p["code"] for p in excinfo.value.problems}
    assert "trigger_event" in codes


async def test_a_single_graph_cannot_loop_back_on_itself(session: Any, org_id: uuid.UUID) -> None:
    """The other half of loop prevention — a cycle *inside* one graph — is refused at publish
    time by `a1_pure.validate_graph`'s own reachability/cycle check, regardless of trigger."""
    graph = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"event_type": TASK_DELAYED}},
            {
                "id": "c",
                "type": "condition",
                "config": {"match": "all", "rules": [{"path": "payload.status", "op": "exists"}]},
            },
        ],
        "edges": [
            {"from": "t", "to": "c"},
            {"from": "c", "to": "c", "label": "yes"},
            {"from": "c", "to": "t", "label": "no"},
        ],
    }
    ctx = _ctx(org_id, WRITE, SEND)
    async with session.begin():
        row = await a1_services.create_automation(
            session, ctx, name="Cycle", description=None, graph=graph
        )
        with pytest.raises(a1_services.PublishProblem):
            await a1_services.publish(session, ctx, automation_id=row.id)
