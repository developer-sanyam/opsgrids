"""Request and response contracts for the customer service.

This module is the published API surface: a change here is a change to the OpenAPI
contract, and the CRM SPA's client maps onto it. The field names, nesting and enums mirror
`frontend/product-crm/src/api/types.ts` exactly — `CompanyOut` is the SPA's `Company`,
`ContactOut` its `Contact`, `OwnerOut` its `Owner` — so wiring the SPA from fixtures to
live is a flag flip rather than a rewrite.

Money (`annual_revenue_minor`) is integer minor units in `currency` (ISO-4217), never a
float. A contact's `phone` is the masked display form the service stores (see
`app.models`); the read path never returns a raw number because none is held.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


def _reject_explicit_null(instance: BaseModel, fields: tuple[str, ...]) -> None:
    """A PATCH field typed `X | None` is optional two different ways: omitted (leave alone) or
    explicitly `null` (the caller's own JSON says so). For a field the database column never
    allows NULL, only the first meaning is valid — the second is a malformed request (422),
    not a value that should reach an `IntegrityError` or a silent no-op write. Every field on
    `OwnerPatch`/`CompanyPatch`/`ContactPatch` maps to a NOT NULL column, so each passes every
    one of its own fields here. Same helper `operations.app.schemas._reject_explicit_null`
    uses."""
    for field in fields:
        if field in instance.model_fields_set and getattr(instance, field) is None:
            raise ValueError(f"{field} cannot be set to null")


# ------------------------------------------------------------------ vocabularies (match the SPA)

CompanyStatus = Literal["customer", "prospect", "lead", "churned"]
ContactStatus = Literal["customer", "qualified", "lead", "dormant"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# ------------------------------------------------------------------ owners


class OwnerIn(BaseModel):
    """Add a rep to the directory. A production org starts with zero owners, and every
    company / contact / deal create needs an `owner_id`, so this is the first write."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=160)
    email: EmailStr = Field(max_length=254)
    role: str = Field(default="", max_length=120)


class OwnerPatch(BaseModel):
    """Edit a rep, or deactivate one. Absent means "leave alone". Setting `is_active` to
    `False` is the deactivation path (`services.deactivate_owner`) and refuses while the rep
    still owns a live company or contact — reassign those first."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    email: EmailStr | None = Field(default=None, max_length=254)
    role: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        _reject_explicit_null(self, ("full_name", "email", "role", "is_active"))
        return self


class OwnerOut(BaseModel):
    """Mirrors the SPA's `Owner`. The `id` is what every `owner_id` in the product carries,
    including deal-service's — the SPA resolves display names from this list client-side."""

    id: str
    full_name: str
    email: str
    role: str
    is_active: bool


# ------------------------------------------------------------------ companies


class CompanyIn(BaseModel):
    """Create an account. `owner_id` must resolve to a rep in this organization's
    directory (`GET /customers/owners`). `annual_revenue_minor` is integer minor units of
    `currency`; no float is accepted on this path."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    owner_id: uuid.UUID
    domain: str = Field(default="", max_length=120)
    industry: str = Field(default="", max_length=80)
    size_band: str = Field(default="", max_length=20)
    city: str = Field(default="", max_length=120)
    country: str = Field(default="", max_length=120)
    status: CompanyStatus = "prospect"
    annual_revenue_minor: int = Field(default=0, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)


class CompanyPatch(BaseModel):
    """Partial update. Absent means "leave alone"; every field is the same shape as
    `CompanyIn`, just optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    owner_id: uuid.UUID | None = None
    domain: str | None = Field(default=None, max_length=120)
    industry: str | None = Field(default=None, max_length=80)
    size_band: str | None = Field(default=None, max_length=20)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    status: CompanyStatus | None = None
    annual_revenue_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        _reject_explicit_null(
            self,
            (
                "name",
                "owner_id",
                "domain",
                "industry",
                "size_band",
                "city",
                "country",
                "status",
                "annual_revenue_minor",
                "currency",
            ),
        )
        return self


class CompanyOut(BaseModel):
    """Mirrors the SPA's `Company`. `annual_revenue_minor` is minor units of `currency`;
    `size_band` is a display band ("201-500"), not a number."""

    id: str
    name: str
    domain: str
    industry: str
    size_band: str
    city: str
    country: str
    status: CompanyStatus
    owner_id: str
    annual_revenue_minor: int
    currency: str
    created_at: datetime


# ------------------------------------------------------------------ contacts


class ContactIn(BaseModel):
    """Add a person to an account. Deliberately **no `phone` field** — the mask is a
    server-derived projection and must never be writable. Give a real number, if there is
    one, through `raw_phone`; it is sealed and the mask derived from it (see
    `services.create_contact`). `extra="forbid"` so a caller can never smuggle `phone` in."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=160)
    email: EmailStr = Field(max_length=254)
    company_id: uuid.UUID
    owner_id: uuid.UUID
    title: str = Field(default="", max_length=160)
    status: ContactStatus = "lead"
    #: E.164, with the country code. Optional — many contacts are added with no number yet.
    raw_phone: str | None = Field(default=None, min_length=4, max_length=40)


class ContactPatch(BaseModel):
    """Partial update of the plain contact fields. Deliberately **no `phone`, no
    `raw_phone`** — the number stays on its own dedicated route
    (`PUT /contacts/{id}/phone`), so a masked value can never be written back over a
    sealed one and a PATCH can never smuggle a raw number in unaudited."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    title: str | None = Field(default=None, max_length=160)
    email: EmailStr | None = Field(default=None, max_length=254)
    company_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    status: ContactStatus | None = None

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> Self:
        _reject_explicit_null(
            self, ("full_name", "title", "email", "company_id", "owner_id", "status")
        )
        return self


class ContactOut(BaseModel):
    """Mirrors the SPA's `Contact`. `phone` is the masked read shape the service returns by
    default; `last_activity_at` is NULL for a contact never touched."""

    id: str
    full_name: str
    title: str
    email: str
    phone: str
    company_id: str
    status: ContactStatus
    owner_id: str
    last_activity_at: datetime | None
    created_at: datetime


# --------------------------------------------------------------------- the real phone number
#
# Three shapes, deliberately separate from `ContactOut`. A raw number must never be reachable
# by widening an existing response, so it has its own request, its own response and its own
# route — and `ContactOut` is untouched, still carrying only the mask.


class ContactPhoneIn(BaseModel):
    """Set a contact's real number. The mask is derived from it, never supplied."""

    model_config = ConfigDict(extra="forbid")

    #: E.164, with the country code. Validated and canonicalised server-side; a number
    #: without a country code is refused rather than guessed at.
    raw_phone: str = Field(min_length=4, max_length=40)


class ContactPhoneRevealed(BaseModel):
    """A contact's real number, revealed to a human who stepped up.

    Returned only by the dedicated unmask route, which requires the dangerous
    `read_sensitive` permission and a second factor, and which writes an audit row every time.
    `phone` is None when the contact has no sealed number.
    """

    contact_id: str
    phone: str | None


class MessagingTargetOut(BaseModel):
    """A contact's messaging address, for a service that is about to send to them.

    The internal API's response, never the public one. `phone` is None when the contact has
    no sealed number, which the caller must treat as "not messageable on this channel" rather
    than as an error — a contact with no number is an ordinary state, not a fault.
    """

    contact_id: str
    channel: str
    phone: str | None


class ContactResolveIn(BaseModel):
    """Find the contact holding a phone number.

    The number travels in a **body**, never a query string. A query string is recorded in
    access logs, proxy logs and browser history; a customer's phone number has no business in
    any of them, and the endpoint would otherwise leak exactly what the sealed column exists
    to protect.
    """

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=4, max_length=40)


