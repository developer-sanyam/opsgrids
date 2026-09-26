"""The open/click tracking URLs (`/api/v1/engage/t/...`) are public at the gateway, and only they.

A mail client fetching a pixel or a recipient following a tracked link carries no bearer token;
the HMAC in the path is the whole of its authority and notification-service verifies it. The
prefix is matched against the *normalised* path (see `ENGAGE_PUBLIC_PATH_PREFIXES`), so a request
dressed up as `/t/../campaigns` is judged by where it routes, not by how it starts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.main import ENGAGE_PUBLIC_PATH_PREFIXES
from platform_core.gateway import RouteTable

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
