"""Every `schedule_event(...)` call in the repo names a target this scheduler will accept.

The consumer's ownership check (`consumers._rejection_reason`) refuses any `target_event_type`
not prefixed with the requesting service's own name, and a refusal is logged and dropped, never
raised — so a caller that gets the prefix wrong fails silently in production: the request is
acked, no job row is written, and the event never fires. This test closes that gap statically:
it finds each call site, resolves the target constant the caller passes, and runs the real check
against the envelope that service's relay would publish (`producer` = its `service_name`).

Pure AST over other services' source: they cannot be imported here (each exposes its own `app`).
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app import services
from app.consumers import _rejection_reason

SERVICES = Path(__file__).resolve().parents[2]


def _service_name(service_dir: Path) -> str:
    tree = ast.parse((service_dir / "app" / "config.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "service_name"
            and isinstance(node.value, ast.Constant)
        ):
            return str(node.value.value)
    raise AssertionError(f"{service_dir.name}: no service_name in app/config.py")


def _module_constants(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        value = getattr(node, "value", None)
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            out[target.id] = value.value
    return out


def _call_sites() -> list[tuple[str, str, str]]:
    """(location, producer, target_event_type) for every `schedule_event(` call."""
    sites = []
    for path in sorted(SERVICES.glob("*/app/**/*.py")):
        source = path.read_text()
        if "schedule_event(" not in source:
            continue
        tree = ast.parse(source)
        constants = _module_constants(tree)
        producer = _service_name(SERVICES / path.relative_to(SERVICES).parts[0])
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "schedule_event"
            ):
                continue
            where = f"{path.relative_to(SERVICES)}:{node.lineno}"
            (arg,) = [k.value for k in node.keywords if k.arg == "target_event_type"]
            if isinstance(arg, ast.Constant):
                sites.append((where, producer, str(arg.value)))
            else:
                assert isinstance(arg, ast.Name) and arg.id in constants, (
                    f"{where}: target_event_type must be a literal or a module-level constant"
                )
                sites.append((where, producer, constants[arg.id]))
    return sites


SITES = _call_sites()


def test_the_scan_finds_the_known_callers() -> None:
    # A scan that silently finds nothing would pass everything below.
    assert {producer for _, producer, _ in SITES} >= {"finance", "notification", "operations"}


@pytest.mark.parametrize(("where", "producer", "target"), SITES, ids=[s[0] for s in SITES])
def test_every_scheduled_target_passes_the_ownership_check(
    where: str, producer: str, target: str
) -> None:
    organization_id = uuid.uuid4()
    envelope = {"producer": producer, "organization_id": str(organization_id)}
    request = services.ScheduleRequest(
        idempotency_key="k",
        due_at=datetime.now(UTC),
        requested_event_id=uuid.uuid4(),
        correlation_id=None,
        target_event_type=target,
        target_aggregate_type="x",
        target_aggregate_id=uuid.uuid4(),
        target_payload={"organization_id": str(organization_id)},
        target_actor=None,
    )
    assert _rejection_reason(envelope, request) is None, where
