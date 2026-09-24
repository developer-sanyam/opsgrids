"""The TEAM backfill: paging employee's hierarchy export into `team_membership`, and the HTTP
client it reads through.

`run_backfill` is exercised against a stubbed `TeamSource` (no real employee-service) but a **real**
PostgreSQL through the same upsert the consumer uses, so what is proved about pagination, the
version gate and resume-by-cursor is proved against the real database and only the network hop is
faked. The client is exercised through `httpx.MockTransport`, so each test controls exactly which
attempt succeeds and asserts how many were made.

The broker is never contacted. What `ensure_queue_bound` must guarantee — that it declares
`attendance.events` *exactly* as `EventConsumer.run()` does, so neither side answers the other with
406 PRECONDITION_FAILED, and that every routing key the worker binds is bound before the first HTTP
call — is a property of the arguments it passes, so a recording fake captures both code paths and
the test compares them. Publishing a real message would also have put a fake employee event on the
shared exchange, where every other service's queue bound to that key would receive it.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import backfill as backfill_module
from app import selectors
from app.backfill import BackfillInterrupted, ensure_queue_bound, run_backfill
from app.config import settings
from app.consumers import BOUND_EVENTS, QUEUE_NAME, consumer, on_employee_updated
from app.employee_client import (
    EMPLOYEE_AUDIENCE,
    MAX_ATTEMPTS,
    HttpTeamSource,
    RetryExhaustedError,
    TeamItem,
    TeamPage,
)
from app.models import TeamMembership
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine
from tests.helpers import ctx_for, employee_event_envelope

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _engine() -> Any:
    """`run_backfill` writes through `session_scope`, which needs the process engine initialised —
    the app lifespan does this in production."""
    init_engine(settings())
    yield
    await dispose_engine()


@dataclass
class FakeTeamSource:
    """An in-memory, keyset-paginated stand-in for employee's internal export.

    `pages` is the full ordered sequence a real cursor walk would produce; `fail_before_page` raises
    before serving that 1-based page, standing in for the run dying partway — the property under
    test is that every page served *before* the failure is already committed.
    """

    pages: list[list[TeamItem]]
    fail_before_page: int | None = None
    calls: list[uuid.UUID | None] = field(default_factory=list)
    cursors: list[uuid.UUID] = field(default_factory=list)
    on_fetch: Any = None

    async def fetch_page(
        self, organization_id: uuid.UUID, *, cursor: uuid.UUID | None, limit: int = 500
    ) -> TeamPage:
        if self.on_fetch is not None:
            self.on_fetch()
        self.calls.append(cursor)
        number = len(self.calls)
        if self.fail_before_page == number:
            raise RuntimeError("boom")
        items = self.pages[number - 1]
        next_cursor = None
        if number < len(self.pages):
            next_cursor = uuid.uuid4()
            self.cursors.append(next_cursor)
        return TeamPage(items=items, next_cursor=next_cursor)


def _item(
    employee_id: uuid.UUID,
    *,
    manager: uuid.UUID | None = None,
    version: int | None = 1,
    status: str = "active",
) -> TeamItem:
    return TeamItem(
        employee_id=employee_id,
        reporting_manager_id=manager,
        status=status,
        hierarchy_version=version,
    )


async def _rows(session: AsyncSession, org_id: uuid.UUID) -> dict[uuid.UUID, tuple[Any, str, int]]:
    async with session.begin():
        found = (
            await session.execute(
                select(
                    TeamMembership.employee_id,
                    TeamMembership.manager_employee_id,
                    TeamMembership.status,
                    TeamMembership.source_version,
                ).where(TeamMembership.organization_id == org_id)
            )
        ).all()
    return {r[0]: (r[1], r[2], r[3]) for r in found}


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 100 ms between pages is a load-shaping choice, not something a test should wait on."""
    monkeypatch.setattr(backfill_module, "PAGE_PACING_SECONDS", 0)


# --------------------------------------------------------------------- the run


