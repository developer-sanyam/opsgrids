"""`delivery.plan_delivery` (round 3): the pure notification-delivery planner that replaced
`main._deliver`'s own inline rate-cap/merge logic after that logic failed review twice.
`main._deliver`'s own integration with a real (fake) mailer, retry counting and
`MonitorRuntime` bookkeeping is covered in `test_ops_monitor_main.py`; everything here is pure
and synchronous — no asyncio, no I/O, just `plan_delivery` and its two small siblings.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from platform_core.ops_monitor.delivery import (
    EXEMPT_CEILING_MULTIPLIER,
    MAX_PENDING_NOTIFICATIONS,
    SentRecord,
    bounded_requeue,
    is_firing_like,
    plan_delivery,
)
from platform_core.ops_monitor.state import Notification

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _at_cap(cap: int = 12, *, exempt: int = 0) -> list[SentRecord]:
    return [SentRecord(at=NOW, exempt=False) for _ in range(cap)] + [
        SentRecord(at=NOW, exempt=True) for _ in range(exempt)
    ]


class TestExemption:
    """Rule 2 (round 3): exempt = a `firing` for a check not reported firing in the last hour,
    or the daily digest. Anything else — including a *repeat* firing — is ordinary."""

    def test_a_fresh_firing_with_no_history_is_exempt_and_flushes_at_cap(self) -> None:
        plan = plan_delivery(
            [],
            [Notification(check="cert", kind="firing", detail="expiring")],
            NOW,
            _at_cap(12),
            {},
            cap=12,
        )
        assert plan.to_send == [Notification(check="cert", kind="firing", detail="expiring")]
        assert plan.new_pending == []
        assert plan.exempt_send is True

    def test_a_firing_within_the_hour_of_its_last_report_is_not_exempt(self) -> None:
        """The bug `probe_m3.py`'s scenario A/C exposed: the pre-round-3 code exempted every
        `kind == "firing"` regardless of history, so a check re-firing minutes after its first
        report got a second free pass through the cap — indistinguishable from a check nobody
        had heard about."""
        last_fired_at = {"api.x": NOW - timedelta(minutes=5)}
        plan = plan_delivery(
            [],
            [Notification(check="api.x", kind="firing", detail="down")],
            NOW,
            _at_cap(12),
            last_fired_at,
            cap=12,
        )
        assert plan.to_send == []
        assert plan.new_pending == [Notification(check="api.x", kind="firing", detail="down")]

    def test_a_firing_more_than_an_hour_after_its_last_report_is_exempt_again(self) -> None:
        last_fired_at = {"api.x": NOW - timedelta(hours=1, minutes=1)}
        plan = plan_delivery(
            [],
            [Notification(check="api.x", kind="firing", detail="down")],
            NOW,
            _at_cap(12),
            last_fired_at,
            cap=12,
        )
        assert plan.to_send == [Notification(check="api.x", kind="firing", detail="down")]
        assert plan.exempt_send is True

    def test_a_reminder_with_no_prior_successful_notify_is_treated_as_firing(self) -> None:
        """Rule 4: nobody has ever actually been told this check is a problem yet, so a
        reminder for it must not be capped as if they had."""
        plan = plan_delivery(
            [],
            [Notification(check="disk", kind="reminder", detail="still down")],
            NOW,
            _at_cap(12),
            {},
            cap=12,
        )
        assert plan.to_send == [Notification(check="disk", kind="reminder", detail="still down")]
        assert plan.exempt_send is True

    def test_a_reminder_for_a_check_already_reported_this_hour_is_ordinary(self) -> None:
        last_fired_at = {"disk": NOW - timedelta(minutes=10)}
        plan = plan_delivery(
            [],
            [Notification(check="disk", kind="reminder", detail="still down")],
            NOW,
            _at_cap(12),
            last_fired_at,
            cap=12,
        )
        assert plan.to_send == []
        assert plan.new_pending == [
            Notification(check="disk", kind="reminder", detail="still down")
        ]

    def test_the_digest_is_always_exempt(self) -> None:
        plan = plan_delivery(
            [],
            [Notification(check="*", kind="digest", detail="all quiet")],
            NOW,
            _at_cap(12),
            {},
            cap=12,
        )
        assert plan.to_send == [Notification(check="*", kind="digest", detail="all quiet")]

    def test_an_ordinary_resolved_with_no_undelivered_predecessor_is_not_exempt(self) -> None:
        plan = plan_delivery(
            [],
            [Notification(check="disk", kind="resolved", detail="back to normal")],
            NOW,
            _at_cap(12),
            {"disk": NOW - timedelta(minutes=30)},
            cap=12,
        )
        assert plan.to_send == []
        assert plan.new_pending == [
            Notification(check="disk", kind="resolved", detail="back to normal")
        ]

    def test_under_the_cap_everything_flushes_together_regardless_of_exemption(self) -> None:
        plan = plan_delivery(
            [],
            [
                Notification(check="disk", kind="reminder", detail="d"),
                Notification(check="cert", kind="firing", detail="c"),
            ],
            NOW,
            [],  # nothing sent yet -- nowhere near any cap
            {"disk": NOW - timedelta(minutes=1)},
            cap=12,
        )
        assert {n.check for n in plan.to_send} == {"disk", "cert"}
        assert plan.exempt_send is False  # not gated by exemption -- just under the cap


class TestExemptCeiling:
    """Rule 2's second half: exemption is not exemption from every limit."""

    def test_the_exempt_ceiling_blocks_a_fresh_firing_once_hit(self) -> None:
        plan = plan_delivery(
            [],
            [Notification(check="cert", kind="firing", detail="c")],
            NOW,
            _at_cap(12, exempt=24),
            {},
            cap=12,
        )
        assert plan.to_send == []
        assert plan.new_pending == [Notification(check="cert", kind="firing", detail="c")]

    def test_the_exempt_ceiling_is_independent_of_the_ordinary_cap(self) -> None:
        """Nowhere near the ordinary cap, but the exempt ceiling itself is spent -- a fresh
        firing still flushes, because it is no longer being sent *as* an exempt bypass; it is
        just an ordinary flush that happens to include an exempt-eligible item."""
        plan = plan_delivery(
            [],
            [Notification(check="cert", kind="firing", detail="c")],
            NOW,
            _at_cap(0, exempt=24),
            {},
            cap=12,
        )
        assert plan.to_send == [Notification(check="cert", kind="firing", detail="c")]
        assert plan.exempt_send is False


