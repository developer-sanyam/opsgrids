"""Pure notification-delivery planning (M3/M6, round 3's restructure).

Two rounds of patching `_deliver`'s own bookkeeping — a plain `list[Notification]` carried
across cycles, merged in place by a mutating helper — is what this module replaces: every
decision about *what to send this cycle* and *what to carry forward* now lives in one pure
function, `plan_delivery`, that `main._deliver` only executes: attempt `plan.to_send`, and on
success commit `plan.new_pending`; on failure, put whatever was in `plan.to_send` back into
the pending queue as the exact same `Notification` objects — same `kind`, same `detail` — never
reconstructed or relabelled.

Terminology this module introduces:

**Exempt.** A notification the per-hour cap (`cap`) never holds back on its own — the daily
digest, always; a `firing` for a check that has not itself been reported firing within the
last hour (`last_fired_at`, tracked per check, updated only once a firing-kind notification is
actually *delivered*, never on a mere attempt); and a `reminder` for a check nobody has ever
*successfully* been told about yet (no entry in `last_fired_at`) — the operator has not
actually heard about the incident, so the cap must not be the reason they never do. A check
that keeps flapping is not exempt on its second, third, ... `firing` inside that hour; from
then on it is throttled exactly like a reminder — the whole point of tracking `last_fired_at`
per check rather than treating every `kind == "firing"` alike.

**The exempt ceiling.** Exemption from the cap is not exemption from every limit: a box with a
hundred different checks opening in the same minute would otherwise send a hundred separate
emails with nothing to stop it. Exempt notifications are themselves capped, separately, at
`EXEMPT_CEILING_MULTIPLIER * cap` sends per rolling hour — generous next to the ordinary cap,
and still finite. Once that ceiling is hit, a new firing queues like anything else — "every
OPENED is emailed within one cycle unless the exempt ceiling is hit" is this module's own
invariant, not an incidental side effect.

**Merge order.** Pending and this cycle's new notifications are merged by check name, and the
newer one — always this cycle's — wins: a queued `resolved` is dropped the instant its check
fires again (never send a false all-clear for a check that is firing right now), and a queued
`firing`/`reminder` is dropped the instant its check resolves. The one exception is closing an
incident nobody was ever told about: a `resolved` that is about to replace a still-undelivered
`firing`/`reminder` is treated as exempt too, so closing that loop is not left to rot in the
queue behind whatever else the cap is holding back this hour — the check would otherwise end
up reported as neither having opened nor having resolved.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from platform_core.ops_monitor.state import Notification

#: How many notification objects `new_pending` carries forward across cycles before the
#: oldest are dropped — a bound on memory, not a design target; a real box's incident count
#: staying anywhere near this would already be its own emergency. In-memory only: this bound,
#: like every field `plan_delivery` reads and writes, is never persisted (`state.py`'s
#: `MonitorState` is the only thing written to disk) — a restart starts the queue empty.
MAX_PENDING_NOTIFICATIONS = 200

#: Exempt notifications get their own ceiling, this many times the ordinary cap, per rolling
#: hour — see the module docstring.
EXEMPT_CEILING_MULTIPLIER = 2

_ONE_HOUR = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class SentRecord:
    """One successful send, for the rolling-hour accounting `plan_delivery` does on every
    call. `exempt=True` means this send happened only because the exempt path let it through
    while the ordinary cap was full — it counts against the exempt ceiling, not the ordinary
    cap. A send that mixed exempt and non-exempt notifications (the ordinary "not at cap,
    flush everything" case) is never `exempt=True`, because it was not gated by exemption at
    all — see `plan_delivery`'s own branches."""

    at: datetime
    exempt: bool


@dataclass(frozen=True, slots=True)
class DeliveryPlan:
    """What `plan_delivery` decided. `to_send` and `new_pending` are the two outputs by name;
    `exempt_send` is bookkeeping `main._deliver` cannot otherwise reconstruct after the fact —
    whether, if `to_send` is delivered, that success should be charged to the exempt ceiling
    instead of the ordinary cap (see `SentRecord.exempt`). `exempt_send` is meaningless when
    `to_send` is empty (nothing was planned to send)."""

    to_send: list[Notification]
    new_pending: list[Notification]
    exempt_send: bool


def _never_fired(check: str, last_fired_at: Mapping[str, datetime]) -> bool:
    return check not in last_fired_at


def _fired_within_the_last_hour(
    check: str, now: datetime, last_fired_at: Mapping[str, datetime]
) -> bool:
    fired_at = last_fired_at.get(check)
    return fired_at is not None and now - fired_at < _ONE_HOUR


def is_firing_like(notification: Notification, last_fired_at: Mapping[str, datetime]) -> bool:
    """Whether `notification` counts as "an opening being reported" for `last_fired_at`
    bookkeeping purposes — a genuine `firing`, or a `reminder` for a check nobody has ever been
    successfully told fired yet (rule 4: "a reminder with no prior successful notify is
    treated as firing"). Exported so `main._deliver` can update `last_fired_at` after an actual
    successful send using the exact same rule `plan_delivery` used to decide exemption."""
    return notification.kind == "firing" or (
        notification.kind == "reminder" and _never_fired(notification.check, last_fired_at)
    )


def _closes_an_unreported_incident(
    notification: Notification, merged_from_pending: Mapping[str, Notification]
) -> bool:
    """True when `notification` is a `resolved` about to replace a still-queued, never-
    delivered `firing`/`reminder` for the same check — see the module docstring's "merge
    order" section for why that closing notice must not simply wait behind the cap like an
    ordinary `resolved` would."""
    if notification.kind != "resolved":
        return False
    predecessor = merged_from_pending.get(notification.check)
    return predecessor is not None and predecessor.kind in ("firing", "reminder")


def _is_exempt(
    notification: Notification,
    *,
    now: datetime,
    last_fired_at: Mapping[str, datetime],
    merged_from_pending: Mapping[str, Notification],
) -> bool:
    if notification.kind == "digest":
        return True
    if is_firing_like(notification, last_fired_at):
        return not _fired_within_the_last_hour(notification.check, now, last_fired_at)
    return _closes_an_unreported_incident(notification, merged_from_pending)


def plan_delivery(
    pending: Sequence[Notification],
    new: Sequence[Notification],
    now: datetime,
    sent_history: Sequence[SentRecord],
    last_fired_at: Mapping[str, datetime],
    cap: int,
) -> DeliveryPlan:
    """Decide what to send this cycle and what to carry forward — pure, no I/O, no mutation of
    any argument. `main._deliver` is the only thing that ever calls `Mailer.send` or writes any
    of `MonitorRuntime`'s fields; this function only plans.

    `pending` and `new` are merged by check name (`new` always wins — see the module
    docstring), split into `exempt`/capped, and then gated by two independent rolling-hour
    counters read from `sent_history`: the ordinary `cap` for everything not exempt, and
    `EXEMPT_CEILING_MULTIPLIER * cap` for everything that is. Three outcomes:

    - Under the ordinary cap: flush everything, exempt and capped alike, in one send.
    - At the ordinary cap, but under the exempt ceiling, with at least one exempt item: send
      only the exempt items; the rest queue.
    - At the ordinary cap with nothing exempt to send, or the exempt ceiling also hit: send
      nothing; everything queues.
    """
    merged_from_pending: dict[str, Notification] = {n.check: n for n in pending}
    merged: dict[str, Notification] = dict(merged_from_pending)
    for notification in new:
        merged[notification.check] = notification

    recent = [record for record in sent_history if now - record.at < _ONE_HOUR]
    regular_sent = sum(1 for record in recent if not record.exempt)
    exempt_sent = sum(1 for record in recent if record.exempt)

    exempt_items: list[Notification] = []
    capped_items: list[Notification] = []
    for notification in merged.values():
        if _is_exempt(
            notification,
            now=now,
            last_fired_at=last_fired_at,
            merged_from_pending=merged_from_pending,
        ):
            exempt_items.append(notification)
        else:
            capped_items.append(notification)

    at_cap = regular_sent >= cap
    exempt_at_ceiling = exempt_sent >= EXEMPT_CEILING_MULTIPLIER * cap

    to_send: list[Notification]
    new_pending: list[Notification]
    exempt_send: bool
    if not at_cap:
        to_send = list(merged.values())
        new_pending = []
        exempt_send = False
    elif exempt_items and not exempt_at_ceiling:
        to_send = exempt_items
        new_pending = capped_items
        exempt_send = True
    else:
        to_send = []
        new_pending = list(merged.values())
        exempt_send = False

    if len(new_pending) > MAX_PENDING_NOTIFICATIONS:
        new_pending = new_pending[-MAX_PENDING_NOTIFICATIONS:]

    return DeliveryPlan(to_send=to_send, new_pending=new_pending, exempt_send=exempt_send)


def bounded_requeue(
    to_send: Sequence[Notification], new_pending: Sequence[Notification]
) -> list[Notification]:
    """`main._deliver`'s own helper for a failed send: everything that was planned to go out
    this cycle goes back into the queue, as the exact same objects (original `kind` intact),
    alongside whatever `plan_delivery` had already decided to hold back — bounded the same way
    `plan_delivery` itself bounds `new_pending`."""
    combined = [*to_send, *new_pending]
    if len(combined) > MAX_PENDING_NOTIFICATIONS:
        combined = combined[-MAX_PENDING_NOTIFICATIONS:]
    return combined
