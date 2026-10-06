#!/usr/bin/env python3
"""Remove dummy, demo and test organisations from a database — owner-run, destructive, and
deliberately hard to run by accident (ADR-0036).

**What it is for.** The edge box accumulates organisations nobody wants: every end-to-end and
smoke run, every demo, every half-finished signup leaves one behind, with its users, its
subscriptions, its invoices and its audit trail spread over thirty-odd service schemas. There is
no in-app way to delete an organisation (a tenant's data is append-only and audited by design),
so this is the one sanctioned way: a tool the **owner runs, on the box, after a backup**. It is
not run by CI, not by a deploy, not by an agent.

**It never selects anything for you.** With no organisation named it only *lists* — every
organisation, its members, its subscriptions, its last activity, and a hint whether it looks like
test data (a name like `e2e-…`, `smoke`, `buyer-…`, `test`, `demo`; throwaway email domains;
nothing paid and nothing active). A hint is a suggestion printed next to a name; it is never an
input to a deletion. You choose the ids and you type them.

    # 1. look (read-only; prints names to your terminal)
    python tools/purge_test_orgs.py              # --json, or --candidates for an ids file to edit
    # 2. say what you would delete, and see exactly what it would take (read-only)
    python tools/purge_test_orgs.py --org-id <uuid> [--org-id <uuid> …] [--ids-file FILE] \\
        [--keep-file FILE]
    # 3. delete (after `make edge-backup`)
    python tools/purge_test_orgs.py --org-id <uuid> … --apply --confirm-count N \\
        --i-took-a-backup [--i-know-this-is-the-live-database]

Step 2 is not an estimate. It runs the same per-table statements as step 3 as `SELECT count(*)` in
a read-only transaction, so `would_delete` is what `--apply` deletes, table by table (with several
organisations, each is counted as it will be found when its turn comes, without the rows an earlier
one in the run has already taken: a user in two of them is counted once). `--explain` prints the
plan itself: every table, the SQL that selects its rows, and the order.

**What `--apply` refuses.** Before it opens a transaction that can write it checks, and exits 2
without touching anything if any of these is false:

* `--confirm-count N` is given and equals the number of distinct organisations selected (a typo
  in the id list or in the count is caught here);
* `--i-took-a-backup` is given (an attestation: the tool cannot see your backup, so it makes you
  say it);
* on a live database `--i-know-this-is-the-live-database` is given. "Live" is `ENVIRONMENT` of
  `production`, `staging` or `uat` (the set `ServiceSettings` already treats as a real deployment)
  **or** a database host that is not provably this machine (a named loopback address; a DSN with
  no host, which is a unix socket, counts as live); the flag is required for `--apply` only,
  listing and the dry run never need it. The tool can only see the DSN, so an `ssh -L` tunnel to
  the box looks like `localhost`: run it on the box, not through one;
* no selected id is the platform's nil-UUID sentinel organisation, or is named by `--keep` /
  `--keep-file`;
* at least one organisation would remain (the tool does not empty a platform), and no selected
  organisation is the parent of one that would remain;
* the connecting role bypasses row-level security and can delete (and, where a table carries a
  delete guard, own it). Under RLS an unauthorised delete does not fail, it silently matches
  nothing, which is the worst thing a purge can do.

**Which role, and why `opsgrids_admin`.** Every table here is owned by the migration/owner role
`opsgrids_admin` (BYPASSRLS; ADR-0002 §2), and it is the only role that can both read an
organisation across every schema and still hold `DELETE` on the append-only tables (those are
append-only by privilege: `UPDATE`/`DELETE` are revoked from each *service* role, not from the
owner). There is no narrower role to prefer, as for `tools/purge_putaway_captures.py`. What
contains it instead: explicit ids only, one transaction per organisation, the refusals above, and
counts-only output.

**Append-only guards, and how they are got past — narrowly.** Some tables also carry a row
trigger that raises on `DELETE` (finance's filed returns, payment's `refunds` and
`provider_events`, subscription transitions, approval steps, …). The tool discovers every such
trigger from `pg_trigger`, and **only on tables where this organisation has rows**, it runs
`ALTER TABLE … DISABLE TRIGGER <that trigger>` *inside the organisation's own transaction*, deletes,
then `ENABLE TRIGGER`s the same ones again and checks `pg_trigger` says they are back to the state
they were found in, still inside the transaction, before it commits. DDL is transactional: if
anything fails the disable is rolled back with the deletes, and no other session ever sees a
trigger off (the `ALTER` also holds a lock that keeps writers out of those tables for the
duration). It does **not** use `session_replication_role = replica`, which would switch every
trigger and foreign-key check off for the whole session and needs a superuser the tool does not
ask for.

**What it deletes.** Every table in every non-system schema with an `organization_id` column,
found from the catalog (`pg_class`, `pg_attribute`: not `information_schema`, which hides a table
the role has no privilege on) at run time, so nothing is a hard-coded list and a service added next
month is covered. A table the role cannot read and delete from is not skipped: the preflight
refuses the run and names it. `--explain` lists the tables that are *not* reached (no
`organization_id` and no foreign key into one), and a real-database test pins that list, so a
new tenant table keyed some other way fails the suite instead of being quietly left alone. Tables
without that column that hang off those rows by a foreign key
(`account.membership_roles`, `account.role_permissions`, …) are found from the foreign keys and
deleted by joining to their parent. Order is foreign-key-safe: children before parents, from the
catalog's own constraint graph (the organisation row goes after everything that references it). Its
sessions and their refresh tokens (`auth.sessions.active_org_id`) go with it.

**Users.** A user is global; membership is per organisation (ADR-0000 §1). Removing an
organisation removes its memberships. A user is deleted too, with their credentials, sessions and
second factors, **only if** every membership they had was in the organisations being removed, the
account is not flagged `is_staff`, and it is not named by `--keep-user`. A user who belongs to any
organisation that stays is never touched. `--keep-orphan-users` turns user deletion off entirely
(memberships go, accounts stay). Operator (staff) accounts live in `auth.staff_accounts`, not here,
and are never touched. The users about to be deleted are locked and asked about again just before
the delete, so a membership added to one in the meantime stops the run instead of being cascaded
away with the user.

**What it leaves, on purpose.** `processed_events` (a per-consumer idempotency ledger with no
organisation on it, so nothing ties a row to a tenant), the global catalogue, and plan versions
restricted to a deleted organisation (`catalog.plan_versions.restricted_to_org_id`: a published
plan version is immutable, CLAUDE.md). The plan versions are counted in the output as
`left_in_place`; `processed_events` is named here and in the RUNBOOK.

**What it prints: counts only.** One JSON line per organisation with the per-table row counts, a
`live_signals` block (members, subscriptions by state, captured payments: the facts a name cannot
fake, so an id that is a paying customer stands out in the dry run) and a final summary; never a
row, an email, a name or the connection string (only `host:port/database` so you can see where it
is aimed). The one exception is the interactive listing in step 1, which prints organisation names
to *your* terminal because you cannot choose an organisation by id alone. **A name is a
stranger's text** (signup is public), so the listing and the candidates file print it through
`inert`: anything that is not a printable character, which includes every line break, escape and
separator, becomes `?`, and it is cut to 80 characters. An ids file is read the same careful way:
a line with a stray control or separator character in it is refused, and ids must be the
canonical hyphenated form. After the counts it prints, but does not act on, what lives outside the
database (object storage under the organisation's prefix, provider-side objects, certificates,
in-flight events).

**Safe to re-run and to interrupt.** One transaction per organisation: an interrupted run leaves
each organisation either wholly deleted or untouched. Organisations go in id order, except that a
sub-organisation always goes before its parent when you select both (`parent_org_id` is
`RESTRICT`), and the dry run uses that same order. Running it again for an organisation that is
gone finds nothing and says so: `already-clean` when an earlier purge of that id is on record,
`not-found` when none is (a typo cannot delete anything, and does not look like a success). The
audit trail of the deletion itself is one row per organisation
in `auth.audit_logs`, written under the platform sentinel organisation in the same transaction
(the deleted organisation's own audit rows are, of course, among what goes).

Run it on the box from the `account` image exactly as the RUNBOOK ("Removing test organisations")
shows; locally, point `DATABASE_URL` at an isolated database first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

ROOT = Path(__file__).resolve().parent.parent

#: The platform's own sentinel organisation (`services.PLATFORM_ORG` in auth, catalog, product):
#: operator actions and platform rows are filed under it. It is never a tenant and never deleted.
PLATFORM_ORG = uuid.UUID(int=0)

#: `ServiceSettings.model_post_init` treats these as real deployments; so does this tool.
LIVE_ENVIRONMENTS = frozenset({"production", "staging", "uat"})
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: Schemas that are not services'. PostGIS ships `tiger`, `tiger_data` and `topology`.
_SYSTEM_SCHEMAS = ("information_schema", "public", "tiger", "tiger_data", "topology")

#: Transaction-scoped: an organisation's deletion waits this long for a lock, then fails and
#: rolls back rather than queueing behind (and in front of) live traffic.
LOCK_TIMEOUT = "10s"
#: A runaway statement is stopped; the organisation's transaction rolls back whole.
STATEMENT_TIMEOUT = "10min"
#: Two runs at once would interleave their `ALTER TABLE`s; the second is refused instead.
_ADVISORY_LOCK_KEY = 7_240_913_001

# The identity tables this tool must know by name: an organisation's root row, its memberships,
# the global user, and the auth schema's per-user and per-session rows. `_check_identity_shape`
# refuses to run if any is missing, rather than guess.
ORGS = ("account", "organizations")
MEMBERSHIPS = ("account", "memberships")
USERS = ("account", "users")
AUTH_SCHEMA = "auth"
SESSIONS = ("auth", "sessions")
AUDIT = ("auth", "audit_logs")

#: Columns that point at an organisation but are not named `organization_id`, and what the tool
#: does about each. A test scans a migrated database for any `*org*_id` column not listed here, so
#: a new one cannot slip in unnoticed.
KNOWN_ORG_REFERENCES: dict[tuple[str, str, str], str] = {
    ("account", "organizations", "id"): "the root row; deleted last",
    ("account", "organizations", "parent_org_id"): "refused if a surviving organisation has it",
    ("auth", "sessions", "active_org_id"): "sessions active in the organisation are deleted",
    ("catalog", "plan_versions", "restricted_to_org_id"): "left: a plan version is immutable",
}
#: Reported (counted, never deleted) so the owner can see what a purge leaves in the catalogue.
LEFT_IN_PLACE: tuple[tuple[str, str, str], ...] = (
    ("catalog", "plan_versions", "restricted_to_org_id"),
)

#: Tables every service schema has that no purge reaches: Alembic's version row, and the
#: per-consumer idempotency ledger (a row per event id, with no organisation on it).
BOOKKEEPING_TABLES = ("alembic_version", "processed_events")

#: A table with rows for the organisation -> what lives outside the database as a result.
EXTERNAL_LEFTOVERS: dict[str, str] = {
    "document.assets": "object storage: images uploaded by this organisation under the prefix "
    "{bucket}/{org}/ (the document service's keys are `<organization>/<asset>`)",
    "storefront.custom_domain_claims": "DNS: custom domains this organisation pointed at the box; "
    "Caddy keeps any certificate it issued (harmless, it expires)",
    "account.org_domains": "DNS: verified or claimed domains for this organisation",
    "payment.mandates": "payment provider: mandates created at Razorpay/Cashfree stay in their "
    "dashboard",
    "payment.merchant_accounts": "payment provider: the organisation's connected provider account "
    "and its webhook registration stay at the provider",
    "payment.charges": "payment provider: orders and payments stay in the provider's dashboard",
    "notification.provider_accounts": "messaging provider: connected WhatsApp/email sender "
    "identities and webhooks stay registered with Meta or the mail provider",
}
GENERAL_LEFTOVERS = (
    "RabbitMQ: queues are not per organisation. A message already published about a removed "
    "organisation is consumed harmlessly or dead-lettered; do not requeue a dead letter that names "
    "it (tools/dlq.py), because a consumer would recreate projection rows for an organisation that "
    "no longer exists.",
    "Redis: caches and rate-limit keys for the organisation expire on their own TTLs.",
    "Web bundles are not per organisation; nothing to remove.",
    "Backups taken before this run still hold the organisation until they age out "
    "(docs/engage-erasure-and-unsubscribe.md says the same of every delete here).",
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ------------------------------------------------------------------------------------ names
@dataclass(frozen=True, order=True)
class Table:
    """A schema-qualified table. Both parts come from the catalog, but they are spliced into SQL
    text, so they are checked rather than trusted."""

    schema: str
    name: str

    def __post_init__(self) -> None:
        for part in (self.schema, self.name):
            if not _IDENT.match(part):
                raise ValueError(f"refusing identifier {part!r}: not a plain identifier")

    @property
    def sql(self) -> str:
        return f'"{self.schema}"."{self.name}"'

    def __str__(self) -> str:
        return f"{self.schema}.{self.name}"


def _t(pair: tuple[str, str]) -> Table:
    return Table(*pair)


@dataclass(frozen=True)
class Fk:
    """One foreign key, child -> parent. `on_delete` is Postgres's `confdeltype`: `a` no action,
    `r` restrict, `c` cascade, `n` set null, `d` set default."""

    child: Table
    parent: Table
    child_cols: tuple[str, ...]
    parent_cols: tuple[str, ...]
    on_delete: str

    def __post_init__(self) -> None:
        for column in (*self.child_cols, *self.parent_cols):
            if not _IDENT.match(column):
                raise ValueError(f"refusing column name {column!r}: not a plain identifier")

    @property
    def blocks_delete(self) -> bool:
        return self.on_delete in ("a", "r")


@dataclass(frozen=True)
class Trigger:
    table: Table
    name: str
    enabled: str  # pg_trigger.tgenabled: O origin (normal), D disabled, R replica, A always


#: A statement's connection. SQLAlchemy's `AsyncConnection` in production; kept loose so the
#: pure helpers below can be exercised without one.
Conn = Any

#: How deep one table's row selector may nest through its parents before the tool decides the
#: foreign keys are not a tree and refuses.
_MAX_DEPTH = 8


@dataclass(frozen=True)
class Cond:
    """One way a row is the organisation's. `sql` renders it against a table alias."""

    kind: str  # org | row | user | session | parent
    column: str = ""
    parent: Table | None = None
    pairs: tuple[tuple[str, str], ...] = ()  # (child column, parent column) for `parent`

    def sql(self, alias: str, nodes: dict[Table, Node], depth: int, tag: str = "") -> str:
        """`tag` suffixes the two bind names (`:org`, `:orphans`), so one statement can ask the
        same question about more than one organisation (see `_count`'s `earlier`)."""
        if self.kind == "org":
            return f'{alias}."organization_id" = :org{tag}'
        if self.kind == "row":
            return f'{alias}."id" = :org{tag}'
        if self.kind == "user":
            return f'{alias}."{self.column}" = ANY(CAST(:orphans{tag} AS uuid[]))'
        if self.kind == "session":
            return f'{alias}."active_org_id" = :org{tag}'
        assert self.parent is not None
        if depth >= _MAX_DEPTH:
            raise SchemaDrift(f"foreign keys nest deeper than {_MAX_DEPTH} levels at {self.parent}")
        inner = f"p{depth + 1}"
        join = " AND ".join(f'{alias}."{c}" = {inner}."{pc}"' for c, pc in self.pairs)
        parents_rows = nodes[self.parent].predicate(nodes, inner, depth + 1, tag)
        # Identifiers come from the catalog and were checked by `Table`; values are bound.
        return f"EXISTS (SELECT 1 FROM {self.parent.sql} {inner} WHERE {join} AND ({parents_rows}))"  # noqa: S608


