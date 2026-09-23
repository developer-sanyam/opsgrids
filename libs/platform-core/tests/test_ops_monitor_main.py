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
from platform_core.ops_monitor.delivery import SentRecord
from platform_core.ops_monitor.mailer import Mailer
from platform_core.ops_monitor.main import (
    _collect,
    _deliver,
    _dump_safely,
    _revert_notification_bookkeeping,
    run_cycle_and_notify,
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


def _at_cap(n: int = 12) -> MonitorRuntime:
    runtime = MonitorRuntime()
    runtime.sent_history = [
        SentRecord(at=NOW - timedelta(minutes=i), exempt=False) for i in range(n)
    ]
    return runtime


class TestDeliverSendFailureRetry:
    """M6: a failed send does not commit until the retry cap — `_deliver` now returns the list
    of notifications to revert (empty list means "nothing to revert")."""

    async def test_a_successful_send_commits_and_records_history(self) -> None:
        runtime = MonitorRuntime()
        mailer = _FakeMailer(outcomes=[True])
        settings = _settings()
        reverted = await _deliver(
            settings, runtime, mailer, [Notification(check="disk", kind="firing", detail="d")], NOW
        )
        assert reverted == []
        # Nowhere near the cap, so this flushed as an ordinary send, not an exempt bypass —
        # `exempt_send` only means something once the ordinary cap is actually full.
        assert runtime.sent_history == [SentRecord(at=NOW, exempt=False)]
        assert runtime.consecutive_send_failures == 0
        assert runtime.last_fired_at["disk"] == NOW

    async def test_a_failed_send_reverts_until_the_retry_cap(self) -> None:
        runtime = MonitorRuntime()
        mailer = _FakeMailer(outcomes=[False])
        settings = _settings(ops_monitor_max_immediate_send_retries=3)
        note = [Notification(check="disk", kind="reminder", detail="d")]

        for _ in range(3):
            reverted = await _deliver(settings, runtime, mailer, note, NOW)
            assert reverted == note
        # The 4th consecutive failure exceeds the cap and is accepted (nothing more to revert).
        reverted = await _deliver(settings, runtime, mailer, note, NOW)
        assert reverted == []
        assert runtime.consecutive_send_failures == 0

    async def test_a_failed_send_of_an_exempt_kind_still_reverts_it(self) -> None:
        """`firing`/`digest` are exempt from the *cap*, not from an actual send failure —
        if the mailer itself is down, everything in the batch failed to deliver."""
        runtime = MonitorRuntime()
        mailer = _FakeMailer(outcomes=[False])
        settings = _settings(ops_monitor_max_immediate_send_retries=3)
        note = [Notification(check="disk", kind="firing", detail="d")]

        reverted = await _deliver(settings, runtime, mailer, note, NOW)
        assert reverted == note


class TestDeliverRateCap:
    """M3 (round 2, refined round 3): the per-hour cap holds back a reminder/resolved for a
    check already reported this hour — never a check's *first-ever* reported firing (or
    equivalent, see rule 4), never the daily digest — and queues what it holds back so the
    next email under the cap still lists every check that was waiting, by name. `delivery.py`
    now decides all of this; `_deliver` only executes it."""

    async def test_a_reminder_for_a_check_already_reported_this_hour_is_suppressed(self) -> None:
        runtime = _at_cap()
        runtime.last_fired_at["disk"] = NOW - timedelta(minutes=5)
        mailer = _FakeMailer(outcomes=[True])
        settings = _settings(ops_monitor_max_emails_per_hour=12)

        reverted = await _deliver(
            settings,
            runtime,
            mailer,
            [Notification(check="disk", kind="reminder", detail="d")],
            NOW,
        )
        assert len(reverted) == 1
        assert mailer.calls == 0
        assert [n.check for n in runtime.pending_notifications] == ["disk"]

    async def test_a_reminder_with_no_prior_successful_notify_is_exempt_even_at_cap(self) -> None:
        """Rule 4 (round 3): nobody has ever actually been told this check is a problem — its
        original `firing` was itself dropped or never sent — so its first reminder must not be
        held hostage by the cap either."""
        runtime = _at_cap()
        mailer = _FakeMailer(outcomes=[True])
        settings = _settings(ops_monitor_max_emails_per_hour=12)

        reverted = await _deliver(
            settings,
            runtime,
            mailer,
            [Notification(check="disk", kind="reminder", detail="still down")],
            NOW,
        )
        assert reverted == []
        assert mailer.calls == 1
        assert runtime.last_fired_at["disk"] == NOW

    async def test_a_first_ever_firing_is_never_suppressed_even_at_the_cap(self) -> None:
        runtime = _at_cap()
        mailer = _FakeMailer(outcomes=[True])
        settings = _settings(ops_monitor_max_emails_per_hour=12)

        reverted = await _deliver(
            settings, runtime, mailer, [Notification(check="cert", kind="firing", detail="d")], NOW
        )
        assert reverted == []
        assert mailer.calls == 1
        assert mailer.sent[0][0]  # a subject was actually built and sent
        assert runtime.sent_history[-1].exempt is True
        assert runtime.last_fired_at["cert"] == NOW

    async def test_a_firing_reported_within_the_last_hour_is_not_exempt_a_second_time(self) -> None:
        """round 3's own MAJOR finding, the bug `probe_m3.py` scenario A/C exposed: the
        pre-round-3 code exempted every `kind == "firing"` regardless of history — a check
        that re-fires minutes after its first report got a second free pass through the cap,
        indistinguishable from a check nobody had heard about yet."""
        runtime = _at_cap()
        runtime.last_fired_at["cert"] = NOW - timedelta(minutes=10)
        mailer = _FakeMailer(outcomes=[True])
        settings = _settings(ops_monitor_max_emails_per_hour=12)

        reverted = await _deliver(
            settings, runtime, mailer, [Notification(check="cert", kind="firing", detail="d")], NOW
        )
        assert len(reverted) == 1
        assert mailer.calls == 0
        assert [n.check for n in runtime.pending_notifications] == ["cert"]

    async def test_the_digest_is_never_suppressed_even_at_the_cap(self) -> None:
        runtime = _at_cap()
        mailer = _FakeMailer(outcomes=[True])
        settings = _settings(ops_monitor_max_emails_per_hour=12)

        reverted = await _deliver(
            settings,
            runtime,
            mailer,
            [Notification(check="*", kind="digest", detail="all quiet")],
            NOW,
        )
        assert reverted == []
        assert mailer.calls == 1

    async def test_a_new_check_opening_while_capped_is_named_in_the_next_sent_email(self) -> None:
        """m3's own scenario: a reminder gets capped, then on a later cycle (window freed) a
        brand new check opens — the email that goes out must name BOTH."""
        runtime = _at_cap()
        runtime.last_fired_at["disk"] = NOW - timedelta(minutes=5)  # already reported -- ordinary
        mailer = _FakeMailer(outcomes=[True])
        settings = _settings(ops_monitor_max_emails_per_hour=12)

        await _deliver(
            settings,
            runtime,
            mailer,
            [Notification(check="disk", kind="reminder", detail="d")],
            NOW,
        )
        assert mailer.calls == 0  # still capped

        # The window frees (old history ages out) and a different check opens for real.
        runtime.sent_history = []
        reverted = await _deliver(
            settings,
            runtime,
            mailer,
            [Notification(check="cert", kind="firing", detail="expiring")],
            NOW + timedelta(hours=1, minutes=1),
        )
        assert reverted == []
        _subject, body = mailer.sent[0]
        assert "cert" in body
        assert "disk" in body  # the previously-capped check is not lost
        assert runtime.pending_notifications == []

    async def test_history_older_than_an_hour_is_pruned_before_checking_the_cap(self) -> None:
        runtime = MonitorRuntime()
        runtime.sent_history = [SentRecord(at=NOW - timedelta(hours=2), exempt=False)] * 12
        runtime.last_fired_at["disk"] = NOW - timedelta(minutes=5)
        mailer = _FakeMailer(outcomes=[True])
        settings = _settings(ops_monitor_max_emails_per_hour=12)

        reverted = await _deliver(
            settings,
            runtime,
            mailer,
            [Notification(check="disk", kind="reminder", detail="d")],
            NOW,
        )
        assert reverted == []
        assert mailer.calls == 1  # not suppressed — the old history had already aged out


class TestUndeliveredResolvedCarriedForward:
    """m4: a `resolved` notice that could not be sent this cycle (capped, or a failed send)
    must not simply vanish — nothing about the check's own state will ever regenerate it, since
    the check is already back to OK by the time `evaluate()` produces it."""

    async def test_a_capped_resolved_notice_is_queued_and_sent_once_the_window_frees(self) -> None:
        runtime = _at_cap()
        mailer = _FakeMailer(outcomes=[True])
        settings = _settings(ops_monitor_max_emails_per_hour=12)

        await _deliver(
            settings,
            runtime,
            mailer,
            [Notification(check="disk", kind="resolved", detail="back to normal")],
            NOW,
        )
        assert mailer.calls == 0
        assert [n.check for n in runtime.pending_notifications] == ["disk"]

        runtime.sent_history = []
        reverted = await _deliver(settings, runtime, mailer, [], NOW + timedelta(hours=1))
        assert reverted == []
        assert mailer.calls == 1
        assert "disk" in mailer.sent[0][1]
        assert "back to normal" in mailer.sent[0][1]


class _ScriptedMailer(Mailer):
    """Round 3: `probe_m3.py`'s own `FM` fake, committed. `script` is a queue of send
    outcomes; once exhausted every further send succeeds, exactly like the probe."""

    def __init__(self, script: list[bool] | None = None) -> None:
        super().__init__(OpsMonitorSettings())
        self.script = list(script or [])
        self.sent: list[tuple[str, str]] = []

    async def send(self, subject: str, body: str) -> bool:
        ok = self.script.pop(0) if self.script else True
        if ok:
            self.sent.append((subject, body))
        return ok


async def _cycle(
    settings: OpsMonitorSettings,
    runtime: MonitorRuntime,
    mailer: Mailer,
    observations: dict[str, Observation],
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_one_cycle(*_a: object, **_kw: object) -> dict[str, Observation]:
        return dict(observations)

    monkeypatch.setattr("platform_core.ops_monitor.main.run_one_cycle", fake_run_one_cycle)
    # `run_one_cycle` is replaced above, so neither the engine nor the client is ever touched.
    no_engine: Any = None
    await run_cycle_and_notify(
        settings, runtime, mailer, outbox_engine=no_engine, http_client=no_engine, now=now
    )


class TestProbeScenarios:
    """Round 3's own reviewer probes (`probe_m3.py`, scenarios A/C/D — B is `TestDeliverRateCap
    .test_a_reminder_with_no_prior_successful_notify_is_exempt_even_at_cap` above, verbatim),
    committed as end-to-end tests through `run_cycle_and_notify`. All four are concrete bugs
    the pre-round-3 `_deliver`/`_merge_pending` implementation had, rooted in treating every
    `kind == "firing"` notification as exempt regardless of history — every one of these fails
    against `f68e82f` (`_CAP_EXEMPT_KINDS = frozenset({"firing", "digest"})`) and passes now."""

    async def test_scenario_a_a_refiring_check_is_not_exempt_the_second_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = _settings(ops_monitor_digest_hour_utc=23)
        runtime = MonitorRuntime()
        mailer = _ScriptedMailer()
        t = NOW

        for _ in range(3):  # opens -- delivered immediately, nowhere near the cap yet
            await _cycle(
                settings,
                runtime,
                mailer,
                {"api.x": Observation(ok=False, detail="down")},
                t,
                monkeypatch,
            )
            t += timedelta(minutes=1)
        assert runtime.state.checks["api.x"].status is Status.FIRING
        assert len(mailer.sent) == 1  # the opening

        runtime.sent_history = [SentRecord(at=t - timedelta(minutes=1), exempt=False)] * 12

        for _ in range(3):  # recovers -- resolved has no undelivered predecessor, so it queues
            await _cycle(
                settings,
                runtime,
                mailer,
                {"api.x": Observation(ok=True, detail="up")},
                t,
                monkeypatch,
            )
            t += timedelta(minutes=1)
        assert [n.kind for n in runtime.pending_notifications] == ["resolved"]

        for _ in range(3):  # reopens within the hour -- must NOT bypass the cap a second time
            await _cycle(
                settings,
                runtime,
                mailer,
                {"api.x": Observation(ok=False, detail="down")},
                t,
                monkeypatch,
            )
            t += timedelta(minutes=1)
        assert len(mailer.sent) == 1, "a re-firing check within the hour must not bypass the cap"
        # The merge dropped the stale queued resolved -- the check is firing again right now.
        assert [n.kind for n in runtime.pending_notifications] == ["firing"]

        t += timedelta(hours=1)  # the cap clears
        await _cycle(
            settings,
            runtime,
            mailer,
            {"api.x": Observation(ok=False, detail="down")},
            t,
            monkeypatch,
        )
        assert len(mailer.sent) == 2
        assert runtime.pending_notifications == []

    async def test_scenario_c_a_flapping_check_never_exceeds_cap_plus_exempt_ceiling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bug: because the old code treated every `firing` as exempt regardless of
        history, a single check flapping open/closed fast enough could generate one exempt
        (uncapped) email every time it reopened — an unbounded flood from one flaky check.
        `cap + EXEMPT_CEILING_MULTIPLIER * cap` bounds the total per rolling hour regardless."""
        settings = _settings(ops_monitor_max_emails_per_hour=3, ops_monitor_digest_hour_utc=23)
        runtime = MonitorRuntime()
        mailer = _ScriptedMailer()
        t = NOW
        pattern = [False, False, False, True, True, True]

        for i in range(60):
            await _cycle(
                settings,
                runtime,
                mailer,
                {"api.x": Observation(ok=pattern[i % len(pattern)], detail="flap")},
                t,
                monkeypatch,
            )
            t += timedelta(minutes=1)

        ceiling = settings.ops_monitor_max_emails_per_hour * 3  # cap + 2*cap
        assert len(mailer.sent) <= ceiling

    async def test_scenario_d_resolve_and_reopen_in_the_same_cycle_is_not_lost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """At cap: `api.x` resolves in the same cycle `api.y` opens for the first time. `api.y`
        is exempt and is delivered right away; `api.x`'s `resolved` has no undelivered
        predecessor (its own `firing` was already sent, long before the cap was forced on), so
        it queues ordinarily — and must not be dropped just because a different check's
        notification shared the cycle."""
        settings = _settings(ops_monitor_digest_hour_utc=23)
        runtime = MonitorRuntime()
        mailer = _ScriptedMailer()
        t = NOW

        for _ in range(3):
            await _cycle(
                settings,
                runtime,
                mailer,
                {
                    "api.x": Observation(ok=False, detail="x"),
                    "api.y": Observation(ok=True, detail="y"),
                },
                t,
                monkeypatch,
            )
            t += timedelta(minutes=1)
        assert runtime.state.checks["api.x"].status is Status.FIRING

        runtime.sent_history = [
            SentRecord(at=t, exempt=False)
        ] * settings.ops_monitor_max_emails_per_hour

        for _ in range(3):  # x recovers, y opens -- same cycle on the third iteration
            await _cycle(
                settings,
                runtime,
                mailer,
                {
                    "api.x": Observation(ok=True, detail="x-up"),
                    "api.y": Observation(ok=False, detail="y-down"),
                },
                t,
                monkeypatch,
            )
            t += timedelta(minutes=1)

        kinds = {n.check: n.kind for n in runtime.pending_notifications}
        assert kinds.get("api.x") == "resolved", (
            "x's resolved must not be dropped just because y opened at the same time"
        )

        for _ in range(90):
            if not runtime.pending_notifications:
                break
            await _cycle(
                settings,
                runtime,
                mailer,
                {
                    "api.x": Observation(ok=True, detail="x-up"),
                    "api.y": Observation(ok=False, detail="y-down"),
                },
                t,
                monkeypatch,
            )
            t += timedelta(minutes=1)

        assert any("x-up" in body for _subject, body in mailer.sent), (
            "x's resolved was eventually delivered"
        )
