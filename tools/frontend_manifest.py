#!/usr/bin/env python3
"""Facts about the frontend workspace that no linter looks at, checked over every package at once.

Three families, each one a way a green build hid something real:

**Tests are run, or there are none.** `vitest run --passWithNoTests` passes when it finds nothing —
so a package whose suite stopped being collected (a moved directory, a glob that no longer matches)
went green with zero tests, and one package (`opsgrids-internal`) carried the flag for months
because it genuinely had none, which is exactly how the flag becomes normal. The rule is now
structural rather than a flag someone remembers to remove:

* no `test` script contains `--passWithNoTests`, and no vitest config sets `passWithNoTests`;
* a package with a `test` script has at least one test file under `src/` — vitest exits non-zero on
  none, so this only says *why* the row would fail, before it does;
* a package with test files has a `test` script — tests nobody runs are not tests;
* the script is `vitest run`, never bare `vitest`, which waits for a keypress in CI.

A package with no tests and no `test` script (`opsgrids-config`, presets only) is legitimately none.

**Fonts are served from the app's own origin.** Every app once linked fonts.googleapis.com from its
`index.html`, so opening any OpsGrids screen made a third-party request before text could draw, and
the edge could not restrict `font-src` to `'self'`. No file in an app may name a Google Fonts host,
and every app that takes the Console stylesheet must also take the typeface it names
(`@opsgrids/ui/fonts`) — an app that forgot would silently render in the system face.

**The operator token and the tenant tokens never share a name.** ADR-0003 draws the operator/tenant
boundary with token audience and gateway; a browser-side slot that two apps both wrote to would let
a token cross it. So no two apps name the same session slot, only the operator console names one
with `operator` in it, only the operator console reaches `/auth/operator`, and every app names its
slot at all (the default is one shared by every app that forgets to).

    python tools/frontend_manifest.py     # part of make frontend-lint
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

OPERATOR_APP = "opsgrids-internal"
GOOGLE_FONT_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")
FONT_IMPORT = "@opsgrids/ui/fonts"

# Not generated, not dependencies: what a search of an app's own code should never wander into.
SKIPPED_DIRS = {"node_modules", "dist", "dist-buyer", ".turbo"}

SESSION_KEY = re.compile(r"""["'](opsgrids\.[a-z0-9-]+\.access_token)["']""")
TEST_FILE = re.compile(r"\.(test|spec)\.(ts|tsx)$")


def _packages(frontend: Path) -> list[Path]:
    return sorted(p.parent for p in frontend.glob("*/package.json"))


def _manifest(package: Path) -> dict[str, object]:
    data: dict[str, object] = json.loads((package / "package.json").read_text(encoding="utf-8"))
    return data


def _files(package: Path, *patterns: str) -> list[Path]:
    """Source files of a package, skipping build output and installed dependencies."""
    found: list[Path] = []
    for pattern in patterns:
        for path in package.rglob(pattern):
            if any(part in SKIPPED_DIRS for part in path.relative_to(package).parts):
                continue
            if path.is_file():
                found.append(path)
    return sorted(found)


def _source(package: Path) -> list[Path]:
    """The package's own non-test code: `src/**/*.ts(x)` minus tests and the vitest setup file."""
    return [
        path
        for path in _files(package, "*.ts", "*.tsx")
        if path.relative_to(package).parts[0] == "src"
        and not TEST_FILE.search(path.name)
        and path.name != "test-setup.ts"
    ]


# ------------------------------------------------------------------------------------------ tests


def test_problems(frontend: Path) -> list[str]:
    found: list[str] = []
    for package in _packages(frontend):
        name = package.name
        scripts = _manifest(package).get("scripts", {})
        assert isinstance(scripts, dict)
        script = scripts.get("test")
        tests = [
            path
            for path in _files(package, "*.ts", "*.tsx")
            if path.relative_to(package).parts[0] == "src" and TEST_FILE.search(path.name)
        ]

        if isinstance(script, str):
            if "passWithNoTests" in script:
                found.append(
                    f"{name}: `test` passes with no tests ({script!r}). A suite that stops being "
                    "collected must fail, not go green; remove the flag."
                )
            if not re.search(r"\bvitest run\b", script):
                found.append(
                    f"{name}: `test` is {script!r}; it must be `vitest run`, because a bare "
                    "`vitest` waits for a keypress in CI."
                )
            if not tests:
                found.append(
                    f"{name}: has a `test` script and no test files under src/ — vitest fails on "
                    "none found. Write a test, or remove the script if the package has none."
                )
        elif tests:
            found.append(
                f"{name}: has {len(tests)} test file(s) and no `test` script — nothing runs them."
            )

        # `vite*` also matches `vitest.config.ts`, so the globs overlap; a set says each file once.
        configs = {*package.glob("vite*.config.*"), *package.glob("vitest*.config.*")}
        for config in sorted(configs):
            if "passWithNoTests" in config.read_text(encoding="utf-8"):
                found.append(f"{name}: {config.name} sets passWithNoTests; remove it.")
    return found


# ------------------------------------------------------------------------------------------ fonts


def font_problems(frontend: Path) -> list[str]:
    found: list[str] = []
    for package in _packages(frontend):
        name = package.name
        for path in _files(package, "*.html", "*.css", "*.ts", "*.tsx"):
            text = path.read_text(encoding="utf-8")
            for host in GOOGLE_FONT_HOSTS:
                if host in text:
                    found.append(
                        f"{name}: {path.relative_to(package)} names {host}. Fonts are self-hosted "
                        "(@fontsource); a third-party request blocks first paint."
                    )

        dependencies = _manifest(package).get("dependencies", {})
        main = package / "src" / "main.tsx"
        if (
            isinstance(dependencies, dict)
            and "@opsgrids/ui" in dependencies
            and main.exists()
            and 'import "./index.css"' in main.read_text(encoding="utf-8")
            and FONT_IMPORT not in main.read_text(encoding="utf-8")
        ):
            found.append(
                f"{name}: src/main.tsx takes the Console stylesheet but not its typeface — add "
                f'`import "{FONT_IMPORT}";`, or the app draws in the system face.'
            )
    return found


# ---------------------------------------------------------------------------------- session slots


def session_problems(frontend: Path) -> list[str]:
    found: list[str] = []
    owners: dict[str, list[str]] = {}
    for package in _packages(frontend):
        name = package.name
        source = _source(package)
        keys = {
            key for path in source for key in SESSION_KEY.findall(path.read_text(encoding="utf-8"))
        }
        for key in keys:
            owners.setdefault(key, []).append(name)
            if ".operator." in key and name != OPERATOR_APP:
                found.append(
                    f"{name}: names the session slot {key!r}, which belongs to {OPERATOR_APP}. "
                    "An operator token and a tenant token must never share a slot."
                )
            if name == OPERATOR_APP and ".operator." not in key:
                found.append(f"{name}: session slot {key!r} does not say `operator`.")

        main = package / "src" / "main.tsx"
        if main.exists() and "configureSession(" not in main.read_text(encoding="utf-8"):
            found.append(
                f"{name}: src/main.tsx never calls configureSession(), so it reads and writes the "
                "default session slot every other app that forgot shares."
            )

        if name != OPERATOR_APP:
            for path in source:
                if "/auth/operator" in path.read_text(encoding="utf-8"):
                    found.append(
                        f"{name}: {path.relative_to(package)} reaches /auth/operator, a route "
                        f"only {OPERATOR_APP} may call — tenant code must never present to the "
                        "operator surface."
                    )

    for key, apps in sorted(owners.items()):
        if len(apps) > 1:
            found.append(f"session slot {key!r} is named by {', '.join(apps)}; each app owns one.")
    operator_has_slot = any(OPERATOR_APP in apps for apps in owners.values())
    if (frontend / OPERATOR_APP).exists() and not operator_has_slot:
        found.append(f"{OPERATOR_APP} names no session slot at all.")
    return found


def problems(frontend: Path) -> list[str]:
    return [*test_problems(frontend), *font_problems(frontend), *session_problems(frontend)]


def main() -> int:
    found = problems(FRONTEND)
    for problem in found:
        print(problem)
    if found:
        print(f"\n{len(found)} frontend manifest problem(s).")
        return 1
    print("tests are run or absent, fonts are self-hosted, session slots are distinct")
    return 0


if __name__ == "__main__":
    sys.exit(main())
