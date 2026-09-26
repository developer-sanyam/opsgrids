"""Writes and the executor for the automation workflow engine (slice A1).

Two halves, one transaction discipline. The **authoring** half (`create_automation`, `publish`, ...)
acts as a principal and follows the platform shape: change the rows, audit, all in the caller's
transaction. The **executor** half (`start_runs`, `wake_run`, `retry_run`, `sweep_due`) acts on an
event or a wake-up: it runs inside the consumer's per-message transaction, the same one that marks
the event processed, so a crash before commit re-runs the whole thing and a redelivery after commit
is a no-op.

Idempotency is layered, on purpose:

* `processed_events` (the consumer's own ledger) drops a redelivered event.
* `a1_runs` is unique per `(organization_id, automation_id, source_event_id)`; the insert is `ON
  CONFLICT DO NOTHING`, so even an event that gets past the ledger starts at most one run.
* a send's message carries the dedupe key `a1:<run_id>:<node_id>`, so re-running a step (a retry,
  a replay) can never stage a second message.
* a wake-up carries a token that rotates on every wait; the row is locked, the token compared and
  cleared in one transaction, so a wait resumes exactly once however many wake-ups arrive (the
  scheduler's, the redelivered one, the sweep's).

Nothing here talks to a provider. A send step stages a message through `services.stage_message` —
consent, suppression, dedupe and the per-tenant transport all apply exactly as for a rule or a
campaign — and the dispatcher sends it. A consent refusal is not an error: the message is recorded
as suppressed, the step is `skipped` with the machine-readable reason, and the flow moves on.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import a1_pure, a1_selectors, consent, domain, selectors, services
from app.a1_models import Automation, AutomationRun, AutomationRunStep, AutomationVersion
from app.config import settings
from app.models import AuditLog, OutboxEvent, ProviderTemplate, Template
from app.targets import TargetResolver, TargetUnavailable
from platform_core import pii
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import (
    ConflictProblem,
    NotFoundProblem,
    ValidationProblem,
)
from platform_core.events import publish_event, schedule_event
from platform_core.logging import get_logger

log = get_logger("notification.automations")

EVT_RUN_WAKE: Final = "notification.automation.run_wake"
EVT_TASK_REQUESTED: Final = "notification.automation.task_requested"
ACTOR: Final = {"type": "system", "id": "system:automation"}

#: A run that has visited this many nodes without finishing is a graph we should not have let
#: publish; failing it is cheaper than a runaway loop inside a consumer transaction.
MAX_HOPS: Final = 200

#: How overdue a wake-up must be before the sweep steps in. The scheduler fires within seconds;
#: this only ever catches a lost one.
SWEEP_GRACE: Final = timedelta(minutes=5)

FAILURE_LABELS: Final[dict[str, str]] = {
    "recipient_missing": "No recipient on the event",
    "recipient_invalid": "Recipient is not a valid address",
    "contact_lookup_unavailable": "Contact lookup unavailable",
    "template_missing": "Template not found",
    "template_not_approved": "Template not approved",
    "variables_missing": "A template variable had no value",
    "graph_invalid": "Automation graph is invalid",
    "unexpected_error": "Unexpected error",
}


class PublishProblem(ValidationProblem):
    """422 carrying the same `problems` list the detail's `validation` shows."""

    def __init__(self, detail: str, problems: list[dict[str, Any]]) -> None:
        super().__init__(detail, errors=problems)
        self.problems = problems

    def to_problem(self, request: Request) -> dict[str, Any]:
        body = super().to_problem(request)
        body["problems"] = self.problems
        return body


