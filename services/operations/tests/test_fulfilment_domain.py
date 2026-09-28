"""`app.domain.PICK_TASK_TRANSITIONS` against ADR-0025 §2's table, verbatim — no I/O, no
session, pure functions, mirroring `test_orders_domain.py`'s own shape for `ORDER_TRANSITIONS`.
"""

from __future__ import annotations

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
