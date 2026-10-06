"""The warehouse-map RUNBOOK's "coordinate key found" rollback trigger (spec §10).

After the release the operator runs one SQL statement and rolls back if either count is above 0.
The statement exists to catch a **worker's** GPS fix leaking into an audit row or an outbox
payload. A **site's** position is approved data and is written, on purpose, as `lat_micro` /
`lng_micro` keys in the `before` / `after` of every `warehouse.updated` audit row
(`update_warehouse`; a PATCH of the position). A detector that counts those rows fires on the
release's headline feature the first time the owner uses "Set location" — a healthy release rolled
back, or a check the operator learns to ignore, which is the same as having none.

* The static tests read the statement out of the RUNBOOK (the page the operator copies from, not a
  copy of it here) and pin its shape: the audit half leaves out the `warehouse` entity; the outbox
  half does not (a `warehouse.updated` event carries field *names*, never a key, so it needs no
  exception and should not get one); the page says why.
* The last test runs that exact statement against a real Postgres and is opt-in, like the purge
  tool's: `OPSGRIDS_RUNBOOK_TEST_DATABASE_URL` must point at a migrated, throwaway database and a
  role that bypasses row-level security (the dev superuser). Everything it inserts is rolled back,
  and it compares counts before and after its own rows, so other data in the database does not
  matter.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPO_ROOT / "infrastructure" / "edge" / "RUNBOOK.md"
TRIGGER = "a **coordinate key** is found where none may be"

LAT = 12_971_600
LNG = 77_594_600


def _trigger_text() -> str:
    """The rollback bullet: from its first words up to the next top-level bullet."""
    text = RUNBOOK.read_text()
    assert text.count(TRIGGER) == 1, "the coordinate-key rollback trigger moved or was duplicated"
    start = text.index(TRIGGER)
    end = text.find("\n- ", start)
    return text[start : end if end != -1 else len(text)]


def _detector_sql() -> str:
    block = re.search(r"```sql\n(.*?)```", _trigger_text(), re.DOTALL)
    assert block is not None, "the coordinate-key trigger no longer carries its SQL"
    return textwrap.dedent(block.group(1)).strip()


def _halves(sql: str) -> tuple[str, str]:
    audit, sep, outbox = sql.partition("AS audit_rows")
    assert sep, "the statement lost its `audit_rows` column"
    return audit, outbox


def test_the_statement_counts_both_tables_and_nothing_else() -> None:
    sql = _detector_sql()
    audit, outbox = _halves(sql)
    assert "operations.audit_logs" in audit
    assert "operations.outbox" in outbox
    assert "AS outbox_rows" in outbox
    assert sql.count(";") == 1 and sql.endswith(";"), "one statement, ended once"
    for half in (audit, outbox):
        assert "lat_micro" in half and "lng_micro" in half


def test_the_audit_half_leaves_out_the_warehouse_entity() -> None:
    """The finding: with no filter, one PATCH of a site's position makes `audit_rows` 1."""
    audit, _ = _halves(_detector_sql())
    assert re.search(r"\bentity\s*(<>|!=)\s*'warehouse'", audit), (
        "site coordinates legitimately sit in `warehouse.updated` audit rows; the audit count "
        "must exclude entity 'warehouse' or it fires on a healthy release"
    )


def test_the_exclusion_cannot_swallow_the_coordinate_tests_through_operator_precedence() -> None:
    """`entity <> 'warehouse' AND a OR b OR c` is `(entity <> 'warehouse' AND a) OR b OR c`: the
    exclusion would then protect only the first key. The four key tests must sit in one group."""
    audit, _ = _halves(_detector_sql())
    where = audit.split("WHERE", 1)[1]
    parts = re.split(r"\bentity\s*(?:<>|!=)\s*'warehouse'", where, maxsplit=1)
    assert len(parts) == 2, "the audit count has no `entity <> 'warehouse'` filter"
    after_exclusion = parts[1]
    assert re.match(r"\s*AND\s*\(", after_exclusion), "parenthesise the OR chain after the filter"
    assert after_exclusion.count("jsonb_path_exists") == 4
    # Everything after the opening paren is inside that group: the last ')' closes the subquery.
    assert after_exclusion.rstrip().endswith("))")


def test_the_outbox_half_has_no_exception() -> None:
    _, outbox = _halves(_detector_sql())
    assert "warehouse" not in outbox.replace("operations.outbox", "")


def test_the_page_says_why_site_coordinates_are_not_a_hit() -> None:
    text = _trigger_text()
    assert "warehouse.updated" in text
    assert "site" in text.lower()
    assert "worker coordinate" in text


