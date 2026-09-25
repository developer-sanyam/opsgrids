"""Engage can be bought: the marketplace read finds a published flat price (`53f1a7a8b99d`).

The production failure this guards: Engage was registered `available` in the product catalogue
but had no plan here, so `GET /plans?product_key=engage` came back empty. The dashboard's
marketplace card offers "Start a 14-day trial" only when that read resolves to a published flat
price (`flatPrice` in `frontend/opsgrids-dashboard/src/routes/Marketplace.tsx`), so no trial could
start, no subscription existed, no activation followed, and every Engage route answered the
gateway's 402 — "engage is not active for this organization".

So the assertion is made the way the marketplace makes it, not by reading tables: over HTTP,
through the customer route and its permission, as an ordinary organisation, applying the same
"first published version with a flat, priced line" rule the card applies. Then the version that
card would post to `POST /subscriptions` is resolved through the internal pin lookup subscription
makes before it pins, because a price the card can show but subscription cannot pin is the same
dead end one step later.

Like `test_product_flat_plans.py`, this drives the real migration rather than building a plan of
its own — `platform_session` truncates the definitional tables before every test, so only running
the migration says anything about what a deployed database ends up with. It first steps back to
the revision production was on and shows the failure there, then steps forward and shows the fix.
Alembic's own `asyncio.run()` cannot nest inside pytest-asyncio's loop, so each alembic call runs
on a worker thread; the database is left at `head` whether the test passes or fails.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.main import app
from app.models import SCHEMA
from platform_core.auth import Principal, get_principal
from platform_core.internal_auth import mint_service_token
from tests.helpers import customer_principal

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parent.parent
_PRODUCT_KEY = "engage"


def _seed_module() -> ModuleType:
    """The Engage plan migration, imported by path — a migrations directory is not a package, and
    the assertions are on the migration's own ids and amount, not a restatement of them."""
    path = _ROOT / "migrations" / "versions" / "53f1a7a8b99d_engage_flat_plan.py"
    spec = importlib.util.spec_from_file_location("seed_engage_flat_plan", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config() -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    return cfg


async def _alembic(fn: Any, cfg: Config, revision: str) -> None:
    """Run one alembic command off the event loop. `migrations/env.py` calls `asyncio.run()`,
    which raises inside a running loop and is fine on a thread of its own."""
    await asyncio.to_thread(fn, cfg, revision)


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


def _flat_price(plans: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The one offer a marketplace card shows: the first published version's flat price.

    The dashboard's `trialOffer`, line for line — a published version with a `flat` price. The
    customer read no longer says what it costs (pricing is set per client), only that the offer
    exists. If this returns None for a product, its card has no "Start trial" button, whatever
    the product catalogue says about its status.
    """
    for plan in plans:
        for version in plan["versions"]:
            if version["state"] != "published":
                continue
            for price in version["prices"]:
                if price["kind"] == "flat":
                    return version, price
    return None


async def _plan_count(plan_id: str) -> int:
    engine = create_async_engine(
        settings().admin_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM catalog.plans WHERE id = CAST(:id AS uuid)"),
                {"id": plan_id},
            )
            return result.scalar_one()
    finally:
        await engine.dispose()


async def test_engage_resolves_to_a_published_flat_price_a_trial_can_pin(
    client, org_a: uuid.UUID
) -> None:
    seed = _seed_module()
    cfg = _alembic_config()
    plan_id, version_id, price_id = seed.IDS[_PRODUCT_KEY]
    ((_key, code, name, amount_minor, _sort_order),) = seed.PRODUCTS

    # Before: the revision production was on. Engage is registered and sellable, and unpriced.
    await _alembic(command.downgrade, cfg, seed.down_revision)
    try:
        with as_principal(customer_principal(org_a)):
            before = await client.get("/api/v1/plans", params={"product_key": _PRODUCT_KEY})
        assert before.status_code == 200
        assert _flat_price(before.json()) is None, (
            "precondition: before this migration Engage has no price a marketplace card can offer"
        )

        # After: the same read, the same caller.
        await _alembic(command.upgrade, cfg, seed.revision)
        with as_principal(customer_principal(org_a)):
            after = await client.get("/api/v1/plans", params={"product_key": _PRODUCT_KEY})
        assert after.status_code == 200
        plans = after.json()

        assert [(p["product_key"], p["code"], p["name"], p["state"]) for p in plans] == [
            (_PRODUCT_KEY, code, name, "active")
        ], "Engage must list exactly its one seeded plan"
        assert plans[0]["id"] == plan_id

        offer = _flat_price(plans)
        assert offer is not None, "the marketplace must find a published flat price for Engage"
        version, price = offer
        assert version["id"] == version_id, "this id is what 'Start trial' posts as plan_version_id"
        assert version["billing_interval"] == "month"
        assert version["restricted_to_org_id"] is None, "a public price, visible to every org"
        assert price["kind"] == "flat"
        assert price["unit"] is None, "a flat price has no unit"
        # The customer read never carries what it costs: prices are set per client by staff.
        assert "currency" not in version
        assert "amount_minor" not in price
        assert "currency" not in price
        assert "unit_price_micros" not in price
        # The amount is still there for the services that need it, through the internal pin
        # lookup below: money is integer minor units (ADR-0000 §8).
        assert amount_minor > 0, "a zero price is not a placeholder, it is free"

        # The version the card posts must also pin. `POST /subscriptions` resolves it through
        # this lookup and refuses anything unpublished, restricted to another org, or belonging
        # to a different product (subscription `_validate_new_pin` and the product-key check).
        pin = await client.get(
            f"/internal/v1/plan-versions/{version_id}",
            headers={"X-Internal-Token": mint_service_token(settings(), audience="catalog")},
        )
        assert pin.status_code == 200
        pinned = pin.json()
        assert pinned["product_key"] == _PRODUCT_KEY
        assert pinned["state"] == "published"
        assert pinned["restricted_to_org_id"] is None
        pinned_prices = [
            (p["id"], p["kind"], p["amount_minor"], p["currency"]) for p in pinned["prices"]
        ]
        assert pinned_prices == [(price_id, "flat", amount_minor, "INR")], (
            "the subscription item references this price by id"
        )
    finally:
        await _alembic(command.downgrade, cfg, seed.down_revision)
        assert await _plan_count(plan_id) == 0, "downgrade must remove the Engage plan exactly"
        await _alembic(command.upgrade, cfg, "head")
