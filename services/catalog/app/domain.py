"""Pure packaging arithmetic and the seat-class → permission derivation. No I/O.

Two real algorithms live here, and nothing else does (ADR service anatomy: `domain.py` exists
only where there is one).

**What a plan version costs, exactly.** `list_total` sums a version's recurring components at a
seat count in integer minor units (ADR-0000 §8). It takes a *frozen* price sheet, so the answer
is a property of the pinned version rather than of the catalogue's current state — which is what
makes a bundle's price reconstructable years later from the `plan_version_id` an invoice line
already records. No float, no `Numeric`, and no division: `cheaper_per_product` compares two
rungs of a graduated ladder by cross-multiplying, because dividing a total by a product count to
get a per-product rate would round, and a rounded comparison of two prices is a comparison of
two other prices.

**What a seat of a given class may do.** `seat_class_grants` turns a seat class into a set of
permission codes. A class with `can_write=False` can only ever produce read grants: the filter
is a whitelist of read actions with dangerous permissions excluded, so a new action added to a
product catalogue is *not* handed to view-only seats by default. Authorization is server-side
(README, "rules that are not style preferences") — a view-only seat is only view-only if the
grant computed for it never contains a write, which is a property of this function and is tested
as one.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from platform_core.money import CurrencyMismatch, Money

#: The price kinds that make up a version's *recurring list price*. A `usage` rate has no fixed
#: amount — what it costs depends on a meter nobody has read yet — so it is deliberately not
#: summed here; a usage-priced version simply contributes its recurring components.
RECURRING_KINDS = ("flat", "per_seat")

#: Actions a **view-only** seat class may hold. A whitelist, not a blacklist of writes: an
#: action nobody has classified yet (`approve`, `export`, `impersonate`) must not fall through
#: to a read-only seat because it did not happen to be spelled `write`.
READ_ACTIONS = frozenset({"read", "list", "view"})


@dataclass(frozen=True, slots=True)
class PricedComponent:
    """One priced component of a version, as this module needs it.

    A plain value object rather than the ORM row, so the arithmetic can be exercised without a
    database and cannot accidentally lazy-load anything.
    """

    kind: str
    amount_minor: int | None
    currency: str


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    """One permission code and whether it is dangerous, as a product catalogue publishes it.

    Mirrors the shape in `libs/contracts/permissions/*.json` (module/resource/action plus
    `is_dangerous`), flattened to what a seat-class grant needs. Catalog does not import any
    product's `catalogue.py` — a product declares its own vocabulary, and reaching into it
    across a service boundary is what this platform exists not to do.
    """

    code: str
    is_dangerous: bool = False

    @property
    def action(self) -> str:
        """The last dotted segment: `hrms.people.employee.read` → `read`."""
        return self.code.rsplit(".", 1)[-1]


@dataclass(frozen=True, slots=True)
class SeatClassSpec:
    """A seat class as the derivation needs it: what it is called, and whether it may write."""

    code: str
    can_write: bool


@dataclass(frozen=True, slots=True)
class BundleRung:
    """One rung of a graduated bundle ladder: a total, and how many products it covers.

    `total` is the *frozen* list total of a published bundle version — so a ladder is compared
    from published prices, never from a discount percentage somebody typed.
    """

    code: str
    products: int
    total: Money


# --------------------------------------------------------------------- what a version costs


def list_total(components: Iterable[PricedComponent], *, seats: int, currency: str) -> Money:
    """The exact recurring list total of a price sheet at `seats` seats, in minor units.

    `flat` counts once; `per_seat` counts `seats` times; `usage` is not counted (see
    `RECURRING_KINDS`). Every step is integer arithmetic on `Money`, and a component quoted in
    another currency raises through `Money._same` rather than silently producing a wrong total.
    """
    if seats < 0:
        raise ValueError("seats must not be negative")

    running = Money.zero(currency)
    for component in components:
        if component.kind not in RECURRING_KINDS or component.amount_minor is None:
            continue
        amount = Money(component.amount_minor, component.currency)
        running = running + (amount * seats if component.kind == "per_seat" else amount)
    return running


def bundle_saving(bundle: Money, standalone: Sequence[Money]) -> Money:
    """What buying the bundle saves against buying its members separately.

    Positive when the bundle is cheaper, which is the whole premise of selling one. Negative is
    not an error here — it is the answer a mispriced rung deserves, and the seed test asserts on
    the sign rather than this function pretending it cannot happen.
    """
    apart = Money.zero(bundle.currency)
    for amount in standalone:
        apart = apart + amount
    return apart - bundle


def cheaper_per_product(rung: BundleRung, than: BundleRung) -> bool:
    """Is `rung` cheaper *per product covered* than `than`? Exact integer comparison.

    `a.total / a.products < b.total / b.products`, cross-multiplied so no division and no
    rounding happens. This is what "graduated" means on a bundle ladder: each further product
    brought into the bundle lowers the price of every product in it, and the ladder is monotone
    or it is not a ladder.
    """
    if rung.total.currency != than.total.currency:
        raise CurrencyMismatch(f"{rung.total.currency} vs {than.total.currency}")
    if rung.products <= 0 or than.products <= 0:
        raise ValueError("a bundle rung must cover at least one product")
    return rung.total.minor * than.products < than.total.minor * rung.products


def ladder_is_graduated(rungs: Sequence[BundleRung]) -> bool:
    """True when every rung is cheaper per product than the one below it.

    Rungs are compared in the order given, which is the order they are sold in (two products,
    then three, then the suite). A ladder of one rung is trivially graduated.
    """
    for lower, higher in itertools.pairwise(rungs):
        if higher.products <= lower.products:
            raise ValueError("a graduated ladder must cover more products at each rung")
        if not cheaper_per_product(higher, lower):
            return False
    return True


# --------------------------------------------------------------------- what a bundle names


@dataclass(frozen=True, slots=True)
class RegisteredProduct:
    """One product-registry entry, as bundle validation needs it. Product-service owns the
    registry; `app/registry.py` fetches it, and this module only judges against it."""

    key: str
    is_platform: bool = False
    is_bundle: bool = False
    #: `available`, `beta`, `soon`. A `soon` product has no service behind it on this deployment.
    status: str = "available"


def bundle_member_problems(
    bundle: RegisteredProduct | None,
    bundle_key: str,
    members: object,
    registry: Mapping[str, RegisteredProduct],
) -> list[str]:
    """Everything wrong with a `bundled_products` declaration, or nothing.

    A subscription to the bundle entitles exactly these keys, so each of them has to be a thing
    the gateway routes and a customer can use:

    * the plan it sits on belongs to a registered **bundle** product — a `crm` plan quietly
      declaring members would entitle products the CRM price was never meant to include;
    * the list is a non-empty list of distinct product keys;
    * every member is **registered** — a typo is a customer paying for a product they never get;
    * no member is a **platform** product — every customer already has those, so selling one is
      charging for nothing;
    * no member is itself a **bundle** — a bundle's key routes nowhere, and nesting would entitle
      a key that means nothing to the gateway;
    * no member is still **`soon`** — a product with no service behind it is a customer paying
      for 404s (product-service refuses to project one too).
    """
    problems: list[str] = []
    if bundle is None or not bundle.is_bundle:
        problems.append(f"{bundle_key!r} is not a bundle product, so it cannot declare members")
    if not isinstance(members, list) or not members:
        return [*problems, "bundled_products must be a non-empty list of product keys"]
    seen: set[str] = set()
    for member in members:
        if not isinstance(member, str) or not member:
            problems.append(f"{member!r} is not a product key")
            continue
        if member in seen:
            problems.append(f"{member!r} is named twice")
            continue
        seen.add(member)
        entry = registry.get(member)
        if entry is None:
            problems.append(f"{member!r} is not a registered product")
        elif entry.is_platform:
            problems.append(f"{member!r} is a platform product every customer already has")
        elif entry.is_bundle:
            problems.append(f"{member!r} is itself a bundle")
        elif entry.status == "soon":
            problems.append(f"{member!r} has not launched (status 'soon')")
    return problems


# --------------------------------------------------------------------- what may be published

#: The currencies a version may be **published** in. An allow-list, not "any three letters":
#: `currency_iso` only checks the length, and a version published in a code nobody bills or
#: collects in is an immutable price sheet nobody can ever be charged against. The exponents
#: behind each code are `platform_core.money`'s (JPY 0, KWD 3, the rest 2); the console's money
#: parser mirrors this list (`frontend/opsgrids-internal/src/lib/minorUnits.ts`). Extend it here,
#: in one place, when a market opens — a draft in another currency can still be authored, it just
#: cannot be frozen.
SELLABLE_CURRENCIES = frozenset(
    {"INR", "USD", "EUR", "GBP", "AED", "SGD", "AUD", "CAD", "JPY", "KWD"}
)

#: The display order of a version's price components: the recurring base first, then seats, then
#: metered rates. With `price_sort_key` below it replaces the relationship's `created_at, id`
#: order for every operator read, which ties inside the one transaction that authored the sheet
#: and so fell back to uuid4 order.
_KIND_RANK = {"flat": 0, "per_seat": 1, "usage": 2}


@dataclass(frozen=True, slots=True)
class PublishablePrice:
    """One price component as publish validation reads it. A value object, not the ORM row."""

    kind: str
    amount_minor: int | None
    currency: str
    unit: str | None
    unit_price_micros: int | None
    meter_key: str | None


@dataclass(frozen=True, slots=True)
class PublishProblem:
    """One reason a draft may not be frozen: which part of the sheet, a stable code, and a
    sentence an operator can act on. Rendered as the problem document's `errors[]`."""

    field: str
    code: str
    message: str


