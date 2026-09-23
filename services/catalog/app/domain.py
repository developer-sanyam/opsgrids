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

import itertools
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
      a key that means nothing to the gateway.
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
    "BundleRung",
    "PermissionSpec",
    "PricedComponent",
    "SeatClassSpec",
    "bundle_saving",
    "cheaper_per_product",
    "is_read_only",
    "ladder_is_graduated",
    "list_total",
    "seat_class_grants",
]
