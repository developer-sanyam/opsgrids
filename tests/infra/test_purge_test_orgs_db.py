"""`tools/purge_test_orgs.py` against a real, migrated Postgres.

The offline tests (`test_purge_test_orgs.py`) pin the rules that need no database. These prove the
tool does what it says to a real schema: thirty-odd service schemas, a few hundred tables, real
foreign keys, row-level security and the append-only triggers.

**Opt-in**, like the put-away purge's real-database test: set
`OPSGRIDS_PURGE_ORGS_TEST_DATABASE_URL` to a **migrated, throwaway** database and the
migration/owner role, for example

    make migrate DATABASE_URL=<a DSN for the superuser on opsgrids_pto>
    OPSGRIDS_PURGE_ORGS_TEST_DATABASE_URL=<the same DSN as opsgrids_admin> \\
        uv run pytest tests/infra/test_purge_test_orgs_db.py

(`tools/dev-bootstrap/init-db.sql` creates both roles; the dev password is the one the compose file
sets. Create the database as `tools/dev-bootstrap/e2e-db.sql` creates `opsgrids_wt`.)

Without it every test here is skipped. Each test builds its own cast of organisations with random
ids (`_purge_orgs_world.build_world`: a row in every table the tool reaches, found from the
catalog, not listed here), purges some of them, and removes every row it made, so it is safe to
point at a database other work is using. One session per database at a time.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from _purge_orgs_world import World, add_membership, build_world, purge, remove_globals
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

REAL_DSN = os.environ.get("OPSGRIDS_PURGE_ORGS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not REAL_DSN,
    reason="set OPSGRIDS_PURGE_ORGS_TEST_DATABASE_URL to a migrated throwaway database "
    "(as opsgrids_admin)",
)

Table = purge.Table
NIL = uuid.UUID(int=0)
_SYSTEM = ("information_schema", "public", "tiger", "tiger_data", "topology")

#: Tables that carry a delete guard and that every cast organisation has a row in: the ones that
#: prove the guard handling, named so a refactor of the seeder cannot quietly stop covering them.
GUARDED = (
    "payment.refunds",
    "payment.provider_events",
    "subscription.subscription_transitions",
    "finance.itc_ledger_entries",
    "finance.gst_returns",
    "deal.activities",
    "approval.approval_steps",
    "itsm.incident_worklogs",
)
#: The tables the owner's brief names, and that the cast must have rows in.
NAMED = (
    "subscription.subscriptions",
    "billing.invoices",
    "billing.credit_ledger",
    "operations.inventory_items",
    "operations.inventory_ledger",
    "operations.putaway_captures",
    "finance.invoices",
    "payment.charges",
    "auth.audit_logs",
    "billing.outbox",
    "account.audit_logs",
    "console.org_commercial_state",
    "document.assets",
    "product.activations",
    "account.memberships",
    "account.membership_roles",
    "account.role_permissions",
    "auth.refresh_tokens",
)


# ------------------------------------------------------------------------------------- fixtures
@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    assert REAL_DSN is not None
    eng = create_async_engine(REAL_DSN)
    yield eng
    await eng.dispose()


def _fresh() -> AsyncEngine:
    assert REAL_DSN is not None
    return create_async_engine(REAL_DSN)


async def _cleanup(engine: AsyncEngine, world: World) -> None:
    """Remove every row the world made, with the tool's own per-organisation transaction (no
    survivor guard: the throwaway database may hold no other organisation) and then the rows the
    tool leaves by design."""
    async with engine.begin() as conn:
        ids = list(world.users.values())
        await conn.execute(
            text("UPDATE account.users SET is_staff = false WHERE id = ANY(:ids)"), {"ids": ids}
        )
        await conn.execute(
            text("UPDATE account.organizations SET parent_org_id = NULL WHERE id = ANY(:ids)"),
            {"ids": world.every_org},
        )
        plan = await purge.build_plan(conn)
    orgs = sorted(world.every_org)
    for i, org in enumerate(orgs):
        await purge._purge_one(engine, plan, org, orgs[: i + 1], keep_users=[], keep_orphans=False)
    async with engine.begin() as conn:
        # A staff account the purge kept on purpose has no organisation left to be found through.
        for table in plan.order:
            for cond in plan.nodes[table].conds:
                if cond.kind == "user":
                    statement = f'DELETE FROM {table.sql} WHERE "{cond.column}" = ANY(:ids)'  # noqa: S608
                    await conn.execute(text(statement), {"ids": list(world.users.values())})
        await remove_globals(conn, world)
        await conn.execute(
            text(
                "DELETE FROM auth.audit_logs WHERE action = 'organization.purged' "
                "AND entity_id = ANY(:ids)"
            ),
            {"ids": orgs},
        )


@pytest.fixture
async def world(engine: AsyncEngine) -> AsyncIterator[World]:
    async with engine.begin() as conn:
        built = await build_world(conn)
    try:
        yield built
    finally:
        await _cleanup(engine, built)


# ------------------------------------------------------------------------------------- helpers
async def _plan(engine: AsyncEngine) -> Any:
    async with engine.connect() as conn:
        return await purge.build_plan(conn)


def _json_lines(lines: list[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in lines if line.startswith("{")]


async def _run(
    selected: list[uuid.UUID], *, apply: bool, keep_orphans: bool = False
) -> tuple[Any, list[str]]:
    lines: list[str] = []
    result = await purge.run(
        _fresh(),
        selected=selected,
        apply=apply,
        keep_orphans=keep_orphans,
        database="localhost:5434/throwaway",
        out=lines.append,
    )
    return result, lines


async def _scalar(conn: AsyncConnection, sql: str, **params: Any) -> Any:
    return (await conn.execute(text(sql), params)).scalar_one()


async def _org_rows(conn: AsyncConnection, table: Any, org: uuid.UUID) -> int:
    """Rows of one table carrying this organisation's id (identifiers come from the catalog)."""
    statement = f'SELECT count(*) FROM {table.sql} WHERE "organization_id" = :o'  # noqa: S608
    return int((await conn.execute(text(statement), {"o": org})).scalar_one())


