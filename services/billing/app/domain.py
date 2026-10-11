"""Pure money mechanics, and the pricing the billing run is built on.

No I/O lives here (ADR service anatomy): proration and discount allocation are integer
arithmetic on `platform_core.money.Money`, and the currency guard is `Money`'s own `_same`,
reached simply by adding two amounts. There are **no floats and no `Numeric`** in any of it
(ADR-0000 §8) — the whole reason money is integer minor units is that these operations are
exact, and that an invoice's lines foot to its total to the last minor unit.

Two ways lines are produced
---------------------------
`finalize` takes **explicit** line items (`ExplicitPricingSource`, an operator's hand-built
invoice). The **billing run** (ADR-0005 addendum, 2026-09-23) prices one subscription period
from the pinned catalog version's prices, the subscription's item quantities and — for a usage
price — metering's count for the closed period, then adds tax from `tax_rates`. That is
`price_subscription_period` below: pure, integer, and handed everything it needs, because the
catalog and metering reads that feed it happen in `app/billing_run.py` **outside** any database
transaction. Nothing here imports catalog or metering; they arrive as the plain values below.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from platform_core import gst
from platform_core.money import Money, total


@dataclass(frozen=True, slots=True)
class LineDraft:
    """One invoice line before it is persisted — the unit both the explicit path and a future
    billing run produce. `amount_minor` is this line's money; `unit_price_micros` is kept for
    reconstruction (ADR-0004 §2) and is not re-derived here."""

    description: str
    quantity: Decimal
    amount_minor: int
    currency: str
    unit_price_micros: int | None = None
    plan_version_id: UUID | None = None

    def money(self) -> Money:
        return Money(self.amount_minor, self.currency)


@dataclass(frozen=True, slots=True)
class Period:
    """The interval an invoice covers. Half-open in spirit: `start` inclusive, `end` exclusive."""

    start: datetime
    end: datetime


# --------------------------------------------------------------------- footing & currency


class LinesDoNotFoot(ValueError):
    """An invoice's lines did not sum to its stated total — the bug you find in a dispute."""


def sum_lines(lines: list[LineDraft], currency: str) -> Money:
    """The exact total of `lines`, in `currency`.

    Every line is turned into a `Money` in its own currency and summed; `Money.__add__` calls
    `_same`, so a line whose currency differs from `currency` (or from its siblings) raises
    `CurrencyMismatch` here rather than silently producing a wrong total.
    """
    return total([line.money() for line in lines], currency)


def assert_foots(stated_total: Money, lines: list[LineDraft]) -> None:
    """Refuse an invoice whose lines do not foot exactly to its total (ADR-0000 §9)."""
    computed = sum_lines(lines, stated_total.currency)
    if computed != stated_total:
        raise LinesDoNotFoot(f"lines sum to {computed} but the invoice total is {stated_total}")


# --------------------------------------------------------------------- proration & discounts


def prorate(full: Money, *, elapsed_units: int, period_units: int) -> Money:
    """The share of `full` earned over `elapsed_units` of a `period_units` period.

    Largest-remainder, via `Money.allocate`, so the prorated part and the unused remainder sum
    back to `full` to the last minor unit — no fraction of a paisa is invented or lost. A whole
    or over-long period returns `full`; a zero-length one is a caller bug.
    """
    if period_units <= 0:
        raise ValueError("period_units must be positive")
    if elapsed_units <= 0:
        return Money.zero(full.currency)
    if elapsed_units >= period_units:
        return full
    earned, _unused = full.allocate([elapsed_units, period_units - elapsed_units])
    return earned


def allocate_discount(discount: Money, weights: list[int]) -> list[Money]:
    """Spread a discount across lines by `weights` so the parts sum **exactly** to it.

    A thin, named wrapper over `Money.allocate` (largest-remainder): the point is that a
    discount split three ways never loses a unit and never over-credits, which is what keeps the
    discounted invoice footing (ADR-0000 §8).
    """
    return discount.allocate(weights)


# --------------------------------------------------------------------- the pricing seams


class UsageSource(Protocol):
    """How metered quantities for a period are read.

    The billing run's implementation is `app.billing_run.HttpMetering`, which asks metering's
    internal usage read for the closed period — outside any transaction — and hands the counts
    to `price_subscription_period` as a plain mapping.
    """

    async def billable_usage(self, *, organization_id: UUID, period: Period) -> dict[str, int]:
        """Billable quantity per `meter_key` over `[period.start, period.end)`.

        Per organization, not per subscription: a meter belongs to one product and an
        organization holds at most one subscription per product, so the organization's count for
        the meter *is* the subscription's."""
        ...


