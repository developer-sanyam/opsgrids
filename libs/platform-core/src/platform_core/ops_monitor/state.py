"""The alerting state machine (M1). Pure — no I/O, no clock of its own.

Every check moves through the same four-state ladder, independently of every other check:

    ok -> pending -> firing -> ok
           ^  |          |
           |  +--(pass)--+ (back to ok before N fails)
           +-------(N consecutive fails)---------> firing: ONE "opened" notification
           +-------(F fails within the last W polls)------> firing, same notification
                     (a flapper that never fails N times *in a row* still opens — see
                     "Flapping" below)
    firing -> (M consecutive passes) -> ok: ONE "resolved" notification
    firing -> (still failing, >= reminder_interval since the last notification) -> firing:
              ONE "reminder" notification, and the clock resets

**Flapping.** A check that fails, passes, fails, passes, fails never reaches N *consecutive*
fails and would never open under pure hysteresis — which is wrong, because three failures in
five polls is not a healthy check. Every `CheckState` keeps a short rolling window of its last
`flap_window` pass/fail results (default 5); opening also fires when `flap_threshold` (default
3) or more of them are failures, regardless of the consecutive-fail counter. The window is
maintained every cycle, on a pass or a fail alike, so a check that stabilises ages the bad
results out of it in `flap_window` polls without needing anything special to happen.

`evaluate()` processes every check in one call and returns every notification that call
produced — so "one email per cycle grouping all changed checks" is just "one call to the
mailer per call to `evaluate()`" in `main.py`; this module never sends anything itself, and it
never decides whether a send succeeded — see `main.py`'s own handling of that (M6): a failed
send must not consume the reminder/digest clock this module advances, so the caller is
responsible for reverting `last_notified_at`/`last_digest_date` on a failed send, not this one.

`evaluate_digest()` is the dead-man's switch: once a day, at a fixed hour, if nothing is
firing, it produces one "digest" notification saying so. It never fires while something is
already firing — the reminder above already covers that case, and a digest on top of a
reminder is one more email to read at 3am for no new information.

Persistence (`dump`/`load`) is JSON to `state/ops-monitor.json`, not the database — this
process must keep working when Postgres is the thing it is watching. Loading is defensive on
purpose (m8): a single corrupt check's entry, or a state file that predates a field this module
now expects, degrades that one check back to fresh rather than crashing the whole process on
its own persisted history.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from platform_core.logging import get_logger

log = get_logger("ops_monitor")

#: Defaults named here, not scattered as bare literals through `evaluate`'s signature and
#: `config.py` both — one place to change the platform's idea of "how many fails is real".
DEFAULT_PENDING_THRESHOLD: Final[int] = 3
DEFAULT_RECOVERY_THRESHOLD: Final[int] = 3
DEFAULT_REMINDER_INTERVAL: Final[timedelta] = timedelta(hours=6)
DEFAULT_DIGEST_HOUR_UTC: Final[int] = 8
#: m9: a check that fails this many times or more within the last `DEFAULT_FLAP_WINDOW` polls
#: opens even without `DEFAULT_PENDING_THRESHOLD` consecutive fails.
DEFAULT_FLAP_WINDOW: Final[int] = 5
DEFAULT_FLAP_THRESHOLD: Final[int] = 3


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
    #: m9: the last `flap_window` pass(`True`)/fail(`False`) results, oldest first. Bounded by
    #: whatever `flap_window` `evaluate()` is called with; a longer or shorter window than a
    #: persisted history simply grows or is trimmed on the next cycle.
    recent: tuple[bool, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "consecutive_fails": self.consecutive_fails,
            "consecutive_passes": self.consecutive_passes,
            "last_detail": self.last_detail,
            "since": self.since,
            "last_notified_at": self.last_notified_at,
            "recent": list(self.recent),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CheckState:
        """Raises on anything unparseable — callers (`MonitorState.from_dict`) catch per
        entry, so one bad check's history degrades that check back to fresh rather than
        taking every other check's history down with it."""
        recent_raw = raw.get("recent", [])
        if not isinstance(recent_raw, list):
            raise ValueError(f"'recent' must be a list, got {type(recent_raw).__name__}")
        since = raw.get("since")
        last_notified_at = raw.get("last_notified_at")
        for name, value in (("since", since), ("last_notified_at", last_notified_at)):
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError(f"{name!r} must be a string or null")
                datetime.fromisoformat(value)  # raises ValueError on anything unparseable
        return cls(
            status=Status(raw.get("status", Status.OK.value)),
            consecutive_fails=int(raw.get("consecutive_fails", 0)),
            consecutive_passes=int(raw.get("consecutive_passes", 0)),
            last_detail=str(raw.get("last_detail", "")),
            since=since,
            last_notified_at=last_notified_at,
            recent=tuple(bool(v) for v in recent_raw),
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
        """Never raises (m8): a state file this defensive about its own history has no excuse
        crashing the process that reads it. `checks` that is not a mapping at all (`[]`,
        `null`, a stray string) is treated as "no history yet"; any *individual* check whose
        entry cannot be parsed (a bad `status`, an unparseable timestamp, `recent` that is not
        a list) is logged and dropped back to a fresh `CheckState()` rather than taking the
        whole file down with it — the alternative is a single corrupt entry permanently
        wiping every other check's carefully-built hysteresis history on the next restart.
        """
        raw_checks = raw.get("checks")
        checks: dict[str, CheckState] = {}
        if isinstance(raw_checks, Mapping):
            for name, entry in raw_checks.items():
                try:
                    if not isinstance(entry, Mapping):
                        raise ValueError(f"expected a mapping, got {type(entry).__name__}")
                    checks[name] = CheckState.from_dict(entry)
                except Exception as exc:  # one bad entry must not sink every other check
                    log.warning(
                        "ops_monitor.state.corrupt_check_entry",
                        check=name,
                        error=str(exc)[:200],
                    )
                    checks[name] = CheckState()
        elif raw_checks not in (None, {}):
            log.warning(
                "ops_monitor.state.corrupt_checks_field",
                type=type(raw_checks).__name__,
            )

        last_digest_date = raw.get("last_digest_date")
        if last_digest_date is not None and not isinstance(last_digest_date, str):
            log.warning("ops_monitor.state.corrupt_last_digest_date", value=repr(last_digest_date))
            last_digest_date = None

        return cls(checks=checks, last_digest_date=last_digest_date)


def _now_iso(now: datetime) -> str:
    return now.astimezone(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _push_recent(prev: tuple[bool, ...], ok: bool, *, flap_window: int) -> tuple[bool, ...]:
    return (*prev, ok)[-flap_window:]


def _is_flapping(recent: tuple[bool, ...], *, flap_threshold: int) -> bool:
    return sum(1 for ok in recent if not ok) >= flap_threshold


def _evaluate_one(
    prev: CheckState,
    obs: Observation,
    now: datetime,
    *,
    pending_threshold: int,
    recovery_threshold: int,
    reminder_interval: timedelta,
    flap_window: int,
    flap_threshold: int,
) -> tuple[CheckState, Notification | None]:
    now_iso = _now_iso(now)
    recent = _push_recent(prev.recent, obs.ok, flap_window=flap_window)

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
                        recent=recent,
                    ),
                    Notification(check="", kind="resolved", detail=obs.detail),
                )
            return (
                replace(
                    prev,
                    consecutive_passes=passes,
                    consecutive_fails=0,
                    last_detail=obs.detail,
                    recent=recent,
                ),
                None,
            )
        if prev.status is Status.PENDING:
            # Recovered before the pending threshold was reached — nothing was ever sent,
            # so there is nothing to resolve either.
            return (
                CheckState(status=Status.OK, last_detail=obs.detail, since=now_iso, recent=recent),
                None,
            )
        # Already OK: just refresh the detail so /status shows the latest reading.
        return (
            replace(prev, last_detail=obs.detail, since=prev.since or now_iso, recent=recent),
            None,
        )

    # obs.ok is False from here down.
    if prev.status is Status.OK or prev.status is Status.PENDING:
        fails = prev.consecutive_fails + 1 if prev.status is Status.PENDING else 1
        flapping = _is_flapping(recent, flap_threshold=flap_threshold)
        if fails >= pending_threshold or flapping:
            reason = obs.detail if fails >= pending_threshold else f"flapping — {obs.detail}"
            return (
                CheckState(
                    status=Status.FIRING,
                    consecutive_fails=fails,
                    last_detail=reason,
                    since=now_iso,
                    last_notified_at=now_iso,
                    recent=recent,
                ),
                Notification(check="", kind="firing", detail=reason),
            )
        return (
            CheckState(
                status=Status.PENDING,
                consecutive_fails=fails,
                last_detail=obs.detail,
                since=prev.since if prev.status is Status.PENDING else now_iso,
                recent=recent,
            ),
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
                recent=recent,
            ),
            Notification(check="", kind="reminder", detail=obs.detail),
        )
    return (
        replace(prev, consecutive_passes=0, last_detail=obs.detail, recent=recent),
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
    flap_window: int = DEFAULT_FLAP_WINDOW,
    flap_threshold: int = DEFAULT_FLAP_THRESHOLD,
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
            flap_window=flap_window,
            flap_threshold=flap_threshold,
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
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("ops_monitor.state.unreadable", path=str(path), error=str(exc)[:200])
        return MonitorState()
    if not isinstance(raw, Mapping):
        log.warning("ops_monitor.state.not_an_object", path=str(path), type=type(raw).__name__)
        return MonitorState()
    return MonitorState.from_dict(raw)
