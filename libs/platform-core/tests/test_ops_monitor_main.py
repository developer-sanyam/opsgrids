"""`main.py`'s own orchestration logic — timeouts (M4), state-write resilience (B2), and
notification delivery/retry/rate-cap (M6/m9) — all with fakes, no real infra. The checks
themselves are covered by `test_ops_monitor_checks.py` (pure) and
`test_ops_monitor_integration.py` (real Postgres/RabbitMQ/MailHog).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from platform_core.ops_monitor.config import OpsMonitorSettings
from platform_core.ops_monitor.mailer import Mailer
from platform_core.ops_monitor.main import (
    _collect,
    _deliver,
    _dump_safely,
    _revert_notification_bookkeeping,
    run_one_cycle,
)
from platform_core.ops_monitor.server import MonitorRuntime
from platform_core.ops_monitor.state import (
    CheckState,
    MonitorState,
    Notification,
    Observation,
    Status,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _FakeMailer(Mailer):
    """A real `Mailer` subclass — so it satisfies `_deliver`'s type exactly — with `send`
    replaced by a scripted outcome instead of ever touching `smtplib`."""

    def __init__(self, outcomes: list[bool] | None = None) -> None:
        super().__init__(OpsMonitorSettings())
        self.outcomes = outcomes or [True]
        self.calls = 0
        self.sent: list[tuple[str, str]] = []

    async def send(self, subject: str, body: str) -> bool:
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if outcome:
            self.sent.append((subject, body))
        return outcome


def _settings(**overrides: Any) -> OpsMonitorSettings:
    return OpsMonitorSettings(**overrides)


class TestCollectTimeout:
    """M4: one hung probe must cost only its own observation, and must not block the others
    or the heartbeat behind it."""

    async def test_a_probe_that_never_returns_times_out_as_a_failing_observation(self) -> None:
        async def hangs_forever() -> dict[str, Observation]:
            await asyncio.sleep(10)
            return {}

        observations: dict[str, Observation] = {}
        await _collect("slow", hangs_forever(), observations, timeout_seconds=0.05)

        assert observations["slow"].ok is False
        assert "timed out" in observations["slow"].detail

    async def test_a_hung_probe_does_not_delay_the_ones_after_it(self) -> None:
        """The regression this exists to prevent: a hung DB/broker call swallowing the whole
        cycle, including checks that have nothing to do with it."""

        async def hangs_forever() -> dict[str, Observation]:
            await asyncio.sleep(10)
            return {}

        async def fast_ok() -> dict[str, Observation]:
            return {"fast": Observation(ok=True)}

        observations: dict[str, Observation] = {}
        start = asyncio.get_event_loop().time()
        await _collect("slow", hangs_forever(), observations, timeout_seconds=0.05)
        await _collect("fast", fast_ok(), observations, timeout_seconds=5)
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed < 1.0
        assert "fast" in observations

    async def test_a_crashing_probe_is_caught_the_same_as_a_timeout(self) -> None:
        async def boom() -> dict[str, Observation]:
            raise RuntimeError("db connection reset")

        observations: dict[str, Observation] = {}
        await _collect("outbox", boom(), observations, timeout_seconds=5)

        assert observations["outbox"].ok is False
        assert "probe crashed" in observations["outbox"].detail


class TestRunOneCycleRoutesEveryCheckThroughCollect:
    async def test_a_hung_outbox_engine_does_not_prevent_disk_backup_or_cert_from_reporting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A locked outbox table (simulated: an engine whose `connect()` never returns) must
        not stall disk, backup or cert — all still report this cycle."""

        class _HangingEngine:
            def connect(self) -> _HangingEngine:
                return self

            async def __aenter__(self) -> _HangingEngine:
                await asyncio.sleep(10)
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

        class _NoDlqClient:
            pass

        async def _fake_dlq(*args: object, **kwargs: object) -> dict[str, object]:
            return {}

        async def _fake_api(*args: object, **kwargs: object) -> dict[str, object]:
            return {}

        monkeypatch.setattr("platform_core.ops_monitor.checks.check_dlq_depth", _fake_dlq)
        monkeypatch.setattr("platform_core.ops_monitor.checks.check_api_readiness", _fake_api)

        settings = _settings(
            ops_monitor_probe_timeout_seconds=0.05,
            ops_monitor_disk_path=str(tmp_path),
            ops_monitor_backup_marker_path=str(tmp_path / "backup.last-ok"),
            edge_host="",  # skips the cert handshake cleanly
            edge_relay_schemas="probe",  # so check_outbox_lag actually reaches the engine
        )
        observations = await run_one_cycle(
            settings,
            outbox_engine=_HangingEngine(),  # type: ignore[arg-type]
            http_client=_NoDlqClient(),  # type: ignore[arg-type]
            now=NOW,
        )
        assert observations["outbox"].ok is False
        assert "timed out" in observations["outbox"].detail
        assert "disk" in observations
        assert "backup" in observations
        assert "cert" in observations


