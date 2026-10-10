"""The CRM services' entrypoints: a relay of their own, and no second copy of it under another name.

`customer`, `deal` and `lead` each carried a `bin/cron` that was a byte-for-byte copy of the
scaffold default, `exec python -m app.relay`. None of them has a scheduled sweep (there is no
`app/cron.py`), and the outbox relay already has its own entrypoint, `bin/relay`, and runs in the
shared `edge-relay` container on the edge. A `bin/cron` that starts the relay is a trap: whoever
runs "the cron" of one of these services starts a second relay on the same outbox. The files were
removed; this pins that they do not come back as relay copies.

It does not say that a CRM service may never have a `bin/cron`: one with a real sweep runs
`app.cron`, and the relay-copy check lets that through. The other services' `bin/cron` files
are not this test's business.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infrastructure/edge/compose.prod.yml"

CRM_SERVICES = ("customer", "deal", "lead")

_EXEC_RELAY = re.compile(r"^\s*exec\s+python\s+-m\s+app\.relay\b", re.MULTILINE)


@pytest.mark.parametrize("service", CRM_SERVICES)
def test_no_crm_service_has_a_bin_cron_that_is_a_second_copy_of_the_relay(service: str) -> None:
    cron = REPO_ROOT / "services" / service / "bin" / "cron"
    is_a_relay_copy = cron.is_file() and _EXEC_RELAY.search(cron.read_text(encoding="utf-8"))
    assert not is_a_relay_copy, (
        f"services/{service}/bin/cron starts the outbox relay: the relay is bin/relay, and "
        "this service has no scheduled sweep. Delete the file, or point it at a real app/cron.py."
    )


@pytest.mark.parametrize("service", CRM_SERVICES)
def test_every_crm_service_keeps_its_own_relay_entrypoint(service: str) -> None:
    relay = REPO_ROOT / "services" / service / "bin" / "relay"
    assert relay.is_file(), f"services/{service}/bin/relay is gone: the outbox has no entrypoint"
    assert _EXEC_RELAY.search(relay.read_text(encoding="utf-8")), service


def test_no_crm_container_runs_a_bin_cron() -> None:
    """Nothing in the production compose file starts `./bin/cron` from a CRM image, so removing the
    files changes no container. The relay for all three runs in `edge-relay`, by schema name."""
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))["services"]
    for name, spec in compose.items():
        image = str(spec.get("image", ""))
        if image not in {f"opsgrids-edge/{service}" for service in CRM_SERVICES}:
            continue
        command = spec.get("command") or []
        words = command if isinstance(command, list) else str(command).split()
        assert "./bin/cron" not in words, f"compose service {name} runs ./bin/cron from {image}"
