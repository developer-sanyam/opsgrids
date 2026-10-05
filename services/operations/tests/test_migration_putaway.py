# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""`d7a3c9e5b184` (warehouse site coordinates and the append-only `putaway_captures` table), run
for real.

Structure at head — columns and nullability, the named CHECKs, single-column in-schema foreign
keys, the tenant-leading indexes, RLS enabled *and forced* with one `tenant_isolation` policy, and
the privileges that make the table append-only for the app role and unreachable for the BYPASSRLS
outbox relay. Then the behaviour a deploy depends on: the revision is additive (code from before
it keeps working and data from before it survives), a second grant re-application (what every
later `alembic upgrade` does) cannot hand `UPDATE`/`DELETE` back, `downgrade()` **refuses** while
any capture or any site coordinate exists unless the operator says destroying them is the point, a
fully empty database round-trips, the autogenerate check sees no drift, there is a single head,
and a migration that cannot get its lock fails whole and leaves nothing half-applied.

Drives alembic in subprocesses (it is the real env.py: grants, append-only re-revoke) down to the
revision right before this one, and always leaves the schema at head. The "before" revision is read
from the migration's own `down_revision`, so re-parenting the revision (two inventory stacks share
the old head) needs no edit here.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.models import SCHEMA

pytestmark = pytest.mark.integration

SERVICE_DIR = Path(__file__).resolve().parent.parent
REV = "d7a3c9e5b184"  # pragma: allowlist secret
ALLOW = {"OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE": "1"}
#: The retention purge's index (`tools/purge_putaway_captures.py` filters on `created_at`).
PURGE_INDEX = "ix_putaway_captures_created_at"
#: What a TRUNCATE ... CASCADE needs named to empty everything the seeds below touch.
TRUNCATE = (
    "putaway_captures",
    "workers",
    "inventory_ledger",
    "inventory_balances",
    "inventory_moves",
    "inventory_items",
    "locations",
    "warehouses",
    "audit_logs",
    "outbox",
)


def _script() -> ScriptDirectory:
    cfg = Config(str(SERVICE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVICE_DIR / "migrations"))
    return ScriptDirectory.from_config(cfg)


def _before() -> str:
    down = _script().get_revision(REV).down_revision
    assert isinstance(down, str), down
    return down


BEFORE = _before()


def alembic(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  (fixed argv, sys.executable, no shell)
        [sys.executable, "-m", "alembic", *args],
        cwd=SERVICE_DIR,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **env} if env else None,
    )