@dataclass
class Node:
    """One table the purge deletes from, and the conditions that say which of its rows are the
    organisation's (OR-ed)."""

    table: Table
    conds: list[Cond] = field(default_factory=list)
    #: True for the identity tables this tool owns (users, sessions, and what hangs off them).
    identity: bool = False
    has_org_column: bool = False

    def add(self, cond: Cond) -> None:
        if cond not in self.conds:
            self.conds.append(cond)

    def predicate(
        self, nodes: dict[Table, Node], alias: str = "t", depth: int = 0, tag: str = ""
    ) -> str:
        return " OR ".join(f"({c.sql(alias, nodes, depth, tag)})" for c in self.conds)


# --------------------------------------------------------------------------- catalog queries
_SYSTEM_SQL = ", ".join(f"'{s}'" for s in _SYSTEM_SCHEMAS)

# Discovery reads `pg_class` and `pg_attribute`, not `information_schema`: the latter lists only
# what the current role owns or holds a privilege on, so a table created by another role (a
# hand-applied hotfix, a superuser's migration) without a grant to this one would be invisible,
# skipped by the purge, and its rows would outlive the organisation row they belong to. The
# catalog shows every table; a table the role cannot delete from is then found by `preflight`
# and refuses the run, instead of being silently left behind.
_ORG_TABLES_SQL = text(
    f"""
    SELECT n.nspname::text, c.relname::text, format_type(a.atttypid, NULL)
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE a.attname::text = 'organization_id' AND a.attnum > 0 AND NOT a.attisdropped
      AND c.relkind IN ('r', 'p') AND NOT c.relispartition
      AND n.nspname NOT IN ({_SYSTEM_SQL}) AND n.nspname NOT LIKE 'pg\\_%'
    ORDER BY 1, 2
    """  # noqa: S608 - the system-schema list is a module constant
)

#: Every base table in a service schema: what `--explain` weighs the plan against.
_ALL_TABLES_SQL = text(
    f"""
    SELECT n.nspname::text, c.relname::text
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition
      AND n.nspname NOT IN ({_SYSTEM_SQL}) AND n.nspname NOT LIKE 'pg\\_%'
    ORDER BY 1, 2
    """  # noqa: S608
)

_FKS_SQL = text(
    f"""
    SELECT cn.nspname, cc.relname, pn.nspname, pc.relname, con.confdeltype::text,
      ARRAY(SELECT a.attname::text FROM unnest(con.conkey) WITH ORDINALITY k(attnum, ord)
            JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
            ORDER BY k.ord),
      ARRAY(SELECT a.attname::text FROM unnest(con.confkey) WITH ORDINALITY k(attnum, ord)
            JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
            ORDER BY k.ord)
    FROM pg_constraint con
    JOIN pg_class cc ON cc.oid = con.conrelid
    JOIN pg_namespace cn ON cn.oid = cc.relnamespace
    JOIN pg_class pc ON pc.oid = con.confrelid
    JOIN pg_namespace pn ON pn.oid = pc.relnamespace
    WHERE con.contype = 'f'
      AND cn.nspname NOT IN ({_SYSTEM_SQL}) AND cn.nspname NOT LIKE 'pg\\_%'
    ORDER BY 1, 2, 3, 4, con.conname
    """  # noqa: S608
)

#: Triggers that would fire on a DELETE. Internal (foreign-key) triggers are excluded: they are
#: the constraint machinery, not a guard, and are never touched.
_DELETE_TRIGGERS_SQL = text(
    f"""
    SELECT n.nspname, c.relname, t.tgname::text, t.tgenabled::text
    FROM pg_trigger t
    JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE NOT t.tgisinternal AND (t.tgtype & 8) <> 0
      AND n.nspname NOT IN ({_SYSTEM_SQL}) AND n.nspname NOT LIKE 'pg\\_%'
    ORDER BY 1, 2, 3
    """  # noqa: S608
)

