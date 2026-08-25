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

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# ------------------------------------------------------------------ vocabularies (match the SPA)

CompanyStatus = Literal["customer", "prospect", "lead", "churned"]
ContactStatus = Literal["customer", "qualified", "lead", "dormant"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# ------------------------------------------------------------------ owners


class OwnerOut(BaseModel):
    """Mirrors the SPA's `Owner`. The `id` is what every `owner_id` in the product carries,
    including deal-service's — the SPA resolves display names from this list client-side."""

    id: str
    full_name: str
    email: str
    role: str


# ------------------------------------------------------------------ companies


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