@pytest.fixture
async def admin():  # type: ignore[no-untyped-def]
    """The BYPASSRLS admin engine, with the schema emptied and — when a test asks for the old
    shape — left at the revision before this one. Always restored to head."""
    engine = create_async_engine(
        settings().admin_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    async with engine.begin() as conn:
        existing = [
            t
            for t in TRUNCATE
            if (await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.{t}"})).scalar()
        ]
        await conn.execute(text(f"TRUNCATE {', '.join(f'{SCHEMA}.{t}' for t in existing)} CASCADE"))
    try:
        yield engine
    finally:
        up = alembic("upgrade", "head")
        await engine.dispose()
        assert up.returncode == 0, up.stderr[-2000:]


async def current_revision(conn) -> str:  # type: ignore[no-untyped-def]
    return str(
        (await conn.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version"))).scalar_one()
    )


async def scalar(conn, sql: str, **params):  # type: ignore[no-untyped-def]
    return (await conn.execute(text(sql), params)).scalar_one()


# ------------------------------------------------------------------ seeds


async def seed_site(
    conn, org: uuid.UUID, *, coords: bool = False, capture: bool = False
) -> dict[str, uuid.UUID]:  # type: ignore[no-untyped-def]
    """A warehouse, two bins, an item and a relocation move; optionally the warehouse's coordinates
    and a put-away capture for the move. Plain SQL as the admin role: it must work at any revision,
    so `coords` is only ever asked for at head."""
    ids = {n: uuid.uuid4() for n in ("wh", "loc", "loc2", "item", "move")}
    params = {**ids, "org": org}
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.warehouses (id, organization_id, code, name, location,
                                             created_at, updated_at)
            VALUES (:wh, :org, 'WH-MIG', 'Migration Plant', 'Pune', now(), now())
            """
        ),
        params,
    )
    for key, code, default in (("loc", "DEFAULT", True), ("loc2", "B-02", False)):
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.locations (id, organization_id, warehouse_id, kind, code,
                                                name, is_default, created_at, updated_at)
                VALUES (:id, :org, :wh, 'bin', :code, '', :d, now(), now())
                """
            ),
            {"id": ids[key], "org": org, "wh": ids["wh"], "code": code, "d": default},
        )
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.inventory_items
                (id, organization_id, sku, name, category, warehouse, home_location_id,
                 reorder_point, uom, unit_value_minor, currency, stocked_at, hsn_code, track_lot,
                 track_roll, track_expiry, track_quality, is_active, created_at, updated_at)
            VALUES (:item, :org, 'DYE-1', 'Dye', 'raw_materials', 'Migration Plant', :loc,
                    0, 'kg', 0, 'INR', '2026-09-01', '', false, false, false, false, true, now(),
                    now())
            """
        ),
        params,
    )
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.inventory_moves (id, organization_id, kind, reference_type, note,
                                                  occurred_on, created_at)
            VALUES (:move, :org, 'relocation', 'bin_transfer', '', '2026-09-01', now())
            """
        ),
        params,
    )
    if coords:
        await conn.execute(
            text(
                f"""
                UPDATE {SCHEMA}.warehouses SET lat_micro = 18520430, lng_micro = 73856744
                 WHERE id = :wh
                """
            ),
            ids,
        )
    if capture:
        await conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.putaway_captures
                    (id, organization_id, move_id, location_id, created_by, lat_micro, lng_micro,
                     accuracy_m, captured_at)
                VALUES (gen_random_uuid(), :org, :move, :loc2, gen_random_uuid(), 12971601,
                        77594602, 9, now())
                """
            ),
            params,
        )
    return ids


# ------------------------------------------------------------------ structure at head

COLUMNS_SQL = """
    SELECT column_name, data_type, is_nullable FROM information_schema.columns
     WHERE table_schema = :s AND table_name = :t
