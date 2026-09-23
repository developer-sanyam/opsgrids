"""The state machine (M1), driven entirely by a fixed clock — no real time, no I/O.

Covers: hysteresis (N fails to open, recovery before the threshold is a no-op), the reminder
cadence while firing, recovery after M passes, one email per cycle grouping every check that
changed, and restarting cleanly from a persisted state file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from platform_core.ops_monitor.state import (
    CheckState,
    MonitorState,
    Notification,
    Observation,
    Status,
    dump,
    evaluate,
    evaluate_digest,
    load,
)

T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def _tick(n: int, seconds: int = 60) -> datetime:
    return T0 + timedelta(seconds=n * seconds)


def _fail(detail: str = "down") -> Observation:
    return Observation(ok=False, detail=detail)


def _ok(detail: str = "up") -> Observation:
    return Observation(ok=True, detail=detail)


class TestHysteresis:
    def test_a_single_failure_does_not_fire(self) -> None:
        state = MonitorState()
        state, notes = evaluate(state, {"disk": _fail()}, _tick(0), pending_threshold=3)
        assert state.checks["disk"].status is Status.PENDING
        assert notes == []

    def test_three_consecutive_failures_fire_once(self) -> None:
        state = MonitorState()
        notes: list[Notification] = []
        for i in range(3):
            state, notes = evaluate(state, {"disk": _fail()}, _tick(i), pending_threshold=3)
        assert state.checks["disk"].status is Status.FIRING
        assert len(notes) == 1
        assert notes[0].kind == "firing"
        assert notes[0].check == "disk"

    def test_recovering_before_the_threshold_is_a_no_op(self) -> None:
        state = MonitorState()
        state, notes = evaluate(state, {"disk": _fail()}, _tick(0), pending_threshold=3)
        assert state.checks["disk"].status is Status.PENDING
        state, notes = evaluate(state, {"disk": _ok()}, _tick(1), pending_threshold=3)
        assert state.checks["disk"].status is Status.OK
        assert notes == []  # never fired, so nothing to resolve

    def test_a_pending_threshold_of_one_fires_immediately(self) -> None:
        state = MonitorState()
        state, notes = evaluate(state, {"disk": _fail()}, _tick(0), pending_threshold=1)
        assert state.checks["disk"].status is Status.FIRING
        assert len(notes) == 1
        assert notes[0].kind == "firing"


class TestRecovery:
    def _fire(self, pending_threshold: int = 3) -> MonitorState:
        state = MonitorState()
        for i in range(pending_threshold):
            state, _ = evaluate(
                state, {"disk": _fail()}, _tick(i), pending_threshold=pending_threshold
            )
        assert state.checks["disk"].status is Status.FIRING
        return state

    def test_recovers_after_m_consecutive_passes(self) -> None:
        state = self._fire()
        notes: list[Notification] = []
        for i in range(3, 6):
            state, notes = evaluate(state, {"disk": _ok()}, _tick(i), recovery_threshold=3)
            if i < 5:
                assert state.checks["disk"].status is Status.FIRING
                assert notes == []
        assert state.checks["disk"].status is Status.OK
        assert len(notes) == 1
        assert notes[0].kind == "resolved"

    def test_a_failure_partway_through_recovery_resets_the_pass_count(self) -> None:
        state = self._fire()
        state, notes = evaluate(state, {"disk": _ok()}, _tick(3), recovery_threshold=3)
        assert state.checks["disk"].consecutive_passes == 1
        # One failure — recovery must start over, not merely pause.
        state, notes = evaluate(state, {"disk": _fail()}, _tick(4), recovery_threshold=3)
        assert state.checks["disk"].status is Status.FIRING
        assert state.checks["disk"].consecutive_passes == 0
        # Two more passes (only 2 of the 3 needed) must NOT resolve it.
        state, notes = evaluate(state, {"disk": _ok()}, _tick(5), recovery_threshold=3)
        state, notes = evaluate(state, {"disk": _ok()}, _tick(6), recovery_threshold=3)
        assert state.checks["disk"].status is Status.FIRING
        assert notes == []


class TestReminder:
    def test_no_reminder_before_the_interval_elapses(self) -> None:
        state = MonitorState()
        for i in range(3):
            state, _ = evaluate(state, {"disk": _fail()}, _tick(i), pending_threshold=3)
        # Still failing, one minute later — well inside a 6h reminder window.
        state, notes = evaluate(
            state,
            {"disk": _fail()},
            _tick(3),
            pending_threshold=3,
            reminder_interval=timedelta(hours=6),
        )
        assert state.checks["disk"].status is Status.FIRING
        assert notes == []

    def test_a_reminder_fires_once_the_interval_elapses(self) -> None:
        state = MonitorState()
        for i in range(3):
            state, _ = evaluate(state, {"disk": _fail()}, _tick(i), pending_threshold=3)
        fired_at = datetime.fromisoformat(state.checks["disk"].last_notified_at)  # type: ignore[arg-type]

        just_under = fired_at + timedelta(hours=6) - timedelta(seconds=1)
        state, notes = evaluate(
            state, {"disk": _fail()}, just_under, reminder_interval=timedelta(hours=6)
        )
        assert notes == []

        at_interval = fired_at + timedelta(hours=6)
        state, notes = evaluate(
            state, {"disk": _fail()}, at_interval, reminder_interval=timedelta(hours=6)
        )
        assert len(notes) == 1
        assert notes[0].kind == "reminder"

    def test_reminders_keep_repeating_while_still_firing(self) -> None:
        state = MonitorState()
        for i in range(3):
            state, _ = evaluate(state, {"disk": _fail()}, _tick(i), pending_threshold=3)

        now = _tick(3)
        reminders = 0
        for _ in range(3):
            now = now + timedelta(hours=6)
            state, notes = evaluate(
                state, {"disk": _fail()}, now, reminder_interval=timedelta(hours=6)
            )
            reminders += sum(1 for n in notes if n.kind == "reminder")
        assert reminders == 3


class TestGrouping:
    def test_one_cycle_covering_several_checks_returns_every_notification(self) -> None:
        """`evaluate()` itself is the grouping boundary — `main.py` sends exactly one email
        per call, so every check that changes in the same call must appear in one list."""
        state = MonitorState(
            checks={
                "disk": CheckState(
                    status=Status.FIRING,
                    consecutive_passes=2,
                    last_notified_at=_tick(0).isoformat(),
                ),
                "cert": CheckState(status=Status.OK),
            }
        )
        state, notes = evaluate(
            state,
            {
                "disk": _ok(),  # 3rd consecutive pass -> resolves
                "cert": _fail(),  # 1st failure -> pending, no notification yet
                "backup": _fail(),  # brand new check, 1st failure -> pending
            },
            _tick(10),
            pending_threshold=1,  # so "backup" and "cert" fire immediately too
            recovery_threshold=3,
        )
        kinds = {n.check: n.kind for n in notes}
        assert kinds["disk"] == "resolved"
        assert kinds["cert"] == "firing"
        assert kinds["backup"] == "firing"
        assert len(notes) == 3

    def test_a_check_absent_from_this_cycles_observations_is_left_untouched(self) -> None:
        """Silence about a check is not evidence about it — it must not decay toward
        resolution just because nobody looked this cycle."""
        state = MonitorState(
            checks={"disk": CheckState(status=Status.FIRING, consecutive_passes=2)}
        )
        state, notes = evaluate(state, {"cert": _ok()}, _tick(1))
        assert state.checks["disk"].status is Status.FIRING
        assert state.checks["disk"].consecutive_passes == 2
        assert notes == []


class TestDigest:
    def test_no_digest_outside_the_configured_hour(self) -> None:
        state = MonitorState()
        now = datetime(2026, 1, 1, 3, 0, tzinfo=UTC)
        state, notification = evaluate_digest(state, now, digest_hour_utc=8)
        assert notification is None

    def test_digest_fires_once_at_the_configured_hour_when_quiet(self) -> None:
        state = MonitorState(checks={"disk": CheckState(status=Status.OK)})
        now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        state, notification = evaluate_digest(state, now, digest_hour_utc=8)
        assert notification is not None
        assert notification.kind == "digest"
        assert state.last_digest_date == "2026-01-01"

    def test_digest_does_not_repeat_on_the_same_day(self) -> None:
        state = MonitorState(checks={"disk": CheckState(status=Status.OK)})
        now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        state, first = evaluate_digest(state, now, digest_hour_utc=8)
        assert first is not None
        later_same_hour = datetime(2026, 1, 1, 8, 59, tzinfo=UTC)
        state, second = evaluate_digest(state, later_same_hour, digest_hour_utc=8)
        assert second is None

    def test_digest_is_suppressed_while_something_is_firing(self) -> None:
        """The reminder cadence already covers a box that is actively unwell; a digest on
        top of it is a second email with no new information."""
        state = MonitorState(checks={"disk": CheckState(status=Status.FIRING)})
        now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        state, notification = evaluate_digest(state, now, digest_hour_utc=8)
        assert notification is None
        # Still recorded as "handled today" so it does not retry later the same day.
        assert state.last_digest_date == "2026-01-01"


class TestPersistence:
    def test_dump_and_load_round_trips(self, tmp_path: Path) -> None:
        state = MonitorState()
        for i in range(3):
            state, _ = evaluate(state, {"disk": _fail()}, _tick(i), pending_threshold=3)
        path = tmp_path / "ops-monitor.json"
        dump(state, path)

        loaded = load(path)
        assert loaded == state

    def test_restarting_from_the_state_file_keeps_the_firing_check_firing(
        self, tmp_path: Path
    ) -> None:
        """A process restart must not reset a firing check back to pending/ok — that would
        both lose the reminder clock and let a real outage silently re-open its own window."""
        state = MonitorState()
        for i in range(3):
            state, _ = evaluate(state, {"disk": _fail()}, _tick(i), pending_threshold=3)
        assert state.checks["disk"].status is Status.FIRING
        path = tmp_path / "ops-monitor.json"
        dump(state, path)

        restarted = load(path)
        # One more failure after "restart" must NOT re-fire — it is already firing.
        restarted, notes = evaluate(restarted, {"disk": _fail()}, _tick(3), pending_threshold=3)
        assert restarted.checks["disk"].status is Status.FIRING
        assert notes == []

    def test_loading_a_missing_file_is_a_cold_but_valid_start(self, tmp_path: Path) -> None:
        loaded = load(tmp_path / "does-not-exist.json")
        assert loaded == MonitorState()

    def test_loading_a_corrupt_file_is_a_cold_but_valid_start(self, tmp_path: Path) -> None:
        path = tmp_path / "ops-monitor.json"
        path.write_text("{not json", encoding="utf-8")
        loaded = load(path)
        assert loaded == MonitorState()

    def test_dump_is_atomic_and_leaves_no_tmp_file_on_success(self, tmp_path: Path) -> None:
        path = tmp_path / "ops-monitor.json"
        dump(MonitorState(), path)
        assert path.exists()
        assert not path.with_suffix(path.suffix + ".tmp").exists()
        assert json.loads(path.read_text(encoding="utf-8")) == MonitorState().to_dict()


class TestFlapping:
    """m9: a check that never fails N times *in a row* but fails often enough over a short
    window is not healthy, and pure consecutive-fail hysteresis would never open for it."""

    def test_pure_consecutive_hysteresis_alone_never_opens_a_2_of_3_flapper(self) -> None:
        """The failure mode this feature exists to fix: fail, ok, fail, ok, fail — 3 of 5,
        never 2 in a row — must open under `flap_threshold=3`."""
        state = MonitorState()
        pattern = [_fail(), _ok(), _fail(), _ok(), _fail()]
        notes: list[Notification] = []
        for i, obs in enumerate(pattern):
            state, notes = evaluate(
                state,
                {"flaky": obs},
                _tick(i),
                pending_threshold=3,  # never reached — no 3 IN A ROW in this pattern
                flap_window=5,
                flap_threshold=3,
            )
        assert state.checks["flaky"].status is Status.FIRING
        assert len(notes) == 1
        assert notes[0].kind == "firing"
        assert "flapping" in notes[0].detail

    def test_two_failures_in_the_window_do_not_open(self) -> None:
        state = MonitorState()
        pattern = [_fail(), _ok(), _ok(), _fail(), _ok()]
        for i, obs in enumerate(pattern):
            state, notes = evaluate(
                state,
                {"flaky": obs},
                _tick(i),
                pending_threshold=3,
                flap_window=5,
                flap_threshold=3,
            )
            assert notes == []
        assert state.checks["flaky"].status is Status.OK

    def test_the_window_is_maintained_on_passes_too_so_old_failures_age_out(self) -> None:
        """Two fails, then enough passes to push both out of a window of 3 — the check must
        not still read as flapping just because it failed at some point in the distant past."""
        state = MonitorState()
        for i, obs in enumerate([_fail(), _fail(), _ok(), _ok(), _ok()]):
            state, _ = evaluate(
                state,
                {"flaky": obs},
                _tick(i),
                pending_threshold=3,
                flap_window=3,
                flap_threshold=2,
            )
        assert state.checks["flaky"].recent == (True, True, True)
        assert state.checks["flaky"].status is Status.OK

    def test_flapping_state_round_trips_through_dump_and_load(self, tmp_path: Path) -> None:
        state = MonitorState()
        for i, obs in enumerate([_fail(), _ok(), _fail()]):
            state, _ = evaluate(state, {"flaky": obs}, _tick(i), flap_window=5, flap_threshold=3)
        path = tmp_path / "ops-monitor.json"
        dump(state, path)
        loaded = load(path)
        assert loaded.checks["flaky"].recent == state.checks["flaky"].recent


class TestRobustLoad:
    """m8: a single corrupt entry in the state file must degrade only that entry, and never
    crash the process reading it."""

    def _write(self, path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_checks_field_as_a_list_is_treated_as_no_history(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        self._write(path, {"checks": [], "last_digest_date": None})
        assert load(path) == MonitorState()

    def test_checks_field_as_null_is_treated_as_no_history(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        self._write(path, {"checks": None, "last_digest_date": None})
        assert load(path) == MonitorState()

    def test_top_level_null_is_a_cold_but_valid_start(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        self._write(path, None)
        assert load(path) == MonitorState()

    def test_top_level_list_is_a_cold_but_valid_start(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        self._write(path, [1, 2, 3])
        assert load(path) == MonitorState()

    def test_a_bad_status_on_one_check_degrades_only_that_check(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        self._write(
            path,
            {
                "checks": {
                    "good": CheckState(status=Status.FIRING, consecutive_fails=5).to_dict(),
                    "bad": {"status": "not-a-real-status"},
                },
                "last_digest_date": None,
            },
        )
        loaded = load(path)
        assert loaded.checks["good"].status is Status.FIRING
        assert loaded.checks["good"].consecutive_fails == 5
        assert loaded.checks["bad"] == CheckState()

    def test_a_bad_timestamp_on_one_check_degrades_only_that_check(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        self._write(
            path,
            {
                "checks": {
                    "good": CheckState(status=Status.OK).to_dict(),
                    "bad": {"status": "firing", "last_notified_at": "not-a-timestamp"},
                },
                "last_digest_date": None,
            },
        )
        loaded = load(path)
        assert loaded.checks["good"].status is Status.OK
        assert loaded.checks["bad"] == CheckState()

    def test_a_non_list_recent_field_degrades_only_that_check(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        self._write(
            path,
            {
                "checks": {"bad": {"status": "ok", "recent": "not-a-list"}},
                "last_digest_date": None,
            },
        )
        loaded = load(path)
        assert loaded.checks["bad"] == CheckState()

    def test_a_non_string_last_digest_date_is_dropped(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        self._write(path, {"checks": {}, "last_digest_date": 12345})
        loaded = load(path)
        assert loaded.last_digest_date is None

    def test_a_non_dict_check_entry_degrades_to_fresh(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        self._write(path, {"checks": {"bad": "not-a-mapping"}, "last_digest_date": None})
        loaded = load(path)
        assert loaded.checks["bad"] == CheckState()
