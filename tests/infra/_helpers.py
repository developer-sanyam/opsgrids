"""Helpers the `tests/infra` modules share.

Imported as a sibling (`from _helpers import ...`), the way `_purge_orgs_world` is: the directory
has no `__init__.py`, so pytest puts it on `sys.path`.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from conftest import GitRepo

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGE_DIR = REPO_ROOT / "infrastructure/edge"
COMPOSE_FILE = EDGE_DIR / "compose.prod.yml"
BUDGET_FILE = EDGE_DIR / "memory-budget.json"
LIB_DIR = EDGE_DIR / "lib"

sys.path.insert(0, str(LIB_DIR))
from memory_budget_totals import load_budget  # noqa: E402

# --------------------------------------------------------------------------- real-database opt-in
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})


def real_database_dsn(env_var: str) -> str | None:
    """The DSN named by `env_var`, or `None` (the test then skips) when it is unset.

    These tests insert and delete rows, and the purge tools they drive issue `DELETE` against
    organisations and GPS captures. So a DSN is refused, loudly, at collection time (an error, not
    a skip) unless it names a throwaway database on this machine: not the developer stack's shared
    `opsgrids` (`platform_core.testing.is_shared_dev_database`; the CI job's own container is the
    one exemption, and it uses `opsgrids_tooling` anyway) and not a host that is not loopback,
    which is what a production or staging URL would be. `make test-infra-db` builds the DSN.
    """
    from sqlalchemy.engine import make_url

    from platform_core.testing import is_shared_dev_database

    dsn = os.environ.get(env_var)
    if not dsn:
        return None
    url = make_url(dsn)
    if is_shared_dev_database(dsn):
        raise RuntimeError(
            f"{env_var} names the shared dev database {url.database!r}; refusing to run "
            "destructive tests against it. Use `make test-infra-db`, which creates a throwaway."
        )
    if (url.host or "") not in _LOCAL_HOSTS:
        raise RuntimeError(
            f"{env_var} points at host {url.host!r}; refusing to run destructive tests against "
            "a database that is not on this machine."
        )
    return dsn


# --------------------------------------------------------------------------- the compose file
def load_compose() -> dict[str, Any]:
    """`compose.prod.yml`, parsed."""
    rendered: dict[str, Any] = yaml.safe_load(COMPOSE_FILE.read_text())
    return rendered


def compose_services() -> dict[str, dict[str, Any]]:
    """The `services:` mapping of `compose.prod.yml`."""
    services: dict[str, dict[str, Any]] = load_compose()["services"]
    return services


# ------------------------------------------------------------------------- the memory budget
def memory_budget_json() -> dict[str, Any]:
    """`memory-budget.json`, read as plain JSON."""
    result: dict[str, Any] = json.loads(BUDGET_FILE.read_text())
    return result


def edge_budget() -> dict[str, Any]:
    """`memory-budget.json`, read through the same loader the preflight uses."""
    budget: dict[str, Any] = load_budget(BUDGET_FILE)
    return budget


def sellable_profiles(budget: dict[str, Any]) -> list[str]:
    """`memory-budget.json`'s own `profiles` list — every product this platform can sell, which is
    exactly the set the box's `COMPOSE_PROFILES` enables when all of them are on."""
    return list(budget["profiles"])


# ------------------------------------------------------------------------------ small readers
def read_text(path: Path) -> str:
    return path.read_text()


def parse_python(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


def baseline_for(files: dict[str, str]) -> Callable[[GitRepo], str]:
    """A `_baseline(git_repo)` for a test module whose scenarios all start from `files`."""

    def _baseline(git_repo: GitRepo) -> str:
        return git_repo.initial_commit(files, "baseline")

    return _baseline


# ------------------------------------------------------------------- the AST dependency scans
def python_files(roots: Iterable[Path]) -> list[Path]:
    """Every production `.py` file under `roots` — no tests, migrations or virtualenvs."""
    files: list[Path] = []
    for root in roots:
        for path in root.rglob("*.py"):
            parts = set(path.parts)
            if "tests" in parts or "migrations" in parts or ".venv" in parts:
                continue
            files.append(path)
    return files


def opens_a_transaction_and_yields(
    node: ast.AsyncFunctionDef, *, exclude_context_managers: bool
) -> bool:
    """An async-generator FastAPI dependency that opens a transaction (`.begin()`) and yields
    inside it. With `exclude_context_managers`, a function decorated `@asynccontextmanager` is
    not a dependency and is skipped."""
    yields = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(node))
    begins = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "begin"
        for n in ast.walk(node)
    )
    if exclude_context_managers:
        decorated_as_context_manager = any(
            (isinstance(d, ast.Name) and d.id == "asynccontextmanager")
            or (isinstance(d, ast.Attribute) and d.attr == "asynccontextmanager")
            for d in node.decorator_list
        )
        if decorated_as_context_manager:
            return False
    return yields and begins


def committing_dependency_names(
    trees: dict[Path, ast.Module], *, exclude_context_managers: bool
) -> set[str]:
    names: set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and opens_a_transaction_and_yields(
                node, exclude_context_managers=exclude_context_managers
            ):
                names.add(node.name)
    return names


# ----------------------------------------------------------------------------------- fakes
class DisposableEngine:
    """The one method of an async engine the tools call on the way out, recording that it ran."""

    disposed = False

    async def dispose(self) -> None:
        self.disposed = True
