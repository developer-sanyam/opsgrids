"""Pure warehouse-geometry functions (plan §2/F7) — no database, no session, exactly
`app.domain`'s own convention. `app.warehouse.selectors.validate_layout`/`find_route` are the only
callers of these; everything checkable about their *logic* is checkable here, by hand, against a
small graph whose shortest path is known in advance.
"""

from __future__ import annotations

import uuid

from app import domain

# ------------------------------------------------------------------ small helpers


def _n(node_id: uuid.UUID, *, location_id: uuid.UUID | None = None) -> domain.GraphNode:
    return domain.GraphNode(id=node_id, location_id=location_id)


def _e(
    a: uuid.UUID,
    b: uuid.UUID,
    distance: int,
    *,
    traversable: bool = True,
    bidirectional: bool = True,
) -> domain.GraphEdge:
    return domain.GraphEdge(
        from_node_id=a,
        to_node_id=b,
        distance_mm=distance,
        is_traversable=traversable,
        bidirectional=bidirectional,
    )


# ------------------------------------------------------------------ shortest_walk_path (Dijkstra)


class TestShortestWalkPathKnownGraph:
    """A -> B -> D costs 20; A -> C -> D costs 6. The shorter route must win even though it has
    more the same number of hops — this is what tells a real Dijkstra apart from a naive
    fewest-hops search."""

    def setup_method(self) -> None:
        self.a, self.b, self.c, self.d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        self.nodes = [_n(self.a), _n(self.b), _n(self.c), _n(self.d)]
        self.edges = [
            _e(self.a, self.b, 10),
            _e(self.b, self.d, 10),
            _e(self.a, self.c, 3),
            _e(self.c, self.d, 3),
        ]

    def test_the_shorter_route_wins(self) -> None:
        plan = domain.shortest_walk_path(self.nodes, self.edges, self.a, self.d)
        assert plan is not None
        assert plan.node_ids == [self.a, self.c, self.d]
        assert plan.distance_mm == 6

    def test_edge_order_does_not_change_the_result(self) -> None:
        """Same graph, edges listed in reverse — the answer must not depend on it."""
        plan = domain.shortest_walk_path(self.nodes, list(reversed(self.edges)), self.a, self.d)
        assert plan is not None
        assert plan.node_ids == [self.a, self.c, self.d]
        assert plan.distance_mm == 6

    def test_same_start_and_end_is_a_zero_distance_trivial_path(self) -> None:
        plan = domain.shortest_walk_path(self.nodes, self.edges, self.a, self.a)
        assert plan is not None
        assert plan.node_ids == [self.a]
        assert plan.distance_mm == 0

    def test_reverse_direction_finds_the_same_shortest_route(self) -> None:
        plan = domain.shortest_walk_path(self.nodes, self.edges, self.d, self.a)
        assert plan is not None
        assert plan.node_ids == [self.d, self.c, self.a]
        assert plan.distance_mm == 6


