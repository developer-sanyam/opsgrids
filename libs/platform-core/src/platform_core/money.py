"""Money as integer minor units (ADR-0000 §8).

Two decimal places is wrong for JPY (zero) and KWD (three), and float arithmetic is wrong
for everything. An amount is an integer count of the currency's smallest unit plus an
ISO-4217 code; a *rate* that needs sub-minor precision is stored in micros.

The only subtle piece here is `allocate`. Splitting 100 across three ways gives 33/33/33
and loses a unit; an invoice whose lines do not sum to its total is a bug you find during
a customer dispute. Largest-remainder distributes the residual deterministically so the
parts always sum to the whole.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Self

# Currencies whose minor unit is not 1/100. Extend as markets are added.
_EXPONENTS: Final[dict[str, int]] = {
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "CLP": 0,
    "ISK": 0,
    "BHD": 3,
    "KWD": 3,
    "OMR": 3,
    "TND": 3,
    "JOD": 3,
}
DEFAULT_EXPONENT: Final[int] = 2

MICROS_PER_UNIT: Final[int] = 1_000_000


def exponent(currency: str) -> int:
    """Number of decimal places in `currency`'s minor unit."""
    return _EXPONENTS.get(currency.upper(), DEFAULT_EXPONENT)


class CurrencyMismatch(ValueError):
    """Two amounts in different currencies were combined."""


@dataclass(frozen=True, slots=True, order=False)
class Money:
    """An exact amount. `minor` is a count of the smallest unit, never a fraction."""

    minor: int
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.minor, int) or isinstance(self.minor, bool):
            raise TypeError("Money.minor must be an int — never a float or Decimal")
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValueError(f"Not an ISO-4217 code: {self.currency!r}")
        object.__setattr__(self, "currency", self.currency.upper())

    # ---------------------------------------------------------------- construction
    @classmethod
    def zero(cls, currency: str) -> Self:
        return cls(0, currency)

    @classmethod
    def from_decimal(cls, amount: Decimal | str | int, currency: str) -> Self:
        """Parse a major-unit amount. Half-up, because that is what an invoice does."""
        scale = Decimal(10) ** exponent(currency)
        quantised = (Decimal(amount) * scale).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return cls(int(quantised), currency)

    def to_decimal(self) -> Decimal:
        """Major units, for display and for tax filings. Never for arithmetic."""
        return Decimal(self.minor) / (Decimal(10) ** exponent(self.currency))

    # ---------------------------------------------------------------- arithmetic
    def _same(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.minor - other.minor, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.minor, self.currency)

    def __mul__(self, count: int) -> Money:
        if not isinstance(count, int) or isinstance(count, bool):
            raise TypeError("Multiply Money by a whole count; use `rate` for prices")
        return Money(self.minor * count, self.currency)

    __rmul__ = __mul__

    def __lt__(self, other: Money) -> bool:
        self._same(other)
        return self.minor < other.minor

    def __le__(self, other: Money) -> bool:
        self._same(other)
        return self.minor <= other.minor

    @property
    def is_zero(self) -> bool:
        return self.minor == 0

    # ---------------------------------------------------------------- rates
    @classmethod
    def rate(cls, unit_price_micros: int, quantity: Decimal | int, currency: str) -> Self:
        """Price `quantity` at a per-unit rate held in micros of a **major** unit.

        1_000_000 micros = one rupee/dollar. Usage prices need finer precision than a minor
        unit — a fraction of a paisa per API call is normal — so the rate is stored exactly
        and rounding happens once, here, when the line is priced.
        """
        scale = Decimal(10) ** exponent(currency)
        exact = Decimal(unit_price_micros) * Decimal(quantity) * scale / Decimal(MICROS_PER_UNIT)
        return cls(int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP)), currency)

    # ---------------------------------------------------------------- allocation
    def allocate(self, weights: list[int]) -> list[Money]:
        """Split across `weights` so the parts sum **exactly** to self.

        Largest-remainder: floor every share, then hand the leftover units out one at a
        time to the largest fractional remainders. Deterministic, and it never loses or
        invents a unit — which is what makes an invoice foot.
        """
        if not weights:
            raise ValueError("allocate needs at least one weight")
        if any(w < 0 for w in weights):
            raise ValueError("weights must not be negative")
        total_weight = sum(weights)
        if total_weight == 0:
            raise ValueError("weights must not all be zero")

        # Work on magnitude so a credit allocates the mirror image of a charge.
        sign = -1 if self.minor < 0 else 1
        amount = abs(self.minor)

        shares = [amount * w // total_weight for w in weights]
        remainder = amount - sum(shares)

        # Rank by fractional part, largest first; ties go to the earlier line.
        order = sorted(
            range(len(weights)),
            key=lambda i: (-(amount * weights[i] % total_weight), i),
        )
        for i in order[:remainder]:
            shares[i] += 1

        return [Money(sign * s, self.currency) for s in shares]

    def split(self, parts: int) -> list[Money]:
        """Even split that still sums exactly to self."""
        return self.allocate([1] * parts)

    def percent_bps(self, bps: int) -> Money:
        """A share in basis points (10000 bps = 100%), rounded half-up."""
        exact = Decimal(self.minor) * Decimal(bps) / Decimal(10_000)
        return Money(int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP)), self.currency)

    # ---------------------------------------------------------------- display
    def __str__(self) -> str:
        return f"{self.to_decimal():.{exponent(self.currency)}f} {self.currency}"

    def __repr__(self) -> str:
        return f"Money({self.minor}, {self.currency!r})"


def total(amounts: list[Money], currency: str) -> Money:
    """Sum, with an explicit currency so an empty list still has one."""
    acc = Money.zero(currency)
    for a in amounts:
        acc = acc + a
    return acc
