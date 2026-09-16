"""Cross-service tests: does employee's real hierarchy publish path actually fit what approval's
consumer and backfill client expect. Neither service's own suite can answer this — employee's
suite only ever inspects its own outbox rows and its own endpoint's response; approval's suite
only ever hand-builds envelopes and HTTP bodies shaped the way *approval* assumes they are
shaped (`tests/helpers.py::employee_onboarded_envelope`, `test_employee_client.py`'s
`httpx.MockTransport`). Both are legitimate unit tests and neither proves the two halves fit.

**Why subprocess, not two ASGI apps in one process.** CLAUDE.md: "`import app` is ambiguous
across services... one process can only ever hold one." This file's own pytest process holds
approval's `app`; employee's `app` and `app.services` never enter it. Employee-side work runs in
a separate Python process (the shared repo venv, a different interpreter invocation) through two
kinds of script under `services/employee/tests/`:

- `emit_events.py` / `bump_employee.py` — one-shot: perform real writes through
  `app.services.create_employee` / `update_employee` / `transition_status`, then print the
  **actual** outbox rows those writes staged, as JSON. Used for the payload-contract and
  convergence tests (1, 2 below), which need employee's real `publish_event` payload, not a
  hand-written dict.
- `serve_hierarchy.py` — seeds two organizations, then runs the real `app.main:app` over a real
  TCP port with `uvicorn`, the same way `bin/api` does. Used for the backfill-shape,
  concurrency, and cross-tenant tests (3, 4, 5 below), which need approval's real
  `HttpHierarchySource` making a real HTTP call against employee's real
  `/internal/v1/hierarchy` route.

Envelopes are built from the printed outbox rows with `platform_core.events.build_envelope` —
the **same function** the real outbox relay calls — rather than reimplementing the wire format,
so what differs between this test and production is only the transport (direct call vs. a
broker round trip), never the shape.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backfill import run_backfill
from app.config import settings as approval_settings
from app.consumers import _apply_hierarchy_event
from app.employee_client import HttpHierarchySource
from platform_core.db import dispose_engine, init_engine
from platform_core.events import build_envelope

pytestmark = pytest.mark.integration

EMPLOYEE_DIR = Path(__file__).resolve().parents[2] / "employee"

# --------------------------------------------------------------------- subprocess plumbing


def _free_port() -> int:
    """A port the OS says is free right now (same technique as `e2e/conftest.py::_free_port`:
    bind to 0, release immediately)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_employee_script(module: str, args: list[str], *, timeout: float = 60.0) -> list[dict]:
    """Run a one-shot `services/employee/tests/<module>.py` script and parse its stdout as
    newline-delimited JSON — one employee outbox row per line, in commit order."""
    result = subprocess.run(  # noqa: S603  (fixed argv, sys.executable, no shell — a sibling
        # service's own test helper, the same trust boundary as importing its code would be)
        [sys.executable, "-m", f"tests.{module}", *args],
        cwd=str(EMPLOYEE_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, (
        f"tests.{module} exited {result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _emit_events(org_id: uuid.UUID, employee_no: str) -> list[dict]:
    """Four real writes on one fresh employee: onboarded (v0), updated (v1), updated (v2),
    transitioned (v3) — see `tests/emit_events.py`."""
    return _run_employee_script("emit_events", ["--org", str(org_id), "--employee-no", employee_no])


def _bump_employee(org_id: uuid.UUID, employee_id: uuid.UUID, location: str) -> dict:
    rows = _run_employee_script(
        "bump_employee",
        ["--org", str(org_id), "--employee", str(employee_id), "--location", location],
    )
    assert len(rows) == 1
    return rows[0]


def _envelope_from_outbox_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the wire envelope from a printed outbox row using the platform's own
    `build_envelope` — the exact function the real relay calls — so nothing about the wire
    shape is reimplemented here."""
    from types import SimpleNamespace

    row = SimpleNamespace(
        id=uuid.UUID(raw["id"]),
        event_type=raw["event_type"],
        event_version=raw["event_version"],
        organization_id=uuid.UUID(raw["organization_id"]),
        aggregate_type=raw["aggregate_type"],
        aggregate_id=uuid.UUID(raw["aggregate_id"]),
        created_at=datetime.fromisoformat(raw["created_at"]),
        correlation_id=raw["correlation_id"],
        causation_id=uuid.UUID(raw["causation_id"]) if raw["causation_id"] else None,
        actor=raw["actor"],
        payload=raw["payload"],
    )
    return build_envelope(row, producer="employee")


async def _stored_node(
    session: AsyncSession, org_id: uuid.UUID, employee_id: uuid.UUID
) -> dict | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT source_version, display_name, status, location_code, "
                    "reporting_manager_id FROM approval.hierarchy_nodes "
                    "WHERE organization_id = :org AND employee_id = :emp"
                ),
                {"org": str(org_id), "emp": str(employee_id)},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """The consumer and the backfill both write through `session_scope`, which needs the
    process engine initialised — the app lifespan does this in production."""
    init_engine(approval_settings())
    yield
    await dispose_engine()


# =========================================================== 1. the payload contract


class TestEmployeesRealPublishPathSatisfiesApprovalsConsumerContract:
    async def test_a_real_event_carries_display_name_not_full_name(self, org_id: uuid.UUID) -> None:
        """The exact field-naming risk `app/consumers.py`'s module docstring calls out: the
        consumer parses `display_name`, and employee's model column is `full_name`. This reads
        the real payload `app/services.py::_hierarchy_snapshot` produced, not a hand-written
        dict, so a rename on either side that breaks the other fails here."""
        rows = _emit_events(org_id, "XC0001")
        onboarded = rows[0]["payload"]
        assert "display_name" in onboarded
        assert "full_name" not in onboarded

    async def test_a_real_event_carries_every_field_the_consumer_parses(
        self, org_id: uuid.UUID
    ) -> None:
        rows = _emit_events(org_id, "XC0002")
        required = {
            "employee_id",
            "reporting_manager_id",
            "dotted_line_manager_id",
            "status",
            "display_name",
            "employee_no",
            "location_code",
            "hierarchy_version",
        }
        for row in rows:
            assert required.issubset(row["payload"].keys()), row["event_type"]

    async def test_hierarchy_version_is_present_and_strictly_increasing_across_real_writes(
        self, org_id: uuid.UUID
    ) -> None:
        rows = _emit_events(org_id, "XC0003")
        versions = [row["payload"]["hierarchy_version"] for row in rows]
        assert all(isinstance(v, int) for v in versions)
        assert versions == sorted(versions)
        assert len(set(versions)) == len(versions), "each real write must carry a distinct version"

    async def test_a_real_onboarded_event_upserts_into_hierarchy_nodes(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        rows = _emit_events(org_id, "XC0004")
        envelope = _envelope_from_outbox_row(rows[0])

        await _apply_hierarchy_event(envelope)

        async with session.begin():
            stored = await _stored_node(
                session, org_id, uuid.UUID(rows[0]["payload"]["employee_id"])
            )
        assert stored is not None
        assert stored["source_version"] == 0
        assert stored["display_name"] == "Cross Service Employee"

    async def test_applying_the_full_real_sequence_leaves_the_last_writes_full_state(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        rows = _emit_events(org_id, "XC0005")
        employee_id = uuid.UUID(rows[0]["payload"]["employee_id"])

        for raw in rows:
            await _apply_hierarchy_event(_envelope_from_outbox_row(raw))

        async with session.begin():
            stored = await _stored_node(session, org_id, employee_id)
        last_payload = rows[-1]["payload"]
        assert stored == {
            "source_version": last_payload["hierarchy_version"],
            "display_name": last_payload["display_name"],
            "status": last_payload["status"],
            "location_code": last_payload["location_code"],
            "reporting_manager_id": None,
        }
        assert stored["display_name"] == "Cross Service Employee Renamed"
        assert stored["status"] == "active"
        assert stored["source_version"] == 3


# =========================================================== 2. convergence under redelivery


class TestConvergenceAcrossOrderingDuplicationAndLoss:
    """ADR-0006 §2's central claim — "full state, not a diff: a consumer that has missed or
    reordered an event converges on the next one it sees, and never needs to replay history" —
    exercised against a **real** sequence of employee writes, not a synthetic one. Each scenario
    gets its own fresh employee (its own `_emit_events` call) so the four are independent and
    order-agnostic against each other.
    """

    @staticmethod
    def _expected_final(rows: list[dict]) -> dict:
        last = rows[-1]["payload"]
        return {
            "source_version": last["hierarchy_version"],
            "display_name": last["display_name"],
            "status": last["status"],
            "location_code": last["location_code"],
            "reporting_manager_id": None,
        }

    async def test_in_order_delivery_converges_to_the_final_state(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        rows = _emit_events(org_id, "XO0001")
        employee_id = uuid.UUID(rows[0]["payload"]["employee_id"])

        for raw in rows:
            await _apply_hierarchy_event(_envelope_from_outbox_row(raw))

        async with session.begin():
            stored = await _stored_node(session, org_id, employee_id)
        assert stored == self._expected_final(rows)

    async def test_out_of_order_delivery_converges_to_the_same_final_state(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        rows = _emit_events(org_id, "XO0002")
        employee_id = uuid.UUID(rows[0]["payload"]["employee_id"])

        # Reverse chronological order, exactly the case the ADR names: the newest write arrives
        # first, and every older one that follows must be dropped by the version gate rather
        # than regress the row.
        for raw in reversed(rows):
            await _apply_hierarchy_event(_envelope_from_outbox_row(raw))

        async with session.begin():
            stored = await _stored_node(session, org_id, employee_id)
        assert stored == self._expected_final(rows)

    async def test_duplicated_delivery_converges_to_the_same_final_state(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        rows = _emit_events(org_id, "XO0003")
        employee_id = uuid.UUID(rows[0]["payload"]["employee_id"])

        # Every event delivered twice, interleaved with its neighbour — a realistic redelivery
        # pattern for an at-least-once broker under a consumer restart mid-batch.
        for raw in rows:
            envelope = _envelope_from_outbox_row(raw)
            await _apply_hierarchy_event(envelope)
            await _apply_hierarchy_event(envelope)

        async with session.begin():
            stored = await _stored_node(session, org_id, employee_id)
        assert stored == self._expected_final(rows)

    async def test_delivery_missing_one_event_still_converges_to_the_same_final_state(
        self, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        rows = _emit_events(org_id, "XO0004")
        employee_id = uuid.UUID(rows[0]["payload"]["employee_id"])

        # The third event (the second `updated`, version 2) never arrives — dropped by the
        # broker, or its queue was unbound for a moment. Full state on the next event must
        # still bring the row current.
        for raw in (rows[0], rows[1], rows[3]):
            await _apply_hierarchy_event(_envelope_from_outbox_row(raw))

        async with session.begin():
            stored = await _stored_node(session, org_id, employee_id)
        assert stored == self._expected_final(rows)


# =========================================================== live employee-service subprocess


class EmployeeServer:
    def __init__(self, base_url: str, org_a: uuid.UUID, org_b: uuid.UUID, seed: dict) -> None:
        self.base_url = base_url
        self.org_a = org_a
        self.org_b = org_b
        self.seed = seed  # {"org_a": [{"employee_id":..,"employee_no":..}, ...], "org_b": [...]}

    def employee_ids(self, key: str) -> list[uuid.UUID]:
        return [uuid.UUID(e["employee_id"]) for e in self.seed[key]]


@pytest.fixture(scope="module")
def employee_server(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """A real employee-service process, seeded with two organizations, listening on a real
    port — so approval's real `HttpHierarchySource` and `run_backfill` can be exercised against
    employee's real `/internal/v1/hierarchy` route rather than a stub.

    Module-scoped: booting `uvicorn` and seeding seven employees costs a few seconds, and
    nothing in this module's tests mutates the seeded rows except the two concurrency tests,
    which each work on a distinct employee id they create themselves via `_bump_employee`.
    """
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    port = _free_port()
    log_path = tmp_path_factory.mktemp("employee-cross-service") / f"{port}.log"
    handle = log_path.open("wb")
    proc = subprocess.Popen(  # noqa: S603  (fixed argv, sys.executable, no shell)
        [
            sys.executable,
            "-m",
            "tests.serve_hierarchy",
            "--org-a",
            str(org_a),
            "--org-b",
            str(org_b),
            "--count-a",
            "5",
            "--count-b",
            "2",
            "--port",
            str(port),
        ],
        cwd=str(EMPLOYEE_DIR),
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 60.0
    seed: dict | None = None
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"employee subprocess exited {proc.returncode} before becoming ready:\n"
                    f"{log_path.read_text(errors='replace')}"
                )
            try:
                response = httpx.get(f"{base_url}/health", timeout=1.0)
                if response.status_code == 200:
                    seed = json.loads(log_path.read_text().splitlines()[0])
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        if seed is None:
            raise RuntimeError(f"employee subprocess never became ready:\n{log_path.read_text()}")

        yield EmployeeServer(base_url, org_a, org_b, seed)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        handle.close()


def _source(base_url: str) -> HttpHierarchySource:
    return HttpHierarchySource(base_url, approval_settings())


# =========================================================== 3. the backfill page shape


class TestBackfillPageShapeMatchesEmployeesRealEndpoint:
    async def test_the_raw_response_carries_exactly_the_fields_employees_hierarchynodeout_defines(
        self, employee_server: EmployeeServer
    ) -> None:
        """Built from `services/employee/app/schemas.py::HierarchyNodeOut` — the response model
        the route actually declares — not from `app/employee_client.py`'s parser. If either
        side renames or adds a field, this fails independently of whether approval's own parser
        happens to tolerate it."""
        from platform_core.internal_auth import mint_service_token

        token = mint_service_token(approval_settings(), audience="employee", caller="approval")
        async with httpx.AsyncClient(base_url=employee_server.base_url) as client:
            response = await client.get(
                "/internal/v1/hierarchy",
                params={"organization_id": str(employee_server.org_a), "limit": 1},
                headers={"X-Internal-Token": token, "X-Calling-Service": "approval"},
            )
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {"items", "next_cursor"}
        item_keys = set(body["items"][0].keys())
        assert item_keys == {
            "employee_id",
            "reporting_manager_id",
            "dotted_line_manager_id",
            "status",
            "location_code",
            "display_name",
            "employee_no",
            "hierarchy_version",
        }

    async def test_approvals_http_client_parses_a_real_page_into_matching_items(
        self, employee_server: EmployeeServer
    ) -> None:
        source = _source(employee_server.base_url)
        try:
            # limit (10) strictly above the seeded count (5): a short page, not the exact-
            # boundary case, which gets its own test below.
            page = await source.fetch_page(employee_server.org_a, cursor=None, limit=10)
        finally:
            await source.aclose()

        assert {str(i.employee_id) for i in page.items} == {
            e["employee_id"] for e in employee_server.seed["org_a"]
        }
        assert all(i.status == "probation" for i in page.items)
        assert all(i.hierarchy_version == 0 for i in page.items)
        assert page.next_cursor is None

    async def test_a_page_landing_exactly_on_the_limit_still_carries_a_next_cursor(
        self, employee_server: EmployeeServer
    ) -> None:
        """ADR-0006 §2.1's exact-boundary case, proved against the real endpoint: five seeded
        employees, page size five — the page is full, so `next_cursor` must still be set, and
        only the *following*, empty page terminates the walk."""
        source = _source(employee_server.base_url)
        try:
            first = await source.fetch_page(employee_server.org_a, cursor=None, limit=5)
            assert len(first.items) == 5
            assert first.next_cursor is not None

            second = await source.fetch_page(
                employee_server.org_a, cursor=first.next_cursor, limit=5
            )
            assert second.items == []
            assert second.next_cursor is None
        finally:
            await source.aclose()

    async def test_resuming_by_cursor_continues_rather_than_restarts(
        self, employee_server: EmployeeServer
    ) -> None:
        source = _source(employee_server.base_url)
        try:
            first = await source.fetch_page(employee_server.org_a, cursor=None, limit=2)
            second = await source.fetch_page(
                employee_server.org_a, cursor=first.next_cursor, limit=2
            )
            third = await source.fetch_page(
                employee_server.org_a, cursor=second.next_cursor, limit=2
            )
        finally:
            await source.aclose()

        seen = [i.employee_id for i in (*first.items, *second.items, *third.items)]
        assert len(seen) == len(set(seen)) == 5, "resuming by cursor must not repeat or drop rows"
        assert third.next_cursor is None


# =========================================================== backfill end-to-end + cross-tenant


class TestBackfillEndToEndAgainstTheLiveEndpoint:
    async def test_the_real_backfill_populates_hierarchy_nodes_for_the_backfilled_org_only(
        self, session: AsyncSession, admin_session: AsyncSession, employee_server: EmployeeServer
    ) -> None:
        source = _source(employee_server.base_url)
        try:
            result = await run_backfill(employee_server.org_a, source, page_size=2, pace_seconds=0)
        finally:
            await source.aclose()

        assert result.items_applied == 5

        async with admin_session.begin():
            result = await admin_session.execute(
                text(
                    "SELECT employee_id FROM approval.hierarchy_nodes WHERE organization_id = :org"
                ),
                {"org": str(employee_server.org_a)},
            )
            rows = result.scalars().all()
        assert {str(r) for r in rows} == {e["employee_id"] for e in employee_server.seed["org_a"]}

    async def test_the_backfill_never_writes_the_other_organizations_employees(
        self, admin_session: AsyncSession, employee_server: EmployeeServer
    ) -> None:
        source = _source(employee_server.base_url)
        try:
            await run_backfill(employee_server.org_a, source, page_size=2, pace_seconds=0)
        finally:
            await source.aclose()

        org_b_ids = {e["employee_id"] for e in employee_server.seed["org_b"]}
        async with admin_session.begin():
            # Bypasses RLS entirely: proves org B's rows are absent from the table full stop,
            # not merely invisible under org A's binding.
            result = await admin_session.execute(
                text("SELECT employee_id FROM approval.hierarchy_nodes")
            )
            rows = result.scalars().all()
        stored_ids = {str(r) for r in rows}
        assert stored_ids.isdisjoint(org_b_ids)

    async def test_a_tenant_bound_session_cannot_see_the_other_organizations_backfilled_rows(
        self, session: AsyncSession, admin_session: AsyncSession, employee_server: EmployeeServer
    ) -> None:
        source_a = _source(employee_server.base_url)
        source_b = _source(employee_server.base_url)
        try:
            await run_backfill(employee_server.org_a, source_a, page_size=5, pace_seconds=0)
            await run_backfill(employee_server.org_b, source_b, page_size=5, pace_seconds=0)
        finally:
            await source_a.aclose()
            await source_b.aclose()

        # `session` is bound to org_id (a third, unrelated organization) — neither backfilled
        # org's rows may be visible through it.
        async with session.begin():
            result = await session.execute(text("SELECT employee_id FROM approval.hierarchy_nodes"))
            rows = result.scalars().all()
        assert rows == []

        async with admin_session.begin():
            a_count = (
                await admin_session.execute(
                    text(
                        "SELECT count(*) FROM approval.hierarchy_nodes WHERE organization_id = :o"
                    ),
                    {"o": str(employee_server.org_a)},
                )
            ).scalar_one()
            b_count = (
                await admin_session.execute(
                    text(
                        "SELECT count(*) FROM approval.hierarchy_nodes WHERE organization_id = :o"
                    ),
                    {"o": str(employee_server.org_b)},
                )
            ).scalar_one()
        assert a_count == 5
        assert b_count == 2


class TestConcurrentBackfillNeverRegressesANewerRow:
    """ADR-0006 §2's ordering rule, raced for real: two independent code paths — the live
    consumer and the one-time backfill's real HTTP client — writing the same row through
    `asyncio.gather` rather than one after the other, so the two coroutines' awaits (the
    backfill's real network round trip; the consumer's real DB round trip) actually interleave.
    """

    async def test_a_higher_live_version_beats_a_concurrent_stale_backfill_page(
        self, session: AsyncSession, admin_session: AsyncSession, employee_server: EmployeeServer
    ) -> None:
        from tests.helpers import employee_onboarded_envelope

        employee_id = employee_server.employee_ids("org_a")[0]
        # The live server's own state for this employee is fresh (version 0) — a page the
        # backfill fetches concurrently is therefore always stale next to this hand-built "the
        # live stream got here first" event, which is the property under test: it must win
        # regardless of which coroutine's I/O happens to finish first.
        live_envelope = employee_onboarded_envelope(
            organization_id=employee_server.org_a,
            employee_id=employee_id,
            display_name="Live Applied First",
            hierarchy_version=1000,
        )
        source = _source(employee_server.base_url)
        try:
            await asyncio.gather(
                _apply_hierarchy_event(live_envelope),
                run_backfill(employee_server.org_a, source, page_size=5, pace_seconds=0),
            )
        finally:
            await source.aclose()

        # `admin_session` bypasses RLS: `session` is bound to this test's own unrelated
        # `org_id`, not `employee_server.org_a`, so it is requested only for its autouse
        # truncate-before-test side effect, never for the read.
        async with admin_session.begin():
            stored = await _stored_node(admin_session, employee_server.org_a, employee_id)
        assert stored is not None
        assert stored["source_version"] == 1000
        assert stored["display_name"] == "Live Applied First"

    async def test_a_higher_backfill_snapshot_beats_a_concurrent_stale_live_event(
        self, session: AsyncSession, admin_session: AsyncSession, employee_server: EmployeeServer
    ) -> None:
        from tests.helpers import employee_onboarded_envelope

        employee_id = employee_server.employee_ids("org_a")[1]
        # Bump this employee for real on the live server first, so the backfill's page for it
        # carries a genuinely higher version (1) than the stale, hand-built "live" event (0)
        # racing it below.
        _bump_employee(employee_server.org_a, employee_id, "BLR")

        stale_live_envelope = employee_onboarded_envelope(
            organization_id=employee_server.org_a,
            employee_id=employee_id,
            display_name="Stale Live Event",
            hierarchy_version=0,
        )
        source = _source(employee_server.base_url)
        try:
            await asyncio.gather(
                run_backfill(employee_server.org_a, source, page_size=5, pace_seconds=0),
                _apply_hierarchy_event(stale_live_envelope),
            )
        finally:
            await source.aclose()

        async with admin_session.begin():
            stored = await _stored_node(admin_session, employee_server.org_a, employee_id)
        assert stored is not None
        assert stored["source_version"] == 1
        assert stored["display_name"] != "Stale Live Event"
