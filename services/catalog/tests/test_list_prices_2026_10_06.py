"""The list prices of 2026-10-06 (`b88a833020dc`, ADR-0035): INR 30,000/month, INR 3,00,000/year.

What is pinned:

* **The owner's numbers, exactly.** The eleven monthly amounts sum to 3,000,000 paise (the owner's
  INR 30,000) and the eleven yearly ones to 30,000,000 (INR 3,00,000); each yearly amount is ten
  times its monthly one — the year is the cheaper option, by the owner's choice; the split follows
  the prices the flat plans were seeded at (read from those migrations by path, not restated). Pure,
  no database.
* **The migration inserts, and only inserts.** Every one of the eleven public flat base plans gains
  a yearly version and then a monthly one, published, public, at the new amount, with deterministic
  ids. Every row that existed before is byte-for-byte unchanged — the old version is still
  published, still the pin of every existing subscription.
* **The sheet is cloned from the plan's newest published public version** — features with their
  stored values, free seat classes — never from a bespoke version, and never onto a per-seat plan.
* **A sheet one flat price cannot replace stops the whole migration**; retired, withdrawn and
  missing plans are skipped.
* **Re-runnable**: a second run (alembic stamped back) changes nothing.
* **The downgrade refuses** while any of these versions exists unless
  `OPSGRIDS_ALLOW_LIST_PRICE_DOWNGRADE=1`; with it, it removes exactly what the upgrade added; on a
  database without these rows it is a clean no-op either way.
* **What the reads offer afterwards**: offerable-versions offers the new monthly version instead of
  the old one, and the yearly one as its own cadence; the customer read lists both without amounts;
  the "newest public flat version" reads (`get_public_flat_plan`, `all_public_flat_plans`,
  `current_public_versions`) land on the new **monthly** price.

Like `test_engage_flat_plan.py`, the database tests drive the real migration: `platform_session`
truncates the definitional tables first, then each test steps back under the flat-plan seeds and up
to the revision production was on (`b7e4d2a9c613`), so it starts from exactly the eleven v1 plans.
Alembic's own `asyncio.run()` cannot nest inside pytest-asyncio's loop, so each alembic call runs on
a worker thread; the database is left at `head` whether a test passes or fails.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app import selectors, services
from app.config import settings
from app.main import app
from app.models import SCHEMA, Plan
from app.schemas import FeatureIn, PlanCreateIn, PriceIn, SeatClassIn, VersionCreateIn
from platform_core.auth import Principal, get_principal
from platform_core.internal_auth import mint_service_token
from tests.helpers import customer_principal, operator_principal

_ROOT = Path(__file__).resolve().parent.parent
_VERSIONS = _ROOT / "migrations" / "versions"
ALLOW = "OPSGRIDS_ALLOW_LIST_PRICE_DOWNGRADE"


def _load(filename: str, name: str) -> ModuleType:
    """A migration imported by path — a migrations directory is not a package, and the assertions
    are on each migration's own ids and amounts, not a restatement of them."""
    spec = importlib.util.spec_from_file_location(name, _VERSIONS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LP = _load("b88a833020dc_list_prices_2026_10_06.py", "list_prices_2026_10_06")
INVENTORY = _load("e1f2a3b4c5d6_inventory_flat_plan.py", "seed_inventory_flat_plan")
FLAT = _load("10e6b62d9ac7_product_flat_plans.py", "seed_product_flat_plans")
ENGAGE = _load("53f1a7a8b99d_engage_flat_plan.py", "seed_engage_flat_plan")

#: The revision production was on before this one.
BEFORE: str = LP.down_revision
#: The revision under every flat-plan seed: stepping down to it and back up re-seeds all eleven.
UNDER_SEEDS: str = INVENTORY.down_revision

#: The owner's decision of 2026-10-06 — "Yearly price would be less that would be 300000 and
#: monthly 30000", split "Proportional to today's prices" — in paise: (product_key, code) ->
#: (month, year). Both totals are the owner's numbers; the year is ten months' price.
OWNER_DECISION: dict[tuple[str, str], tuple[int, int]] = {
    ("crm", "revenue"): (300000, 3000000),
    ("hrms", "people"): (300000, 3000000),
    ("itsm", "it"): (300000, 3000000),
    ("finance", "finance"): (375000, 3750000),
    ("operations", "inventory"): (375000, 3750000),
    ("engage", "engage"): (225000, 2250000),
    ("marketing", "marketing"): (225000, 2250000),
    ("projects", "projects"): (225000, 2250000),
    ("pulseboard", "pulseboard"): (225000, 2250000),
    ("storefront", "storefront"): (225000, 2250000),
    ("support", "support"): (225000, 2250000),
}


def _seeded_prices() -> dict[tuple[str, str], int]:
    """What each public flat plan cost until 2026-10-06, from the seeds themselves."""
    seeded = {("operations", "inventory"): INVENTORY.AMOUNT_MINOR}
    for product_key, code, _name, amount_minor, _sort in (*FLAT.PRODUCTS, *ENGAGE.PRODUCTS):
        seeded[(product_key, code)] = amount_minor
    return seeded


# ================================================================== 1. the numbers (no database)


class TestTheNumbers:
    def test_the_table_is_the_owners_decision_exactly(self) -> None:
        assert {(k, c): (m, y) for k, c, m, y in LP.NEW_PRICES} == OWNER_DECISION
        assert len(LP.NEW_PRICES) == 11

    def test_every_amount_is_integer_paise(self) -> None:
        for _key, _code, monthly, yearly in LP.NEW_PRICES:
            assert type(monthly) is int and type(yearly) is int, "money is integer minor units"
            assert monthly > 0 and yearly > 0

    def test_the_monthly_prices_sum_to_exactly_thirty_thousand_rupees(self) -> None:
        assert sum(m for _k, _c, m, _y in LP.NEW_PRICES) == 3_000_000
        assert LP.MONTHLY_TOTAL_MINOR == 3_000_000

    def test_the_yearly_prices_sum_to_exactly_three_lakh_rupees(self) -> None:
        assert sum(y for _k, _c, _m, y in LP.NEW_PRICES) == 30_000_000
        assert LP.YEARLY_TOTAL_MINOR == 30_000_000

    def test_a_year_costs_ten_months(self) -> None:
        """The owner set both numbers and made the year the cheaper option: INR 3,00,000 a year
        against INR 30,000 a month, so a year is two months free. The monthly price is not a
        twelfth of the yearly one (that reading, INR 25,000 a month, the owner rejected)."""
        for product_key, _code, monthly, yearly in LP.NEW_PRICES:
            assert yearly == 10 * monthly, product_key
            assert yearly < 12 * monthly, product_key
        assert LP.YEARLY_TOTAL_MINOR == 10 * LP.MONTHLY_TOTAL_MINOR

    def test_it_reprices_exactly_the_eleven_public_flat_plans_the_seeds_made(self) -> None:
        assert {(k, c) for k, c, _m, _y in LP.NEW_PRICES} == set(_seeded_prices())

    def test_the_split_is_proportional_to_what_each_product_cost_before(self) -> None:
        seeded = _seeded_prices()
        old_total = sum(seeded.values())
        assert old_total == 1_998_900, "the INR 19,989 the owner divided from"
        new = {(k, c): m for k, c, m, _y in LP.NEW_PRICES}
        for plan, old in seeded.items():
            exact_share = old * LP.MONTHLY_TOTAL_MINOR  # / old_total, kept in integers
            # Within 0.1% of the exact proportional share: rounded to whole rupees that still sum
            # to INR 30,000 a month (and so, ten times each, to INR 3,00,000 a year).
            assert abs(new[plan] * old_total - exact_share) * 1000 <= exact_share, plan
        # The same old price gives the same new one, and the order is kept.
        for a, b in ((a, b) for a in seeded for b in seeded):
            if seeded[a] == seeded[b]:
                assert new[a] == new[b], (a, b)
            elif seeded[a] < seeded[b]:
                assert new[a] < new[b], (a, b)

    def test_ids_are_deterministic_and_never_another_migrations(self) -> None:
        again = _load("b88a833020dc_list_prices_2026_10_06.py", "list_prices_again")
        assert again.VERSION_IDS == LP.VERSION_IDS
        ours = set(LP.VERSION_IDS.values())
        assert len(ours) == 22
        assert {LP.price_id(k, cad) for k, *_ in LP.NEW_PRICES for cad in LP.CADENCES}.isdisjoint(
            ours
        )
        theirs = {INVENTORY.VERSION, INVENTORY.PRICE}
        for ids in (*FLAT.IDS.values(), *ENGAGE.IDS.values()):
            theirs.update(ids)
        assert ours.isdisjoint(theirs)

    def test_the_monthly_version_is_numbered_last(self) -> None:
        """So every "newest public flat version" read stays on the monthly price."""
        assert LP.CADENCES == ("year", "month")


# ================================================================== helpers for the database tests


def _alembic_config() -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    return cfg


async def _alembic(fn: Any, *args: Any) -> None:
    """One alembic command off the event loop (`migrations/env.py` calls `asyncio.run()`)."""
    await asyncio.to_thread(fn, _alembic_config(), *args)


async def _query(sql: str, **params: Any) -> list[Any]:
    engine = create_async_engine(
        settings().admin_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params)
            return list(result.all()) if result.returns_rows else []
    finally:
        await engine.dispose()


async def _current() -> str:
    (row,) = await _query("SELECT version_num FROM catalog.alembic_version")
    return str(row[0])


#: The definitional tables, in the order `_snapshot` reports them, each with its whole-row read.
_DEFINITIONAL = ("plans", "plan_versions", "prices", "features", "seat_classes")
_READ_ALL = {
    "plans": "SELECT * FROM catalog.plans",
    "plan_versions": "SELECT * FROM catalog.plan_versions",
    "prices": "SELECT * FROM catalog.prices",
    "features": "SELECT * FROM catalog.features",
    "seat_classes": "SELECT * FROM catalog.seat_classes",
}


async def _snapshot() -> dict[str, list[tuple[str, ...]]]:
    """Every definitional row, whole and as text (a JSONB value is not hashable) — what
    "unchanged" means."""
    return {
        table: sorted(tuple(str(col) for col in row) for row in await _query(_READ_ALL[table]))
        for table in _DEFINITIONAL
    }


async def _clear() -> None:
    """Empty the definitional tables the way the suite's fixture does (TRUNCATE fires no row
    trigger), so a test that left a sheet the migration refuses cannot fail the next upgrade."""
    tables = ", ".join(f"catalog.{t}" for t in _DEFINITIONAL)
    await _query(f"TRUNCATE {tables} CASCADE")


async def _versions(product_key: str, code: str) -> list[dict[str, Any]]:
    rows = await _query(
        """
        SELECT v.id, v.version_no, v.state, v.billing_interval, v.currency,
               v.restricted_to_org_id, v.published_at
        FROM catalog.plan_versions v JOIN catalog.plans p ON p.id = v.plan_id
        WHERE p.product_key = :key AND p.code = :code
        ORDER BY v.version_no
        """,
        key=product_key,
        code=code,
    )
    return [
        {
            "id": str(r[0]),
            "version_no": r[1],
            "state": r[2],
            "billing_interval": r[3],
            "currency": r[4],
            "restricted_to_org_id": r[5],
            "published_at": r[6],
        }
        for r in rows
    ]


async def _prices(version_id: str) -> list[tuple[Any, ...]]:
    rows = await _query(
        """
        SELECT id, kind, amount_minor, currency, unit, unit_price_micros, meter_key
        FROM catalog.prices WHERE plan_version_id = CAST(:v AS uuid)
        """,
        v=version_id,
    )
    return [(str(r[0]), *r[1:]) for r in rows]


async def _features(version_id: str) -> dict[str, Any]:
    rows = await _query(
        "SELECT feature_key, value FROM catalog.features WHERE plan_version_id = CAST(:v AS uuid)",
        v=version_id,
    )
    return {str(k): v for k, v in rows}


async def _seat_classes(version_id: str) -> list[tuple[Any, ...]]:
    return [
        tuple(r)
        for r in await _query(
            """
            SELECT code, name, price_id, included_count, can_write, sort_order, description
            FROM catalog.seat_classes WHERE plan_version_id = CAST(:v AS uuid) ORDER BY code
            """,
            v=version_id,
        )
    ]


async def _ours_present() -> int:
    (row,) = await _query(
        "SELECT count(*) FROM catalog.plan_versions WHERE id = ANY(CAST(:ids AS uuid[]))",
        ids=list(LP.VERSION_IDS.values()),
    )
    return int(row[0])


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _internal(caller: str = "console") -> dict[str, str]:
    return {"X-Internal-Token": mint_service_token(settings(), audience="catalog", caller=caller)}


def _trial_offer(plans: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The dashboard Marketplace's `trialOffer`, line for line: in the first plan with a published
    flat-priced version, the newest public monthly one (else the newest such version)."""
    for plan in plans:
        flat = [
            v
            for v in plan["versions"]
            if v["state"] == "published" and any(p["kind"] == "flat" for p in v["prices"])
        ]
        if not flat:
            continue
        monthly = [
            v
            for v in flat
            if v["billing_interval"] == "month" and v["restricted_to_org_id"] is None
        ]
        return max(monthly or flat, key=lambda v: v["version_no"])
    return None


@pytest_asyncio.fixture
async def yesterday(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """The catalog as production had it on the morning of 2026-10-06: the eleven flat plans at
    version 1, at the revision before this one. `client` has already truncated the definitional
    tables; stepping under the seeds and back up re-seeds exactly the eleven. Leaves the database
    at head afterwards, whatever the test did."""
    monkeypatch.setenv(ALLOW, "1")
    await _alembic(command.downgrade, UNDER_SEEDS)
    monkeypatch.delenv(ALLOW)
    await _alembic(command.upgrade, BEFORE)
    try:
        yield client
    finally:
        await _alembic(command.upgrade, "head")


async def _plan(session: AsyncSession, product_key: str, code: str) -> Plan:
    async with session.begin():
        plan = (
            await session.execute(
                select(Plan).where(Plan.product_key == product_key, Plan.code == code)
            )
        ).scalar_one()
    return plan


async def _publish(session: AsyncSession, plan: Plan, payload: VersionCreateIn) -> str:
    """An operator publishing a version, the way the Plans page does."""
    op = operator_principal()
    async with session.begin():
        version = await services.create_version(session, op, plan, payload, request=None)
        await services.publish_version(session, op, version, request=None)
        return str(version.id)


# ================================================================== 2. the migration


@pytest.mark.integration
class TestTheMigration:
    async def test_every_flat_plan_gains_a_yearly_then_a_monthly_version_at_the_new_price(
        self, yesterday: AsyncClient
    ) -> None:
        before = await _snapshot()
        assert await _ours_present() == 0

        await _alembic(command.upgrade, LP.revision)

        for product_key, code, monthly, yearly in LP.NEW_PRICES:
            versions = await _versions(product_key, code)
            assert [v["version_no"] for v in versions] == [1, 2, 3], product_key
            old, year, month = versions
            assert old["state"] == "published", "the version in force before is left as it was"
            for version, cadence, amount in ((year, "year", yearly), (month, "month", monthly)):
                assert version["id"] == LP.VERSION_IDS[(product_key, cadence)]
                assert version["state"] == "published"
                assert version["published_at"] is not None
                assert version["billing_interval"] == cadence
                assert version["currency"] == "INR"
                assert version["restricted_to_org_id"] is None, "a public version"
                assert await _prices(version["id"]) == [
                    (LP.price_id(product_key, cadence), "flat", amount, "INR", None, None, None)
                ], f"{product_key} {cadence}: one flat INR price at exactly {amount} paise"
                # The seeds declare no features or seat classes on a flat plan; neither does this.
                assert await _features(version["id"]) == {}
                assert await _seat_classes(version["id"]) == []

        after = await _snapshot()
        for table, rows in before.items():
            assert set(rows) <= set(after[table]), f"a row in {table} changed or vanished"
        assert len(after["plan_versions"]) == len(before["plan_versions"]) + 22
        assert len(after["prices"]) == len(before["prices"]) + 22
        assert after["plans"] == before["plans"], "no plan is created or edited"

    async def test_the_sheet_is_cloned_from_the_newest_public_version_and_nothing_else(
        self, yesterday: AsyncClient, platform_session: AsyncSession, org_a: uuid.UUID
    ) -> None:
        revenue = await _plan(platform_session, "crm", "revenue")
        # The owner published a v2 on the Plans page: a trial length, a limit, a free viewer seat.
        await _publish(
            platform_session,
            revenue,
            VersionCreateIn(
                currency="INR",
                prices=[PriceIn(kind="flat", amount_minor=199900, currency="INR")],
                features=[
                    FeatureIn(feature_key="trial_days", value=7),
                    FeatureIn(feature_key="limits", value={"contacts": 5000, "pipelines": [1, 2]}),
                ],
                seat_classes=[
                    SeatClassIn(
                        code="viewer",
                        name="Viewer",
                        can_write=False,
                        sort_order=5,
                        description="Read-only",
                    )
                ],
            ),
        )
        # Then a v3 negotiated for one customer — a newer number, but never a clone source.
        bespoke = await _publish(
            platform_session,
            revenue,
            VersionCreateIn(
                currency="INR",
                restricted_to_org_id=org_a,
                prices=[PriceIn(kind="flat", amount_minor=150000, currency="INR")],
                features=[FeatureIn(feature_key="trial_days", value=30)],
            ),
        )
        # A per-seat plan on a product that also sells a flat one: not repriced.
        async with platform_session.begin():
            starter = await services.create_plan(
                platform_session,
                operator_principal(),
                PlanCreateIn(product_key="hrms", code="starter", name="People Starter"),
                request=None,
            )
        await _publish(
            platform_session,
            starter,
            VersionCreateIn(
                currency="INR",
                prices=[PriceIn(kind="per_seat", amount_minor=19900, currency="INR", unit="seat")],
            ),
        )
        bespoke_before = (await _prices(bespoke), await _features(bespoke))

        await _alembic(command.upgrade, LP.revision)

        versions = await _versions("crm", "revenue")
        assert [(v["version_no"], v["restricted_to_org_id"]) for v in versions] == [
            (1, None),
            (2, None),
            (3, org_a),
            (4, None),
            (5, None),
        ], "the version numbers are one sequence across audiences; ours follow the bespoke one"
        for version in versions[3:]:
            assert await _features(version["id"]) == {
                "trial_days": 7,
                "limits": {"contacts": 5000, "pipelines": [1, 2]},
            }, "cloned from the newest public version, values exactly as stored"
            assert await _seat_classes(version["id"]) == [
                ("viewer", "Viewer", None, None, False, 5, "Read-only")
            ]
        assert (await _prices(bespoke), await _features(bespoke)) == bespoke_before
        assert [v["version_no"] for v in await _versions("hrms", "starter")] == [1]

    async def test_a_second_run_inserts_nothing_and_changes_nothing(
        self, yesterday: AsyncClient
    ) -> None:
        await _alembic(command.upgrade, LP.revision)
        first = await _snapshot()

        await _alembic(command.stamp, BEFORE)  # forget it ran, so upgrade really runs it again
        await _alembic(command.upgrade, LP.revision)

        assert await _snapshot() == first
        assert await _current() == LP.revision

    @pytest.mark.parametrize(
        "sheet",
        [
            {
                "currency": "INR",
                "prices": [
                    PriceIn(kind="flat", amount_minor=249900, currency="INR"),
                    PriceIn(kind="per_seat", amount_minor=9900, currency="INR", unit="seat"),
                ],
            },
            {
                "currency": "USD",
                "prices": [PriceIn(kind="flat", amount_minor=2999, currency="USD")],
            },
        ],
        ids=["a-per-seat-component", "not-inr"],
    )
    async def test_a_sheet_one_flat_price_cannot_replace_stops_the_whole_migration(
        self, yesterday: AsyncClient, platform_session: AsyncSession, sheet: dict[str, Any]
    ) -> None:
        finance = await _plan(platform_session, "finance", "finance")
        await _publish(platform_session, finance, VersionCreateIn(**sheet))
        before = await _snapshot()
        try:
            # Alembic loads the migration under its own module name, so the class is matched by
            # name rather than by identity with `LP.SourceShapeError`.
            with pytest.raises(RuntimeError, match="finance: the current public version") as err:
                await _alembic(command.upgrade, LP.revision)
            assert type(err.value).__name__ == "SourceShapeError"

            assert await _current() == BEFORE
            assert await _ours_present() == 0, (
                "one refusal rolls back every product, not just this one"
            )
            assert await _snapshot() == before
        finally:
            await _clear()  # or the fixture's `upgrade head` would be refused the same way

    async def test_an_open_public_draft_stops_the_whole_migration_and_is_named(
        self, yesterday: AsyncClient, platform_session: AsyncSession, org_a: uuid.UUID
    ) -> None:
        """The owner left a re-price of Revenue as a draft (v2) on the Plans page. Numbered above
        it, the new versions would supersede it the moment it was published — it would be live on
        paper and never sell. So nothing is applied, and the error says which draft to resolve."""
        op = operator_principal()
        revenue = await _plan(platform_session, "crm", "revenue")
        support = await _plan(platform_session, "support", "support")
        finance = await _plan(platform_session, "finance", "finance")
        async with platform_session.begin():
            public_draft = await services.create_version(
                platform_session,
                op,
                revenue,
                VersionCreateIn(
                    currency="INR",
                    prices=[PriceIn(kind="flat", amount_minor=260000, currency="INR")],
                ),
                request=None,
            )
            other_draft = await services.create_version(
                platform_session,
                op,
                support,
                VersionCreateIn(
                    currency="INR",
                    prices=[PriceIn(kind="flat", amount_minor=99000, currency="INR")],
                ),
                request=None,
            )
            draft_ids = (public_draft.id, other_draft.id)
            # A draft negotiated for one organization is its own audience: never in the way.
            bespoke_draft = await services.create_version(
                platform_session,
                op,
                finance,
                VersionCreateIn(
                    currency="INR",
                    restricted_to_org_id=org_a,
                    prices=[PriceIn(kind="flat", amount_minor=200000, currency="INR")],
                ),
                request=None,
            )
            bespoke_id = bespoke_draft.id
        before = await _snapshot()

        with pytest.raises(RuntimeError, match="nothing applied") as err:
            await _alembic(command.upgrade, LP.revision)
        assert type(err.value).__name__ == "OpenDraftError"
        assert "crm/revenue v2" in str(err.value) and "support/support v2" in str(err.value)
        assert "finance" not in str(err.value), "a bespoke draft is not in the way"
        assert await _current() == BEFORE
        assert await _ours_present() == 0
        assert await _snapshot() == before

        # Resolved the way the error says — the drafts deleted — it applies.
        async with platform_session.begin():
            for version_id in draft_ids:
                version = await services.require_version_for_update(platform_session, version_id)
                await services.delete_draft_version(platform_session, op, version, request=None)
        await _alembic(command.upgrade, LP.revision)
        assert await _ours_present() == 22

        # The bespoke draft, numbered below the new public versions, is still its own audience's
        # newest: publishing it takes effect.
        async with platform_session.begin():
            version = await services.require_version_for_update(platform_session, bespoke_id)
            published = await services.publish_version(platform_session, op, version, request=None)
            assert (published.version_no, published.state) == (2, "published")

    async def test_a_source_at_another_price_than_the_split_assumed_is_applied_and_warned(
        self,
        yesterday: AsyncClient,
        platform_session: AsyncSession,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        engage = await _plan(platform_session, "engage", "engage")
        await _publish(
            platform_session,
            engage,
            VersionCreateIn(
                currency="INR", prices=[PriceIn(kind="flat", amount_minor=99900, currency="INR")]
            ),
        )
        capsys.readouterr()
        await _alembic(command.upgrade, LP.revision)
        assert await _ours_present() == 22
        err = capsys.readouterr().err
        assert "engage sold at 99900 paise, not the 149900 paise" in err, err

    async def test_seeded_minor_is_what_the_seeds_sold(self) -> None:
        assert {k: LP.SEEDED_MINOR[k] for k, _c, _m, _y in LP.NEW_PRICES} == {
            k: amount for (k, _c), amount in _seeded_prices().items()
        }

    async def test_retired_withdrawn_and_missing_plans_are_skipped(
        self, yesterday: AsyncClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await _query("UPDATE catalog.plans SET state = 'retired' WHERE code = 'pulseboard'")
        await _query(
            """
            UPDATE catalog.plan_versions SET state = 'grandfathered'
            WHERE plan_id = (SELECT id FROM catalog.plans WHERE code = 'support')
            """
        )
        await _query("UPDATE catalog.plans SET code = 'storefront-old' WHERE code = 'storefront'")

        capsys.readouterr()
        await _alembic(command.upgrade, LP.revision)

        # Each skip is in the migrate output — the RUNBOOK tells the operator to read it there.
        err = capsys.readouterr().err
        assert "plan pulseboard/pulseboard is retired; skipped" in err, err
        assert "plan support/support has no published public version; skipped" in err, err
        assert "no plan storefront/storefront; skipped" in err, err

        assert [v["version_no"] for v in await _versions("pulseboard", "pulseboard")] == [1]
        support = await _versions("support", "support")
        assert [(v["version_no"], v["state"]) for v in support] == [(1, "grandfathered")], (
            "withdrawn on purpose: publishing here would put it back on sale"
        )
        assert [v["version_no"] for v in await _versions("storefront", "storefront-old")] == [1]
        assert await _ours_present() == 16, "the other eight plans, two versions each"


# ================================================================== 3. the downgrade


@pytest.mark.integration
class TestTheDowngrade:
    async def test_it_refuses_without_the_override_and_reverses_exactly_with_it(
        self, yesterday: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = await _snapshot()
        await _alembic(command.upgrade, LP.revision)

        for value in (None, "0", "true", "yes", ""):
            if value is None:
                monkeypatch.delenv(ALLOW, raising=False)
            else:
                monkeypatch.setenv(ALLOW, value)
            with pytest.raises(RuntimeError, match=ALLOW):
                await _alembic(command.downgrade, BEFORE)
            assert await _current() == LP.revision, value
            assert await _ours_present() == 22, value

        # The shared switch is not this one: `tools/roundtrip.sh` passes only this migration's own,
        # so the shared one keeps guarding operations' real lots and rolls on a roundtrip.
        monkeypatch.delenv(ALLOW, raising=False)
        monkeypatch.setenv("OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE", "1")
        with pytest.raises(RuntimeError, match=ALLOW):
            await _alembic(command.downgrade, BEFORE)
        assert await _ours_present() == 22
        monkeypatch.delenv("OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE")
        roundtrip = (_ROOT.parent.parent / "tools" / "roundtrip.sh").read_text()
        assert f"{ALLOW}=1 uv run alembic downgrade base" in roundtrip
        assert "OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1 uv run" not in roundtrip

        monkeypatch.setenv(ALLOW, "1")
        await _alembic(command.downgrade, BEFORE)
        monkeypatch.delenv(ALLOW)

        assert await _current() == BEFORE
        assert await _snapshot() == before, "exactly the rows the upgrade added, and nothing else"
        enabled = await _query(
            """
            SELECT tgname, tgenabled::text FROM pg_trigger
            WHERE tgrelid IN ('catalog.prices'::regclass, 'catalog.features'::regclass,
                              'catalog.seat_classes'::regclass, 'catalog.plan_versions'::regclass)
              AND NOT tgisinternal
            """
        )
        assert enabled and all(state == "O" for _name, state in enabled), enabled

        # And it can be applied again.
        await _alembic(command.upgrade, LP.revision)
        assert await _ours_present() == 22

    async def test_a_database_without_these_rows_downgrades_cleanly_without_the_override(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`client` truncated the definitional tables: nothing of this migration's is left, so
        there is nothing a subscription could be pinned to and nothing to refuse."""
        monkeypatch.delenv(ALLOW, raising=False)
        await _alembic(command.upgrade, "head")
        assert await _ours_present() == 0
        try:
            await _alembic(command.downgrade, BEFORE)
            assert await _current() == BEFORE
        finally:
            await _alembic(command.upgrade, "head")
        assert await _current() == LP.revision
        assert await _ours_present() == 0, "with no plans to reprice, the upgrade adds nothing"


# ================================================================== 4. what the reads offer


@pytest.mark.integration
class TestWhatIsOfferedAfterwards:
    async def test_offerable_versions_offer_the_new_monthly_and_the_yearly_never_the_old(
        self,
        yesterday: AsyncClient,
        platform_session: AsyncSession,
        org_a: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        old = {(k, c): (await _versions(k, c))[0]["id"] for k, c, _m, _y in LP.NEW_PRICES}
        await _alembic(command.upgrade, LP.revision)

        for product_key, code, _monthly, _yearly in LP.NEW_PRICES:
            for org in (org_a, org_b):
                async with platform_session.begin():
                    offered = await selectors.offerable_versions(
                        platform_session, product_key=product_key, org_id=org
                    )
                by_cadence = {v.billing_interval: v for _plan, v in offered}
                assert set(by_cadence) == {"month", "year"}, product_key
                assert str(by_cadence["month"].id) == LP.VERSION_IDS[(product_key, "month")]
                assert str(by_cadence["year"].id) == LP.VERSION_IDS[(product_key, "year")]
                assert old[(product_key, code)] not in {str(v.id) for _p, v in offered}, (
                    "the old monthly version is superseded by the new one"
                )

        response = await yesterday.get(
            f"/internal/v1/organizations/{org_a}/offerable-versions",
            params={"product_key": "crm"},
            headers=_internal("console"),
        )
        assert response.status_code == 200, response.text
        rows = {
            r["billing_interval"]: (r["plan_version_id"], r["prices"][0]["amount_minor"])
            for r in response.json()["versions"]
        }
        assert rows == {
            "month": (LP.VERSION_IDS[("crm", "month")], 300000),
            "year": (LP.VERSION_IDS[("crm", "year")], 3000000),
        }
        as_subscription = await yesterday.get(
            f"/internal/v1/organizations/{org_a}/offerable-versions",
            params={"product_key": "crm"},
            headers=_internal("subscription"),
        )
        assert as_subscription.status_code == 200
        assert len(as_subscription.json()["versions"]) == 2
        # The negative case still holds: no token, no answer.
        refused = await yesterday.get(
            f"/internal/v1/organizations/{org_a}/offerable-versions",
            params={"product_key": "crm"},
        )
        assert refused.status_code == 401

    async def test_the_newest_public_flat_version_is_the_new_monthly_price(
        self, yesterday: AsyncClient, platform_session: AsyncSession
    ) -> None:
        await _alembic(command.upgrade, LP.revision)
        async with platform_session.begin():
            public = await selectors.all_public_flat_plans(platform_session)
            plan_ids = [plan.id for plan, _v in public]
            current = await selectors.current_public_versions(platform_session, plan_ids)
            single = {
                key: await selectors.get_public_flat_plan(platform_session, key)
                for key, *_rest in LP.NEW_PRICES
            }
        assert {
            plan.product_key: (str(v.id), v.billing_interval, v.prices[0].amount_minor)
            for plan, v in public
        } == {
            key: (LP.VERSION_IDS[(key, "month")], "month", monthly)
            for key, _code, monthly, _yearly in LP.NEW_PRICES
        }
        assert {str(v.id) for v in current.values()} == {
            LP.VERSION_IDS[(key, "month")] for key, *_rest in LP.NEW_PRICES
        }
        for key, found in single.items():
            assert found is not None
            assert str(found[1].id) == LP.VERSION_IDS[(key, "month")], key

    async def test_a_customer_sees_both_cadences_without_amounts_and_a_trial_pins_the_new_month(
        self, yesterday: AsyncClient, org_a: uuid.UUID
    ) -> None:
        await _alembic(command.upgrade, LP.revision)
        with as_principal(customer_principal(org_a)):
            response = await yesterday.get("/api/v1/plans", params={"product_key": "crm"})
        assert response.status_code == 200
        (revenue,) = response.json()
        assert [(v["version_no"], v["billing_interval"]) for v in revenue["versions"]] == [
            (1, "month"),
            (2, "year"),
            (3, "month"),
        ]
        for version in revenue["versions"]:
            assert "currency" not in version
            assert all("amount_minor" not in p for p in version["prices"]), "no list price"
        offer = _trial_offer(response.json())
        assert offer is not None and offer["id"] == LP.VERSION_IDS[("crm", "month")], (
            "Start trial pins the new monthly price, not the old one"
        )

        # Both new versions pin: the lookup subscription makes before it subscribes or switches.
        for cadence, amount in (("month", 300000), ("year", 3000000)):
            pin = await yesterday.get(
                f"/internal/v1/plan-versions/{LP.VERSION_IDS[('crm', cadence)]}",
                headers=_internal("subscription"),
            )
            assert pin.status_code == 200
            body = pin.json()
            assert (body["state"], body["billing_interval"], body["currency"]) == (
                "published",
                cadence,
                "INR",
            )
            assert [(p["kind"], p["amount_minor"]) for p in body["prices"]] == [("flat", amount)]
