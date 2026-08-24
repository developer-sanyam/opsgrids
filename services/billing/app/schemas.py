"""Request and response contracts for billing.

This module is the OpenAPI surface: a change here is a change to the published contract, and
the frontend generates its client from it.

Money is carried as integer minor units — the wire format matches the storage, so no float
ever appears between the database and the client (ADR-0000 §8). `quantity` is a measured count
and is the one decimal here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# --------------------------------------------------------------------- customer output


class InvoiceLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    description: str
    quantity: Decimal
    unit_price_micros: int | None = None
    amount_minor: int
    plan_version_id: uuid.UUID | None = None


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    subscription_id: uuid.UUID | None = None
    state: str
    currency: str
    total_minor: int
    period_start: datetime
    period_end: datetime
    finalized_at: datetime | None = None
    voided_at: datetime | None = None
    lines: list[InvoiceLineOut] = Field(default_factory=list)


class CreditBalanceOut(BaseModel):
    """The windowed `SUM` (ADR-0004 §3), not a stored column — see `selectors.credit_balance`."""

    organization_id: uuid.UUID
    currency: str
    balance_minor: int
    as_of: datetime


# --------------------------------------------------------------------- internal input


class FinalizeLineIn(BaseModel):
    """One explicit, already-priced line. `currency` defaults to the invoice's; when a caller
    sets a different one it is rejected, which is what proves the currency guard end to end."""

    description: str = Field(min_length=1, max_length=300)
    quantity: Decimal = Field(default=Decimal(1))
    unit_price_micros: int | None = Field(default=None, ge=0)
    amount_minor: int
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    plan_version_id: uuid.UUID | None = None


class FinalizeInvoiceIn(BaseModel):
    """Create a finalized invoice from explicit line items (ADR-0004 §1). The total is the exact
    sum of the lines; `expected_total_minor`, when given, is cross-checked against it."""

    organization_id: uuid.UUID
    subscription_id: uuid.UUID | None = None
    number: str = Field(min_length=1, max_length=60)
    currency: str = Field(min_length=3, max_length=3)
    period_start: datetime
    period_end: datetime
    lines: list[FinalizeLineIn] = Field(min_length=1)
    expected_total_minor: int | None = None


class CreditGrantIn(BaseModel):
    """A grant, comp-access or goodwill credit — a positive ledger row with a validity window."""

    organization_id: uuid.UUID
    amount_minor: int = Field(gt=0, description="Positive minor units; a grant adds credit")
    currency: str = Field(min_length=3, max_length=3)
    reason: str = Field(min_length=1, max_length=200)
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class CreditConsumeIn(BaseModel):
    """Spend credit — recorded as a negative row, never a mutation of the grant."""

    organization_id: uuid.UUID
    amount_minor: int = Field(gt=0, description="Positive minor units; stored as a negative row")
    currency: str = Field(min_length=3, max_length=3)
    reason: str = Field(min_length=1, max_length=200)


class CreditReverseIn(BaseModel):
    """Reverse an earlier entry — a new row that negates the one it names (ADR-0004 §3)."""

    organization_id: uuid.UUID
    reversal_of: uuid.UUID
    reason: str = Field(min_length=1, max_length=200)


class VoidInvoiceIn(BaseModel):
    organization_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=200)


# --------------------------------------------------------------------- internal output


class CreditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entry_type: str
    amount_minor: int
    currency: str
    reason: str
    valid_from: datetime
    valid_until: datetime | None = None
    reversal_of: uuid.UUID | None = None
