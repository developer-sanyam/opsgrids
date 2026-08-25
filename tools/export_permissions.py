#!/usr/bin/env python3
"""Write every product's permission manifest to libs/contracts/permissions/, or check them.

The committed manifest is the contract — the same idea as the OpenAPI documents beside it.
Each service declares its permission catalogue and role templates in `app/catalogue.py`, and
that module deliberately cannot be imported by another service: a product declares its own
vocabulary, and account-service importing it would couple the two across the very boundary the
platform exists to keep clean. So the catalogue is exported here as *data*, at build time, and
account-service installs a product's manifest when the product is activated for an organization
(`core.product.activated` → `app/consumers.py`), without ever importing a foreign module.

A product may span several services — `hrms` is the employee service *and* the attendance
service, each carrying its own slice of the catalogue and its own copy of the shared role
templates (`HR_ADMIN`, `EMPLOYEE`). The manifest is per **product**, so those slices are merged
here: permissions are concatenated (a duplicate code across services is an error), and role
templates with the same code are merged by unioning their grants. Merging at export time is
what keeps `install_registration` simple — it installs one registration per product and never
needs to know how many services contributed to it.

One template per product is marked `is_admin`: the one granting the most of the product's own
catalogue (ties go to the first declared). That is the role account-service hands the
organization's owners on activation, so a newly activated product has an administrator from the
first minute.

Each service is read in its own subprocess, for the same reason tests, mypy and the OpenAPI
export loop per service: every service exposes a top-level `app`, so one process can hold
exactly one of them. Importing `app.catalogue` touches no database, broker or settings.

    python tools/export_permissions.py            # regenerate
    python tools/export_permissions.py --check    # fail if a committed manifest is stale
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"
CONTRACTS = ROOT / "libs" / "contracts" / "permissions"

MANIFEST_VERSION = 1

_SCOPE_RANK = {"OWN": 0, "TEAM": 1, "LOCATION": 2, "ORG": 3}


def services_with_catalogues() -> list[Path]:
    """Every service that declares a catalogue, discovered rather than listed."""
    return sorted(p for p in SERVICES.iterdir() if (p / "app" / "catalogue.py").is_file())


def dump_one(service: Path, target: Path) -> None:
    """Read `service`'s catalogue in a fresh interpreter and write its registrations."""
    subprocess.run(  # noqa: S603 - this interpreter, this file, a path we chose
        [sys.executable, __file__, "--one", str(target)],
        cwd=service,
        check=True,
    )


def write_registrations(target: Path) -> None:
    """The child side of `dump_one`: runs with the service directory as cwd."""
    # Imported by name rather than statically: which `app` this is depends on the cwd, and
    # there is nothing for a type checker to resolve from the tools directory.
    sys.path.insert(0, str(Path.cwd()))
    module = importlib.import_module("app.catalogue")

    registrations = []
    for name in sorted(vars(module)):
        if not name.endswith("_REGISTRATION"):
            continue
        reg = getattr(module, name)
        registrations.append(
            {
                "product": reg.product,
                "permissions": [
                    {
                        "module": spec.module,
                        "resource": spec.resource,
                        "action": spec.action,
                        "description": spec.description,
                        "default_scope": spec.default_scope,
                        "max_scope": spec.max_scope,
                        "is_dangerous": spec.is_dangerous,
                    }
                    for spec in reg.catalogue.specs
                ],
                "role_templates": [
                    {
                        "code": template.code,
                        "name": template.name,
                        "description": template.description,
                        "is_default": template.is_default,
                        "cross_product": template.cross_product,
                        "grants": [
                            {"code": grant.code, "scope": grant.scope} for grant in template.grants
                        ],
                    }
                    for template in reg.role_templates
                ],
            }
        )

    target.write_text(
        json.dumps({"service": Path.cwd().name, "registrations": registrations}),
        encoding="utf-8",
    )


