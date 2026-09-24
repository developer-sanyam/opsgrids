"""The compose side of the memory-headroom slice's round-2 consolidation: `operations`,
`storefront` and `document` run their periodic sweep inside their own API container, so the three
`*-cron` compose services that used to carry them must stay gone, and the API containers must be
told to run the sweep on the edge.

The service-side wiring (lifespan starts the sweep when `run_sweeps_in_api` is on, does not when
off, survives a failing tick, cancels cleanly) is tested in each service's own
`tests/test_main_sweep_wiring.py`. This file pins the two facts only the compose file can get
wrong:

- a sweep that runs in the API *and* in a leftover `*-cron` container is not a crash, but it is
  the ~85 MB per service this consolidation exists to give back;
- an API container that is not told `RUN_SWEEPS_IN_API=true` runs *no* sweep at all — the
  Settings default is deliberately `false` everywhere else (dev, tests, a standalone `bin/cron`),
  so a compose typo here silently stops reconciliation, domain verification or purging with
  nothing failing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infrastructure/edge/compose.prod.yml"

SWEEPING_SERVICES = ("operations", "storefront", "document")


def _services() -> dict[str, dict]:
    return yaml.safe_load(COMPOSE_FILE.read_text())["services"]


@pytest.mark.parametrize("service", SWEEPING_SERVICES)
def test_the_dedicated_cron_container_is_gone(service: str) -> None:
    assert f"{service}-cron" not in _services(), (
        f"{service}-cron is back in compose.prod.yml — its sweep runs inside the `{service}` API "
        f"container now (RUN_SWEEPS_IN_API); a second container is ~85 MB for nothing."
    )


@pytest.mark.parametrize("service", SWEEPING_SERVICES)
def test_the_api_container_is_told_to_run_the_sweep(service: str) -> None:
    env = _services()[service].get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    value = str(env.get("RUN_SWEEPS_IN_API", ""))
    assert re.fullmatch(r"\$\{RUN_SWEEPS_IN_API:-true\}|true", value), (
        f"`{service}` does not enable its in-process sweep on the edge "
        f"(RUN_SWEEPS_IN_API={value!r}) — with the Settings default of false that means the sweep "
        f"runs nowhere."
    )


@pytest.mark.parametrize("service", SWEEPING_SERVICES)
def test_each_service_setting_defaults_to_off_outside_the_edge(service: str) -> None:
    """Off by default is what keeps dev, the test suite and a manual `bin/cron` from running the
    sweep twice; compose turns it on. If the default ever flips, the compose value above becomes
    redundant and the *manual* `docker compose run ... ./bin/cron` runbook step (document's
    orphan scan) would double up with the API's own."""
    config = (REPO_ROOT / "services" / service / "app" / "config.py").read_text()
    assert re.search(r"^\s+run_sweeps_in_api:\s*bool\s*=\s*False\s*$", config, re.MULTILINE), (
        f"services/{service}/app/config.py no longer defaults run_sweeps_in_api to False"
    )


def test_bin_cron_is_kept_for_the_three_services() -> None:
    """`bin/cron` stays: it is the manual/one-off entrypoint (the document orphan scan, an
    operator re-running a sweep, or putting a sweep back in its own container by setting
    RUN_SWEEPS_IN_API=false)."""
    for service in SWEEPING_SERVICES:
        assert (REPO_ROOT / "services" / service / "bin" / "cron").is_file(), service