_USER_TABLES_SQL = text(
    """
    SELECT c.relname::text
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname::text = :schema AND a.attname::text = 'user_id'
      AND a.attnum > 0 AND NOT a.attisdropped
      AND c.relkind IN ('r', 'p') AND NOT c.relispartition
    ORDER BY 1
    """
)

_COLUMN_EXISTS_SQL = text(
    """
    SELECT EXISTS (
      SELECT 1 FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname::text = :s AND c.relname::text = :t AND a.attname::text = :c
        AND a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r', 'p'))
    """
)

_ROLE_SQL = text(
    "SELECT current_user::text, r.rolsuper, r.rolbypassrls FROM pg_roles r "
    "WHERE r.rolname = current_user"
)

_PRIVILEGE_SQL = text(
    """
    SELECT n.nspname, c.relname,
           has_table_privilege(current_user, c.oid, 'DELETE'),
           pg_has_role(current_user, c.relowner, 'USAGE'),
           has_table_privilege(current_user, c.oid, 'SELECT')
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p') AND (n.nspname || '.' || c.relname) = ANY(:tables)
    """
)


async def discover_org_tables(conn: Conn) -> list[Table]:
    """Every base table with an `organization_id` column, in every non-system schema. Refuses a
    column of any type but `uuid`: the tool cannot safely compare what it cannot type."""
    rows = (await conn.execute(_ORG_TABLES_SQL)).all()
    wrong = [f"{r[0]}.{r[1]} ({r[2]})" for r in rows if r[2] != "uuid"]
    if wrong:
        raise SchemaDrift(f"organization_id is not a uuid on: {', '.join(wrong)}")
    return [Table(r[0], r[1]) for r in rows]


async def discover_fks(conn: Conn) -> list[Fk]:
    rows = (await conn.execute(_FKS_SQL)).all()
    return [Fk(Table(r[0], r[1]), Table(r[2], r[3]), tuple(r[5]), tuple(r[6]), r[4]) for r in rows]


async def discover_delete_triggers(conn: Conn) -> dict[Table, list[Trigger]]:
    found: dict[Table, list[Trigger]] = defaultdict(list)
    for schema, name, trigger, enabled in (await conn.execute(_DELETE_TRIGGERS_SQL)).all():
        table = Table(schema, name)
        found[table].append(Trigger(table, trigger, enabled))
    return found


async def _column_exists(conn: Conn, table: Table, column: str) -> bool:
    row = await conn.execute(_COLUMN_EXISTS_SQL, {"s": table.schema, "t": table.name, "c": column})
    return bool(row.scalar_one())


class SchemaDrift(RuntimeError):
    """The database is not shaped the way this tool needs; it refuses rather than guess."""


async def _check_identity_shape(conn: Conn) -> None:
    needed = [
        (ORGS, "id"),
        (ORGS, "parent_org_id"),
        (MEMBERSHIPS, "user_id"),
        (MEMBERSHIPS, "organization_id"),
        (USERS, "id"),
        (USERS, "is_staff"),
        (SESSIONS, "user_id"),
        (SESSIONS, "active_org_id"),
    ]
    missing = [f"{p[0]}.{p[1]}.{c}" for p, c in needed if not await _column_exists(conn, _t(p), c)]
    if missing:
        raise SchemaDrift(f"expected identity columns are missing: {', '.join(missing)}")


# ------------------------------------------------------------------------------ the plan
@dataclass(frozen=True)
class Plan:
    """Everything that is the same for every organisation: which tables, what selects their rows,
    and in what order they are deleted."""

    nodes: dict[Table, Node]
    order: list[Table]
    triggers: dict[Table, list[Trigger]]
    org_tables: frozenset[Table]
    #: Base tables in a service schema that the purge does not reach (`schema.name`): no
    #: `organization_id`, and no foreign key into a table that has one. Shown by `--explain` so
    #: what is left alone is as visible as what is deleted.
    unreached: tuple[str, ...] = ()


def build_nodes(
    org_tables: Iterable[Table],
    fks: Sequence[Fk],
    auth_user_tables: Iterable[str],
) -> dict[Table, Node]:
    """Decide which tables are deleted from and how their rows are chosen.

    * a table with `organization_id`: that column equals the organisation;
    * the organisation's own row: `id` equals it;
    * the user table and the auth tables keyed by `user_id`: the user is one being retired;
      `auth.sessions` also when it was active in the organisation;
    * any other table with a foreign key that blocks or cascades a delete into a table above: the
      rows whose parent row is chosen. That is found by walking the constraint graph to a fixed
      point, so `membership_roles` (no organisation column; child of `memberships` and `roles`)
      and anything shaped like it is covered without being named.
    """
    nodes: dict[Table, Node] = {}

    def node(table: Table, *, identity: bool = False) -> Node:
        n = nodes.setdefault(table, Node(table))
        n.identity = n.identity or identity
        return n

    for table in org_tables:
        n = node(table)
        n.has_org_column = True
        n.add(Cond("org"))
    node(_t(ORGS)).add(Cond("row"))
    node(_t(USERS), identity=True).add(Cond("user", "id"))
    for name in auth_user_tables:
        node(Table(AUTH_SCHEMA, name), identity=True).add(Cond("user", "user_id"))
    node(_t(SESSIONS), identity=True).add(Cond("session"))

    # A table outside the set joins it when it has a delete-blocking or cascading foreign key into
    # it. A table already in it by its `organization_id` takes a parent's rows too only when that
    # parent is an identity table (a session it hangs off), whose rows no organisation column of
    # the child's could select.
    by_child: dict[Table, list[Fk]] = defaultdict(list)
    for fk in fks:
        if fk.on_delete in ("a", "r", "c") and fk.child != fk.parent:
            by_child[fk.child].append(fk)
    changed = True
    while changed:
        changed = False
        for child, child_fks in sorted(by_child.items()):
            for fk in child_fks:
                parent = nodes.get(fk.parent)
                if parent is None:
                    continue
                existing = nodes.get(child)
                owns_its_rows = existing is not None and (
                    existing.has_org_column or existing.identity
                )
                if owns_its_rows and not parent.identity:
                    continue
                cond = Cond(
                    "parent",
                    parent=fk.parent,
                    pairs=tuple(zip(fk.child_cols, fk.parent_cols, strict=True)),
                )
                target = node(child, identity=parent.identity)
                if cond not in target.conds:
                    target.add(cond)
                    changed = True
    return nodes


def delete_order(nodes: Iterable[Table], fks: Sequence[Fk]) -> list[Table]:
    """Children before parents, from the constraint graph.

    Every foreign key between two nodes says the child's rows must be gone before the parent's.
    A cycle is first loosened by dropping the edges whose action does the work itself (cascade,
    set null, set default); if one still remains between delete-blocking edges the tool refuses
    with the tables named, because no order is safe. Self-references are not edges: a single
    `DELETE` removes a whole self-referencing set at once.
    """
    members = set(nodes)
    edges: dict[Table, set[Table]] = {t: set() for t in members}  # parent -> {children}
    soft: dict[tuple[Table, Table], bool] = {}
    for fk in fks:
        if fk.child in members and fk.parent in members and fk.child != fk.parent:
            edges[fk.parent].add(fk.child)
            pair = (fk.parent, fk.child)
            soft[pair] = soft.get(pair, True) and not fk.blocks_delete

    remaining_children: dict[Table, set[Table]] = {t: set(c) for t, c in edges.items()}
    ordered: list[Table] = []
    pending = set(members)
    while pending:
        ready = sorted(t for t in pending if not (remaining_children[t] & pending))
        if not ready:
            # A cycle. Drop soft edges among what is left and look again.
            dropped = False
            for parent in sorted(pending):
                for child in sorted(remaining_children[parent] & pending):
                    if soft.get((parent, child)):
                        remaining_children[parent].discard(child)
                        dropped = True
            if not dropped:
                stuck = ", ".join(str(t) for t in sorted(pending))
                raise SchemaDrift(
                    f"foreign keys form a cycle with no safe delete order among: {stuck}"
                )
            continue
        # Emit in a stable order; emitting one may unblock the rest of the layer, so take them all.
        ordered.extend(ready)
        pending.difference_update(ready)
    return ordered


async def build_plan(conn: Conn) -> Plan:
    await _check_identity_shape(conn)
    org_tables = await discover_org_tables(conn)
    fks = await discover_fks(conn)
    auth_user_tables = [
        r[0] for r in (await conn.execute(_USER_TABLES_SQL, {"schema": AUTH_SCHEMA})).all()
    ]
    nodes = build_nodes(org_tables, fks, auth_user_tables)
    order = delete_order(nodes, fks)
    triggers = await discover_delete_triggers(conn)
    reached = {str(t) for t in nodes}
    unreached = tuple(
        f"{schema}.{name}"
        for schema, name in (await conn.execute(_ALL_TABLES_SQL)).all()
        if f"{schema}.{name}" not in reached
    )
    return Plan(nodes, order, triggers, frozenset(org_tables), unreached)


# ------------------------------------------------------------------------------- selection
class Refusal(Exception):
    """A rule that is not negotiable. The message names the rule, never a row."""


#: An organisation id as the tool prints it and as `psql` shows it: ASCII hex, hyphenated. Stricter
#: than `uuid.UUID`, which also takes braces, a `urn:` prefix and any Unicode digit (so a line made
#: of Arabic-Indic numerals would parse to an id it does not look like).
_ID_LINE = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def canonical_uuid(value: str) -> uuid.UUID:
    """An `--org-id`, `--keep` or `--keep-user` argument: the canonical form and nothing else."""
    if not _ID_LINE.fullmatch(value.strip()):
        raise argparse.ArgumentTypeError(f"invalid uuid {value!r}: expected 8-4-4-4-12 hex digits")
    return uuid.UUID(value.strip())


