"""Pure money mechanics, and the typed seam a future billing run will implement.

No I/O lives here (ADR service anatomy): proration and discount allocation are integer
arithmetic on `platform_core.money.Money`, and the currency guard is `Money`'s own `_same`,
reached simply by adding two amounts. There are **no floats and no `Numeric`** in any of it
(ADR-0000 §8) — the whole reason money is integer minor units is that these operations are
exact, and that an invoice's lines foot to its total to the last minor unit.

The seam
--------
`finalize` in this service takes **explicit** line items. Assembling those lines from catalog
prices and metering usage — a "billing run" — is a deliberately deferred concern (the brief's
scope boundary; ADR-0004 §7). Rather than leave a bare comment, this module ships the interface
that work will implement, `PricingSource`, together with the one minimal implementation that
exists today, `ExplicitPricingSource`, which returns the lines it was handed. `finalize` depends
on the interface, so the day a catalog+metering-backed source is written it drops in with no
change to the service — and **nothing here imports catalog or metering**.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

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


# --------------------------------------------------------------------- the deferred seam


class UsageSource(Protocol):
    """How a future billing run reads metered quantities for a period.

    Ships as a type only: no implementation lives in this service, because billing does not read
    metering in this PR (ADR-0004 §7). A `PricingSource` that prices usage lines will depend on
    one of these; it is named here so that dependency has a contract to be written against.
    """

    async def quantity_for(
        self, *, meter_key: str, subscription_id: UUID, period: Period
    ) -> Decimal: ...


class PricingSource(Protocol):
    """How invoice lines are produced for a subscription and period — the billing-run seam.

    Today the only implementation is `ExplicitPricingSource`, which returns lines given to it.
    The future billing run implements this by reading catalog prices and, via a `UsageSource`,
    metering usage; when it does, `finalize` uses it unchanged. The interface is what makes that
    a drop-in rather than a rewrite (ADR-0004 §7).
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
