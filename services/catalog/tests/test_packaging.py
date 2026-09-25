"""Commerce packaging over the catalogue that already existed.

Three of the four packaging shapes on the market backlog needed no new machinery, and the point
of this module is to prove that rather than assert it. Each class below corresponds to one shape
and says which side of the line it fell on:

* **Bundles are configuration.** A bundle is an ordinary immutable plan version whose `features`
  name its members and its rung — not a discount flag layered over the standalone plans. What is
  tested is that the money works out (a rung is cheaper than its members bought apart, and each
  further rung is cheaper *per product* than the one below it) and that it stays reconstructable:
  re-pricing the ladder afterwards must not move what a pinned version costs.
* **Partner pricing is configuration.** A reseller's wholesale price is `restricted_to_org_id`
  pointed at the reseller — the same audience-of-one mechanism a negotiated enterprise price
  uses. It is guarded by the same negative-authz test, because a leak here shows one customer
  another's channel margin.
* **Seat classes needed code.** Nothing in a price sheet could say that a plan sells two kinds of
  seat and that one of them may not write. The load-bearing test is the last one: the permission
  set derived for a view-only class contains no write and no dangerous permission, because
  authorization is server-side and a "view-only" seat that is only view-only in the UI is not
  view-only at all.
"""

from __future__ import annotations

import contextlib
import importlib.util
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import ClassVar

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors, services
from app.main import app
from app.models import (
    FEATURE_BUNDLE_TIER,
    FEATURE_BUNDLED_PRODUCTS,
    FEATURE_PARTNER_OF,
    PlanVersion,
)
from app.schemas import FeatureIn, PlanCreateIn, PriceIn, SeatClassIn, VersionCreateIn
from platform_core.auth import Principal, get_principal
from platform_core.errors import BusinessRuleProblem
from platform_core.money import CurrencyMismatch, Money
from tests.helpers import FakeRegistry, customer_principal, operator_principal

pytestmark = pytest.mark.integration