# --------------------------------------------------------------------------- real Postgres (opt-in)
REAL_DSN = os.environ.get("OPSGRIDS_RUNBOOK_TEST_DATABASE_URL")


@pytest.mark.skipif(
    not REAL_DSN,
    reason="set OPSGRIDS_RUNBOOK_TEST_DATABASE_URL to a migrated throwaway database",
)
async def test_against_a_real_postgres_a_site_edit_is_not_a_hit_and_a_worker_fix_is() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    assert REAL_DSN is not None
    detector = _detector_sql()
    org = uuid.uuid4()
    warehouse_id = uuid.uuid4()
    move_id = uuid.uuid4()

    async def counts(conn) -> tuple[int, int]:  # type: ignore[no-untyped-def]
        row = (await conn.execute(text(detector))).one()
        return int(row.audit_rows), int(row.outbox_rows)

    async def audit(conn, *, action, entity, entity_id, before, after) -> None:  # type: ignore[no-untyped-def]
        await conn.execute(
            text(
                "INSERT INTO operations.audit_logs "
                "(id, organization_id, action, entity, entity_id, before, after) "
                "VALUES (gen_random_uuid(), :o, :a, :e, :i, CAST(:b AS jsonb), CAST(:f AS jsonb))"
            ),
            {
                "o": org,
                "a": action,
                "e": entity,
                "i": entity_id,
                "b": json.dumps(before) if before is not None else None,
                "f": json.dumps(after) if after is not None else None,
            },
        )

    async def outbox(conn, *, event_type, aggregate_type, aggregate_id, payload) -> None:  # type: ignore[no-untyped-def]
        await conn.execute(
            text(
                "INSERT INTO operations.outbox (id, organization_id, event_type, event_version, "
                "aggregate_type, aggregate_id, payload, attempts) "
                "VALUES (gen_random_uuid(), :o, :t, 1, :g, :i, CAST(:p AS jsonb), 0)"
            ),
            {
                "o": org,
                "t": event_type,
                "g": aggregate_type,
                "i": aggregate_id,
                "p": json.dumps(payload),
            },
        )

    engine = create_async_engine(REAL_DSN)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.organization_id', :o, true)"), {"o": str(org)}
            )
            base_audit, base_outbox = await counts(conn)

            # The owner's "Set location", then clearing it: exactly what `update_warehouse` writes
            # (before = the old values of the changed fields, after = the patch), and its event,
            # which lists field names only.
            await audit(
                conn,
                action="warehouse.updated",
                entity="warehouse",
                entity_id=warehouse_id,
                before={"lat_micro": None, "lng_micro": None},
                after={"lat_micro": LAT, "lng_micro": LNG},
            )
            await audit(
                conn,
                action="warehouse.updated",
                entity="warehouse",
                entity_id=warehouse_id,
                before={"lat_micro": LAT, "lng_micro": LNG},
                after={"lat_micro": None, "lng_micro": None},
            )
            await outbox(
                conn,
                event_type="operations.inventory.warehouse.updated",
                aggregate_type="warehouse",
                aggregate_id=warehouse_id,
                payload={
                    "organization_id": str(org),
                    "warehouse_id": str(warehouse_id),
                    "changed": ["lat_micro", "lng_micro"],
                },
            )
            # The put-away's own rows: the audited reveal lists field *names* in an array.
            await audit(
                conn,
                action="entity.sensitive_read",
                entity="inventory_move",
                entity_id=move_id,
                before=None,
                after={"fields": ["lat_micro", "lng_micro", "accuracy_m"]},
            )
            assert await counts(conn) == (base_audit, base_outbox), (
                "a site-position edit, its event and an audited reveal are not coordinate leaks"
            )

            # A worker's fix in a put-away audit row, at the top level and nested: both are hits.
            await audit(
                conn,
                action="inventory_move.posted",
                entity="inventory_move",
                entity_id=move_id,
                before=None,
                after={"lat_micro": LAT, "lng_micro": LNG},
            )
            await audit(
                conn,
                action="inventory_move.posted",
                entity="inventory_move",
                entity_id=move_id,
                before={"geo": {"lng_micro": LNG}},
                after=None,
            )
            assert await counts(conn) == (base_audit + 2, base_outbox)

            # ... and in an outbox payload.
            await outbox(
                conn,
                event_type="operations.inventory.move.posted",
                aggregate_type="inventory_move",
                aggregate_id=move_id,
                payload={"move_id": str(move_id), "geo": {"lat_micro": LAT}},
            )
            assert await counts(conn) == (base_audit + 2, base_outbox + 1)

            await conn.rollback()  # nothing this test wrote is ever committed
    finally:
        await engine.dispose()
