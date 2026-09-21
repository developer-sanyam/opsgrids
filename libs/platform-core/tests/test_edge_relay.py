"""The edge relay: one process, many schemas' outboxes (ADR-0012, the one-box deployment).

`EDGE_RELAY_SCHEMAS` parsing is tested against the same shapes an operator will actually
type — extra whitespace, a trailing comma, an explicit producer override — and against the
two ways a value can be wrong (a schema named twice, too many `:`). The generated
per-schema model is checked for exact column parity with `OutboxMixin`, because a future
column added to the mixin and not carried here would silently narrow every envelope this
process publishes. The supervisor is exercised with fake relays so schema isolation — one
schema's failure must not touch another's task — and the missing-table slow path are both
pinned without a broker or a real outbox table.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any, cast

import pytest
import structlog
from pydantic_settings import SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from platform_core.edge_relay import (
    EdgeRelaySettings,
    SchemaSpec,
    _graceful_stop,
    _is_missing_table_error,
    _make_outbox_model,
    _supervise_schema,
    parse_edge_relay_schemas,
    run_forever,
)
from platform_core.events import OutboxMixin


class _HermeticSettings(EdgeRelaySettings):
    """No repo `.env`, no ambient environment — only what the test passes in explicitly."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")


class TestParseEdgeRelaySchemas:
    def test_bare_schema_defaults_its_producer_to_itself(self) -> None:
        assert parse_edge_relay_schemas("account") == [SchemaSpec("account", "account")]

    def test_schema_colon_producer_overrides_the_producer(self) -> None:
        assert parse_edge_relay_schemas("billing:Billing-Service") == [
            SchemaSpec("billing", "Billing-Service")
        ]

    def test_whitespace_around_entries_and_the_colon_is_trimmed(self) -> None:
        assert parse_edge_relay_schemas(" account , billing : Billing-Service ") == [
            SchemaSpec("account", "account"),
            SchemaSpec("billing", "Billing-Service"),
        ]

    def test_empty_string_parses_to_no_schemas(self) -> None:
        assert parse_edge_relay_schemas("") == []

    def test_blank_entries_from_stray_commas_are_dropped(self) -> None:
        assert parse_edge_relay_schemas("account,,billing,") == [
            SchemaSpec("account", "account"),
            SchemaSpec("billing", "billing"),
        ]

    def test_a_schema_named_twice_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="more than once"):
            parse_edge_relay_schemas("account,billing,account")

    def test_a_schema_and_an_override_of_the_same_schema_is_still_a_duplicate(self) -> None:
        with pytest.raises(ValueError, match="more than once"):
            parse_edge_relay_schemas("account,account:Other-Name")

    def test_more_than_one_colon_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="more than one ':'"):
            parse_edge_relay_schemas("account:foo:bar")

    def test_an_empty_schema_name_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="names no schema"):
            parse_edge_relay_schemas(":producer")

    def test_an_empty_producer_override_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty producer"):
            parse_edge_relay_schemas("account:")


class TestEdgeRelaySettings:
    def test_edge_relay_schemas_is_parsed_from_the_env_shaped_string(self) -> None:
        settings = _HermeticSettings(edge_relay_schemas="account,billing:Billing-Service")
        assert settings.edge_relay_schemas == [
            SchemaSpec("account", "account"),
            SchemaSpec("billing", "Billing-Service"),
        ]

    def test_defaults_to_no_schemas(self) -> None:
        assert _HermeticSettings().edge_relay_schemas == []

    def test_a_duplicate_schema_fails_construction(self) -> None:
        with pytest.raises(ValueError, match="more than once"):
            _HermeticSettings(edge_relay_schemas="account,account")

    def test_the_outbox_role_is_pinned_by_default(self) -> None:
        """Unlike the base `ServiceSettings`, where `db_outbox_role` defaults to `None` —
        every service pins it in its own `config.py`; the edge relay pins it the same way
        so it binds correctly without extra configuration in every environment that runs
        it."""
        assert _HermeticSettings().db_outbox_role == "opsgrids_outbox"

    def test_owns_no_schema_of_its_own(self) -> None:
        assert _HermeticSettings().db_schema == "public"


class TestMakeOutboxModel:
    def _mixin_columns(self) -> dict[str, tuple[str, bool]]:
        """The columns `OutboxMixin` actually declares, built the same way any service
        builds them — mixed onto a declarative base — so this is a real comparison and
        not a tautology against `_make_outbox_model`'s own implementation."""

        class _ReferenceBase(DeclarativeBase):
            pass

        class _ReferenceOutboxEvent(_ReferenceBase, OutboxMixin):
            pass

        return {
            c.name: (type(c.type).__name__, c.nullable)
            for c in _ReferenceOutboxEvent.__table__.columns
        }

    def test_generated_model_has_exactly_the_mixins_columns(self) -> None:
        cls = _make_outbox_model("account")
        generated = {c.name: (type(c.type).__name__, c.nullable) for c in cls.__table__.columns}
        assert generated == self._mixin_columns()

    def test_generated_model_is_bound_to_the_requested_schema(self) -> None:
        cls = _make_outbox_model("activity")
        assert cls.__table__.schema == "activity"
        assert cls.__table__.name == "outbox"

    def test_two_schemas_produce_independent_classes_and_tables(self) -> None:
        account_cls = _make_outbox_model("account")
        billing_cls = _make_outbox_model("billing")
        assert account_cls is not billing_cls
        assert account_cls.__table__ is not billing_cls.__table__
        assert account_cls.__table__.schema == "account"
        assert billing_cls.__table__.schema == "billing"

    def test_calling_it_twice_for_the_same_schema_does_not_collide(self) -> None:
        """Two independent declarative registries, not a shared one — a second relay
        restart in the same process must not raise "table already defined"."""
        first = _make_outbox_model("account")
        second = _make_outbox_model("account")
        assert first is not second