async def _tables_with_org_column(conn: AsyncConnection) -> list[Any]:
    """Independent of the tool: every base table with an `organization_id`, from the catalog (not
    `information_schema`, which hides what the role has no privilege on)."""
    rows = (
        await conn.execute(
            text(
                "SELECT n.nspname, c.relname FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE a.attname = 'organization_id' AND a.attnum > 0 AND NOT a.attisdropped "
                "AND c.relkind IN ('r', 'p') AND NOT c.relispartition "
                "AND n.nspname <> ALL(:sys) AND n.nspname NOT LIKE 'pg\\_%' ORDER BY 1, 2"
            ),
            {"sys": list(_SYSTEM)},
        )
    ).all()
    return [Table(r[0], r[1]) for r in rows]


async def _residual(conn: AsyncConnection, org: uuid.UUID) -> dict[str, int]:
    """Rows still carrying this organisation's id, in every table that has the column."""
    found: dict[str, int] = {}
    for table in await _tables_with_org_column(conn):
        n = await _org_rows(conn, table, org)
        if n:
            found[str(table)] = n
    return found


async def _fingerprint(
    conn: AsyncConnection, table: Any, where: str = "true", **params: Any
) -> tuple[int, str]:
    select = "SELECT count(*), coalesce(md5(string_agg(t::text, '|' ORDER BY t::text)), '')"
    statement = f"{select} FROM {table.sql} t WHERE {where}"
    row = (await conn.execute(text(statement), params)).one()
    return int(row[0]), str(row[1])


async def _everything(conn: AsyncConnection) -> dict[str, tuple[int, str]]:
    """A count and a hash of every row of every table in every service schema."""
    rows = (
        await conn.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_type = 'BASE TABLE' AND table_schema <> ALL(:sys) "
                "AND table_schema NOT LIKE 'pg\\_%' ORDER BY 1, 2"
            ),
            {"sys": list(_SYSTEM)},
        )
    ).all()
    return {f"{r[0]}.{r[1]}": await _fingerprint(conn, Table(r[0], r[1])) for r in rows}


async def _everything_but_org(
    conn: AsyncConnection, plan: Any, org: uuid.UUID, orphans: list[uuid.UUID]
) -> dict[str, tuple[int, str]]:
    """A count and a hash of every row that is *not* this organisation's (or one of its retired
    users'), by the tool's own selector, plus every table outside the plan whole. The platform
    sentinel's audit rows are left out: writing one is part of what a purge does."""
    snap: dict[str, tuple[int, str]] = {}
    for table, node in plan.nodes.items():
        where = f"({node.predicate(plan.nodes)}) IS NOT TRUE"
        if str(table) == "auth.audit_logs":
            where += " AND t.organization_id IS DISTINCT FROM CAST(:nil AS uuid)"
        snap[str(table)] = await _fingerprint(
            conn, table, where, org=org, orphans=orphans, nil=str(NIL)
        )
    every = await _everything(conn)
    for key, value in every.items():
        schema, name = key.split(".", 1)
        if Table(schema, name) not in plan.nodes:
            snap[key] = value
    return snap


async def _trigger_states(conn: AsyncConnection) -> dict[tuple[str, str], str]:
    rows = (
        await conn.execute(
            text(
                "SELECT n.nspname || '.' || c.relname, t.tgname::text, t.tgenabled::text "
                "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE NOT t.tgisinternal "
                "AND n.nspname <> ALL(:sys)"
            ),
            {"sys": list(_SYSTEM)},
        )
    ).all()
    return {(r[0], r[1]): r[2] for r in rows}


# ------------------------------------------------------------------------------------- the cast
async def test_the_cast_has_a_row_in_nearly_every_table_the_tool_reaches(
    engine: AsyncEngine, world: World
) -> None:
    """The proof is only as wide as the fixture: pin how wide it is."""
    plan = await _plan(engine)
    assert len(world.seeded) >= len(plan.nodes) - 3, sorted(
        str(t) for t in set(plan.nodes) - world.seeded
    )
    seeded = {str(t) for t in world.seeded}
    for name in (*GUARDED, *NAMED):
        assert name in seeded, f"{name} has no cast row: {world.skipped}"
    async with engine.connect() as conn:
        for key in ("a", "b", "c", "anchor", "real"):
            assert await _residual(conn, world.orgs[key]), key
    # RESTRICT keys that point at the table itself, with rows pointing at rows: the delete has to
    # take the whole set in one statement.
    referring = {str(t) for t in world.self_referencing}
    for name in ("operations.locations", "operations.item_categories", "account.org_units"):
        assert name in referring, (name, sorted(referring))


async def test_discovery_is_the_catalogs_not_a_list(engine: AsyncEngine, world: World) -> None:
    """Every table that has an `organization_id` is in the plan, found independently here, and
    every column that points at an organisation by another name is one the tool knows about."""
    plan = await _plan(engine)
    async with engine.connect() as conn:
        independent = set(await _tables_with_org_column(conn))
        others = (
            await conn.execute(
                text(
                    "SELECT table_schema, table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema <> ALL(:sys) AND table_schema NOT LIKE 'pg\\_%' "
                    "AND column_name <> 'organization_id' AND data_type = 'uuid' "
                    "AND column_name !~ 'org_unit' "  # an org *unit* is not an organisation
                    "AND (column_name ~ '(^|_)org(_|$)' OR column_name ~ 'organi[sz]ation')"
                ),
                {"sys": list(_SYSTEM)},
            )
        ).all()
    assert independent <= set(plan.nodes)
    unknown = [
        f"{r[0]}.{r[1]}.{r[2]}"
        for r in others
        if (r[0], r[1], r[2]) not in purge.KNOWN_ORG_REFERENCES
    ]
    assert unknown == [], (
        f"a column points at an organisation and the tool does not know: {unknown}"
    )


#: The base tables in a service schema that the purge does not reach, each on purpose: no
#: `organization_id` and no foreign key into a table that has one. (Besides `alembic_version` and
#: `processed_events`, which every schema has.) A table added by a later migration that is not
#: tenant data goes here with a reason; one that IS tenant data needs an `organization_id`
#: (CLAUDE.md), and then the purge reaches it by itself.
UNREACHED = {
    # The global catalogue and reference data: shared by every organisation.
    "catalog.features",
    "catalog.plan_versions",  # `restricted_to_org_id` is counted and left (immutable)
    "catalog.plans",
    "catalog.prices",
    "catalog.seat_classes",
    "product.products",
    "metering.meters",
    "billing.tax_rates",
    "finance.tax_rates",
    "finance.withholding_rates",
    "account.permissions",
    # The platform's own: signing keys, social-login provider config, operator accounts. The
    # operator side is never an organisation's.
    "auth.signing_keys",
    "auth.platform_oauth_providers",
    "auth.staff_accounts",
    "auth.staff_enrollment_codes",
    # Meta's data-deletion callback ledger: a confirmation code and a request key, no tenant.
    "notification.w1_meta_deletion_requests",
}


