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
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app import domain
from platform_core.validation import reject_explicit_null

LeadSource = Literal["form", "import", "manual"]
LeadStatus = Literal["new", "working", "qualified", "disqualified", "converted"]
AssignmentMode = Literal["round_robin", "manual"]
#: The two sealed contact details a lead carries, and the only two `:reveal` can open.
RevealField = Literal["phone", "email"]


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
    #: Who owns the lead when `owner_id` is omitted and the round-robin hands it to nobody (no
    #: rule configured, or every rep in it has left): the SPA names the signed-in login's own
    #: rep row here. Omitted, the creator is still tried (their employee link, or, at ORG scope,
    #: their membership id). Never used when `owner_id` is given or a rotation picks someone.
    fallback_owner_id: uuid.UUID | None = None
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
    #: Nullable in the domain: unassigning a lead (`owner_id: null`) is a real, valid state
    #: (`services.update_lead` requires ORG scope for it), so this one is deliberately not
    #: passed to `reject_explicit_null` below.
    owner_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        reject_explicit_null(self, ("full_name", "status", "notes"))
        return self


class LeadOut(BaseModel):
    id: str
    full_name: str
    company: str | None
    email: str | None
    phone: str | None
    source: LeadSource
    status: LeadStatus
    #: The statuses a plain status change may move this lead to, from the server's own state
    #: machine (`domain.allowed_next_statuses`). The SPA offers exactly these. Never contains
    #: `converted`: that is only `:convert`.
    next_statuses: list[LeadStatus]
    owner_id: str | None
    notes: str
    converted_company_id: str | None
    converted_contact_id: str | None
    converted_deal_id: str | None
    converted_at: datetime | None
    created_at: datetime


class LeadExportItem(BaseModel):
    """One lead as Engage's cold e-mail audience needs it (internal). `email` is the real address
    (null with `emails=false` or none on file)."""

    lead_id: str
    full_name: str
    company: str | None
    email: str | None
    status: LeadStatus
    source: LeadSource
    owner_id: str | None


class LeadExportOut(BaseModel):
    items: list[LeadExportItem]
    #: Keyset cursor for the next page; None on the last page (and always for an `ids` read).
    next_after_id: str | None = None


class LeadConvertIn(BaseModel):
    """Optional say over the deal a conversion opens. The whole body is optional, and so is each
    field: `POST /leads/{id}:convert` with no body opens a zero-value deal in the pipeline's
    entry stage, exactly as before. `deal_value_minor` is integer minor units (INR) -- never a
    float; `deal_stage` is a stage key of the organization's pipeline (deal-service checks it
    and refuses an unknown or archived one)."""

    model_config = ConfigDict(extra="forbid")

    deal_value_minor: int = Field(default=0, ge=0, le=10**13, strict=True)
    deal_stage: str | None = Field(default=None, min_length=1, max_length=40)


class LeadConvertOut(BaseModel):
    """The result of `:convert` — the ids the lead became. Returned identically whether this
    call performed the conversion or a lead already converted is being replayed."""

    lead_id: str
    company_id: str
    contact_id: str | None
    deal_id: str


class LeadRevealIn(BaseModel):
    """Which sealed detail to reveal. One route serves both, so the client has a single call
    and the audit row names exactly the field that was opened. Anything but `phone` or
    `email` is a 422 — there is no way to ask this route for some other column."""

    model_config = ConfigDict(extra="forbid")

    field: RevealField


class LeadRevealOut(BaseModel):
    """A lead's real phone number or email, revealed to a human who stepped up.

    Returned only by `POST /leads/{id}:reveal`, which requires the dangerous
    `crm.lead.lead.read_sensitive` permission and a second factor, and which writes an audit
    row every time. `value` is always present: a lead with nothing sealed for the requested
    field is a 404, never a 200 with a null. The response is sent `Cache-Control: no-store`.
    """

    lead_id: str
    field: RevealField
    value: str


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