class ContactResolveOut(BaseModel):
    """Only the id. Returning any part of the contact would make this a read-around for the
    permission-guarded routes, and the caller already has the number it asked with."""

    contact_id: str | None


# ------------------------------------------------------------------ lead conversion (internal)


class LeadConversionIn(BaseModel):
    """What lead-service sends `:convert` — its own deterministic ids, so ensuring is
    idempotent by id before either natural key is even considered (`services
    .ensure_company_and_contact_for_lead`)."""

    model_config = ConfigDict(extra="forbid")

    company_id: uuid.UUID
    contact_id: uuid.UUID
    company_name: str = Field(min_length=1, max_length=200)
    owner_id: uuid.UUID
    contact_full_name: str = Field(min_length=1, max_length=160)
    #: Raw, not masked — lead-service opens its own sealed copy before this call and the
    #: value travels once, service-to-service, to be re-sealed here under this schema's own
    #: key. Absent when the lead carried none; a contact is then not created at all (a
    #: contact's `email` column is not nullable — see `app/models.py`).
    contact_email: str | None = Field(default=None, max_length=320)
    contact_raw_phone: str | None = Field(default=None, min_length=4, max_length=40)


class LeadConversionOut(BaseModel):
    company_id: str
    contact_id: str | None


# ------------------------------------------------------------------ owner validation (internal)


class OwnersValidateIn(BaseModel):
    """Which of these ids resolve to an active rep — lead-service's own gate for `owner_id`
    on a lead create/patch and for the round-robin's owner list, so an inactive or unknown rep
    never enters lead-service's own writes in the first place."""

    model_config = ConfigDict(extra="forbid")

    owner_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class OwnersValidateOut(BaseModel):
    """The ids from the request that do **not** resolve to an active rep. Empty means every
    id named is a known, active owner."""

    invalid_owner_ids: list[str]
