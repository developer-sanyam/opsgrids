"""`app.domain.PICK_TASK_TRANSITIONS` against ADR-0025 §2's table, verbatim — no I/O, no
session, pure functions, mirroring `test_orders_domain.py`'s own shape for `ORDER_TRANSITIONS`.

`TestAutoAssignment*` covers the auto-assignment engine's own pure functions (ADR-0024 §9):
eligibility, ranking and the combined `select_worker_for_offer` — many cases, no database, the
same pattern this module already uses for `PICK_TASK_TRANSITIONS`/`can_transition_pick_task`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app import domain


def test_every_adr_0025_section_2_edge_is_present() -> None:
    edges = {
        ("CREATED", "OFFERED"),
        ("CREATED", "ESCALATED"),
        ("ESCALATED", "OFFERED"),
        ("OFFERED", "CREATED"),
        ("OFFERED", "ACCEPTED"),
        ("ACCEPTED", "CREATED"),
        ("ACCEPTED", "IN_PROGRESS"),
        ("IN_PROGRESS", "EXCEPTION"),
        ("EXCEPTION", "IN_PROGRESS"),
        ("IN_PROGRESS", "PICKED"),
        ("PICKED", "CLOSED"),
    }
    cancellable_from = {"CREATED", "OFFERED", "ESCALATED", "ACCEPTED", "IN_PROGRESS", "EXCEPTION"}
    for status in cancellable_from:
        edges.add((status, "CANCELLED"))

    actual = {(frm, to) for frm, tos in domain.PICK_TASK_TRANSITIONS.items() for to in tos}
    assert actual == edges


def test_a_picked_task_is_not_cancellable() -> None:
    assert not domain.can_transition_pick_task("PICKED", "CANCELLED")
    assert domain.can_transition_pick_task("PICKED", "CLOSED")


def test_there_is_no_direct_offered_to_escalated_edge() -> None:
    """A failed round always lands on `CREATED` first; `CREATED -> ESCALATED` is a second, separate
    edge (`app.domain`'s own module comment) — never a shortcut this map should offer."""
    assert not domain.can_transition_pick_task("OFFERED", "ESCALATED")


def test_terminal_statuses_have_no_way_out() -> None:
    assert domain.PICK_TASK_TRANSITIONS["CLOSED"] == frozenset()
    assert domain.PICK_TASK_TRANSITIONS["CANCELLED"] == frozenset()


def test_a_no_op_move_to_the_same_status_is_refused() -> None:
    for status in domain.PICK_TASK_STATUSES:
        assert not domain.can_transition_pick_task(status, status)


def test_pick_task_line_settled() -> None:
    assert domain.pick_task_line_settled(quantity_milli=10, picked_milli=10, short_milli=0)
    assert domain.pick_task_line_settled(quantity_milli=10, picked_milli=6, short_milli=4)
    assert not domain.pick_task_line_settled(quantity_milli=10, picked_milli=6, short_milli=0)


# ------------------------------------------------------------------ auto-assignment (ADR-0024 §9)


def _candidate(
    *,
    worker_id: uuid.UUID | None = None,
    is_active: bool = True,
    presence_status: str = "online",
    zone_location_ids: frozenset[uuid.UUID] = frozenset(),
    skills: frozenset[str] = frozenset(),
    open_task_count: int = 0,
    last_offered_at: datetime | None = None,
) -> domain.WorkerCandidate:
    return domain.WorkerCandidate(
        worker_id=worker_id or uuid.uuid4(),
        is_active=is_active,
        presence_status=presence_status,
        zone_location_ids=zone_location_ids,
        skills=skills,
        open_task_count=open_task_count,
        last_offered_at=last_offered_at,
    )


class TestEligibility:
    def test_an_active_online_worker_with_no_zone_or_skill_requirement_is_eligible(self) -> None:
        candidate = _candidate()
        assert domain.is_worker_eligible_for_task(
            candidate, zone_location_id=None, required_skills=frozenset()
        )

    def test_an_inactive_worker_is_never_eligible(self) -> None:
        candidate = _candidate(is_active=False)
        assert not domain.is_worker_eligible_for_task(
            candidate, zone_location_id=None, required_skills=frozenset()
        )

    def test_an_offline_worker_is_never_eligible(self) -> None:
        candidate = _candidate(presence_status="offline")
        assert not domain.is_worker_eligible_for_task(
            candidate, zone_location_id=None, required_skills=frozenset()
        )

    def test_a_worker_without_the_tasks_zone_is_not_eligible(self) -> None:
        zone = uuid.uuid4()
        candidate = _candidate(zone_location_ids=frozenset({uuid.uuid4()}))
        assert not domain.is_worker_eligible_for_task(
            candidate, zone_location_id=zone, required_skills=frozenset()
        )

    def test_a_worker_with_the_tasks_zone_is_eligible(self) -> None:
        zone = uuid.uuid4()
        candidate = _candidate(zone_location_ids=frozenset({zone}))
        assert domain.is_worker_eligible_for_task(
            candidate, zone_location_id=zone, required_skills=frozenset()
        )

    def test_a_task_with_no_resolved_zone_imposes_no_zone_requirement(self) -> None:
        """`zone_location_id is None` — the single-bin-shop case (ADR-0025 §1) — never refuses a
        worker for lacking zone access, the same reading `offer_task`'s own manual path gives."""
        candidate = _candidate(zone_location_ids=frozenset())
        assert domain.is_worker_eligible_for_task(
            candidate, zone_location_id=None, required_skills=frozenset()
        )

    def test_a_worker_missing_a_required_skill_is_not_eligible(self) -> None:
        candidate = _candidate(skills=frozenset({"forklift"}))
        assert not domain.is_worker_eligible_for_task(
            candidate, zone_location_id=None, required_skills=frozenset({"cold_storage"})
        )

    def test_a_worker_holding_every_required_skill_is_eligible(self) -> None:
        candidate = _candidate(skills=frozenset({"forklift", "cold_storage"}))
        assert domain.is_worker_eligible_for_task(
            candidate, zone_location_id=None, required_skills=frozenset({"cold_storage"})
        )

    def test_a_worker_missing_one_of_several_required_skills_is_not_eligible(self) -> None:
        candidate = _candidate(skills=frozenset({"forklift"}))
        assert not domain.is_worker_eligible_for_task(
            candidate,
            zone_location_id=None,
            required_skills=frozenset({"forklift", "cold_storage"}),
        )


class TestEligibleWorkersForTask:
    def test_zero_candidates_gives_an_empty_list(self) -> None:
        assert (
            domain.eligible_workers_for_task([], zone_location_id=None, required_skills=frozenset())
            == []
        )

    def test_only_eligible_candidates_pass_through(self) -> None:
        eligible = _candidate()
        offline = _candidate(presence_status="offline")
        inactive = _candidate(is_active=False)
        result = domain.eligible_workers_for_task(
            [eligible, offline, inactive], zone_location_id=None, required_skills=frozenset()
        )
        assert result == [eligible]

    def test_an_excluded_worker_is_not_eligible_even_if_otherwise_ideal(self) -> None:
        """ADR-0025 §2: a worker whose offer on this task was declined/expired/released is
        excluded from every later *automatic* round of it — without this a re-offering call would
        keep re-picking the same worker every round, since nothing else about them changed."""
        declined_before = _candidate()
        result = domain.eligible_workers_for_task(
            [declined_before],
            zone_location_id=None,
            required_skills=frozenset(),
            excluded_worker_ids=frozenset({declined_before.worker_id}),
        )
        assert result == []

    def test_exclusion_only_removes_the_named_worker(self) -> None:
        excluded = _candidate()
        still_eligible = _candidate()
        result = domain.eligible_workers_for_task(
            [excluded, still_eligible],
            zone_location_id=None,
            required_skills=frozenset(),
            excluded_worker_ids=frozenset({excluded.worker_id}),
        )
        assert result == [still_eligible]


class TestRanking:
    def test_fewer_open_tasks_ranks_first(self) -> None:
        busy = _candidate(open_task_count=3)
        idle = _candidate(open_task_count=0)
        ranked = domain.rank_workers_for_offer([busy, idle])
        assert ranked == [idle, busy]

    def test_a_worker_never_offered_anything_ranks_before_one_offered_recently(self) -> None:
        never_offered = _candidate(last_offered_at=None)
        recently_offered = _candidate(last_offered_at=datetime.now(UTC))
        ranked = domain.rank_workers_for_offer([recently_offered, never_offered])
        assert ranked == [never_offered, recently_offered]

    def test_idle_time_only_breaks_ties_on_open_task_count(self) -> None:
        now = datetime.now(UTC)
        busy_but_idle = _candidate(open_task_count=1, last_offered_at=now - timedelta(days=1))
        idle_and_recent = _candidate(open_task_count=0, last_offered_at=now)
        ranked = domain.rank_workers_for_offer([busy_but_idle, idle_and_recent])
        assert ranked == [idle_and_recent, busy_but_idle]

    def test_worker_id_is_the_final_deterministic_tiebreak(self) -> None:
        now = datetime.now(UTC)
        low_id = _candidate(worker_id=uuid.UUID(int=1), open_task_count=0, last_offered_at=now)
        high_id = _candidate(worker_id=uuid.UUID(int=2), open_task_count=0, last_offered_at=now)
        ranked = domain.rank_workers_for_offer([high_id, low_id])
        assert ranked == [low_id, high_id]
        # Same inputs, different input order — same output order (a total order, ADR-0024 §9).
        ranked_again = domain.rank_workers_for_offer([low_id, high_id])
        assert ranked_again == ranked

    def test_an_empty_candidate_list_ranks_to_an_empty_list(self) -> None:
        assert domain.rank_workers_for_offer([]) == []


class TestSelectWorkerForOffer:
    def test_zero_eligible_workers_selects_nobody(self) -> None:
        offline = _candidate(presence_status="offline")
        assert (
            domain.select_worker_for_offer(
                [offline], zone_location_id=None, required_skills=frozenset()
            )
            is None
        )

    def test_one_eligible_worker_is_selected(self) -> None:
        only = _candidate()
        assert (
            domain.select_worker_for_offer(
                [only], zone_location_id=None, required_skills=frozenset()
            )
            == only
        )

    def test_many_eligible_workers_selects_the_best_ranked_one(self) -> None:
        idle = _candidate(open_task_count=0)
        busy = _candidate(open_task_count=5)
        ineligible = _candidate(presence_status="offline", open_task_count=0)
        chosen = domain.select_worker_for_offer(
            [busy, ineligible, idle], zone_location_id=None, required_skills=frozenset()
        )
        assert chosen == idle

    def test_zone_mismatch_excludes_an_otherwise_ideal_worker(self) -> None:
        zone = uuid.uuid4()
        wrong_zone = _candidate(open_task_count=0, zone_location_ids=frozenset({uuid.uuid4()}))
        right_zone_but_busy = _candidate(open_task_count=9, zone_location_ids=frozenset({zone}))
        chosen = domain.select_worker_for_offer(
            [wrong_zone, right_zone_but_busy], zone_location_id=zone, required_skills=frozenset()
        )
        assert chosen == right_zone_but_busy

    def test_skill_mismatch_excludes_an_otherwise_ideal_worker(self) -> None:
        unskilled = _candidate(open_task_count=0)
        skilled_but_busy = _candidate(open_task_count=9, skills=frozenset({"cold_storage"}))
        chosen = domain.select_worker_for_offer(
            [unskilled, skilled_but_busy],
            zone_location_id=None,
            required_skills=frozenset({"cold_storage"}),
        )
        assert chosen == skilled_but_busy

    def test_a_worker_excluded_by_a_past_decline_loses_to_the_next_best_eligible_worker(
        self,
    ) -> None:
        declined_before = _candidate(open_task_count=0)
        next_best = _candidate(open_task_count=2)
        chosen = domain.select_worker_for_offer(
            [declined_before, next_best],
            zone_location_id=None,
            required_skills=frozenset(),
            excluded_worker_ids=frozenset({declined_before.worker_id}),
        )
        assert chosen == next_best

    def test_every_candidate_excluded_selects_nobody(self) -> None:
        only = _candidate()
        chosen = domain.select_worker_for_offer(
            [only],
            zone_location_id=None,
            required_skills=frozenset(),
            excluded_worker_ids=frozenset({only.worker_id}),
        )
        assert chosen is None
