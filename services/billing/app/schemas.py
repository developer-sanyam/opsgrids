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
from typing import Literal

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


#: What a billing-run invoice is for (ADR-0005 addendum and its review fixes). `period` is a
#: trial conversion or a renewal; `proration` an upgrade's prorated difference for the rest of the
#: current period; `close` the usage-only closing period a cancellation or suspension leaves.
BillingRunKind = Literal["period", "proration", "close"]


class SubscriptionInvoiceKeyIn(BaseModel):
    """The fields a billing-run invoice's idempotency key is built from — by billing, never taken
    from the caller as a string: `subscription:{id}:{period_start}` for a period,
    `subscription:{id}:proration:{change_id}` for a proration, and
    `subscription:{id}:{period_start}:close` for a closing period."""

    organization_id: uuid.UUID
    subscription_id: uuid.UUID
    kind: BillingRunKind = "period"
    period_start: datetime
    change_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _proration_names_its_change(self) -> SubscriptionInvoiceKeyIn:
        if self.kind == "proration" and self.change_id is None:
            raise ValueError("a proration is keyed on its change_id")
        return self


class BillSubscriptionPeriodIn(SubscriptionInvoiceKeyIn):
    """Invoice one subscription claim — the billing run's request (ADR-0005 addendum).

    The caller names *what* is owed for — the pin, the items, the period — and never a price or
    an amount: billing reads the prices off the pinned catalog version itself (both versions, for
    a proration) and the usage off metering, and computes every minor unit. Idempotent on the
    claim's key: asking twice, or from two replicas at once, returns the one invoice.

    * `period`: recurring prices in advance for `[period_start, period_end)`; usage in arrears for
      `usage_period_*`, the period that just closed (absent after a trial — trial usage is free).
    * `proration`: `[period_start, period_end)` is the rest of the current period from the
      upgrade, a fraction of `[full_period_start, period_end)`; billed is the positive difference
      between the new pin's recurring price and `from_plan_version_id`'s, prorated.
    * `close`: usage only, for the usage window, which is the period itself.
    """

    product_key: str = Field(min_length=1, max_length=40)
    plan_version_id: uuid.UUID
    currency: str = Field(min_length=3, max_length=3)
    period_end: datetime
    usage_period_start: datetime | None = None
    usage_period_end: datetime | None = None
    items: list[SubscriptionItemIn] = Field(default_factory=list)
    from_plan_version_id: uuid.UUID | None = None
    from_items: list[SubscriptionItemIn] = Field(default_factory=list)
    full_period_start: datetime | None = None

    @model_validator(mode="after")
    def _periods_are_ordered(self) -> BillSubscriptionPeriodIn:
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")
        if (self.usage_period_start is None) != (self.usage_period_end is None):
            raise ValueError("usage_period_start and usage_period_end come together or not at all")
        if self.usage_period_start is not None and self.usage_period_end is not None:
            if self.usage_period_end <= self.usage_period_start:
                raise ValueError("usage_period_end must be after usage_period_start")
            if self.kind == "period" and self.usage_period_end > self.period_start:
                # Usage is billed in arrears for a period that has *closed*; a window reaching
                # into the period being billed in advance would bill usage not yet complete.
                raise ValueError("the usage period must close by the billed period's start")
        if self.kind == "close" and self.usage_period_start is None:
            raise ValueError("a closing period bills usage, so it needs its usage window")
        if self.kind == "proration":
            if self.from_plan_version_id is None or self.full_period_start is None:
                raise ValueError("a proration needs from_plan_version_id and full_period_start")
            if self.full_period_start > self.period_start:
                raise ValueError("full_period_start must not be after the proration's start")
        return self


class SubscriptionInvoiceFinalizeIn(SubscriptionInvoiceKeyIn):
    """Finalize the run's draft under a claim's key, after subscription recorded the claim."""

    product_key: str | None = Field(default=None, max_length=40)


class SubscriptionInvoiceVoidIn(SubscriptionInvoiceKeyIn):
    """Void whatever billing-run invoice stands under a claim's key — the superseded-period fix."""

    reason: str = Field(min_length=1, max_length=200)


class SubscriptionInvoiceVoidOut(BaseModel):
    """`voided` is true when an invoice under the key is void now (this call or an earlier one);
    false when no invoice was ever raised under it."""

    voided: bool
    invoice_id: uuid.UUID | None = None


class BilledPeriodOut(BaseModel):
    """The draft invoice for one claim, and whether this call created it. `invoice` is null only
    for a proration that nets to nothing — no zero-line invoice is ever written for one."""

    created: bool
    invoice: InvoiceOut | None = None


class FinalizedPeriodOut(BaseModel):
    """The claim's invoice after finalize, and whether this call finalized it."""

    finalized: bool
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


# --------------------------------------------------------------------- operator (staff) output


class InvoiceAdminOut(BaseModel):
    """One OpsGrids invoice as staff sees it on an account's Billing tab — read-only, and
    narrower than `InvoiceOut` on purpose: no lines, no subscription id, no void reason. A
    cross-tenant read has no use for what an invoice *contains*, and the smaller the surface the
    smaller the blast radius of a bug in it.

    `status` is billing's own `state`, verbatim: `finalized` (issued, and the only live one) or
    `void`. There is **no paid date and no paid status here, deliberately**: billing records what
    was billed and never whether it was collected — collection lives in payment-service and in
    subscription's dunning — so a `paid_at` on this shape would be a field with nothing real to
    fill it. `issued_at` is `finalized_at`, the moment the document became immutable.
    """

    id: uuid.UUID
    organization_id: uuid.UUID
    number: str
    status: str
    period_start: datetime
    period_end: datetime
    total_minor: int
    currency: str
    issued_at: datetime | None = None
    voided_at: datetime | None = None


class CreditLedgerEntryAdminOut(BaseModel):
    """One ledger row as OpsGrids staff sees it — the tenant fields `CreditEntryOut` omits
    because a customer already knows which organization it is asking about; an operator
    reading across every customer does not, so `organization_id` is here and `created_at`
    is the row's own append-only timestamp rather than something a customer needs."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    entry_type: str
    amount_minor: int
    currency: str
    reason: str
    valid_from: datetime
    valid_until: datetime | None = None
    reversal_of: uuid.UUID | None = None
    drawn_from: uuid.UUID | None = None
    created_at: datetime