INR = "INR"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _seed_module() -> ModuleType:
    """The packaging seed migration, imported by path.

    Deliberate coupling: the seeded ladder is the platform's actual price list, and a test that
    built its own numbers would prove the arithmetic works while saying nothing about whether
    what ships is graduated. Loaded by path because a migrations directory is not a package.
    """
    path = (
        Path(__file__).resolve().parent.parent
        / "migrations"
        / "versions"
        / "f78c86fcec78_seed_commerce_packaging.py"
    )
    spec = importlib.util.spec_from_file_location("seed_commerce_packaging", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _publish(
    session: AsyncSession,
    *,
    product_key: str,
    code: str,
    per_seat_minor: int,
    features: list[FeatureIn] | None = None,
    seat_classes: list[SeatClassIn] | None = None,
    restricted_to_org_id: uuid.UUID | None = None,
) -> PlanVersion:
    """One published, per-seat-priced plan version — the unit every case here is built from.

    Returned re-read through the selector, so its prices, features and seat classes are eagerly
    loaded: async SQLAlchemy forbids the implicit lazy load a bare relationship access would
    trigger once the transaction has closed.
    """
    op = operator_principal()
    plan = await services.create_plan(
        session,
        op,
        PlanCreateIn(product_key=product_key, code=code, name=code.title()),
        request=None,
    )
    version = await services.create_version(
        session,
        op,
        plan,
        VersionCreateIn(
            currency=INR,
            restricted_to_org_id=restricted_to_org_id,
            prices=[
                PriceIn(kind="per_seat", amount_minor=per_seat_minor, currency=INR, unit="seat")
            ],
            features=features or [],
            seat_classes=seat_classes or [],
        ),
        request=None,
        # These ladders live under throwaway product keys; register them as the bundle and its
        # members so authoring validates against a registry that knows them.
        registry=FakeRegistry.default().with_bundle(
            product_key,
            next(
                (list(f.value) for f in features or [] if f.feature_key == "bundled_products"),
                [],
            ),
        ),
    )
    published = await services.publish_version(session, op, version, request=None)
    fresh = await selectors.get_version(session, published.id)
    assert fresh is not None
    return fresh


def _components(version: PlanVersion) -> list[domain.PricedComponent]:
    return [
        domain.PricedComponent(kind=p.kind, amount_minor=p.amount_minor, currency=p.currency)
        for p in version.prices
    ]


# --------------------------------------------------------------------- bundles: configuration


class TestBundlesArePlanVersions:
    """A bundle is a plan version, and its price is that version's price sheet.

    Nothing here computes a discount. A bundled invoice is priced exactly the way a standalone
    one is — from the frozen sheet the line already names — which is what keeps it defensible
    years later. The "discount" only exists as a *comparison* between two published prices, and
    that is what these tests measure.
    """

    async def test_a_bundle_is_cheaper_than_its_members_bought_apart(
        self, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            hrms = await _publish(
                platform_session, product_key="pk-hrms", code="growth", per_seat_minor=34900
            )
            crm = await _publish(
                platform_session, product_key="pk-crm", code="growth", per_seat_minor=29900
            )
            duo = await _publish(
                platform_session,
                product_key="pk-suite",
                code="duo",
                per_seat_minor=58200,
                features=[
                    FeatureIn(feature_key=FEATURE_BUNDLED_PRODUCTS, value=["pk-hrms", "pk-crm"]),
                    FeatureIn(feature_key=FEATURE_BUNDLE_TIER, value="duo"),
                ],
            )

        seats = 12
        apart = [
            domain.list_total(_components(hrms), seats=seats, currency=INR),
            domain.list_total(_components(crm), seats=seats, currency=INR),
        ]
        bundled = domain.list_total(_components(duo), seats=seats, currency=INR)

        assert bundled == Money(58200 * seats, INR)
        assert domain.bundle_saving(bundled, apart) == Money((34900 + 29900 - 58200) * seats, INR)
        assert bundled < apart[0] + apart[1], "a bundle nobody saves on is not a bundle"

    async def test_the_bundle_declares_its_members_on_the_version(
        self, platform_session: AsyncSession
    ) -> None:
        """What makes a version a bundle is a feature row, not a column and not a flag.

        The declaration has to be *on the version* so it is frozen with the price: a bundle whose
        membership could drift after publication would let a customer's invoice and their access
        disagree, with no record of when they stopped matching.
        """
        async with platform_session.begin():
            trio = await _publish(
                platform_session,
                product_key="pk-suite2",
                code="trio",
                per_seat_minor=80700,
                features=[
                    FeatureIn(
                        feature_key=FEATURE_BUNDLED_PRODUCTS, value=["hrms", "crm", "finance"]
                    ),
                    FeatureIn(feature_key=FEATURE_BUNDLE_TIER, value="trio"),
                ],
            )
        declared = {f.feature_key: f.value for f in trio.features}
        assert declared[FEATURE_BUNDLED_PRODUCTS] == ["hrms", "crm", "finance"]
        assert declared[FEATURE_BUNDLE_TIER] == "trio"

    async def test_a_pinned_bundle_price_survives_the_ladder_being_re_priced(
        self, platform_session: AsyncSession
    ) -> None:
        """The reconstructability property, which is the whole reason a bundle is a *version*.

        A customer pins v1. The ladder is then re-priced — a new version, never an edit. Reading
        the pinned id back must still produce the original total, or every invoice priced against
        it becomes indefensible the moment sales changes the price list.
        """
        async with platform_session.begin():
            v1 = await _publish(
                platform_session,
                product_key="pk-suite3",
                code="duo",
                per_seat_minor=58200,
                features=[FeatureIn(feature_key=FEATURE_BUNDLE_TIER, value="duo")],
            )
            pinned_id = v1.id
            original = domain.list_total(_components(v1), seats=10, currency=INR)

        # Re-price: a new version of the same plan, published beside the old one.
        async with platform_session.begin():
            plan = await services.require_plan(platform_session, v1.plan_id)
            v2 = await services.create_version(
                platform_session,
                operator_principal(),
                plan,
                VersionCreateIn(
                    currency=INR,
                    prices=[
                        PriceIn(kind="per_seat", amount_minor=64900, currency=INR, unit="seat")
                    ],
                ),
                request=None,
            )
            await services.publish_version(platform_session, operator_principal(), v2, request=None)

        async with platform_session.begin():
            still_pinned = await selectors.get_version(platform_session, pinned_id)
            assert still_pinned is not None
            reconstructed = domain.list_total(_components(still_pinned), seats=10, currency=INR)

        assert reconstructed == original == Money(582000, INR)
        assert reconstructed != Money(649000, INR), "the pin must not follow the new price"

    async def test_the_seeded_ladder_is_actually_graduated(self) -> None:
        """The price list that ships, not a price list invented for a test.

        `ladder_is_graduated` compares rungs by cross-multiplying, so this holds for any rung
        counts and never rounds. If someone re-prices a rung into a ladder where buying a fourth
        product raises the per-product price, this fails and names it.
        """
        seed = _seed_module()
        by_id = {v[0]: v for v in seed.VERSIONS}
        tiers = {
            feature[1]: feature[3] for feature in seed.FEATURES if feature[2] == FEATURE_BUNDLE_TIER
        }
        members = {
            feature[1]: feature[3]
            for feature in seed.FEATURES
            if feature[2] == FEATURE_BUNDLED_PRODUCTS
        }

        rungs = [
            domain.BundleRung(
                code=tiers[version_id],
                products=len(members[version_id]),
                total=Money(by_id[version_id][4], INR),
            )
            # Public rungs only: the partner version is the same trio at a wholesale price and
            # is not a further rung of the ladder.
            for version_id in members
            if by_id[version_id][3] is None
        ]
        rungs.sort(key=lambda rung: rung.products)

        assert [r.products for r in rungs] == [2, 3, 4], "the seeded ladder should have 3 rungs"
        assert domain.ladder_is_graduated(rungs), (
            "each rung must be cheaper per product than the one below it"
        )
        # And the ladder's own numbers, stated once so a re-price has to come here too.
        assert [r.total.minor for r in rungs] == [58200, 80700, 95600]

    def test_a_ladder_that_gets_dearer_per_product_is_not_graduated(self) -> None:
        """The negative case. Two products at 58200 is 29100 each; three at 90000 is 30000 each,
        so the third product made every product dearer — which is an anti-bundle."""
        rungs = [
            domain.BundleRung(code="duo", products=2, total=Money(58200, INR)),
            domain.BundleRung(code="trio", products=3, total=Money(90000, INR)),
        ]
        assert not domain.ladder_is_graduated(rungs)

    def test_per_product_comparison_never_divides(self) -> None:
        """Cross-multiplied, so a ladder whose per-product rates are not whole minor units still
        compares exactly. 3 products at 100 is 33.33 each; 4 at 133 is 33.25 — cheaper, and a
        comparison that floored each side to 33 would have called them equal."""
        trio = domain.BundleRung(code="trio", products=3, total=Money(100, INR))
        full = domain.BundleRung(code="full", products=4, total=Money(133, INR))
        assert domain.cheaper_per_product(full, trio)
        assert not domain.cheaper_per_product(trio, full)


class TestListTotalIsExactIntegerMoney:
    def test_flat_counts_once_and_per_seat_counts_per_seat(self) -> None:
        components = [
            domain.PricedComponent(kind="flat", amount_minor=50000, currency=INR),
            domain.PricedComponent(kind="per_seat", amount_minor=19900, currency=INR),
        ]
        assert domain.list_total(components, seats=7, currency=INR) == Money(50000 + 19900 * 7, INR)

    def test_a_usage_rate_contributes_nothing_to_the_recurring_total(self) -> None:
        """A meter nobody has read has no amount. Counting it as zero is right; counting its
        `unit_price_micros` as a minor-unit amount would be off by a factor of ten thousand."""
        components = [
            domain.PricedComponent(kind="per_seat", amount_minor=19900, currency=INR),
            domain.PricedComponent(kind="usage", amount_minor=None, currency=INR),
        ]
        assert domain.list_total(components, seats=2, currency=INR) == Money(39800, INR)

    def test_a_component_in_another_currency_raises_rather_than_totalling(self) -> None:
        components = [
            domain.PricedComponent(kind="flat", amount_minor=1000, currency=INR),
            domain.PricedComponent(kind="flat", amount_minor=1000, currency="USD"),
        ]
        with pytest.raises(CurrencyMismatch, match=r"INR vs USD"):
            domain.list_total(components, seats=1, currency=INR)


# --------------------------------------------------------------------- partner: configuration


class TestPartnerPricingIsTheAudienceOfOne:
    """A reseller price is `restricted_to_org_id` pointed at the reseller. No new code.

    Same table, same filter, same negative-authz test as a negotiated enterprise price — which is
    the argument for having built audience-of-one pricing as a plan version rather than a
    discount column. Channel distribution turned out to be the same problem wearing a hat.
    """

    async def test_a_partner_price_is_invisible_to_every_other_organization(
        self, platform_session: AsyncSession, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        async with platform_session.begin():
            public = await _publish(
                platform_session, product_key="pk-channel", code="trio", per_seat_minor=80700
            )
            plan = await services.require_plan(platform_session, public.plan_id)
            wholesale = await services.create_version(
                platform_session,
                operator_principal(),
                plan,
                VersionCreateIn(
                    currency=INR,
                    restricted_to_org_id=org_a,
                    prices=[
                        PriceIn(kind="per_seat", amount_minor=68595, currency=INR, unit="seat")
                    ],
                    features=[FeatureIn(feature_key=FEATURE_PARTNER_OF, value=str(org_a))],
                ),
                request=None,
            )
            wholesale = await services.publish_version(
                platform_session, operator_principal(), wholesale, request=None
            )

        async with platform_session.begin():
            seen_by_partner = await selectors.visible_plans(
                platform_session, product_key="pk-channel", org_id=org_a
            )
            seen_by_stranger = await selectors.visible_plans(
                platform_session, product_key="pk-channel", org_id=org_b
            )

        partner_versions = {v.id for _, vs in seen_by_partner for v in vs}
        stranger_versions = {v.id for _, vs in seen_by_stranger for v in vs}

        assert wholesale.id in partner_versions, "the reseller must see its own wholesale price"
        assert public.id in stranger_versions, "the list price stays public"
        assert wholesale.id not in stranger_versions, (
            "a partner's channel margin leaked to another organization"
        )

    async def test_the_customer_route_hides_a_partner_price_too(
        self, client, platform_session: AsyncSession, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """End to end over HTTP: the filter is on the read, not on a screen."""
        async with platform_session.begin():
            base = await _publish(
                platform_session, product_key="pk-channel2", code="trio", per_seat_minor=80700
            )
            plan = await services.require_plan(platform_session, base.plan_id)
            wholesale = await services.create_version(
                platform_session,
                operator_principal(),
                plan,
                VersionCreateIn(
                    currency=INR,
                    restricted_to_org_id=org_a,
                    prices=[
                        PriceIn(kind="per_seat", amount_minor=68595, currency=INR, unit="seat")
                    ],
                ),
                request=None,
            )
            await services.publish_version(
                platform_session, operator_principal(), wholesale, request=None
            )

        with as_principal(customer_principal(org_a)):
            partner = await client.get("/api/v1/plans", params={"product_key": "pk-channel2"})
        with as_principal(customer_principal(org_b)):
            stranger = await client.get("/api/v1/plans", params={"product_key": "pk-channel2"})

        # The customer read carries no amounts at all (the owner's rule: prices are set per
        # client by staff), so the partner version is told apart by its id, not its price.
        partner_versions = {
            version["id"] for plan_out in partner.json() for version in plan_out["versions"]
        }
        stranger_versions = {
            version["id"] for plan_out in stranger.json() for version in plan_out["versions"]
        }
        assert str(wholesale.id) in partner_versions
        assert str(wholesale.id) not in stranger_versions
        assert str(base.id) in stranger_versions
        for body in (partner.json(), stranger.json()):
            for plan_out in body:
                for version in plan_out["versions"]:
                    assert "currency" not in version
                    for price in version["prices"]:
                        assert "amount_minor" not in price
                        assert "unit_price_micros" not in price


# --------------------------------------------------------------------- seat classes: new code


PAID_MEMBER = SeatClassIn(
    code="member", name="Member seat", price_index=0, can_write=True, sort_order=10
)
FREE_VIEWER = SeatClassIn(
    code="viewer",
    name="View-only seat",
    price_index=None,
    can_write=False,
    sort_order=20,
    description="Free. Read scopes only.",
)


class TestSeatClasses:
    """The one shape that needed a table. A price says what a seat costs; a seat class says what
    a seat *is*, and a free view-only seat is both of those at once."""

    async def test_a_version_can_sell_a_paid_and_a_free_class_at_once(
        self, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            version = await _publish(
                platform_session,
                product_key="pk-seats",
                code="growth",
                per_seat_minor=29900,
                seat_classes=[PAID_MEMBER, FREE_VIEWER],
            )

        async with platform_session.begin():
            fresh = await selectors.get_version(platform_session, version.id)
        assert fresh is not None
        classes = {sc.code: sc for sc in fresh.seat_classes}

        assert classes["member"].price_id == fresh.prices[0].id, (
            "a paid class is charged at the version's own per-seat price, not a copy of it"
        )
        assert classes["member"].can_write is True
        assert classes["viewer"].price_id is None, (
            "free is the absence of a price row, never a zero-amount one"
        )
        assert classes["viewer"].can_write is False

    async def test_seat_classes_reach_the_customer_over_http(
        self, client, platform_session: AsyncSession, org_a: uuid.UUID
    ) -> None:
        async with platform_session.begin():
            await _publish(
                platform_session,
                product_key="pk-seats-http",
                code="growth",
                per_seat_minor=29900,
                seat_classes=[PAID_MEMBER, FREE_VIEWER],
            )

        with as_principal(customer_principal(org_a)):
            resp = await client.get("/api/v1/plans", params={"product_key": "pk-seats-http"})

        assert resp.status_code == 200
        seats = [
            sc
            for plan_out in resp.json()
            for version in plan_out["versions"]
            for sc in version["seat_classes"]
        ]
        assert {sc["code"] for sc in seats} == {"member", "viewer"}
        viewer = next(sc for sc in seats if sc["code"] == "viewer")
        assert viewer["can_write"] is False
        assert viewer["price_id"] is None

    async def test_a_published_versions_seat_classes_are_frozen(
        self, platform_session: AsyncSession
    ) -> None:
        """What a seat may do is a term of the deal, so it is frozen with the price sheet.
        Flipping `can_write` on a published version would silently widen every seat sold under
        it — which is why the guard is a trigger and not a service check."""
        async with platform_session.begin():
            version = await _publish(
                platform_session,
                product_key="pk-seats-frozen",
                code="growth",
                per_seat_minor=29900,
                seat_classes=[PAID_MEMBER, FREE_VIEWER],
            )

        with pytest.raises(IntegrityError, match="immutable"):
            async with platform_session.begin():
                await platform_session.execute(
                    text(
                        "UPDATE catalog.seat_classes SET can_write = true "
                        "WHERE plan_version_id = CAST(:id AS uuid) AND code = 'viewer'"
                    ),
                    {"id": str(version.id)},
                )

    async def test_a_draft_version_clones_its_seat_classes_onto_its_own_prices(
        self, platform_session: AsyncSession
    ) -> None:
        """The clone must re-point a seat class at the *new* version's price row.

        Carrying the old `price_id` across would leave a new draft charging seats at a frozen
        predecessor's rate — and would still look right, because the id resolves.
        """
        async with platform_session.begin():
            v1 = await _publish(
                platform_session,
                product_key="pk-seats-clone",
                code="growth",
                per_seat_minor=29900,
                seat_classes=[PAID_MEMBER, FREE_VIEWER],
            )

        async with platform_session.begin():
            plan = await services.require_plan(platform_session, v1.plan_id)
            v2 = await services.create_version(
                platform_session,
                operator_principal(),
                plan,
                VersionCreateIn(currency=INR),  # nothing supplied -> clone
                request=None,
            )
            fresh = await selectors.get_version(platform_session, v2.id)

        assert fresh is not None
        cloned = {sc.code: sc for sc in fresh.seat_classes}
        assert set(cloned) == {"member", "viewer"}
        assert cloned["viewer"].price_id is None
        assert cloned["member"].price_id == fresh.prices[0].id
        assert cloned["member"].price_id != v1.prices[0].id, (
            "a cloned seat class must be charged at the new version's price, not the old one"
        )

    async def test_a_seat_class_naming_a_price_that_does_not_exist_is_refused(
        self, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            plan = await services.create_plan(
                platform_session,
                operator_principal(),
                PlanCreateIn(product_key="pk-seats-bad", code="growth", name="Growth"),
                request=None,
            )
            with pytest.raises(BusinessRuleProblem, match="price #4"):
                await services.create_version(
                    platform_session,
                    operator_principal(),
                    plan,
                    VersionCreateIn(
                        currency=INR,
                        prices=[
                            PriceIn(kind="per_seat", amount_minor=100, currency=INR, unit="seat")
                        ],
                        seat_classes=[
                            SeatClassIn(code="ghost", name="Ghost", price_index=4),
                        ],
                    ),
                    request=None,
                )

    async def test_a_seat_class_cannot_be_charged_at_a_flat_price(
        self, platform_session: AsyncSession
    ) -> None:
        """A flat price bills once however many seats are bought, so a seat class charged at one
        would bill the same for one seat and for a hundred."""
        async with platform_session.begin():
            plan = await services.create_plan(
                platform_session,
                operator_principal(),
                PlanCreateIn(product_key="pk-seats-flat", code="growth", name="Growth"),
                request=None,
            )
            with pytest.raises(BusinessRuleProblem, match="per_seat"):
                await services.create_version(
                    platform_session,
                    operator_principal(),
                    plan,
                    VersionCreateIn(
                        currency=INR,
                        prices=[PriceIn(kind="flat", amount_minor=50000, currency=INR)],
                        seat_classes=[
                            SeatClassIn(code="member", name="Member", price_index=0),
                        ],
                    ),
                    request=None,
                )


class TestAViewOnlySeatCannotWrite:
    """The entitlement half of the seat class, which is the half that matters.

    A seat class that is view-only only in a screen is not view-only: a UI gate hides a control,
    it does not protect data (README, "rules that are not style preferences"). So the permission
    set a view-only seat may hold is *computed* from the class, and these tests are about what
    that computation refuses to include.
    """

    CATALOGUE: ClassVar[list[domain.PermissionSpec]] = [
        domain.PermissionSpec("hrms.people.employee.read"),
        domain.PermissionSpec("hrms.people.employee.write"),
        domain.PermissionSpec("hrms.people.employee.read_sensitive", is_dangerous=True),
        domain.PermissionSpec("hrms.time.attendance.read"),
        domain.PermissionSpec("hrms.time.attendance.write"),
        domain.PermissionSpec("hrms.leave.request.approve"),
        domain.PermissionSpec("hrms.people.employee.export"),
    ]

    def test_a_view_only_seat_gets_read_scopes_and_nothing_else(self) -> None:
        grants = domain.seat_class_grants(
            domain.SeatClassSpec(code="viewer", can_write=False), self.CATALOGUE
        )
        assert grants == ["hrms.people.employee.read", "hrms.time.attendance.read"]

    def test_no_write_permission_survives_the_derivation(self) -> None:
        """Stated as a property rather than a list, so a permission added to the catalogue later
        cannot slip a write into a view-only seat without failing here."""
        grants = domain.seat_class_grants(
            domain.SeatClassSpec(code="viewer", can_write=False), self.CATALOGUE
        )
        assert not any(code.endswith(".write") for code in grants)

    def test_an_unclassified_action_does_not_fall_through_to_a_view_only_seat(self) -> None:
        """`approve` and `export` are neither `read` nor `write`. The filter is a whitelist of
        read actions, so a new verb is excluded until somebody classifies it — the safe default
        for a seat class whose entire promise is that it cannot change anything."""
        grants = domain.seat_class_grants(
            domain.SeatClassSpec(code="viewer", can_write=False), self.CATALOGUE
        )
        assert "hrms.leave.request.approve" not in grants
        assert "hrms.people.employee.export" not in grants

    def test_a_dangerous_read_is_excluded_even_though_it_reads(self) -> None:
        """`read_sensitive` unmasks PII and writes an audit row every time. "View-only" is a
        statement about blast radius, not about the verb."""
        grants = domain.seat_class_grants(
            domain.SeatClassSpec(code="viewer", can_write=False), self.CATALOGUE
        )
        assert "hrms.people.employee.read_sensitive" not in grants

    def test_a_writing_seat_gets_the_catalogue_as_published(self) -> None:
        """Which role a paying seat actually holds is the product's business; the seat class only
        decides whether writing is possible at all."""
        grants = domain.seat_class_grants(
            domain.SeatClassSpec(code="member", can_write=True), self.CATALOGUE
        )
        assert grants == [spec.code for spec in self.CATALOGUE]