class PricingSource(Protocol):
    """How invoice lines are produced for a subscription and period.

    `ExplicitPricingSource` returns lines an operator already priced; the billing run prices
    from catalog and metering through `price_subscription_period` (ADR-0004 §7).
    """

    async def assemble(
        self, *, subscription_id: UUID | None, period: Period
    ) -> list[LineDraft]: ...


@dataclass(frozen=True, slots=True)
class ExplicitPricingSource:
    """The minimal `PricingSource`: the caller has already priced the lines, so hand them back.

    This is the whole of the "explicit line items" path. It is not a stub that raises or returns
    nothing — it is the real, complete implementation for the case this service supports today.
    """

    lines: list[LineDraft] = field(default_factory=list)

    async def assemble(self, *, subscription_id: UUID | None, period: Period) -> list[LineDraft]:
        return list(self.lines)


# --------------------------------------------------------------------- the billing run's pricing

#: Catalog's price kinds. Contract strings, named here rather than imported — nothing in this
#: service imports catalog (one process may only ever hold one service's `app`).
PRICE_FLAT = "flat"
PRICE_PER_SEAT = "per_seat"
PRICE_USAGE = "usage"


class PricingError(ValueError):
    """The pinned version, the subscription's items and the invoice cannot be priced together.

    Every case is a data defect a person has to look at — an item naming a price that is not on
    the pin, a price in another currency, a usage price with no rate — and never something to
    paper over with a zero line: an invoice that silently omits a component is under-billing
    nobody will notice, and one that guesses is over-billing somebody will.
    """


@dataclass(frozen=True, slots=True)
class PriceSpec:
    """One catalog price on the pinned version, as billing needs it. Money is integer minor
    units (`amount_minor`) or micros of a major unit for a usage rate (`unit_price_micros`)."""

    id: UUID
    kind: str
    currency: str
    amount_minor: int | None = None
    unit: str | None = None
    unit_price_micros: int | None = None
    meter_key: str | None = None


@dataclass(frozen=True, slots=True)
class ItemSpec:
    """One subscription item: which price, and how many (seats for `per_seat`; 1 for flat)."""

    price_id: UUID
    quantity: int


@dataclass(frozen=True, slots=True)
class TaxSpec:
    """The statutory rate in force for the period: basis points, never a float."""

    code: str
    name: str
    rate_bps: int


def _fmt_day(at: datetime) -> str:
    return at.strftime("%Y-%m-%d")


def price_subscription_period(
    *,
    currency: str,
    plan_version_id: UUID,
    prices: Sequence[PriceSpec],
    items: Sequence[ItemSpec],
    period: Period,
    usage_period: Period | None,
    usage: Mapping[str, int] | None,
    tax: TaxSpec | None,
    recurring: bool = True,
) -> list[LineDraft]:
    """The lines of one subscription period's invoice, exactly, in integer minor units.

    The rules (ADR-0005 addendum, 2026-09-23, "the billing run"):

    * **Recurring prices bill in advance** for `period`: a `flat` price is `amount x quantity`
      (quantity is 1 unless the item says otherwise), a `per_seat` price is `amount x seats`.
      A zero-quantity item bills nothing and writes no line.
    * **Usage prices bill in arrears** for `usage_period`, the period that has just closed, at the
      pinned rate: `Money.rate(unit_price_micros, quantity)`, rounded half-up once per line. With
      no `usage_period` (the first invoice after a trial — trial usage is free) usage prices
      write nothing. A meter with no recorded billable usage writes nothing.
    * **Every item must name a price on the pinned version**, and every price must be in the
      invoice currency. Either failing is a `PricingError`, never a skipped line.
    * **Tax is exclusive and computed once on the subtotal** (`Money.percent_bps`, half-up), as
      its own line, so the lines still foot exactly to the total. A zero tax writes no line.
    * `recurring=False` is a **closing period** (a cancellation's or suspension's): its recurring
      prices were billed in advance when the period opened, so only usage is billed.

    Nothing here reads a clock, a database or the network.
    """
    currency = currency.upper()
    by_id = {p.id: p for p in prices}
    lines: list[LineDraft] = []
    span = f"{_fmt_day(period.start)} to {_fmt_day(period.end)}"

    for item in items:
        price = by_id.get(item.price_id)
        if price is None:
            raise PricingError(
                f"subscription item names price {item.price_id}, which is not on plan version "
                f"{plan_version_id}"
            )
        if price.currency.upper() != currency:
            raise PricingError(
                f"price {price.id} is in {price.currency.upper()}, the invoice is in {currency}"
            )
        if item.quantity < 0:
            raise PricingError(f"item for price {price.id} has a negative quantity")

        if price.kind in (PRICE_FLAT, PRICE_PER_SEAT):
            if price.amount_minor is None or price.amount_minor < 0:
                raise PricingError(f"{price.kind} price {price.id} has no amount")
            if item.quantity == 0 or not recurring:
                continue
            amount = Money(price.amount_minor, currency) * item.quantity
            label = "Subscription" if price.kind == PRICE_FLAT else f"{price.unit or 'seat'}s"
            lines.append(
                LineDraft(
                    description=f"{label}, {span}",
                    quantity=Decimal(item.quantity),
                    amount_minor=amount.minor,
                    currency=currency,
                    unit_price_micros=None,
                    plan_version_id=plan_version_id,
                )
            )
        elif price.kind == PRICE_USAGE:
            if price.unit_price_micros is None or price.unit_price_micros < 0:
                raise PricingError(f"usage price {price.id} has no rate")
            if not price.meter_key:
                raise PricingError(f"usage price {price.id} names no meter")
            if usage_period is None or usage is None:
                continue
            quantity = int(usage.get(price.meter_key, 0))
            if quantity < 0:
                raise PricingError(f"meter {price.meter_key} reported negative usage")
            if quantity == 0:
                continue
            amount = Money.rate(price.unit_price_micros, quantity, currency)
            lines.append(
                LineDraft(
                    description=(
                        f"{price.meter_key} usage, {_fmt_day(usage_period.start)} to "
                        f"{_fmt_day(usage_period.end)}"
                    ),
                    quantity=Decimal(quantity),
                    amount_minor=amount.minor,
                    currency=currency,
                    unit_price_micros=price.unit_price_micros,
                    plan_version_id=plan_version_id,
                )
            )
        else:
            raise PricingError(f"price {price.id} has unknown kind {price.kind!r}")

    return _with_tax(lines, currency, tax)