"""
COLUMNS = {
    "id": ("uuid", "NO"),
    "organization_id": ("uuid", "NO"),
    "move_id": ("uuid", "NO"),
    "location_id": ("uuid", "NO"),
    "suggested_location_id": ("uuid", "YES"),
    "worker_id": ("uuid", "YES"),
    "created_by": ("uuid", "NO"),
    "lat_micro": ("bigint", "YES"),
    "lng_micro": ("bigint", "YES"),
    "accuracy_m": ("integer", "YES"),
    "captured_at": ("timestamp with time zone", "NO"),
    "created_at": ("timestamp with time zone", "NO"),
    "matched_suggestion": ("boolean", "YES"),
}


class TestStructureAtHead:
    async def test_the_warehouse_columns(self, admin) -> None:
        async with admin.begin() as conn:
            rows = (
                await conn.execute(
                    text(COLUMNS_SQL),
                    {"s": SCHEMA, "t": "warehouses"},
                )
            ).all()
        found = {r[0]: (r[1], r[2]) for r in rows}
        assert found["lat_micro"] == ("bigint", "YES")
        assert found["lng_micro"] == ("bigint", "YES")
        # The address is `location`, already there: no second copy of it.
        assert "address_text" not in found

    async def test_the_capture_columns_are_exactly_these(self, admin) -> None:
        async with admin.begin() as conn:
            rows = (
                await conn.execute(
                    text(COLUMNS_SQL),
                    {"s": SCHEMA, "t": "putaway_captures"},
                )
            ).all()
        assert {r[0]: (r[1], r[2]) for r in rows} == COLUMNS
        # No float anywhere, and nothing that could hold free-text location data.
        assert not {r[1] for r in rows} & {"double precision", "real", "numeric", "text"}

    async def test_the_creation_time_defaults_to_now(self, admin) -> None:
        async with admin.begin() as conn:
            default = await scalar(
                conn,
                "SELECT column_default FROM information_schema.columns WHERE table_schema = :s "
                "AND table_name = 'putaway_captures' AND column_name = 'created_at'",
                s=SCHEMA,
            )
        assert "now()" in default

    @pytest.mark.parametrize(
        ("table", "name", "fragments"),
        [
            (
                "warehouses",
                "ck_warehouses_coords_paired",
                ["lat_micro IS NULL", "lng_micro IS NULL"],
            ),
            ("warehouses", "ck_warehouses_lat_in_range", ["-90000000", "90000000"]),
            ("warehouses", "ck_warehouses_lng_in_range", ["-180000000", "180000000"]),
            ("putaway_captures", "ck_putaway_captures_coords_paired", ["lat_micro IS NULL"]),
            ("putaway_captures", "ck_putaway_captures_lat_in_range", ["-90000000", "90000000"]),
            ("putaway_captures", "ck_putaway_captures_lng_in_range", ["-180000000", "180000000"]),
            ("putaway_captures", "ck_putaway_captures_accuracy_non_negative", ["accuracy_m >= 0"]),
        ],
    )
    async def test_the_named_checks_exist(self, admin, table, name, fragments) -> None:
        async with admin.begin() as conn:
            definition = await scalar(
                conn,
                "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
                "JOIN pg_namespace n ON n.oid = c.connamespace "
                "WHERE n.nspname = :s AND c.conrelid = (:s || '.' || :t)::regclass "
                "AND c.conname = :n AND c.contype = 'c'",
                s=SCHEMA,
                t=table,
                n=name,
            )
        for fragment in fragments:
            assert fragment in definition, (name, definition)

    async def test_the_foreign_keys_are_single_column_in_schema_and_restrict(self, admin) -> None:
        async with admin.begin() as conn:
            keys = (
                await conn.execute(
                    text(
                        """
                        SELECT c.conname, array_length(c.conkey, 1), CAST(c.confdeltype AS text),
                               fn.nspname, fc.relname
                          FROM pg_constraint c
                          JOIN pg_class fc ON fc.oid = c.confrelid
                          JOIN pg_namespace fn ON fn.oid = fc.relnamespace
                         WHERE c.conrelid = (:s || '.putaway_captures')::regclass
                           AND c.contype = 'f'
                        """
                    ),
                    {"s": SCHEMA},
                )
            ).all()
        assert {k[0]: (k[3], k[4]) for k in keys} == {
            "fk_putaway_captures_move_id_inventory_moves": (SCHEMA, "inventory_moves"),
            "fk_putaway_captures_location_id_locations": (SCHEMA, "locations"),
            "fk_putaway_captures_suggested_location_id_locations": (SCHEMA, "locations"),
            "fk_putaway_captures_worker_id_workers": (SCHEMA, "workers"),
        }
        # One column each (no composite key to drag a tenant across), RESTRICT (never a cascade
        # that deletes proof), and never to another service's schema (ADR-0000 §10).
        assert all(k[1] == 1 and k[2] == "r" for k in keys)

    @pytest.mark.parametrize(
        ("index", "unique", "columns"),
        [
            ("uq_putaway_captures_org_move", True, "(organization_id, move_id)"),
            ("ix_putaway_captures_org_location", False, "(organization_id, location_id)"),
            ("ix_operations_putaway_captures_organization_id", False, "(organization_id)"),
        ],
    )
    async def test_every_index_leads_with_the_tenant(self, admin, index, unique, columns) -> None:
        async with admin.begin() as conn:
            definition = await scalar(
                conn,
                "SELECT indexdef FROM pg_indexes WHERE schemaname = :s "
                "AND tablename = 'putaway_captures' AND indexname = :i",
                s=SCHEMA,
                i=index,
            )
        assert columns in definition and ("UNIQUE" in definition) is unique, definition

    async def test_the_retention_purge_has_an_index_on_the_creation_time(self, admin) -> None:
        """`tools/purge_putaway_captures.py` counts and deletes `created_at < cutoff` across every
        tenant, in batches ordered `created_at, id`. Every other index leads with the tenant and
        none of them can serve that, so without this one each dry run and each batch of a long
        backlog scans the whole table. It is deliberately not tenant-leading, and is the only
        secondary index here that is not."""
        async with admin.begin() as conn:
            definition = await scalar(
                conn,
                "SELECT indexdef FROM pg_indexes WHERE schemaname = :s "
                "AND tablename = 'putaway_captures' AND indexname = :i",
                s=SCHEMA,
                i=PURGE_INDEX,
            )
        assert "(created_at)" in definition and "UNIQUE" not in definition, definition

    async def test_these_are_all_the_indexes_and_only_the_purge_one_is_not_tenant_leading(
        self, admin
    ) -> None:
        async with admin.begin() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE schemaname = :s AND tablename = 'putaway_captures'"
                    ),
                    {"s": SCHEMA},
                )
            ).all()
        assert {r[0] for r in rows} == {
            "pk_putaway_captures",
            "ix_operations_putaway_captures_organization_id",
            "uq_putaway_captures_org_move",
            "ix_putaway_captures_org_location",
            PURGE_INDEX,
        }
        not_tenant_leading = {r[0] for r in rows if "(organization_id" not in r[1]}
        assert not_tenant_leading == {"pk_putaway_captures", PURGE_INDEX}

    async def test_row_level_security_is_enabled_and_forced_with_one_tenant_policy(
        self, admin
    ) -> None:
        async with admin.begin() as conn:
            flags = (
                await conn.execute(
                    text(
                        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND c.relname = 'putaway_captures'"
                    ),
                    {"s": SCHEMA},
                )
            ).one()
            policies = (
                await conn.execute(
                    text(
                        "SELECT policyname, cmd, roles, qual, with_check FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = 'putaway_captures'"
                    ),
                    {"s": SCHEMA},
                )
            ).all()
        assert tuple(flags) == (True, True)
        [policy] = policies
        assert (policy.policyname, policy.cmd) == ("tenant_isolation", "ALL")
        assert list(policy.roles) == [settings().db_app_role]
        for clause in (policy.qual, policy.with_check):  # USING, and a WITH CHECK too
            assert re.search(r"organization_id = (operations\.)?current_org\(\)", clause), clause

    async def test_the_app_role_can_read_and_add_but_never_rewrite_or_erase(self, admin) -> None:
        app_role = settings().db_app_role
        async with admin.begin() as conn:
            granted = {
                p: await scalar(
                    conn,
                    "SELECT has_table_privilege(:r, :t, :p)",
                    r=app_role,
                    t=f"{SCHEMA}.putaway_captures",
                    p=p,
                )
                for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
            }
            site = await scalar(
                conn,
                "SELECT has_table_privilege(:r, :t, 'UPDATE')",
                r=app_role,
                t=f"{SCHEMA}.warehouses",
            )
        assert granted == {
            "SELECT": True,
            "INSERT": True,
            "UPDATE": False,
            "DELETE": False,
            "TRUNCATE": False,
        }
        assert site is True  # a warehouse's position is edited in place

    async def test_the_bypassrls_outbox_relay_cannot_reach_the_captures(self, admin) -> None:
        """The relay reads and updates `outbox` and nothing else: with BYPASSRLS, a stray grant here
        would put every worker's position one `SELECT` away from a role that ignores tenancy."""
        relay = settings().db_outbox_role
        async with admin.begin() as conn:
            privileges = [
                await scalar(
                    conn,
                    "SELECT has_table_privilege(:r, :t, :p)",
                    r=relay,
                    t=f"{SCHEMA}.putaway_captures",
                    p=p,
                )
                for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
            ]
            columns = await scalar(
                conn,
                "SELECT count(*) FROM information_schema.column_privileges WHERE table_schema = :s "
                "AND table_name = 'putaway_captures' AND grantee = :r",
                s=SCHEMA,
                r=relay,
            )
        assert privileges == [False] * 4 and columns == 0

    async def test_nobody_but_the_owner_and_the_app_role_holds_any_privilege(self, admin) -> None:
        async with admin.begin() as conn:
            grantees = set(
                (
                    await conn.execute(
                        text(
                            "SELECT DISTINCT grantee FROM information_schema.role_table_grants "
                            "WHERE table_schema = :s AND table_name = 'putaway_captures'"
                        ),
                        {"s": SCHEMA},
                    )
                )
                .scalars()
                .all()
            )
            owner = await scalar(
                conn,
                "SELECT relowner::regrole::text FROM pg_class WHERE oid = CAST(:t AS regclass)",
                t=f"{SCHEMA}.putaway_captures",
            )
        assert grantees == {owner, settings().db_app_role}
        assert "PUBLIC" not in grantees