class TestRunBackfill:
    async def test_it_applies_every_page_and_counts_them(self, session, org_id) -> None:
        mgr = uuid.uuid4()
        a, b, c, d = (uuid.uuid4() for _ in range(4))
        source = FakeTeamSource(
            pages=[
                [_item(mgr), _item(a, manager=mgr)],
                [_item(b, manager=mgr), _item(c, manager=mgr, status="exited", version=3)],
                [_item(d, manager=a)],
            ]
        )

        result = await run_backfill(org_id, source, pace_seconds=0)

        assert (result.pages, result.items_seen, result.items_applied) == (3, 5, 5)
        assert await _rows(session, org_id) == {
            mgr: (None, "active", 1),
            a: (mgr, "active", 1),
            b: (mgr, "active", 1),
            c: (mgr, "exited", 3),
            d: (a, "active", 1),
        }
        # The cursor handed to each page is the one the previous page returned.
        assert source.calls == [None, *source.cursors]

    async def test_a_second_full_pass_applies_nothing(self, session, org_id) -> None:
        """Re-running is the resume mechanism, so it must be a no-op when nothing has changed."""
        mgr, a = uuid.uuid4(), uuid.uuid4()
        pages = [[_item(mgr), _item(a, manager=mgr)]]
        await run_backfill(org_id, FakeTeamSource(pages=pages), pace_seconds=0)

        again = await run_backfill(org_id, FakeTeamSource(pages=pages), pace_seconds=0)

        assert (again.items_seen, again.items_applied) == (2, 0)

    async def test_it_does_not_overwrite_a_newer_state_the_live_stream_applied(
        self, session, org_id
    ) -> None:
        """A backfill racing the live stream: the export was read at version 3, but by the time the
        page is applied the consumer has already written version 5. The gate keeps version 5."""
        me, old_mgr, new_mgr = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await on_employee_updated(
            employee_event_envelope(
                organization_id=org_id, employee_id=me, manager_id=new_mgr, version=5
            )
        )

        result = await run_backfill(
            org_id,
            FakeTeamSource(pages=[[_item(me, manager=old_mgr, version=3)]]),
            pace_seconds=0,
        )

        assert result.items_applied == 0
        assert (await _rows(session, org_id))[me] == (new_mgr, "active", 5)

    async def test_it_does_overwrite_an_older_state(self, session, org_id) -> None:
        me, old_mgr, new_mgr = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await on_employee_updated(
            employee_event_envelope(
                organization_id=org_id, employee_id=me, manager_id=old_mgr, version=1
            )
        )
        await run_backfill(
            org_id,
            FakeTeamSource(pages=[[_item(me, manager=new_mgr, version=4)]]),
            pace_seconds=0,
        )
        assert (await _rows(session, org_id))[me] == (new_mgr, "active", 4)

    async def test_a_row_without_a_version_is_dropped_never_read_as_zero(
        self, session, org_id
    ) -> None:
        """An un-upgraded employee pod: ADR-0006 §2.1 says ignore it. Read as version 0 it would
        also *overwrite nothing and insert a stale manager* for a person with no row yet."""
        me = uuid.uuid4()
        result = await run_backfill(
            org_id,
            FakeTeamSource(pages=[[_item(me, manager=uuid.uuid4(), version=None)]]),
            pace_seconds=0,
        )
        assert result.items_applied == 0
        assert await _rows(session, org_id) == {}

    async def test_a_self_manager_row_is_stored_as_no_manager(self, session, org_id) -> None:
        me = uuid.uuid4()
        await run_backfill(org_id, FakeTeamSource(pages=[[_item(me, manager=me)]]), pace_seconds=0)
        assert (await _rows(session, org_id))[me] == (None, "active", 1)

    async def test_an_empty_export_is_a_clean_no_op(self, session, org_id) -> None:
        result = await run_backfill(org_id, FakeTeamSource(pages=[[]]), pace_seconds=0)
        assert (result.pages, result.items_seen, result.items_applied) == (1, 0, 0)

    async def test_a_backfilled_team_is_what_a_managers_read_returns(self, session, org_id) -> None:
        """End to end through the read path: nothing but the backfill filled the projection."""
        from app import services
        from app.schemas import CheckInIn

        mgr, a, stranger = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await run_backfill(
            org_id,
            FakeTeamSource(pages=[[_item(mgr), _item(a, manager=mgr), _item(stranger)]]),
            pace_seconds=0,
        )
        for employee in (a, stranger):
            async with session.begin():
                await services.check_in(
                    session,
                    ctx_for(org_id, employee, "hrms.time.attendance.write", Scope.OWN),
                    CheckInIn(work_mode="office", source="web"),
                )

        team_ctx = ctx_for(org_id, mgr, "hrms.time.attendance.read", Scope.TEAM)
        async with session.begin():
            seen = {
                r.employee_id
                for r in (await session.execute(selectors.attendance_stmt(team_ctx)))
                .scalars()
                .all()
            }
        assert seen == {a}


