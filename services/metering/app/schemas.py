"""Request and response contracts for metering.

This module is the OpenAPI surface: a change here is a change to the published contract, and
the frontend generates its client from it. Two surfaces meet here — the internal ingest batch
(service-to-service) and the customer usage read.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# --------------------------------------------------------------------- ingest (internal)


class UsageEventIn(BaseModel):
    """One usage event to record. Posted server-to-server by a product service.

    `organization_id` is carried per event, not taken from a token: the ingest endpoint is a
    service-token call with no tenant context, and a single batch may (in principle) span more
    than one organization. `is_billable` defaults to false and is expected to stay false at this
    stage — billing is decided in the catalogue, not by the emitter (ADR-0004 §7).
    """

    organization_id: uuid.UUID
    meter_key: str = Field(max_length=60)
    quantity: int = Field(ge=0)
    unit: str = Field(max_length=40)
    idempotency_key: str = Field(max_length=200)
    subject_ref: uuid.UUID | None = None
    occurred_at: datetime | None = None
    is_billable: bool = False


class UsageBatchIn(BaseModel):
    """A batch of usage events. Idempotent on each event's `idempotency_key` (per organization)."""

    events: list[UsageEventIn] = Field(min_length=1, max_length=1000)


class UsageIngestOut(BaseModel):
    """The result of an ingest call.

    `recorded` counts newly-stored events; `duplicates` counts events whose idempotency key was
    already present and were therefore no-ops. `recorded + duplicates` always equals the number
    of events posted, which is what lets a caller retry a whole batch safely.
    """

    recorded: int
    duplicates: int


# --------------------------------------------------------------------- read (customer)


class UsageLineOut(BaseModel):
    """One meter's usage over the queried period."""

    meter_key: str
    unit: str
    total_quantity: int
    billable_quantity: int
    event_count: int


class UsageSummaryOut(BaseModel):
    """A customer's usage for the current period, one line per meter."""

    organization_id: uuid.UUID
    period_start: datetime
    period_end: datetime
    lines: list[UsageLineOut]