class TestMergeOrder:
    """Rule 3: merge by check name, newer wins — with two refinements so that a bare
    replacement does not lose information (module docstring, "Merge order")."""

    def test_a_queued_resolved_is_dropped_when_the_check_fires_again(self) -> None:
        pending = [Notification(check="api.x", kind="resolved", detail="was up")]
        new = [Notification(check="api.x", kind="firing", detail="down again")]
        plan = plan_delivery(pending, new, NOW, [], {}, cap=12)  # under cap -- flush
        assert plan.to_send == [Notification(check="api.x", kind="firing", detail="down again")]

    def test_a_queued_firing_is_dropped_for_a_resolved_that_is_itself_delivered(self) -> None:
        pending = [Notification(check="api.x", kind="firing", detail="down")]
        new = [Notification(check="api.x", kind="resolved", detail="up")]
        plan = plan_delivery(pending, new, NOW, [], {}, cap=12)  # under cap -- flush
        assert [(n.check, n.kind) for n in plan.to_send] == [("api.x", "resolved")]
        assert plan.new_pending == []

    def test_a_resolved_that_replaces_a_never_emailed_firing_says_so(self) -> None:
        """Finding 6 (post-review fixup): the operator's first and only word about this
        incident would otherwise be a bare "back to normal"."""
        pending = [Notification(check="api.x", kind="firing", detail="HTTP 503")]
        new = [Notification(check="api.x", kind="resolved", detail="HTTP 200 ok")]
        plan = plan_delivery(pending, new, NOW, [], {}, cap=12)
        (resolved,) = plan.to_send
        assert resolved.kind == "resolved"
        assert "HTTP 200 ok" in resolved.detail
        assert "HTTP 503" in resolved.detail, "the undelivered firing's own detail is carried"
        assert "never emailed" in resolved.detail
        assert "rate cap" in resolved.detail

    def test_a_resolved_replacing_a_reminder_for_an_already_reported_check_is_left_alone(
        self,
    ) -> None:
        """The incident *was* emailed (that is what `last_fired_at` says) — only a reminder was
        held back — so "never emailed" would be false."""
        pending = [Notification(check="disk", kind="reminder", detail="still down")]
        new = [Notification(check="disk", kind="resolved", detail="back")]
        plan = plan_delivery(pending, new, NOW, [], {"disk": NOW - timedelta(hours=3)}, cap=12)
        assert plan.to_send == [Notification(check="disk", kind="resolved", detail="back")]

    def test_a_resolved_replacing_a_reminder_for_a_never_reported_check_says_so(self) -> None:
        pending = [Notification(check="disk", kind="reminder", detail="still down")]
        new = [Notification(check="disk", kind="resolved", detail="back")]
        plan = plan_delivery(pending, new, NOW, [], {}, cap=12)
        assert "never emailed" in plan.to_send[0].detail

    def test_closing_an_undelivered_incident_is_exempt_even_at_cap(self) -> None:
        """The queued `firing` was never delivered, and the check was not reported firing in the
        last hour -- if the `resolved` that replaces it were just an ordinary capped item, the
        check could end up reported as neither having opened nor having resolved. Closing that
        loop is prioritised instead."""
        pending = [Notification(check="api.x", kind="firing", detail="down")]
        new = [Notification(check="api.x", kind="resolved", detail="up")]
        plan = plan_delivery(pending, new, NOW, _at_cap(12), {}, cap=12)
        assert [(n.check, n.kind) for n in plan.to_send] == [("api.x", "resolved")]
        assert plan.exempt_send is True

    def test_closing_an_incident_last_reported_over_an_hour_ago_is_still_exempt(self) -> None:
        pending = [Notification(check="api.x", kind="firing", detail="down")]
        new = [Notification(check="api.x", kind="resolved", detail="up")]
        last_fired_at = {"api.x": NOW - timedelta(hours=2)}
        plan = plan_delivery(pending, new, NOW, _at_cap(12), last_fired_at, cap=12)
        assert [(n.check, n.kind) for n in plan.to_send] == [("api.x", "resolved")]
        assert plan.exempt_send is True

    def test_a_flappers_closing_notice_is_throttled_like_everything_else(self) -> None:
        """Finding 1 (post-review fixup, the MAJOR): the check was reported firing 10 minutes
        ago, so this is a flapper. Its `resolved` -- which replaces a queued re-firing -- was
        treated as "closing an unreported incident" and therefore exempt: one flapper at cap 12
        sent 16 emails an hour, 12 of them back-to-back RESOLVED with no OPENED, and a few
        out of phase spent the whole exempt ceiling ahead of a brand-new check's first alert."""
        pending = [Notification(check="X", kind="firing", detail="down")]
        new = [Notification(check="X", kind="resolved", detail="up")]
        plan = plan_delivery(
            pending, new, NOW, _at_cap(12), {"X": NOW - timedelta(minutes=10)}, cap=12
        )
        assert plan.to_send == []
        (queued,) = plan.new_pending
        assert (queued.check, queued.kind) == ("X", "resolved")
        assert plan.exempt_send is False

    def test_an_untouched_pending_entry_carries_forward_unchanged(self) -> None:
        pending = [Notification(check="disk", kind="reminder", detail="d")]
        plan = plan_delivery(
            pending, [], NOW, _at_cap(12), {"disk": NOW - timedelta(minutes=1)}, cap=12
        )
        assert plan.to_send == []
        assert plan.new_pending == pending

    def test_a_reminder_replacing_a_queued_firing_stays_an_opening(self) -> None:
        """Finding 2 (post-review fixup): a first notification that was capped or failed to send
        comes back next cycle as a *reminder* (its bookkeeping was reverted, so the reminder
        clock reads it as due). Replacing the queued `firing` with it must not turn the
        operator's first word about the incident into "STILL FIRING"."""
        pending = [Notification(check="disk", kind="firing", detail="down")]
        new = [Notification(check="disk", kind="reminder", detail="still down (newer)")]
        plan = plan_delivery(pending, new, NOW, [], {}, cap=12)
        assert plan.to_send == [
            Notification(check="disk", kind="firing", detail="still down (newer)")
        ]

    def test_a_reminder_replacing_a_queued_reminder_stays_a_reminder(self) -> None:
        pending = [Notification(check="disk", kind="reminder", detail="old")]
        new = [Notification(check="disk", kind="reminder", detail="new")]
        plan = plan_delivery(pending, new, NOW, [], {"disk": NOW - timedelta(hours=2)}, cap=12)
        assert plan.to_send == [Notification(check="disk", kind="reminder", detail="new")]


