"""The step-up marker is one string in three services, and the reveal routes are only as safe as
the three agree (ADR-0023, second review round).

`POST /auth/mfa/step-up` puts `otp` **and** a marker in the token's `amr`; lead- and
customer-service require both on their reveal routes, because `otp` alone is what every session
that gave a code at sign-in carries for its whole life. The marker's value is duplicated rather than
shared: a shared constant would live in `platform-core`, and a change there redeploys every
service's image. So the copies are pinned to each other here — a rename in one place that misses
another turns every reveal into a 403 (or, worse, a guard that asks for a value no token has and a
review that misses it).

Read from the source with `ast`: `import app` is ambiguous across services (each exposes a
top-level `app`), so the three cannot be imported into one process.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

AUTH = REPO_ROOT / "services/auth/app/services.py"
GUARDS = {
    "lead": REPO_ROOT / "services/lead/app/api/routes.py",
    "customer": REPO_ROOT / "services/customer/app/api/routes.py",
}
EXPECTED = "stepup"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


def _string_constant(path: Path, name: str) -> str:
    """The module-level `NAME = "literal"` in `path`."""
    for node in _tree(path).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise AssertionError(f"{path.relative_to(REPO_ROOT)} defines no string constant {name}")


def test_the_three_copies_of_the_marker_are_equal_and_are_the_documented_value() -> None:
    values = {
        "auth": _string_constant(AUTH, "STEP_UP_AMR"),
        **{svc: _string_constant(path, "STEP_UP_AMR") for svc, path in GUARDS.items()},
    }
    assert values == {"auth": EXPECTED, "lead": EXPECTED, "customer": EXPECTED}


@pytest.mark.parametrize("service", sorted(GUARDS))
def test_the_reveal_guard_requires_otp_and_the_marker_together(service: str) -> None:
    """`require(PERMISSION, amr={"otp", STEP_UP_AMR})`: the whole set, in one call. `require`
    demands every member (`amr.issubset(...)`, pinned in auth's `test_step_up.py`), so this is what
    makes a login-time `otp` token insufficient."""
    calls = [
        node
        for node in ast.walk(_tree(GUARDS[service]))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "require"
        and any(k.arg == "amr" for k in node.keywords)
    ]
    assert len(calls) == 1, f"{service}: expected exactly one guarded `require(..., amr=...)`"
    [amr] = [k.value for k in calls[0].keywords if k.arg == "amr"]
    assert isinstance(amr, ast.Set)
    members = {
        (element.value if isinstance(element, ast.Constant) else f"<{ast.unparse(element)}>")
        for element in amr.elts
    }
    assert members == {"otp", "<STEP_UP_AMR>"}, f"{service}: the reveal guard asks for {members}"


def test_only_the_step_up_hands_the_marker_to_a_token() -> None:
    """Every call in auth-service that adds to a token's `amr` beyond its session's own
    (`extra_amr=`) is inside `step_up` — a sign-in, a refresh, an organization switch or a
    grace-window rotation cannot mint it, because none of them passes anything."""
    tree = _tree(AUTH)
    owners: list[str] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and any(k.arg == "extra_amr" for k in node.keywords):
                owners.append(function.name)
    # `ast.walk` visits a nested call from its enclosing function too, so de-duplicate.
    assert set(owners) == {"step_up"}, owners


def test_the_session_never_stores_it() -> None:
    """No assignment to a session's `amr` mentions the marker — not through the constant and not
    as a literal. (`verify_mfa` writes `otp`, and only that; `start_session` writes what it is
    told, which no caller tells it.)"""
    tree = _tree(AUTH)
    writes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "amr" for t in node.targets)
    ]
    assert writes, "the scan found no assignment to `.amr`: it is not looking at the right thing"
    for node in writes:
        mentioned = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)} | {
            n.value for n in ast.walk(node.value) if isinstance(n, ast.Constant)
        }
        assert "STEP_UP_AMR" not in mentioned and EXPECTED not in mentioned, ast.unparse(node)
