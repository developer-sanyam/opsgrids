"""P6 — the launch-status CLI, and the service layer underneath it.

`app.launch` replaces a hand-run `UPDATE product.products SET status = ...` with an operator
command that goes through `services.set_launch_status`: real refusals, a real audit row, and a
seam a test can call directly instead of shelling out to a subprocess for every case.

`products` is global catalogue data seeded by a migration and — like every other test in this
service — is deliberately never truncated between tests (`conftest.py`), so a test here cannot
assume `crm`'s starting status. Every test that needs a *real* transition primes it first with
a call whose own audit row is not the one under test, and counts by diffing audit row ids
rather than asserting an absolute count, so this file is not order-dependent on the rest of the
suite.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import launch, services
from app.config import settings
from app.models import PLATFORM_ORG, AuditLog, Product
from platform_core.db import dispose_engine, init_engine
from platform_core.errors import BusinessRuleProblem, NotFoundProblem

pytestmark = pytest.mark.integration

ACTION = "product.launch_status_set"


@pytest.fixture(autouse=True)
async def _process_engine() -> None:
    """`launch.main` writes through `session_scope`, which needs the process-wide engine
    `init_engine` sets up — the app lifespan does this in production, and each test here gets
    a fresh event loop (pytest-asyncio, function-scoped), so the engine from a previous test
    cannot simply be reused: its connections belong to a loop that is already closed. Same
    pattern as `services/approval/tests/test_backfill.py`.
    """
    init_engine(settings())
    yield
    await dispose_engine()


async def _product_status(session: AsyncSession, key: str) -> str:
    row = (await session.execute(select(Product).where(Product.key == key))).scalar_one()
    return str(row.status)


async def _audit_ids(session: AsyncSession) -> set[object]:
    rows = (
        (await session.execute(select(AuditLog.id).where(AuditLog.action == ACTION)))
        .scalars()
        .all()
    )
    return set(rows)


class TestSetLaunchStatus:
    async def test_promotes_a_known_product(self, platform_session: AsyncSession) -> None:
        async with platform_session.begin():
            # Prime a known, different starting state so this assertion holds regardless of
            # what an earlier test (in this file or another) left `crm` as.
            await services.set_launch_status(
                platform_session, product_key="crm", status="soon", acting_as="operator:cli"
            )
        async with platform_session.begin():
            product = await services.set_launch_status(
                platform_session, product_key="crm", status="available", acting_as="operator:cli"
            )
        assert product.status == "available"
        async with platform_session.begin():
            assert await _product_status(platform_session, "crm") == "available"

    async def test_writes_an_audit_row_against_the_platform_sentinel(
        self, platform_session: AsyncSession
    ) -> None:
        async with platform_session.begin():
            await services.set_launch_status(
                platform_session, product_key="hrms", status="soon", acting_as="operator:cli"
            )
            before = await _audit_ids(platform_session)

        async with platform_session.begin():
            await services.set_launch_status(
                platform_session, product_key="hrms", status="available", acting_as="operator:cli"
            )
            after = await _audit_ids(platform_session)
            new_ids = after - before
            assert len(new_ids) == 1
            row = (
                await platform_session.execute(
                    select(AuditLog).where(AuditLog.id == next(iter(new_ids)))
                )
            ).scalar_one()

        assert row.organization_id == PLATFORM_ORG
        assert row.acting_as == "operator:cli"
        assert row.after is not None
        assert row.after["status"] == "available"
        assert row.after["product_key"] == "hrms"

    async def test_a_no_op_flip_writes_no_audit_row(self, platform_session: AsyncSession) -> None:
        """Setting a product to the status it already holds changes nothing — no audit row for
        an act that did not happen."""
        async with platform_session.begin():
            await services.set_launch_status(
                platform_session, product_key="crm", status="available", acting_as="operator:cli"
            )
            before = await _audit_ids(platform_session)

        async with platform_session.begin():
            # `crm` is already "available" from the call just above: this one is a no-op.
            await services.set_launch_status(
                platform_session, product_key="crm", status="available", acting_as="operator:cli"
            )
            after = await _audit_ids(platform_session)

        assert after == before

    async def test_refuses_an_unknown_product_key(self, platform_session: AsyncSession) -> None:
        with pytest.raises(NotFoundProblem):
            async with platform_session.begin():
                await services.set_launch_status(
                    platform_session,
                    product_key="not-a-real-product",
                    status="available",
                    acting_as="operator:cli",
                )

    async def test_refuses_a_status_outside_the_launch_vocabulary(
        self, platform_session: AsyncSession
    ) -> None:
        """`beta` is a real value in the column (`Product.status`'s own comment names it), but
        nothing reconciles a product out of it, so the CLI does not let an operator set it."""
        with pytest.raises(BusinessRuleProblem):
            async with platform_session.begin():
                await services.set_launch_status(
                    platform_session, product_key="crm", status="beta", acting_as="operator:cli"
                )


class TestLaunchCli:
    async def test_argv_promotes_and_returns_zero(self, platform_session: AsyncSession) -> None:
        async with platform_session.begin():
            await services.set_launch_status(
                platform_session, product_key="crm", status="soon", acting_as="operator:cli"
            )

        code = await launch.main(["crm", "available"])

        assert code == 0
        async with platform_session.begin():
            assert await _product_status(platform_session, "crm") == "available"

    async def test_argv_refuses_an_unknown_key_without_raising(self) -> None:
        """The CLI's job is to report the refusal and exit non-zero, not to crash — an operator
        running this by hand should see a message, not a traceback."""
        code = await launch.main(["not-a-real-product", "available"])
        assert code == 1

    async def test_argv_rejects_a_status_outside_the_choices_at_the_parser(self) -> None:
        """argparse's own `choices=` refuses this before the service layer ever runs, which is
        why it raises `SystemExit` (argparse's usage-error exit) rather than returning 1."""
        with pytest.raises(SystemExit):
            await launch.main(["crm", "beta"])