def _with_tax(lines: list[LineDraft], currency: str, tax: TaxSpec | None) -> list[LineDraft]:
    """Exclusive tax, once on the subtotal, half-up, as its own line; none on nothing."""
    if tax is not None and tax.rate_bps > 0 and lines:
        subtotal = sum_lines(lines, currency)
        tax_amount = subtotal.percent_bps(tax.rate_bps)
        if not tax_amount.is_zero:
            whole, frac = divmod(tax.rate_bps, 100)
            lines.append(
                LineDraft(
                    description=f"{tax.name} {whole}.{frac:02d}%",
                    quantity=Decimal(1),
                    amount_minor=tax_amount.minor,
                    currency=currency,
                )
            )
    return lines


def recurring_total(
    *,
    currency: str,
    plan_version_id: UUID,
    prices: Sequence[PriceSpec],
    items: Sequence[ItemSpec],
) -> Money:
    """One full period's recurring price (`flat` and `per_seat`) for a pin and its items — the
    same lines `price_subscription_period` writes, summed, with usage and tax left out."""
    period = Period(datetime(2000, 1, 1, tzinfo=UTC), datetime(2000, 1, 2, tzinfo=UTC))
    lines = price_subscription_period(
        currency=currency,
        plan_version_id=plan_version_id,
        prices=prices,
        items=items,
        period=period,
        usage_period=None,
        usage=None,
        tax=None,
    )
    return sum_lines(lines, currency.upper())


def price_proration(
    *,
    currency: str,
    new_plan_version_id: UUID,
    new_prices: Sequence[PriceSpec],
    new_items: Sequence[ItemSpec],
    old_plan_version_id: UUID,
    old_prices: Sequence[PriceSpec],
    old_items: Sequence[ItemSpec],
    full_period: Period,
    effective_at: datetime,
    tax: TaxSpec | None,
) -> list[LineDraft]:
    """An upgrade's charge for the rest of the current period (ADR-0005 addendum §4, as revised).

    Each side's full-period recurring price is prorated with `prorate` — largest-remainder over
    whole **seconds** remaining out of the period's seconds, integer minor units throughout — and
    the charge is the new share less the old share. Never negative: a change that nets to no more
    (the list comparison called it an upgrade; seats or rounding made it equal) bills nothing and
    writes no line, and no credit is issued here. Tax as everywhere else.
    """
    currency = currency.upper()
    if not full_period.start <= effective_at < full_period.end:
        raise PricingError("the upgrade is not inside the period it prorates")
    period_seconds = int((full_period.end - full_period.start).total_seconds())
    remaining = int((full_period.end - effective_at).total_seconds())
    new_full = recurring_total(
        currency=currency, plan_version_id=new_plan_version_id, prices=new_prices, items=new_items
    )
    old_full = recurring_total(
        currency=currency, plan_version_id=old_plan_version_id, prices=old_prices, items=old_items
    )
    new_share = prorate(new_full, elapsed_units=remaining, period_units=period_seconds)
    old_share = prorate(old_full, elapsed_units=remaining, period_units=period_seconds)
    difference = new_share - old_share
    if difference.minor <= 0:
        return []
    lines = [
        LineDraft(
            description=(
                f"Upgrade, prorated {_fmt_day(effective_at)} to {_fmt_day(full_period.end)}"
            ),
            quantity=Decimal(1),
            amount_minor=difference.minor,
            currency=currency,
            plan_version_id=new_plan_version_id,
        )
    ]
    return _with_tax(lines, currency, tax)


