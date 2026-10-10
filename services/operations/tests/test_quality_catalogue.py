"""The quality permissions and role templates (inventory plan §4): who can do what, which two are
dangerous, and that the committed manifest the ADR-0007 reconciler reads carries them."""

from __future__ import annotations

import json
from pathlib import Path

from app.catalogue import OPERATIONS, OPERATIONS_ROLE_TEMPLATES

PREFIX = "operations.quality.inspection."
READ, INSPECT, APPROVE, REJECT = (PREFIX + a for a in ("read", "inspect", "approve", "reject"))
MANIFEST = (
    Path(__file__).resolve().parents[3] / "libs" / "contracts" / "permissions" / "operations.json"
)


def role(code: str) -> dict[str, str]:
    template = next(t for t in OPERATIONS_ROLE_TEMPLATES if t.code == code)
    return {g.code: g.scope for g in template.grants if g.code.startswith(PREFIX)}


def test_the_four_permissions_exist_and_only_approve_and_reject_are_dangerous() -> None:
    specs = {
        s.code("operations"): s for s in OPERATIONS.specs if s.code("operations").startswith(PREFIX)
    }
    assert set(specs) == {READ, INSPECT, APPROVE, REJECT}
    assert {c for c, s in specs.items() if s.is_dangerous} == {APPROVE, REJECT}
    assert all(s.max_scope == "ORG" and s.default_scope == "ORG" for s in specs.values())


def test_the_inspector_inspects_but_cannot_decide() -> None:
    assert role("QC_INSPECTOR") == {READ: "ORG", INSPECT: "ORG"}


def test_the_manager_holds_all_four_and_the_admin_holds_them_through_all() -> None:
    everything = dict.fromkeys((READ, INSPECT, APPROVE, REJECT), "ORG")
    assert role("OPERATIONS_MANAGER") == everything
    assert role("OPERATIONS_ADMIN") == everything


def test_ordinary_users_and_workers_get_no_quality_permission() -> None:
    assert role("OPERATIONS_USER") == {}
    assert role("OPERATIONS_WORKER") == {}


def test_the_committed_manifest_carries_the_permissions_and_the_new_role() -> None:
    text = json.dumps(json.loads(MANIFEST.read_text()))
    for code in (READ, INSPECT, APPROVE, REJECT, "QC_INSPECTOR"):
        assert code in text, code
