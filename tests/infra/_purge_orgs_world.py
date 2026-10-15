"""A catalog-driven fixture world for `tools/purge_test_orgs.py`, for the opt-in real-DB tests.

**Why it is generated, not written out.** The tool's whole claim is that it finds every table with
an `organization_id` and deletes in a foreign-key-safe order. A hand-written fixture of twenty
tables proves the tool handles twenty tables. This builds one row per organisation in **every**
table the tool will touch, from the live catalog (`information_schema` for columns,
`pg_constraint` for foreign keys and enumerated `CHECK`s), parents before children, each insert in
its own savepoint: a table whose own constraints refuse a synthesised row is recorded in `skipped`
with its SQLSTATE and the tests assert that the set stays small and that the tables they name
explicitly were seeded.

The identity tables are seeded by hand (below) because the cases that matter there are specific: a
user in one organisation only, a user shared with an organisation that stays, a user shared
between two selected organisations, and a staff-flagged user.

Nothing here is imported by the tool. It talks to a database only through the connection it is
given and writes only rows keyed to the random organisation ids it makes up.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "purge_test_orgs.py"
# The tool imports its sibling `_db_common`, which is on `sys.path` when run as a script.
sys.path.insert(0, str(TOOL.parent))


def load_tool() -> ModuleType:
    """The tool, loaded the way the other `tests/infra` modules load theirs (it is a script, not a
    package)."""
    if "purge_test_orgs" in sys.modules:
        return sys.modules["purge_test_orgs"]
    spec = importlib.util.spec_from_file_location("purge_test_orgs", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["purge_test_orgs"] = module
    spec.loader.exec_module(module)
    return module


purge = load_tool()
Table = purge.Table


# ---------------------------------------------------------------------------------------- catalog
@dataclass(frozen=True)
class Col:
    name: str
    data_type: str
    udt: str
    nullable: bool
    has_default: bool
    maxlen: int | None
    generated: bool


#: `CHECK ((state)::text = ANY ((ARRAY['a'::character varying, 'b'::character varying])::text[]))`
_ENUM = re.compile(r"\(\((\w+)\)::text = ANY \(\(?ARRAY\[(.*?)\]\)?(?:::text\[\])?\)\)")
_ITEM = re.compile(r"'((?:[^']|'')*)'::")


class Skip(Exception):
    """A table the synthesiser cannot (or should not) give a row to; the reason is the message."""


@dataclass
class World:
    """What was seeded. `orgs` and `users` are keyed by the roles they play in the tests."""

    orgs: dict[str, uuid.UUID] = field(default_factory=dict)
    users: dict[str, uuid.UUID] = field(default_factory=dict)
    seeded: set[Any] = field(default_factory=set)
    skipped: dict[Any, str] = field(default_factory=dict)
    #: Tables given a second row that points at the first through their own foreign key.
    self_referencing: list[Any] = field(default_factory=list)
    #: Rows made in tables outside the purge's reach (a global parent such as a permission), which
    #: the fixture removes itself afterwards so a shared database is left as it was found.
    globals_made: list[tuple[Any, dict[str, Any]]] = field(default_factory=list)

    @property
    def every_org(self) -> list[uuid.UUID]:
        return list(self.orgs.values())


class Seeder:
    def __init__(self, conn: AsyncConnection, plan: Any, fks: list[Any]) -> None:
        self.conn = conn
        self.plan = plan
        self.fks_by_child: dict[Any, list[Any]] = {}
        for fk in fks:
            self.fks_by_child.setdefault(fk.child, []).append(fk)
        self.referenced: dict[Any, set[str]] = {}
        for fk in fks:
            self.referenced.setdefault(fk.parent, set()).update(fk.parent_cols)
        self.cols: dict[Any, list[Col]] = {}
        self.enums: dict[tuple[Any, str], list[str]] = {}
        self.registry: dict[tuple[Any, uuid.UUID | None], dict[str, Any]] = {}
        self.world = World()
        self._n = 0

    # ----------------------------------------------------------------------------------- loading
    async def load(self) -> None:
        schemas = sorted(
            {t.schema for t in self.plan.nodes} | {t.schema for t in self.fks_parents()}
        )
        rows = (
            await self.conn.execute(
                text(
                    "SELECT table_schema, table_name, column_name, data_type, udt_name, "
                    "is_nullable, column_default IS NOT NULL, character_maximum_length, "
                    "(is_generated = 'ALWAYS' OR is_identity = 'YES') "
                    "FROM information_schema.columns WHERE table_schema = ANY(:s) "
                    "ORDER BY table_schema, table_name, ordinal_position"
                ),
                {"s": schemas},
            )
        ).all()
        for r in rows:
            self.cols.setdefault(Table(r[0], r[1]), []).append(
                Col(r[2], r[3], r[4], r[5] == "YES", bool(r[6]), r[7], bool(r[8]))
            )
        checks = (
            await self.conn.execute(
                text(
                    "SELECT n.nspname, c.relname, pg_get_constraintdef(con.oid) "
                    "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE con.contype = 'c' AND n.nspname = ANY(:s)"
                ),
                {"s": schemas},
            )
        ).all()
        for schema, table, definition in checks:
            for m in _ENUM.finditer(definition):
                items = [i.replace("''", "'") for i in _ITEM.findall(m.group(2))]
                if items:
                    self.enums[(Table(schema, table), m.group(1))] = items

    def fks_parents(self) -> set[Any]:
        return {fk.parent for fks in self.fks_by_child.values() for fk in fks}

    # ------------------------------------------------------------------------------------ values
    def _param(self) -> str:
        self._n += 1
        return f"p{self._n}"

    def _bind(self, col: Col, value: Any, params: dict[str, Any]) -> str:
        name = self._param()
        params[name] = value
        if col.data_type in ("character varying", "text", "character") or col.udt == "citext":
            return f"CAST(:{name} AS text)"
        if col.data_type in ("jsonb", "json"):
            return f"CAST(:{name} AS {col.udt})"
        return f":{name}"

    def _synthesise(self, table: Any, col: Col, tag: str, params: dict[str, Any]) -> str:
        allowed = self.enums.get((table, col.name))
        if col.data_type == "uuid":
            return self._bind(col, uuid.uuid4(), params)
        if col.data_type in ("character varying", "text", "character") or col.udt == "citext":
            if allowed:
                return self._bind(col, allowed[0], params)
            value = {
                "email": f"{tag}@example.com",
                "domain": f"{tag}.example.com",
                "currency": "INR",
                "country": "IN",
            }.get(col.name, tag)
            if col.maxlen:
                value = value[: col.maxlen]
            return self._bind(col, value, params)
        simple = {
            "jsonb": "CAST('{}' AS jsonb)",
            "json": "CAST('{}' AS json)",
            "timestamp with time zone": "now()",
            "timestamp without time zone": "localtimestamp",
            "date": "current_date",
            "time without time zone": "localtime",
            "boolean": "false",
            "integer": "1",
            "smallint": "1",
            "bigint": "1",
            "numeric": "1",
            "double precision": "1",
            "real": "1",
            "bytea": "CAST('\\x01' AS bytea)",
            "inet": "CAST('127.0.0.1' AS inet)",
            "interval": "CAST('1 day' AS interval)",
        }
        if col.data_type in simple:
            return simple[col.data_type]
        if col.data_type == "ARRAY":
            return f"CAST('{{}}' AS \"{col.udt}\")"
        raise Skip(f"unsupported type {col.data_type}/{col.udt} on {col.name}")

    # ------------------------------------------------------------------------------------ insert
    async def insert(
        self,
        table: Any,
        *,
        org: uuid.UUID | None,
        overrides: dict[str, Any] | None = None,
        register_as: uuid.UUID | str | None = "org",
    ) -> dict[str, Any]:
        """Insert one row, in a savepoint. Returns the values later rows may point at; raises
        `Skip` for a table that cannot be seeded."""
        overrides = dict(overrides or {})
        for name, value in list(overrides.items()):
            if isinstance(value, Fresh):
                made = await self.insert(
                    value.table, org=org, overrides=value.overrides, register_as=None
                )
                overrides[name] = made[value.column]
        tag = uuid.uuid4().hex
        values: dict[str, str] = {}
        params: dict[str, Any] = {}
        columns = self.cols[table]
        by_name = {c.name: c for c in columns}

        for fk in self.fks_by_child.get(table, []):
            if fk.child == fk.parent or all(c in overrides for c in fk.child_cols):
                continue
            parent_org = org if fk.parent in self.plan.nodes else None
            parent_row = self.registry.get((fk.parent, parent_org))
            if parent_row is None and fk.parent not in self.plan.nodes:
                parent_row = await self._ensure_global(fk.parent)
            if parent_row is None:
                if all(by_name[c].nullable for c in fk.child_cols):
                    continue
                raise Skip(f"parent {fk.parent} has no seeded row")
            for child_col, parent_col in zip(fk.child_cols, fk.parent_cols, strict=True):
                if child_col not in overrides and parent_col in parent_row:
                    overrides[child_col] = parent_row[parent_col]

        for col in columns:
            if col.generated:
                continue
            if col.name in overrides:
                values[col.name] = self._bind(col, overrides[col.name], params)
            elif col.name == "organization_id" and org is not None:
                values[col.name] = self._bind(col, org, params)
            elif col.nullable or col.has_default:
                continue
            else:
                values[col.name] = self._synthesise(table, col, tag, params)

        returning = sorted(self.referenced.get(table, set()))
        columns_sql = ", ".join(f'"{c}"' for c in values)
        # Identifiers come from the catalog; every value is a bound parameter or a constant.
        sql = f"INSERT INTO {table.sql} ({columns_sql}) VALUES ({', '.join(values.values())})"  # noqa: S608
        if returning:
            sql += f" RETURNING {', '.join(f'"{c}"' for c in returning)}"
        try:
            async with self.conn.begin_nested():
                result = await self.conn.execute(text(sql), params)
                got = dict(zip(returning, result.one(), strict=True)) if returning else {}
        except DBAPIError as exc:
            cause = getattr(exc.orig, "__cause__", None) or exc.orig
            state = getattr(cause, "sqlstate", "?")
            what = getattr(cause, "constraint_name", None) or getattr(cause, "table_name", "")
            raise Skip(f"{state} {what}") from None

        row = {**overrides, **got}
        if org is not None and "organization_id" in by_name:
            row["organization_id"] = org
        if register_as is not None:
            key_org = org if register_as == "org" else register_as
            self.registry[(table, key_org)] = row  # type: ignore[index]
        return row

    async def _ensure_global(self, table: Any) -> dict[str, Any] | None:
        existing = self.registry.get((table, None))
        if existing is not None:
            return existing
        try:
            row = await self.insert(table, org=None, register_as=None)
        except Skip:
            return None
        self.registry[(table, None)] = row
        self.world.globals_made.append((table, row))
        return row

    async def try_insert(self, table: Any, *, org: uuid.UUID | None, **kw: Any) -> None:
        fixup = FIXUPS.get((table.schema, table.name))
        if fixup:
            kw["overrides"] = {**fixup(self), **kw.get("overrides", {})}
        try:
            await self.insert(table, org=org, **kw)
            self.world.seeded.add(table)
        except Skip as exc:
            if table not in self.world.seeded:
                self.world.skipped[table] = str(exc)


# ------------------------------------------------------------------------------------------- build
def _not_in(seeder: Seeder, table: tuple[str, str], column: str, *excluded: str) -> str:
    allowed = seeder.enums[(Table(*table), column)]
    return next(v for v in allowed if v not in excluded)


@dataclass(frozen=True)
class Fresh:
    """An override meaning "a second, new row of `table`, and its `column`": for a CHECK that
    needs two different parents (`from_warehouse_id <> to_warehouse_id`), where the registry
    holds only one row per table and organisation."""

    table: Any
    column: str = "id"
    overrides: dict[str, Any] | None = None


#: Tables whose own CHECK constraints relate columns to each other in ways a type-driven value
#: cannot satisfy by accident. Each entry is the smallest override that makes one valid row; the
#: tests do not depend on what the row means, only on there being one to delete.
FIXUPS: dict[tuple[str, str], Callable[[Seeder], dict[str, Any]]] = {
    ("approval", "approval_policies"): lambda s: {
        "request_type": "leave.request.create",
        "sla_minutes": 60,
    },
    ("approval", "approval_requests"): lambda s: {"request_type": "leave.request.create"},
    ("approval", "approval_steps"): lambda s: {"delegation_id": None, "reason": "unassigned"},
    ("payment", "charges"): lambda s: {"merchant_account_id": None},
    # A shop's own domain, not an `example.com` one: the listing's hints read domains.
    ("account", "org_domains"): lambda s: {
        "domain": f"shop-{uuid.uuid4().hex[:10]}.acme-textiles.in"
    },
    ("approval", "delegations"): lambda s: {
        "starts_at": datetime.now(UTC),
        "ends_at": datetime.now(UTC) + timedelta(days=1),
    },
    ("leave", "leave_ledger_entries"): lambda s: {
        "entry_type": _not_in(
            s, ("leave", "leave_ledger_entries"), "entry_type", "consumption", "reversal"
        ),
        "leave_request_id": None,
    },
    ("finance", "itc_ledger_entries"): lambda s: {"kind": "accrual", "amount_minor": 100},
    ("payment", "merchant_accounts"): lambda s: {"status": "removed"},
    ("notification", "messaging_rate_cards"): lambda s: {"channel": "whatsapp"},
    ("operations", "item_attributes"): lambda s: {"value_type": "boolean", "value_boolean": False},
    ("operations", "inventory_reservations"): lambda s: {"quantity_milli": 10},
    ("operations", "pick_task_lines"): lambda s: {"quantity_milli": 10},
    # CHECKs added after this seeder was written, each the smallest override that satisfies it.
    ("operations", "stock_transfers"): lambda s: {
        "to_warehouse_id": Fresh(Table("operations", "warehouses"))
    },
    ("operations", "walk_edges"): lambda s: {
        "to_node_id": Fresh(Table("operations", "walk_nodes"), overrides={"location_id": None})
    },
    ("operations", "purchase_requisitions"): lambda s: {
        "status": "DRAFT",
        "converted_po_id": None,
    },
    ("operations", "production_materials"): lambda s: {
        "consumed_milli": 0,
        "returned_milli": 0,
        "wasted_milli": 0,
    },
    ("operations", "job_work_materials"): lambda s: {
        "received_milli": 0,
        "rejected_milli": 0,
        "shortage_milli": 0,
    },
    ("operations", "production_picks"): lambda s: {
        "consumed_milli": 0,
        "returned_milli": 0,
        "wasted_milli": 0,
    },
    ("operations", "stock_transfer_lines"): lambda s: {
        "received_milli": 0,
        "shortage_milli": 0,
        "pending_shortage_milli": 0,
    },
    ("operations", "mrp_demands"): lambda s: {"reserved_milli": 0},
    ("operations", "goods_receipts"): lambda s: {"status": "POSTED"},
    ("operations", "challans"): lambda s: {"kind": "DELIVERY"},
    ("notification", "campaigns"): lambda s: {
        "channel": "whatsapp",
        "audience": "all_sendable",
        "list_id": None,
        "segment_id": None,
    },
    ("subscription", "dunning_cycles"): lambda s: {
        "opened_at": datetime.now(UTC),
        "grace_ends_at": datetime.now(UTC) + timedelta(days=1),
    },
}

IDENTITY_EXPLICIT = {
    ("account", "organizations"),
    ("account", "users"),
    ("account", "memberships"),
    ("auth", "sessions"),
}


async def build_world(conn: AsyncConnection) -> World:
    """Four organisations — `a`, `b`, `c` (the ones the tests select from) and `anchor` (never
    selected, so a survivor always exists) — with one row in every table the tool reaches, and a
    cast of users arranged to exercise each user rule."""
    plan = await purge.build_plan(conn)
    fks = await purge.discover_fks(conn)
    seeder = Seeder(conn, plan, fks)
    await seeder.load()
    world = seeder.world
    orgs, users = world.orgs, world.users
    for key in ("a", "b", "c", "anchor", "real"):
        orgs[key] = uuid.uuid4()
    org_table = Table("account", "organizations")
    user_table = Table("account", "users")
    member_table = Table("account", "memberships")
    session_table = Table("auth", "sessions")
    #: The per-user auth tables, found the way the tool finds them: identity tables with no
    #: organisation column of their own (sessions is seeded below with its active organisation).
    user_tables = sorted(
        t
        for t, n in plan.nodes.items()
        if n.identity
        and not n.has_org_column
        and t.schema == purge.AUTH_SCHEMA
        and t != session_table
        and t.name != "refresh_tokens"
    )

    for key, org in orgs.items():
        real = key == "real"
        await seeder.try_insert(
            org_table,
            org=None,
            overrides={
                "id": org,
                "slug": f"latest-textiles-{org.hex[:8]}" if real else f"e2e-{key}-{org.hex[:8]}",
                "name": "Latest Textiles" if real else f"e2e {key}",
                "status": "active",
            },
            register_as=org,
        )

    #: name -> (is_staff, organisations it belongs to, email domain)
    cast: dict[str, tuple[bool, list[str], str]] = {
        "a_only": (False, ["a"], "example.com"),
        "a_staff": (True, ["a"], "example.com"),
        "ab": (False, ["a", "b"], "example.com"),
        "ac": (False, ["a", "c"], "example.com"),
        "b_only": (False, ["b"], "example.com"),
        "c_only": (False, ["c"], "example.com"),
        "anchor_user": (False, ["anchor"], "example.com"),
        "real_owner": (False, ["real"], "acme-textiles.in"),
    }
    for name, (is_staff, member_of, domain) in cast.items():
        users[name] = uuid.uuid4()
        await seeder.try_insert(
            user_table,
            org=None,
            overrides={
                "id": users[name],
                "email": f"{name}-{users[name].hex[:8]}@{domain}",
                "is_staff": is_staff,
            },
            register_as=None,
        )
        for key in member_of:
            await seeder.try_insert(
                member_table,
                org=orgs[key],
                overrides={"user_id": users[name], "organization_id": orgs[key]},
                register_as=orgs[key],
            )
        for table in user_tables:
            await seeder.try_insert(
                table, org=None, overrides={"user_id": users[name]}, register_as=None
            )
        active = orgs[member_of[0]]
        await seeder.try_insert(
            session_table,
            org=None,
            overrides={"user_id": users[name], "active_org_id": active},
            register_as=active,
        )

    # Everything else, parents before children, for every organisation.
    for table in reversed(plan.order):
        if (table.schema, table.name) in IDENTITY_EXPLICIT or table in user_tables:
            continue
        for org in orgs.values():
            await seeder.try_insert(table, org=org)

    # A second row that points at the first, in every table that references itself (a location in a
    # location, a category in a category, a roll cut from a roll): a single DELETE has to remove a
    # whole self-referencing set although the key says RESTRICT.
    world.self_referencing = []
    for table, table_fks in sorted(seeder.fks_by_child.items()):
        if (table.schema, table.name) in IDENTITY_EXPLICIT:
            continue
        for fk in table_fks:
            if fk.child != fk.parent or table not in world.seeded:
                continue
            for org in orgs.values():
                first = seeder.registry.get((table, org))
                if first is None or not all(p in first for p in fk.parent_cols):
                    continue
                pointing = dict(zip(fk.child_cols, (first[p] for p in fk.parent_cols), strict=True))
                try:
                    await seeder.insert(table, org=org, overrides=pointing, register_as=None)
                except Skip:
                    continue
                if table not in world.self_referencing:
                    world.self_referencing.append(table)
    world.seeded.update(
        t for t in (org_table, user_table, member_table, session_table) if t in world.seeded
    )
    return world


async def add_membership(conn: AsyncConnection, user: uuid.UUID, org: uuid.UUID) -> None:
    """One more membership, as a signup or an invitation would make it (a test's way of changing
    the world mid-run)."""
    plan = await purge.build_plan(conn)
    seeder = Seeder(conn, plan, await purge.discover_fks(conn))
    await seeder.load()
    await seeder.insert(
        Table("account", "memberships"),
        org=org,
        overrides={"user_id": user, "organization_id": org},
        register_as=None,
    )


async def remove_globals(conn: AsyncConnection, world: World) -> None:
    for table, row in reversed(world.globals_made):
        key = next(iter(row), None)
        if key is None:
            continue
        statement = f'DELETE FROM {table.sql} WHERE "{key}" = :v'  # noqa: S608
        await conn.execute(text(statement), {"v": row[key]})
