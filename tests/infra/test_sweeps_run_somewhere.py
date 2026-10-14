"""Every service sweep runs somewhere on the edge.

A sweep (`app/cron.py`, started by `bin/cron`) that no container runs is worse than no sweep: the
code, its tests and its docstrings all say the work is being done, and on the box nothing does it.
That is exactly what shipped for finance's filing-reclaim sweep and payment's stuck-charge sweep
in D3 round 2 — `finance-cron` and `payment-cron` were never added to compose — while the
consumers had stopped letting the queue retry a crashed claim, so the sweep was the *only* thing
that would ever finish one.

The rule this pins: every service whose `bin/cron` runs `app.cron` has its sweep run by one of
three shapes —

* its own compose service running `./bin/cron`;
* its worker: a compose service built from its image runs `./bin/worker`, `bin/worker` runs
  `app.worker`, and `app/worker.py` imports the sweep from `app.cron` (the memory-budget
  erratum's folds — subscription, account, approval — and D3's — finance, payment);
* its API: the API container's environment turns `RUN_SWEEPS_IN_API` on (`true`, or
  `${RUN_SWEEPS_IN_API:-true}`) — the memory-headroom slice's fold for document, operations and
  storefront, whose own tests pin the lifespan wiring.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from _helpers import compose_services

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infrastructure/edge/compose.prod.yml"

_EXEC_CRON = re.compile(r"^\s*exec\s+python\s+-m\s+app\.cron\b", re.MULTILINE)
_EXEC_WORKER = re.compile(r"^\s*exec\s+python\s+-m\s+app\.worker\b", re.MULTILINE)


def _services_with_a_sweep() -> list[str]:
    names = []
    for bin_cron in sorted((REPO_ROOT / "services").glob("*/bin/cron")):
        service_dir = bin_cron.parent.parent
        if _EXEC_CRON.search(bin_cron.read_text()) and (service_dir / "app" / "cron.py").is_file():
            names.append(service_dir.name)
    return names


def _command(service: dict) -> list[str]:
    command = service.get("command") or []
    return command if isinstance(command, list) else str(command).split()


def _runs(compose: dict[str, dict], service: str, entrypoint: str) -> bool:
    """Some compose service built from this service's image runs `entrypoint`. By image, not by
    name: `scheduler`'s worker container is named plain `scheduler`."""
    image = f"opsgrids-edge/{service}"
    return any(
        spec.get("image") == image and entrypoint in _command(spec) for spec in compose.values()
    )


_SWEEPS_IN_API_ON = re.compile(r"^(true|\$\{RUN_SWEEPS_IN_API:-true\})$")


def _environment(spec: dict) -> dict[str, str]:
    env = spec.get("environment") or {}
    if isinstance(env, list):
        env = dict(str(item).split("=", 1) for item in env if "=" in str(item))
    return {str(k): str(v) for k, v in env.items()}


def _runs_sweeps_in_api(compose: dict[str, dict], service: str) -> bool:
    """An API container of this service's image (no worker or cron command) with
    `RUN_SWEEPS_IN_API` switched on."""
    image = f"opsgrids-edge/{service}"
    for spec in compose.values():
        if spec.get("image") != image:
            continue
        command = _command(spec)
        if "./bin/worker" in command or "./bin/cron" in command:
            continue
        if _SWEEPS_IN_API_ON.match(_environment(spec).get("RUN_SWEEPS_IN_API", "")):
            return True
    return False


def _sweep_runs(compose: dict[str, dict], service: str) -> bool:
    return (
        _runs(compose, service, "./bin/cron")
        or (_runs(compose, service, "./bin/worker") and _folded_into_worker(service))
        or _runs_sweeps_in_api(compose, service)
    )


def _folded_into_worker(service: str) -> bool:
    root = REPO_ROOT / "services" / service
    bin_worker = root / "bin" / "worker"
    worker = root / "app" / "worker.py"
    return (
        bin_worker.is_file()
        and bool(_EXEC_WORKER.search(bin_worker.read_text()))
        and worker.is_file()
        and "from app.cron import" in worker.read_text()
    )


SWEEPING = _services_with_a_sweep()


def test_the_sweeping_services_are_found() -> None:
    """Guards the guard: finance and payment carry the D3 sweeps this file exists for."""
    assert {"finance", "payment", "subscription"} <= set(SWEEPING)


@pytest.mark.parametrize("service", SWEEPING)
def test_every_sweep_runs_in_a_container(service: str) -> None:
    assert _sweep_runs(compose_services(), service), (
        f"services/{service}/app/cron.py has a sweep and nothing on the edge runs it: add a "
        f"`{service}-cron` compose service on ./bin/cron, run it from app/worker.py "
        f"(bin/worker -> app.worker) in `{service}-worker`, or set RUN_SWEEPS_IN_API on its API "
        "container."
    )


@pytest.mark.parametrize(
    ("environment", "runs"),
    [
        ({"RUN_SWEEPS_IN_API": "${RUN_SWEEPS_IN_API:-true}"}, True),
        (["RUN_SWEEPS_IN_API=true"], True),
        ({"RUN_SWEEPS_IN_API": "false"}, False),
        ({}, False),
    ],
)
def test_a_sweep_folded_into_its_api_container_counts(environment: dict | list, runs: bool) -> None:
    """The memory-headroom shape: no `*-cron` container, the API runs the sweep in its lifespan
    when compose turns it on. Anything but an explicit on is the silent "runs nowhere" this file
    exists to catch."""
    compose = {"document": {"image": "opsgrids-edge/document", "environment": environment}}
    assert _sweep_runs(compose, "document") is runs


@pytest.mark.parametrize("service", ["finance", "payment"])
def test_the_d3_sweeps_are_folded_into_their_workers(service: str) -> None:
    """D3 round 3's decision: no new containers (no memory-budget change) — the reclaim and
    stuck-charge sweeps run in the existing workers, supervised."""
    compose = compose_services()
    assert not _runs(compose, service, "./bin/cron")
    assert _runs(compose, service, "./bin/worker")
    assert _folded_into_worker(service)
    assert "run_supervised" in (REPO_ROOT / "services" / service / "app" / "worker.py").read_text()