def _merge_template(existing: dict[str, Any], incoming: dict[str, Any], product: str) -> None:
    """Fold one service's copy of a shared role template into the merged one.

    Grants are unioned; where two services grant the same permission at different scopes the
    wider one wins, because two rows for one permission would violate the role's unique
    constraint at install time. Identity fields must agree — two services disagreeing about
    whether `EMPLOYEE` is the default role is a bug to fix at the source, not to paper over.
    """
    for field in ("name", "is_default", "cross_product"):
        if existing[field] != incoming[field]:
            raise SystemExit(
                f"role template {existing['code']!r} of product {product!r} disagrees on "
                f"{field!r} across services: {existing[field]!r} vs {incoming[field]!r}"
            )
    if incoming["description"] and incoming["description"] not in existing["description"]:
        existing["description"] = f"{existing['description']} {incoming['description']}".strip()
    by_code: dict[str, str] = {g["code"]: g["scope"] for g in existing["grants"]}
    for grant in incoming["grants"]:
        held = by_code.get(grant["code"])
        if held is None or _SCOPE_RANK[grant["scope"]] > _SCOPE_RANK[held]:
            by_code[grant["code"]] = grant["scope"]
    existing["grants"] = [{"code": c, "scope": s} for c, s in sorted(by_code.items())]


def merge(dumps: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """One manifest per product, merged across every service that declares a slice of it."""
    manifests: dict[str, dict[str, Any]] = {}
    for dump in dumps:
        for reg in dump["registrations"]:
            product = reg["product"]
            manifest = manifests.setdefault(
                product,
                {
                    "manifest_version": MANIFEST_VERSION,
                    "product": product,
                    "sources": [],
                    "permissions": [],
                    "role_templates": [],
                },
            )
            manifest["sources"].append(dump["service"])

            known = {(p["module"], p["resource"], p["action"]) for p in manifest["permissions"]}
            for spec in reg["permissions"]:
                key = (spec["module"], spec["resource"], spec["action"])
                if key in known:
                    raise SystemExit(
                        f"permission {product}.{'.'.join(key)} is declared by more than one "
                        "service — a code must have exactly one owner"
                    )
                manifest["permissions"].append(spec)

            templates = {t["code"]: t for t in manifest["role_templates"]}
            for template in reg["role_templates"]:
                if template["code"] in templates:
                    _merge_template(templates[template["code"]], template, product)
                else:
                    template["grants"] = sorted(template["grants"], key=lambda g: str(g["code"]))
                    manifest["role_templates"].append(template)

    for manifest in manifests.values():
        manifest["sources"] = sorted(manifest["sources"])
        manifest["permissions"] = sorted(
            manifest["permissions"], key=lambda p: (p["module"], p["resource"], p["action"])
        )
        _flag_admin(manifest)
    return manifests


def _flag_admin(manifest: dict[str, Any]) -> None:
    """Mark the admin-most template: the widest coverage of the product's own catalogue.

    Mirrors how the core catalogue works — `ORG_OWNER` is the template granting all of core —
    without needing every service to declare a flag. Ties go to the first declared template,
    which is where every catalogue in the platform puts its administrator.
    """
    admin: dict[str, Any] | None = None
    for template in manifest["role_templates"]:
        template["is_admin"] = False
        if template["cross_product"]:
            continue
        if admin is None or len(template["grants"]) > len(admin["grants"]):
            admin = template
    if admin is not None:
        admin["is_admin"] = True


def regenerate(into: Path) -> dict[str, Path]:
    dumps: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for service in services_with_catalogues():
            dump_path = Path(tmp) / f"{service.name}.json"
            dump_one(service, dump_path)
            dumps.append(json.loads(dump_path.read_text(encoding="utf-8")))

    written: dict[str, Path] = {}
    into.mkdir(parents=True, exist_ok=True)
    for product, manifest in sorted(merge(dumps).items()):
        target = into / f"{product}.json"
        # Sorted keys and a fixed indent so two runs on two machines produce one byte
        # sequence — the check below is a byte comparison, and a diff should mean the
        # catalogue changed.
        target.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        written[product] = target
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
        extra = (
            sorted(p.stem for p in CONTRACTS.glob("*.json") if p.stem not in fresh)
            if CONTRACTS.is_dir()
            else []
        )
    if stale or extra:
        for name in stale:
            print(f"libs/contracts/permissions/{name}.json does not match the code")
        for name in extra:
            print(f"libs/contracts/permissions/{name}.json matches no catalogue")
        print("\nThe permission manifest is committed. Run `make openapi` and commit the result.")
        return 1
    print(f"{len(fresh)} permission manifest(s) match the code")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or check the permission manifests")
    parser.add_argument("--check", action="store_true", help="fail if a manifest is stale")
    parser.add_argument("--one", metavar="TARGET", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.one:
        write_registrations(Path(args.one))
        return 0
    if args.check:
        return check()
    for name, path in regenerate(CONTRACTS).items():
        print(f"{name}: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