async def test_the_tables_the_purge_does_not_reach_are_exactly_the_ones_named(
    engine: AsyncEngine,
) -> None:
    """Discovery is by `organization_id` and by foreign keys into it. A future tenant table keyed
    some other way (`tenant_id`, `workspace_id`) with no such key would be skipped and the purge
    would report success with that data still there. So the complement is pinned: a new table the
    purge does not reach fails here until someone decides it is not tenant data and names it."""
    plan = await _plan(engine)
    async with engine.connect() as conn:
        every = {
            f"{r[0]}.{r[1]}"
            for r in (
                await conn.execute(
                    text(
                        "SELECT n.nspname, c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition "
                        "AND n.nspname <> ALL(:sys) AND n.nspname NOT LIKE 'pg\\_%'"
                    ),
                    {"sys": list(_SYSTEM)},
                )
            ).all()
        }
    reached = {str(t) for t in plan.nodes}
    outside = every - reached
    assert set(plan.unreached) == outside, "the tool's own list of what it leaves is wrong"
    tenant_candidates = {t for t in outside if t.split(".", 1)[1] not in purge.BOOKKEEPING_TABLES}
    assert tenant_candidates == UNREACHED, (
        f"not reached and not named: {sorted(tenant_candidates - UNREACHED)}; "
        f"named but now reached or gone: {sorted(UNREACHED - tenant_candidates)}"
    )
    assert {"account.users", "account.organizations", "auth.sessions"} <= reached


async def test_discovery_does_not_depend_on_what_the_role_may_see(
    engine: AsyncEngine, world: World
) -> None:
    """`information_schema` shows a role only the tables it owns or holds a privilege on. A narrow
    role (the operations service's) sees a fraction of them there; the plan the tool builds from
    the catalog is the same whoever builds it."""
    assert REAL_DSN is not None
    narrow = create_async_engine(make_url(REAL_DSN).set(username="opsgrids_operations"))
    try:
        async with engine.connect() as admin, narrow.connect() as limited:
            full = await purge.discover_org_tables(admin)
            assert await purge.discover_org_tables(limited) == full
            through_information_schema = await _scalar(
                limited,
                "SELECT count(DISTINCT (table_schema, table_name)) FROM information_schema.columns "
                "WHERE column_name = 'organization_id' AND table_schema <> ALL(:sys)",
                sys=list(_SYSTEM),
            )
            assert through_information_schema < len(full) // 2, "the premise of this test"
            plan_a, plan_b = await purge.build_plan(admin), await purge.build_plan(limited)
            assert [str(t) for t in plan_a.order] == [str(t) for t in plan_b.order]
            assert plan_a.unreached == plan_b.unreached
    finally:
        await narrow.dispose()


async def test_a_table_created_by_another_role_without_a_grant_stops_the_run(
    engine: AsyncEngine, world: World
) -> None:
    """The case behind the change: a hand-applied hotfix, run as a superuser, creates
    `x.new_table (organization_id ...)` and grants this role nothing. Through `information_schema`
    it does not exist for the owner role, so it was skipped, the organisation row went, and its
    rows in that table stayed as orphans for good. From the catalog the tool sees it, and the
    preflight refuses the run naming it."""
    assert REAL_DSN is not None
    a = world.orgs["a"]
    superuser = create_async_engine(
        make_url(REAL_DSN).set(username="opsgrids", password="opsgrids")  # the dev superuser
    )
    try:
        try:
            async with superuser.begin() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            pytest.skip(
                "needs the dev superuser (`opsgrids`) to create a table the owner cannot see"
            )
        async with superuser.begin() as conn:
            await conn.execute(text("CREATE SCHEMA pto_hidden"))
            await conn.execute(
                text(
                    "CREATE TABLE pto_hidden.leftover "
                    "(id uuid PRIMARY KEY, organization_id uuid NOT NULL)"
                )
            )
            await conn.execute(
                text("INSERT INTO pto_hidden.leftover VALUES (gen_random_uuid(), :o)"), {"o": a}
            )
        try:
            async with engine.connect() as conn:
                assert (
                    await _scalar(
                        conn,
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema = 'pto_hidden'",
                    )
                    == 0
                ), "the premise: the owner role cannot see it through information_schema"
                assert "pto_hidden.leftover" in {
                    str(t) for t in await purge.discover_org_tables(conn)
                }
            with pytest.raises(
                purge.Refusal, match=r"lacks DELETE or SELECT on: .*pto_hidden\.leftover"
            ):
                await _run([a], apply=True)
            async with engine.connect() as conn:
                assert (
                    await _scalar(
                        conn, "SELECT count(*) FROM account.organizations WHERE id = :o", o=a
                    )
                    == 1
                ), "a refused run deleted the organisation row"
        finally:
            async with superuser.begin() as conn:
                await conn.execute(text("DROP SCHEMA pto_hidden CASCADE"))
    finally:
        await superuser.dispose()


# ---------------------------------------------------------------------------------- dry run
async def test_a_dry_run_changes_nothing_and_says_exactly_what_apply_will_delete(
    engine: AsyncEngine, world: World
) -> None:
    a = world.orgs["a"]
    async with engine.connect() as conn:
        before = await _everything(conn)
        triggers_before = await _trigger_states(conn)

    dry, dry_lines = await _run([a], apply=False)

    async with engine.connect() as conn:
        assert await _everything(conn) == before, "a dry run wrote something"
        assert await _trigger_states(conn) == triggers_before
        assert (
            await _scalar(
                conn,
                "SELECT count(*) FROM auth.audit_logs WHERE action = 'organization.purged' "
                "AND entity_id = :o",
                o=a,
            )
            == 0
        )
    dry_line = next(x for x in _json_lines(dry_lines) if x.get("organization_id") == str(a))
    assert dry_line["status"] == "would-delete"
    assert dry.rows == dry_line["rows"] > 200

    done, lines = await _run([a], apply=True)
    applied = next(x for x in _json_lines(lines) if x.get("organization_id") == str(a))
    assert applied["deleted"] == dry_line["would_delete"], "the dry run is not what apply did"
    assert applied["users"] == dry_line["users"]
    assert done.rows == dry.rows and done.failed == 0
    # The facts a name cannot fake are printed beside the id, and are the same before the delete.
    assert dry_line["live_signals"]["members"] == 4
    assert set(dry_line["live_signals"]) == {"members", "subscriptions", "captured_charges"}
    assert applied["live_signals"] == dry_line["live_signals"]