# ------------------------------------------------------------------ grants re-applied


class TestAppendOnlyHoldsAcrossGrantReapplication:
    async def test_a_later_upgrade_re_revokes_what_the_grants_hand_back(self, admin) -> None:
        """`apply_schema_grants` re-grants UPDATE/DELETE on *every* table on each `alembic upgrade`
        and only re-revokes `audit_logs`; this table survives that because `migrations/env.py`
        revokes it again straight after. Simulate the re-grant, run a no-op upgrade, look."""
        app_role = settings().db_app_role
        table = f"{SCHEMA}.putaway_captures"
        async with admin.begin() as conn:
            await conn.execute(text(f'GRANT UPDATE, DELETE ON {table} TO "{app_role}"'))
            assert await scalar(
                conn, "SELECT has_table_privilege(:r, :t, 'UPDATE')", r=app_role, t=table
            )
        again = alembic("upgrade", "head")
        assert again.returncode == 0, again.stderr[-1500:]
        async with admin.begin() as conn:
            for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                assert not await scalar(
                    conn, "SELECT has_table_privilege(:r, :t, :p)", r=app_role, t=table, p=privilege
                ), privilege
            for privilege in ("SELECT", "INSERT"):
                assert await scalar(
                    conn, "SELECT has_table_privilege(:r, :t, :p)", r=app_role, t=table, p=privilege
                ), privilege

    async def test_a_down_and_up_cycle_leaves_the_same_privileges_and_one_policy(
        self, admin
    ) -> None:
        assert alembic("downgrade", BEFORE).returncode == 0
        assert alembic("upgrade", "head").returncode == 0
        app_role = settings().db_app_role
        async with admin.begin() as conn:
            assert not await scalar(
                conn,
                "SELECT has_table_privilege(:r, :t, 'DELETE')",
                r=app_role,
                t=f"{SCHEMA}.putaway_captures",
            )
            policies = await scalar(
                conn,
                "SELECT count(*) FROM pg_policies WHERE schemaname = :s "
                "AND tablename = 'putaway_captures' AND policyname = 'tenant_isolation'",
                s=SCHEMA,
            )
        assert policies == 1


