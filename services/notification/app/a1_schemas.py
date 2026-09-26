"""Pydantic contracts for the automation endpoints (slice A1) — the OpenAPI surface.

Requests refuse unknown fields (`extra="forbid"`). A node's `config` is deliberately a free-form
object here: its shape depends on the node type and is validated, with per-node problems the
builder can show, by `app.a1_pure.validate_graph` at publish time. A draft may be saved half-built.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

NodeType = Literal["trigger", "condition", "wait", "send_message", "create_task", "end"]
AutomationStatus = Literal["draft", "active", "paused"]
RunState = Literal["running", "waiting", "completed", "failed", "skipped"]


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int


class NodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    type: NodeType
    config: dict[str, Any] = Field(default_factory=dict)
    position: Position | None = None


class EdgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from", min_length=1, max_length=64)
    to: str = Field(min_length=1, max_length=64)
    label: Literal["yes", "no"] | None = None


class GraphIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[NodeIn] = Field(max_length=60)
    edges: list[EdgeIn] = Field(max_length=200)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class AutomationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    graph: GraphIn


class AutomationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    graph: GraphIn | None = None


class AutomationOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: AutomationStatus
    trigger_event: str
    module: str
    version: int | None
    executions_30d: int
    success_rate_30d: float | None
    last_run_at: datetime | None
    updated_at: datetime


class ProblemOut(BaseModel):
    node_id: str | None
    code: str
    message: str
    #: `error` blocks a publish; `warning` (a channel not connected yet) does not.
    severity: Literal["error", "warning"] = "error"


class ValidationOut(BaseModel):
    ok: bool
    problems: list[ProblemOut]


class AutomationDetail(AutomationOut):
    graph: dict[str, Any]
    validation: ValidationOut


class TriggerVariableOut(BaseModel):
    path: str
    label: str
    kind: str


class TriggerOut(BaseModel):
    event_type: str
    label: str
    module: str
    variables: list[TriggerVariableOut]


class TriggersOut(BaseModel):
    items: list[TriggerOut]


class RunOut(BaseModel):
    id: uuid.UUID
    automation_id: uuid.UUID
    version: int
    state: RunState
    started_at: datetime
    finished_at: datetime | None
    source_event_id: uuid.UUID
    #: Masked: the recipient once a send resolved one, the trigger event until then.
    subject: str
    failure_code: str | None
    next_wake_at: datetime | None


class StepOut(BaseModel):
    node_id: str
    type: NodeType
    state: Literal["waiting", "running", "done", "failed", "skipped"]
    started_at: datetime
    finished_at: datetime | None
    attempt: int
    detail: str | None
    message_id: uuid.UUID | None


class RunDetail(RunOut):
    steps: list[StepOut]
    idempotency_key: str


class OkOut(BaseModel):
    ok: bool = True


class SeriesPoint(BaseModel):
    date: str
    successful: int
    failed: int


class ReasonOut(BaseModel):
    code: str
    label: str
    count: int


class PerAutomationOut(BaseModel):
    automation_id: uuid.UUID
    name: str
    executions: int
    success_rate: float
    failed: int
    avg_duration_ms: int | None
    last_run_at: datetime | None


class StatsOut(BaseModel):
    executions: int
    successful: int
    failed: int
    avg_duration_ms: int | None
    series: list[SeriesPoint]
    failures_by_reason: list[ReasonOut]
    per_automation: list[PerAutomationOut]
