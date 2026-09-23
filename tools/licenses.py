#!/usr/bin/env python3
"""Refuse a dependency whose licence is not on the allow-list.

The list lives in tools/licenses.toml, next to the reasons. A package whose metadata is
missing or wrong is allowed *by name* there, with the licence that was checked by hand —
never by loosening the rule, because a rule loose enough to pass "UNKNOWN" passes
anything.

Two ecosystems, one rule and one file:

* Python (default) runs over the synced environment, which `uv sync --frozen` makes
  identical to uv.lock, so the answer here is the answer for the lock file.
* `--npm` runs over what `pnpm licenses list --prod` reports for the frontend workspace —
  the packages that ship to a browser, as pnpm-lock.yaml resolves them. The allow-list is
  the `[npm]` table, because npm spells licences as SPDX identifiers and nothing else.

    python tools/licenses.py            # make licenses
    python tools/licenses.py --npm      # make frontend-licenses
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "tools" / "licenses.toml"
FRONTEND = ROOT / "frontend"


def permitted(expression: str, allowed: set[str]) -> bool:
    """Whether every licence the package requires is allowed.

    pip-licenses reports either an SPDX expression ("Apache-2.0 OR BSD-3-Clause",
    "MIT AND PSF-2.0") or several classifiers joined by ";". AND and ";" mean the code
    is under all of them, so each part must be allowed; OR offers a choice, so one is
    enough. ";" is treated as AND because the metadata does not say which it meant.

    npm wraps a compound expression in parentheses — "(MIT OR CC0-1.0)" — so a choice matches
    either as written or with its brackets dropped. As written comes first because classifier
    names carry brackets of their own: "ISC License (ISCL)". Anything more deeply nested fails
    closed: an unrecognised token is not on the list.
    """
    expression = expression.strip()
    if expression.startswith("(") and expression.endswith(")"):
        expression = expression[1:-1]
    for conjunct in re.split(r"\s+AND\s+|\s*;\s*", expression):
        choices = [choice.strip() for choice in re.split(r"\s+OR\s+", conjunct)]
        if not any(c in allowed or c.strip("()") in allowed for c in choices):
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


def npm_installed() -> list[dict[str, str]]:
    """Every production package of the frontend workspace, one row per name and version.

    `pnpm licenses list --json` groups packages by the licence they declare; this flattens it
    into the same Name / Version / License rows pip-licenses produces, so one check serves both.
    """
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise SystemExit("pnpm is not on PATH — install it (corepack enable) and run pnpm install")
    result = subprocess.run(  # noqa: S603 — a fixed argv, the binary resolved just above
        [pnpm, "licenses", "list", "--prod", "--json"],
        cwd=FRONTEND,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # pnpm reports a wrong Node version or a missing install on stdout as often as stderr.
        raise SystemExit(f"pnpm licenses list failed:\n{result.stdout}{result.stderr}")
    grouped: dict[str, list[dict[str, Any]]] = json.loads(result.stdout)
    rows: list[dict[str, str]] = []
    for licence, packages in grouped.items():
        for package in packages:
            for version in package.get("versions", []):
                rows.append({"Name": package["name"], "Version": version, "License": licence})
    return rows


def check(
    rows: list[dict[str, str]], allowed: set[str], by_name: dict[str, str], table: str
) -> int:
    refused = [
        row for row in rows if row["Name"] not in by_name and not permitted(row["License"], allowed)
    ]
    for row in refused:
        print(f"{row['Name']} {row['Version']}: {row['License']}")
    if refused:
        print(
            f"\n{len(refused)} package(s) carry a licence not in {CONFIG.relative_to(ROOT)}"
            f" ({table}). Check the licence; if it is permissive, add its spelling to `allowed`."
            " If the metadata is wrong, allow the package by name under its `packages` table"
            " with the reason."
        )
        return 1
    print(f"{len(rows)} packages, every licence on the allow-list ({table})")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--npm", action="store_true", help="check the frontend workspace")
    args = parser.parse_args(argv)

    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    if args.npm:
        npm: dict[str, Any] = config["npm"]
        return check(npm_installed(), set(npm["allowed"]), npm.get("packages", {}), "[npm]")
    return check(installed(), set(config["allowed"]), config.get("packages", {}), "Python")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
