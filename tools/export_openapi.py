#!/usr/bin/env python3
"""Write every service's OpenAPI document to libs/contracts/openapi/, or check them.

The committed document is the contract. The frontend generates its client from it, and a
reviewer can read a route change as a diff to a JSON file rather than by reconstructing
it from Python. `--check` is what CI runs: it regenerates into a temporary directory and
fails if any committed document differs, so a change to `schemas.py` cannot land without
the contract it implies.

Each service is built in its own subprocess. Every service exposes a top-level `app`, so
one process can hold exactly one of them; this is the same reason tests and mypy loop per
service. Building the app does not touch a database or Redis — `create_app` opens those
in the lifespan, which never runs here — so this works with no infrastructure at all.

    python tools/export_openapi.py            # regenerate
    python tools/export_openapi.py --check    # fail if a committed document is stale
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import tempfile
from pathlib import Path

from _per_service import run_one

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"
CONTRACTS = ROOT / "libs" / "contracts" / "openapi"


def services() -> list[Path]:
    """Every service directory with an app to build, discovered rather than listed."""
    return sorted(p for p in SERVICES.iterdir() if (p / "app" / "main.py").is_file())


def export_one(service: Path, target: Path) -> None:
    """Build `service`'s app in a fresh interpreter and write its document to `target`."""
    run_one(__file__, service, target)


def write_document(target: Path) -> None:
    """The child side of `export_one`: runs with the service directory as cwd."""
    # Imported by name rather than statically: which `app` this is depends on the cwd, and
    # there is nothing for a type checker to resolve from the tools directory.
    sys.path.insert(0, str(Path.cwd()))
    document = importlib.import_module("app.main").app.openapi()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Sorted keys and a fixed indent so two runs on two machines produce one byte sequence
    # — the check below is a byte comparison, and a diff should mean the API changed.
    target.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def regenerate(into: Path) -> dict[str, Path]:
    written: dict[str, Path] = {}
    for service in services():
        target = into / f"{service.name}.json"
        export_one(service, target)
        written[service.name] = target
    return written


def check() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        fresh = regenerate(Path(tmp))
        stale = [
            name
            for name, path in fresh.items()
            if not (CONTRACTS / path.name).is_file()
            or (CONTRACTS / path.name).read_bytes() != path.read_bytes()
        ]
    if stale:
        for name in stale:
            print(f"libs/contracts/openapi/{name}.json does not match the code")
        print("\nThe OpenAPI contract is committed. Run `make openapi` and commit the result.")
        return 1
    print(f"{len(fresh)} OpenAPI document(s) match the code")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or check the OpenAPI contracts")
    parser.add_argument("--check", action="store_true", help="fail if a document is stale")
    parser.add_argument("--one", metavar="TARGET", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.one:
        write_document(Path(args.one))
        return 0
    if args.check:
        return check()
    for name, path in regenerate(CONTRACTS).items():
        print(f"{name}: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
