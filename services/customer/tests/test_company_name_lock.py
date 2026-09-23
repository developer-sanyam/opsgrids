"""M2: the database no longer refuses a *new* case-insensitive duplicate company name outright
(`uq_companies_org_name` stayed case-sensitive, on purpose — see its own comment in
`app/models.py`) — `services.create_company`'s `pg_advisory_xact_lock` is what "no new
case-insensitive duplicate" rests on now. Proven here with two real sessions racing to create
the same name in different cases, not a single session where nothing would ever actually
contend for the lock.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import selectors, services
from app.config import settings
from app.models import SCHEMA, Owner
from app.schemas import CompanyPatch
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from platform_core.errors import ConflictProblem
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def test_two_concurrent_case_variant_creates_only_one_succeeds(
    org_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Widens the natural check-then-insert race window with an artificial delay right after
    the name lookup — on localhost, two real transactions started via `asyncio.gather` are not
    reliably guaranteed to actually overlap inside that window otherwise, and the point of
    this test is that they *are* forced to. `services.create_company`'s advisory lock is what
    is supposed to close this window; without it, both branches would read "not found" and
    both would insert, since the database itself no longer refuses a case-insensitive
    duplicate outright (M2)."""
    original_lookup = selectors.get_company_by_name

    async def _slow_lookup(session, organization_id, name):  # type: ignore[no-untyped-def]
        result = await original_lookup(session, organization_id, name)
        await asyncio.sleep(0.1)
        return result

    monkeypatch.setattr(selectors, "get_company_by_name", _slow_lookup)

    cfg = settings()
    engine_a = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    engine_b = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
    maker_b = async_sessionmaker(engine_b, expire_on_commit=False)

    def _bind(org: uuid.UUID):
        def _listener(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

        return _listener

    ctx = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
    # Two *different* owners -- `create_company` also row-locks the owner it is assigning
    # (`_assert_owner_assignable`), and sharing one owner between the two concurrent creates
    # would serialize them on that lock regardless of whether the name lock under test does
    # anything at all, making the test pass even with the fix removed.
    owner_a, owner_b = uuid.uuid4(), uuid.uuid4()

    try:
        async with maker_a() as session_a, maker_b() as session_b:
            event.listen(session_a.sync_session, "after_begin", _bind(org_id))
            event.listen(session_b.sync_session, "after_begin", _bind(org_id))

            async with session_a.begin():
                for owner_id, email in (
                    (owner_a, "priya-lock-test@example.com"),
                    (owner_b, "arjun-lock-test@example.com"),
                ):
                    await session_a.execute(
                        Owner.__table__.insert().values(
                            id=owner_id,
                            organization_id=org_id,
                            full_name="Rep",
                            email=email,
                            role="",
                            is_active=True,
                        )
                    )

            async def _create(sess, name: str, owner_id: uuid.UUID):
                try:
                    async with sess.begin():
                        return await services.create_company(
                            sess, ctx, name=name, owner_id=owner_id
                        )
                except ConflictProblem as exc:
                    return exc

            result_a, result_b = await asyncio.gather(
                _create(session_a, "Riverside Traders", owner_a),
                _create(session_b, "RIVERSIDE TRADERS", owner_b),
            )
    finally:
        await engine_a.dispose()
        await engine_b.dispose()

    outcomes = [result_a, result_b]
    successes = [r for r in outcomes if not isinstance(r, ConflictProblem)]
    refusals = [r for r in outcomes if isinstance(r, ConflictProblem)]
    assert len(successes) == 1
    assert len(refusals) == 1


def _tenant_binder(org: uuid.UUID):
    def _listener(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

    return _listener


async def _sql_folds_equal(session, a: str, b: str) -> bool:  # type: ignore[no-untyped-def]
    """Whether *this database's* `lower(btrim(...))` treats the two spellings as one name —
    the only definition of "the same name" the lookup and the lock both share now."""
    async with session.begin():
        return bool(
            (
                await session.execute(
                    text("SELECT lower(btrim(CAST(:a AS text))) = lower(btrim(CAST(:b AS text)))"),
                    {"a": a, "b": b},
                )
            ).scalar_one()
        )


class TestCaseFoldingHappensInSql:
    """m5, fix round 3. Python's `str.lower()` and Postgres's `lower()` disagree on inputs like
    "İ" (two code points in Python, one in an en_US.utf8 database, unchanged in a C-locale
    one), so folding on either side of a comparison in different places lets two spellings the
    database treats as one name look different to the check — and hash to different advisory
    lock keys. Both now fold in SQL. The expected outcome below is derived from the database's
    own folding rather than hard-coded, so this holds under any locale the suite runs against.
    """

    async def test_the_lookup_agrees_with_the_databases_own_folding(
        self, session, org_id, data
    ) -> None:
        write = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
        async with session.begin():
            await services.create_company(
                session, write, name="istanbul ltd", owner_id=data["subject_a"]
            )

        equal = await _sql_folds_equal(session, "istanbul ltd", "İSTANBUL LTD")
        if equal:
            with pytest.raises(ConflictProblem):
                async with session.begin():
                    await services.create_company(
                        session, write, name="İSTANBUL LTD", owner_id=data["subject_a"]
                    )
        else:
            async with session.begin():
                created = await services.create_company(
                    session, write, name="İSTANBUL LTD", owner_id=data["subject_a"]
                )
            assert created.name == "İSTANBUL LTD"

    async def test_surrounding_whitespace_is_not_a_different_name(
        self, session, org_id, data
    ) -> None:
        write = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
        async with session.begin():
            await services.create_company(
                session, write, name="Padded Traders", owner_id=data["subject_a"]
            )
        with pytest.raises(ConflictProblem):
            async with session.begin():
                await services.create_company(
                    session, write, name="  padded traders  ", owner_id=data["subject_a"]
                )

    async def test_two_concurrent_creates_of_sql_equal_spellings_serialize_on_one_lock(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_lookup = selectors.get_company_by_name

        async def _slow_lookup(sess, organization_id, name):  # type: ignore[no-untyped-def]
            result = await original_lookup(sess, organization_id, name)
            await asyncio.sleep(0.1)
            return result

        monkeypatch.setattr(selectors, "get_company_by_name", _slow_lookup)
        equal = await _sql_folds_equal(session, "istanbul traders", "İSTANBUL TRADERS")

        cfg = settings()
        engine_a = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
        engine_b = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
        maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
        maker_b = async_sessionmaker(engine_b, expire_on_commit=False)
        ctx = ctx_for(org_id, "crm.book.company.write", Scope.ORG)

        async def _create(sess, name: str, owner: uuid.UUID):  # type: ignore[no-untyped-def]
            try:
                async with sess.begin():
                    return await services.create_company(sess, ctx, name=name, owner_id=owner)
            except ConflictProblem as exc:
                return exc

        try:
            async with maker_a() as session_a, maker_b() as session_b:
                event.listen(session_a.sync_session, "after_begin", _tenant_binder(org_id))
                event.listen(session_b.sync_session, "after_begin", _tenant_binder(org_id))
                results = await asyncio.gather(
                    _create(session_a, "istanbul traders", data["subject_a"]),
                    _create(session_b, "İSTANBUL TRADERS", data["subject_b"]),
                )
        finally:
            await engine_a.dispose()
            await engine_b.dispose()

        successes = [r for r in results if not isinstance(r, ConflictProblem)]
        assert len(successes) == (1 if equal else 2)


class TestLockOrderIsOwnerThenName:
    """m4, fix round 3. `update_company` used to take the name lock and *then* the owner's row
    lock; `create_company` takes them the other way round. A rename-plus-reassign racing a
    create naming the same owner and the same name each ended up holding the lock the other
    was waiting for — a deadlock Postgres breaks by killing one of them."""

    async def test_a_rename_with_reassign_racing_a_create_never_deadlocks(
        self, session, org_id, data, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_assert = services._assert_owner_assignable
        original_lock = services._lock_company_name

        async def _assert_then_linger(*args, **kwargs):  # type: ignore[no-untyped-def]
            await original_assert(*args, **kwargs)
            await asyncio.sleep(0.4)

        async def _lock_then_linger(*args, **kwargs):  # type: ignore[no-untyped-def]
            await original_lock(*args, **kwargs)
            await asyncio.sleep(0.4)

        monkeypatch.setattr(services, "_assert_owner_assignable", _assert_then_linger)
        monkeypatch.setattr(services, "_lock_company_name", _lock_then_linger)

        cfg = settings()
        engine_a = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
        engine_b = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
        maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
        maker_b = async_sessionmaker(engine_b, expire_on_commit=False)
        ctx = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
        shared_owner = data["subject_b"]

        async def _rename_and_reassign(sess):  # type: ignore[no-untyped-def]
            try:
                async with sess.begin():
                    company = await selectors.get_company(sess, org_id, data["company_a_id"])
                    assert company is not None
                    return await services.update_company(
                        sess,
                        ctx,
                        company,
                        CompanyPatch(name="Zeta Holdings", owner_id=shared_owner),
                    )
            except ConflictProblem as exc:
                return exc

        async def _create_same_name(sess):  # type: ignore[no-untyped-def]
            try:
                async with sess.begin():
                    return await services.create_company(
                        sess, ctx, name="Zeta Holdings", owner_id=shared_owner
                    )
            except ConflictProblem as exc:
                return exc

        try:
            async with maker_a() as session_a, maker_b() as session_b:
                event.listen(session_a.sync_session, "after_begin", _tenant_binder(org_id))
                event.listen(session_b.sync_session, "after_begin", _tenant_binder(org_id))
                results = await asyncio.gather(
                    _rename_and_reassign(session_a),
                    _create_same_name(session_b),
                    return_exceptions=True,
                )
        finally:
            await engine_a.dispose()
            await engine_b.dispose()

        # Neither was killed as a deadlock victim: each either won or got the ordinary 409 for
        # losing the race to the same name.
        for result in results:
            assert not isinstance(result, BaseException) or isinstance(result, ConflictProblem), (
                f"unexpected failure: {result!r}"
            )
        wins = [r for r in results if not isinstance(r, BaseException)]
        assert len(wins) == 1


class TestLookupTiebreakIsDeterministic:
    """m6, fix round 3: two live companies whose names fold together and whose `created_at`
    is identical (a bulk import stamps one instant on every row) must still resolve to the
    same row every time — `id` is the last tiebreak."""

    async def test_identical_timestamps_resolve_to_the_smallest_id(
        self, session, org_id, admin_session
    ) -> None:
        low, high = sorted([uuid.uuid4(), uuid.uuid4()])
        owner = uuid.uuid4()
        stamp = datetime(2026, 9, 1, tzinfo=UTC)
        async with admin_session.begin():
            # The larger id goes in first, so physical order alone would return it.
            for company_id, name in ((high, "TIE BREAK LTD"), (low, "Tie Break Ltd")):
                await admin_session.execute(
                    text(
                        "INSERT INTO customer.companies (id, organization_id, name, domain, "
                        "industry, size_band, city, country, status, owner_id, "
                        "annual_revenue_minor, currency, created_at, updated_at) VALUES "
                        "(:id, :org, :name, '', '', '', '', '', 'prospect', :owner, 0, 'INR', "
                        ":ts, :ts)"
                    ),
                    {"id": company_id, "org": org_id, "name": name, "owner": owner, "ts": stamp},
                )
        async with session.begin():
            found = await selectors.get_company_by_name(session, org_id, "tie break ltd")
        assert found is not None
        assert found.id == low
