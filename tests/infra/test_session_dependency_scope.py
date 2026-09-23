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

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = [REPO_ROOT / "services", REPO_ROOT / "libs" / "platform-core" / "src"]


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            parts = set(path.parts)
            if "tests" in parts or "migrations" in parts or ".venv" in parts:
                continue
            files.append(path)
    return files


def _opens_a_transaction_and_yields(node: ast.AsyncFunctionDef) -> bool:
    yields = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(node))
    begins = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "begin"
        for n in ast.walk(node)
    )
    decorated_as_context_manager = any(
        (isinstance(d, ast.Name) and d.id == "asynccontextmanager")
        or (isinstance(d, ast.Attribute) and d.attr == "asynccontextmanager")
        for d in node.decorator_list
    )
    return yields and begins and not decorated_as_context_manager


def _committing_dependencies(trees: dict[Path, ast.Module]) -> set[str]:
    names: set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and _opens_a_transaction_and_yields(node):
                names.add(node.name)
    return names


def _parse_all() -> dict[Path, ast.Module]:
    return {path: ast.parse(path.read_text(), filename=str(path)) for path in _python_files()}


def test_the_tenant_chokepoint_is_recognised_as_committing() -> None:
    """Guards the guard: if the scan stopped recognising `get_tenant_session`, every assertion
    below would pass vacuously."""
    assert "get_tenant_session" in _committing_dependencies(_parse_all())


def test_every_committing_dependency_is_function_scoped() -> None:
    trees = _parse_all()
    committing = _committing_dependencies(trees)
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
