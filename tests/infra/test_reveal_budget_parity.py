"""lead- and customer-service spend one reveal budget, so the two copies of it must be one.

A person's reveals across the two routes are bounded together — that is the point of the budget —
which only holds while both services increment the same counters in the same way. The Redis script
and the key names are therefore duplicated on purpose (a shared constant would live in
`platform-core` and redeploy every service) and pinned equal here, the same way the step-up marker
is (`test_step_up_marker.py`). A script edited in one service and not the other would quietly give
each route a budget of its own, and double what one person can read.

Read with `ast`: `import app` is ambiguous across services, so the two cannot share a process.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES = {
    "lead": REPO_ROOT / "services/lead/app/services.py",
    "customer": REPO_ROOT / "services/customer/app/services.py",
}


def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


def _constant(path: Path, name: str) -> object:
    for node in _module(path).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
            and isinstance(node.value, ast.Constant)
        ):
            return node.value.value
    raise AssertionError(f"{path.relative_to(REPO_ROOT)} defines no constant {name}")


def _budget_function(path: Path) -> ast.AsyncFunctionDef:
    for node in _module(path).body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "enforce_reveal_budget":
            return node
    raise AssertionError(f"{path.relative_to(REPO_ROOT)} has no enforce_reveal_budget")


def test_the_two_services_run_the_same_script() -> None:
    scripts = {svc: _constant(path, "_REVEAL_BUDGET_SCRIPT") for svc, path in SERVICES.items()}
    assert isinstance(scripts["lead"], str) and scripts["lead"].strip()
    assert scripts["lead"] == scripts["customer"]


def test_the_two_services_use_the_same_window_and_timeout_constants() -> None:
    for name in ("_REVEAL_DAILY_WINDOW_SECONDS", "_REVEAL_BUDGET_TIMEOUT_SECONDS"):
        values = {svc: _constant(path, name) for svc, path in SERVICES.items()}
        assert values["lead"] == values["customer"], (name, values)


@pytest.mark.parametrize("service", sorted(SERVICES))
def test_the_counters_are_the_same_keys(service: str) -> None:
    """Both services build `rl:crm.reveal.{burst,daily}:<org>:<user>:<window>`, read off the
    f-string templates in the function, so a rename in one is a failure here."""
    templates = sorted(
        ast.unparse(node)
        for node in ast.walk(_budget_function(SERVICES[service]))
        if isinstance(node, ast.JoinedStr) and "rl:crm.reveal." in ast.unparse(node)
    )
    assert templates == [
        "f'rl:crm.reveal.burst:{who}:{now // burst_window}'",
        "f'rl:crm.reveal.daily:{who}:{now // daily_window}'",
    ]


@pytest.mark.parametrize("service", sorted(SERVICES))
def test_the_check_is_one_call_to_redis_under_one_timeout(service: str) -> None:
    """The point of the script: a single `eval`, inside a single `asyncio.timeout`, and no
    separate `incr`/`expire` that a timeout could land between (second review, m2)."""
    function = _budget_function(SERVICES[service])
    redis_calls = [
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"eval", "incr", "expire", "pipeline", "get", "set", "decr"}
    ]
    assert redis_calls == ["eval"], redis_calls
    timeouts = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "timeout"
    ]
    assert len(timeouts) == 1, "exactly one timeout around the whole check"
