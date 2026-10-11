"""The documents that describe OWN scope say what the code does (CRM overhaul R2.2, AC-2.2-9).

Until R2.2 the resolver registry was written and never read, `Principal.subject_ref` was documented
as "a rep id for CRM" when it is only ever an HR employee id, and the architecture page said each
service registers a resolver "declaring which column is the owner", as if `apply` consulted it.
This pins the corrected wording to the names that exist, so the next reader of any of those pages
is not told a CRM rep has an employee id, or that the registry is decoration.

It pins names (`OwnerSubject`, `own_ref`, `register_scope_resolver`), not prose.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"
REGISTER = REPO_ROOT / "docs" / "adr" / "open-questions-register.md"
AUTH = REPO_ROOT / "libs" / "platform-core" / "src" / "platform_core" / "auth.py"

STALE = "a rep id for CRM"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _line_starting(text: str, prefix: str) -> str:
    matches = [line for line in text.splitlines() if line.startswith(prefix)]
    assert len(matches) == 1, f"expected one line starting {prefix!r}, found {len(matches)}"
    return matches[0]


def test_architecture_names_the_membership_subject_and_its_accessor() -> None:
    text = _text(ARCHITECTURE)
    for name in ("OwnerSubject", "own_ref", "register_scope_resolver", "membership_id"):
        assert name in text, f"docs/architecture.md never mentions {name}"


def test_nothing_still_calls_subject_ref_a_rep_id_for_crm() -> None:
    assert STALE not in _text(ARCHITECTURE)
    assert STALE not in _text(AUTH)


def test_the_auth_module_describes_both_ids_and_who_chooses() -> None:
    module_docstring = ast.get_docstring(ast.parse(_text(AUTH))) or ""
    for name in ("OwnerSubject", "own_ref", "subject_ref", "membership_id"):
        assert name in module_docstring, f"platform_core.auth's docstring never mentions {name}"


def test_the_register_records_the_platform_mechanism_as_built() -> None:
    new_7 = _line_starting(_text(REGISTER), "| **NEW-7**")
    assert "OwnerSubject" in new_7
    assert "own_ref" in new_7
    assert "R2.2" in new_7

    row_20 = _line_starting(_text(REGISTER), "| 20 |")
    assert "NEW-7" in row_20
    assert "OwnerSubject" in row_20
