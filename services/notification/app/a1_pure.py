"""Pure automation logic: graph validation, path resolution, condition evaluation, wait windows.

No I/O, no session, no clock of its own (`now` is always an argument). Everything the workflow
engine decides that is not "talk to the database" lives here so it can be property-tested without
Postgres: whether a graph may be published, what a condition says about an event, and *when* a
wait ends once a business-hours window is applied.

A graph is `{"nodes": [...], "edges": [...]}` exactly as the API contract states it. Validation
returns every problem it finds rather than the first, because the builder shows them on the
offending nodes all at once.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NODE_TYPES: Final[tuple[str, ...]] = (
    "trigger",
    "condition",
    "wait",
    "send_message",
    "create_task",
    "end",
)
OPS: Final[tuple[str, ...]] = ("eq", "neq", "gt", "gte", "lt", "lte", "contains", "in", "exists")
CHANNELS: Final[tuple[str, ...]] = ("whatsapp", "email")
PURPOSES: Final[tuple[str, ...]] = ("transactional", "marketing")

WAIT_MIN_SECONDS: Final = 60
WAIT_MAX_SECONDS: Final = 2_592_000  # 30 days
MAX_NODES: Final = 60
MAX_RULES: Final = 20

#: Problem severities. Only `error` blocks a publish; a `warning` (a channel that is not connected
#: yet) is shown but the automation may still go live.
ERROR: Final = "error"
WARNING: Final = "warning"

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_ROOTS: Final = ("payload", "run")


@dataclass(frozen=True, slots=True)
class Problem:
    node_id: str | None
    code: str
    message: str
    severity: str = ERROR

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }


# ------------------------------------------------------------------------------ path reading

_MISSING: Final = object()


def _walk(root: Any, segments: list[str]) -> Any:
    current = root
    for segment in segments:
        if not isinstance(current, Mapping) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def read_path(path: str, payload: Mapping[str, Any], run: Mapping[str, Any]) -> Any:
    """Read `payload.a.b` from the trigger event or `run.a.b` from the run's own context.

    Missing is `None`. A path with any other root is also `None` rather than an error: the
    root is checked at authoring time (`validate_graph`), and an evaluation that meets a path it
    does not understand must not guess.
    """
    found = read_path_strict(path, payload, run)
    return None if found is _MISSING else found


def read_path_strict(path: str, payload: Mapping[str, Any], run: Mapping[str, Any]) -> Any:
    root, _, rest = path.partition(".")
    if root not in _ROOTS or not rest:
        return _MISSING
    source: Mapping[str, Any] = payload if root == "payload" else run
    return _walk(source, rest.split("."))


def path_is_valid(path: Any) -> bool:
    if not isinstance(path, str):
        return False
    root, _, rest = path.partition(".")
    return root in _ROOTS and bool(rest) and all(rest.split("."))


# ---------------------------------------------------------------------- condition evaluation


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _equal(actual: Any, expected: Any) -> bool:
    if actual is None:
        return expected is None
    a, b = _number(actual), _number(expected)
    if a is not None and b is not None and not isinstance(actual, str | bool):
        return a == b
    return bool(actual == expected)


def evaluate_rule(
    rule: Mapping[str, Any], payload: Mapping[str, Any], run: Mapping[str, Any]
) -> bool:
    """One `{path, op, value?}`. A missing path fails every operator except `neq` and
    `exists: false`, so a condition on an absent field routes to `no` rather than raising."""
    op = rule.get("op")
    found = read_path_strict(str(rule.get("path", "")), payload, run)
    present = found is not _MISSING and found is not None
    if op == "exists":
        wanted = rule.get("value", True)
        return present == (wanted is not False)
    actual = None if found is _MISSING else found
    expected = rule.get("value")
    if op == "eq":
        return _equal(actual, expected)
    if op == "neq":
        return not _equal(actual, expected)
    if not present:
        return False
    if op in ("gt", "gte", "lt", "lte"):
        a, b = _number(actual), _number(expected)
        if a is None or b is None:
            return False
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[str(op)]
    if op == "contains":
        if isinstance(actual, str):
            return isinstance(expected, str) and expected.lower() in actual.lower()
        if isinstance(actual, list):
            return any(_equal(item, expected) for item in actual)
        return False
    if op == "in":
        return isinstance(expected, list) and any(_equal(actual, item) for item in expected)
    return False


def evaluate_condition(
    config: Mapping[str, Any], payload: Mapping[str, Any], run: Mapping[str, Any]
) -> bool:
    """`match: all` is an AND, `any` an OR. An empty rule list is refused at publish, and is
    `False` here so an unvalidated graph can never route everything to `yes`."""
    rules = config.get("rules")
    if not isinstance(rules, list) or not rules:
        return False
    results = (evaluate_rule(r, payload, run) for r in rules if isinstance(r, Mapping))
    return any(results) if config.get("match") == "any" else all(results)


# ------------------------------------------------------------------------- wait arithmetic


def _parse_hhmm(value: Any) -> time | None:
    if not isinstance(value, str):
        return None
    match = _HHMM.match(value)
    return time(int(match.group(1)), int(match.group(2))) if match else None


def _zone(name: Any) -> ZoneInfo | None:
    if not isinstance(name, str) or not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def in_window(moment: datetime, window: Mapping[str, Any]) -> bool:
    """Is `moment` inside the `[start, end)` local-time window? Overnight windows (`22:00`-`06:00`)
    wrap midnight. `start == end` is the whole day."""
    zone = _zone(window.get("timezone"))
    start, end = _parse_hhmm(window.get("start")), _parse_hhmm(window.get("end"))
    if zone is None or start is None or end is None:
        raise ValueError("invalid window")
    local = moment.astimezone(zone).time()
    if start == end:
        return True
    if start < end:
        return start <= local < end
    return local >= start or local < end


def wake_time(now: datetime, seconds: int, window: Mapping[str, Any] | None) -> datetime:
    """When a wait that started at `now` ends: `now + seconds`, pushed forward to the next moment
    the window is open. Always UTC, always `>= now + seconds`, and always inside the window."""
    base = now.astimezone(UTC) + timedelta(seconds=seconds)
    if not window:
        return base
    if in_window(base, window):
        return base
    zone = _zone(window["timezone"])
    start = _parse_hhmm(window["start"])
    if zone is None or start is None:
        raise ValueError("invalid window")
    local = base.astimezone(zone)
    candidate = datetime.combine(local.date(), start, tzinfo=zone)
    if candidate <= local:
        candidate = datetime.combine(local.date() + timedelta(days=1), start, tzinfo=zone)
    # A DST gap can make the wall-clock start not exist; normalising through UTC lands on the
    # first real instant at or after it.
    result = candidate.astimezone(UTC)
    while not in_window(result, window):
        result += timedelta(minutes=1)
    return result


# ------------------------------------------------------------------------------- retry policy

MAX_ATTEMPTS: Final = 4


def retry_delay_seconds(attempt: int) -> int:
    """Backoff before attempt `attempt + 1`: 60s, 2m, 4m ... capped at an hour."""
    return int(min(60 * 2 ** max(attempt - 1, 0), 3600))


def should_retry(attempt: int) -> bool:
    return attempt < MAX_ATTEMPTS


# ------------------------------------------------------------------------- graph validation


def node_map(graph: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {n["id"]: n for n in graph.get("nodes", []) if isinstance(n, dict) and "id" in n}


def outgoing(graph: Mapping[str, Any], node_id: str) -> list[dict[str, Any]]:
    return [e for e in graph.get("edges", []) if isinstance(e, dict) and e.get("from") == node_id]


def trigger_event(graph: Mapping[str, Any]) -> str | None:
    for node in graph.get("nodes", []):
        if isinstance(node, dict) and node.get("type") == "trigger":
            event = (node.get("config") or {}).get("event_type")
            return event if isinstance(event, str) else None
    return None


def next_node_id(
    graph: Mapping[str, Any], node: Mapping[str, Any], *, label: str | None
) -> str | None:
    """The node after `node`. A condition follows its `yes`/`no` edge; anything else its only
    edge. `None` means the flow ends here."""
    edges = outgoing(graph, str(node["id"]))
    if node.get("type") == "condition":
        for edge in edges:
            if edge.get("label") == label:
                return str(edge["to"])
        return None
    return str(edges[0]["to"]) if edges else None


def _check_condition(node_id: str, config: Mapping[str, Any]) -> list[Problem]:
    problems: list[Problem] = []
    if config.get("match") not in ("all", "any"):
        problems.append(Problem(node_id, "condition_match", "match must be 'all' or 'any'"))
    rules = config.get("rules")
    if not isinstance(rules, list) or not rules:
        problems.append(Problem(node_id, "condition_rules", "add at least one rule"))
        return problems
    if len(rules) > MAX_RULES:
        problems.append(Problem(node_id, "condition_rules", f"at most {MAX_RULES} rules"))
    for i, rule in enumerate(rules[:MAX_RULES], start=1):
        if not isinstance(rule, dict):
            problems.append(Problem(node_id, "condition_rule", f"rule {i} must be an object"))
            continue
        if not path_is_valid(rule.get("path")):
            problems.append(
                Problem(
                    node_id, "condition_path", f"rule {i}: path must start with payload. or run."
                )
            )
        op = rule.get("op")
        if op not in OPS:
            problems.append(Problem(node_id, "condition_op", f"rule {i}: unknown operator {op!r}"))
        elif op != "exists" and "value" not in rule:
            problems.append(Problem(node_id, "condition_value", f"rule {i}: a value is required"))
        elif op == "in" and not isinstance(rule.get("value"), list):
            problems.append(Problem(node_id, "condition_value", f"rule {i}: 'in' needs a list"))
    return problems


def _check_wait(node_id: str, config: Mapping[str, Any]) -> list[Problem]:
    problems: list[Problem] = []
    seconds = config.get("seconds")
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, int)
        or not WAIT_MIN_SECONDS <= seconds <= WAIT_MAX_SECONDS
    ):
        problems.append(
            Problem(
                node_id,
                "wait_seconds",
                f"seconds must be a whole number between {WAIT_MIN_SECONDS} and {WAIT_MAX_SECONDS}",
            )
        )
    window = config.get("window")
    if window is not None:
        if not isinstance(window, dict):
            problems.append(Problem(node_id, "wait_window", "window must be an object"))
        elif (
            _parse_hhmm(window.get("start")) is None
            or _parse_hhmm(window.get("end")) is None
            or _zone(window.get("timezone")) is None
        ):
            problems.append(
                Problem(
                    node_id,
                    "wait_window",
                    "window needs start and end as HH:MM and a valid IANA timezone",
                )
            )
    return problems


def _check_send(node_id: str, config: Mapping[str, Any]) -> list[Problem]:
    problems: list[Problem] = []
    if config.get("channel") not in CHANNELS:
        problems.append(Problem(node_id, "send_channel", "channel must be whatsapp or email"))
    template = config.get("template")
    if not isinstance(template, dict) or not str(template.get("ref") or "").strip():
        problems.append(Problem(node_id, "send_template", "choose a template"))
    if not path_is_valid(config.get("recipient_path")):
        problems.append(
            Problem(node_id, "send_recipient", "recipient_path must start with payload. or run.")
        )
    variable_map = config.get("variable_map", {})
    if not isinstance(variable_map, dict) or not all(
        isinstance(k, str) and path_is_valid(v) for k, v in variable_map.items()
    ):
        problems.append(
            Problem(
                node_id,
                "send_variables",
                "variable_map must map each variable to a payload. or run. path",
            )
        )
    purpose = config.get("purpose")
    if purpose is not None and purpose not in PURPOSES:
        problems.append(
            Problem(node_id, "send_purpose", "purpose must be transactional or marketing")
        )
    kind = config.get("recipient_kind")
    if kind is not None and kind not in ("address", "contact_id"):
        problems.append(
            Problem(node_id, "send_recipient", "recipient_kind must be address or contact_id")
        )
    return problems


def validate_graph(
    graph: Any, *, subscribable_events: Collection[str]
) -> tuple[list[Problem], dict[str, Any] | None]:
    """Every structural problem in `graph`, and the graph normalised to plain dicts (or `None`
    when it is not even shaped like one). Nothing here knows about templates or connections:
    the service layer adds those problems, which need a database."""
    problems: list[Problem] = []
    if not isinstance(graph, dict):
        return [Problem(None, "graph_shape", "graph must be an object")], None
    nodes, edges = graph.get("nodes"), graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return [Problem(None, "graph_shape", "graph needs nodes and edges lists")], None
    if len(nodes) > MAX_NODES:
        return [Problem(None, "graph_size", f"at most {MAX_NODES} nodes")], None

    ids: set[str] = set()
    typed: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]:
            problems.append(Problem(None, "node_shape", "every node needs a string id"))
            continue
        node_id = node["id"]
        if node_id in ids:
            problems.append(
                Problem(node_id, "node_duplicate", f"node id {node_id!r} is used twice")
            )
            continue
        ids.add(node_id)
        if node.get("type") not in NODE_TYPES:
            problems.append(
                Problem(node_id, "node_type", f"unknown node type {node.get('type')!r}")
            )
            continue
        if not isinstance(node.get("config", {}), dict):
            problems.append(Problem(node_id, "node_config", "config must be an object"))
            continue
        typed[node_id] = node

    triggers = [n for n in typed.values() if n["type"] == "trigger"]
    if len(triggers) != 1:
        problems.append(
            Problem(
                triggers[1]["id"] if len(triggers) > 1 else None,
                "trigger_count",
                f"a flow needs exactly one trigger, found {len(triggers)}",
            )
        )

    clean_edges: list[dict[str, Any]] = []
    incoming: dict[str, int] = {}
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("from") not in ids or edge.get("to") not in ids:
            problems.append(
                Problem(None, "edge_dangling", "an edge points at a node that is not there")
            )
            continue
        if edge["from"] == edge["to"]:
            problems.append(Problem(edge["from"], "cycle", "a node cannot lead to itself"))
            continue
        if edge.get("label") not in (None, "yes", "no"):
            problems.append(Problem(edge["from"], "edge_label", "an edge label is yes or no"))
            continue
        clean_edges.append(edge)
        incoming[edge["to"]] = incoming.get(edge["to"], 0) + 1

    out: dict[str, list[dict[str, Any]]] = {}
    for edge in clean_edges:
        out.setdefault(edge["from"], []).append(edge)

    for node_id, node in typed.items():
        kind, config = node["type"], node.get("config") or {}
        edges_out = out.get(node_id, [])
        if kind == "trigger":
            event = config.get("event_type")
            if not isinstance(event, str) or event not in subscribable_events:
                problems.append(
                    Problem(node_id, "trigger_event", "choose an event this platform publishes")
                )
            if incoming.get(node_id):
                problems.append(
                    Problem(node_id, "trigger_incoming", "nothing may lead into the trigger")
                )
        elif kind == "condition":
            problems.extend(_check_condition(node_id, config))
            labels = sorted(str(e.get("label")) for e in edges_out)
            if labels != ["no", "yes"]:
                problems.append(
                    Problem(
                        node_id,
                        "condition_edges",
                        "a condition needs exactly one yes and one no edge",
                    )
                )
        elif kind == "wait":
            problems.extend(_check_wait(node_id, config))
        elif kind == "send_message":
            problems.extend(_check_send(node_id, config))
        elif kind == "create_task":
            title = config.get("title")
            if not isinstance(title, str) or not title.strip() or len(title) > 200:
                problems.append(
                    Problem(node_id, "task_title", "a task needs a title (200 characters at most)")
                )
        if kind == "end" and edges_out:
            problems.append(Problem(node_id, "end_outgoing", "nothing may follow an end"))
        if kind not in ("condition", "end") and len(edges_out) > 1:
            problems.append(
                Problem(node_id, "node_outgoing", "this step can lead to only one next step")
            )

    # Reachability and cycles, from the trigger only — a cycle in an island nothing reaches is
    # reported as unreachable instead.
    if len(triggers) == 1:
        start = triggers[0]["id"]
        seen: set[str] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(e["to"] for e in out.get(current, []))
        for node_id in typed:
            if node_id not in seen:
                problems.append(
                    Problem(
                        node_id, "node_unreachable", "this step is not reachable from the trigger"
                    )
                )
        if _has_cycle(start, out):
            problems.append(Problem(None, "cycle", "the flow loops back on itself"))

    normalised = {"nodes": list(typed.values()), "edges": clean_edges} if not problems else graph
    return problems, normalised


def _has_cycle(start: str, out: Mapping[str, list[dict[str, Any]]]) -> bool:
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in done:
            return False
        if node_id in visiting:
            return True
        visiting.add(node_id)
        for edge in out.get(node_id, []):
            if visit(edge["to"]):
                return True
        visiting.discard(node_id)
        done.add(node_id)
        return False

    return visit(start)