class TestClockStepsBackwards:
    """Finding 3 (post-review fixup): both rolling-hour windows require `0 <= now - at < 1h`."""

    def test_sends_dated_in_the_future_do_not_hold_the_cap_shut(self) -> None:
        future = NOW + timedelta(days=1)
        history = [SentRecord(at=future, exempt=False) for _ in range(36)]
        plan = plan_delivery(
            [], [Notification(check="cert", kind="firing", detail="c")], NOW, history, {}, cap=12
        )
        assert plan.to_send == [Notification(check="cert", kind="firing", detail="c")]

    def test_a_future_dated_exempt_record_does_not_count_toward_the_ceiling(self) -> None:
        future = NOW + timedelta(days=1)
        history = _at_cap(12) + [SentRecord(at=future, exempt=True) for _ in range(24)]
        plan = plan_delivery(
            [], [Notification(check="cert", kind="firing", detail="c")], NOW, history, {}, cap=12
        )
        assert plan.to_send == [Notification(check="cert", kind="firing", detail="c")]
        assert plan.exempt_send is True

    def test_a_future_dated_last_report_does_not_make_a_check_look_recently_reported(self) -> None:
        plan = plan_delivery(
            [],
            [Notification(check="cert", kind="firing", detail="c")],
            NOW,
            _at_cap(12),
            {"cert": NOW + timedelta(days=1)},
            cap=12,
        )
        assert plan.to_send == [Notification(check="cert", kind="firing", detail="c")]