# ------------------------------------------------------------------ additive: old code, old data


class TestTheRevisionIsAdditive:
    async def test_data_from_before_it_survives_and_gains_null_coordinates(self, admin) -> None:
        org = uuid.uuid4()
        assert alembic("downgrade", BEFORE).returncode == 0
        async with admin.begin() as conn:
            ids = await seed_site(conn, org)
            before = {
                t: await scalar(
                    conn, f"SELECT count(*) FROM {SCHEMA}.{t} WHERE organization_id = :o", o=org
                )
                for t in ("warehouses", "locations", "inventory_items", "inventory_moves")
            }
        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stderr[-2000:]
        async with admin.begin() as conn:
            after = {
                t: await scalar(
                    conn, f"SELECT count(*) FROM {SCHEMA}.{t} WHERE organization_id = :o", o=org
                )
                for t in ("warehouses", "locations", "inventory_items", "inventory_moves")
            }
            site = (
                await conn.execute(
                    text(f"SELECT lat_micro, lng_micro FROM {SCHEMA}.warehouses WHERE id = :wh"),
                    ids,
                )
            ).one()
            captures = await scalar(conn, f"SELECT count(*) FROM {SCHEMA}.putaway_captures")
        assert after == before
        assert tuple(site) == (None, None)  # not 0, 0: "no position yet"
        assert captures == 0

    async def test_a_writer_that_knows_nothing_of_the_new_columns_still_works(self, admin) -> None:
        """Code from before this revision (a rollback of the image, an old worker mid-deploy)
        inserts warehouses and moves exactly as it always did."""
        org = uuid.uuid4()
        async with admin.begin() as conn:
            await seed_site(conn, org)  # the old column lists, at head
            await conn.execute(
                text(
                    f"""
                    INSERT INTO {SCHEMA}.inventory_moves (id, organization_id, kind, reference_type,
                                                          note, occurred_on, created_at)
                    VALUES (gen_random_uuid(), :o, 'relocation', 'putaway', '', '2026-10-05',
                            now())
                    """
                ),
                {"o": org},
            )  # `reference_type` is free text: a put-away's move needs no DDL

    async def test_moves_need_no_schema_change_for_the_new_reference_type(self, admin) -> None:
        async with admin.begin() as conn:
            check = (
                (
                    await conn.execute(
                        text(
                            """
                            SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c
                             WHERE c.conrelid = CAST(:t AS regclass) AND c.contype = 'c'
                            """
                        ),
                        {"t": f"{SCHEMA}.inventory_moves"},
                    )
                )
                .scalars()
                .all()
            )
        assert not any("reference_type" in c for c in check)


