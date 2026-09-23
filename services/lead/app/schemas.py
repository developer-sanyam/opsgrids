"""Request and response contracts for the lead service.

This module is the published API surface: a change here is a change to the OpenAPI contract,
and the CRM SPA's client maps onto it — `LeadOut` is the SPA's `Lead`. Ids are opaque row
UUIDs, exchanged as strings. `email` and `phone` on the wire are always the masked display
form; the raw value never appears in a response body (see `app/models.py`).

`full_name` and `email` are capped the same as `customer`'s own `full_name`/`email` fields
(160 / 254) — a lead that converts becomes a company and a contact there, and a value this
service accepted but that service would refuse is a failure surfacing at `:convert` instead of
at capture. `phone` is canonicalised to E.164 here too (`domain.canonical_e164`), for the same
reason: customer-service seals its own copy at conversion and would refuse a malformed number
then, which is a service-to-service 4xx instead of an ordinary 422 on the form that took it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app import domain

LeadSource = Literal["form", "import", "manual"]
LeadStatus = Literal["new", "working", "qualified", "disqualified", "converted"]
AssignmentMode = Literal["round_robin", "manual"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# ------------------------------------------------------------------ leads


class LeadIn(BaseModel):
    """Capture a lead — from a web form, an import row or a rep typing one in by hand.

    `owner_id` is optional: an omitted one is auto-assigned from the organization's
    `AssignmentRule` (round-robin), matching how a form submission has no rep to name.
    `email` / `phone` are the raw values on the way in; they are sealed and masked before
    anything is stored (see `app/models.py`), and never echoed back raw.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=160)
    company: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = Field(default=None, max_length=254)
    #: Any reasonable human-typed format in; `_canonicalize_phone` below refuses anything
    #: that does not resolve to E.164 rather than storing it as typed.
    phone: str | None = Field(default=None, max_length=32)
    source: LeadSource = "manual"
    owner_id: uuid.UUID | None = None
    notes: str = Field(default="", max_length=4000)

    @field_validator("phone")
    @classmethod
    def _canonicalize_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        canonical = domain.canonical_e164(value)
        if canonical is None:
            raise ValueError("must be a valid E.164 phone number, with a country code")
        return canonical


class LeadPatch(BaseModel):
    """Partial update of a lead's plain fields and its status. Deliberately **no path to
    `converted`** — `status` here is validated against `domain.can_transition`, which has no
    transition landing on `converted`; only `POST /leads/{id}:convert` can set it, because
    that is the one path that also creates the company and deal the status implies."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    company: str | None = Field(default=None, max_length=200)
    status: LeadStatus | None = None
    owner_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=4000)


class LeadOut(BaseModel):
    id: str
    full_name: str
    company: str | None
    email: str | None
    phone: str | None
    source: LeadSource
    status: LeadStatus
    owner_id: str | None
    notes: str
    converted_company_id: str | None
    converted_contact_id: str | None
    converted_deal_id: str | None
    converted_at: datetime | None
    created_at: datetime


class LeadConvertOut(BaseModel):
    """The result of `:convert` — the ids the lead became. Returned identically whether this
    call performed the conversion or a lead already converted is being replayed."""

    lead_id: str
    company_id: str
    contact_id: str | None
    deal_id: str


# ------------------------------------------------------------------ assignment rule


class AssignmentRuleIn(BaseModel):
    """Configure the organization's round-robin. ORG-scoped: this is workspace
    configuration, not any one rep's record (see `app/services.py`)."""

    model_config = ConfigDict(extra="forbid")

    mode: AssignmentMode = "round_robin"
    owner_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class AssignmentRuleOut(BaseModel):
    mode: AssignmentMode
    owner_ids: list[str]
    cursor: int
