"""The automation engine's pure half: graph validation, path reading, conditions, wait windows.

No database. Everything a run decides that is not "talk to Postgres" is checked here, including a
property test for the one piece of arithmetic that can quietly send a customer a message at 3am:
the business-hours window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from app import a1_pure

EVENTS = {"support.desk.ticket.created", "commerce.invoice.finalized"}


def graph(*, trigger: str = "support.desk.ticket.created") -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"event_type": trigger}},
            {
                "id": "c",
                "type": "condition",
                "config": {
                    "match": "all",
                    "rules": [{"path": "payload.priority", "op": "eq", "value": "high"}],
                },
            },
            {"id": "w", "type": "wait", "config": {"seconds": 3600}},
            {
                "id": "s",
                "type": "send_message",
                "config": {
                    "channel": "email",
                    "template": {"ref": "ack"},
                    "recipient_path": "payload.requester.email",
                    "variable_map": {"number": "payload.number"},
                },
            },
            {"id": "e", "type": "end", "config": {}},
        ],
        "edges": [
            {"from": "t", "to": "c"},
            {"from": "c", "to": "w", "label": "yes"},
            {"from": "c", "to": "e", "label": "no"},
            {"from": "w", "to": "s"},
            {"from": "s", "to": "e"},
        ],
    }


def codes(g: Any) -> set[str]:
    problems, _ = a1_pure.validate_graph(g, subscribable_events=EVENTS)
    return {p.code for p in problems}


# ------------------------------------------------------------------------- graph validation


def test_a_well_formed_graph_has_no_problems() -> None:
    assert codes(graph()) == set()


def test_not_a_graph() -> None:
    assert codes("nope") == {"graph_shape"}
    assert codes({"nodes": [], "edges": "x"}) == {"graph_shape"}


def test_exactly_one_trigger() -> None:
    g = graph()
    g["nodes"] = [n for n in g["nodes"] if n["id"] != "t"]
    g["edges"] = [e for e in g["edges"] if e["from"] != "t"]
    assert "trigger_count" in codes(g)
    g = graph()
    g["nodes"].append({"id": "t2", "type": "trigger", "config": {"event_type": "x"}})
    assert "trigger_count" in codes(g)


def test_trigger_event_must_be_in_the_catalogue() -> None:
    assert "trigger_event" in codes(graph(trigger="made.up.event"))


def test_unreachable_node_and_cycle_are_reported() -> None:
    g = graph()
    g["nodes"].append({"id": "orphan", "type": "end", "config": {}})
    assert "node_unreachable" in codes(g)
    g = graph()
    g["edges"].append({"from": "s", "to": "w"})
    found = codes(g)
    assert "cycle" in found
    g = graph()
    g["edges"] = [e for e in g["edges"] if e["from"] != "s"] + [{"from": "s", "to": "s"}]
    assert "cycle" in codes(g)


def test_condition_needs_one_yes_and_one_no() -> None:
    g = graph()
    g["edges"] = [e for e in g["edges"] if e.get("label") != "no"]
    assert "condition_edges" in codes(g)


def test_dangling_edge_and_duplicate_ids() -> None:
    g = graph()
    g["edges"].append({"from": "s", "to": "ghost"})
    assert "edge_dangling" in codes(g)
    g = graph()
    g["nodes"].append({"id": "s", "type": "end", "config": {}})
    assert "node_duplicate" in codes(g)


def test_wait_bounds_and_window() -> None:
    for seconds in (59, 2_592_001, "10", True, None):
        g = graph()
        g["nodes"][2]["config"] = {"seconds": seconds}
        assert "wait_seconds" in codes(g), seconds
    for seconds in (60, 2_592_000):
        g = graph()
        g["nodes"][2]["config"] = {"seconds": seconds}
        assert codes(g) == set()
    g = graph()
    g["nodes"][2]["config"] = {
        "seconds": 60,
        "window": {"start": "25:00", "end": "18:00", "timezone": "Asia/Kolkata"},
    }
    assert "wait_window" in codes(g)
    g["nodes"][2]["config"]["window"] = {"start": "09:00", "end": "18:00", "timezone": "Nowhere/X"}
    assert "wait_window" in codes(g)


def test_send_message_config_is_checked() -> None:
    g = graph()
    g["nodes"][3]["config"] = {"channel": "sms", "template": {}, "recipient_path": "email"}
    found = codes(g)
    assert {"send_channel", "send_template", "send_recipient"} <= found
    g = graph()
    g["nodes"][3]["config"]["variable_map"] = {"1": "customer.name"}
    assert "send_variables" in codes(g)


def test_condition_rules_are_checked() -> None:
    g = graph()
    g["nodes"][1]["config"] = {"match": "some", "rules": []}
    assert {"condition_match", "condition_rules"} <= codes(g)
    g["nodes"][1]["config"] = {
        "match": "any",
        "rules": [
            {"path": "bad", "op": "regex", "value": 1},
            {"path": "payload.a", "op": "in", "value": 3},
        ],
    }
    assert {"condition_path", "condition_op", "condition_value"} <= codes(g)


# ---------------------------------------------------------------------------- path reading


def test_read_path_roots() -> None:
    payload = {"customer": {"name": "Asha", "tags": ["a"]}}
    run = {"previous": {"status": "done"}}
    assert a1_pure.read_path("payload.customer.name", payload, run) == "Asha"
    assert a1_pure.read_path("run.previous.status", payload, run) == "done"
    assert a1_pure.read_path("payload.customer.missing", payload, run) is None
    assert a1_pure.read_path("other.customer.name", payload, run) is None
    assert a1_pure.read_path("payload", payload, run) is None
    assert a1_pure.read_path("payload.customer.name.deeper", payload, run) is None


# ------------------------------------------------------------------------------ conditions


def rule(op: str, value: Any = None, path: str = "payload.x") -> dict[str, Any]:
    out: dict[str, Any] = {"path": path, "op": op}
    if value is not None:
        out["value"] = value
    return out


def test_operators() -> None:
    ev = a1_pure.evaluate_rule
    assert ev(rule("eq", "high"), {"x": "high"}, {})
    assert not ev(rule("eq", "high"), {"x": "low"}, {})
    assert ev(rule("eq", 5), {"x": 5.0}, {})
    assert ev(rule("neq", "high"), {"x": "low"}, {})
    assert ev(rule("neq", "high"), {}, {})  # absent is not equal
    assert ev(rule("gt", 10), {"x": 11}, {})
    assert ev(rule("gte", 10), {"x": "10"}, {})
    assert ev(rule("lt", 10), {"x": 9}, {})
    assert ev(rule("lte", 10), {"x": 10}, {})
    assert not ev(rule("gt", 10), {"x": "abc"}, {})
    assert not ev(rule("gt", 10), {}, {})
    assert ev(rule("contains", "urgent"), {"x": "Very URGENT thing"}, {})
    assert ev(rule("contains", "b"), {"x": ["a", "b"]}, {})
    assert ev(rule("in", ["a", "b"]), {"x": "b"}, {})
    assert not ev(rule("in", ["a", "b"]), {"x": "c"}, {})
    assert ev(rule("exists"), {"x": 0}, {})
    assert not ev(rule("exists"), {"x": None}, {})
    assert not ev(rule("exists"), {}, {})
    assert ev({"path": "payload.x", "op": "exists", "value": False}, {}, {})


def test_any_and_all() -> None:
    cfg = {"match": "all", "rules": [rule("eq", 1), rule("eq", 2, "payload.y")]}
    assert not a1_pure.evaluate_condition(cfg, {"x": 1, "y": 3}, {})
    assert a1_pure.evaluate_condition({**cfg, "match": "any"}, {"x": 1, "y": 3}, {})
    assert a1_pure.evaluate_condition(cfg, {"x": 1, "y": 2}, {})
    assert not a1_pure.evaluate_condition({"match": "all", "rules": []}, {}, {})


@given(st.dictionaries(st.text(min_size=1, max_size=5), st.integers(), max_size=5))
def test_conditions_never_raise_on_arbitrary_payloads(payload: dict[str, int]) -> None:
    for op in a1_pure.OPS:
        a1_pure.evaluate_rule(rule(op, "v"), payload, {})


# --------------------------------------------------------------------------- wait windows

IST = "Asia/Kolkata"
DAY = {"start": "09:00", "end": "18:00", "timezone": IST}
NIGHT = {"start": "22:00", "end": "06:00", "timezone": IST}


def utc(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_no_window_is_plain_addition() -> None:
    now = utc(2026, 9, 26, 5)
    assert a1_pure.wake_time(now, 3600, None) == now + timedelta(hours=1)


def test_inside_the_window_wakes_on_time() -> None:
    now = utc(2026, 9, 26, 5)  # 10:30 IST
    assert a1_pure.wake_time(now, 3600, DAY) == now + timedelta(hours=1)


def test_outside_the_window_moves_to_the_next_open() -> None:
    now = utc(2026, 9, 26, 13)  # 18:30 IST, closed
    assert a1_pure.wake_time(now, 60, DAY) == utc(2026, 9, 27, 3, 30)  # 09:00 IST next day
    early = utc(2026, 9, 26, 0)  # 05:30 IST
    assert a1_pure.wake_time(early, 60, DAY) == utc(2026, 9, 26, 3, 30)


def test_overnight_window() -> None:
    assert a1_pure.in_window(utc(2026, 9, 26, 17), NIGHT)  # 22:30 IST
    assert a1_pure.in_window(utc(2026, 9, 26, 22), NIGHT)  # 03:30 IST
    assert not a1_pure.in_window(utc(2026, 9, 26, 5), NIGHT)  # 10:30 IST
    assert a1_pure.wake_time(utc(2026, 9, 26, 5), 60, NIGHT) == utc(2026, 9, 26, 16, 30)


@given(
    seconds=st.integers(min_value=60, max_value=2_592_000),
    start=st.datetimes(min_value=datetime(2026, 1, 1), max_value=datetime(2027, 12, 31)),
    window=st.sampled_from([DAY, NIGHT, {"start": "00:00", "end": "00:00", "timezone": IST}]),
)
def test_wake_is_never_early_and_always_inside_the_window(
    seconds: int, start: datetime, window: dict[str, str]
) -> None:
    now = start.replace(tzinfo=UTC)
    woke = a1_pure.wake_time(now, seconds, window)
    assert woke >= now + timedelta(seconds=seconds)
    assert a1_pure.in_window(woke, window)
    # Never later than a day past the plain wake time: a window opens at least daily.
    assert woke <= now + timedelta(seconds=seconds) + timedelta(days=1)


def test_retry_backoff() -> None:
    assert [a1_pure.retry_delay_seconds(n) for n in (1, 2, 3, 4, 10)] == [60, 120, 240, 480, 3600]
    assert a1_pure.should_retry(1) and not a1_pure.should_retry(a1_pure.MAX_ATTEMPTS)