class TestBackfillIsTenantBound:
    async def test_it_writes_only_the_named_organization(
        self, session, admin_session, org_id
    ) -> None:
        other_org = uuid.uuid4()
        me = uuid.uuid4()
        await run_backfill(org_id, FakeTeamSource(pages=[[_item(me)]]), pace_seconds=0)

        async with admin_session.begin():
            elsewhere = (
                await admin_session.execute(
                    select(func.count())
                    .select_from(TeamMembership)
                    .where(TeamMembership.organization_id == other_org)
                )
            ).scalar_one()
        assert elsewhere == 0
        assert set(await _rows(session, org_id)) == {me}

    async def test_the_same_employee_id_in_two_tenants_keeps_two_rows(
        self, session, admin_session, org_id
    ) -> None:
        """The primary key leads with the tenant: another organization reusing an employee id
        neither collides with nor overwrites this one's row."""
        other_org = uuid.uuid4()
        me, mgr_here, mgr_there = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await run_backfill(
            org_id, FakeTeamSource(pages=[[_item(me, manager=mgr_here)]]), pace_seconds=0
        )
        await run_backfill(
            other_org,
            FakeTeamSource(pages=[[_item(me, manager=mgr_there, version=9)]]),
            pace_seconds=0,
        )

        assert (await _rows(session, org_id))[me] == (mgr_here, "active", 1)
        async with admin_session.begin():
            theirs = (
                await admin_session.execute(
                    select(TeamMembership.manager_employee_id, TeamMembership.source_version).where(
                        TeamMembership.organization_id == other_org,
                        TeamMembership.employee_id == me,
                    )
                )
            ).one()
        assert tuple(theirs) == (mgr_there, 9)


class TestFailureAndResume:
    async def test_a_failure_mid_run_carries_the_cursor_of_the_last_committed_page(
        self, session, org_id
    ) -> None:
        first, second = uuid.uuid4(), uuid.uuid4()
        source = FakeTeamSource(pages=[[_item(first)], [_item(second)]], fail_before_page=2)

        with pytest.raises(BackfillInterrupted) as excinfo:
            await run_backfill(org_id, source, pace_seconds=0)

        # Page 1 is committed, and the resume point is exactly the cursor page 2 was asked with.
        assert set(await _rows(session, org_id)) == {first}
        assert excinfo.value.last_cursor == source.cursors[0]

        resumed = await run_backfill(
            org_id,
            FakeTeamSource(pages=[[_item(second)]]),
            start_cursor=excinfo.value.last_cursor,
            pace_seconds=0,
        )
        assert resumed.items_applied == 1
        assert set(await _rows(session, org_id)) == {first, second}

    async def test_a_bad_row_rolls_back_its_whole_page_and_reports_the_resume_point(
        self, session, org_id
    ) -> None:
        good, bad = uuid.uuid4(), uuid.uuid4()
        source = FakeTeamSource(pages=[[_item(good), _item(bad, status="teleported")]])

        with pytest.raises(BackfillInterrupted):
            await run_backfill(org_id, source, pace_seconds=0)

        assert await _rows(session, org_id) == {}

    async def test_nothing_is_fetched_while_a_transaction_is_open(
        self, session, org_id, monkeypatch
    ) -> None:
        """A slow employee-service must never hold a pooled connection or a tenant-bound
        transaction (ADR-0008 §8's rule, and D3's). The fake asserts, at the moment each page is
        requested, that no `session_scope` is open."""
        open_scopes = 0
        real_scope = backfill_module.session_scope

        @contextlib.asynccontextmanager
        async def counting_scope(**kwargs: Any) -> AsyncIterator[Any]:
            nonlocal open_scopes
            open_scopes += 1
            try:
                async with real_scope(**kwargs) as s:
                    yield s
            finally:
                open_scopes -= 1

        monkeypatch.setattr(backfill_module, "session_scope", counting_scope)
        seen_open: list[int] = []
        source = FakeTeamSource(
            pages=[[_item(uuid.uuid4())], [_item(uuid.uuid4())], [_item(uuid.uuid4())]],
            on_fetch=lambda: seen_open.append(open_scopes),
        )

        await run_backfill(org_id, source, pace_seconds=0)

        assert seen_open == [0, 0, 0]
        assert len(await _rows(session, org_id)) == 3