@pytest.mark.parametrize("keys", [("a", "c"), ("a", "b", "c")])
async def test_a_dry_run_of_organisations_that_share_a_user_counts_what_apply_deletes(
    engine: AsyncEngine, world: World, keys: tuple[str, ...]
) -> None:
    """The cast has users in two selected organisations (`ac` is in a and c, `ab` in a and b). In
    one snapshot the first organisation is still there when the second is counted, so a shared
    user's session, membership and role rows used to be counted under both: the dry run said 551
    rows where `--apply` deleted 547. The promise is table by table, organisation by organisation,
    for any selection, not only for one organisation."""
    selected = [world.orgs[k] for k in keys]
    dry, dry_lines = await _run(selected, apply=False)
    done, lines = await _run(selected, apply=True)

    dry_by_org = {x["organization_id"]: x for x in _json_lines(dry_lines) if "status" in x}
    done_by_org = {x["organization_id"]: x for x in _json_lines(lines) if "status" in x}
    assert len(dry_by_org) == len(done_by_org) == len(keys)
    assert _processed(dry_lines) == _processed(lines)
    for org, line in done_by_org.items():
        assert line["deleted"] == dry_by_org[org]["would_delete"], f"{org}: dry run != apply"
        assert line["users"] == dry_by_org[org]["users"], org
    assert dry.rows == done.rows and dry.users_deleted == done.users_deleted
    assert (done.failed, done.removed) == (0, len(keys))


# ------------------------------------------------------------------------------------- apply
async def test_apply_removes_one_organisation_and_nothing_of_any_other(
    engine: AsyncEngine, world: World
) -> None:
    a = world.orgs["a"]
    plan = await _plan(engine)
    async with engine.connect() as conn:
        outside_before = await _everything_but_org(conn, plan, a, [world.users["a_only"]])
        assert await _residual(conn, a)

    result, _lines = await _run([a], apply=True)

    assert (result.removed, result.failed, result.already_clean) == (1, 0, 0)
    async with engine.connect() as conn:
        assert await _residual(conn, a) == {}, "rows still carry the organisation's id"
        assert (
            await _scalar(conn, "SELECT count(*) FROM account.organizations WHERE id = :o", o=a)
            == 0
        )
        assert (
            await _scalar(
                conn,
                "SELECT count(*) FROM auth.sessions WHERE active_org_id = :o",
                o=a,
            )
            == 0
        )
        outside_after = await _everything_but_org(conn, plan, a, [world.users["a_only"]])
        for org in ("b", "c", "anchor", "real"):
            assert await _residual(conn, world.orgs[org]), f"{org} lost its rows"
    changed = {k for k in outside_before if outside_before[k] != outside_after[k]}
    assert changed == set(), f"rows that are not the organisation's changed in: {sorted(changed)}"


async def test_users_follow_the_membership_rules(engine: AsyncEngine, world: World) -> None:
    a, u = world.orgs["a"], world.users

    async def user_rows(conn: AsyncConnection, name: str) -> tuple[int, int, int]:
        users_n = await _scalar(conn, "SELECT count(*) FROM account.users WHERE id = :u", u=u[name])
        creds = await _scalar(
            conn, "SELECT count(*) FROM auth.credentials WHERE user_id = :u", u=u[name]
        )
        members = await _scalar(
            conn, "SELECT count(*) FROM account.memberships WHERE user_id = :u", u=u[name]
        )
        return users_n, creds, members

    async with engine.connect() as conn:
        orphans_before = await _scalar(
            conn,
            "SELECT count(*) FROM account.users u WHERE NOT EXISTS "
            "(SELECT 1 FROM account.memberships m WHERE m.user_id = u.id)",
        )
    result, lines = await _run([a], apply=True)

    async with engine.connect() as conn:
        # Only in the removed organisation: the account and its credentials go with it.
        assert await user_rows(conn, "a_only") == (0, 0, 0)
        # Staff-flagged: the membership goes, the account stays.
        assert await user_rows(conn, "a_staff") == (1, 1, 0)
        # Also in an organisation that stays: untouched, with the other membership intact.
        assert await user_rows(conn, "ab") == (1, 1, 1)
        assert await user_rows(conn, "ac") == (1, 1, 1)
        for name in ("b_only", "c_only", "anchor_user", "real_owner"):
            assert await user_rows(conn, name) == (1, 1, 1), name
    line = next(x for x in _json_lines(lines) if x.get("organization_id") == str(a))
    assert line["users"] == {
        "members": 4,
        "kept_shared_with_another_organization": 2,
        "kept_protected": 1,
        "kept_by_choice": 0,
        "deleted": 1,
    }
    assert result.users_deleted == 1
    # What is left, as the summary prints it: the staff account has no organisation now.
    assert result.users_without_organisation == orphans_before + 1


async def test_a_user_in_two_selected_organisations_goes_with_the_last_of_them(
    engine: AsyncEngine, world: World
) -> None:
    a, c, u = world.orgs["a"], world.orgs["c"], world.users
    _result, _ = await _run([a, c], apply=True)
    async with engine.connect() as conn:
        for name in ("ac", "a_only", "c_only"):
            assert (
                await _scalar(conn, "SELECT count(*) FROM account.users WHERE id = :u", u=u[name])
                == 0
            ), name
        for name in ("ab", "a_staff", "b_only"):
            assert (
                await _scalar(conn, "SELECT count(*) FROM account.users WHERE id = :u", u=u[name])
                == 1
            ), name


async def test_keep_orphan_users_removes_memberships_only(
    engine: AsyncEngine, world: World
) -> None:
    a, u = world.orgs["a"], world.users
    result, _ = await _run([a], apply=True, keep_orphans=True)
    assert result.users_deleted == 0
    async with engine.connect() as conn:
        assert (
            await _scalar(conn, "SELECT count(*) FROM account.users WHERE id = :u", u=u["a_only"])
            == 1
        )
        assert (
            await _scalar(
                conn, "SELECT count(*) FROM account.memberships WHERE user_id = :u", u=u["a_only"]
            )
            == 0
        )