class TestDumpSafely:
    """B2: a state write that cannot land (an unwritable directory standing in for a
    uid/permissions mismatch) must be logged, never fatal."""

    def test_an_unwritable_directory_does_not_raise(self, tmp_path: Path) -> None:
        unwritable = tmp_path / "state"
        unwritable.mkdir(mode=0o400)
        try:
            _dump_safely(MonitorState(), unwritable / "ops-monitor.json")
        finally:
            unwritable.chmod(0o700)  # so pytest can clean up tmp_path afterwards

    def test_a_writable_directory_still_writes(self, tmp_path: Path) -> None:
        path = tmp_path / "ops-monitor.json"
        _dump_safely(MonitorState(), path)
        assert path.exists()


class TestRevertNotificationBookkeeping:
    def test_a_firing_notifications_last_notified_at_is_reverted(self) -> None:
        prev = MonitorState(checks={"disk": CheckState(status=Status.OK)})
        new = MonitorState(
            checks={
                "disk": CheckState(
                    status=Status.FIRING, last_notified_at="2026-01-01T00:00:00+00:00"
                )
            }
        )
        reverted = _revert_notification_bookkeeping(
            prev, new, [Notification(check="disk", kind="firing", detail="down")]
        )
        assert reverted.checks["disk"].last_notified_at is None
        assert reverted.checks["disk"].status is Status.FIRING  # the real status is kept

    def test_a_resolved_notification_is_not_reverted(self) -> None:
        """Nothing to revert to — `last_notified_at` is already cleared on resolution, and
        the check really has recovered regardless of whether the email went out."""
        prev = MonitorState(checks={"disk": CheckState(status=Status.FIRING)})
        new = MonitorState(checks={"disk": CheckState(status=Status.OK, last_notified_at=None)})
        reverted = _revert_notification_bookkeeping(
            prev, new, [Notification(check="disk", kind="resolved", detail="up")]
        )
        assert reverted.checks["disk"].status is Status.OK

    def test_a_digest_notification_reverts_last_digest_date(self) -> None:
        prev = MonitorState(last_digest_date="2025-12-31")
        new = MonitorState(last_digest_date="2026-01-01")
        reverted = _revert_notification_bookkeeping(
            prev, new, [Notification(check="*", kind="digest", detail="all quiet")]
        )
        assert reverted.last_digest_date == "2025-12-31"


class TestDeliver:
    async def test_a_successful_send_commits_and_records_history(self) -> None:
        runtime = MonitorRuntime()
        mailer = _FakeMailer(outcomes=[True])
        settings = _settings()
        committed = await _deliver(
            settings,
            runtime,
            mailer,
            [Notification(check="disk", kind="firing", detail="d")],
            NOW,
        )
        assert committed is True
        assert runtime.sent_at_history == [NOW]
        assert runtime.consecutive_send_failures == 0

    async def test_a_failed_send_does_not_commit_until_the_retry_cap(self) -> None:
        runtime = MonitorRuntime()
        mailer = _FakeMailer(outcomes=[False])
        settings = _settings(ops_monitor_max_immediate_send_retries=3)
        note = [Notification(check="disk", kind="firing", detail="d")]

        for _ in range(3):
            committed = await _deliver(settings, runtime, mailer, note, NOW)
            assert committed is False
        # The 4th consecutive failure exceeds the cap and is accepted as committed.
        committed = await _deliver(settings, runtime, mailer, note, NOW)
        assert committed is True
        assert runtime.consecutive_send_failures == 0

    async def test_the_hourly_cap_suppresses_without_calling_the_mailer(self) -> None:
        runtime = MonitorRuntime()
        runtime.sent_at_history = [NOW - timedelta(minutes=i) for i in range(12)]
        mailer = _FakeMailer(outcomes=[True])
        settings = _settings(ops_monitor_max_emails_per_hour=12)

        committed = await _deliver(
            settings,
            runtime,
            mailer,
            [Notification(check="disk", kind="firing", detail="d")],
            NOW,
        )
        assert committed is True  # a policy choice, not a failure — no retry needed
        assert mailer.calls == 0
        assert runtime.suppressed_count == 1

    async def test_history_older_than_an_hour_is_pruned_before_checking_the_cap(self) -> None:
        runtime = MonitorRuntime()
        runtime.sent_at_history = [NOW - timedelta(hours=2)] * 12
        mailer = _FakeMailer(outcomes=[True])
        settings = _settings(ops_monitor_max_emails_per_hour=12)

        committed = await _deliver(
            settings,
            runtime,
            mailer,
            [Notification(check="disk", kind="firing", detail="d")],
            NOW,
        )
        assert committed is True
        assert mailer.calls == 1  # not suppressed — the old history had already aged out