class StepFailure(Exception):
    """One node could not do its job. `retryable` says whether trying again can help."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {"type": "user", "id": str(ctx.principal.sub)}


def module_of(event_type: str) -> str:
    return event_type.split(".", 1)[0] if event_type else ""


# --------------------------------------------------------------------------- validation


async def collect_problems(
    session: AsyncSession, organization_id: uuid.UUID, graph: dict[str, Any]
) -> list[a1_pure.Problem]:
    """Structural problems plus the ones that need the database: templates that do not resolve,
    variables the map does not cover, and a channel that is not connected (a warning)."""
    problems, _ = a1_pure.validate_graph(graph, subscribable_events=domain.SUBSCRIBABLE_EVENTS)
    sends = [
        n for n in graph.get("nodes", []) if isinstance(n, dict) and n.get("type") == "send_message"
    ]
    sends = [
        n
        for n in sends
        if isinstance(n.get("config"), dict)
        and n["config"].get("channel") in a1_pure.CHANNELS
        and isinstance(n["config"].get("template"), dict)
        and str(n["config"]["template"].get("ref") or "").strip()
    ]
    if not sends:
        return problems
    refs = {str(n["config"]["template"]["ref"]) for n in sends}
    provider = {
        (t.code, t.channel): t
        for t in (
            await session.execute(
                select(ProviderTemplate).where(
                    ProviderTemplate.organization_id == organization_id,
                    ProviderTemplate.code.in_(refs),
                    ProviderTemplate.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    }
    own = {
        (t.code, t.channel): t
        for t in (
            await session.execute(
                select(Template).where(
                    Template.organization_id == organization_id,
                    Template.code.in_(refs),
                    Template.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    }
    connected: dict[str, bool] = {}
    channels = {n["config"]["channel"] for n in sends}
    if "whatsapp" in channels:
        connected["whatsapp"] = (
            await selectors.get_whatsapp_connection(session, organization_id) is not None
        )
    if "email" in channels:
        connected["email"] = await selectors.get_connection(session, organization_id) is not None

    for node in sends:
        cfg = node["config"]
        node_id, channel, ref = str(node["id"]), cfg["channel"], str(cfg["template"]["ref"])
        mapped = set((cfg.get("variable_map") or {}).keys())
        if channel == "whatsapp":
            template = provider.get((ref, "whatsapp"))
            if template is None:
                problems.append(
                    a1_pure.Problem(node_id, "template_missing", f"No WhatsApp template {ref!r}")
                )
            elif not domain.template_is_sendable(template.status):
                problems.append(
                    a1_pure.Problem(
                        node_id,
                        "template_not_approved",
                        f"Template {ref!r} is {template.status}; only approved templates send",
                    )
                )
            else:
                needed = _provider_variable_slots(template)
                missing = [
                    key for key, index in needed if key not in mapped and index not in mapped
                ]
                if missing:
                    problems.append(
                        a1_pure.Problem(
                            node_id,
                            "variables_unmapped",
                            "Map a value for: " + ", ".join(missing),
                        )
                    )
        else:
            email_template = own.get((ref, "email"))
            if email_template is None:
                problems.append(
                    a1_pure.Problem(node_id, "template_missing", f"No email template {ref!r}")
                )
            else:
                wanted = domain.placeholders(email_template.body)
                if email_template.subject:
                    wanted |= domain.placeholders(email_template.subject)
                missing = sorted(wanted - mapped)
                if missing:
                    problems.append(
                        a1_pure.Problem(
                            node_id,
                            "variables_unmapped",
                            "Map a value for: " + ", ".join(missing),
                        )
                    )
        if not connected.get(channel, False):
            problems.append(
                a1_pure.Problem(
                    node_id,
                    "channel_not_connected",
                    f"{channel} is not connected yet; sends will fail until it is",
                    severity=a1_pure.WARNING,
                )
            )
    return problems


def _provider_variable_slots(template: ProviderTemplate) -> list[tuple[str, str]]:
    """`(key, positional index)` for each variable, so a map may use either spelling."""
    return [(str(v.get("key")), str(i)) for i, v in enumerate(template.variables or [], start=1)]


def has_errors(problems: Iterable[a1_pure.Problem]) -> bool:
    return any(p.severity == a1_pure.ERROR for p in problems)


# --------------------------------------------------------------------------- authoring


def _check_shell(graph: dict[str, Any]) -> None:
    """A draft may be incomplete, but it must be a graph we can store and later reason about."""
    nodes, edges = graph.get("nodes"), graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValidationProblem("graph needs nodes and edges lists")
    if len(nodes) > a1_pure.MAX_NODES:
        raise ValidationProblem(f"a graph has at most {a1_pure.MAX_NODES} nodes")


def _denormalise(row: Automation, graph: dict[str, Any]) -> None:
    event = a1_pure.trigger_event(graph) or ""
    row.trigger_event = event
    row.module = module_of(event)
    row.draft_graph = graph


async def create_automation(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    name: str,
    description: str | None,
    graph: dict[str, Any],
    request: Request | None = None,
) -> Automation:
    _check_shell(graph)
    row = Automation(
        organization_id=ctx.organization_id,
        name=name.strip(),
        description=description,
        status="draft",
        created_by=str(ctx.principal.sub),
        updated_by=str(ctx.principal.sub),
    )
    _denormalise(row, graph)
    session.add(row)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.automation.created",
        entity="automation",
        entity_id=row.id,
        after={"name": row.name, "trigger_event": row.trigger_event},
        request=request,
    )
    return row


async def _load(
    session: AsyncSession,
    organization_id: uuid.UUID,
    automation_id: uuid.UUID,
    *,
    lock: bool = True,
) -> Automation:
    row = await a1_selectors.get_automation(session, organization_id, automation_id, lock=lock)
    if row is None:
        raise NotFoundProblem("No such automation")
    return row


async def update_automation(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    automation_id: uuid.UUID,
    fields: dict[str, Any],
    request: Request | None = None,
) -> Automation:
    row = await _load(session, ctx.organization_id, automation_id)
    if row.status not in ("draft", "paused"):
        raise ConflictProblem(
            f"Only a draft or paused automation can be edited; this is {row.status}"
        )
    if "name" in fields and fields["name"] is not None:
        row.name = str(fields["name"]).strip()
    if "description" in fields:
        row.description = fields["description"]
    if fields.get("graph") is not None:
        _check_shell(fields["graph"])
        _denormalise(row, fields["graph"])
    row.updated_by = str(ctx.principal.sub)
    row.updated_at = datetime.now(UTC)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.automation.updated",
        entity="automation",
        entity_id=row.id,
        after={"fields": sorted(fields)},
        request=request,
    )
    return row


async def delete_automation(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    automation_id: uuid.UUID,
    request: Request | None = None,
) -> None:
    row = await _load(session, ctx.organization_id, automation_id)
    if row.status not in ("draft", "paused"):
        raise ConflictProblem(f"Pause an automation before deleting it; this one is {row.status}")
    now = datetime.now(UTC)
    row.deleted_at = now
    row.updated_by = str(ctx.principal.sub)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.automation.deleted",
        entity="automation",
        entity_id=row.id,
        request=request,
    )


async def publish(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    automation_id: uuid.UUID,
    request: Request | None = None,
) -> Automation:
    """Validate the draft and freeze it as the next immutable version. 422 with `problems` when it
    cannot go live; warnings (a channel not connected yet) do not block."""
    row = await _load(session, ctx.organization_id, automation_id)
    if row.status not in ("draft", "paused"):
        raise ConflictProblem(
            f"Only a draft or paused automation can be published; this is {row.status}"
        )
    problems = await collect_problems(session, ctx.organization_id, row.draft_graph)
    if has_errors(problems):
        raise PublishProblem(
            "This automation cannot be published yet", [p.as_dict() for p in problems]
        )
    latest = (
        await session.execute(
            select(AutomationVersion.version)
            .where(
                AutomationVersion.organization_id == ctx.organization_id,
                AutomationVersion.automation_id == row.id,
            )
            .order_by(AutomationVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    number = (latest or 0) + 1
    version = AutomationVersion(
        organization_id=ctx.organization_id,
        automation_id=row.id,
        version=number,
        trigger_event=row.trigger_event,
        graph=row.draft_graph,
        published_by=str(ctx.principal.sub),
    )
    session.add(version)
    await session.flush()
    before = row.status
    row.status = "active"
    row.current_version_id = version.id
    row.current_version = number
    row.updated_by = str(ctx.principal.sub)
    row.updated_at = datetime.now(UTC)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.automation.published",
        entity="automation",
        entity_id=row.id,
        before={"status": before},
        after={"status": "active", "version": number},
        request=request,
    )
    return row


async def set_status(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    automation_id: uuid.UUID,
    target: str,
    request: Request | None = None,
) -> Automation:
    """`pause` (active -> paused) and `resume` (paused -> active, only if it was ever published)."""
    row = await _load(session, ctx.organization_id, automation_id)
    if target == "paused":
        if row.status != "active":
            raise ConflictProblem(f"Only an active automation can be paused; this is {row.status}")
    elif row.status != "paused" or row.current_version_id is None:
        raise ConflictProblem("Only a paused, previously published automation can be resumed")
    before = row.status
    row.status = target
    row.updated_by = str(ctx.principal.sub)
    row.updated_at = datetime.now(UTC)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action=f"engage.automation.{'paused' if target == 'paused' else 'resumed'}",
        entity="automation",
        entity_id=row.id,
        before={"status": before},
        after={"status": target},
        request=request,
    )
    return row


# --------------------------------------------------------------------------- run context


def _seal_context(context: dict[str, Any]) -> bytes:
    sealed, _ = pii.seal(json.dumps(context, default=str), kind="generic", settings=settings())
    return sealed


def _open_context(run: AutomationRun) -> dict[str, Any]:
    raw = pii.open_sealed(run.context_enc, settings=settings())
    loaded = json.loads(raw)
    return loaded if isinstance(loaded, dict) else {"payload": {}, "run": {}}


def _referenced_paths(graph: dict[str, Any]) -> set[str]:
    """Every `payload.*` path the graph reads, so a run keeps only that slice of the event."""
    paths: set[str] = set()
    for node in graph.get("nodes", []):
        cfg = node.get("config") or {}
        for rule in cfg.get("rules") or []:
            if isinstance(rule, dict) and isinstance(rule.get("path"), str):
                paths.add(rule["path"])
        if isinstance(cfg.get("recipient_path"), str):
            paths.add(cfg["recipient_path"])
        for value in (cfg.get("variable_map") or {}).values():
            if isinstance(value, str):
                paths.add(value)
    return {p for p in paths if p.startswith("payload.")}


def _slice_payload(payload: dict[str, Any], paths: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path in paths:
        segments = path.split(".")[1:]
        value = a1_pure.read_path_strict(path, payload, {})
        if value is a1_pure._MISSING:
            continue
        cursor = out
        for segment in segments[:-1]:
            nxt = cursor.setdefault(segment, {})
            if not isinstance(nxt, dict):
                break
            cursor = nxt
        else:
            cursor[segments[-1]] = value
    return out


# --------------------------------------------------------------------------- executor


async def start_runs(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    envelope: dict[str, Any],
    resolve_target: TargetResolver | None = None,
    now: datetime | None = None,
) -> list[AutomationRun]:
    """Start one run per active automation whose trigger is this event, and drive each until it
    waits, ends or fails. Called from the consumer's fold, in its transaction."""
    now = now or datetime.now(UTC)
    event_type = str(envelope.get("event_type", ""))
    raw_id = envelope.get("event_id")
    if not raw_id:
        return []
    source_event_id = uuid.UUID(str(raw_id))
    payload = envelope.get("payload") or {}

    automations = list(
        (
            await session.execute(
                select(Automation).where(
                    Automation.organization_id == organization_id,
                    Automation.trigger_event == event_type,
                    Automation.status == "active",
                    Automation.deleted_at.is_(None),
                    Automation.current_version_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not automations:
        return []
    versions = {
        v.id: v
        for v in (
            await session.execute(
                select(AutomationVersion).where(
                    AutomationVersion.organization_id == organization_id,
                    AutomationVersion.id.in_([a.current_version_id for a in automations]),
                )
            )
        )
        .scalars()
        .all()
    }

    started: list[AutomationRun] = []
    for automation in automations:
        version = (
            versions.get(automation.current_version_id) if automation.current_version_id else None
        )
        if version is None:
            log.error("automation.version_missing", automation_id=str(automation.id))
            continue
        graph = version.graph
        trigger = next((n for n in graph["nodes"] if n["type"] == "trigger"), None)
        if trigger is None:
            continue
        context = {
            "payload": _slice_payload(payload, _referenced_paths(graph)),
            "run": {"previous": {}},
        }
        inserted = (
            await session.execute(
                pg_insert(AutomationRun)
                .values(
                    id=uuid.uuid4(),
                    organization_id=organization_id,
                    automation_id=automation.id,
                    version_id=version.id,
                    version=version.version,
                    source_event_id=source_event_id,
                    idempotency_key=f"{automation.id}:{source_event_id}",
                    state="running",
                    started_at=now,
                    subject=event_type[:254],
                    current_node_id=trigger["id"],
                    context_enc=_seal_context(context),
                )
                .on_conflict_do_nothing(constraint="uq_a1_runs_org_auto_event")
                .returning(AutomationRun.id)
            )
        ).scalar_one_or_none()
        if inserted is None:
            log.info(
                "automation.run.duplicate",
                automation_id=str(automation.id),
                source_event_id=str(source_event_id),
            )
            continue
        run = await session.get(AutomationRun, inserted)
        assert run is not None
        automation.last_run_at = now
        await _drive(
            session,
            run,
            graph,
            context,
            resolve_target=resolve_target,
            resume_step=None,
            now=now,
        )
        started.append(run)
    return started


async def _steps_for(
    session: AsyncSession, organization_id: uuid.UUID, run_id: uuid.UUID
) -> list[AutomationRunStep]:
    return list(
        (
            await session.execute(
                select(AutomationRunStep)
                .where(
                    AutomationRunStep.organization_id == organization_id,
                    AutomationRunStep.run_id == run_id,
                )
                .order_by(AutomationRunStep.seq)
            )
        )
        .scalars()
        .all()
    )


def _finish_step(
    step: AutomationRunStep, state: str, now: datetime, detail: str | None = None
) -> None:
    step.state = state
    step.finished_at = now
    if detail is not None:
        step.detail = detail[:500]


async def _drive(
    session: AsyncSession,
    run: AutomationRun,
    graph: dict[str, Any],
    context: dict[str, Any],
    *,
    resolve_target: TargetResolver | None,
    resume_step: AutomationRunStep | None,
    now: datetime,
) -> None:
    """Execute nodes from `run.current_node_id` until the run waits, ends or fails."""
    nodes = a1_pure.node_map(graph)
    hops = 0
    while run.state == "running":
        hops += 1
        node = nodes.get(run.current_node_id or "")
        if hops > MAX_HOPS or node is None:
            _fail_run(run, "graph_invalid", now)
            break
        step = resume_step
        resume_step = None
        if step is None:
            run.step_count += 1
            step = AutomationRunStep(
                organization_id=run.organization_id,
                run_id=run.id,
                seq=run.step_count,
                node_id=node["id"],
                type=node["type"],
                state="running",
                started_at=now,
                attempt=1,
            )
            session.add(step)
        else:
            step.state = "running"
            step.attempt += 1
        await session.flush()

        kind = node["type"]
        try:
            label = await _execute(
                session, run, node, step, context, resolve_target=resolve_target, now=now
            )
        except StepFailure as failure:
            if failure.retryable and a1_pure.should_retry(step.attempt):
                delay = a1_pure.retry_delay_seconds(step.attempt)
                step.state = "waiting"
                step.detail = f"{failure.code}: retrying in {delay}s"[:500]
                _park(session, run, now + timedelta(seconds=delay))
                return
            _finish_step(step, "failed", now, f"{failure.code}: {failure.message}")
            _fail_run(run, failure.code, now)
            return
        if kind == "wait" and step.state == "waiting":
            return  # parked; `_execute` scheduled the wake-up
        context["run"]["previous"] = {
            "node_id": node["id"],
            "type": kind,
            "status": step.state,
            **({"branch": label} if kind == "condition" else {}),
        }
        run.context_enc = _seal_context(context)
        if kind == "end":
            _complete_run(run, await _steps_for(session, run.organization_id, run.id), now)
            return
        following = a1_pure.next_node_id(graph, node, label=label)
        if following is None:
            _complete_run(run, await _steps_for(session, run.organization_id, run.id), now)
            return
        run.current_node_id = following
    await session.flush()


def _park(session: AsyncSession, run: AutomationRun, wake_at: datetime) -> None:
    """Park the run until `wake_at`: state, token, and one scheduler request keyed on the token."""
    run.state = "waiting"
    run.next_wake_at = wake_at
    run.wake_token = uuid.uuid4().hex
    schedule_event(
        session,
        OutboxEvent,
        organization_id=run.organization_id,
        due_at=wake_at,
        idempotency_key=f"a1-wake:{run.id}:{run.wake_token}",
        target_event_type=EVT_RUN_WAKE,
        target_aggregate_type="automation_run",
        target_aggregate_id=run.id,
        target_payload={
            "organization_id": str(run.organization_id),
            "run_id": str(run.id),
            "wake_token": run.wake_token,
        },
        target_actor=ACTOR,
    )


def _fail_run(run: AutomationRun, code: str, now: datetime) -> None:
    run.state = "failed"
    run.failure_code = code
    run.finished_at = now
    run.next_wake_at = None
    run.wake_token = None


def _complete_run(run: AutomationRun, steps: list[AutomationRunStep], now: datetime) -> None:
    sends = [s for s in steps if s.type == "send_message"]
    consent_skipped = bool(sends) and all(s.state == "skipped" for s in sends)
    run.state = "skipped" if consent_skipped else "completed"
    run.finished_at = now
    run.next_wake_at = None
    run.wake_token = None


async def _execute(
    session: AsyncSession,
    run: AutomationRun,
    node: dict[str, Any],
    step: AutomationRunStep,
    context: dict[str, Any],
    *,
    resolve_target: TargetResolver | None,
    now: datetime,
) -> str | None:
    """Run one node. Returns the branch label for a condition, `None` otherwise."""
    kind, config = node["type"], node.get("config") or {}
    payload, run_ctx = context["payload"], context["run"]
    if kind in ("trigger", "end"):
        _finish_step(step, "done", now)
        return None
    if kind == "condition":
        branch = "yes" if a1_pure.evaluate_condition(config, payload, run_ctx) else "no"
        _finish_step(step, "done", now, branch)
        return branch
    if kind == "wait":
        wake_at = a1_pure.wake_time(now, int(config["seconds"]), config.get("window"))
        step.state = "waiting"
        step.detail = f"until {wake_at.isoformat()}"
        _park(session, run, wake_at)
        return None
    if kind == "create_task":
        publish_event(
            session,
            OutboxEvent,
            organization_id=run.organization_id,
            event_type=EVT_TASK_REQUESTED,
            aggregate_type="automation_run",
            aggregate_id=run.id,
            payload={
                "organization_id": str(run.organization_id),
                "automation_id": str(run.automation_id),
                "run_id": str(run.id),
                "node_id": node["id"],
                "title": str(config["title"]),
                "description": config.get("description"),
                "assignee_team": config.get("assignee_team"),
                "source_event_id": str(run.source_event_id),
            },
            actor=ACTOR,
        )
        _finish_step(step, "done", now)
        return None
    if kind == "send_message":
        await _send(session, run, node, step, context, resolve_target=resolve_target, now=now)
        return None
    raise StepFailure("graph_invalid", f"unknown node type {kind!r}")


def _nest(target: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cursor = target
    for part in parts[:-1]:
        nxt = cursor.setdefault(part, {})
        if not isinstance(nxt, dict):
            nxt = cursor[part] = {}
        cursor = nxt
    cursor[parts[-1]] = value


async def _send(
    session: AsyncSession,
    run: AutomationRun,
    node: dict[str, Any],
    step: AutomationRunStep,
    context: dict[str, Any],
    *,
    resolve_target: TargetResolver | None,
    now: datetime,
) -> None:
    """Stage one message through the shared path. Consent refusal is a skip, not a failure."""
    config = node["config"]
    channel = str(config["channel"])
    payload, run_ctx = context["payload"], context["run"]
    org = run.organization_id

    raw = a1_pure.read_path(str(config["recipient_path"]), payload, run_ctx)
    if raw is None or not str(raw).strip():
        raise StepFailure("recipient_missing", "the event carries no recipient")
    if config.get("recipient_kind") == "contact_id":
        if resolve_target is None:
            raise StepFailure("contact_lookup_unavailable", "no contact resolver", retryable=True)
        try:
            raw = await resolve_target(str(org), str(raw), channel)
        except TargetUnavailable as exc:
            raise StepFailure(
                "contact_lookup_unavailable", "customer service unavailable", retryable=True
            ) from exc
        if raw is None:
            raise StepFailure("recipient_missing", "the contact has no address on this channel")
    try:
        to_address = domain.normalize_recipient(channel, str(raw))
    except domain.RuleError as exc:
        raise StepFailure("recipient_invalid", str(exc)) from exc

    values: dict[str, Any] = {}
    variable_map: dict[str, str] = config.get("variable_map") or {}
    for key, path in variable_map.items():
        found = a1_pure.read_path(path, payload, run_ctx)
        if found is not None:
            values[key] = found

    purpose = config.get("purpose") or "transactional"
    category = consent.MARKETING_CATEGORY if purpose == "marketing" else "operational"
    ref = str(config["template"]["ref"])
    subject: str | None
    provider_code: str | None = None
    provider_payload: dict[str, Any] | None = None

    if channel == "whatsapp":
        template = await selectors.get_provider_template(session, org, ref, "whatsapp")
        if template is None:
            raise StepFailure("template_missing", f"no WhatsApp template {ref!r}")
        if not domain.template_is_sendable(template.status):
            raise StepFailure("template_not_approved", f"template {ref!r} is {template.status}")
        language = (config["template"].get("language")) or template.language
        try:
            variables = [domain.TemplateVariable(**v) for v in template.variables]
            ctx_values: dict[str, Any] = {}
            for index, var in enumerate(variables, start=1):
                supplied = values.get(var.key, values.get(str(index)))
                if supplied is not None:
                    _nest(ctx_values, var.key, supplied)
            provider_payload = domain.build_template_payload(
                template_name=template.provider_name,
                language=language,
                variables=variables,
                context=ctx_values,
                parameter_format=template.parameter_format,
            )
        except (domain.TemplateError, TypeError) as exc:
            raise StepFailure("variables_missing", str(exc)) from exc
        subject, body, provider_code = None, template.preview_text, template.code
    else:
        email_template = await selectors.get_template(session, org, ref, "email")
        if email_template is None:
            raise StepFailure("template_missing", f"no email template {ref!r}")
        ctx_values = {}
        for key, value in values.items():
            _nest(ctx_values, key, value)
        try:
            body = domain.render(email_template.body, ctx_values)
            subject = (
                domain.render(email_template.subject, ctx_values)
                if email_template.subject
                else None
            )
        except domain.RenderError as exc:
            raise StepFailure("variables_missing", str(exc)) from exc

    dedupe = f"a1:{run.id}:{node['id']}"
    async with session.begin_nested():
        message = await services.stage_message(
            session,
            organization_id=org,
            channel=channel,
            category=category,
            to_address=to_address,
            subject=subject,
            body=body,
            created_by=services.SYSTEM_ACTOR,
            rule_code=f"automation:{run.automation_id}",
            event_type=None,
            source_event_id=run.source_event_id,
            dedupe_key=dedupe,
            actor=ACTOR,
            provider_template_code=provider_code,
            provider_template_payload=provider_payload,
        )
    if message is None:
        # Already staged by an earlier pass over this same step: reuse it, never send twice.
        message = await selectors.find_by_dedupe_key(session, org, dedupe)
    run.subject = pii.mask(to_address, kind="email" if channel == "email" else "phone")[:254]
    if message is None:
        raise StepFailure("unexpected_error", "the message could not be staged", retryable=True)
    step.message_id = message.id
    if message.state == domain.STATE_SUPPRESSED:
        _finish_step(step, "skipped", now, message.suppression_reason or "suppressed")
    else:
        _finish_step(step, "done", now)


# ------------------------------------------------------------------------ wake and retry


async def _locked_run(
    session: AsyncSession, organization_id: uuid.UUID, run_id: uuid.UUID
) -> AutomationRun | None:
    return (
        await session.execute(
            select(AutomationRun)
            .where(AutomationRun.id == run_id, AutomationRun.organization_id == organization_id)
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _graph_for(session: AsyncSession, run: AutomationRun) -> dict[str, Any] | None:
    version = (
        await session.execute(
            select(AutomationVersion).where(
                AutomationVersion.id == run.version_id,
                AutomationVersion.organization_id == run.organization_id,
            )
        )
    ).scalar_one_or_none()
    return version.graph if version is not None else None


async def wake_run(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    run_id: uuid.UUID,
    wake_token: str | None,
    resolve_target: TargetResolver | None = None,
    now: datetime | None = None,
) -> str:
    """Resume a parked run exactly once. `stale` when the token no longer matches (already woken,
    superseded or finished), `resumed` otherwise. The row lock plus the token compare-and-clear is
    the whole guarantee: two wake-ups racing serialise on the lock and the second sees no token."""
    now = now or datetime.now(UTC)
    run = await _locked_run(session, organization_id, run_id)
    if run is None or run.state != "waiting" or not wake_token or run.wake_token != wake_token:
        log.info("automation.wake.stale", run_id=str(run_id))
        return "stale"
    graph = await _graph_for(session, run)
    if graph is None:
        _fail_run(run, "graph_invalid", now)
        return "failed"
    context = _open_context(run)
    nodes = a1_pure.node_map(graph)
    node = nodes.get(run.current_node_id or "")
    steps = await _steps_for(session, organization_id, run.id)
    step = next(
        (s for s in reversed(steps) if s.node_id == run.current_node_id and s.state == "waiting"),
        None,
    )
    run.state = "running"
    run.wake_token = None
    run.next_wake_at = None
    if node is None or step is None:
        _fail_run(run, "graph_invalid", now)
        return "failed"
    if node["type"] == "wait":
        _finish_step(step, "done", now)
        context["run"]["previous"] = {"node_id": node["id"], "type": "wait", "status": "done"}
        following = a1_pure.next_node_id(graph, node, label=None)
        if following is None:
            _complete_run(run, await _steps_for(session, organization_id, run.id), now)
            return "resumed"
        run.current_node_id = following
        resume: AutomationRunStep | None = None
    else:
        resume = step  # a retry of the same node
    await _drive(
        session, run, graph, context, resolve_target=resolve_target, resume_step=resume, now=now
    )
    return "resumed"


async def retry_run(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    run_id: uuid.UUID,
    resolve_target: TargetResolver | None = None,
    request: Request | None = None,
    now: datetime | None = None,
) -> AutomationRun:
    """Re-run a failed run from the step that failed. Only `failed` runs."""
    now = now or datetime.now(UTC)
    run = await _locked_run(session, ctx.organization_id, run_id)
    if run is None:
        raise NotFoundProblem("No such run")
    if run.state != "failed":
        raise ConflictProblem(f"Only a failed run can be retried; this one is {run.state}")
    graph = await _graph_for(session, run)
    if graph is None:
        raise ConflictProblem("The version this run started on is gone")
    steps = await _steps_for(session, ctx.organization_id, run.id)
    failed = next((s for s in reversed(steps) if s.state == "failed"), None)
    if failed is None:
        raise ConflictProblem("This run has no failed step to retry")
    run.state = "running"
    run.failure_code = None
    run.finished_at = None
    run.current_node_id = failed.node_id
    failed.finished_at = None
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="engage.automation.run_retried",
        entity="automation_run",
        entity_id=run.id,
        request=request,
    )
    # Attempts restart: an operator asking for a retry has decided it is worth another go, so the
    # automatic backoff budget is theirs again.
    failed.attempt = 0
    await _drive(
        session,
        run,
        graph,
        _open_context(run),
        resolve_target=resolve_target,
        resume_step=failed,
        now=now,
    )
    return run


async def due_waits(
    session: AsyncSession, organization_id: uuid.UUID, *, now: datetime, limit: int = 100
) -> list[tuple[uuid.UUID, str]]:
    """Waiting runs whose wake-up is overdue by more than `SWEEP_GRACE`: the scheduler's own
    firing normally beats this; only a lost one lands here."""
    rows = (
        await session.execute(
            select(AutomationRun.id, AutomationRun.wake_token)
            .where(
                AutomationRun.organization_id == organization_id,
                AutomationRun.state == "waiting",
                AutomationRun.next_wake_at.is_not(None),
                AutomationRun.next_wake_at <= now - SWEEP_GRACE,
                AutomationRun.wake_token.is_not(None),
            )
            .order_by(AutomationRun.next_wake_at)
            .limit(limit)
        )
    ).all()
    return [(r[0], str(r[1])) for r in rows]


async def sweep_due(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    resolve_target: TargetResolver | None = None,
    now: datetime | None = None,
) -> int:
    """Wake every overdue run in one organization. Returns how many were resumed."""
    now = now or datetime.now(UTC)
    woken = 0
    for run_id, token in await due_waits(session, organization_id, now=now):
        outcome = await wake_run(
            session,
            organization_id=organization_id,
            run_id=run_id,
            wake_token=token,
            resolve_target=resolve_target,
            now=now,
        )
        woken += outcome == "resumed"
    return woken


async def sweep_overdue(
    *,
    discovery_factory: Any,
    work_scope: Any,
    now: datetime | None = None,
    limit: int = 50,
) -> int:
    """Wake overdue runs across organizations. Discovery (which organizations?) runs on the
    BYPASSRLS outbox role with column-level `SELECT` on three `a1_runs` columns; every write runs
    under the organization's own tenant binding via `work_scope`, one transaction per run so one
    bad run cannot roll back another's wake."""
    at = now or datetime.now(UTC)
    async with discovery_factory() as session, session.begin():
        organizations = await orphaned_wakes(session, now=at)
    woken = 0
    for organization_id in organizations[:limit]:
        async with work_scope(organization_id) as session:
            due = await due_waits(session, organization_id, now=at, limit=limit)
        for run_id, token in due:
            try:
                async with work_scope(organization_id) as session:
                    outcome = await wake_run(
                        session,
                        organization_id=organization_id,
                        run_id=run_id,
                        wake_token=token,
                        now=at,
                    )
            except Exception:
                log.exception("automation.sweep.wake_failed", run_id=str(run_id))
                continue
            woken += outcome == "resumed"
    return woken


async def orphaned_wakes(session: AsyncSession, *, now: datetime) -> list[uuid.UUID]:
    """Organizations with an overdue wait, for the sweep's cross-tenant discovery. Run on the
    BYPASSRLS outbox role: it answers *which organizations* have work and nothing else."""
    rows = (
        await session.execute(
            select(AutomationRun.organization_id)
            .where(
                AutomationRun.state == "waiting",
                AutomationRun.next_wake_at.is_not(None),
                AutomationRun.next_wake_at <= now - SWEEP_GRACE,
            )
            .distinct()
            .limit(200)
        )
    ).all()
    return [r[0] for r in rows]