class TestPendingBound:
    def test_the_oldest_entries_are_dropped_past_the_bound(self) -> None:
        pending = [
            Notification(check=f"c{i}", kind="reminder", detail="x")
            for i in range(MAX_PENDING_NOTIFICATIONS + 5)
        ]
        last_fired_at = {n.check: NOW for n in pending}  # all reported recently -- none exempt
        plan = plan_delivery(pending, [], NOW, _at_cap(12), last_fired_at, cap=12)
        assert len(plan.new_pending) == MAX_PENDING_NOTIFICATIONS
        # The oldest (c0..c4) are the ones dropped; the newest (last MAX) survive.
        assert [n.check for n in plan.new_pending] == [
            f"c{i}" for i in range(5, MAX_PENDING_NOTIFICATIONS + 5)
        ]


class TestIsFiringLike:
    def test_a_firing_notification_is_always_firing_like(self) -> None:
        assert is_firing_like(Notification(check="a", kind="firing", detail=""), {}) is True

    def test_a_reminder_is_firing_like_only_with_no_prior_notify(self) -> None:
        assert is_firing_like(Notification(check="a", kind="reminder", detail=""), {}) is True
        assert (
            is_firing_like(Notification(check="a", kind="reminder", detail=""), {"a": NOW}) is False
        )

    def test_resolved_and_digest_are_never_firing_like(self) -> None:
        assert is_firing_like(Notification(check="a", kind="resolved", detail=""), {}) is False
        assert is_firing_like(Notification(check="*", kind="digest", detail=""), {}) is False


class TestBoundedRequeue:
    def test_concatenates_and_bounds(self) -> None:
        to_send = [Notification(check=f"s{i}", kind="reminder", detail="") for i in range(3)]
        pending = [Notification(check=f"p{i}", kind="reminder", detail="") for i in range(3)]
        assert bounded_requeue(to_send, pending) == [*to_send, *pending]

    def test_drops_the_oldest_past_the_bound(self) -> None:
        to_send = [Notification(check=f"s{i}", kind="reminder", detail="") for i in range(3)]
        pending = [
            Notification(check=f"p{i}", kind="reminder", detail="")
            for i in range(MAX_PENDING_NOTIFICATIONS)
        ]
        result = bounded_requeue(to_send, pending)
        assert len(result) == MAX_PENDING_NOTIFICATIONS
        # `to_send` is placed first (it was older -- planned before this cycle's `pending`
        # carry-forward existed as such) so it is `to_send` that is dropped first.
        assert result[0].check == "p0"
        assert all(n.check.startswith("p") for n in result)


