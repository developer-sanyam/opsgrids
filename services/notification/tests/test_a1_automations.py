"""The automation engine end to end: authoring, the executor, waits, consent, failure and retry.

Real PostgreSQL as the app role (row-level security is live), the real consumer entry points
(`consumers.fold`, `consumers.wake_automation_run`) driven with the envelopes the relay and the
scheduler would deliver, and the real HTTP surface for the authorization checks.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app import a1_services, consumers, services
from app.a1_models import Automation, AutomationRun, AutomationRunStep
from app.config import settings
from app.models import SCHEMA, Message, OutboxEvent, Template
from app.targets import TargetUnavailable
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from platform_core.errors import ConflictProblem, ValidationProblem
from platform_core.events import validate_event_schema
from tests.helpers import ctx_for, envelope_for
from tests.test_engage_campaigns import as_principal, caller

pytestmark = pytest.mark.integration

WRITE = "engage.campaign.campaign.write"
SEND = "engage.campaign.campaign.send"
READ = "engage.campaign.campaign.read"
EVENT = "support.desk.ticket.created"
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"
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


def _send_node(**over: Any) -> dict[str, Any]:
    config = {
        "channel": "email",
        "template": {"ref": "ack"},
        "recipient_path": "payload.requester.email",
        "variable_map": {"number": "payload.number"},
    }
    config.update(over)
    return {"id": "send", "type": "send_message", "config": config}


def _linear(*middle: dict[str, Any], trigger: str = EVENT) -> dict[str, Any]:
    nodes = [
        {"id": "t", "type": "trigger", "config": {"event_type": trigger}},
        *middle,
        {"id": "end", "type": "end", "config": {}},
    ]
    edges = [{"from": a["id"], "to": b["id"]} for a, b in pairwise(nodes)]
    return {"nodes": nodes, "edges": edges}


async def _template(session: Any, org_id: uuid.UUID, code: str = "ack") -> None:
    async with session.begin():
        await services.upsert_template(
            session,
            ctx_for(org_id, "notification.delivery.template.write", Scope.ORG),
            code=code,
            channel="email",
            subject="Ticket {{number}}",
            body="We have ticket {{number}}.",
        )


async def _drop_template(session: Any, org_id: uuid.UUID, code: str = "ack") -> None:
    async with session.begin():
        await session.execute(
            update(Template)
            .where(Template.organization_id == org_id, Template.code == code)
            .values(deleted_at=datetime.now(UTC))
        )


async def _go_live(
    session: Any, org_id: uuid.UUID, graph: dict[str, Any], name: str = "Ack"
) -> Any:
    ctx = _ctx(org_id, WRITE, SEND)
    async with session.begin():
        row = await a1_services.create_automation(
            session, ctx, name=name, description=None, graph=graph
        )
        await a1_services.publish(session, ctx, automation_id=row.id)
    return row.id


def _event(org_id: uuid.UUID, number: str = "T-1", **payload: Any) -> dict[str, Any]:
    body = {"number": number, "requester": {"email": "asha@example.com"}, "priority": "high"}
    body.update(payload)
    return envelope_for(org_id, EVENT, body)


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
                    select(Message)
                    .where(
                        Message.organization_id == org_id,
                        Message.rule_code.like("automation:%"),
                    )
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )


async def _outbox(session: Any, event_type: str) -> list[OutboxEvent]:
    async with session.begin():
        return list(
            (await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type)))
            .scalars()
            .all()
        )


# ------------------------------------------------------------------------------- authoring


async def test_publish_creates_an_immutable_version(session: Any, org_id: uuid.UUID) -> None:
    await _template(session, org_id)
    automation_id = await _go_live(session, org_id, _linear(_send_node()))
    async with session.begin():
        row = await session.get(Automation, automation_id)
        assert row.status == "active" and row.current_version == 1
        assert row.trigger_event == EVENT and row.module == "support"
    # The version is frozen in the database: an UPDATE is refused, not merely discouraged.
    with pytest.raises(DBAPIError, match="immutable"):
        async with session.begin():
            await session.execute(
                text(f"UPDATE \"{SCHEMA}\".a1_versions SET trigger_event = 'x'")  # noqa: S608
            )
    with pytest.raises(DBAPIError, match="immutable"):
        async with session.begin():
            await session.execute(text(f'DELETE FROM "{SCHEMA}".a1_versions'))  # noqa: S608


async def test_publish_refuses_a_broken_graph_with_problems(
    session: Any, org_id: uuid.UUID
) -> None:
    ctx = _ctx(org_id, WRITE, SEND)
    graph = _linear(_send_node(template={"ref": "missing"}, variable_map={}))
    async with session.begin():
        row = await a1_services.create_automation(
            session, ctx, name="x", description=None, graph=graph
        )
        automation_id = row.id
    with pytest.raises(ValidationProblem) as caught:
        async with session.begin():
            await a1_services.publish(session, ctx, automation_id=automation_id)
    problems = caught.value.problems  # type: ignore[attr-defined]
    assert {"template_missing"} <= {p["code"] for p in problems}
    assert all(p["node_id"] == "send" for p in problems if p["code"] == "template_missing")
    async with session.begin():
        assert (await session.get(Automation, automation_id)).status == "draft"


async def test_a_draft_may_be_incomplete_but_only_a_draft_is_editable(
    session: Any, org_id: uuid.UUID
) -> None:
    await _template(session, org_id)
    automation_id = await _go_live(session, org_id, _linear(_send_node()))
    ctx = _ctx(org_id, WRITE, SEND)
    with pytest.raises(ConflictProblem):
        async with session.begin():
            await a1_services.update_automation(
                session, ctx, automation_id=automation_id, fields={"name": "new"}
            )
    with pytest.raises(ConflictProblem):
        async with session.begin():
            await a1_services.delete_automation(session, ctx, automation_id=automation_id)
    async with session.begin():
        await a1_services.set_status(session, ctx, automation_id=automation_id, target="paused")
        await a1_services.update_automation(
            session, ctx, automation_id=automation_id, fields={"name": "renamed"}
        )
    with pytest.raises(ConflictProblem):  # pausing twice
        async with session.begin():
            await a1_services.set_status(session, ctx, automation_id=automation_id, target="paused")
    async with session.begin():
        await a1_services.delete_automation(session, ctx, automation_id=automation_id)


# -------------------------------------------------------------------------------- executor


async def test_a_trigger_event_runs_the_flow_and_stages_one_message(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    await _go_live(session, org_id, _linear(_send_node()))

    await consumers.fold(_event(org_id))

    (run,) = await _runs(session, org_id)
    assert run.state == "completed" and run.version == 1
    assert run.subject.startswith("a") and "asha@example.com" not in run.subject
    steps = await _steps(session, org_id, run.id)
    assert [(s.type, s.state) for s in steps] == [
        ("trigger", "done"),
        ("send_message", "done"),
        ("end", "done"),
    ]
    (message,) = await _messages(session, org_id)
    assert message.state == "pending" and message.to_address == "asha@example.com"
    assert message.body == "We have ticket T-1." and steps[1].message_id == message.id
    queued = await _outbox(session, services.EVT_MESSAGE_QUEUED)
    assert len(queued) == 1  # the dispatcher will send it, through the tenant's own transport


async def test_a_redelivered_event_starts_no_second_run(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    await _go_live(session, org_id, _linear(_send_node()))
    envelope = _event(org_id)

    await consumers.fold(envelope)
    await consumers.fold(envelope)  # the ledger drops it

    # And the net under the ledger: the same source event under a *new* delivery id, straight into
    # the engine, still cannot start a second run or stage a second message.
    async with session.begin():
        again = await a1_services.start_runs(session, organization_id=org_id, envelope=envelope)
    assert again == []
    assert len(await _runs(session, org_id)) == 1
    assert len(await _messages(session, org_id)) == 1


async def test_a_paused_or_other_tenants_automation_does_not_fire(
    session: Any, admin_session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    mine = await _go_live(session, org_id, _linear(_send_node()), name="mine")
    other_org = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            Automation(
                organization_id=other_org,
                name="theirs",
                status="active",
                trigger_event=EVENT,
                module="support",
                draft_graph=_linear(_send_node()),
            )
        )
    async with session.begin():
        await a1_services.set_status(
            session, _ctx(org_id, WRITE, SEND), automation_id=mine, target="paused"
        )
    await consumers.fold(_event(org_id))
    assert await _runs(session, org_id) == []
    # The other tenant's row is invisible to this tenant even by a direct read (RLS as the net).
    async with session.begin():
        seen = (await session.execute(select(Automation.name))).scalars().all()
    assert seen == ["mine"]


async def test_a_condition_routes_yes_and_no(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    graph = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"event_type": EVENT}},
            {
                "id": "c",
                "type": "condition",
                "config": {
                    "match": "all",
                    "rules": [{"path": "payload.priority", "op": "eq", "value": "high"}],
                },
            },
            _send_node(),
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"from": "t", "to": "c"},
            {"from": "c", "to": "send", "label": "yes"},
            {"from": "c", "to": "end", "label": "no"},
            {"from": "send", "to": "end"},
        ],
    }
    await _go_live(session, org_id, graph)
    await consumers.fold(_event(org_id, "T-1", priority="high"))
    await consumers.fold(_event(org_id, "T-2", priority="low"))

    assert len(await _messages(session, org_id)) == 1  # only the high-priority one
    runs = await _runs(session, org_id)
    assert [r.state for r in runs] == ["completed", "completed"]
    branches = []
    for run in runs:
        branches.append(
            next(s.detail for s in await _steps(session, org_id, run.id) if s.type == "condition")
        )
    assert sorted(branches) == ["no", "yes"]


async def test_a_wait_parks_the_run_and_resumes_exactly_once(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    wait = {"id": "w", "type": "wait", "config": {"seconds": 3600}}
    await _go_live(session, org_id, _linear(wait, _send_node()))

    await consumers.fold(_event(org_id))
    (run,) = await _runs(session, org_id)
    assert run.state == "waiting" and run.next_wake_at is not None
    assert await _messages(session, org_id) == []  # nothing is sent while it sleeps
    (scheduled,) = await _outbox(session, "platform.schedule.requested")
    target = scheduled.payload["target"]
    assert target["event_type"] == a1_services.EVT_RUN_WAKE
    validate_event_schema(
        a1_services.EVT_RUN_WAKE,
        1,
        target["payload"],
        contracts_root=__import__("pathlib").Path(__file__).resolve().parents[3]
        / "libs"
        / "contracts",
    )

    wake = envelope_for(org_id, a1_services.EVT_RUN_WAKE, target["payload"])
    assert await consumers.wake_automation_run(wake) == "resumed"
    # The scheduler delivering it again, under a fresh delivery id, is a no-op...
    again = envelope_for(org_id, a1_services.EVT_RUN_WAKE, target["payload"])
    assert await consumers.wake_automation_run(again) == "stale"
    # ...and so is the very same delivery redelivered.
    assert await consumers.wake_automation_run(wake) == "redelivered"

    (run,) = await _runs(session, org_id)
    assert run.state == "completed" and run.wake_token is None
    assert len(await _messages(session, org_id)) == 1
    assert [s.state for s in await _steps(session, org_id, run.id)] == ["done"] * 4


async def test_the_sweep_wakes_a_lost_wakeup_once(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    wait = {"id": "w", "type": "wait", "config": {"seconds": 60}}
    await _go_live(session, org_id, _linear(wait, _send_node()))
    await consumers.fold(_event(org_id))

    later = datetime.now(UTC) + timedelta(hours=1)
    async with session.begin():
        assert await a1_services.sweep_due(session, organization_id=org_id, now=later) == 1
    async with session.begin():
        assert await a1_services.sweep_due(session, organization_id=org_id, now=later) == 0
    assert len(await _messages(session, org_id)) == 1


async def test_a_wait_window_defers_the_wakeup(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    from app import a1_pure

    await _template(session, org_id)
    window = {"start": "09:00", "end": "18:00", "timezone": "Asia/Kolkata"}
    wait = {"id": "w", "type": "wait", "config": {"seconds": 60, "window": window}}
    await _go_live(session, org_id, _linear(wait, _send_node()))
    await consumers.fold(_event(org_id))
    (run,) = await _runs(session, org_id)
    assert run.next_wake_at is not None and a1_pure.in_window(run.next_wake_at, window)


async def test_consent_denial_is_recorded_on_the_step(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    # Marketing on WhatsApp with no opt-in on record: the shared consent decision refuses it.
    graph = _linear(
        _send_node(
            channel="whatsapp",
            template={"ref": "promo"},
            recipient_path="payload.requester.phone",
            variable_map={},
            purpose="marketing",
        )
    )
    from app.models import ProviderTemplate

    async with session.begin():
        session.add(
            ProviderTemplate(
                organization_id=org_id,
                code="promo",
                channel="whatsapp",
                provider_name="promo",
                language="en",
                category="marketing",
                status="approved",
                variables=[],
                preview_text="Hello",
            )
        )
    await _go_live(session, org_id, graph)
    await consumers.fold(_event(org_id, requester={"phone": "+919812345678"}))

    (run,) = await _runs(session, org_id)
    assert run.state == "skipped"  # every send was refused; nothing reached the customer
    step = next(s for s in await _steps(session, org_id, run.id) if s.type == "send_message")
    assert step.state == "skipped" and step.detail == "consent:no_opt_in"
    (message,) = await _messages(session, org_id)
    assert message.state == "suppressed" and step.message_id == message.id
    assert await _outbox(session, services.EVT_MESSAGE_QUEUED) == []


async def test_a_permanent_failure_fails_the_run_and_retry_recovers_it(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    await _go_live(session, org_id, _linear(_send_node()))
    await _drop_template(session, org_id)  # deleted after publish: the run finds it gone
    await consumers.fold(_event(org_id))

    (run,) = await _runs(session, org_id)
    assert run.state == "failed" and run.failure_code == "template_missing"
    assert run.finished_at is not None
    failed = [s for s in await _steps(session, org_id, run.id) if s.state == "failed"]
    assert len(failed) == 1 and failed[0].detail.startswith("template_missing")

    async with session.begin():
        await session.execute(
            update(Template).where(Template.organization_id == org_id).values(deleted_at=None)
        )
    async with session.begin():
        await a1_services.retry_run(session, _ctx(org_id, WRITE), run_id=run.id)
    (run,) = await _runs(session, org_id)
    assert run.state == "completed" and run.failure_code is None
    assert len(await _messages(session, org_id)) == 1
    with pytest.raises(ConflictProblem):  # only failed runs retry
        async with session.begin():
            await a1_services.retry_run(session, _ctx(org_id, WRITE), run_id=run.id)


async def test_a_missing_recipient_is_a_permanent_failure(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    await _go_live(session, org_id, _linear(_send_node()))
    envelope = envelope_for(org_id, EVENT, {"number": "T-9"})
    await consumers.fold(envelope)
    (run,) = await _runs(session, org_id)
    assert run.state == "failed" and run.failure_code == "recipient_missing"
    assert await _messages(session, org_id) == []


async def test_a_transient_failure_retries_with_backoff_then_gives_up(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    contact_send = _send_node(recipient_path="payload.contact_id", recipient_kind="contact_id")
    await _go_live(session, org_id, _linear(contact_send))

    async def down(_org: str, _contact: str, _channel: str) -> str | None:
        raise TargetUnavailable

    envelope = envelope_for(org_id, EVENT, {"number": "T-1", "contact_id": str(uuid.uuid4())})
    await consumers.fold(envelope, down)

    (run,) = await _runs(session, org_id)
    assert run.state == "waiting" and run.next_wake_at is not None  # parked for a backoff
    (scheduled,) = await _outbox(session, "platform.schedule.requested")
    assert scheduled.payload["target"]["payload"]["run_id"] == str(run.id)

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


async def test_create_task_stages_the_contracted_event(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    task = {
        "id": "task",
        "type": "create_task",
        "config": {"title": "Call {{customer}} back", "assignee_team": "support"},
    }
    await _go_live(session, org_id, _linear(task))
    await consumers.fold(_event(org_id))
    (event,) = await _outbox(session, a1_services.EVT_TASK_REQUESTED)
    validate_event_schema(
        a1_services.EVT_TASK_REQUESTED,
        1,
        dict(event.payload),
        contracts_root=CONTRACTS_ROOT,
    )
    (run,) = await _runs(session, org_id)
    assert run.state == "completed"


# ------------------------------------------------------------------------------ HTTP surface


async def test_the_authoring_and_run_api_round_trip(
    client: Any, session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    graph = _linear(_send_node())
    perms = [f"{p}:ORG" for p in (READ, WRITE, SEND)]
    with as_principal(caller(org_id, perms)):
        created = await client.post(
            "/api/v1/engage/automations", json={"name": "Ack", "graph": graph}
        )
        assert created.status_code == 201, created.text
        body = created.json()
        automation_id = body["id"]
        assert body["status"] == "draft" and body["version"] is None
        assert body["graph"]["nodes"][0]["id"] == "t"
        assert body["validation"]["ok"] is True
        # Only a warning: the org has no email connection yet.
        assert [p["code"] for p in body["validation"]["problems"]] == ["channel_not_connected"]

        bad = await client.post(f"/api/v1/engage/automations/{automation_id}/publish")
        assert bad.status_code == 200, bad.text
        assert bad.json()["status"] == "active" and bad.json()["version"] == 1

        listing = await client.get("/api/v1/engage/automations", params={"status": "active"})
        assert listing.status_code == 200
        assert [i["id"] for i in listing.json()["items"]] == [automation_id]
        assert (await client.get("/api/v1/engage/automations", params={"q": "zzz"})).json()[
            "total"
        ] == 0

    await consumers.fold(_event(org_id))
    with as_principal(caller(org_id, perms)):
        runs = await client.get(f"/api/v1/engage/automations/{automation_id}/runs")
        assert runs.status_code == 200 and runs.json()["total"] == 1
        run = runs.json()["items"][0]
        assert run["state"] == "completed" and run["failure_code"] is None
        detail = await client.get(f"/api/v1/engage/automations/runs/{run['id']}")
        assert detail.status_code == 200
        assert [s["type"] for s in detail.json()["steps"]] == ["trigger", "send_message", "end"]
        assert detail.json()["idempotency_key"].startswith(automation_id)

        stats = await client.get("/api/v1/engage/automations/stats")
        assert stats.status_code == 200
        assert stats.json()["executions"] == 1 and stats.json()["successful"] == 1
        assert stats.json()["per_automation"][0]["name"] == "Ack"
        one = await client.get(f"/api/v1/engage/automations/{automation_id}")
        assert one.json()["executions_30d"] == 1 and one.json()["success_rate_30d"] == 1.0

        triggers = await client.get("/api/v1/engage/automations/triggers")
        assert triggers.status_code == 200
        assert EVENT in {t["event_type"] for t in triggers.json()["items"]}

        publish_bad = await client.post(
            "/api/v1/engage/automations",
            json={"name": "Broken", "graph": _linear(_send_node(template={"ref": "nope"}))},
        )
        problem = await client.post(
            f"/api/v1/engage/automations/{publish_bad.json()['id']}/publish"
        )
        assert problem.status_code == 422
        assert any(p["code"] == "template_missing" for p in problem.json()["problems"])

        unknown = await client.post(
            "/api/v1/engage/automations", json={"name": "x", "graph": graph, "extra": 1}
        )
        assert unknown.status_code == 422


async def test_failed_run_retry_over_http(
    client: Any, session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    await _go_live(session, org_id, _linear(_send_node()))
    await _drop_template(session, org_id)
    await consumers.fold(_event(org_id))
    (run,) = await _runs(session, org_id)
    async with session.begin():
        await session.execute(
            update(Template).where(Template.organization_id == org_id).values(deleted_at=None)
        )
    perms = [f"{p}:ORG" for p in (READ, WRITE)]
    with as_principal(caller(org_id, perms)):
        ok = await client.post(f"/api/v1/engage/automations/runs/{run.id}/retry")
        assert ok.status_code == 200 and ok.json() == {"ok": True}
        again = await client.post(f"/api/v1/engage/automations/runs/{run.id}/retry")
        assert again.status_code == 409


async def test_another_tenants_ids_are_404(
    client: Any, session: Any, admin_session: Any, org_id: uuid.UUID
) -> None:
    other = uuid.uuid4()
    async with admin_session.begin():
        row = Automation(
            organization_id=other,
            name="theirs",
            status="draft",
            trigger_event=EVENT,
            module="support",
            draft_graph=_linear(),
        )
        admin_session.add(row)
        await admin_session.flush()
        run = AutomationRun(
            organization_id=other,
            automation_id=row.id,
            version_id=uuid.uuid4(),
            version=1,
            source_event_id=uuid.uuid4(),
            idempotency_key="k",
            state="failed",
            context_enc=b"x",
        )
        admin_session.add(run)
        await admin_session.flush()
        run_id, automation_id = run.id, row.id
    perms = [f"{p}:ORG" for p in (READ, WRITE, SEND)]
    with as_principal(caller(org_id, perms)):
        assert (await client.get(f"/api/v1/engage/automations/{automation_id}")).status_code == 404
        assert (
            await client.get(f"/api/v1/engage/automations/{automation_id}/runs")
        ).status_code == 404
        assert (await client.get(f"/api/v1/engage/automations/runs/{run_id}")).status_code == 404
        assert (
            await client.post(f"/api/v1/engage/automations/runs/{run_id}/retry")
        ).status_code == 404
        assert (
            await client.post(f"/api/v1/engage/automations/{automation_id}/publish")
        ).status_code == 404
        assert (
            await client.delete(f"/api/v1/engage/automations/{automation_id}")
        ).status_code == 404
        assert (await client.get("/api/v1/engage/automations")).json()["total"] == 0


# ------------------------------------------------------------------------ negative authz

_ROUTES: list[tuple[str, str, str, dict[str, Any] | None]] = [
    ("GET", "/api/v1/engage/automations/triggers", READ, None),
    ("GET", "/api/v1/engage/automations/stats", READ, None),
    ("GET", "/api/v1/engage/automations", READ, None),
    (
        "POST",
        "/api/v1/engage/automations",
        WRITE,
        {"name": "x", "graph": {"nodes": [], "edges": []}},
    ),
    ("GET", f"/api/v1/engage/automations/{uuid.uuid4()}", READ, None),
    ("PATCH", f"/api/v1/engage/automations/{uuid.uuid4()}", WRITE, {"name": "y"}),
    ("DELETE", f"/api/v1/engage/automations/{uuid.uuid4()}", WRITE, None),
    ("POST", f"/api/v1/engage/automations/{uuid.uuid4()}/publish", SEND, None),
    ("POST", f"/api/v1/engage/automations/{uuid.uuid4()}/pause", WRITE, None),
    ("POST", f"/api/v1/engage/automations/{uuid.uuid4()}/resume", SEND, None),
    ("GET", f"/api/v1/engage/automations/{uuid.uuid4()}/runs", READ, None),
    ("GET", f"/api/v1/engage/automations/runs/{uuid.uuid4()}", READ, None),
    ("POST", f"/api/v1/engage/automations/runs/{uuid.uuid4()}/retry", WRITE, None),
]


@pytest.mark.parametrize(("method", "path", "needs", "body"), _ROUTES)
async def test_every_route_refuses_a_caller_without_its_permission(
    client: Any, org_id: uuid.UUID, method: str, path: str, needs: str, body: Any
) -> None:
    others = [p for p in (READ, WRITE, SEND, "engage.setup.connection.read") if p != needs]
    with as_principal(caller(org_id, [f"{p}:ORG" for p in others])):
        response = await client.request(method, path, json=body)
    assert response.status_code == 403, (method, path, response.text)


@pytest.mark.parametrize(("method", "path", "needs", "body"), _ROUTES)
async def test_every_route_needs_the_engage_entitlement(
    client: Any, org_id: uuid.UUID, method: str, path: str, needs: str, body: Any
) -> None:
    from tests.helpers import principal_for

    principal = principal_for(org_id, [f"{needs}:ORG"], entitlements=["notification"])
    with as_principal(principal):
        response = await client.request(method, path, json=body)
    assert response.status_code == 402, (method, path, response.text)


@pytest.mark.parametrize(("method", "path", "needs", "body"), _ROUTES)
async def test_every_route_refuses_the_own_scope(
    client: Any, org_id: uuid.UUID, method: str, path: str, needs: str, body: Any
) -> None:
    with as_principal(caller(org_id, [f"{needs}:OWN"])):
        response = await client.request(method, path, json=body)
    assert response.status_code == 403, (method, path, response.text)


async def test_an_unauthenticated_request_is_refused(client: Any) -> None:
    response = await client.get("/api/v1/engage/automations")
    assert response.status_code in (401, 403)


async def test_update_marks_last_run(session: Any, org_id: uuid.UUID, worker_engine: None) -> None:
    await _template(session, org_id)
    automation_id = await _go_live(session, org_id, _linear(_send_node()))
    await consumers.fold(_event(org_id))
    async with session.begin():
        row = await session.get(Automation, automation_id)
        assert row.last_run_at is not None
        await session.execute(
            update(Automation).where(Automation.id == automation_id).values(name="n")
        )


async def test_lifecycle_over_http_pause_edit_resume_delete(
    client: Any, session: Any, org_id: uuid.UUID
) -> None:
    await _template(session, org_id)
    base = "/api/v1/engage/automations"
    perms = [f"{p}:ORG" for p in (READ, WRITE, SEND)]
    with as_principal(caller(org_id, perms)):
        created = await client.post(base, json={"name": "A", "graph": _linear(_send_node())})
        automation_id = created.json()["id"]
        # A draft can be renamed and re-drawn, but a half-built one is refused at publish only.
        patched = await client.patch(
            f"{base}/{automation_id}", json={"name": "B", "graph": {"nodes": [], "edges": []}}
        )
        assert patched.status_code == 200 and patched.json()["name"] == "B"
        assert patched.json()["validation"]["ok"] is False
        assert (await client.post(f"{base}/{automation_id}/publish")).status_code == 422
        assert (await client.post(f"{base}/{automation_id}/resume")).status_code == 409
        assert (await client.post(f"{base}/{automation_id}/pause")).status_code == 409
        fixed = await client.patch(f"{base}/{automation_id}", json={"graph": _linear(_send_node())})
        assert fixed.json()["validation"]["ok"] is True
        live = await client.post(f"{base}/{automation_id}/publish")
        assert live.json()["status"] == "active"
        # Active automations are not editable or deletable; pause first.
        assert (
            await client.patch(f"{base}/{automation_id}", json={"name": "C"})
        ).status_code == 409
        assert (await client.delete(f"{base}/{automation_id}")).status_code == 409
        paused = await client.post(f"{base}/{automation_id}/pause")
        assert paused.json()["status"] == "paused"
        resumed = await client.post(f"{base}/{automation_id}/resume")
        assert resumed.json()["status"] == "active" and resumed.json()["version"] == 1
        await client.post(f"{base}/{automation_id}/pause")
        assert (await client.delete(f"{base}/{automation_id}")).status_code == 204
        assert (await client.get(f"{base}/{automation_id}")).status_code == 404
        assert (await client.get(base)).json()["total"] == 0


async def test_a_template_ref_may_be_the_row_id_or_the_code(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    from app.models import ProviderTemplate

    await _template(session, org_id)
    async with session.begin():
        email_id = (
            await session.execute(select(Template.id).where(Template.organization_id == org_id))
        ).scalar_one()
        provider = ProviderTemplate(
            organization_id=org_id,
            code="order_update",
            channel="whatsapp",
            provider_name="order_update_v2",
            language="en",
            category="utility",
            status="approved",
            variables=[
                {"key": "order", "component": "body", "position": 0, "kind": "text"},
            ],
            preview_text="Order update",
        )
        session.add(provider)
        await session.flush()
        provider_id = provider.id
    by_id = _send_node(template={"ref": str(email_id)})
    whatsapp = _send_node(
        channel="whatsapp",
        template={"ref": "order_update_v2"},  # the provider's name, not our code
        recipient_path="payload.requester.phone",
        variable_map={"1": "payload.number"},  # positional spelling
    )
    whatsapp["id"] = "wa"
    graph = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"event_type": EVENT}},
            by_id,
            whatsapp,
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"from": "t", "to": "send"},
            {"from": "send", "to": "wa"},
            {"from": "wa", "to": "end"},
        ],
    }
    await _go_live(session, org_id, graph)
    await consumers.fold(
        _event(org_id, requester={"email": "a@example.com", "phone": "+919812345678"})
    )
    (run,) = await _runs(session, org_id)
    assert run.state == "completed", run.failure_code
    channels = sorted(m.channel for m in await _messages(session, org_id))
    assert channels == ["email", "whatsapp"]
    assert provider_id is not None


async def test_stats_range_and_failure_reasons(
    client: Any, session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    await _go_live(session, org_id, _linear(_send_node()))
    await consumers.fold(_event(org_id, "T-1"))
    await consumers.fold(envelope_for(org_id, EVENT, {"number": "T-2"}))  # no recipient: fails
    today = datetime.now(UTC).date().isoformat()
    with as_principal(caller(org_id, [f"{READ}:ORG"])):
        both = await client.get(
            "/api/v1/engage/automations/stats", params={"from": today, "to": today}
        )
        assert both.status_code == 200, both.text
        body = both.json()
        assert (body["executions"], body["successful"], body["failed"]) == (2, 1, 1)
        assert body["series"] == [{"date": today, "successful": 1, "failed": 1}]
        assert body["failures_by_reason"] == [
            {"code": "recipient_missing", "label": "No recipient on the event", "count": 1}
        ]
        assert body["per_automation"][0]["success_rate"] == 0.5
        assert body["avg_duration_ms"] is not None
        past = await client.get(
            "/api/v1/engage/automations/stats",
            params={"from": "2020-01-01", "to": "2020-01-31"},
        )
        assert past.json()["executions"] == 0 and past.json()["series"] == []
        inverted = await client.get(
            "/api/v1/engage/automations/stats", params={"from": today, "to": "2020-01-01"}
        )
        assert inverted.status_code == 422


@asynccontextmanager
async def _cron_driver(resolve_target: Any = None) -> AsyncIterator[Any]:
    """The worker's `CampaignDriver` wired as production wires it: cross-tenant discovery on the
    BYPASSRLS outbox role, every write on the app role under the tenant binding."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.cron import CampaignDriver
    from platform_core.db import TENANT_GUC

    cfg = settings()
    args = {"server_settings": {"search_path": f"{SCHEMA},public"}}
    discovery_engine = create_async_engine(cfg.outbox_dsn, connect_args=args)
    work_engine = create_async_engine(cfg.app_dsn, connect_args=args)
    work_maker = async_sessionmaker(work_engine, expire_on_commit=False)

    @asynccontextmanager
    async def work_scope(organization_id: uuid.UUID) -> AsyncIterator[Any]:
        async with work_maker() as s, s.begin():
            await s.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :org, true)"),
                {"org": str(organization_id)},
            )
            yield s

    try:
        yield CampaignDriver(
            discovery_factory=async_sessionmaker(discovery_engine, expire_on_commit=False),
            work_scope=work_scope,
            resolve_target=resolve_target,
        )
    finally:
        await discovery_engine.dispose()
        await work_engine.dispose()