class TestCommandLine:
    async def test_a_cursor_needs_exactly_one_organization(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            await backfill_module.main(
                [
                    "--org",
                    str(uuid.uuid4()),
                    "--org",
                    str(uuid.uuid4()),
                    "--cursor",
                    str(uuid.uuid4()),
                ]
            )
        assert excinfo.value.code == 2

    async def test_an_organization_is_required(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            await backfill_module.main([])
        assert excinfo.value.code == 2

    async def test_a_malformed_organization_id_is_refused_at_parse_time(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            await backfill_module.main(["--org", "not-a-uuid"])
        assert excinfo.value.code == 2


# --------------------------------------------------------------------- the broker topology


@dataclass
class _Recorder:
    """What one code path told the (fake) broker."""

    exchanges: list[tuple[str, Any, bool]] = field(default_factory=list)
    queues: list[tuple[str, bool, Any]] = field(default_factory=list)
    bindings: list[tuple[str, str]] = field(default_factory=list)


class _FakeQueue:
    def __init__(self, name: str, rec: _Recorder) -> None:
        self.name = name
        self._rec = rec

    async def bind(self, exchange: Any, routing_key: str | None = None) -> None:
        self._rec.bindings.append((self.name, str(routing_key)))

    def iterator(self) -> Any:
        class _Empty:
            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            def __aiter__(self) -> Any:
                return self

            async def __anext__(self) -> Any:
                raise StopAsyncIteration

        return _Empty()


class _FakeChannel:
    def __init__(self, rec: _Recorder) -> None:
        self._rec = rec

    async def set_qos(self, **_: Any) -> None:
        return None

    async def declare_exchange(self, name: str, kind: Any, durable: bool = False) -> Any:
        self._rec.exchanges.append((name, kind, durable))
        return type("Exchange", (), {"name": name})()

    async def declare_queue(
        self, name: str, durable: bool = False, arguments: Any = None
    ) -> _FakeQueue:
        self._rec.queues.append((name, durable, arguments))
        return _FakeQueue(name, self._rec)


class _FakeConnection:
    def __init__(self, rec: _Recorder) -> None:
        self._rec = rec

    async def channel(self, **_: Any) -> _FakeChannel:
        return _FakeChannel(self._rec)

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> _FakeConnection:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()

    async def _connect(*_: Any, **__: Any) -> _FakeConnection:
        return _FakeConnection(rec)

    monkeypatch.setattr("aio_pika.connect_robust", _connect)
    return rec


class TestQueueBoundBeforeBackfill:
    async def test_it_declares_the_queue_exactly_as_the_workers_own_consumer_does(
        self, broker: _Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A declare that differs in one argument makes RabbitMQ answer 406 on whichever side
        declares second, and a 406 on the worker's startup means the worker cannot start."""
        await ensure_queue_bound("amqp://unused")
        from_backfill = (list(broker.exchanges), list(broker.queues))

        broker.exchanges.clear()
        broker.queues.clear()
        broker.bindings.clear()
        await consumer.run()
        from_worker = (list(broker.exchanges), list(broker.queues))

        assert from_backfill == from_worker
        # ... and the queue is the one the worker consumes, with its dead-letter queue beside it.
        assert {name for name, _, _ in broker.queues} == {QUEUE_NAME, f"{QUEUE_NAME}.dlq"}

    async def test_it_binds_every_routing_key_the_worker_binds(self, broker: _Recorder) -> None:
        """A topic exchange drops what nothing is bound for, so a hierarchy change published while
        the queue did not exist is gone. Every key the worker will bind is bound first."""
        await ensure_queue_bound("amqp://unused")
        from_backfill = {key for name, key in broker.bindings if name == QUEUE_NAME}

        broker.bindings.clear()
        await consumer.run()
        from_worker = {key for name, key in broker.bindings if name == QUEUE_NAME}

        assert from_backfill == from_worker == set(BOUND_EVENTS)

    async def test_the_employee_lifecycle_events_are_among_the_bound_keys(self) -> None:
        assert {
            "hrms.people.employee.onboarded",
            "hrms.people.employee.updated",
            "hrms.people.employee.transitioned",
        } <= set(BOUND_EVENTS)

    async def test_the_queue_is_bound_before_the_first_page_is_fetched(
        self, monkeypatch: pytest.MonkeyPatch, org_id
    ) -> None:
        order: list[str] = []

        async def _bound(_url: str) -> None:
            order.append("bound")

        class _Source:
            def __init__(self, *_: Any) -> None:
                pass

            async def fetch_page(self, *_: Any, **__: Any) -> TeamPage:
                order.append("fetched")
                return TeamPage(items=[], next_cursor=None)

            async def aclose(self) -> None:
                order.append("closed")

        monkeypatch.setattr(backfill_module, "ensure_queue_bound", _bound)
        monkeypatch.setattr(backfill_module, "HttpTeamSource", _Source)

        async def _app_role(*_: Any, **__: Any) -> None:
            return None

        monkeypatch.setattr(backfill_module, "assert_app_role", _app_role)

        assert await backfill_module.main(["--org", str(org_id)]) == 0
        assert order == ["bound", "fetched", "closed"]


# --------------------------------------------------------------------- the HTTP client


ORG_ID = uuid.uuid4()
EMPLOYEE_ID = uuid.uuid4()


def _page_body(next_cursor: uuid.UUID | None = None) -> dict[str, Any]:
    return {
        "items": [
            {
                "employee_id": str(EMPLOYEE_ID),
                "reporting_manager_id": None,
                "dotted_line_manager_id": None,
                "status": "active",
                "display_name": "Ananya Rao",
                "employee_no": "EMP0001",
                "location_code": "MUM",
                "hierarchy_version": 1,
            }
        ],
        "next_cursor": str(next_cursor) if next_cursor else None,
    }


@pytest.fixture
def instant_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The backoff tops out at 16 s; tests assert *how many times* it slept, not how long."""
    slept: list[float] = []

    async def _sleep(seconds: float, *_: object) -> None:
        slept.append(seconds)

    monkeypatch.setattr("app.employee_client.asyncio.sleep", _sleep)
    return slept


def _source(handler: Any) -> HttpTeamSource:
    source = HttpTeamSource("http://employee.test", settings())
    source._client = httpx.AsyncClient(
        base_url="http://employee.test", transport=httpx.MockTransport(handler)
    )
    return source


class TestTheHttpClient:
    async def test_no_retry_when_the_first_response_is_ok(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=_page_body())

        page = await _source(handler).fetch_page(ORG_ID, cursor=None)

        assert calls == 1
        assert page.items == [
            TeamItem(
                employee_id=EMPLOYEE_ID,
                reporting_manager_id=None,
                status="active",
                hierarchy_version=1,
            )
        ]
        assert page.next_cursor is None

    async def test_it_parses_the_minimal_shape_employee_now_sends_it(self) -> None:
        """Employee serves attendance the reporting line only — four fields, no names. The parser
        must need nothing more than that."""
        mgr = uuid.uuid4()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "employee_id": str(EMPLOYEE_ID),
                            "reporting_manager_id": str(mgr),
                            "status": "on_notice",
                            "hierarchy_version": 7,
                        }
                    ],
                    "next_cursor": None,
                },
            )

        page = await _source(handler).fetch_page(ORG_ID, cursor=None)

        assert page.items == [
            TeamItem(
                employee_id=EMPLOYEE_ID,
                reporting_manager_id=mgr,
                status="on_notice",
                hierarchy_version=7,
            )
        ]

    async def test_a_name_never_leaves_the_response_object(self) -> None:
        """The export carries names and employee numbers for approval's sake. This client parses
        four fields; nothing here can put a name in a table or a log line by way of a helper that
        forgot to skip it."""
        assert set(TeamItem.__dataclass_fields__) == {
            "employee_id",
            "reporting_manager_id",
            "status",
            "hierarchy_version",
        }

    async def test_a_5xx_is_retried_and_then_succeeds(self, instant_sleep) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls < 3:
                return httpx.Response(503, text="upstream unhappy")
            return httpx.Response(200, json=_page_body())

        page = await _source(handler).fetch_page(ORG_ID, cursor=None)

        assert calls == 3
        assert len(page.items) == 1
        assert len(instant_sleep) == 2

    async def test_a_timeout_is_retried(self, instant_sleep) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadTimeout("slow", request=request)
            return httpx.Response(200, json=_page_body())

        await _source(handler).fetch_page(ORG_ID, cursor=None)
        assert calls == 2

    async def test_every_attempt_failing_raises_retry_exhausted_after_max_attempts(
        self, instant_sleep
    ) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500)

        with pytest.raises(RetryExhaustedError):
            await _source(handler).fetch_page(ORG_ID, cursor=None)

        assert calls == MAX_ATTEMPTS
        # No point sleeping after the last attempt.
        assert len(instant_sleep) == MAX_ATTEMPTS - 1

    async def test_a_4xx_is_never_retried(self, instant_sleep) -> None:
        """A 401 means the audience, secret or issuer is wrong; retrying repeats the mistake."""
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(401, json={"detail": "Valid service token required"})

        with pytest.raises(httpx.HTTPStatusError):
            await _source(handler).fetch_page(ORG_ID, cursor=None)

        assert calls == 1
        assert instant_sleep == []

    async def test_the_token_names_this_service_the_audience_and_binds_the_organization(
        self,
    ) -> None:
        captured: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.headers["X-Internal-Token"])
            return httpx.Response(200, json=_page_body())

        await _source(handler).fetch_page(ORG_ID, cursor=None)

        claims = jwt.decode(
            captured[0],
            settings().internal_service_token.get_secret_value(),
            algorithms=["HS256"],
            audience=EMPLOYEE_AUDIENCE,
        )
        assert claims["iss"] == "attendance"
        assert claims["org"] == str(ORG_ID)

    async def test_a_fresh_token_is_minted_for_every_attempt(self, instant_sleep) -> None:
        """The 60 s token can expire inside one page's retries, so it is per request."""
        tokens: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            tokens.append(request.headers["X-Internal-Token"])
            return (
                httpx.Response(503) if len(tokens) < 3 else httpx.Response(200, json=_page_body())
            )

        await _source(handler).fetch_page(ORG_ID, cursor=None)

        assert len(set(tokens)) == 3

    async def test_the_cursor_and_limit_are_sent_as_query_parameters(self) -> None:
        seen: list[httpx.URL] = []
        cursor = uuid.uuid4()

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url)
            return httpx.Response(200, json=_page_body(next_cursor=uuid.uuid4()))

        page = await _source(handler).fetch_page(ORG_ID, cursor=cursor, limit=50)

        assert seen[0].path == "/internal/v1/hierarchy"
        assert dict(seen[0].params) == {
            "organization_id": str(ORG_ID),
            "limit": "50",
            "cursor": str(cursor),
        }
        assert page.next_cursor is not None

    async def test_a_malformed_row_is_refused_at_the_boundary(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = _page_body()
            body["items"][0]["employee_id"] = "not-a-uuid"
            return httpx.Response(200, json=body)

        with pytest.raises(ValueError):
            await _source(handler).fetch_page(ORG_ID, cursor=None)
