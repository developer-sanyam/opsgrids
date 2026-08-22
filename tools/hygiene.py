#!/usr/bin/env python3
"""Refuse a test suite that has quietly stopped testing something.

`@pytest.mark.skip` takes a test out of the run without anyone deciding it should stay
out; a `skipif` with no reason cannot be revisited because nobody knows what it was
waiting for; a `.only`-style marker — pytest has none, but the habit arrives from other
runners — would narrow the suite to one test. None of these fail a build on their own,
which is exactly why they need a check that does.

Everything else on the "stray debug" list already has a ruff rule — `print` (T20),
`breakpoint()` and `pdb` (T10), a bare `# noqa` or `# type: ignore` (PGH003, PGH004) — and
`make hygiene` runs those through ruff rather than spelling them out again here. This file
covers only what ruff has no rule for.

    python tools/hygiene.py            # every tracked Python file
    python tools/hygiene.py FILE...    # just these
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _dotted(node: ast.expr) -> str:
    """`pytest.mark.skip` from its AST, or "" when it is not a plain attribute chain."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


def findings(source: str) -> list[tuple[int, str]]:
    """(line, problem) for every marker in `source` that hides a test."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        # A bare `@pytest.mark.skip` is an Attribute; `@pytest.mark.skip(...)` wraps the same
        # Attribute in a Call. Matching the Attribute catches both forms once.
        if isinstance(node, ast.Attribute):
            name = _dotted(node)
            if name.endswith("mark.skip"):
                found.append((node.lineno, "pytest.mark.skip — delete the test or fix it"))
            elif name.endswith("mark.only"):
                found.append((node.lineno, "pytest.mark.only — narrows the suite to one test"))
        elif (
            isinstance(node, ast.Call)
            and _dotted(node.func).endswith("mark.skipif")
            and not any(kw.arg == "reason" for kw in node.keywords)
        ):
            found.append((node.lineno, "skipif without reason= — say what it is waiting for"))
    return found


def tracked_python_files() -> list[Path]:
    out = subprocess.run(
        # git is whichever one is on PATH, deliberately: there is no fixed location to pin.
        ["git", "ls-files", "-z", "--", "*.py"],  # noqa: S607
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return [ROOT / name for name in out.decode().split("\0") if name]


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv] or tracked_python_files()
    problems = 0
    for path in files:
        # This file names every marker it hunts, so it is the one file that cannot be held
        # to its own rule.
        if path.resolve() == Path(__file__).resolve():
            continue
        for line, problem in findings(path.read_text(encoding="utf-8")):
            print(f"{path.relative_to(ROOT)}:{line}: {problem}")
            problems += 1
    if problems:
        print(f"\n{problems} problem(s). A skipped test is a test that does not run.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