async def test_the_cron_tick_wakes_an_overdue_wait_as_the_outbox_role_would(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    """The driver's own tick (cross-tenant discovery on the BYPASSRLS outbox role, writes on the
    app role under the tenant binding) resumes a wait whose scheduler wake-up never came."""
    await _template(session, org_id)
    wait = {"id": "w", "type": "wait", "config": {"seconds": 60}}
    await _go_live(session, org_id, _linear(wait, _send_node()))
    await consumers.fold(_event(org_id))

    async with _cron_driver() as driver:
        # Not overdue yet: nothing to do.
        await driver.tick(datetime.now(UTC))
        assert (await _runs(session, org_id))[0].state == "waiting"
        # An hour on, the scheduler's wake-up is long overdue; the tick resumes it.
        await driver.tick(datetime.now(UTC) + timedelta(hours=1))
        (run,) = await _runs(session, org_id)
        assert run.state == "completed"
        await driver.tick(datetime.now(UTC) + timedelta(hours=2))
    assert len(await _messages(session, org_id)) == 1


async def test_the_sweep_resolves_a_contact_id_recipient_like_the_scheduler_wake_does(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    """A run parked before a contact-id send, woken by the lost-wake sweep, must be handed the
    contact resolver; without it the send fails as `contact_lookup_unavailable` and backs off."""
    await _template(session, org_id)
    wait = {"id": "w", "type": "wait", "config": {"seconds": 60}}
    contact_send = _send_node(recipient_path="payload.contact_id", recipient_kind="contact_id")
    await _go_live(session, org_id, _linear(wait, contact_send))
    contact_id = str(uuid.uuid4())
    await consumers.fold(envelope_for(org_id, EVENT, {"number": "T-1", "contact_id": contact_id}))
    (run,) = await _runs(session, org_id)
    assert run.state == "waiting"

    asked: list[tuple[str, str, str]] = []

    async def resolver(org: str, contact: str, channel: str) -> str | None:
        asked.append((org, contact, channel))
        return "asha@example.com"

    async with _cron_driver(resolver) as driver:
        await driver.tick(datetime.now(UTC) + timedelta(hours=1))

    (run,) = await _runs(session, org_id)
    assert run.state == "completed", run.failure_code
    assert asked == [(str(org_id), contact_id, "email")]
    (message,) = await _messages(session, org_id)
    assert message.to_address == "asha@example.com"


# --------------------------------------------------------------------- review fixes (round 1)

OTHER_EVENT = "support.desk.ticket.resolved"


async def test_a_resumed_automation_runs_its_published_trigger_not_the_edited_draft(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    """Publish on X, pause, repoint the draft at Y, resume: v1 is still what is live, so it fires
    on X's events and never on Y's. The row's own trigger copy follows the draft and is not what
    the executor matches on."""
    await _template(session, org_id)
    automation_id = await _go_live(session, org_id, _linear(_send_node()))
    ctx = _ctx(org_id, WRITE, SEND)
    async with session.begin():
        await a1_services.set_status(session, ctx, automation_id=automation_id, target="paused")
    async with session.begin():
        row = await a1_services.update_automation(
            session,
            ctx,
            automation_id=automation_id,
            fields={"graph": _linear(_send_node(), trigger=OTHER_EVENT)},
        )
        assert row.trigger_event == OTHER_EVENT  # the draft copy moved; the version did not
    async with session.begin():
        await a1_services.set_status(session, ctx, automation_id=automation_id, target="active")

    # Y's payload must not drive v1's graph...
    async with session.begin():
        started = await a1_services.start_runs(
            session,
            organization_id=org_id,
            envelope=envelope_for(org_id, OTHER_EVENT, _event(org_id)["payload"]),
        )
    assert started == [] and await _runs(session, org_id) == []
    assert await _messages(session, org_id) == []
    # ...and X's still does.
    await consumers.fold(_event(org_id))
    (run,) = await _runs(session, org_id)
    assert run.state == "completed" and run.version == 1
    assert len(await _messages(session, org_id)) == 1


async def _park_then_pause(
    session: Any, org_id: uuid.UUID, *, delete: bool
) -> tuple[uuid.UUID, dict[str, Any]]:
    """A run parked on a wait; its automation then paused, and deleted when `delete`."""
    await _template(session, org_id)
    wait = {"id": "w", "type": "wait", "config": {"seconds": 3600}}
    automation_id = await _go_live(session, org_id, _linear(wait, _send_node()))
    await consumers.fold(_event(org_id))
    (parked,) = await _outbox(session, "platform.schedule.requested")
    ctx = _ctx(org_id, WRITE, SEND)
    async with session.begin():
        await a1_services.set_status(session, ctx, automation_id=automation_id, target="paused")
    if delete:
        async with session.begin():
            await a1_services.delete_automation(session, ctx, automation_id=automation_id)
    return automation_id, parked.payload["target"]["payload"]


async def test_a_waiting_run_of_a_deleted_automation_ends_skipped_and_sends_nothing(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    _, wake_payload = await _park_then_pause(session, org_id, delete=True)

    wake = envelope_for(org_id, a1_services.EVT_RUN_WAKE, wake_payload)
    assert await consumers.wake_automation_run(wake) == "skipped"

    (run,) = await _runs(session, org_id)
    assert run.state == "skipped" and run.failure_code == "automation_deleted"
    assert run.finished_at is not None and run.wake_token is None and run.next_wake_at is None
    assert await _messages(session, org_id) == []
    assert await _outbox(session, services.EVT_MESSAGE_QUEUED) == []
    steps = await _steps(session, org_id, run.id)
    assert [(s.type, s.state) for s in steps] == [("trigger", "done"), ("wait", "skipped")]
    assert steps[-1].detail == "automation_deleted"
    # The same wake-up arriving again, and the sweep, find nothing left to do.
    again = envelope_for(org_id, a1_services.EVT_RUN_WAKE, wake_payload)
    assert await consumers.wake_automation_run(again) == "stale"
    async with session.begin():
        later = datetime.now(UTC) + timedelta(hours=2)
        assert await a1_services.sweep_due(session, organization_id=org_id, now=later) == 0
    assert await _messages(session, org_id) == []


async def test_the_sweep_also_skips_a_waiting_run_of_a_deleted_automation(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _park_then_pause(session, org_id, delete=True)
    async with _cron_driver() as driver:
        await driver.tick(datetime.now(UTC) + timedelta(hours=2))
    (run,) = await _runs(session, org_id)
    assert run.state == "skipped" and run.failure_code == "automation_deleted"
    assert await _messages(session, org_id) == []


async def test_pausing_still_lets_a_waiting_run_finish(
    session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    """Pause stops new runs starting; in-flight ones finish (documented). Only delete cuts them."""
    _, wake_payload = await _park_then_pause(session, org_id, delete=False)
    wake = envelope_for(org_id, a1_services.EVT_RUN_WAKE, wake_payload)
    assert await consumers.wake_automation_run(wake) == "resumed"
    (run,) = await _runs(session, org_id)
    assert run.state == "completed"
    assert len(await _messages(session, org_id)) == 1


async def test_a_failed_run_of_a_deleted_automation_cannot_be_retried(
    client: Any, session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    automation_id = await _go_live(session, org_id, _linear(_send_node()))
    await _drop_template(session, org_id)
    await consumers.fold(_event(org_id))
    (run,) = await _runs(session, org_id)
    assert run.state == "failed"
    run_id = run.id

    ctx = _ctx(org_id, WRITE, SEND)
    async with session.begin():
        await a1_services.set_status(session, ctx, automation_id=automation_id, target="paused")
    async with session.begin():
        await a1_services.delete_automation(session, ctx, automation_id=automation_id)
    # The cause of the failure is fixed, so a retry that were allowed would now really send.
    async with session.begin():
        await session.execute(
            update(Template).where(Template.organization_id == org_id).values(deleted_at=None)
        )

    with as_principal(caller(org_id, [f"{WRITE}:ORG"])):
        response = await client.post(f"/api/v1/engage/automations/runs/{run_id}/retry")
    assert response.status_code == 409, response.text
    assert response.json()["errors"][0]["code"] == "automation_deleted"
    with pytest.raises(ConflictProblem):
        async with session.begin():
            await a1_services.retry_run(session, ctx, run_id=run_id)

    (run,) = await _runs(session, org_id)
    assert run.state == "failed" and run.failure_code == "template_missing"
    assert await _messages(session, org_id) == []


async def test_an_invalid_recipient_never_puts_the_address_in_the_run_detail(
    client: Any, session: Any, org_id: uuid.UUID, worker_engine: None
) -> None:
    await _template(session, org_id)
    await _go_live(session, org_id, _linear(_send_node()))
    address = "jane.doe-at-example.com"  # no @: the rule's own message would quote it verbatim
    await consumers.fold(_event(org_id, requester={"email": address}))

    (run,) = await _runs(session, org_id)
    assert run.state == "failed" and run.failure_code == "recipient_invalid"
    failed = [s for s in await _steps(session, org_id, run.id) if s.state == "failed"]
    assert [s.detail for s in failed] == ["recipient_invalid: the recipient is not a valid address"]
    assert address not in run.subject

    with as_principal(caller(org_id, [f"{READ}:ORG"])):
        response = await client.get(f"/api/v1/engage/automations/runs/{run.id}")
    assert response.status_code == 200, response.text
    assert address not in response.text
    assert "jane.doe" not in response.text


STATS = "/api/v1/engage/automations/stats"


@pytest.mark.parametrize(
    "params",
    [
        {"from": "0001-01-01"},  # `to` defaults to today: a 2000-year scan
        {"from": "2000-01-01", "to": "2026-09-26"},  # longer than a year
        {"to": "9999-12-31"},  # the day after does not exist
        {"to": "0001-01-01"},  # the default start before it does not exist
        {"from": "2026-09-12", "to": "2026-09-10"},  # inverted
    ],
)
async def test_stats_bound_the_range_however_it_is_sent(
    client: Any, org_id: uuid.UUID, params: dict[str, str]
) -> None:
    with as_principal(caller(org_id, [f"{READ}:ORG"])):
        response = await client.get(STATS, params=params)
    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_stats_are_rate_limited_per_organization(client: Any, org_id: uuid.UUID) -> None:
    from app.api.m1_analytics import OVERVIEW_LIMIT_PER_MINUTE

    statuses: list[int] = []
    with as_principal(caller(org_id, [f"{READ}:ORG"])):
        # A fixed one-minute window can roll over mid-test: allow two windows' worth of calls.
        for _ in range(2 * OVERVIEW_LIMIT_PER_MINUTE + 2):
            response = await client.get(STATS)
            statuses.append(response.status_code)
            if response.status_code == 429:
                break
    assert statuses[-1] == 429, statuses
    assert set(statuses[:-1]) == {200}
    assert response.headers["retry-after"] == "60"
    with as_principal(caller(uuid.uuid4(), [f"{READ}:ORG"])):
        assert (await client.get(STATS)).status_code == 200