# ------------------------------------------------------------------------ append-only tables
async def test_append_only_tables_are_emptied_for_the_organisation_and_their_guards_survive(
    engine: AsyncEngine, world: World
) -> None:
    a, b = world.orgs["a"], world.orgs["b"]
    async with engine.connect() as conn:
        triggers_before = await _trigger_states(conn)
        for name in GUARDED:
            table = Table(*name.split("."))
            assert await _org_rows(conn, table, a), name

    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _spy(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        statements.append(statement)

    lines: list[str] = []
    result = await purge.run(engine, selected=[a], apply=True, out=lines.append)
    event.remove(engine.sync_engine, "before_cursor_execute", _spy)
    assert result.failed == 0

    async with engine.connect() as conn:
        for name in GUARDED:
            table = Table(*name.split("."))
            assert not await _org_rows(conn, table, a), name
            assert await _org_rows(conn, table, b), (
                f"{name}: another organisation's rows were touched"
            )
        # Every guard is back exactly as it was found...
        assert await _trigger_states(conn) == triggers_before
    # ...and still bites: the owner role cannot delete another organisation's row by hand.
    async with _fresh().connect() as conn:
        await conn.execute(text("SET lock_timeout = '5s'"))
        with pytest.raises(Exception, match="append-only"):
            await conn.execute(
                text("DELETE FROM payment.refunds WHERE organization_id = :o"), {"o": b}
            )
        await conn.rollback()

    # How narrowly it was done: guards lifted by name, only on delete, none left lifted, and no
    # global switch anywhere in what was sent.
    alters = [s for s in statements if s.startswith("ALTER TABLE")]
    disables = [s for s in alters if " DISABLE TRIGGER " in s]
    enables = [s for s in alters if " ENABLE TRIGGER " in s]
    assert disables and len(disables) == len(enables) == len(alters) // 2
    for s in alters:
        assert s.rstrip().endswith('"') and " ALL" not in s and " USER" not in s, s
    joined = "\n".join(statements).lower()
    for forbidden in ("session_replication_role", "truncate", "alter role", "drop ", "grant "):
        assert forbidden not in joined, forbidden
    # Only the guards of tables that had rows were lifted (this organisation had a row in each).
    assert len(disables) >= len(GUARDED)


async def test_a_failure_part_way_rolls_the_whole_organisation_back(
    engine: AsyncEngine, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = world.orgs["a"]
    async with engine.connect() as conn:
        before = await _everything(conn)
        triggers_before = await _trigger_states(conn)

    async def boom(*_args: Any, **_kw: Any) -> None:
        raise RuntimeError("simulated failure after every delete and before the commit")

    monkeypatch.setattr(purge, "_write_audit", boom)
    result, lines = await _run([a], apply=True)

    assert (result.failed, result.removed) == (1, 0)
    failure = next(x for x in _json_lines(lines) if x.get("status") == "failed")
    assert failure["organization_id"] == str(a) and failure["error"] == "RuntimeError"
    async with engine.connect() as conn:
        assert await _everything(conn) == before, "a failed organisation left a partial delete"
        assert await _trigger_states(conn) == triggers_before

    monkeypatch.undo()
    again, _ = await _run([a], apply=True)
    assert (again.removed, again.failed) == (1, 0), "a failed run must be re-runnable"


async def test_an_interrupted_run_leaves_each_organisation_whole_or_untouched(
    engine: AsyncEngine, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    ordered = sorted([world.orgs["a"], world.orgs["b"], world.orgs["c"]])
    first, second, third = ordered
    real_audit = purge._write_audit

    async def fail_second(conn: Any, org: uuid.UUID, summary: dict[str, Any]) -> None:
        if org == second:
            raise RuntimeError("simulated")
        await real_audit(conn, org, summary)

    monkeypatch.setattr(purge, "_write_audit", fail_second)
    result, _ = await _run(ordered, apply=True)

    assert (result.removed, result.failed, result.not_attempted) == (1, 1, 1)
    async with engine.connect() as conn:
        assert await _residual(conn, first) == {}
        assert await _residual(conn, second), "the failed organisation was partly deleted"
        assert await _residual(conn, third), "an organisation after the failure was touched"


async def test_a_refusal_after_the_first_organisation_committed_exits_1_with_the_summary(
    engine: AsyncEngine, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 2 is "refused before anything was written". Here the first organisation is already
    gone when the second is refused (a second purge holds the lock), so the run exits 1 and prints
    what happened, and the owner is not told "nothing was done"."""
    first, second, third = sorted([world.orgs["a"], world.orgs["b"], world.orgs["c"]])
    real = purge._purge_one
    calls: list[uuid.UUID] = []

    async def lock_lost_on_the_second(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[2])
        if len(calls) == 2:
            raise purge.Refusal("another purge is running against this database")
        return await real(*args, **kwargs)

    monkeypatch.setattr(purge, "_purge_one", lock_lost_on_the_second)
    out: list[str] = []
    argv = ["--apply", "--confirm-count", "3", "--i-took-a-backup"]
    for org in (first, second, third):
        argv += ["--org-id", str(org)]
    code = await asyncio.to_thread(
        purge.main,
        argv,
        cfg=SimpleNamespace(environment="local", admin_dsn=REAL_DSN),
        out=out.append,
    )
    assert code == 1
    summary = next(x for x in _json_lines(out) if "organisations_remaining" in x)
    assert (summary["organisations_deleted"], summary["failed"], summary["not_attempted"]) == (
        1,
        1,
        1,
    )
    async with engine.connect() as conn:
        assert await _residual(conn, first) == {}
        assert await _residual(conn, second), "the refused organisation was touched"
        assert await _residual(conn, third), "an organisation after the refusal was touched"


async def test_a_second_apply_is_a_no_op(engine: AsyncEngine, world: World) -> None:
    a = world.orgs["a"]
    await _run([a], apply=True)
    async with engine.connect() as conn:
        before = await _everything(conn)
    again, lines = await _run([a], apply=True)
    assert (again.removed, again.already_clean, again.rows, again.failed) == (0, 1, 0, 0)
    assert next(x for x in _json_lines(lines) if "status" in x)["status"] == "already-clean"
    async with engine.connect() as conn:
        assert await _everything(conn) == before
        audits = await _scalar(
            conn,
            "SELECT count(*) FROM auth.audit_logs WHERE action = 'organization.purged' "
            "AND entity_id = :o",
            o=a,
        )
    assert audits == 1, "one audit row per purge, not per run"


async def test_an_id_that_names_no_organisation_is_a_no_op_that_says_so(
    engine: AsyncEngine, world: World
) -> None:
    """A typo cannot delete anything, and it must not look like a success: `not-found` is not
    `already-clean`, which is what an id purged earlier says."""
    ghost, a = uuid.uuid4(), world.orgs["a"]
    result, lines = await _run([ghost], apply=True)
    assert (result.not_found, result.already_clean, result.rows, result.failed) == (1, 0, 0, 0)
    assert next(x for x in _json_lines(lines) if "status" in x)["status"] == "not-found"
    assert any("check them for typos" in line for line in lines)

    await _run([a], apply=True)
    _again, lines = await _run([a], apply=False)
    assert next(x for x in _json_lines(lines) if "status" in x)["status"] == "already-clean"


async def test_a_membership_added_to_a_doomed_user_mid_run_stops_the_run(
    engine: AsyncEngine, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user was alone in the organisation when the run looked, and joins another before it
    deletes them. Deleting them would cascade the new membership away; the run refuses instead."""
    a, b, a_only = world.orgs["a"], world.orgs["b"], world.users["a_only"]
    real_plan_org = purge.plan_org

    async def plan_then_a_signup_lands(*args: Any, **kwargs: Any) -> Any:
        op = await real_plan_org(*args, **kwargs)
        other = _fresh()
        try:
            async with other.begin() as conn:
                await add_membership(conn, a_only, b)
        finally:
            await other.dispose()
        return op

    monkeypatch.setattr(purge, "plan_org", plan_then_a_signup_lands)
    result, lines = await _run([a], apply=True)

    assert result.failed == 1
    failure = next(x for x in _json_lines(lines) if x.get("status") == "failed")
    assert "membership(s) were added" in failure["detail"]
    async with engine.connect() as conn:
        assert await _residual(conn, a), "the failed organisation was partly deleted"
        assert (
            await _scalar(conn, "SELECT count(*) FROM account.users WHERE id = :u", u=a_only) == 1
        )
        assert (
            await _scalar(
                conn,
                "SELECT count(*) FROM account.memberships "
                "WHERE user_id = :u AND organization_id = :b",
                u=a_only,
                b=b,
            )
            == 1
        ), "the membership that raced in must survive"
    monkeypatch.undo()
    again, _ = await _run([a], apply=True)
    assert again.failed == 0 and again.users_deleted == 0, "now shared with B: kept"


async def test_the_deletion_leaves_one_counts_only_audit_row_under_the_platform(
    engine: AsyncEngine, world: World
) -> None:
    a = world.orgs["a"]
    result, _ = await _run([a], apply=True)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT organization_id, action, entity, after, acting_as FROM auth.audit_logs "
                    "WHERE action = 'organization.purged' AND entity_id = :o"
                ),
                {"o": a},
            )
        ).one()
    assert row[0] == NIL and row[1] == "organization.purged" and row[2] == "organization"
    assert row[4] == "owner-cli"
    assert set(row[3]) == {"tables", "rows", "users_deleted"}
    assert row[3]["rows"] == result.rows


# --------------------------------------------------------------------------------- refusals
async def test_a_protected_organisation_is_refused_and_nothing_changes(
    engine: AsyncEngine, world: World
) -> None:
    b = world.orgs["b"]
    async with engine.connect() as conn:
        before = await _everything(conn)
    code = await asyncio.to_thread(
        purge.main,
        [
            "--org-id",
            str(b),
            "--keep",
            str(b),
            "--apply",
            "--confirm-count",
            "1",
            "--i-took-a-backup",
        ],
        cfg=SimpleNamespace(environment="local", admin_dsn=REAL_DSN),
    )
    assert code == 2
    async with engine.connect() as conn:
        assert await _everything(conn) == before


@pytest.mark.parametrize(
    "extra",
    [
        ["--confirm-count", "2"],  # one organisation selected, two claimed
        ["--confirm-count", "0"],
        [],  # no --confirm-count at all
    ],
)
async def test_a_wrong_or_missing_confirm_count_is_refused(
    engine: AsyncEngine, world: World, extra: list[str]
) -> None:
    a = world.orgs["a"]
    async with engine.connect() as conn:
        before = await _everything(conn)
    argv = ["--org-id", str(a), "--apply", "--i-took-a-backup", *extra]
    code = await asyncio.to_thread(
        purge.main, argv, cfg=SimpleNamespace(environment="local", admin_dsn=REAL_DSN)
    )
    assert code == 2
    async with engine.connect() as conn:
        assert await _everything(conn) == before


async def test_a_missing_backup_attestation_is_refused(engine: AsyncEngine, world: World) -> None:
    a = world.orgs["a"]
    async with engine.connect() as conn:
        before = await _everything(conn)
    code = await asyncio.to_thread(
        purge.main,
        ["--org-id", str(a), "--apply", "--confirm-count", "1"],
        cfg=SimpleNamespace(environment="local", admin_dsn=REAL_DSN),
    )
    assert code == 2
    async with engine.connect() as conn:
        assert await _everything(conn) == before


async def test_a_live_environment_needs_its_own_flag_for_apply_only(
    engine: AsyncEngine, world: World
) -> None:
    a = world.orgs["a"]
    prod = SimpleNamespace(environment="production", admin_dsn=REAL_DSN)
    flags = ["--org-id", str(a), "--confirm-count", "1", "--i-took-a-backup"]
    async with engine.connect() as conn:
        before = await _everything(conn)

    assert await asyncio.to_thread(purge.main, [*flags, "--apply"], cfg=prod) == 2
    async with engine.connect() as conn:
        assert await _everything(conn) == before

    # Listing and the dry run never need it, even on a live environment.
    lines: list[str] = []
    assert (
        await asyncio.to_thread(purge.main, ["--org-id", str(a)], cfg=prod, out=lines.append) == 0
    )
    assert await asyncio.to_thread(purge.main, [], cfg=prod, out=lines.append) == 0

    code = await asyncio.to_thread(
        purge.main,
        [*flags, "--apply", "--i-know-this-is-the-live-database"],
        cfg=prod,
        out=lines.append,
    )
    assert code == 0
    async with engine.connect() as conn:
        assert await _residual(conn, a) == {}


async def test_the_survivor_rules(engine: AsyncEngine, world: World) -> None:
    """Never empties the platform; never orphans a child organisation."""
    async with engine.connect() as conn:
        everything = [
            r[0] for r in (await conn.execute(text("SELECT id FROM account.organizations"))).all()
        ]
        problems, total, remaining = await purge.guard_survivors(conn, everything)
        assert remaining == 0 and total == len(everything)
        assert any("every organisation" in p for p in problems)

    a, b = world.orgs["a"], world.orgs["b"]
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE account.organizations SET parent_org_id = :a WHERE id = :b"),
            {"a": a, "b": b},
        )
    async with engine.connect() as conn:
        problems, _, _ = await purge.guard_survivors(conn, [a])
        assert any("children of a selected one" in p for p in problems)
        problems, _, _ = await purge.guard_survivors(conn, [a, b])
        assert not any("children" in p for p in problems)
    with pytest.raises(purge.Refusal, match="children of a selected one"):
        await _run([a], apply=True)


