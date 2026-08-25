"""The dunning ladder as arithmetic (ADR-0005 §1, §3). No database, no clock, no I/O.

`domain.py` is where the ladder lives precisely so these properties can be pinned exactly rather
than inferred from a sequence of database states. Three of them are load-bearing:

* **Every due moment is anchored to the stored `opened_at`.** Not to `now`, not to the previous
  attempt. A sweep that runs late must land on the schedule it would have had if it had run on
  time — otherwise fourteen days of grace drift into twenty, one late pass at a time, with nothing
  in the logs to show it.
* **The window dominates the ladder.** Past `grace_ends_at` the answer is `suspend` whatever the
  ladder has left; a spent ladder inside an open window is `wait`, not an early suspension.
* **A retry key is per attempt.** Razorpay has no idempotency key of its own (ADR-0004 §6), so
  ours is the only one — and a retry that reused the failed charge's key would be deduplicated
  into the charge that already failed and debit nobody.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app import domain

T0 = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)


class TestThePolicyIsWellFormed:
    def test_the_shipped_ladder_is_three_retries_inside_a_fourteen_day_window(self) -> None:
        """The numbers ADR-0005 fixes, asserted so a casual edit has to argue with a test."""
        assert domain.POLICY.retry_offsets == (
            timedelta(days=3),
            timedelta(days=7),
            timedelta(days=12),
        )
        assert domain.POLICY.grace == timedelta(days=14)
        assert domain.POLICY.max_attempts == 3

    def test_the_last_retry_falls_inside_the_window(self) -> None:
        """The 48-hour gap that makes the final notice honest (ADR-0005 §3).

        If the last attempt landed on or after the boundary, the platform would suspend before
        knowing whether the last debit worked — and "your access ends on Thursday" would be a
        sentence it could not truthfully send.
        """
        assert domain.POLICY.retry_offsets[-1] < domain.POLICY.grace
        gap = domain.POLICY.grace - domain.POLICY.retry_offsets[-1]
        assert gap >= timedelta(days=1), "no room left to tell the customer before access stops"

    @pytest.mark.parametrize(
        ("offsets", "grace"),
        [
            pytest.param((), timedelta(days=14), id="no-retries-is-an-immediate-suspension"),
            pytest.param((timedelta(0),), timedelta(days=14), id="a-retry-at-the-failure-itself"),
            pytest.param(
                (timedelta(days=-1),), timedelta(days=14), id="a-retry-before-the-failure"
            ),
            pytest.param(
                (timedelta(days=7), timedelta(days=3)),
                timedelta(days=14),
                id="offsets-out-of-order",
            ),
            pytest.param(
                (timedelta(days=3), timedelta(days=3)),
                timedelta(days=14),
                id="two-retries-at-the-same-moment",
            ),
            pytest.param(
                (timedelta(days=3), timedelta(days=20)),
                timedelta(days=14),
                id="a-retry-outside-the-window",
            ),
        ],
    )
    def test_an_unexecutable_ladder_is_refused_at_construction(
        self, offsets: tuple[timedelta, ...], grace: timedelta
    ) -> None:
        """Each of these produces a ladder that cannot be run — a debit that never fires, a
        suspension before its own last attempt, a duplicate presentation and its bank fee. They
        fail where the policy is written, not on the day a customer is dunned by one."""
        with pytest.raises(domain.InvalidPolicy):
            domain.DunningPolicy(retry_offsets=offsets, grace=grace)


class TestTheScheduleIsAnchoredToTheStoredOpening:
    def test_every_rung_is_opened_at_plus_its_offset(self) -> None:
        assert domain.attempt_due_at(T0, 1) == T0 + timedelta(days=3)
        assert domain.attempt_due_at(T0, 2) == T0 + timedelta(days=7)
        assert domain.attempt_due_at(T0, 3) == T0 + timedelta(days=12)
        assert domain.grace_ends_at(T0) == T0 + timedelta(days=14)

    def test_past_the_end_of_the_ladder_there_is_no_rung(self) -> None:
        assert domain.attempt_due_at(T0, 4) is None
        assert domain.attempt_due_at(T0, 0) is None

    def test_a_late_sweep_does_not_push_the_remaining_ladder_out(self) -> None:
        """The drift property, stated as arithmetic.

        Attempt 1 was due at T+3d and the sweep only noticed at T+3d+11h. The *next* thing to
        happen is still T+7d from the opening — not seven days from when the sweep got round to
        it. Anywhere this were computed as `now + interval` the answer would be T+10d+11h, and
        every late pass would compound.
        """
        noticed_late = T0 + timedelta(days=3, hours=11)
        window = domain.grace_ends_at(T0)

        after_first = domain.next_action_at(T0, 1, window)

        assert after_first == T0 + timedelta(days=7)
        assert after_first != noticed_late + timedelta(days=4)

    def test_the_step_after_the_last_retry_is_the_close_of_the_window(self) -> None:
        window = domain.grace_ends_at(T0)
        assert domain.next_action_at(T0, 3, window) == window

    def test_no_rung_is_ever_scheduled_past_the_window(self) -> None:
        """A window shorter than the ladder is possible — an operator can shorten one on an open
        cycle. A retry after entitlement has stopped is one nobody is waiting for, so the window
        wins and the next action is the suspension."""
        short_window = T0 + timedelta(days=5)
        assert domain.next_action_at(T0, 1, short_window) == short_window


class TestWhatTheSweepShouldDo:
    def _action(self, at: datetime, attempts: int, window: datetime | None = None) -> str:
        return domain.next_dunning_action(
            opened_at=T0,
            window_ends_at=window or domain.grace_ends_at(T0),
            attempts_made=attempts,
            now=at,
        )

    @pytest.mark.parametrize(
        ("elapsed", "attempts", "expected"),
        [
            pytest.param(timedelta(hours=1), 0, domain.ACTION_WAIT, id="just-failed"),
            pytest.param(timedelta(days=2, hours=23), 0, domain.ACTION_WAIT, id="an-hour-early"),
            pytest.param(timedelta(days=3), 0, domain.ACTION_RETRY, id="retry-1-due"),
            pytest.param(timedelta(days=4), 1, domain.ACTION_WAIT, id="between-1-and-2"),
            pytest.param(timedelta(days=7), 1, domain.ACTION_RETRY, id="retry-2-due"),
            pytest.param(timedelta(days=12), 2, domain.ACTION_RETRY, id="retry-3-due"),
            pytest.param(timedelta(days=13), 3, domain.ACTION_WAIT, id="ladder-spent-window-open"),
            pytest.param(timedelta(days=14), 3, domain.ACTION_SUSPEND, id="window-closed"),
            pytest.param(timedelta(days=90), 3, domain.ACTION_SUSPEND, id="long-past-the-window"),
        ],
    )
    def test_the_ladder_from_the_failure_to_the_switch(
        self, elapsed: timedelta, attempts: int, expected: str
    ) -> None:
        assert self._action(T0 + elapsed, attempts) == expected

    def test_a_spent_ladder_inside_an_open_window_waits_rather_than_suspending_early(
        self,
    ) -> None:
        """The deliberate quiet gap (ADR-0005 §3). Three attempts have failed by T+12d and the
        window does not close until T+14d; those two days are the customer's last chance to pay by
        hand, and the notice that says so is only truthful because the platform waits."""
        assert self._action(T0 + timedelta(days=12, minutes=1), 3) == domain.ACTION_WAIT
        assert self._action(T0 + timedelta(days=13, hours=23), 3) == domain.ACTION_WAIT
        assert self._action(T0 + timedelta(days=14), 3) == domain.ACTION_SUSPEND

    def test_a_closed_window_suspends_even_with_ladder_left(self) -> None:
        """The asymmetry is on purpose: the window is the promise made to the customer, the ladder
        is only how the platform spends it. An operator who shortens a window means it."""
        short_window = T0 + timedelta(days=1)
        assert self._action(T0 + timedelta(days=1), 0, window=short_window) == (
            domain.ACTION_SUSPEND
        )

    def test_a_compressed_policy_walks_the_same_ladder(self) -> None:
        """The policy is a value, so a test can run the real decision function over a ladder
        measured in seconds — which is how the integration tests exercise fourteen days of
        behaviour without sleeping or patching a clock."""
        fast = domain.DunningPolicy(
            retry_offsets=(timedelta(seconds=1), timedelta(seconds=2)), grace=timedelta(seconds=3)
        )
        window = domain.grace_ends_at(T0, fast)
        seen = [
            domain.next_dunning_action(
                opened_at=T0,
                window_ends_at=window,
                attempts_made=attempts,
                now=T0 + timedelta(seconds=second),
                policy=fast,
            )
            for second, attempts in ((0, 0), (1, 0), (2, 1), (3, 2))
        ]
        assert seen == [
            domain.ACTION_WAIT,
            domain.ACTION_RETRY,
            domain.ACTION_RETRY,
            domain.ACTION_SUSPEND,
        ]


class TestTheRetryKey:
    def test_each_attempt_gets_its_own_key(self) -> None:
        """Reusing the failed charge's key would be deduplicated into the charge that already
        failed — a ladder that looks like it is retrying and debits nobody (ADR-0004 §6)."""
        cycle = "0e5f7a3c-1c2d-4a5b-8c9d-0e1f2a3b4c5d"
        keys = {domain.retry_idempotency_key(cycle, n) for n in (1, 2, 3)}
        assert len(keys) == 3

    def test_the_key_is_stable_for_the_same_attempt(self) -> None:
        """Redelivery of one retry request must be one debit, not two."""
        cycle = "0e5f7a3c-1c2d-4a5b-8c9d-0e1f2a3b4c5d"
        assert domain.retry_idempotency_key(cycle, 2) == domain.retry_idempotency_key(cycle, 2)

    def test_two_cycles_never_collide(self) -> None:
        """A customer who recovers, then fails again next month, gets a second cycle — and its
        first attempt must not be deduplicated into the first cycle's."""
        first = domain.retry_idempotency_key("cycle-a", 1)
        second = domain.retry_idempotency_key("cycle-b", 1)
        assert first != second