def subscription_invoice_key(
    subscription_id: UUID,
    period_start: datetime,
    *,
    kind: str = "period",
    change_id: UUID | None = None,
) -> str:
    """The platform idempotency key for one billing-run invoice.

    Built by billing from the facts that identify the claim, never accepted from a caller as free
    text, and normalised to UTC with microseconds so the same instant always yields the same
    string however the caller's client serialised it:

    * `period` — `subscription:{id}:{period_start}`
    * `proration` — `subscription:{id}:proration:{change_id}` (one upgrade, one invoice)
    * `close` — `subscription:{id}:{period_start}:close`
    """
    if kind == "proration":
        if change_id is None:
            raise ValueError("a proration is keyed on its change")
        return f"subscription:{subscription_id}:proration:{change_id}"
    at = period_start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    base = f"subscription:{subscription_id}:{at}"
    return f"{base}:close" if kind == "close" else base


def subscription_invoice_number(key: str, period_start: datetime, attempt: int = 0) -> str:
    """A human-facing number, deterministic in the invoice's key.

    Derived from a hash of the **whole** key, so a period, its proration and its close — three
    keys for one subscription and day — get three numbers, and a retry that races another mints
    the same one. `attempt` suffixes it (`-2`, `-3`, …) for the one clash hashing cannot rule out:
    a hand-finalized invoice that happened to take the number first (review fix 12).
    """
    day = period_start.astimezone(UTC).strftime("%Y%m%d")
    digest = hashlib.sha256(key.encode()).hexdigest()[:10].upper()
    number = f"INV-{day}-{digest}"
    return number if attempt == 0 else f"{number}-{attempt + 1}"


# --------------------------------------------------------------------- GST (ADR-0004 Q1)
# Groundwork only: nothing in finalize calls these yet, so no invoice changes until the supplier
# settings are configured and a later slice wires them in.

#: The prefix of the supplier's tax-invoice series, by series (ADR-0004 Q1 decision 4). The
#: credit-note series has no decided format yet, so it is absent and refused rather than guessed.
TAX_INVOICE_PREFIXES: dict[str, str] = {"invoice": "OG"}

#: CGST Rule 46(b): the serial is at most 16 characters.
MAX_TAX_INVOICE_NUMBER_LENGTH = 16


def place_of_supply(
    *, supplier_state_code: str, buyer_gstin: str | None, profile_state_code: str | None
) -> str:
    """The GST state code the supply is made in: the buyer GSTIN's state (B2B); without one, the
    profile's state if an address is on record; otherwise the supplier's own (IGST Act s.12(2)).

    A malformed GSTIN or an unknown state code raises rather than falling through to the next
    rule — a wrong place of supply picks the wrong tax head, which is a refund claim, not a
    rounding difference.
    """
    if buyer_gstin:
        return gst.parse_gstin(buyer_gstin).state_code
    state = profile_state_code or supplier_state_code
    if state not in gst.GST_STATE_CODES:
        raise ValueError(f"{state!r} is not a GST state code")
    return state


def tax_heads(
    total_minor: int, *, supplier_state_code: str, place_of_supply: str, supply_type: str = "b2b"
) -> gst.TaxSplit:
    """Split the invoice's GST total (computed once on the subtotal) into its heads.

    IGST takes all of it when the place of supply is outside the supplier's state, or when the
    recipient is SEZ or export (inter-state by statute). Otherwise CGST is `⌊total / 2⌋` and SGST
    the remainder, so the heads always add back to the total.
    """
    intra = gst.is_intra_state(
        supply_type=supply_type,
        seller_state_code=supplier_state_code,
        place_of_supply=place_of_supply,
    )
    return gst.split_gst_total(total_minor, intra_state=intra)


def tax_invoice_number(series: str, financial_year: str, seq: int) -> str:
    """`OG/2627/000001`: series prefix, the financial year's two two-digit halves, the serial."""
    prefix = TAX_INVOICE_PREFIXES.get(series)
    if prefix is None:
        raise ValueError(f"no number format is decided for the {series!r} series")
    if seq < 1:
        raise ValueError("a tax-invoice serial starts at 1")
    number = f"{prefix}/{financial_year[2:4]}{financial_year[5:7]}/{seq:06d}"
    if len(number) > MAX_TAX_INVOICE_NUMBER_LENGTH:
        raise ValueError(f"{number!r} exceeds Rule 46's 16 characters")
    return number