def parse_ids(text_: str, *, source: str) -> list[uuid.UUID]:
    """One uuid per line; `#` starts a comment; blank lines are ignored. A line that is not a uuid
    is a refusal that names the line number, not the line.

    The file is read as the owner's own eyes read it, which is not how `str.splitlines` reads it:
    that also splits on `\\x0b`, `\\x0c`, `\\x1c`-`\\x1e`, `\\x85`, U+2028 and U+2029, characters a
    terminal or editor shows as nothing or as part of the line before. A candidates file carries
    tenant-chosen organisation names in its comments, so a name could hold one of them and a second
    id behind it. So lines end at `\\n` (and a CRLF file's `\\r`) only, and a line with any other
    control, format or separator character in it, comment or not, is refused rather than guessed
    at."""
    found: list[uuid.UUID] = []
    for number, raw in enumerate(text_.removeprefix("\ufeff").split("\n"), start=1):
        line = raw.removesuffix("\r")
        stray = next((c for c in line if c != "\t" and not c.isprintable()), None)
        if stray is not None:
            raise Refusal(
                f"{source}: line {number} contains U+{ord(stray):04X}, a control or separator "
                "character; a file of ids is plain text, one id to a visible line"
            )
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if not _ID_LINE.fullmatch(line):
            raise Refusal(f"{source}: line {number} is not an organisation uuid")
        found.append(uuid.UUID(line))
    return found


