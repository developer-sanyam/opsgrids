"""`tools/purge_putaway_captures.py` — the admin-run 30-day erasure of storage put-away captures
(spec D3). Pins the promises the owner approved, not the SQL's spelling:

* **Dry run is the default and cannot delete.** The plan transaction is read-only before it does
  anything else; `--apply` is the only way a `DELETE` is ever issued.
* **30 days, on `created_at`, from one cutoff.** A 29-day-old row stays, a 31-day-old one goes, and
  the number of rows it says are eligible is the number it deletes.
* **Counts only.** No statement reads a coordinate (only `lat_micro IS NOT NULL`), and nothing it
  prints contains one, a worker, a move or the connection string's password.
* **`--organization-id` really limits it**, and the window is not a flag.

The tool talks to Postgres through SQLAlchemy. This file runs it against an in-memory stand-in for
the one table (no database, like everything in `tests/infra`), which enforces the one Postgres rule
that matters here: a `DELETE` in a read-only transaction fails. Whether the SQL means what the
stand-in says is proved against a real Postgres by the last test, which needs
`OPSGRIDS_PURGE_TEST_DATABASE_URL` to point at a migrated, throwaway database (a role allowed to
`SET session_replication_role`, i.e. the dev superuser) and is skipped without it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "purge_putaway_captures.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("purge_putaway_captures", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["purge_putaway_captures"] = module
    spec.loader.exec_module(module)
    return module


purge = _load()

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
ORG_A = uuid.UUID("aaaaaaaa-0000-0000-0000-00000000000a")
ORG_B = uuid.UUID("bbbbbbbb-0000-0000-0000-00000000000b")
#: Distinctive enough that finding one in any output would be unmistakable.
LAT = 12_345_678_901
LNG = 98_765_432_109


# --------------------------------------------------------------------------- an in-memory table
@dataclass
class Capture:
    id: int
    organization_id: uuid.UUID
    age_days: float
    lat_micro: int | None = LAT

    @property
    def created_at(self) -> datetime:
        return NOW - timedelta(days=self.age_days)


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows

    def scalar_one(self) -> Any:
        return self._rows[0][0]


@dataclass
class FakeDb:
    rows: list[Capture]
    #: One entry per transaction, each the SQL text of its statements in order.
    transactions: list[list[str]] = field(default_factory=list)
    params: list[dict[str, Any]] = field(default_factory=list)
    disposed: bool = False

    def begin(self) -> _Txn:
        return _Txn(self)

    async def dispose(self) -> None:
        self.disposed = True

    @property
    def statements(self) -> list[str]:
        return [sql for txn in self.transactions for sql in txn]

    def deletes(self) -> list[str]:
        return [sql for sql in self.statements if sql.lstrip().upper().startswith("DELETE")]


class _Txn:
    def __init__(self, db: FakeDb) -> None:
        self.db = db
        self.read_only = False

    async def __aenter__(self) -> _Txn:
        self.db.transactions.append([])
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(statement).split())
        params = params or {}
        self.db.transactions[-1].append(sql)
        self.db.params.append(params)
        org = params.get("org")

        def eligible() -> list[Capture]:
            rows = [r for r in self.db.rows if r.created_at < params["cutoff"]]
            return [r for r in rows if org is None or r.organization_id == org]

        if sql == "SET TRANSACTION READ ONLY":
            self.read_only = True
            return _Result([])
        if sql.startswith("SELECT now()"):
            return _Result([(NOW - timedelta(days=params["days"]),)])
        if "GROUP BY organization_id" in sql:
            grouped: dict[uuid.UUID, list[Capture]] = {}
            for r in eligible():
                grouped.setdefault(r.organization_id, []).append(r)
            return _Result(
                [
                    (o, len(g), sum(1 for r in g if r.lat_micro is not None))
                    for o, g in sorted(grouped.items())
                ]
            )
        if sql.startswith("SELECT count(*)"):
            kept = [r for r in self.db.rows if r.created_at >= params["cutoff"]]
            return _Result([(len([r for r in kept if org is None or r.organization_id == org]),)])
        if sql.startswith("DELETE FROM"):
            if self.read_only:
                raise RuntimeError("cannot execute DELETE in a read-only transaction")
            batch = sorted(eligible(), key=lambda r: (r.created_at, r.id))[: params["batch"]]
            gone = {r.id for r in batch}
            self.db.rows = [r for r in self.db.rows if r.id not in gone]
            return _Result([(r.organization_id,) for r in batch])
        raise AssertionError(f"the tool issued a statement this stand-in does not know: {sql}")


def _rows() -> list[Capture]:
    return [
        Capture(1, ORG_A, 40),  # old, with a fix
        Capture(2, ORG_A, 31, lat_micro=None),  # old, no fix
        Capture(3, ORG_A, 29),  # inside the window
        Capture(4, ORG_B, 90),  # old, with a fix
        Capture(5, ORG_B, 1),  # recent
    ]


async def _run(
    db: FakeDb, *, apply: bool, org: uuid.UUID | None = None, batch_size: int = 5000
) -> dict[str, Any]:
    return await purge.run(
        db,
        schema="operations",
        apply=apply,
        organization_id=org,
        batch_size=batch_size,
        database="postgres:5432/opsgrids",
    )


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


# --------------------------------------------------------------------------- dry run
async def test_a_dry_run_deletes_nothing_and_says_what_it_would(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = FakeDb(_rows())
    summary = await _run(db, apply=False)

    assert db.deletes() == []
    assert len(db.rows) == 5
    assert summary["mode"] == "dry-run"
    assert (summary["eligible"], summary["with_coordinates"], summary["retained"]) == (3, 2, 2)
    assert summary["deleted"] == 0
    assert "nothing was deleted" in capsys.readouterr().out


async def test_the_plan_transaction_is_read_only_before_it_does_anything_else() -> None:
    db = FakeDb(_rows())
    await _run(db, apply=True)
    assert db.transactions[0][0] == "SET TRANSACTION READ ONLY"


async def test_the_connection_is_always_released() -> None:
    db = FakeDb(_rows())
    await _run(db, apply=False)
    assert db.disposed


# --------------------------------------------------------------------------- apply
async def test_apply_deletes_what_is_older_than_thirty_days_and_nothing_else() -> None:
    db = FakeDb(_rows())
    summary = await _run(db, apply=True)

    assert sorted(r.id for r in db.rows) == [3, 5]  # the 29-day and the 1-day row survive
    assert summary["eligible"] == summary["deleted"] == 3
    assert summary["mode"] == "apply"
    assert summary["retention_days"] == purge.RETENTION_DAYS == 30


async def test_every_statement_uses_the_one_cutoff_taken_at_the_start() -> None:
    db = FakeDb(_rows())
    await _run(db, apply=True, batch_size=1)
    cutoffs = {p["cutoff"] for p in db.params if "cutoff" in p}
    assert cutoffs == {NOW - timedelta(days=30)}


async def test_apply_deletes_in_batches_each_in_its_own_transaction() -> None:
    db = FakeDb([Capture(i, ORG_A, 60 + i) for i in range(1, 6)])
    summary = await _run(db, apply=True, batch_size=2)

    assert db.rows == []
    assert summary["deleted"] == 5
    delete_transactions = [t for t in db.transactions if any(s.startswith("DELETE") for s in t)]
    assert len(delete_transactions) == 4  # 2 + 2 + 1, then the empty batch that ends the loop
    assert all(len(t) == 1 for t in delete_transactions)


async def test_a_second_apply_finds_nothing_to_do(capsys: pytest.CaptureFixture[str]) -> None:
    db = FakeDb(_rows())
    await _run(db, apply=True)
    capsys.readouterr()
    again = await _run(db, apply=True)
    assert (again["eligible"], again["deleted"], again["organizations"]) == (0, 0, 0)


async def test_organization_id_limits_both_the_plan_and_the_delete() -> None:
    db = FakeDb(_rows())
    summary = await _run(db, apply=True, org=ORG_B)

    assert summary["organization_filter"] == str(ORG_B)
    assert summary["eligible"] == summary["deleted"] == 1
    assert sorted(r.id for r in db.rows) == [1, 2, 3, 5]  # org A untouched, even its old rows
    assert all("organization_id = :org" in sql for sql in db.deletes())
    assert all(p.get("org") == ORG_B for p in db.params if "cutoff" in p)


async def test_the_retained_count_respects_the_organization_filter() -> None:
    summary = await _run(FakeDb(_rows()), apply=False, org=ORG_A)
    assert summary["retained"] == 1


# --------------------------------------------------------------------------- counts only
async def test_nothing_it_prints_is_a_coordinate_a_connection_string_or_a_row(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _run(FakeDb(_rows()), apply=True)
    captured = capsys.readouterr()
    for needle in (str(LAT), str(LNG), "lat_micro", "lng_micro", "accuracy", "password", "://"):
        assert needle not in captured.out + captured.err
    for line in (json.loads(x) for x in captured.out.splitlines() if x.startswith("{")):
        allowed = {
            "organization_id",
            "eligible",
            "with_coordinates",
            "deleted",
            "mode",
            "database",
            "retention_days",
            "cutoff",
            "organization_filter",
            "organizations",
            "retained",
        }
        assert set(line) <= allowed


async def test_no_statement_reads_a_coordinate_or_any_column_but_the_ones_it_counts() -> None:
    db = FakeDb(_rows())
    await _run(db, apply=True)
    for sql in db.statements:
        without_the_count = sql.replace("lat_micro IS NOT NULL", "")
        assert "lat_micro" not in without_the_count, sql
        assert "lng_micro" not in without_the_count, sql
        assert "accuracy_m" not in without_the_count, sql
        assert "SELECT *" not in sql
        assert "RETURNING *" not in sql
        # The only thing a DELETE hands back is which organization the row belonged to.
        if sql.startswith("DELETE"):
            assert sql.endswith("RETURNING organization_id")


# --------------------------------------------------------------------------- the CLI
def test_the_default_is_a_dry_run() -> None:
    args = purge.build_parser().parse_args([])
    assert args.apply is False
    assert args.organization_id is None


def test_apply_and_organization_id_parse() -> None:
    args = purge.build_parser().parse_args(["--apply", "--organization-id", str(ORG_A)])
    assert args.apply is True
    assert args.organization_id == ORG_A


def test_an_organization_id_that_is_not_a_uuid_is_refused_before_anything_connects(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        purge.main(["--apply", "--organization-id", "not-a-uuid"])
    assert exit_info.value.code == 2
    assert "invalid" in capsys.readouterr().err


def test_the_retention_window_is_not_a_command_line_option() -> None:
    """A purge that can be pointed at 0 days is a purge that can erase evidence by typo."""
    option_strings = {s for action in purge.build_parser()._actions for s in action.option_strings}
    assert option_strings == {"-h", "--help", "--apply", "--organization-id"}
    assert purge.RETENTION_DAYS == 30


def test_the_database_label_never_carries_the_password() -> None:
    label = purge._database_label("postgresql+asyncpg://opsgrids_admin:hunter2@db:5432/opsgrids")
    assert label == "db:5432/opsgrids"


def test_a_schema_that_is_not_a_plain_identifier_is_refused() -> None:
    with pytest.raises(ValueError, match="refusing schema name"):
        purge._qualified('operations"; DROP TABLE x; --')
    assert purge._qualified("operations") == '"operations".putaway_captures'


async def test_a_non_positive_batch_size_is_refused() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        await _run(FakeDb(_rows()), apply=True, batch_size=0)


def test_the_tool_connects_as_the_admin_role_and_nothing_else() -> None:
    """The one tool in this directory that uses `admin_dsn`, because `putaway_captures` is
    append-only by privilege and the owner role is the only one still holding `DELETE`."""
    source = TOOL.read_text()
    assert "cfg.admin_dsn" in source
    for other in ("cfg.app_dsn", "directory_dsn", "outbox_dsn"):
        assert other not in re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


# --------------------------------------------------------------------------- real Postgres (opt-in)
REAL_DSN = os.environ.get("OPSGRIDS_PURGE_TEST_DATABASE_URL")


@pytest.mark.skipif(
    not REAL_DSN, reason="set OPSGRIDS_PURGE_TEST_DATABASE_URL to a migrated throwaway database"
)
async def test_against_a_real_postgres_the_sql_means_what_the_stand_in_says(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only this test's own rows (two random organizations) are inserted, counted and deleted, so
    it is safe against a database other work is using; it cleans up after itself."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    assert REAL_DSN is not None
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    setup = create_async_engine(REAL_DSN)
    try:
        async with setup.begin() as conn:
            await conn.execute(
                text("SET session_replication_role = replica")
            )  # FKs: not under test
            for org, age_days, with_fix in [
                (org_a, 40, True),
                (org_a, 31, False),
                (org_a, 29, True),
                (org_b, 90, True),
                (org_b, 1, True),
            ]:
                await conn.execute(
                    text(
                        "INSERT INTO operations.putaway_captures (id, organization_id, move_id, "
                        "location_id, created_by, lat_micro, lng_micro, captured_at, created_at) "
                        "VALUES (gen_random_uuid(), :o, gen_random_uuid(), gen_random_uuid(), "
                        "gen_random_uuid(), :lat, :lng, now(), now() - make_interval(days => :d))"
                    ),
                    {
                        "o": org,
                        "lat": LAT % 90_000_000 if with_fix else None,
                        "lng": LNG % 180_000_000 if with_fix else None,
                        "d": age_days,
                    },
                )

        # Dry run for one organization: counts, no change.
        dry = await purge.run(
            create_async_engine(REAL_DSN), schema="operations", apply=False, organization_id=org_a
        )
        assert (dry["eligible"], dry["with_coordinates"], dry["retained"]) == (2, 1, 1)
        assert dry["deleted"] == 0

        # Apply for organization A only: the 29-day row and all of B's rows stay.
        done = await purge.run(
            create_async_engine(REAL_DSN),
            schema="operations",
            apply=True,
            organization_id=org_a,
            batch_size=1,
        )
        assert done["deleted"] == 2

        async with setup.begin() as conn:
            counts = dict(
                (
                    await conn.execute(
                        text(
                            "SELECT organization_id, count(*) FROM operations.putaway_captures "
                            "WHERE organization_id IN (:a, :b) GROUP BY organization_id"
                        ),
                        {"a": org_a, "b": org_b},
                    )
                ).all()
            )
        assert counts == {org_a: 1, org_b: 2}
        printed = capsys.readouterr().out
        assert str(LAT) not in printed
        assert "lat_micro" not in printed
    finally:
        async with setup.begin() as conn:
            await conn.execute(text("SET session_replication_role = replica"))
            await conn.execute(
                text("DELETE FROM operations.putaway_captures WHERE organization_id IN (:a, :b)"),
                {"a": org_a, "b": org_b},
            )
        await setup.dispose()
