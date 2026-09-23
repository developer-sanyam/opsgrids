"""The alerting state machine (M1). Pure — no I/O, no clock of its own.

Every check moves through the same four-state ladder, independently of every other check:

    ok -> pending -> firing -> ok
           ^  |          |
           |  +--(pass)--+ (back to ok before N fails)
           +-------(N consecutive fails)---------> firing: ONE "opened" notification
    firing -> (M consecutive passes) -> ok: ONE "resolved" notification
    firing -> (still failing, >= reminder_interval since the last notification) -> firing:
              ONE "reminder" notification, and the clock resets

`evaluate()` processes every check in one call and returns every notification that call
produced — so "one email per cycle grouping all changed checks" is just "one call to the
mailer per call to `evaluate()`" in `main.py`; this module never sends anything itself.

`evaluate_digest()` is the dead-man's switch: once a day, at a fixed hour, if nothing is
firing, it produces one "digest" notification saying so. It never fires while something is
already firing — the reminder above already covers that case, and a digest on top of a
reminder is one more email to read at 3am for no new information.

Persistence (`dump`/`load`) is JSON to `state/ops-monitor.json`, not the database — this
process must keep working when Postgres is the thing it is watching.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

#: Defaults named here, not scattered as bare literals through `evaluate`'s signature and
#: `config.py` both — one place to change the platform's idea of "how many fails is real".
DEFAULT_PENDING_THRESHOLD: Final[int] = 3
DEFAULT_RECOVERY_THRESHOLD: Final[int] = 3
DEFAULT_REMINDER_INTERVAL: Final[timedelta] = timedelta(hours=6)
DEFAULT_DIGEST_HOUR_UTC: Final[int] = 8


class Status(StrEnum):
    OK = "ok"
    PENDING = "pending"
    FIRING = "firing"


@dataclass(frozen=True, slots=True)
class Observation:
    """What one check saw this cycle. `ok=False` is a failure; `detail` is free text for a
    human reading `/status` or the alert email — never parsed back by this module."""

    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CheckState:
    status: Status = Status.OK
    consecutive_fails: int = 0
    consecutive_passes: int = 0
    last_detail: str = ""
    #: ISO-8601, UTC. When `status` last changed.
    since: str | None = None
    #: ISO-8601, UTC. The last time this check caused a notification (opened or reminder) —
    #: what the 6-hour reminder cadence is measured from. Cleared on resolution.
    last_notified_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "consecutive_fails": self.consecutive_fails,
            "consecutive_passes": self.consecutive_passes,
            "last_detail": self.last_detail,
            "since": self.since,
            "last_notified_at": self.last_notified_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CheckState:
        return cls(
            status=Status(raw.get("status", Status.OK.value)),
            consecutive_fails=int(raw.get("consecutive_fails", 0)),
            consecutive_passes=int(raw.get("consecutive_passes", 0)),
            last_detail=str(raw.get("last_detail", "")),
            since=raw.get("since"),
            last_notified_at=raw.get("last_notified_at"),
        )


@dataclass(frozen=True, slots=True)
class Notification:
    check: str
    #: "firing" (just opened), "reminder" (still firing), "resolved", or "digest" (the daily
    #: all-quiet dead-man's-switch — the one kind with no real check behind it; `check` is "*").
    kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class MonitorState:
    checks: Mapping[str, CheckState] = field(default_factory=dict)
    #: ISO date (`YYYY-MM-DD`), UTC. The last day the digest was sent, so it fires at most once
    #: a day even if the process restarts and replays the same hour twice.
    last_digest_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": {name: cs.to_dict() for name, cs in self.checks.items()},
            "last_digest_date": self.last_digest_date,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> MonitorState:
        checks = {name: CheckState.from_dict(cs) for name, cs in raw.get("checks", {}).items()}
        return cls(checks=checks, last_digest_date=raw.get("last_digest_date"))


def _now_iso(now: datetime) -> str:
    return now.astimezone(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _evaluate_one(
    prev: CheckState,
    obs: Observation,
    now: datetime,
    *,
    pending_threshold: int,
    recovery_threshold: int,
    reminder_interval: timedelta,
) -> tuple[CheckState, Notification | None]:
    now_iso = _now_iso(now)

    if obs.ok:
        if prev.status is Status.FIRING:
            passes = prev.consecutive_passes + 1
            if passes >= recovery_threshold:
                return (
                    CheckState(
                        status=Status.OK,
                        last_detail=obs.detail,
                        since=now_iso,
                        last_notified_at=None,
                    ),
                    Notification(check="", kind="resolved", detail=obs.detail),
                )
            return (
                replace(
                    prev,
                    consecutive_passes=passes,
                    consecutive_fails=0,
                    last_detail=obs.detail,
                ),
                None,
            )
        if prev.status is Status.PENDING:
            # Recovered before the pending threshold was reached — nothing was ever sent,
            # so there is nothing to resolve either.
            return (
                CheckState(status=Status.OK, last_detail=obs.detail, since=now_iso),
                None,
            )
        # Already OK: just refresh the detail so /status shows the latest reading.
        return (
            replace(prev, last_detail=obs.detail, since=prev.since or now_iso),
            None,
        )

    # obs.ok is False from here down.
    if prev.status is Status.OK:
        fails = 1
        if fails >= pending_threshold:
            return (
                CheckState(
                    status=Status.FIRING,
                    consecutive_fails=fails,
                    last_detail=obs.detail,
                    since=now_iso,
                    last_notified_at=now_iso,
                ),
                Notification(check="", kind="firing", detail=obs.detail),
            )
        return (
            CheckState(
                status=Status.PENDING,
                consecutive_fails=fails,
                last_detail=obs.detail,
                since=now_iso,
            ),
            None,
        )

    if prev.status is Status.PENDING:
        fails = prev.consecutive_fails + 1
        if fails >= pending_threshold:
            return (
                CheckState(
                    status=Status.FIRING,
                    consecutive_fails=fails,
                    last_detail=obs.detail,
                    since=now_iso,
                    last_notified_at=now_iso,
                ),
                Notification(check="", kind="firing", detail=obs.detail),
            )
        return (
            replace(prev, consecutive_fails=fails, last_detail=obs.detail),
            None,
        )

    # prev.status is Status.FIRING: still failing. Reset the recovery counter (hysteresis —
    # an intervening pass must not count toward recovery once it fails again), and check
    # whether it is time for a reminder.
    due_for_reminder = prev.last_notified_at is None or (
        now - _parse_iso(prev.last_notified_at) >= reminder_interval
    )
    if due_for_reminder:
        return (
            replace(
                prev,
                consecutive_passes=0,
                last_detail=obs.detail,
                last_notified_at=now_iso,
            ),
            Notification(check="", kind="reminder", detail=obs.detail),
        )
    return (
        replace(prev, consecutive_passes=0, last_detail=obs.detail),
        None,
    )


def evaluate(
    prev_state: MonitorState,
    observations: Mapping[str, Observation],
    now: datetime,
    *,
    pending_threshold: int = DEFAULT_PENDING_THRESHOLD,
    recovery_threshold: int = DEFAULT_RECOVERY_THRESHOLD,
    reminder_interval: timedelta = DEFAULT_REMINDER_INTERVAL,
) -> tuple[MonitorState, list[Notification]]:
    """Advance every check in `observations` by one cycle.

    A check named in `prev_state` but absent from `observations` this cycle (a probe that
    itself raised, or a check that was removed from configuration) is left exactly as it was
    — silence is not evidence of anything, and it must not decay toward "resolved" for a check
    nobody actually looked at this cycle.
    """
    new_checks: dict[str, CheckState] = dict(prev_state.checks)
    notifications: list[Notification] = []

    for name, obs in observations.items():
        prev = prev_state.checks.get(name, CheckState())
        new, notification = _evaluate_one(
            prev,
            obs,
            now,
            pending_threshold=pending_threshold,
            recovery_threshold=recovery_threshold,
            reminder_interval=reminder_interval,
        )
        new_checks[name] = new
        if notification is not None:
            notifications.append(replace(notification, check=name))

    new_state = MonitorState(checks=new_checks, last_digest_date=prev_state.last_digest_date)
    return new_state, notifications


def evaluate_digest(
    state: MonitorState,
    now: datetime,
    *,
    digest_hour_utc: int = DEFAULT_DIGEST_HOUR_UTC,
) -> tuple[MonitorState, Notification | None]:
    """The dead-man's switch: once a day, at `digest_hour_utc`, if nothing is firing, say so.

    Never fires while something *is* firing — the reminder cadence in `evaluate()` already
    covers that box being noisy, and a digest on top of it is a second email with no new
    information. Never fires twice on the same UTC date, so a process that restarts mid-hour
    (or that polls more than once inside the digest hour) does not resend it.
    """
    today = now.astimezone(UTC).date().isoformat()
    if state.last_digest_date == today:
        return state, None
    if now.astimezone(UTC).hour != digest_hour_utc:
        return state, None

    still_firing = any(cs.status is Status.FIRING for cs in state.checks.values())
    new_state = replace(state, last_digest_date=today)
    if still_firing:
        return new_state, None

    checked = ", ".join(sorted(state.checks)) or "no checks configured yet"
    return new_state, Notification(
        check="*", kind="digest", detail=f"all quiet — checked: {checked}"
    )


def dump(state: MonitorState, path: Path) -> None:
    """Write `state` to `path` atomically — a torn write must never leave a truncated,
    unparseable state file that the next restart cannot recover from."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def load(path: Path) -> MonitorState:
    """The empty state for a box that has never run this monitor, or whose state file was
    lost — restarting cold is "everything is OK, find out again", never a crash."""
    if not path.exists():
        return MonitorState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return MonitorState()
    return MonitorState.from_dict(raw)