class TestIsMissingTableError:
    def test_a_bare_undefined_table_error_is_detected(self) -> None:
        class UndefinedTableError(Exception):
            pass

        assert _is_missing_table_error(UndefinedTableError("relation does not exist"))

    def test_wrapped_in_orig_is_still_detected(self) -> None:
        class UndefinedTableError(Exception):
            pass

        class ProgrammingError(Exception):
            def __init__(self, orig: BaseException) -> None:
                super().__init__(str(orig))
                self.orig = orig

        wrapped = ProgrammingError(UndefinedTableError("relation does not exist"))
        assert _is_missing_table_error(wrapped)

    def test_chained_via_cause_is_still_detected(self) -> None:
        class UndefinedTableError(Exception):
            pass

        try:
            try:
                raise UndefinedTableError("relation does not exist")
            except UndefinedTableError as inner:
                raise RuntimeError("wrapped") from inner
        except RuntimeError as outer:
            assert _is_missing_table_error(outer)

    def test_an_unrelated_error_is_not_detected(self) -> None:
        assert not _is_missing_table_error(ConnectionError("connection refused"))

    def test_a_self_referential_cause_does_not_infinite_loop(self) -> None:
        exc = RuntimeError("boom")
        exc.__cause__ = exc
        assert not _is_missing_table_error(exc)


class _FakeRelay:
    """Stands in for `OutboxRelay`: scripted to raise on `run()`, or return once `stop()`
    is called, and records whether it was asked to stop."""

    def __init__(self, *, outcomes: list[BaseException | None]) -> None:
        self._outcomes = outcomes
        self.stopped = False
        self.run_calls = 0

    async def run(self) -> None:
        self.run_calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if outcome is not None:
            raise outcome
        # No outcome scripted: behave like a relay that was told to stop.
        return None

    def stop(self) -> None:
        self.stopped = True


#: `_supervise_schema` only ever passes this through to `OutboxRelay`, which every test in
#: this class fakes out entirely — so the real thing is never dereferenced, and a `cast`
#: stub is honest about that rather than smuggling an untyped `object()` past strict mypy.
_UNUSED_SESSION_FACTORY = cast("async_sessionmaker[AsyncSession]", object())


