"""The reveal rate budget has Redis in production.

`enforce_reveal_budget` in lead- and customer-service spends a per-user counter in Redis before a
real phone number or email leaves either service, and **fails closed**: with Redis unreachable a
reveal is a 503, `/health/ready` reports `degraded`, and nothing else in the service notices
(`services/*/tests/test_reveal_budget.py::TestRedisOutage`). That behaviour is only the intended
one if Redis is reachable in the first place — a service that is never handed a `REDIS_URL` falls
back to the library default (`localhost:6380`, the developer's port), cannot reach it from a
container, and refuses every reveal from the day it ships.

Rendered the way compose renders it — `yaml.safe_load` resolves the `<<: *service-env` merges — so
a refactor that moves either service off the shared environment block shows up here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infrastructure/edge/compose.prod.yml"
SERVICES_DIR = REPO_ROOT / "services"

#: The two services that hold the reveal budget.
REVEAL_SERVICES = ("lead", "customer")


def _rendered() -> dict[str, Any]:
    loaded = yaml.safe_load(COMPOSE_FILE.read_text())
    assert isinstance(loaded, dict)
    return loaded


def _service(name: str) -> dict[str, Any]:
    service = _rendered()["services"][name]
    assert isinstance(service, dict)
    return service


def _redis_host(url: str) -> str:
    match = re.match(r"^rediss?://(?:[^@/]*@)?([^:/]+)", url)
    assert match is not None, f"not a redis URL: {url!r}"
    return match.group(1)


def _services_using_redis() -> list[str]:
    """Every service whose `create_app(...)` declares `redis=True`: the lifespan opens a client
    for it and `/health/ready` reports it."""
    return sorted(
        path.parent.parent.name
        for path in SERVICES_DIR.glob("*/app/main.py")
        if re.search(r"^\s*redis=True\s*,?\s*$", path.read_text(), flags=re.MULTILINE)
    )


@pytest.mark.parametrize("name", REVEAL_SERVICES)
def test_the_reveal_services_are_declared_as_redis_users(name: str) -> None:
    assert name in _services_using_redis(), (
        f"{name}: create_app(...) must pass redis=True so the lifespan opens Redis and "
        "readiness reports it"
    )


@pytest.mark.parametrize("name", REVEAL_SERVICES)
def test_the_reveal_services_are_given_the_redis_the_box_runs(name: str) -> None:
    environment = _service(name).get("environment")
    assert isinstance(environment, dict), f"{name}: expected a mapping environment"
    url = environment.get("REDIS_URL")
    assert isinstance(url, str) and url, f"{name}: no REDIS_URL — every reveal would be a 503"
    assert url == _rendered()["x-service-env"]["REDIS_URL"]
    assert _redis_host(url) in _rendered()["services"], (
        f"{name}: REDIS_URL names a host that is not a compose service"
    )


@pytest.mark.parametrize("name", REVEAL_SERVICES)
def test_the_reveal_services_start_after_redis_is_healthy_and_share_its_network(name: str) -> None:
    service = _service(name)
    depends_on = service.get("depends_on")
    assert isinstance(depends_on, dict), f"{name}: expected a mapping depends_on"
    assert depends_on.get("redis") == {"condition": "service_healthy"}, name
    redis = _service("redis")
    assert set(service.get("networks", [])) & set(redis.get("networks", [])), (
        f"{name} and redis share no network"
    )


def test_every_service_that_uses_redis_is_handed_it_in_production() -> None:
    """The general rule the two above are instances of: a service that declares `redis=True` and
    is deployed here must carry `REDIS_URL`. (A service that is not in the compose file at all is
    not deployed by this stack and is not this test's business.)"""
    services = _rendered()["services"]
    missing = sorted(
        name
        for name in _services_using_redis()
        if name in services and "REDIS_URL" not in (services[name].get("environment") or {})
    )
    assert missing == [], missing