def request_fingerprint(action: str, target: str | None, body: Mapping[str, object]) -> str:
    """The identity of an operator write, for its `Idempotency-Key`: what was asked (`action`),
    of what (`target`, the path id) and with what body — canonical JSON, sha256, hex. Two
    requests with one key and different fingerprints are two different requests, and the second
    is refused rather than silently answered with the first's result."""
    canonical = json.dumps(
        {"action": action, "target": target, "body": body},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def price_sort_key(
    kind: str,
    unit: str | None,
    meter_key: str | None,
    amount_minor: int | None,
    unit_price_micros: int | None,
    price_id: str,
) -> tuple[int, str, str, int, int, str]:
    """A total, deterministic order over a version's prices (the id breaks every tie)."""
    return (
        _KIND_RANK.get(kind, len(_KIND_RANK)),
        unit or "",
        meter_key or "",
        amount_minor if amount_minor is not None else -1,
        unit_price_micros if unit_price_micros is not None else -1,
        price_id,
    )


def trial_days_problem(value: object) -> str | None:
    """`trial_days` is whole days, 0 (sells no trial) to 365 — the only length subscription will
    grant. `bool` is refused explicitly because it is an `int` subclass in Python."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 365:
        return "trial_days must be a whole number of days, 0 to 365"
    return None


def publish_problems(
    *,
    currency: str,
    prices: Sequence[PublishablePrice],
    features: Mapping[str, object],
    allow_zero_amounts: bool,
) -> list[PublishProblem]:
    """Everything that makes a draft unsafe to freeze, or nothing.

    Publishing is irreversible (ADR-0000 §9), so this is the last moment a mistake is cheap.
    Each rule is a failure somebody would otherwise find on an invoice:

    * **at least one price** — a version that prices nothing pins a subscription to a free plan
      nobody chose;
    * **a sellable currency** (`SELLABLE_CURRENCIES`), and every price in it;
    * **a usage price names its meter** and carries a rate — without `meter_key` billing cannot
      find the usage to price, and fails on every invoice for every pin, permanently;
    * **a per-seat price names its unit** — a seat price with no unit is not a seat price;
    * **a flat or per-seat price carries an amount**;
    * **no zero amount unless the caller said so** (`allow_zero_amounts`) — a free component is
      legitimate (a free tier) but far more often a missing digit, so it must be deliberate;
    * **`trial_days`**, when declared, is re-checked here because it is frozen with the version.

    Bundle membership is re-checked by the caller against the product registry, which is I/O.
    """
    problems: list[PublishProblem] = []
    if currency not in SELLABLE_CURRENCIES:
        problems.append(
            PublishProblem(
                "currency",
                "currency_not_sellable",
                f"{currency} is not a currency this platform sells in "
                f"({', '.join(sorted(SELLABLE_CURRENCIES))})",
            )
        )
    if not prices:
        problems.append(
            PublishProblem("prices", "no_prices", "a version needs at least one price to publish")
        )
    for index, price in enumerate(prices):
        field = f"prices.{index}"
        if price.currency != currency:
            problems.append(
                PublishProblem(
                    f"{field}.currency",
                    "currency_mismatch",
                    f"price #{index + 1} is in {price.currency}, the version is in {currency}",
                )
            )
        if price.kind in RECURRING_KINDS:
            if price.amount_minor is None:
                problems.append(
                    PublishProblem(
                        f"{field}.amount_minor",
                        "amount_missing",
                        f"price #{index + 1} ({price.kind}) has no amount",
                    )
                )
            elif price.amount_minor == 0 and not allow_zero_amounts:
                problems.append(
                    PublishProblem(
                        f"{field}.amount_minor",
                        "zero_amount",
                        f"price #{index + 1} ({price.kind}) is zero; confirm a free component "
                        "explicitly to publish it",
                    )
                )
        if price.kind == "per_seat" and not (price.unit or "").strip():
            problems.append(
                PublishProblem(
                    f"{field}.unit",
                    "unit_missing",
                    f"price #{index + 1} is per seat but names no unit (for example 'seat')",
                )
            )
        if price.kind == "usage":
            if not (price.meter_key or "").strip():
                problems.append(
                    PublishProblem(
                        f"{field}.meter_key",
                        "meter_missing",
                        f"price #{index + 1} is a usage rate but names no meter, so billing "
                        "could never price it",
                    )
                )
            if price.unit_price_micros is None:
                problems.append(
                    PublishProblem(
                        f"{field}.unit_price_micros",
                        "rate_missing",
                        f"price #{index + 1} is a usage rate with no rate",
                    )
                )
            elif price.unit_price_micros == 0 and not allow_zero_amounts:
                problems.append(
                    PublishProblem(
                        f"{field}.unit_price_micros",
                        "zero_amount",
                        f"price #{index + 1} (usage) is zero; confirm a free component "
                        "explicitly to publish it",
                    )
                )
    if "trial_days" in features:
        message = trial_days_problem(features["trial_days"])
        if message is not None:
            problems.append(PublishProblem("features.trial_days", "trial_days_invalid", message))
    return problems


# --------------------------------------------------------------------- what a seat may do


def is_read_only(permission: PermissionSpec) -> bool:
    """A permission a view-only seat may hold: a whitelisted read action, and not dangerous.

    A dangerous permission is excluded even when its action reads — `read_sensitive` unmasks
    PII, and "view-only" is a statement about blast radius, not about the verb.
    """
    return permission.action in READ_ACTIONS and not permission.is_dangerous


def seat_class_grants(seat_class: SeatClassSpec, catalogue: Iterable[PermissionSpec]) -> list[str]:
    """The permission codes a seat of this class may hold, from a product's own catalogue.

    A writing class gets the catalogue as published — deciding *which* role a paying seat holds
    is the product's business, not the price sheet's. A view-only class gets `is_read_only` and
    nothing else, so the entitlement implication of the seat class is computed here rather than
    asserted in a UI. Order is the catalogue's, and the result is deduplicated.
    """
    seen: dict[str, None] = {}
    for permission in catalogue:
        if not seat_class.can_write and not is_read_only(permission):
            continue
        seen.setdefault(permission.code, None)
    return list(seen)


__all__ = [
    "READ_ACTIONS",
    "RECURRING_KINDS",
    "SELLABLE_CURRENCIES",
    "BundleRung",
    "PermissionSpec",
    "PricedComponent",
    "PublishProblem",
    "PublishablePrice",
    "SeatClassSpec",
    "bundle_saving",
    "cheaper_per_product",
    "is_read_only",
    "ladder_is_graduated",
    "list_total",
    "price_sort_key",
    "publish_problems",
    "request_fingerprint",
    "seat_class_grants",
    "trial_days_problem",
]
