"""The production CRUD smoke reads the Customer 360 between the stage move and the first delete.

`e2e/edge_product_crud.py::flow_crm` is the owner's standard post-deploy check for the CRM: it
creates a company, a contact and a deal on the box and removes them. The 360's deal leg was a 404
in production for a long time and every other check passed, because nothing asked the 360 for its
`crm` section. One read-only step does, on data the flow has just made.

The script is not importable here (it signs up real organisations over ssh and http), so it is read
as source: this pins that the step exists, where it sits, and what it asserts. The owner runs the
script; an agent never does.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE = REPO_ROOT / "e2e" / "edge_product_crud.py"

STEP = "the 360 shows the deal and its CRM section"


def _flow_crm() -> ast.FunctionDef:
    tree = ast.parse(SMOKE.read_text(encoding="utf-8"))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "flow_crm"
    )


def _steps(flow: ast.FunctionDef) -> list[tuple[str, ast.Call]]:
    """`(step name, the _require call)` in source order. `ast.walk` is breadth-first, so sort."""
    steps: list[tuple[str, ast.Call]] = []
    for node in ast.walk(flow):
        is_require = (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_require"
            and len(node.args) >= 3
        )
        if not is_require:
            continue
        assert isinstance(node, ast.Call)
        name = node.args[1]
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            steps.append((name.value, node))
    steps.sort(key=lambda step: (step[1].lineno, step[1].col_offset))
    return steps


def test_the_crm_flow_reads_the_360_after_the_stage_move_and_before_the_first_delete() -> None:
    names = [name for name, _call in _steps(_flow_crm())]
    assert STEP in names, f"flow_crm has no step named {STEP!r}; its steps are {names}"
    assert (
        names.index("POST /deals/{id}/stage") < names.index(STEP) < names.index("delete the deal")
    )


def test_the_360_step_asserts_the_crm_section_and_not_just_the_status() -> None:
    (call,) = [call for name, call in _steps(_flow_crm()) if name == STEP]
    condition = ast.unparse(call.args[2])
    assert "r.status_code == 200" in condition
    assert "'crm' not in" in condition and "'unavailable'" in condition
    assert "'open_opportunities': 1" in condition