class TestSupervisSchema:
    """`_supervise_schema` in isolation, `OutboxRelay` itself replaced by `_FakeRelay`."""

    async def test_a_clean_run_returns_without_looping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeRelay(outcomes=[])
        monkeypatch.setattr("platform_core.edge_relay.OutboxRelay", lambda **kwargs: fake)
        settings = _HermeticSettings(edge_relay_schemas="account")
        active: dict[str, Any] = {"account": None}

        await _supervise_schema(
            SchemaSpec("account", "account"), settings, _UNUSED_SESSION_FACTORY, active
        )

        assert fake.run_calls == 1
        assert active["account"] is None

    async def test_an_ordinary_failure_is_restarted_with_backoff(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        relays = [
            _FakeRelay(outcomes=[RuntimeError("db hiccup")]),
            _FakeRelay(outcomes=[]),
        ]
        built = iter(relays)
        monkeypatch.setattr("platform_core.edge_relay.OutboxRelay", lambda **kwargs: next(built))
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        settings = _HermeticSettings(
            edge_relay_schemas="account", edge_relay_restart_backoff_initial_seconds=1.0
        )
        active: dict[str, Any] = {"account": None}

        with structlog.testing.capture_logs() as logs:
            await _supervise_schema(
                SchemaSpec("account", "account"), settings, _UNUSED_SESSION_FACTORY, active
            )

        assert [r.run_calls for r in relays] == [1, 1]
        assert sleeps == [1.0]
        assert any(entry["event"] == "edge_relay.schema.failed" for entry in logs)

    async def test_a_missing_table_is_retried_slowly_and_warned_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class UndefinedTableError(Exception):
            pass

        relays = [
            _FakeRelay(outcomes=[UndefinedTableError("relation does not exist")]),
            _FakeRelay(outcomes=[UndefinedTableError("relation does not exist")]),
            _FakeRelay(outcomes=[]),
        ]
        built = iter(relays)
        monkeypatch.setattr("platform_core.edge_relay.OutboxRelay", lambda **kwargs: next(built))
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        settings = _HermeticSettings(
            edge_relay_schemas="account", edge_relay_missing_table_retry_seconds=60.0
        )
        active: dict[str, Any] = {"account": None}

        with structlog.testing.capture_logs() as logs:
            await _supervise_schema(
                SchemaSpec("account", "account"), settings, _UNUSED_SESSION_FACTORY, active
            )

        # Fixed 60s each time — no exponential growth for the specific "not migrated yet"
        # case, unlike the ordinary-failure path above.
        assert sleeps == [60.0, 60.0]
        missing_table_logs = [
            entry for entry in logs if entry["event"] == "edge_relay.schema.missing_table"
        ]
        assert len(missing_table_logs) == 1

    async def test_one_schemas_failure_does_not_stop_another_schemas_task(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two real `asyncio.create_task`s: `account` fails once then recovers, `billing`
        never fails. Both must reach a clean stop; `account` failing must not cancel or
        otherwise disturb `billing`'s task."""
        failing = [
            _FakeRelay(outcomes=[RuntimeError("boom")]),
            _FakeRelay(outcomes=[]),
        ]
        healthy = [_FakeRelay(outcomes=[])]
        built: dict[str, Iterator[_FakeRelay]] = {
            "account": iter(failing),
            "billing": iter(healthy),
        }

        def _build(**kwargs: Any) -> _FakeRelay:
            return next(built[kwargs["producer"]])

        monkeypatch.setattr("platform_core.edge_relay.OutboxRelay", _build)
        real_sleep = asyncio.sleep
        monkeypatch.setattr(asyncio, "sleep", lambda seconds: real_sleep(0))
        settings = _HermeticSettings(edge_relay_schemas="account,billing")
        active: dict[str, Any] = {"account": None, "billing": None}

        await asyncio.gather(
            _supervise_schema(
                SchemaSpec("account", "account"), settings, _UNUSED_SESSION_FACTORY, active
            ),
            _supervise_schema(
                SchemaSpec("billing", "billing"), settings, _UNUSED_SESSION_FACTORY, active
            ),
        )

        assert [r.run_calls for r in failing] == [1, 1]
        assert [r.run_calls for r in healthy] == [1]


class TestGracefulStop:
    async def test_stop_is_called_on_every_active_relay(self) -> None:
        account_relay = _FakeRelay(outcomes=[])
        billing_relay = _FakeRelay(outcomes=[])
        active: dict[str, _FakeRelay | None] = {
            "account": account_relay,
            "billing": billing_relay,
            "idle": None,  # a schema currently between retries has nothing to stop
        }

        async def _noop() -> None:
            return None

        tasks = {name: asyncio.create_task(_noop()) for name in active}
        await asyncio.sleep(0)  # let the no-op tasks finish

        await _graceful_stop(tasks, active, grace_seconds=1.0)

        assert account_relay.stopped
        assert billing_relay.stopped

    async def test_a_task_still_running_past_the_grace_period_is_cancelled(self) -> None:
        async def _never_finishes() -> None:
            await asyncio.sleep(3600)

        tasks = {"stuck": asyncio.create_task(_never_finishes())}
        active: dict[str, Any] = {"stuck": None}

        await _graceful_stop(tasks, active, grace_seconds=0.01)

        assert tasks["stuck"].cancelled()


class TestRunForever:
    async def test_refuses_to_start_with_no_configured_schemas(self) -> None:
        settings = _HermeticSettings()
        with pytest.raises(RuntimeError, match="EDGE_RELAY_SCHEMAS is empty"):
            await run_forever(settings, engine=object())  # type: ignore[arg-type]

    async def test_cancellation_stops_every_relay_and_re_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A fake relay whose `run()` blocks until `stop()` flips a flag — closer to the
        # real `OutboxRelay.run()` loop than "returns immediately", so this actually
        # exercises cancellation racing a relay that is still working.
        class _BlockingFakeRelay(_FakeRelay):
            def __init__(self, *, outcomes: list[BaseException | None]) -> None:
                super().__init__(outcomes=outcomes)
                self._stop_event = asyncio.Event()

            async def run(self) -> None:
                self.run_calls += 1
                await self._stop_event.wait()

            def stop(self) -> None:
                super().stop()
                self._stop_event.set()

        relays = {
            "account": _BlockingFakeRelay(outcomes=[]),
            "billing": _BlockingFakeRelay(outcomes=[]),
        }
        monkeypatch.setattr(
            "platform_core.edge_relay.OutboxRelay",
            lambda **kwargs: relays[kwargs["producer"]],
        )
        monkeypatch.setattr(
            "platform_core.edge_relay.assert_outbox_role",
            lambda engine, *, environment: asyncio.sleep(0),
        )

        settings = _HermeticSettings(
            edge_relay_schemas="account,billing", edge_relay_shutdown_grace_seconds=1.0
        )

        class _FakeEngine:
            async def dispose(self) -> None:
                return None

        task = asyncio.create_task(run_forever(settings, engine=_FakeEngine()))  # type: ignore[arg-type]
        await asyncio.sleep(0.05)  # let both supervisors start and build their relays
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert relays["account"].stopped
        assert relays["billing"].stopped