# ------------------------------------------------------------------ downgrade


class TestDowngrade:
    async def test_an_empty_database_round_trips(self, admin) -> None:
        down = alembic("downgrade", BEFORE)
        assert down.returncode == 0, down.stderr[-1500:]
        async with admin.begin() as conn:
            assert await current_revision(conn) == BEFORE
            assert (
                await scalar(conn, "SELECT to_regclass(:t)", t=f"{SCHEMA}.putaway_captures") is None
            )
            gone = (
                await conn.execute(
                    text(
                        """
                        SELECT column_name FROM information_schema.columns
                         WHERE table_schema = :s AND table_name = 'warehouses'
                           AND column_name IN ('lat_micro', 'lng_micro')
                        """
                    ),
                    {"s": SCHEMA},
                )
            ).all()
            assert gone == []
            leftovers = await scalar(
                conn,
                """
                SELECT count(*) FROM pg_constraint c
                  JOIN pg_namespace n ON n.oid = c.connamespace
                 WHERE n.nspname = :s
                   AND c.conname IN ('ck_warehouses_coords_paired', 'ck_warehouses_lat_in_range',
                                     'ck_warehouses_lng_in_range')
                """,
                s=SCHEMA,
            )
            assert leftovers == 0
            assert await scalar(conn, "SELECT to_regclass(:i)", i=f"{SCHEMA}.{PURGE_INDEX}") is None
        up = alembic("upgrade", "head")
        assert up.returncode == 0, up.stderr[-1500:]
        async with admin.begin() as conn:
            assert await current_revision(conn) == _script().get_current_head()
            # The index comes back with the table it belongs to.
            assert (
                await scalar(conn, "SELECT to_regclass(:i)", i=f"{SCHEMA}.{PURGE_INDEX}")
                is not None
            )

    async def test_unplaced_warehouses_do_not_block_a_downgrade(self, admin) -> None:
        async with admin.begin() as conn:
            await seed_site(conn, uuid.uuid4())  # a warehouse, no coordinates, no capture
        assert alembic("downgrade", BEFORE).returncode == 0

    async def test_it_refuses_while_a_capture_exists_and_changes_nothing(self, admin) -> None:
        org = uuid.uuid4()
        async with admin.begin() as conn:
            await seed_site(conn, org, capture=True)
            head = await current_revision(conn)
        refused = alembic("downgrade", BEFORE)
        assert refused.returncode != 0
        assert "Refusing to downgrade" in refused.stderr
        assert "OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE" in refused.stderr
        assert "1 putaway capture(s)" in refused.stderr
        # No coordinate in the message the operator pastes into a ticket.
        assert "12971601" not in refused.stderr and "77594602" not in refused.stderr
        async with admin.begin() as conn:
            assert await current_revision(conn) == head
            assert await scalar(conn, f"SELECT count(*) FROM {SCHEMA}.putaway_captures") == 1

    async def test_it_refuses_while_a_warehouse_has_coordinates(self, admin) -> None:
        async with admin.begin() as conn:
            await seed_site(conn, uuid.uuid4(), coords=True)
        refused = alembic("downgrade", BEFORE)
        assert refused.returncode != 0 and "Refusing to downgrade" in refused.stderr
        async with admin.begin() as conn:
            kept = await scalar(
                conn, f"SELECT count(*) FROM {SCHEMA}.warehouses WHERE lat_micro IS NOT NULL"
            )
        assert kept == 1

    async def test_the_override_destroys_them_deliberately(self, admin) -> None:
        async with admin.begin() as conn:
            await seed_site(conn, uuid.uuid4(), coords=True, capture=True)
        allowed = alembic("downgrade", BEFORE, env=ALLOW)
        assert allowed.returncode == 0, allowed.stderr[-1500:]
        async with admin.begin() as conn:
            assert await current_revision(conn) == BEFORE
            assert (
                await scalar(conn, "SELECT to_regclass(:t)", t=f"{SCHEMA}.putaway_captures") is None
            )
            # The moves the captures belonged to are untouched: only the proof went.
            assert await scalar(conn, f"SELECT count(*) FROM {SCHEMA}.inventory_moves") == 1
        # …and the schema can be brought back up over that data.
        assert alembic("upgrade", "head").returncode == 0

    async def test_the_override_must_be_exactly_one(self, admin) -> None:
        async with admin.begin() as conn:
            await seed_site(conn, uuid.uuid4(), capture=True)
        for value in ("0", "true", "yes", ""):
            refused = alembic(
                "downgrade", BEFORE, env={"OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE": value}
            )
            assert refused.returncode != 0, value