async def _set_parent(engine: AsyncEngine, child: uuid.UUID, parent: uuid.UUID) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE account.organizations SET parent_org_id = :p WHERE id = :c"),
            {"p": parent, "c": child},
        )


def _processed(lines: list[str]) -> list[uuid.UUID]:
    """The organisations a run reported on, in the order it reported them."""
    return [
        uuid.UUID(x["organization_id"])
        for x in _json_lines(lines)
        if "organization_id" in x and "status" in x
    ]


@pytest.mark.parametrize("parent_sorts_first", [True, False])
async def test_a_parent_and_its_child_selected_together_go_child_first(
    engine: AsyncEngine, world: World, parent_sorts_first: bool
) -> None:
    """The refusal says "select them too": that has to work, whichever of the two ids sorts first.
    `parent_org_id` is RESTRICT and each organisation is its own transaction, so processing in id
    order failed on the parent (SQLSTATE 23503) when its id was the smaller, while the dry run,
    which only counts, reported both as deletable."""
    low, high = sorted([world.orgs["a"], world.orgs["b"]])
    parent, child = (low, high) if parent_sorts_first else (high, low)
    await _set_parent(engine, child, parent)

    dry, dry_lines = await _run([parent, child], apply=False)
    assert (dry.failed, dry.removed) == (0, 2)
    done, lines = await _run([parent, child], apply=True)
    assert (done.failed, done.removed, done.not_attempted) == (0, 2, 0), lines
    assert _processed(dry_lines) == _processed(lines) == [child, parent], (
        "the dry run and the apply take the same order, child first"
    )
    async with engine.connect() as conn:
        for org in (parent, child):
            assert await _residual(conn, org) == {}
            assert (
                await _scalar(
                    conn, "SELECT count(*) FROM account.organizations WHERE id = :o", o=org
                )
                == 0
            )
        for key in ("c", "anchor", "real"):
            assert await _residual(conn, world.orgs[key]), f"{key} lost its rows"


