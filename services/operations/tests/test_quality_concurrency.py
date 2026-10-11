"""Quality decisions under real concurrency: every request gets its own connection and its own
transaction through the real ASGI app (the `concurrent_client` pattern of
`test_orders_accept_concurrency.py`), racing on one inspection or one pool of stock.

What is proven is the design in `app/quality/services.py`'s docstring: the inspection row is the
anchor lock, so two decisions queue; the loser re-reads the status the winner committed and gets a
409 (or, with the same Idempotency-Key and payload, the winner's own result); and the kernel's
bucket locks and `CHECK`s mean stock can neither be posted twice nor driven negative.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.inventory.reconcile import reconcile_organization
from app.main import app
from app.models import SCHEMA, Inspection, InventoryMove
from platform_core.db import get_session
from tests.helpers import as_principal, principal_for
from tests.inventory_fixtures import balance, ledger_total, q
from tests.quality_fixtures import ALL_PERMS, BASE, QcShop, build_qc_shop, open_ok

pytestmark = pytest.mark.integration


@pytest.fixture
async def qc(session, org_id) -> QcShop:
    return await build_qc_shop(session, org_id, stock=100)


@pytest.fixture
async def concurrent_client(org_id):  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        settings().app_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        pool_size=20,
        max_overflow=0,
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session_override():  # type: ignore[no-untyped-def]
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _session_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_session, None)
    await engine.dispose()


async def race(  # type: ignore[no-untyped-def]
    client, org_id: uuid.UUID, *requests: tuple[str, str, Any, str | None]
) -> list[Any]:
    """Fire every `(method, path, json, key)` at once as one principal holding every permission."""
    principal = principal_for(org_id, ALL_PERMS, subject_ref=uuid.uuid4())
    with as_principal(principal):
        return list(
            await asyncio.gather(
                *(
                    client.request(
                        method,
                        f"{BASE}{path}",
                        json=body,
                        headers={"Idempotency-Key": key} if key else {},
                    )
                    for method, path, body, key in requests
                )
            )
        )


async def moves(session) -> int:  # type: ignore[no-untyped-def]
    async with session.begin():
        return int(
            (await session.execute(select(func.count()).select_from(InventoryMove))).scalar_one()
        )


async def assert_clean(session, qc: QcShop) -> None:  # type: ignore[no-untyped-def]
    async with session.begin():
        report = await reconcile_organization(session, qc.org_id)
    assert report.clean, report.findings


async def test_release_and_reject_at_once_exactly_one_wins(
    concurrent_client, session, qc: QcShop
) -> None:
    insp = await open_ok(concurrent_client, qc, quantity="40")
    path = f"/inspections/{insp['id']}"
    release, reject = await race(
        concurrent_client,
        qc.org_id,
        ("POST", f"{path}/release", {"note": "ok"}, None),
        ("POST", f"{path}/reject", {"reason": "bad"}, None),
    )
    assert sorted([release.status_code, reject.status_code]) == [200, 409], (
        release.text,
        reject.text,
    )
    winner = "release" if release.status_code == 200 else "reject"
    available = (await balance(session, qc.org_id, qc.shop.widget, qc.location_id))[0]
    rejected = (
        await balance(session, qc.org_id, qc.shop.widget, qc.location_id, status="REJECTED")
    )[0]
    held = (
        await balance(session, qc.org_id, qc.shop.widget, qc.location_id, status="QUALITY_HOLD")
    )[0]
    assert held == 0
    if winner == "release":
        assert (available, rejected) == (q("100"), 0)
    else:
        assert (available, rejected) == (q("60"), q("40"))
    assert await ledger_total(session, qc.org_id, qc.shop.widget) == q("100")
    async with session.begin():
        row = (await session.execute(select(Inspection))).scalar_one()
    assert row.status == ("PASSED" if winner == "release" else "FAILED")
    await assert_clean(session, qc)


async def test_two_different_partial_releases_one_wins_and_stock_is_not_double_posted(
    concurrent_client, session, qc: QcShop
) -> None:
    insp = await open_ok(concurrent_client, qc, quantity="40")
    path = f"/inspections/{insp['id']}/release"
    a, b = await race(
        concurrent_client,
        qc.org_id,
        ("POST", path, {"accepted": "10", "note": "n"}, None),
        ("POST", path, {"accepted": "20", "note": "n"}, None),
    )
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    accepted = 10 if a.status_code == 200 else 20
    got = (await balance(session, qc.org_id, qc.shop.widget, qc.location_id))[0]
    assert got == q("60") + q(str(accepted))
    assert (await balance(session, qc.org_id, qc.shop.widget, qc.location_id, status="REJECTED"))[
        0
    ] == q(str(40 - accepted))
    assert await ledger_total(session, qc.org_id, qc.shop.widget) == q("100")
    await assert_clean(session, qc)


async def test_ten_identical_keyed_releases_all_answer_one_result_and_post_once(
    concurrent_client, session, qc: QcShop
) -> None:
    insp = await open_ok(concurrent_client, qc, quantity="40")
    before = await moves(session)
    path = f"/inspections/{insp['id']}/release"
    responses = await race(
        concurrent_client, qc.org_id, *[("POST", path, {"note": "ok"}, "same-key")] * 10
    )
    assert [r.status_code for r in responses] == [200] * 10, [r.text for r in responses]
    assert len({r.json()["decision_move_id"] for r in responses}) == 1
    assert await moves(session) == before + 1
    assert (await balance(session, qc.org_id, qc.shop.widget, qc.location_id))[0] == q("100")
    await assert_clean(session, qc)


async def test_concurrent_opens_with_one_key_hold_the_stock_once(
    concurrent_client, session, qc: QcShop
) -> None:
    body = {
        "kind": "incoming",
        "item_id": str(qc.shop.widget),
        "location_id": str(qc.location_id),
        "quantity": "30",
    }
    responses = await race(
        concurrent_client, qc.org_id, *[("POST", "/inspections", body, "open-key")] * 8
    )
    assert [r.status_code for r in responses] == [201] * 8, [r.text for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1
    assert (
        await balance(session, qc.org_id, qc.shop.widget, qc.location_id, status="QUALITY_HOLD")
    )[0] == q("30")
    assert (await balance(session, qc.org_id, qc.shop.widget, qc.location_id))[0] == q("70")
    await assert_clean(session, qc)


async def test_inspections_racing_for_the_same_stock_cannot_oversubscribe_it(
    concurrent_client, session, qc: QcShop
) -> None:
    def body(quantity: str) -> dict[str, str]:
        return {
            "kind": "incoming",
            "item_id": str(qc.shop.widget),
            "location_id": str(qc.location_id),
            "quantity": quantity,
        }

    responses = await race(
        concurrent_client,
        qc.org_id,
        *[("POST", "/inspections", body("60"), None) for _ in range(5)],
    )
    codes = sorted(r.status_code for r in responses)
    assert codes == [201, 409, 409, 409, 409], [r.text for r in responses]
    held = (
        await balance(session, qc.org_id, qc.shop.widget, qc.location_id, status="QUALITY_HOLD")
    )[0]
    assert held == q("60")
    assert (await balance(session, qc.org_id, qc.shop.widget, qc.location_id))[0] == q("40")
    async with session.begin():
        count = (await session.execute(select(func.count()).select_from(Inspection))).scalar_one()
    assert count == 1  # the refused ones rolled back entirely
    await assert_clean(session, qc)


async def test_two_dispositions_at_once_dispose_the_stock_once(
    concurrent_client, session, qc: QcShop
) -> None:
    insp = await open_ok(concurrent_client, qc, quantity="40")
    base = f"/inspections/{insp['id']}"
    await race(concurrent_client, qc.org_id, ("POST", f"{base}/reject", {"reason": "r"}, None))
    path = f"{base}/disposition"
    a, b = await race(
        concurrent_client,
        qc.org_id,
        ("POST", path, {"action": "SCRAP"}, None),
        ("POST", path, {"action": "RETURN_TO_VENDOR"}, None),
    )
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    assert (await balance(session, qc.org_id, qc.shop.widget, qc.location_id, status="REJECTED"))[
        0
    ] == 0
    assert await ledger_total(session, qc.org_id, qc.shop.widget) == q("60")
    await assert_clean(session, qc)
