"""`crm.lead.conversion_aborted` is built in one place.

Two paths stage the event -- `finalize_conversion`'s abort branch and `fail_conversion`'s
deleted-record bump -- and its committed schema is closed (`additionalProperties: false`). One
private builder, `_stage_conversion_aborted`, is the only code that writes the payload, so there
is exactly one place to keep it true to the schema and one place a producer-side test can call.
A second `publish_event` for this event type, or a payload written inline, would be a way to
drift from the contract that no test on the payload would notice.
"""

from __future__ import annotations

import ast
from pathlib import Path

SERVICES_PY = Path(__file__).resolve().parents[1] / "app" / "services.py"
EVENT_TYPE = "crm.lead.conversion_aborted"
EVENT_CONSTANT = "EVT_LEAD_CONVERSION_ABORTED"
BUILDER = "_stage_conversion_aborted"


def _names_the_event(value: ast.expr) -> bool:
    """`EVT_LEAD_CONVERSION_ABORTED`, or the event type spelled out as a string."""
    return (isinstance(value, ast.Name) and value.id == EVENT_CONSTANT) or (
        isinstance(value, ast.Constant) and value.value == EVENT_TYPE
    )


class _Sites(ast.NodeVisitor):
    """Where the event is published, where the builder is called and where the event type is
    spelled out as a string, each as the name of the function it sits in."""

    def __init__(self) -> None:
        self._stack: list[str] = []
        self.publish_sites: list[str] = []
        self.builder_callers: list[str] = []
        self.literal_sites: list[str] = []

    def _here(self) -> str:
        return self._stack[-1] if self._stack else "<module>"

    def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if node.value == EVENT_TYPE:
            self.literal_sites.append(self._here())

    def visit_Call(self, node: ast.Call) -> None:
        callee = node.func.id if isinstance(node.func, ast.Name) else None
        if callee == BUILDER:
            self.builder_callers.append(self._here())
        if callee == "publish_event" and any(
            kw.arg == "event_type" and _names_the_event(kw.value) for kw in node.keywords
        ):
            self.publish_sites.append(self._here())
        self.generic_visit(node)


def _sites() -> _Sites:
    sites = _Sites()
    sites.visit(ast.parse(SERVICES_PY.read_text(encoding="utf-8")))
    return sites


def test_the_event_is_published_from_exactly_one_place_and_it_is_the_builder() -> None:
    assert _sites().publish_sites == [BUILDER]


def test_the_event_type_is_spelled_out_once_as_the_constant_and_nowhere_else() -> None:
    """`event_type="crm.lead.conversion_aborted"` written inline would dodge the check above."""
    assert _sites().literal_sites == ["<module>"]


def test_the_builder_is_called_from_both_paths_that_abort_a_conversion() -> None:
    """`finalize_conversion` (status_changed, owner_changed) and `fail_conversion` with a bump
    (record_deleted) -- and from nowhere else."""
    assert sorted(_sites().builder_callers) == ["fail_conversion", "finalize_conversion"]