def distinct(ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    """Sorted and de-duplicated: what `--confirm-count` counts, and the order organisations are
    processed in unless one is the parent of another (see `children_first`)."""
    return sorted(set(ids))


def children_first(
    ids: Sequence[uuid.UUID], parent_of: Mapping[uuid.UUID, uuid.UUID | None]
) -> list[uuid.UUID]:
    """The selected organisations in the order they can be deleted.

    `account.organizations.parent_org_id` is `ON DELETE RESTRICT` and each organisation is its own
    transaction, so a parent can only go after every child of it is gone. When the owner selects a
    parent and its sub-organisation together (which is what `guard_survivors` tells them to do), the
    child goes first whatever the two ids sort like. Everything else keeps `distinct`'s id order:
    each pass takes, in id order, every organisation with no selected child still waiting. An
    organisation naming itself as its own parent is not waiting on anything (one `DELETE` removes
    it). A loop (A is B's parent and B is A's) has no order, and is refused."""
    selected = set(ids)
    waiting: dict[uuid.UUID, set[uuid.UUID]] = {i: set() for i in selected}
    for child in selected:
        parent = parent_of.get(child)
        if parent is not None and parent != child and parent in selected:
            waiting[parent].add(child)
    ordered: list[uuid.UUID] = []
    pending = sorted(selected)
    while pending:
        ready = [i for i in pending if not waiting[i]]
        if not ready:
            loop = ", ".join(str(i) for i in pending)
            raise Refusal(
                f"parent_org_id forms a loop among the selected organisations ({loop}); "
                "no order can remove them: fix the parent links first"
            )
        ordered.extend(ready)
        gone = set(ready)
        pending = [i for i in pending if i not in gone]
        for children in waiting.values():
            children -= gone
    return ordered


def requires_live_flag(environment: str, host: str | None) -> bool:
    """`--apply` needs `--i-know-this-is-the-live-database` when the environment is a real
    deployment, or when the database is not on this machine whatever the environment says (a
    laptop with `ENVIRONMENT=local` and a `DATABASE_URL` copied from the box is the accident this
    exists for). A DSN with no host at all (`?host=/path/to/socket`) is a unix socket, which can
    just as well be a forwarded one, so it is treated as live too; only a named loopback address is
    taken to be this machine.

    What it cannot see: an `ssh -L` tunnel to the box is a loopback address. The tool only knows
    the DSN, so the RUNBOOK says to run it on the box and not through one."""
    return environment in LIVE_ENVIRONMENTS or host is None or host not in _LOOPBACK_HOSTS


def check_selection(
    selected: Sequence[uuid.UUID], keep: Iterable[uuid.UUID], *, confirm_count: int | None
) -> list[str]:
    """The refusals that need no database. Returns every violated rule, not just the first."""
    problems: list[str] = []
    if PLATFORM_ORG in selected:
        problems.append("the nil uuid is the platform's own sentinel organisation; never selected")
    kept = set(keep)
    clash = [str(o) for o in selected if o in kept]
    if clash:
        problems.append(f"selected but listed in --keep/--keep-file: {', '.join(clash)}")
    if confirm_count is None:
        problems.append("--confirm-count N is required with --apply")
    elif confirm_count != len(selected):
        problems.append(
            f"--confirm-count {confirm_count} does not match the {len(selected)} organisation(s) "
            "selected"
        )
    return problems


def check_apply_gates(
    args: argparse.Namespace,
    selected: Sequence[uuid.UUID],
    *,
    keep: Iterable[uuid.UUID],
    environment: str,
    host: str | None,
) -> list[str]:
    """Everything `--apply` needs before it may connect, as the list of rules not met."""
    problems = check_selection(selected, keep, confirm_count=args.confirm_count)
    if not args.i_took_a_backup:
        problems.append("--i-took-a-backup is required: take one (make edge-backup) and say so")
    if requires_live_flag(environment, host) and not args.i_know_this_is_the_live_database:
        problems.append(
            f"this is a live database (environment={environment!r}, host={host!r}): "
            "--i-know-this-is-the-live-database is required"
        )
    return problems


# ------------------------------------------------------------------- per-organisation work
@dataclass
class UserReport:
    members: int = 0
    kept_shared: int = 0
    kept_protected: int = 0
    kept_by_choice: int = 0
    deleted: int = 0


@dataclass
class OrgPlan:
    organization_id: uuid.UUID
    counts: dict[Table, int]
    orphans: list[uuid.UUID]
    users: UserReport
    left_in_place: dict[str, int]
    #: The organisations already handled in this run, this one included: what "another
    #: organisation" means for a user, so `apply_org` can re-check it under a lock.
    processed: list[uuid.UUID] = field(default_factory=list)
    #: With nothing to delete: whether an earlier purge of this id left its audit row (so the id
    #: is "already clean") or nothing ever did (so it names no organisation: a typo).
    previously_purged: bool = False
    #: Counts that say whether this looks like a live customer (see `_signals`); printed with the
    #: dry run and the apply, so the owner's last look at an id is not a name a stranger chose.
    signals: dict[str, Any] = field(default_factory=dict)

    @property
    def rows(self) -> int:
        return sum(self.counts.values())

    def nonzero(self) -> dict[str, int]:
        return {str(t): n for t, n in sorted(self.counts.items()) if n}


async def classify_users(
    conn: Conn,
    org: uuid.UUID,
    processed: Sequence[uuid.UUID],
    *,
    keep_users: Iterable[uuid.UUID],
    keep_orphans: bool,
) -> tuple[list[uuid.UUID], UserReport]:
    """Which of the organisation's members are deleted with it. A user survives when they hold a
    membership in any organisation not already processed (so one who belongs to two selected
    organisations goes with the last of them, and one who belongs to an organisation that stays
    never goes), is `is_staff`, or is named by `--keep-user`."""
    rows = (
        await conn.execute(
            text(
                f"""
                SELECT u.id, u.is_staff,
                  EXISTS (SELECT 1 FROM {MEMBERSHIPS[0]}.{MEMBERSHIPS[1]} m2
                          WHERE m2.user_id = u.id
                            AND m2.organization_id <> ALL(CAST(:processed AS uuid[]))) AS shared
                FROM {USERS[0]}.{USERS[1]} u
                WHERE u.id IN (SELECT m.user_id FROM {MEMBERSHIPS[0]}.{MEMBERSHIPS[1]} m
                               WHERE m.organization_id = :org)
                ORDER BY u.id
                """  # noqa: S608 - identifiers are module constants
            ),
            {"org": org, "processed": list(processed)},
        )
    ).all()
    kept = set(keep_users)
    report = UserReport(members=len(rows))
    orphans: list[uuid.UUID] = []
    for user_id, is_staff, shared in rows:
        if shared:
            report.kept_shared += 1
        elif is_staff or user_id in kept:
            report.kept_protected += 1
        elif keep_orphans:
            report.kept_by_choice += 1
        else:
            orphans.append(user_id)
    report.deleted = len(orphans)
    return orphans, report


def _params(org: uuid.UUID, orphans: Sequence[uuid.UUID]) -> dict[str, Any]:
    return {"org": org, "orphans": list(orphans)}


#: An organisation a dry run treats as already gone when it counts a later one: its id and the
#: users it retires.
Earlier = Sequence[tuple[uuid.UUID, Sequence[uuid.UUID]]]


async def _count(
    conn: Conn,
    node: Node,
    nodes: dict[Table, Node],
    params: dict[str, Any],
    earlier: Earlier = (),
) -> int:
    """Rows of one table that are this organisation's. `earlier` is for the dry run only: it counts
    every selected organisation from one snapshot in which the first is still there, so a row two
    of them could both claim (a user in both has one session and one membership in each, and
    the user, with every session, goes with the last of them) would be counted twice, though
    `--apply` deletes it once, under the first. Excluding what each earlier organisation's
    selector matches makes the dry run count what the apply will find when its turn comes."""
    where = f"({node.predicate(nodes)})"
    bound = dict(params)
    for k, (prior, prior_orphans) in enumerate(earlier):
        # `IS NOT TRUE`, not `NOT`: a selector that is NULL for a row (a nullable column) did not
        # match it, and the row is still there for this organisation.
        where += f" AND (({node.predicate(nodes, 't', 0, f'_{k}')}) IS NOT TRUE)"
        bound[f"org_{k}"] = prior
        bound[f"orphans_{k}"] = list(prior_orphans)
    row = await conn.execute(
        text(f"SELECT count(*) FROM {node.table.sql} t WHERE {where}"),  # noqa: S608
        bound,
    )
    return int(row.scalar_one())


async def _left_in_place(conn: Conn, org: uuid.UUID) -> dict[str, int]:
    found: dict[str, int] = {}
    for schema, name, column in LEFT_IN_PLACE:
        table = Table(schema, name)
        if not await _column_exists(conn, table, column):
            continue
        n = (
            await conn.execute(
                text(f'SELECT count(*) FROM {table.sql} WHERE "{column}" = :org'),  # noqa: S608
                {"org": org},
            )
        ).scalar_one()
        if n:
            found[f"{table}.{column}"] = int(n)
    return found


async def _signals(conn: Conn, org: uuid.UUID) -> dict[str, Any]:
    """Members, subscriptions by state and captured payments for one organisation: counts a tenant
    cannot dress up the way it can a name. The listing's hints are only as honest as the name behind
    them, so the dry run shows these beside every id, and an organisation with a live subscription
    or a captured payment among ids the owner thought were idle is plain to see."""
    members = _t(MEMBERSHIPS)
    member_count = (
        await conn.execute(
            text(f"SELECT count(*) FROM {members.sql} WHERE organization_id = :org"),  # noqa: S608
            {"org": org},
        )
    ).scalar_one()
    subscriptions: dict[str, int] = {}
    subs = Table("subscription", "subscriptions")
    if await _exists_table(conn, subs):
        for state, n in await _grouped(
            conn,
            f"SELECT state, count(*) FROM {subs.sql} "  # noqa: S608
            "WHERE organization_id = :org AND deleted_at IS NULL GROUP BY 1 ORDER BY 1",
            {"org": org},
        ):
            subscriptions[str(state)] = int(n)
    captured = 0
    charges = Table("payment", "charges")
    if await _exists_table(conn, charges):
        captured = int(
            (
                await conn.execute(
                    text(
                        f"SELECT count(*) FROM {charges.sql} "  # noqa: S608
                        "WHERE organization_id = :org AND state = 'captured'"
                    ),
                    {"org": org},
                )
            ).scalar_one()
        )
    return {
        "members": int(member_count),
        "subscriptions": subscriptions,
        "captured_charges": captured,
    }


async def plan_org(
    conn: Conn,
    plan: Plan,
    org: uuid.UUID,
    processed: Sequence[uuid.UUID],
    *,
    keep_users: Iterable[uuid.UUID],
    keep_orphans: bool,
    earlier: Earlier = (),
) -> OrgPlan:
    orphans, users = await classify_users(
        conn, org, processed, keep_users=keep_users, keep_orphans=keep_orphans
    )
    params = _params(org, orphans)
    counts = {t: await _count(conn, plan.nodes[t], plan.nodes, params, earlier) for t in plan.order}
    op = OrgPlan(
        org,
        counts,
        orphans,
        users,
        await _left_in_place(conn, org),
        processed=list(processed),
        signals=await _signals(conn, org),
    )
    if not op.rows:
        op.previously_purged = await _was_purged(conn, org)
    return op


async def _was_purged(conn: Conn, org: uuid.UUID) -> bool:
    audit = _t(AUDIT)
    if not await _exists_table(conn, audit):
        return False
    statement = (
        f"SELECT EXISTS (SELECT 1 FROM {audit.sql} "  # noqa: S608
        "WHERE action = 'organization.purged' AND entity_id = :org)"
    )
    return bool((await conn.execute(text(statement), {"org": org})).scalar_one())


def status_of(op: OrgPlan, mode: str) -> str:
    """One word for what happened (or would) to one organisation."""
    if op.rows:
        return "deleted" if mode == "apply" else "would-delete"
    return "already-clean" if op.previously_purged else "not-found"


async def _set_trigger(conn: Conn, trigger: Trigger, state: str) -> None:
    for part in (trigger.name,):
        if not _IDENT.match(part):
            raise ValueError(f"refusing trigger name {part!r}")
    verb = {
        "O": "ENABLE TRIGGER",
        "D": "DISABLE TRIGGER",
        "R": "ENABLE REPLICA TRIGGER",
        "A": "ENABLE ALWAYS TRIGGER",
    }[state]
    # Both names were checked against `_IDENT` and `verb` is one of four constants.
    statement = f'ALTER TABLE {trigger.table.sql} {verb} "{trigger.name}"'
    await conn.execute(text(statement))


async def apply_org(
    conn: Conn,
    plan: Plan,
    org_plan: OrgPlan,
    *,
    audit_summary: dict[str, Any],
) -> None:
    """Delete one organisation's rows. Runs inside the caller's transaction and writes nothing the
    caller does not commit; any exception propagates and the transaction rolls back whole."""
    params = _params(org_plan.organization_id, org_plan.orphans)
    await _lock_and_recheck_orphans(conn, org_plan)

    # Only the guards on tables that have rows to delete are lifted, and only the ones that fire
    # on DELETE. They are put back to the state they were found in before this function returns.
    lifted: list[Trigger] = []
    for table in plan.order:
        if org_plan.counts[table]:
            lifted.extend(t for t in plan.triggers.get(table, []) if t.enabled != "D")
    for trigger in lifted:
        await _set_trigger(conn, trigger, "D")

    for table in plan.order:
        expected = org_plan.counts[table]
        if not expected:
            continue
        node = plan.nodes[table]
        where = node.predicate(plan.nodes)
        statement = f"DELETE FROM {table.sql} t WHERE {where}"  # noqa: S608
        result = await conn.execute(text(statement), params)
        if result.rowcount > expected:
            raise RuntimeError(
                f"{table}: deleted {result.rowcount} rows, more than the {expected} counted; "
                "rows appeared during the run, rolling back"
            )

    for trigger in lifted:
        await _set_trigger(conn, trigger, trigger.enabled)
    await _assert_triggers_restored(conn, lifted)

    # Nothing may remain, whatever a concurrent writer did while the locks allowed it.
    for table in plan.order:
        left = await _count(conn, plan.nodes[table], plan.nodes, params)
        if left:
            raise RuntimeError(f"{table}: {left} rows remain after the delete, rolling back")

    await _write_audit(conn, org_plan.organization_id, audit_summary)


async def _lock_and_recheck_orphans(conn: Conn, org_plan: OrgPlan) -> None:
    """A user is deleted only if they are in no organisation that stays. That was decided from a
    read a moment ago, and a membership could have been added since: deleting the user would then
    cascade it away. So the users are locked first (a concurrent membership insert takes a share
    lock on the user row, and now waits for this transaction and then fails on the missing user),
    and the question is asked again under the lock."""
    if not org_plan.orphans:
        return
    users, members = _t(USERS), _t(MEMBERSHIPS)
    params = {"orphans": org_plan.orphans, "processed": org_plan.processed}
    await conn.execute(
        text(f"SELECT 1 FROM {users.sql} WHERE id = ANY(CAST(:orphans AS uuid[])) FOR UPDATE"),  # noqa: S608
        params,
    )
    gained = (
        await conn.execute(
            text(
                f"SELECT count(*) FROM {members.sql} WHERE user_id = ANY(CAST(:orphans AS uuid[])) "  # noqa: S608
                "AND organization_id <> ALL(CAST(:processed AS uuid[]))"
            ),
            params,
        )
    ).scalar_one()
    if gained:
        raise RuntimeError(
            f"{gained} membership(s) were added to users about to be deleted; rolling back"
        )


async def _assert_triggers_restored(conn: Conn, lifted: Sequence[Trigger]) -> None:
    if not lifted:
        return
    rows = (await conn.execute(_DELETE_TRIGGERS_SQL)).all()
    now = {(Table(r[0], r[1]), r[2]): r[3] for r in rows}
    for trigger in lifted:
        if now.get((trigger.table, trigger.name)) != trigger.enabled:
            raise RuntimeError(
                f"{trigger.table}: trigger {trigger.name} is not back to its original state; "
                "rolling back"
            )


async def _write_audit(conn: Conn, org: uuid.UUID, summary: dict[str, Any]) -> None:
    """One row per purged organisation in `auth.audit_logs`, under the platform sentinel — the
    shape operator actions already use there. Counts only. Written in the same transaction, so it
    exists exactly when the deletion committed."""
    if not await _column_exists(conn, _t(AUDIT), "after"):
        return
    statement = (
        f"INSERT INTO {_t(AUDIT).sql} (id, organization_id, actor_id, acting_as, action, "  # noqa: S608
        "entity, entity_id, after, reason) VALUES (gen_random_uuid(), :platform, NULL, "
        "'owner-cli', 'organization.purged', 'organization', :org, CAST(:after AS jsonb), "
        "'tools/purge_test_orgs.py (owner-run)')"
    )
    await conn.execute(
        text(statement),
        {"platform": PLATFORM_ORG, "org": org, "after": json.dumps(summary, sort_keys=True)},
    )


# ------------------------------------------------------------------------------ preflight
async def preflight(conn: Conn, plan: Plan) -> list[str]:
    """What the connecting role must be, checked before any write. Returns the problems."""
    problems: list[str] = []
    name, is_super, bypass = (await conn.execute(_ROLE_SQL)).one()
    if not (is_super or bypass):
        problems.append(
            f"role {name!r} neither bypasses row-level security nor is a superuser: a delete "
            "would silently match nothing"
        )
    tables = [str(t) for t in plan.order]
    rows = (await conn.execute(_PRIVILEGE_SQL, {"tables": tables})).all()
    # Every table the catalog shows is in the plan, so a table this role cannot read and delete
    # from (created by another role, never granted) stops the run here: it would otherwise be
    # counted and deleted from blind, or skipped while the organisation row went.
    no_delete = [f"{r[0]}.{r[1]}" for r in rows if not (r[2] and r[4])]
    if no_delete:
        problems.append(f"role {name!r} lacks DELETE or SELECT on: {', '.join(no_delete)}")
    found = {f"{r[0]}.{r[1]}" for r in rows}
    unseen = [t for t in tables if t not in found]
    if unseen:
        problems.append(f"tables in the plan that the catalog does not show: {', '.join(unseen)}")
    not_owner = [
        f"{r[0]}.{r[1]}" for r in rows if not r[3] and plan.triggers.get(Table(r[0], r[1]))
    ]
    if not_owner:
        problems.append(
            f"role {name!r} does not own tables whose delete guards must be lifted: "
            f"{', '.join(not_owner)}"
        )
    return problems


async def guard_survivors(conn: Conn, selected: Sequence[uuid.UUID]) -> tuple[list[str], int, int]:
    """(problems, organisations in total, organisations that would remain). Refuses to empty the
    platform and to orphan a child organisation."""
    org = _t(ORGS)
    count_all = f"SELECT count(*) FROM {org.sql}"  # noqa: S608
    count_selected = f"{count_all} WHERE id = ANY(CAST(:ids AS uuid[]))"
    total = int((await conn.execute(text(count_all))).scalar_one())
    present = int((await conn.execute(text(count_selected), {"ids": list(selected)})).scalar_one())
    remaining = total - present
    problems: list[str] = []
    if present and remaining == 0:
        problems.append("this would remove every organisation; at least one must remain")
    orphaned_children = (
        f"{count_all} WHERE parent_org_id = ANY(CAST(:ids AS uuid[])) "
        "AND NOT (id = ANY(CAST(:ids AS uuid[])))"
    )
    children = int(
        (await conn.execute(text(orphaned_children), {"ids": list(selected)})).scalar_one()
    )
    if children:
        problems.append(
            f"{children} surviving organisation(s) are children of a selected one; "
            "select them too or keep the parent"
        )
    return problems, total, remaining


async def selected_parents(
    conn: Conn, selected: Sequence[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID | None]:
    """`parent_org_id` of every selected organisation that exists, for `children_first`."""
    org = _t(ORGS)
    rows = await _grouped(
        conn,
        f"SELECT id, parent_org_id FROM {org.sql} WHERE id = ANY(CAST(:ids AS uuid[]))",  # noqa: S608
        {"ids": list(selected)},
    )
    return {r[0]: r[1] for r in rows}


# --------------------------------------------------------------------------------- listing
#: Whole tokens (split on anything that is not a letter or digit) that mark a name or slug as test
#: data. Token-wise and exact, so `Latest Textiles` is not `test`, `Contest Winners` is not `test`
#: and `Demonstrators of Delhi` is not `demo`; `e2e0920a` and `smoketest` still are.
_TEST_TOKEN = re.compile(
    r"^(e2e\w*|smoke\w*|buyers?|tests?|testing|tester|demos?|dummy|samples?|sandbox\w*|"
    r"playground|fixtures?|qa|tmp|temp)$"
)
#: Throwaway or reserved mailbox domains, as one regex applied to a lower-cased address in SQL so
#: the tool never has to read an address into Python to classify it.
THROWAWAY_EMAIL_RE = (
    r"@([a-z0-9-]+\.)*(example\.(com|org|net)|mailinator\.[a-z]+|yopmail\.com|"
    r"guerrillamail\.[a-z]+|test\.com)$|\.(test|example|invalid|localhost)$"
)


def name_hints(name: str | None, slug: str | None) -> list[str]:
    """Reasons a name or slug looks like test data."""
    reasons: list[str] = []
    for label, value in (("name", name), ("slug", slug)):
        tokens = [t for t in re.split(r"[^a-z0-9]+", (value or "").lower()) if t]
        hit = next((t for t in tokens if _TEST_TOKEN.match(t)), None)
        if hit:
            reasons.append(f"{label} looks like '{hit}'")
    return reasons


@dataclass
class OrgRow:
    id: uuid.UUID
    name: str
    slug: str
    status: str
    created_at: datetime | None
    members: int = 0
    throwaway_members: int = 0
    last_active: datetime | None = None
    subscriptions: dict[str, int] = field(default_factory=dict)
    captured_charges: int = 0
    test_domains: int = 0
    kept: bool = False

    @property
    def reasons(self) -> list[str]:
        out = name_hints(self.name, self.slug)
        if self.members and self.throwaway_members == self.members:
            out.append("every member uses a throwaway email domain")
        if self.test_domains:
            out.append("has a test/example domain")
        return out

    @property
    def idle(self) -> bool:
        live = self.subscriptions.get("active", 0) + self.subscriptions.get("past_due", 0)
        return live == 0 and self.captured_charges == 0

    @property
    def hint(self) -> str:
        if self.reasons:
            return "LIKELY TEST"
        return "idle" if self.idle else ""


async def _exists_table(conn: Conn, table: Table) -> bool:
    return bool(
        (
            await conn.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": str(table)})
        ).scalar_one()
    )


#: A test or reserved domain, as one regex applied to a lower-cased domain in SQL.
TEST_DOMAIN_RE = (
    r"(^|\.)(example\.(com|org|net)|mailinator\.[a-z]+)$|\.(test|example|invalid|localhost)$"
)


async def _grouped(conn: Conn, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    return list((await conn.execute(text(sql), params or {})).all())


async def list_orgs(conn: Conn, keep: Iterable[uuid.UUID]) -> list[OrgRow]:
    """Every organisation with the facts a person needs to decide, and no more. Tables a database
    does not have (an older schema) are skipped, not an error."""
    org, members, users = _t(ORGS), _t(MEMBERSHIPS), _t(USERS)
    rows = await _grouped(
        conn,
        f"SELECT id, name, slug::text, status, created_at FROM {org.sql} "  # noqa: S608
        "ORDER BY created_at, id",
    )
    by_id = {r[0]: OrgRow(r[0], r[1], r[2], r[3], r[4]) for r in rows}
    for org_id in keep:
        if org_id in by_id:
            by_id[org_id].kept = True

    for org_id, n, last, throwaway in await _grouped(
        conn,
        "SELECT m.organization_id, count(*), max(m.last_active_at), "  # noqa: S608
        "count(*) FILTER (WHERE lower(u.email::text) ~ :throwaway) "
        f"FROM {members.sql} m JOIN {users.sql} u ON u.id = m.user_id GROUP BY m.organization_id",
        {"throwaway": THROWAWAY_EMAIL_RE},
    ):
        if org_id in by_id:
            row = by_id[org_id]
            row.members, row.last_active, row.throwaway_members = n, last, throwaway

    subs = Table("subscription", "subscriptions")
    if await _exists_table(conn, subs):
        for org_id, state, n in await _grouped(
            conn,
            f"SELECT organization_id, state, count(*) FROM {subs.sql} "  # noqa: S608
            "WHERE deleted_at IS NULL GROUP BY 1, 2",
        ):
            if org_id in by_id:
                by_id[org_id].subscriptions[state] = n

    charges = Table("payment", "charges")
    if await _exists_table(conn, charges):
        for org_id, n in await _grouped(
            conn,
            f"SELECT organization_id, count(*) FROM {charges.sql} "  # noqa: S608
            "WHERE state = 'captured' GROUP BY 1",
        ):
            if org_id in by_id:
                by_id[org_id].captured_charges = n

    domains = Table("account", "org_domains")
    if await _exists_table(conn, domains):
        for org_id, n in await _grouped(
            conn,
            f"SELECT organization_id, count(*) FROM {domains.sql} "  # noqa: S608
            "WHERE lower(domain::text) ~ :pat GROUP BY 1",
            {"pat": TEST_DOMAIN_RE},
        ):
            if org_id in by_id:
                by_id[org_id].test_domains = n
    return list(by_id.values())


#: How much of a tenant-chosen name or slug the listing and the candidates file show.
DISPLAY_LIMIT = 80


def inert(value: str | None, limit: int = DISPLAY_LIMIT) -> str:
    """A tenant-chosen string made safe to print and to write into a file the owner will read.

    An organisation's name is typed by whoever signed up (`organization_name` is only length
    checked), so it is data from a stranger, and it is printed where the owner decides what to
    delete. Every character Python does not call printable (controls, escape, line and paragraph
    separators, bidirectional overrides, zero-width and other format characters, exotic spaces) is
    replaced by a visible `?`, and the result is cut to `limit`: it can neither end the line it is
    on, repaint the terminal, nor read as something other than what it is."""
    cleaned = "".join(c if c.isprintable() else "?" for c in value or "")
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 3] + "..."


def _fmt_date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else "-"


def _fmt_subs(subs: dict[str, int]) -> str:
    if not subs:
        return "0"
    return f"{sum(subs.values())} ({', '.join(f'{k}:{v}' for k, v in sorted(subs.items()))})"


def render_listing(rows: Sequence[OrgRow], database: str, environment: str) -> str:
    """The interactive table. Prints names: this is the one place the tool does, for the owner's
    own terminal."""
    headers = (
        "#",
        "id",
        "name",
        "slug",
        "created",
        "last active",
        "members",
        "subscriptions",
        "paid",
        "hint",
    )
    body = [
        (
            str(i),
            str(r.id),
            inert(r.name),
            inert(r.slug),
            _fmt_date(r.created_at),
            _fmt_date(r.last_active),
            str(r.members),
            _fmt_subs(r.subscriptions),
            str(r.captured_charges),
            "KEEP" if r.kept else (r.hint + (f" - {'; '.join(r.reasons)}" if r.reasons else "")),
        )
        for i, r in enumerate(rows, start=1)
    ]
    widths = [
        max(len(h), *(len(line[c]) for line in body)) if body else len(h)
        for c, h in enumerate(headers)
    ]
    lines = [
        f"database: {database}   environment: {environment}   organisations: {len(rows)}",
        "",
        "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip(),
        "  ".join("-" * w for w in widths),
    ]
    lines += [
        "  ".join(c.ljust(w) for c, w in zip(line, widths, strict=True)).rstrip() for line in body
    ]
    likely = sum(1 for r in rows if r.hint == "LIKELY TEST" and not r.kept)
    lines += [
        "",
        f"{likely} of {len(rows)} look like test data by name, slug or email domain. A hint is a "
        "suggestion only: nothing is selected for you.",
        "'paid' counts captured payments (a test-mode box counts them too). "
        "'idle' means no active subscription and no captured payment.",
        "Choose with --org-id <uuid> (repeatable) or --ids-file; add --apply only after a dry run.",
    ]
    return "\n".join(lines)


def render_candidates(rows: Sequence[OrgRow]) -> str:
    """The `LIKELY TEST` rows as an ids file to edit. Printing a file is not selecting: you review
    it, delete the lines you want to keep, and pass what is left with --ids-file.

    Only a hinted row is ever written, and the name and slug are tenant-chosen text, so they go
    through `inert`: a name can no longer end its line and put another organisation's id on the
    next one (`parse_ids` refuses such a file as a second defence)."""
    lines = [
        "# organisations whose name, slug or email domain looks like test data.",
        "# REVIEW EVERY LINE. Delete the ones you want to keep. Nothing here is selected until you",
        "# pass this file with --ids-file.",
    ]
    lines += [
        f"{r.id}  # {inert(r.name)} ({inert(r.slug)})"
        for r in rows
        if r.hint == "LIKELY TEST" and not r.kept
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------ run
@dataclass
class Result:
    mode: str
    database: str
    environment: str
    selected: int = 0
    organisations_before: int = 0
    organisations_remaining: int | None = None
    users_without_organisation: int | None = None
    #: Organisations that had rows: removed under `--apply`, removable in a dry run.
    removed: int = 0
    already_clean: int = 0
    not_found: int = 0
    failed: int = 0
    not_attempted: int = 0
    rows: int = 0
    users_deleted: int = 0


def _org_line(op: OrgPlan, mode: str, status: str) -> dict[str, Any]:
    line: dict[str, Any] = {
        "organization_id": str(op.organization_id),
        "mode": mode,
        "status": status,
        ("deleted" if mode == "apply" else "would_delete"): op.nonzero(),
        "rows": op.rows,
        "users": {
            "members": op.users.members,
            "kept_shared_with_another_organization": op.users.kept_shared,
            "kept_protected": op.users.kept_protected,
            "kept_by_choice": op.users.kept_by_choice,
            "deleted": op.users.deleted,
        },
        "live_signals": op.signals,
    }
    if op.left_in_place:
        line["left_in_place"] = op.left_in_place
    return line


def _external_notes(op: OrgPlan, bucket: str) -> list[str]:
    """What this organisation leaves outside the database, from the tables it had rows in."""
    counts = {str(t): n for t, n in op.counts.items()}
    return [
        f"  {op.organization_id}: {counts[table]} row(s) in {table}: "
        + advice.format(bucket=bucket, org=op.organization_id)
        for table, advice in EXTERNAL_LEFTOVERS.items()
        if counts.get(table)
    ]


def _error_line(org: uuid.UUID, exc: BaseException) -> dict[str, Any]:
    """Enough to find the problem and nothing that could be a row: the class and, for a database
    error, the SQLSTATE and the table or constraint it names. Postgres's message text is left out
    because it can quote a key."""
    orig = getattr(exc, "orig", exc)
    cause = getattr(orig, "__cause__", None) or orig
    line: dict[str, Any] = {
        "organization_id": str(org),
        "status": "failed",
        "error": type(exc).__name__,
    }
    for attr in ("sqlstate", "schema_name", "table_name", "constraint_name"):
        value = getattr(cause, attr, None)
        if value:
            line[attr] = value
    if isinstance(exc, RuntimeError | Refusal):
        # Our own messages: a table and a count, or the rule that was broken, never a value.
        line["detail"] = str(exc)[:300]
    return line


async def _read_phase(
    engine: AsyncEngine,
    selected: Sequence[uuid.UUID],
    *,
    keep_users: Sequence[uuid.UUID],
    keep_orphans: bool,
    want_plans: bool,
) -> tuple[Plan, list[uuid.UUID], list[OrgPlan], int]:
    """Everything read before anything can be written: the catalog plan, the role and survivor
    checks, the order the organisations will be processed in (children before their parents) and,
    for a dry run, each organisation's counts, all from one snapshot. The dry run and `--apply`
    take that one order, so what the first prints is what the second does, in the same sequence."""
    async with engine.connect() as conn:
        await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        plan = await build_plan(conn)
        problems = await preflight(conn, plan)
        more, total, _remaining = await guard_survivors(conn, selected)
        problems += more
        if problems:
            raise Refusal("; ".join(problems))
        ordered = children_first(selected, await selected_parents(conn, selected))
        plans: list[OrgPlan] = []
        if want_plans:
            earlier: list[tuple[uuid.UUID, list[uuid.UUID]]] = []
            for i, org in enumerate(ordered):
                op = await plan_org(
                    conn,
                    plan,
                    org,
                    ordered[: i + 1],
                    keep_users=keep_users,
                    keep_orphans=keep_orphans,
                    earlier=earlier,
                )
                plans.append(op)
                earlier.append((org, op.orphans))
        await conn.rollback()
    return plan, ordered, plans, total


async def _purge_one(
    engine: AsyncEngine,
    plan: Plan,
    org: uuid.UUID,
    processed: Sequence[uuid.UUID],
    *,
    keep_users: Sequence[uuid.UUID],
    keep_orphans: bool,
) -> OrgPlan:
    """One organisation, one transaction: counted, deleted, verified empty, audited, committed."""
    async with engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
        await conn.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
        locked = (
            await conn.execute(
                text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _ADVISORY_LOCK_KEY}
            )
        ).scalar_one()
        if not locked:
            raise Refusal("another purge is running against this database")
        # Belt and braces (CLAUDE.md: the tenant GUC is transaction-local): the role bypasses
        # RLS, but if a table's policy ever applied it would still see exactly this organisation.
        await conn.execute(
            text("SELECT set_config('app.organization_id', :o, true)"), {"o": str(org)}
        )
        op = await plan_org(
            conn, plan, org, processed, keep_users=keep_users, keep_orphans=keep_orphans
        )
        if op.rows:
            await apply_org(
                conn,
                plan,
                op,
                audit_summary={
                    "tables": len(op.nonzero()),
                    "rows": op.rows,
                    "users_deleted": op.users.deleted,
                },
            )
    return op


async def run(
    engine: AsyncEngine,
    *,
    selected: Sequence[uuid.UUID],
    keep_users: Sequence[uuid.UUID] = (),
    keep_orphans: bool = False,
    apply: bool = False,
    database: str = "",
    environment: str = "local",
    bucket: str = "opsgrids-public",
    out: Callable[[str], None] = print,
) -> Result:
    """Plan the selected organisations read-only; under `apply`, delete them one transaction each,
    in id order (a child organisation before its parent, if both are selected), stopping at the
    first failure. Prints counts, returns the summary, disposes of the engine. Raises `Refusal` or
    `SchemaDrift` before any write if a rule is not met."""
    selected = distinct(selected)
    mode = "apply" if apply else "dry-run"
    result = Result(mode, database, environment, selected=len(selected))
    try:
        plan, selected, plans, result.organisations_before = await _read_phase(
            engine,
            selected,
            keep_users=keep_users,
            keep_orphans=keep_orphans,
            want_plans=not apply,
        )
        if not apply:
            for op in plans:
                out(json.dumps(_org_line(op, mode, status_of(op, mode))))
                for note in _external_notes(op, bucket):
                    out(note)
                result.rows += op.rows
                result.users_deleted += op.users.deleted
                result.removed += 1 if op.rows else 0
                result.already_clean += 1 if status_of(op, mode) == "already-clean" else 0
                result.not_found += 1 if status_of(op, mode) == "not-found" else 0
        else:
            for i, org in enumerate(selected):
                try:
                    op = await _purge_one(
                        engine,
                        plan,
                        org,
                        selected[: i + 1],
                        keep_users=keep_users,
                        keep_orphans=keep_orphans,
                    )
                except Refusal as exc:
                    if not result.removed:
                        raise  # nothing written yet: a refusal, exit 2
                    # Earlier organisations are committed: that is a run that failed part-way
                    # (exit 1, with the summary), not "refused before anything was written".
                    result.failed += 1
                    result.not_attempted = len(selected) - i - 1
                    out(json.dumps(_error_line(org, exc)))
                    break
                except Exception as exc:
                    result.failed += 1
                    result.not_attempted = len(selected) - i - 1
                    out(json.dumps(_error_line(org, exc)))
                    break
                out(json.dumps(_org_line(op, mode, status_of(op, mode))))
                for note in _external_notes(op, bucket):
                    out(note)
                result.rows += op.rows
                result.users_deleted += op.users.deleted
                result.removed += 1 if op.rows else 0
                result.already_clean += 1 if status_of(op, mode) == "already-clean" else 0
                result.not_found += 1 if status_of(op, mode) == "not-found" else 0
        await _finish(engine, result, out)
        return result
    finally:
        await engine.dispose()


async def _finish(engine: AsyncEngine, result: Result, out: Callable[[str], None]) -> None:
    """The closing summary, including what is left."""
    org, members, users = _t(ORGS), _t(MEMBERSHIPS), _t(USERS)
    async with engine.connect() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        result.organisations_remaining = int(
            (await conn.execute(text(f"SELECT count(*) FROM {org.sql}"))).scalar_one()  # noqa: S608
        )
        result.users_without_organisation = int(
            (
                await conn.execute(
                    text(
                        f"SELECT count(*) FROM {users.sql} u WHERE NOT EXISTS "  # noqa: S608
                        f"(SELECT 1 FROM {members.sql} m WHERE m.user_id = u.id)"
                    )
                )
            ).scalar_one()
        )
    apply = result.mode == "apply"
    out(
        json.dumps(
            {
                "mode": result.mode,
                "database": result.database,
                "environment": result.environment,
                "selected": result.selected,
                "organisations_before": result.organisations_before,
                "organisations_remaining": result.organisations_remaining,
                "users_without_any_organisation_remaining": result.users_without_organisation,
                ("organisations_deleted" if apply else "organisations_with_rows"): result.removed,
                "already_clean": result.already_clean,
                "not_found": result.not_found,
                "failed": result.failed,
                "not_attempted": result.not_attempted,
                ("rows_deleted" if apply else "rows_would_delete"): result.rows,
                ("users_deleted" if apply else "users_would_delete"): result.users_deleted,
            }
        )
    )
    out("")
    out("Outside the database (this tool does not touch these):")
    for line in GENERAL_LEFTOVERS:
        out(f"  - {line}")
    if result.not_found:
        out(
            f"{result.not_found} id(s) name no organisation and no rows, and no earlier purge of "
            "them is on record: check them for typos."
        )
    if apply:
        out("Verify: run the tool with no ids to list what is left.")
    else:
        out(
            "dry run only: nothing was deleted. Re-run with --apply (and its three flags) to "
            "delete exactly the rows above."
        )


# ------------------------------------------------------------------------------------ CLI
class _Once(argparse.Action):
    """A flag that may be given once. argparse's default keeps the last of a repeated flag and says
    nothing, so `--confirm-count 3 ... --confirm-count 1` (a shell alias, an edited history line)
    would silently be 1; here it is an error."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            parser.error(f"{option_string} was given more than once")
        setattr(namespace, self.dest, values)


def build_parser() -> argparse.ArgumentParser:
    # No abbreviations: `--ap` for `--apply` or `--i-know-this-is-the-live-d` is not a flag the
    # owner typed on purpose, and the gates exist to be typed in full.
    p = argparse.ArgumentParser(
        description=(
            "List organisations, or remove the ones you name (a dry run unless --apply). "
            "Owner-run and destructive: see the module docstring and the RUNBOOK."
        ),
        allow_abbrev=False,
    )
    sel = p.add_argument_group("selection (nothing is ever selected for you)")
    sel.add_argument(
        "--org-id",
        action="append",
        type=canonical_uuid,
        default=[],
        metavar="UUID",
        help="an organisation to remove; repeatable",
    )
    sel.add_argument(
        "--ids-file",
        action="append",
        type=Path,
        default=[],
        metavar="FILE",
        help="organisation ids to remove, one per line, # comments allowed; repeatable, all read",
    )
    sel.add_argument(
        "--keep",
        action="append",
        type=canonical_uuid,
        default=[],
        metavar="UUID",
        help="an organisation that must never be removed; repeatable",
    )
    sel.add_argument(
        "--keep-file",
        action="append",
        type=Path,
        default=[],
        metavar="FILE",
        help="organisation ids that must never be removed, one per line; repeatable, all read",
    )
    sel.add_argument(
        "--keep-user",
        action="append",
        type=canonical_uuid,
        default=[],
        metavar="UUID",
        help="a user that is never deleted, even if left in no organisation",
    )
    sel.add_argument(
        "--keep-orphan-users",
        action="store_true",
        help="remove memberships only: keep every user account",
    )
    lst = p.add_argument_group("listing (no ids given)")
    lst.add_argument("--json", action="store_true", help="list as JSON lines (names included)")
    lst.add_argument(
        "--candidates",
        action="store_true",
        help="print the 'LIKELY TEST' rows as an ids file to review and edit",
    )
    p.add_argument(
        "--explain",
        action="store_true",
        help="print every table, what selects its rows and the delete order, then exit",
    )
    gate = p.add_argument_group(
        "deleting (--apply needs a count, a backup attestation and, on a live database, its flag)"
    )
    gate.add_argument(
        "--apply", action="store_true", help="actually delete (the default is a read-only dry run)"
    )
    gate.add_argument(
        "--confirm-count",
        action=_Once,
        type=int,
        metavar="N",
        help="the number of organisations you are removing; must match; given once",
    )
    gate.add_argument(
        "--i-took-a-backup",
        action="store_true",
        help="attest that you took a backup just now (make edge-backup)",
    )
    gate.add_argument(
        "--i-know-this-is-the-live-database",
        action="store_true",
        help="required for --apply on a production, staging or uat environment or a remote host",
    )
    return p


class Settings(Protocol):
    environment: str

    @property
    def admin_dsn(self) -> str: ...


def _default_settings() -> Settings:
    from platform_core.config import ServiceSettings

    cfg: Settings = ServiceSettings(
        service_name="purge-test-orgs", db_schema="public", db_admin_role="opsgrids_admin"
    )
    return cfg


def _database_label(dsn: str) -> str:
    """Where the tool is aimed, without the credentials: `host:port/database`."""
    url = make_url(dsn)
    return f"{url.host}:{url.port}/{url.database}"


def _read_ids(paths: Iterable[Path]) -> list[uuid.UUID]:
    """Every id in every file given (a repeated `--keep-file` keeps all of them, not the last)."""
    found: list[uuid.UUID] = []
    for path in paths:
        try:
            found += parse_ids(path.read_text(encoding="utf-8"), source=str(path))
        except (OSError, UnicodeDecodeError) as exc:
            raise Refusal(f"cannot read {path}: {type(exc).__name__}") from None
    return found


def _refuse(problems: Iterable[str]) -> int:
    for problem in problems:
        print(f"refused: {problem}", file=sys.stderr)
    return 2


def main(
    argv: list[str] | None = None,
    *,
    cfg: Settings | None = None,
    engine_factory: Callable[[str], AsyncEngine] = create_async_engine,
    out: Callable[[str], None] = print,
) -> int:
    """Exit 0 done, 1 a run failed part-way (each organisation whole or untouched), 2 refused
    before anything was written."""
    args = build_parser().parse_args(argv)
    try:
        keep = distinct([*args.keep, *_read_ids(args.keep_file)])
        selected = distinct([*args.org_id, *_read_ids(args.ids_file)])
    except Refusal as exc:
        return _refuse([str(exc)])

    if args.apply and not selected:
        return _refuse(["--apply needs at least one --org-id or --ids-file"])
    cfg = cfg or _default_settings()
    dsn = cfg.admin_dsn
    host = make_url(dsn).host
    database = _database_label(dsn)

    if args.apply:
        problems = check_apply_gates(
            args, selected, keep=keep, environment=cfg.environment, host=host
        )
    elif selected:
        # A dry run enforces every rule that needs no flag, so a mistake shows up now and not
        # first at --apply time.
        problems = check_selection(selected, keep, confirm_count=len(selected))
    else:
        problems = []
    if problems:
        return _refuse(problems)

    try:
        engine = engine_factory(dsn)
        if args.explain:
            return asyncio.run(_explain(engine, out))
        if not selected:
            return asyncio.run(_list(engine, args, keep, database, cfg.environment, out))
        result = asyncio.run(
            run(
                engine,
                selected=selected,
                keep_users=args.keep_user,
                keep_orphans=args.keep_orphan_users,
                apply=args.apply,
                database=database,
                environment=cfg.environment,
                bucket=os.environ.get("S3_PUBLIC_BUCKET") or "opsgrids-public",
                out=out,
            )
        )
    except (Refusal, SchemaDrift) as exc:
        return _refuse([str(exc)])
    except Exception as exc:
        # Only the class and, for a database error, its SQLSTATE: the message can quote a value.
        orig = getattr(exc, "orig", exc)
        cause = getattr(orig, "__cause__", None) or orig
        state = getattr(cause, "sqlstate", None)
        print(
            f"failed: {type(exc).__name__}" + (f" (sqlstate {state})" if state else ""),
            file=sys.stderr,
        )
        return 1
    return 1 if result.failed else 0


async def _list(
    engine: AsyncEngine,
    args: argparse.Namespace,
    keep: Sequence[uuid.UUID],
    database: str,
    environment: str,
    out: Callable[[str], None],
) -> int:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            rows = await list_orgs(conn, keep)
    finally:
        await engine.dispose()
    if args.candidates:
        out(render_candidates(rows))
    elif args.json:
        for r in rows:
            out(
                json.dumps(
                    {
                        "id": str(r.id),
                        "name": r.name,
                        "slug": r.slug,
                        "status": r.status,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "members": r.members,
                        "subscriptions": r.subscriptions,
                        "captured_charges": r.captured_charges,
                        "hint": r.hint,
                        "reasons": r.reasons,
                        "kept": r.kept,
                    }
                )
            )
    else:
        out(render_listing(rows, database, environment))
    return 0


async def _explain(engine: AsyncEngine, out: Callable[[str], None]) -> int:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            plan = await build_plan(conn)
    finally:
        await engine.dispose()
    out(f"{len(plan.order)} tables, deleted in this order (children first):")
    for i, table in enumerate(plan.order, start=1):
        out(f"{i:>4}  {table}   WHERE {plan.nodes[table].predicate(plan.nodes)}")
        guards = ", ".join(t.name for t in plan.triggers.get(table, []))
        if guards:
            out(f"      delete guard lifted inside the transaction when rows exist: {guards}")
    bookkeeping = [n for n in plan.unreached if n.split(".", 1)[1] in BOOKKEEPING_TABLES]
    others = [n for n in plan.unreached if n not in bookkeeping]
    out("")
    out(
        f"{len(others)} other tables are NOT reached: no organization_id, and no foreign key "
        "from them into a table that is deleted from. Whatever a tenant has in them stays:"
    )
    for name in others:
        out(f"      {name}")
    out(
        f"      (and {len(bookkeeping)} bookkeeping tables, `{'` and `'.join(BOOKKEEPING_TABLES)}` "
        "in every schema: migration state, and an idempotency ledger with no organisation on it)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
