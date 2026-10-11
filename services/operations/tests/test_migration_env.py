"""The migration environment commits each revision on its own.

One transaction around a multi-revision upgrade keeps every ACCESS EXCLUSIVE lock an early
revision took (ADD COLUMN, ADD CONSTRAINT on busy tables) until the whole chain finishes, while
the old app is still serving. `transaction_per_migration` (on for an upgrade; a downgrade stays
atomic) releases them revision by revision.
"""

from __future__ import annotations

import ast
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / "migrations" / "env.py"


def test_each_revision_runs_in_its_own_transaction() -> None:
    calls = [
        node
        for node in ast.walk(ast.parse(ENV.read_text()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "configure"
        and any(k.arg == "connection" for k in node.keywords)
    ]
    assert calls, "no online context.configure(connection=...) in env.py"
    for call in calls:
        assert "transaction_per_migration" in {k.arg for k in call.keywords}