@pytest.mark.parametrize("root_sorts_first", [True, False])
async def test_a_chain_of_sub_organisations_goes_leaf_first(
    engine: AsyncEngine, world: World, root_sorts_first: bool
) -> None:
    ascending = sorted([world.orgs["a"], world.orgs["b"], world.orgs["c"]])
    root, mid, leaf = ascending if root_sorts_first else ascending[::-1]
    await _set_parent(engine, mid, root)
    await _set_parent(engine, leaf, mid)

    done, lines = await _run([root, mid, leaf], apply=True)
    assert (done.failed, done.removed) == (0, 3), lines
    assert _processed(lines) == [leaf, mid, root]
    async with engine.connect() as conn:
        for org in (root, mid, leaf):
            assert await _residual(conn, org) == {}


async def test_a_loop_of_parent_links_is_refused_before_anything_is_written(
    engine: AsyncEngine, world: World
) -> None:
    a, b = world.orgs["a"], world.orgs["b"]
    await _set_parent(engine, a, b)
    await _set_parent(engine, b, a)
    async with engine.connect() as conn:
        before = await _everything(conn)
    for apply in (False, True):
        with pytest.raises(purge.Refusal, match="forms a loop"):
            await _run([a, b], apply=apply)
    async with engine.connect() as conn:
        assert await _everything(conn) == before


async def test_the_platform_sentinel_organisation_is_refused(
    engine: AsyncEngine, world: World
) -> None:
    code = await asyncio.to_thread(
        purge.main,
        ["--org-id", str(NIL), "--apply", "--confirm-count", "1", "--i-took-a-backup"],
        cfg=SimpleNamespace(environment="local", admin_dsn=REAL_DSN),
    )
    assert code == 2


async def test_a_role_that_cannot_bypass_row_level_security_is_refused(
    engine: AsyncEngine, world: World
) -> None:
    """Under RLS a delete does not fail, it silently matches nothing: the tool checks first."""
    assert REAL_DSN is not None
    async with engine.connect() as conn:
        plan = await purge.build_plan(conn)
        assert await purge.preflight(conn, plan) == []
    service = create_async_engine(make_url(REAL_DSN).set(username="opsgrids_operations"))
    try:
        async with service.connect() as conn:
            problems = await purge.preflight(conn, plan)
    finally:
        await service.dispose()
    assert any("row-level security" in p for p in problems), problems
    assert any("lacks DELETE" in p for p in problems), problems


async def test_a_second_purge_at_the_same_time_is_refused(
    engine: AsyncEngine, world: World
) -> None:
    a = world.orgs["a"]
    holder = _fresh()
    async with holder.connect() as hold:
        await hold.execute(text("SELECT pg_advisory_lock(:k)"), {"k": purge._ADVISORY_LOCK_KEY})
        try:
            with pytest.raises(purge.Refusal, match="another purge"):
                await _run([a], apply=True)
        finally:
            await hold.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": purge._ADVISORY_LOCK_KEY}
            )
    await holder.dispose()
    async with engine.connect() as conn:
        assert await _residual(conn, a), "a refused purge deleted something"


