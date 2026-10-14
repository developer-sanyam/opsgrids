"""The production CRUD smoke reads the Customer 360 between the stage move and the first delete.

`e2e/edge_product_crud.py::flow_crm` is the owner's standard post-deploy check for the CRM: it
creates a company, a contact and a deal on the box and removes them. The 360's deal leg was a 404
in production for a long time and every other check passed, because nothing asked the 360 for its
`crm` section. One read-only step does, on data the flow has just made.

`flow_crm` takes its HTTP client as an argument and importing the script has no side effects (it
signs up real organisations only when `main` runs), so the flow is run here against a stub gateway
and nothing is sent anywhere. Reading the step as source text cannot tell a step that works from
one that passes on any answer, so it is run on the card production returned and on the cards it
must refuse. The owner runs the script against the box; an agent never does.
"""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE = REPO_ROOT / "e2e" / "edge_product_crud.py"
sys.path.insert(0, str(SMOKE.parent))

import edge_product_crud as smoke  # noqa: E402
from edge_lib import StepLog  # noqa: E402

STEP = "the 360 shows the deal and its CRM section"

#: What a box with the fixed deal leg answers for the deal the flow has just made.
GOOD_CARD = {
    "crm": {"stage": "qualified", "open_opportunities": 1, "hot": False},
    "unavailable": ["orders", "finance", "support"],
}


def _flow_crm() -> ast.FunctionDef:
    tree = ast.parse(SMOKE.read_text(encoding="utf-8"))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "flow_crm"
    )


def _steps(flow: ast.FunctionDef) -> list[tuple[str, ast.Call]]:
    """`(step name, the `require` call)` in source order. `ast.walk` is breadth-first, so sort."""
    steps: list[tuple[str, ast.Call]] = []
    for node in ast.walk(flow):
        is_require = (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "require"
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


def _gateway(card: dict[str, Any], card_status: int) -> Callable[[httpx.Request], httpx.Response]:
    """The routes `flow_crm` calls, answered as the services answer them on a good box, with the
    360 answering `card`. Any other route answers 599, so a wrong URL aborts the flow instead of
    passing by luck."""
    simple: dict[tuple[str, str], tuple[int, dict[str, Any] | None]] = {
        ("POST", "/api/v1/customers/owners"): (201, {"id": "owner-1"}),
        ("POST", "/api/v1/customers"): (201, {"id": "company-1"}),
        ("POST", "/api/v1/deals"): (201, {"id": "deal-1", "stage": "lead"}),
        ("POST", "/api/v1/deals/deal-1/stage"): (200, {"stage": "qualified"}),
        ("GET", "/api/v1/customers/contact-1/360"): (card_status, card),
        ("POST", "/api/v1/deals/activities"): (201, {}),
        ("DELETE", "/api/v1/deals/deal-1"): (204, None),
    }
    contact_deleted = False

    def answer(request: httpx.Request) -> httpx.Response:
        nonlocal contact_deleted
        route = (request.method, request.url.path)
        if route == ("POST", "/api/v1/contacts"):
            # `raw_phone` is the one accepted spelling, and the contact comes back masked.
            spoofed = "phone" in json.loads(request.content)
            masked = {"id": "contact-1", "phone": f"+91{smoke.BULLET * 6}3210"}
            return httpx.Response(422 if spoofed else 201, json=masked)
        if route == ("DELETE", "/api/v1/contacts/contact-1"):
            contact_deleted = True
            return httpx.Response(204)
        if route == ("DELETE", "/api/v1/customers/company-1"):
            return httpx.Response(204 if contact_deleted else 409, json={})
        status, body = simple.get(route, (599, {"unexpected": " ".join(route)}))
        return httpx.Response(status, json=body)

    return answer


def _run(log: StepLog, card: dict[str, Any], status: int = 200) -> None:
    """Run the real `flow_crm` against the stub gateway; `smoke.Abort` is how a step fails."""
    transport = httpx.MockTransport(_gateway(card, status))
    with httpx.Client(transport=transport, base_url="https://gateway.invalid") as client:
        smoke.flow_crm(client, log, {}, "crm")


def test_the_crm_flow_reads_the_360_after_the_stage_move_and_before_the_first_delete() -> None:
    names = [name for name, _call in _steps(_flow_crm())]
    assert STEP in names, f"flow_crm has no step named {STEP!r}; its steps are {names}"
    assert (
        names.index("POST /deals/{id}/stage") < names.index(STEP) < names.index("delete the deal")
    )


def test_the_flow_completes_on_the_card_a_box_with_the_fixed_deal_leg_returns() -> None:
    log = StepLog()
    _run(log, GOOD_CARD)
    assert log.ok
    assert STEP in [name for name, _ok, _detail in log.results]


@pytest.mark.parametrize(
    ("card", "status"),
    [
        pytest.param(
            {"crm": None, "unavailable": ["orders", "finance", "support", "crm"]},
            200,
            id="the card production returned: crm null and named unavailable",
        ),
        pytest.param({}, 500, id="the 360 fails outright"),
        pytest.param(
            {
                "crm": {"stage": None, "open_opportunities": 0, "hot": False},
                "unavailable": ["orders", "finance", "support"],
            },
            200,
            id="the deal leg answers but does not see the deal the flow just made",
        ),
    ],
)
def test_the_flow_stops_at_the_360_step_on_a_card_that_misses_the_deal(
    card: dict[str, Any], status: int
) -> None:
    log = StepLog()
    with pytest.raises(smoke.Abort) as aborted:
        _run(log, card, status)
    assert str(aborted.value) == STEP
    assert [name for name, ok, _detail in log.results if not ok] == [STEP]