# ------------------------------------------------------------------ the repository's own checks


class TestHeadsAndDrift:
    def test_there_is_a_single_head_and_this_revision_is_on_its_path(self) -> None:
        script = _script()
        heads = script.get_heads()
        assert len(heads) == 1, heads
        on_the_path = {r.revision for r in script.iterate_revisions(heads[0], "base")}
        assert REV in on_the_path

    def test_alembic_check_sees_no_drift_between_the_models_and_the_migrations(self) -> None:
        checked = alembic("check")
        assert checked.returncode == 0, checked.stdout[-1500:] + checked.stderr[-1500:]
        assert "No new upgrade operations detected" in checked.stdout + checked.stderr

    def test_the_migration_names_the_lock_timeout_it_sets(self) -> None:
        source = next((SERVICE_DIR / "migrations" / "versions").glob(f"{REV}_*.py")).read_text(
            "utf-8"
        )
        assert "SET LOCAL lock_timeout" in source and 'LOCK_TIMEOUT = "5s"' in source


async def test_a_migration_that_cannot_get_its_lock_fails_whole_and_leaves_nothing_half_applied(
    admin,
) -> None:
    """`warehouses` is locked by someone else: the upgrade gives up after its five seconds instead
    of queueing every later query behind it — and, being one transaction, adds neither column nor
    the new table."""
    assert alembic("downgrade", BEFORE).returncode == 0
    async with admin.connect() as holder:
        await holder.execute(text(f"LOCK TABLE {SCHEMA}.warehouses IN ACCESS EXCLUSIVE MODE"))
        started = time.monotonic()
        blocked = await asyncio.to_thread(alembic, "upgrade", "head")
        elapsed = time.monotonic() - started
        assert blocked.returncode != 0
        assert "lock timeout" in blocked.stderr.lower(), blocked.stderr[-1500:]
        assert 3 < elapsed < 60, elapsed
        await holder.rollback()
    async with admin.begin() as conn:
        assert await current_revision(conn) == BEFORE
        assert await scalar(conn, "SELECT to_regclass(:t)", t=f"{SCHEMA}.putaway_captures") is None
        columns = await scalar(
            conn,
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = :s "
            "AND table_name = 'warehouses' AND column_name IN ('lat_micro', 'lng_micro')",
            s=SCHEMA,
        )
        assert columns == 0
    assert alembic("upgrade", "head").returncode == 0