# ----------------------------------------------------------------------------------- output
async def test_nothing_printed_is_a_name_an_address_a_row_or_a_connection_string(
    engine: AsyncEngine, world: World
) -> None:
    a = world.orgs["a"]
    _dry, dry_lines = await _run([a], apply=False)
    _done, lines = await _run([a], apply=True)
    printed = "\n".join(dry_lines + lines)
    secrets = [
        "e2e a",
        "e2e-a-",
        "@example.com",
        "@acme-textiles.in",
        "opsgrids_admin",
        "password_hash",
        "://",
        "asyncpg",
    ]
    for needle in secrets:
        assert needle not in printed, needle
    allowed_org = {
        "organization_id",
        "mode",
        "status",
        "deleted",
        "would_delete",
        "rows",
        "users",
        "live_signals",
        "left_in_place",
    }
    allowed_summary = {
        "mode",
        "database",
        "environment",
        "selected",
        "organisations_before",
        "organisations_remaining",
        "users_without_any_organisation_remaining",
        "organisations_deleted",
        "organisations_with_rows",
        "already_clean",
        "failed",
        "not_attempted",
        "not_found",
        "rows_deleted",
        "rows_would_delete",
        "users_deleted",
        "users_would_delete",
    }
    for line in _json_lines(dry_lines + lines):
        assert set(line) <= (allowed_org | allowed_summary), set(line)


# ---------------------------------------------------------------------------------- listing
async def test_the_listing_shows_hints_without_selecting_anything(
    engine: AsyncEngine, world: World
) -> None:
    keep = world.orgs["anchor"]
    async with engine.connect() as conn:
        rows = [r for r in await purge.list_orgs(conn, [keep]) if r.id in set(world.every_org)]
    by_id = {r.id: r for r in rows}
    real = by_id[world.orgs["real"]]
    assert real.hint != "LIKELY TEST", real.reasons
    for key in ("a", "b", "c", "anchor"):
        assert by_id[world.orgs[key]].hint == "LIKELY TEST", key
    assert by_id[keep].kept and not by_id[world.orgs["a"]].kept
    a_row = by_id[world.orgs["a"]]
    assert a_row.members == 4 and a_row.throwaway_members == 4
    assert any("throwaway email" in r for r in a_row.reasons)

    listing = purge.render_listing(rows, "localhost:5434/throwaway", "local")
    assert "e2e a" in listing and "Latest Textiles" in listing and "KEEP" in listing
    assert "nothing is selected for you" in listing
    candidates = purge.render_candidates(rows)
    assert str(world.orgs["a"]) in candidates and str(world.orgs["real"]) not in candidates
    assert str(keep) not in candidates, "a kept organisation is never offered"
    assert "Nothing here is selected until you" in candidates


@pytest.mark.parametrize("separator", ["\n", "\u2028", "\x85", "\x0b", "\r"])
async def test_a_hostile_organisation_name_cannot_put_a_real_customers_id_in_the_candidates_file(
    engine: AsyncEngine, world: World, separator: str
) -> None:
    """Signup is public: anyone can name their organisation `smoke test<line break><victim's id>`
    (an organisation id is in every public image URL). Through the real listing and the real
    `--candidates` path, the hostile organisation's own id may be offered (it is hinted) and the
    customer's must not be, either in the file or when the owner passes the file back."""
    real, b = world.orgs["real"], world.orgs["b"]
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE account.organizations SET name = :n WHERE id = :b"),
            {"n": f"smoke test{separator}{real} # x", "b": b},
        )
    cfg = SimpleNamespace(environment="local", admin_dsn=REAL_DSN)
    out: list[str] = []
    assert await asyncio.to_thread(purge.main, ["--candidates"], cfg=cfg, out=out.append) == 0
    selected = purge.parse_ids("\n".join(out), source="candidates")
    assert b in selected and real not in selected
    assert len(selected) == len(set(selected))

    out.clear()
    assert await asyncio.to_thread(purge.main, [], cfg=cfg, out=out.append) == 0
    listing = "\n".join(out)
    assert not any(c in listing for c in "\u2028\x85\x0b\r"), "a separator reached the terminal"
    id_column = [parts[1] for line in listing.split("\n") if len(parts := line.split()) > 1]
    assert id_column.count(str(real)) == 1, "the customer's id leads the customer's own row only"
    assert id_column.count(str(b)) == 1, "and the hostile name did not make a row of its own"


async def test_main_lists_dry_runs_and_applies_end_to_end(
    engine: AsyncEngine, world: World
) -> None:
    a = world.orgs["a"]
    cfg = SimpleNamespace(environment="local", admin_dsn=REAL_DSN)
    out: list[str] = []
    assert await asyncio.to_thread(purge.main, [], cfg=cfg, out=out.append) == 0
    assert "e2e a" in "\n".join(out)

    out.clear()
    assert await asyncio.to_thread(purge.main, ["--json"], cfg=cfg, out=out.append) == 0
    listed = {json.loads(x)["id"] for x in out}
    assert str(a) in listed

    out.clear()
    assert await asyncio.to_thread(purge.main, ["--explain"], cfg=cfg, out=out.append) == 0
    assert any("auth.sessions" in x for x in out) and any("delete guard" in x for x in out)
    explained = "\n".join(out)
    assert "NOT reached" in explained and "catalog.plans" in explained, "what is left is shown"
    assert "alembic_version" in explained and "processed_events" in explained

    out.clear()
    assert await asyncio.to_thread(purge.main, ["--org-id", str(a)], cfg=cfg, out=out.append) == 0
    assert any("dry run only" in x for x in out)
    async with engine.connect() as conn:
        assert await _residual(conn, a)

    out.clear()
    code = await asyncio.to_thread(
        purge.main,
        ["--org-id", str(a), "--apply", "--confirm-count", "1", "--i-took-a-backup"],
        cfg=cfg,
        out=out.append,
    )
    assert code == 0
    async with engine.connect() as conn:
        assert await _residual(conn, a) == {}


async def test_an_ids_file_selects_the_same_as_repeated_org_ids(
    engine: AsyncEngine, world: World, tmp_path: Any
) -> None:
    a, b = world.orgs["a"], world.orgs["b"]
    ids = tmp_path / "ids.txt"
    ids.write_text(f"# reviewed by hand\n{a}  # one\n\n{b}\n{a}\n")
    out: list[str] = []
    code = await asyncio.to_thread(
        purge.main,
        ["--ids-file", str(ids), "--apply", "--confirm-count", "2", "--i-took-a-backup"],
        cfg=SimpleNamespace(environment="local", admin_dsn=REAL_DSN),
        out=out.append,
    )
    assert code == 0, "duplicates collapse: two organisations, count 2"
    async with engine.connect() as conn:
        assert await _residual(conn, a) == {} and await _residual(conn, b) == {}
        assert await _residual(conn, world.orgs["c"])