# --------------------------------------------------------------------------------------------
# Property test (round 3's own requirement): random sequences of check open/resolve/reminder,
# a random cap, and a random SMTP-success pattern, replayed against `plan_delivery` the same
# way `main._deliver` applies its output (duplicated here in miniature — `_replay_cycle` — to
# keep this fast and free of asyncio/Mailer/MonitorRuntime; that wiring is covered end to end
# in `test_ops_monitor_main.py`'s `TestProbeScenarios`).
# --------------------------------------------------------------------------------------------

_CHECKS = ("a", "b", "c")
_ACTIONS = ("open", "resolve", "reminder", "noop")


def _replay_cycle(
    pending: list[Notification],
    sent_history: list[SentRecord],
    last_fired_at: dict[str, datetime],
    new: list[Notification],
    now: datetime,
    cap: int,
    delivered: bool,
) -> tuple[list[Notification], list[SentRecord], dict[str, datetime], list[Notification]]:
    """Exactly what `main._deliver` does with one `plan_delivery` result -- see its own
    docstring. Returns `(new_pending, new_sent_history, new_last_fired_at, actually_sent)`."""
    plan = plan_delivery(pending, new, now, sent_history, last_fired_at, cap)
    if plan.exempt_send:
        # Invariant 6 (post-review fixup, finding 1): nothing that goes out through the exempt
        # path, past a full ordinary cap, may be about a check reported firing in the last hour
        # -- that is a flapper (or a very recent incident), and its every notice, `resolved`
        # included, counts against the cap. The digest is the one exempt kind with no check.
        for n in plan.to_send:
            fired_at = last_fired_at.get(n.check)
            assert n.kind == "digest" or fired_at is None or now - fired_at >= timedelta(hours=1), (
                f"{n.check} ({n.kind}) went out through the exempt path although it was "
                f"reported firing at {fired_at}, within the last hour of {now}"
            )
    if not plan.to_send:
        return plan.new_pending, sent_history, last_fired_at, []
    if delivered:
        pruned = [r for r in sent_history if now - r.at < timedelta(hours=1)]
        new_sent_history = [*pruned, SentRecord(at=now, exempt=plan.exempt_send)]
        new_last_fired_at = dict(last_fired_at)
        for n in plan.to_send:
            if is_firing_like(n, last_fired_at):
                new_last_fired_at[n.check] = now
        return plan.new_pending, new_sent_history, new_last_fired_at, plan.to_send
    return bounded_requeue(plan.to_send, plan.new_pending), sent_history, last_fired_at, []


@st.composite
def _cycle_plans(draw: st.DrawFn) -> tuple[int, list[tuple[str, str, bool]]]:
    cap = draw(st.integers(min_value=1, max_value=5))
    n_cycles = draw(st.integers(min_value=5, max_value=40))
    actions = draw(
        st.lists(
            st.tuples(
                st.sampled_from(_CHECKS),
                st.sampled_from(_ACTIONS),
                st.booleans(),
            ),
            min_size=n_cycles,
            max_size=n_cycles,
        )
    )
    return cap, actions


