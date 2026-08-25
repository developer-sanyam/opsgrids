"""Request and response contracts for the standup service.

This module is the published API surface: a change here is a change to the OpenAPI contract,
and the PulseBoard SPA wires its client against it. The entry shape mirrors
`frontend/product-pulseboard/src/api/types.ts` (`StandupEntry`) exactly: `person_id` on the
wire is the person's stable `ref` (`p01`) — the id the SPA's roster is keyed by — and
`blockers` is null when nothing blocks (the SPA's flagged state is exactly non-null).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


class StandupEntryOut(BaseModel):
    """Mirrors the SPA's `StandupEntry` — one person's check-in for one day."""

    id: str
    person_id: str
    date: date
    yesterday: str
    today: str
    blockers: str | None


class StandupEntryIn(BaseModel):
    """Post (or repost) a day's check-in. `person_ref` names the roster person; `person_id` is
    their roster UUID (the OWN-scope subject). Reposting the same person+day replaces the
    entry — a standup is the day's current word, not an append-only ledger."""

    person_ref: str = Field(min_length=1, max_length=40)
    person_id: str = Field(min_length=1, description="The roster person's UUID")
    date: date
    yesterday: str = Field(default="", max_length=4000)
    today: str = Field(default="", max_length=4000)
    blockers: str | None = Field(default=None, max_length=4000)
