#!/usr/bin/env python3
"""Refuse a dependency whose licence is not on the allow-list.

The list lives in tools/licenses.toml, next to the reasons. A package whose metadata is
missing or wrong is allowed *by name* there, with the licence that was checked by hand —
never by loosening the rule, because a rule loose enough to pass "UNKNOWN" passes
anything.

Runs over the synced environment, which `uv sync --frozen` makes identical to uv.lock, so
the answer here is the answer for the lock file.

    python tools/licenses.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "tools" / "licenses.toml"


def permitted(expression: str, allowed: set[str]) -> bool:
    """Whether every licence the package requires is allowed.

    pip-licenses reports either an SPDX expression ("Apache-2.0 OR BSD-3-Clause",
    "MIT AND PSF-2.0") or several classifiers joined by ";". AND and ";" mean the code
    is under all of them, so each part must be allowed; OR offers a choice, so one is
    enough. ";" is treated as AND because the metadata does not say which it meant.
    """
    for conjunct in re.split(r"\s+AND\s+|\s*;\s*", expression):
        if not any(choice.strip() in allowed for choice in re.split(r"\s+OR\s+", conjunct)):
            return False
    return True


def installed() -> list[dict[str, str]]:
    """Every distribution in the environment, as pip-licenses sees it."""
    out = subprocess.run(
        [sys.executable, "-m", "piplicenses", "--from=mixed", "--format=json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows: list[dict[str, str]] = json.loads(out)
    return rows


def main() -> int:
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    allowed = set(config["allowed"])
    by_name: dict[str, str] = config.get("packages", {})

    rows = installed()
    refused = [
        row for row in rows if row["Name"] not in by_name and not permitted(row["License"], allowed)
    ]
    for row in refused:
        print(f"{row['Name']} {row['Version']}: {row['License']}")
    if refused:
        print(
            f"\n{len(refused)} package(s) carry a licence not in {CONFIG.relative_to(ROOT)}."
            " Check the licence; if it is permissive, add its spelling to `allowed`. If the"
            " metadata is wrong, allow the package by name under [packages] with the reason."
        )
        return 1
    print(f"{len(rows)} packages, every licence on the allow-list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
