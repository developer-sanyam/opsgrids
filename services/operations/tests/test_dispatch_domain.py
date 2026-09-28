"""`app.domain.SHIPMENT_TRANSITIONS` / `can_transition_shipment` — pure, no database
(`docs/fulfilment/plan.md` F5). Every edge in the map, and every non-edge answers 409's own
predicate (`False` here — the route layer is what turns it into a 409, proven in
`test_dispatch_shipments.py`), the same convention `can_transition_order`/`can_transition_pick_task`
already document.
"""

from __future__ import annotations

import pytest

from app import domain

#: The exact content of `app.domain.SHIPMENT_TRANSITIONS`.
EDGES: list[tuple[str, str]] = [
    ("PICKUP_HANDOVER", "IN_TRANSIT"),
    ("PICKUP_HANDOVER", "FAILED"),
    ("PICKUP_HANDOVER", "RETURNED"),
    ("IN_TRANSIT", "DELIVERED"),
    ("IN_TRANSIT", "FAILED"),
    ("IN_TRANSIT", "RETURNED"),
    ("FAILED", "IN_TRANSIT"),
    ("FAILED", "RETURNED"),
]

TERMINAL = ("DELIVERED", "RETURNED")


class TestEveryEdgeIsReachable:
    @pytest.mark.parametrize(("current", "new"), EDGES)
    def test_edge(self, current: str, new: str) -> None:
        assert domain.can_transition_shipment(current, new) is True

    def test_every_status_is_covered_by_the_map(self) -> None:
        assert set(domain.SHIPMENT_TRANSITIONS) == set(domain.SHIPMENT_STATUSES)

    def test_no_edge_is_missing_from_the_fixture(self) -> None:
        """The fixture above and the map agree exactly — a change to one without the other is a
        red test, not a silent gap."""
        from_map = {(frm, to) for frm, tos in domain.SHIPMENT_TRANSITIONS.items() for to in tos}
        assert from_map == set(EDGES)


class TestTerminalStatesHaveNoOutgoingEdge:
    @pytest.mark.parametrize("status", TERMINAL)
    def test_terminal(self, status: str) -> None:
        assert domain.SHIPMENT_TRANSITIONS[status] == frozenset()
        assert status in domain.SHIPMENT_TERMINAL_STATUSES


class TestNoSelfEdges:
    @pytest.mark.parametrize("status", list(domain.SHIPMENT_STATUSES))
    def test_no_self_edge(self, status: str) -> None:
        assert domain.can_transition_shipment(status, status) is False


class TestNonEdgesAreRefused:
    @pytest.mark.parametrize(
        ("current", "new"),
        [
            ("PICKUP_HANDOVER", "DELIVERED"),  # no transit leg recorded — not an edge this
            # build offers (a same-day local collection is the counter path, not this one)
            ("DELIVERED", "IN_TRANSIT"),
            ("DELIVERED", "RETURNED"),
            ("DELIVERED", "FAILED"),
            ("RETURNED", "IN_TRANSIT"),
            ("RETURNED", "DELIVERED"),
            ("RETURNED", "PICKUP_HANDOVER"),
            ("IN_TRANSIT", "PICKUP_HANDOVER"),
            ("FAILED", "DELIVERED"),  # a failed attempt must be retried (-> IN_TRANSIT) or
            # returned, never marked delivered directly
            ("FAILED", "PICKUP_HANDOVER"),
        ],
    )
    def test_non_edge(self, current: str, new: str) -> None:
        assert domain.can_transition_shipment(current, new) is False
