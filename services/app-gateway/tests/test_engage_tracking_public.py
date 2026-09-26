"""The open/click tracking URLs (`/api/v1/engage/t/...`) are public at the gateway, and only they.

A mail client fetching a pixel or a recipient following a tracked link carries no bearer token;
the HMAC in the path is the whole of its authority and notification-service verifies it. The
prefix is matched against the *normalised* path (see `ENGAGE_PUBLIC_PATH_PREFIXES`), so a request
dressed up as `/t/../campaigns` is judged by where it routes, not by how it starts.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.config import settings
from app.main import ENGAGE_PUBLIC_PATH_PREFIXES, ENGAGE_TRACKING_PREFIX, policy
from platform_core.gateway import RouteTable
from tests.conftest import EchoUpstream, FakeRedis

SERVICE_DIR = Path(__file__).resolve().parent.parent
TABLES = {
    "dev": SERVICE_DIR / "routes.json",
    "prod": Path(__file__).resolve().parents[3] / "infrastructure/edge/routes.prod.json",
}
TRACKING = "/api/v1/engage/t/"


def test_the_tracking_prefix_is_public_next_to_unsubscribe() -> None:
    assert TRACKING in ENGAGE_PUBLIC_PATH_PREFIXES
    assert "/api/v1/engage/unsubscribe/" in ENGAGE_PUBLIC_PATH_PREFIXES


def test_no_public_prefix_reaches_an_authenticated_engage_route() -> None:
    """Every public prefix is a strict subtree; none is the engage root or a bare `/t` that would
    also match `/api/v1/engage/templates` or `/api/v1/engage/tokens`."""
    for prefix in ENGAGE_PUBLIC_PATH_PREFIXES:
        assert prefix.endswith("/")
        assert prefix != "/api/v1/engage/"
        assert not "/api/v1/engage/templates".startswith(prefix)
        assert not "/api/v1/engage/campaigns".startswith(prefix)


@pytest.mark.parametrize("table", sorted(TABLES))
@pytest.mark.parametrize("kind", ["o", "c"])
def test_both_route_tables_send_tracking_urls_to_the_engage_service(table: str, kind: str) -> None:
    route = RouteTable.from_file(TABLES[table]).resolve(f"{TRACKING}{kind}/some-token")
    assert route is not None
    assert route.product == "engage"


# ------------------------------------------------------------------------- its own rate limit

UNSUBSCRIBE = "/api/v1/engage/unsubscribe/"


def test_tracking_has_its_own_generous_limit_and_unsubscribe_keeps_the_shared_one() -> None:
    cfg = settings()
    assert ENGAGE_TRACKING_PREFIX == TRACKING
    assert policy.public_prefix_rate_limits == {TRACKING: cfg.engage_tracking_rate_limit_per_minute}
    assert cfg.engage_tracking_rate_limit_per_minute >= 10 * cfg.anonymous_rate_limit_per_minute
    assert UNSUBSCRIBE not in policy.public_prefix_rate_limits


async def test_a_recipient_behind_a_shared_address_is_not_refused_at_the_anonymous_limit(
    client: AsyncClient, redis: FakeRedis, upstream: EchoUpstream
) -> None:
    """The reported failure: a corporate NAT or a mailbox provider's image proxy is one address for
    many recipients, and the sixty-first request in a minute was a 429 instead of a redirect."""
    over = settings().anonymous_rate_limit_per_minute + 20
    for _ in range(over):
        assert (await client.get(f"{TRACKING}o/token")).status_code == 200
    assert len(upstream.calls) == over
    assert [k for k in redis.store if k.startswith("rl:anon:")] != []
    assert all(TRACKING in k for k in redis.store if k.startswith("rl:anon:"))


async def test_tracking_and_sign_in_do_not_spend_each_others_allowance(
    client: AsyncClient, redis: FakeRedis, upstream: EchoUpstream
) -> None:
    cfg = settings()
    login = {"email": "a@example.com", "password": "x"}
    await client.post("/api/v1/auth/login", json=login)
    await client.get(f"{TRACKING}o/token")
    shared = next(k for k in redis.store if k.startswith("rl:anon:") and TRACKING not in k)
    own = next(k for k in redis.store if TRACKING in k)

    # Sign-in and unsubscribe spent: both refused, tracking untouched.
    redis.store[shared] = str(cfg.anonymous_rate_limit_per_minute)
    assert (await client.post("/api/v1/auth/login", json=login)).status_code == 429
    assert (await client.get(f"{UNSUBSCRIBE}token")).status_code == 429
    assert (await client.get(f"{TRACKING}c/token")).status_code == 200

    # Tracking spent: refused at its own figure, with a retry hint; sign-in is not affected.
    redis.store[shared] = "0"
    redis.store[own] = str(cfg.engage_tracking_rate_limit_per_minute)
    spent = await client.get(f"{TRACKING}c/token")
    assert spent.status_code == 429 and spent.headers["retry-after"]
    assert (await client.post("/api/v1/auth/login", json=login)).status_code != 429