class TestShortestWalkPathDisconnectedGraph:
    def test_an_isolated_node_has_no_path(self) -> None:
        a, b, isolated = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        nodes = [_n(a), _n(b), _n(isolated)]
        edges = [_e(a, b, 5)]
        assert domain.shortest_walk_path(nodes, edges, a, isolated) is None

    def test_two_separate_components_have_no_path_between_them(self) -> None:
        a, b, c, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        nodes = [_n(a), _n(b), _n(c), _n(d)]
        edges = [_e(a, b, 5), _e(c, d, 5)]  # two disjoint components
        assert domain.shortest_walk_path(nodes, edges, a, d) is None
        # but each component is internally routable
        assert domain.shortest_walk_path(nodes, edges, a, b) is not None
        assert domain.shortest_walk_path(nodes, edges, c, d) is not None

    def test_an_endpoint_not_in_the_graph_at_all_has_no_path(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        nodes = [_n(a)]
        edges: list[domain.GraphEdge] = []
        assert domain.shortest_walk_path(nodes, edges, a, b) is None


class TestShortestWalkPathBlockedEdges:
    def test_a_blocked_edge_is_excluded_even_when_it_is_the_only_direct_route(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        nodes = [_n(a), _n(b)]
        edges = [_e(a, b, 5, traversable=False)]
        assert domain.shortest_walk_path(nodes, edges, a, b) is None

    def test_a_blocked_edge_forces_the_longer_traversable_detour(self) -> None:
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        nodes = [_n(a), _n(b), _n(c)]
        edges = [
            _e(a, b, 1, traversable=False),  # the short direct route is blocked
            _e(a, c, 4),
            _e(c, b, 4),
        ]
        plan = domain.shortest_walk_path(nodes, edges, a, b)
        assert plan is not None
        assert plan.node_ids == [a, c, b]
        assert plan.distance_mm == 8

    def test_a_one_way_edge_routes_only_in_its_own_direction(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        nodes = [_n(a), _n(b)]
        edges = [_e(a, b, 5, bidirectional=False)]
        assert domain.shortest_walk_path(nodes, edges, a, b) is not None
        assert domain.shortest_walk_path(nodes, edges, b, a) is None


# ------------------------------------------------------------------ overlapping_placements


def _box(x: int, y: int, w: int, d: int) -> domain.PlacementBox:
    return domain.PlacementBox(location_id=uuid.uuid4(), x_mm=x, y_mm=y, width_mm=w, depth_mm=d)


class TestOverlappingPlacements:
    def test_two_overlapping_boxes_are_reported(self) -> None:
        a = _box(0, 0, 100, 100)
        b = _box(50, 50, 100, 100)
        pairs = domain.overlapping_placements([a, b])
        assert {a.location_id, b.location_id} == set(pairs[0])

    def test_boxes_that_only_touch_edges_do_not_overlap(self) -> None:
        a = _box(0, 0, 100, 100)
        b = _box(100, 0, 100, 100)  # a's right edge == b's left edge, exactly
        assert domain.overlapping_placements([a, b]) == []

    def test_far_apart_boxes_do_not_overlap(self) -> None:
        a = _box(0, 0, 10, 10)
        b = _box(1000, 1000, 10, 10)
        assert domain.overlapping_placements([a, b]) == []

    def test_zero_or_one_placement_has_nothing_to_overlap(self) -> None:
        assert domain.overlapping_placements([]) == []
        assert domain.overlapping_placements([_box(0, 0, 10, 10)]) == []

    def test_three_boxes_two_overlapping_pairs(self) -> None:
        a = _box(0, 0, 100, 100)
        b = _box(50, 50, 100, 100)  # overlaps a
        c = _box(1000, 1000, 10, 10)  # overlaps nothing
        pairs = domain.overlapping_placements([a, b, c])
        assert len(pairs) == 1
        assert {a.location_id, b.location_id} == set(pairs[0])


# ------------------------------------------------------------------ disconnected_required_nodes


class TestDisconnectedRequiredNodes:
    def test_a_connected_pair_of_required_nodes_has_no_issue(self) -> None:
        loc_a, loc_b = uuid.uuid4(), uuid.uuid4()
        a, b = uuid.uuid4(), uuid.uuid4()
        nodes = [_n(a, location_id=loc_a), _n(b, location_id=loc_b)]
        edges = [_e(a, b, 5)]
        assert domain.disconnected_required_nodes(nodes, edges) == []

    def test_an_isolated_required_node_is_reported(self) -> None:
        """Whichever of the two components the flood-fill happens to start from (an
        implementation detail — the lexicographically smaller id, unpredictable across random
        UUIDs), the *other* component is exactly what comes back: either the isolated node alone,
        or the connected pair, never neither and never a partial mix."""
        loc_a, loc_b = uuid.uuid4(), uuid.uuid4()
        a, b, isolated = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        nodes = [
            _n(a, location_id=loc_a),
            _n(b, location_id=loc_b),
            _n(isolated, location_id=uuid.uuid4()),
        ]
        edges = [_e(a, b, 5)]
        result = domain.disconnected_required_nodes(nodes, edges)
        assert set(result) in ({isolated}, {a, b})

    def test_a_required_node_reachable_only_through_a_blocked_edge_is_reported(self) -> None:
        """Both required nodes are mutually unreachable — which one the function names is an
        implementation detail (it flood-fills from whichever has the lexicographically smaller
        id), but it must name exactly one of them, not neither."""
        loc_a, loc_b = uuid.uuid4(), uuid.uuid4()
        a, b = uuid.uuid4(), uuid.uuid4()
        nodes = [_n(a, location_id=loc_a), _n(b, location_id=loc_b)]
        edges = [_e(a, b, 5, traversable=False)]
        result = domain.disconnected_required_nodes(nodes, edges)
        assert len(result) == 1
        assert result[0] in (a, b)

    def test_a_junction_node_bridging_two_required_nodes_connects_them(self) -> None:
        loc_a, loc_b = uuid.uuid4(), uuid.uuid4()
        a, b, junction = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        nodes = [_n(a, location_id=loc_a), _n(junction), _n(b, location_id=loc_b)]
        edges = [_e(a, junction, 3), _e(junction, b, 3)]
        assert domain.disconnected_required_nodes(nodes, edges) == []

    def test_zero_or_one_required_node_is_trivially_connected(self) -> None:
        assert domain.disconnected_required_nodes([], []) == []
        lone = uuid.uuid4()
        assert domain.disconnected_required_nodes([_n(lone, location_id=uuid.uuid4())], []) == []

    def test_connectivity_ignores_one_way_direction(self) -> None:
        """A one-way edge still counts as connective tissue for validation, even though it would
        not let a real walk return — the deliberately conservative reading `disconnected_required
        _nodes`'s own docstring documents (it flags graphs a routing search would still solve,
        never the other way round)."""
        loc_a, loc_b = uuid.uuid4(), uuid.uuid4()
        a, b = uuid.uuid4(), uuid.uuid4()
        nodes = [_n(a, location_id=loc_a), _n(b, location_id=loc_b)]
        edges = [_e(a, b, 5, bidirectional=False)]
        assert domain.disconnected_required_nodes(nodes, edges) == []


# ------------------------------------------------------------------ layout version transitions


class TestLayoutVersionTransitions:
    def test_draft_can_publish(self) -> None:
        assert domain.can_transition_layout_version("draft", "published") is True

    def test_archived_can_publish_again_a_rollback(self) -> None:
        assert domain.can_transition_layout_version("archived", "published") is True

    def test_published_cannot_be_published_again(self) -> None:
        assert domain.can_transition_layout_version("published", "published") is False

    def test_published_never_transitions_directly_to_archived(self) -> None:
        """`publish_layout_version` writes that move itself, as a side effect of publishing a
        different version — it is never something a caller asks for directly."""
        assert domain.can_transition_layout_version("published", "archived") is False

    def test_draft_cannot_go_straight_to_archived(self) -> None:
        assert domain.can_transition_layout_version("draft", "archived") is False

    def test_no_status_has_a_self_edge(self) -> None:
        for status in ("draft", "published", "archived"):
            assert domain.can_transition_layout_version(status, status) is False
