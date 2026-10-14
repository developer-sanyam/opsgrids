"""`make boundaries` runs import-linter once per service with one shared config.

import-linter fails a run on any contract naming a module the service does not have, so contracts
that only operations can satisfy (its inventory, orders, MRP packages) live in
`.importlinter.operations`, which the Makefile target selects for that service alone. These tests
keep the split honest: the shared contracts are identical in both files, the shared file names no
module specific to one service, and the Makefile selects per-service configs by file name.
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"


def _parse(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)
    return parser


def _contracts(path: Path) -> dict[str, dict[str, str]]:
    parser = _parse(path)
    return {
        name.removeprefix("importlinter:contract:"): dict(parser[name])
        for name in parser.sections()
        if name.startswith("importlinter:contract:")
    }


def test_operations_config_repeats_every_shared_contract_verbatim() -> None:
    shared = _contracts(ROOT / ".importlinter")
    operations = _contracts(ROOT / ".importlinter.operations")
    assert shared, "the shared config has no contracts"
    for name, options in shared.items():
        assert operations.get(name) == options, f"contract {name!r} differs between the configs"
    assert set(operations) > set(shared), "operations' config adds nothing to the shared one"


def test_root_packages_match_between_the_configs() -> None:
    shared = _parse(ROOT / ".importlinter")["importlinter"]["root_packages"]
    operations = _parse(ROOT / ".importlinter.operations")["importlinter"]["root_packages"]
    assert shared == operations


def test_shared_contracts_only_name_modules_every_service_has() -> None:
    """A layer in parentheses is optional; a bare module name must exist in every service. The
    shared config may therefore only use bare `app` / `platform_core` or parenthesised layers."""
    for name, options in _contracts(ROOT / ".importlinter").items():
        for key in ("source_modules", "forbidden_modules", "layers"):
            for line in options.get(key, "").splitlines():
                token = line.strip()
                if not token or token.startswith("("):
                    continue
                assert token in {"app", "platform_core"}, (
                    f"shared contract {name!r} names {token!r}, which not every service has — "
                    "put it in a per-service config"
                )


def test_makefile_selects_a_per_service_config_by_file_name() -> None:
    makefile = (ROOT / "Makefile").read_text()
    target = makefile[makefile.index("\nboundaries:") :].split("\n\n", 1)[0]
    assert "--config $$cfg" in target
    assert re.search(r'\[ -f "\.importlinter\.\$\$s" \]', target), target
    for config in ROOT.glob(".importlinter.*"):
        service = config.suffix.removeprefix(".")
        assert (SERVICES / service).is_dir(), f"{config.name} is for a service that does not exist"