@hyp_settings(max_examples=500, deadline=None)
@given(_cycle_plans())
def test_delivery_invariants_hold_under_random_check_sequences(
    plan: tuple[int, list[tuple[str, str, bool]]],
) -> None:
    """The four invariants round 3 asked for, replayed over a random walk of up to 40 cycles
    across three checks, a random cap (1-5), and a random SMTP-success pattern -- >=500
    examples (hypothesis' default backend re-runs on failure with a shrunk, reported seed)."""
    cap, actions = plan
    now = NOW
    pending: list[Notification] = []
    sent_history: list[SentRecord] = []
    last_fired_at: dict[str, datetime] = {}
    true_state = dict.fromkeys(_CHECKS, "ok")
    ever_fired = dict.fromkeys(_CHECKS, False)
    last_told_kind: dict[str, str] = {}

    def _exempt_sent_in_the_last_hour() -> int:
        return sum(1 for r in sent_history if now - r.at < timedelta(hours=1) and r.exempt)

    def _told(sent: list[Notification]) -> None:
        for n in sent:
            # Invariant 5 (post-review fixup, finding 6): a `resolved` about a check whose
            # opening was never emailed must say so, carrying the opening's own detail --
            # otherwise a bare "back to normal" is the operator's only word about the incident.
            if n.kind == "resolved" and last_told_kind.get(n.check) not in ("firing", "reminder"):
                assert "never emailed" in n.detail and "down" in n.detail, (
                    f"{n.check}: RESOLVED emailed with no OPENED before it and no note saying "
                    f"the opening was never sent: {n.detail!r}"
                )
            last_told_kind[n.check] = n.kind

    for check, action, delivered in actions:
        new: list[Notification] = []
        if action == "open" and true_state[check] == "ok":
            true_state[check] = "firing"
            ever_fired[check] = True
            new.append(Notification(check=check, kind="firing", detail="down"))
        elif action == "resolve" and true_state[check] == "firing":
            true_state[check] = "ok"
            new.append(Notification(check=check, kind="resolved", detail="up"))
        elif action == "reminder" and true_state[check] == "firing":
            new.append(Notification(check=check, kind="reminder", detail="still down"))

        # Invariant 4: every OPENED is emailed within one cycle unless the exempt ceiling is
        # hit -- for a check that has never *successfully* been reported firing before
        # (`last_fired_at` has no entry for it). A check that fired, resolved and reopened
        # again within the hour of that last report is legitimately throttled by the
        # *ordinary* cap instead (rule 2: "any other firing counts against the cap") -- that
        # is what `TestExemption.test_a_firing_within_the_hour_of_its_last_report_is_not_
        # exempt` covers directly; this invariant is only about a check nobody has ever heard
        # about yet.
        opened_this_cycle = next((n for n in new if n.kind == "firing"), None)
        never_reported_before = opened_this_cycle is not None and check not in last_fired_at
        ceiling_was_full = _exempt_sent_in_the_last_hour() >= EXEMPT_CEILING_MULTIPLIER * cap

        pending, sent_history, last_fired_at, sent = _replay_cycle(
            pending, sent_history, last_fired_at, new, now, cap, delivered
        )
        _told(sent)

        if never_reported_before and delivered:
            assert opened_this_cycle in sent or ceiling_was_full, (
                "a check nobody has ever been told about was neither emailed this cycle nor "
                "blocked by a full exempt ceiling"
            )

        # Invariant 3: no RESOLVED emailed while that check is firing right now.
        for n in sent:
            if n.kind == "resolved":
                assert true_state[n.check] != "firing", "false all-clear sent while firing"

        # Invariant 2: emails per rolling hour <= cap + exempt ceiling.
        recent = [r for r in sent_history if now - r.at < timedelta(hours=1)]
        assert len(recent) <= cap + EXEMPT_CEILING_MULTIPLIER * cap

        now += timedelta(minutes=1)

    # Drain: enough idle cycles (comfortably more than one hour) for the cap to free and
    # whatever is left in the queue to flush.
    for _ in range(75):
        if not pending:
            break
        pending, sent_history, last_fired_at, sent = _replay_cycle(
            pending, sent_history, last_fired_at, [], now, cap, delivered=True
        )
        _told(sent)
        for n in sent:
            # Invariant 3 holds while draining too: a stale queued `resolved` must never be
            # what finally gets flushed for a check that is firing again.
            if n.kind == "resolved":
                assert true_state[n.check] != "firing", "false all-clear sent while firing"
        now += timedelta(minutes=1)

    # Invariant 1: the last real state change of every check is eventually emailed once below
    # the cap.
    for check in _CHECKS:
        if not ever_fired[check]:
            continue
        if true_state[check] == "firing":
            assert last_told_kind.get(check) in ("firing", "reminder"), (
                f"{check} is firing but was never told about after draining"
            )
        else:
            assert last_told_kind.get(check) == "resolved", (
                f"{check} resolved but was never told about after draining"
            )
