"""Row-level security, exercised as the app role — the isolation the whole platform rests on.

Seed a book into org A, then bind a fresh transaction to org B and confirm none of A's rows are
reachable: not through a list, not through a paid-amount rollup, and not by naming A's invoice id
directly. Run as the **app** role (NOBYPASSRLS), so a policy regression fails here rather than
passing silently under a bypassing connection.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import selectors
from app.config import settings
from app.models import SCHEMA, Invoice
from platform_core.db import TENANT_GUC
from tests.helpers import admin_ctx

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _session_bound_to(org: uuid.UUID):
    """A fresh app-role session bound to `org` on every transaction."""
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    s = maker()

    @event.listens_for(s.sync_session, "after_begin")
    def _bind(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

    return engine, s


class TestCrossTenantIsolation:
    async def test_org_b_cannot_see_org_a_book(self, session, org_id, book) -> None:
        """`session`/`org_id` is org A, seeded by `book`. Org B is a different tenant entirely."""
        org_a = org_id
        org_b = uuid.uuid4()

        # Sanity: org A really does have the book.
        ctx_a = admin_ctx(org_a)
        async with session.begin():
            a_rows = (await session.execute(selectors.invoices_stmt(ctx_a))).scalars().all()
        assert len(a_rows) == 21
        a_invoice_id = a_rows[0].id

        engine, s_b = await _session_bound_to(org_b)
        try:
            ctx_b = admin_ctx(org_b)
            async with s_b.begin():
                # 1) The list is empty for org B.
                b_rows = (await s_b.execute(selectors.invoices_stmt(ctx_b))).scalars().all()
                assert b_rows == []

                # 2) A raw count under RLS sees zero of A's rows.
                count = (await s_b.execute(select(func.count()).select_from(Invoice))).scalar_one()
                assert count == 0

                # 3) Naming A's invoice id directly resolves to nothing for org B.
                got = await selectors.get_invoice(s_b, org_b, a_invoice_id)
                assert got is None

                # 4) The paid-amount rollup returns nothing for A's ids under B's tenant.
                paid = await selectors.paid_by_invoice(s_b, org_b, [a_invoice_id])
                assert paid == {}
        finally:
            await s_b.close()
            await engine.dispose()

    async def test_the_suite_runs_as_a_nobypassrls_role(self, session) -> None:
        """A false-pass guard: if these tests ran as a bypassing role, every isolation assertion
        above would pass for the wrong reason."""
        async with session.begin():
            row = (
                await session.execute(
                    text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
                )
            ).scalar_one()
        assert row is False
