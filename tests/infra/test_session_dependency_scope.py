"""A transaction-owning dependency must commit before the response leaves.

FastAPI runs the exit code of a `yield` dependency *after* the response has been sent unless the
dependency is declared with `Depends(..., scope="function")`. For `get_tenant_session` — which
commits on the way out — that meant a client could hold a 201 for a row its very next request
could not see yet, and a commit that failed was reported to the client as a success. The e2e
suite's intermittent "just-created X is not found" failures were this.

This scans every service for async-generator dependencies that open a transaction
(`.begin()` + `yield`) and fails if any `Depends(<that function>)` omits `scope="function"`.
"""

from __future__ import annotations

import ast
from pathlib import Path

from _helpers import committing_dependency_names, python_files

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = [REPO_ROOT / "services", REPO_ROOT / "libs" / "platform-core" / "src"]


def _parse_all() -> dict[Path, ast.Module]:
    return {
        path: ast.parse(path.read_text(), filename=str(path)) for path in python_files(SOURCE_ROOTS)
    }


def test_the_tenant_chokepoint_is_recognised_as_committing() -> None:
    """Guards the guard: if the scan stopped recognising `get_tenant_session`, every assertion
    below would pass vacuously."""
    assert "get_tenant_session" in committing_dependency_names(
        _parse_all(), exclude_context_managers=True
    )


def test_every_committing_dependency_is_function_scoped() -> None:
    trees = _parse_all()
    committing = committing_dependency_names(trees, exclude_context_managers=True)
    offenders: list[str] = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Depends"
                and node.args
            ):
                continue
            target = node.args[0]
            name = target.id if isinstance(target, ast.Name) else None
            if name not in committing:
                continue
            scope = next((kw.value for kw in node.keywords if kw.arg == "scope"), None)
            if not (isinstance(scope, ast.Constant) and scope.value == "function"):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} Depends({name})")
    assert not offenders, (
        "these dependencies commit a transaction but would run their exit code after the "
        'response is sent — add scope="function":\n' + "\n".join(sorted(offenders))
    )
