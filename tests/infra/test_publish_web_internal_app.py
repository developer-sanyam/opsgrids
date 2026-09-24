"""`publish-web.sh` must know the staff console (`APP=internal`) the same way it knows every
other named product app — a case in its own app table, pointing at the workspace package that
actually builds it, and named in the usage/error text so `make edge-web APP=bogus` still lists it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "infrastructure/edge/publish-web.sh"


def _text() -> str:
    return SCRIPT.read_text()


def test_internal_is_a_case_in_the_app_table() -> None:
    text = _text()
    match = re.search(r"\ninternal\)\n(.*?)\n\t;;\n", text, re.DOTALL)
    assert match, "publish-web.sh has no `internal)` case in its app table"
    block = match.group(1)
    assert "frontend/opsgrids-internal" in block, (
        "the `internal` case does not point at frontend/opsgrids-internal"
    )
    assert 'build_script="build"' in block
    assert 'entry="index.html"' in block


def test_internal_is_named_in_the_unknown_app_error() -> None:
    text = _text()
    assert "unknown APP" in text
    unknown_line = next(line for line in text.splitlines() if "unknown APP" in line)
    assert "internal" in unknown_line, (
        "the unknown-APP error text does not list 'internal' — a typo would print a list that "
        "lies about what this script accepts"
    )


def test_opsgrids_internal_workspace_package_actually_exists() -> None:
    """The other half of the same drift: a case naming a package directory that was renamed or
    never existed would only be discovered the first time someone tries to publish it."""
    package_dir = REPO_ROOT / "frontend/opsgrids-internal"
    assert package_dir.is_dir(), f"{package_dir} does not exist"
    assert (package_dir / "package.json").is_file()
