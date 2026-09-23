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

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class CreditPackOut(BaseModel):
    """One prepaid pack and what is left on it — both derived, neither stored.

    `remaining_minor` is a `SUM` over the grant and every consumption that named it, so it is
    correct by construction rather than by a column somebody remembered to decrement. `lapsed`
    compares the pack's own `valid_until` to the instant asked about; nothing runs to expire a
    pack, so a lapsed one is simply a row whose window has closed (ADR-0004 §3).
    """

    id: uuid.UUID
    currency: str
    granted_minor: int
    remaining_minor: int
    reason: str
    valid_from: datetime
    valid_until: datetime | None = None
    lapsed: bool
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
    """A grant, comp-access or goodwill credit — a positive ledger row with a validity window.

    A **prepaid credit pack** is this and nothing more (docs/commerce-packaging.md): money the
    customer paid up front, recorded as one positive row with `valid_from`/`valid_until` set to
    the commitment term. It needs no product of its own, because a pack is not a thing the
    customer owns — it is credit, and credit is already a ledger entry.
    """

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
    drawn_from: uuid.UUID | None = Field(
        default=None,
        description=(
            "Draw this down against one specific grant — a prepaid pack. The consumption "
            "inherits that grant's validity window, may not exceed what is left on it, and is "
            "refused once the pack has lapsed. Omit to spend the general balance."
        ),
    )


class CreditReverseIn(BaseModel):
    """Reverse an earlier entry — a new row that negates the one it names (ADR-0004 §3)."""

    organization_id: uuid.UUID
    reversal_of: uuid.UUID
    reason: str = Field(min_length=1, max_length=200)


class SubscriptionItemIn(BaseModel):
    """One item of the subscription being billed: the catalog price it names and its quantity."""

    price_id: uuid.UUID
    quantity: int = Field(ge=0)


class BillSubscriptionPeriodIn(BaseModel):
    """Invoice one subscription for one period — the billing run's request (ADR-0005 addendum).

    The caller names *what* is owed for — the pin, the items, the period — and never a price or
    an amount: billing reads the prices off the pinned catalog version itself and the usage off
    metering, and computes every minor unit. Idempotent on `(subscription_id, period_start)`:
    asking twice, or from two replicas at once, returns the one invoice.

    `usage_period_*` is the period that has just closed, billed in arrears for usage prices; it is
    absent on the first invoice after a trial, because trial usage is not billed.
    """

    organization_id: uuid.UUID
    subscription_id: uuid.UUID
    product_key: str = Field(min_length=1, max_length=40)
    plan_version_id: uuid.UUID
    currency: str = Field(min_length=3, max_length=3)
    period_start: datetime
    period_end: datetime
    usage_period_start: datetime | None = None
    usage_period_end: datetime | None = None
    items: list[SubscriptionItemIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _periods_are_ordered(self) -> BillSubscriptionPeriodIn:
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")
        if (self.usage_period_start is None) != (self.usage_period_end is None):
            raise ValueError("usage_period_start and usage_period_end come together or not at all")
        if self.usage_period_start is not None and self.usage_period_end is not None:
            if self.usage_period_end <= self.usage_period_start:
                raise ValueError("usage_period_end must be after usage_period_start")
            if self.usage_period_end > self.period_start:
                # Usage is billed in arrears for a period that has *closed*; a window reaching
                # into the period being billed in advance would bill usage not yet complete.
                raise ValueError("the usage period must close by the billed period's start")
        return self


class BilledPeriodOut(BaseModel):
    """The invoice for one subscription period, and whether this call created it."""

    created: bool
    invoice: InvoiceOut


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
    drawn_from: uuid.UUID | None = None
