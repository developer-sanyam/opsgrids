"""No stage key is spelled outside the default pipeline's own definition.

Before stages were data, six literals were compared all over the CRM: in the domain rules, the
service, the schemas, the model default, the internal conversion route and the customer 360's
"hot" rule. Each one is a place a customer-configured stage would behave as if it did not exist.
This scan holds the line: every `.py` file under `services/deal/app` except `pipeline.py` (where
the default pipeline is *defined*), and `services/customer/app/c1_360.py`, must not contain a
string constant equal to one of the six legacy keys. Docstrings and comments are not constants
equal to a key, so prose may still say "lead".

The scan matches whole constants, so four places that really do spell `lead` for another reason
are named here, each with the exact source line and why. A change to one of those lines, or a
fifth place, fails this test and asks for a decision rather than slipping past.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[2]
DEAL_APP = SERVICES / "deal" / "app"
CUSTOMER_360 = SERVICES / "customer" / "app" / "c1_360.py"

LEGACY_KEYS = frozenset(
    {"lead", "qualified", "proposal", "negotiation", "closed_won", "closed_lost"}
)

#: (path under `services/`, the stripped source line) -> why that text is not a stage key. The
#: lead service is the one caller of the internal conversion route, and `lead` is also the
#: `source` label a converted deal carries.
ALLOWED: dict[tuple[str, str], str] = {
    (
        "deal/app/api/internal.py",
        '_require_caller(request, "lead")',
    ): "the calling service's name (the route's caller allow-list)",
    (
        "deal/app/api/internal.py",
        'acting_as="lead",',
    ): "the calling service's name on the audit row",
    (
        "deal/app/services.py",
        'LEAD_CREATED_ROW = ("deal.created", "lead")',
    ): "the calling service's name on the creation audit row the cleanup's provenance check reads",
    (
        "deal/app/schemas.py",
        'source: str = Field(default="lead", max_length=60)',
    ): "the `source` label of a deal opened by a lead conversion",
}


def findings_in(source: str, relative: str) -> list[str]:
    """Every string constant in `source` equal to a legacy key, as `path:line: ...`, minus the
    allow-listed lines."""
    lines = source.splitlines()
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Constant) and node.value in LEGACY_KEYS):
            continue
        line = lines[node.lineno - 1].strip()
        if (relative, line) not in ALLOWED:
            found.append(f"{relative}:{node.lineno}: {node.value!r} in `{line}`")
    return sorted(found)


def scanned_files() -> list[Path]:
    deal = sorted(p for p in DEAL_APP.rglob("*.py") if p.name != "pipeline.py")
    return [*deal, CUSTOMER_360]


def test_no_legacy_stage_key_is_spelled_outside_the_default_pipeline() -> None:
    found: list[str] = []
    for path in scanned_files():
        relative = path.relative_to(SERVICES).as_posix()
        found.extend(findings_in(path.read_text(encoding="utf-8"), relative))
    assert not found, (
        "a stage key is compared or defaulted by literal; read the outcome from the "
        "PipelineSpec instead:\n" + "\n".join(found)
    )


def test_the_scan_covers_the_files_it_names() -> None:
    names = {p.relative_to(SERVICES).as_posix() for p in scanned_files()}
    assert "deal/app/domain.py" in names
    assert "deal/app/services.py" in names
    assert "deal/app/api/routes.py" in names
    assert "customer/app/c1_360.py" in names
    assert "deal/app/pipeline.py" not in names


@pytest.mark.parametrize("key", sorted(LEGACY_KEYS))
def test_the_scanner_finds_each_key(key: str) -> None:
    """The guard has teeth: a snippet that compares or defaults by each key is a finding."""
    assert findings_in(f'if stage == "{key}":\n    pass\n', "x.py")
    assert findings_in(f'STAGES = ("{key}", "other")\n', "x.py")
    assert findings_in(f'def f(stage: str = "{key}") -> None: ...\n', "x.py")


def test_prose_and_near_misses_are_not_findings() -> None:
    source = (
        '"""A lead becomes a deal; a lead is not a stage."""\n'
        "# lead qualified proposal negotiation closed_won closed_lost\n"
        'LABEL = "Lead"\n'
        'OTHER = "leads"\n'
        'LONGER = "closed_won_at"\n'
    )
    assert findings_in(source, "x.py") == []


def test_every_allow_listed_line_is_still_there() -> None:
    """An allow-list entry for text that no longer exists is a stale excuse: remove it."""
    for (relative, line), reason in ALLOWED.items():
        text = (SERVICES / relative).read_text(encoding="utf-8")
        assert line in {candidate.strip() for candidate in text.splitlines()}, (relative, reason)
