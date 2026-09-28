"""BLOCKER 2 of the 2026-09-24 security review: the dev route table (`services/app-gateway/
routes.json`) and the production one (`infrastructure/edge/routes.prod.json`) are two separate
files by design (different upstream hosts — see the `$comment` in each), which means nothing
stops them from drifting. A `blocked` entry that exists only in dev is the specific way that
drift is dangerous: `/api/v1/auth/operator` was refused locally and openly reachable in prod,
because only the dev table had ever been told to refuse it.

These tests pin the one property that matters: every `blocked` prefix in the dev table has an
identical entry (same prefix, same `blocked` value) in the prod table. They do not require the
tables to be otherwise identical — upstream hosts differ on purpose, and prod may legitimately
lack a route dev has no compose profile for.

The second half of the file holds the same two tables against the app-gateway's *policy* and
against the service behind the one public provider webhook (Meta's WhatsApp callback): the public
path, both route tables, the body cap and the mount in notification cannot drift apart again.
"""

from __future__ import annotations

import functools
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_ROUTES = REPO_ROOT / "services/app-gateway/routes.json"
PROD_ROUTES = REPO_ROOT / "infrastructure/edge/routes.prod.json"


def _routes(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text())
    routes = data["routes"]
    assert isinstance(routes, list)
    return routes


def _blocked_prefixes(routes: list[dict[str, object]]) -> dict[str, bool]:
    return {
        str(route["prefix"]): bool(route.get("blocked", False))
        for route in routes
        if route.get("blocked")
    }


def test_the_dev_table_actually_blocks_the_operator_prefix() -> None:
    """Fails without BLOCKER 2's fix on the dev side — proves the fixture itself is not
    vacuous before asserting anything about parity."""
    assert _blocked_prefixes(_routes(DEV_ROUTES)).get("/api/v1/auth/operator") is True


def test_every_blocked_dev_prefix_is_blocked_in_prod_too() -> None:
    """The regression this whole file exists to catch: `/api/v1/auth/operator` (or any future
    `blocked` entry) present in dev only is a route that is openly reachable in production."""
    dev_blocked = _blocked_prefixes(_routes(DEV_ROUTES))
    prod_blocked = _blocked_prefixes(_routes(PROD_ROUTES))
    missing = {
        prefix: value for prefix, value in dev_blocked.items() if prod_blocked.get(prefix) != value
    }
    assert missing == {}, f"blocked in dev but not identically blocked in prod: {missing}"


def test_the_prod_operator_route_points_at_the_in_cluster_auth_host() -> None:
    """Parity in `blocked` is not enough on its own if the entry silently rots to point
    somewhere else — pin the upstream too, since that is what a hand-edit is most likely to
    get wrong."""
    prod_routes = _routes(PROD_ROUTES)
    matches = [r for r in prod_routes if r["prefix"] == "/api/v1/auth/operator"]
    assert len(matches) == 1
    assert matches[0]["upstream"] == "http://auth:8000"
    assert matches[0]["blocked"] is True


# ------------------------------------------------------------------------------------------
# The WhatsApp webhook, and Facebook Login's deauthorize / data-deletion callbacks alongside it.
# `routes.prod.json` routed `/api/v1/webhooks/whatsapp` through app-gateway while the gateway's
# policy only knew a root-path `/webhooks/whatsapp` (which a proxy that serves only `/api/...` can
# never match) and notification mounted the router only at the root — so every Meta delivery and
# handshake was a 401 at the gateway, or a 404 behind it. Nothing compared the *policy* or the
# *service* with the *production table*, which is how it shipped. These tests read the real policy
# and the real notification app (each in a subprocess inside its own service directory: `import
# app` is ambiguous across services, so neither can share this process) and hold them against both
# tables.
#
# Every assertion below is written against `EXPECTED_WEBHOOK_PATHS`/`EXPECTED_BODY_CAPS` — a
# mapping, not one hardcoded path — so a fourth callback is one entry in each mapping, not a new
# copy of every test in this section.
# ------------------------------------------------------------------------------------------

WHATSAPP = "/api/v1/webhooks/whatsapp"
DEAUTHORIZE = "/api/v1/webhooks/whatsapp/deauthorize"
DATA_DELETION = "/api/v1/webhooks/whatsapp/data-deletion"
DATA_DELETION_STATUS = "/api/v1/webhooks/whatsapp/data-deletion-status"
WEBHOOK_PREFIX = "/api/v1/webhooks/"

#: Meta documents WhatsApp Business Platform webhook payloads up to 3 MB. Above the gateway's
#: 2 MiB default, so the route has to say so, and the same figure is notification's own ceiling on
#: the body it will buffer for that one callback.
WHATSAPP_BODY_CAP = 3 * 1024 * 1024
#: The deauthorize and data-deletion callbacks are a form-encoded `signed_request` — a couple
#: hundred bytes — never a batch of events, so they share one much smaller cap:
#: `app.meta_signed_request.MAX_SIGNED_REQUEST_BYTES` on notification's own side, which
#: `test_notification_will_buffer_exactly_what_the_gateway_lets_through` holds this number to.
META_PRIVACY_BODY_CAP = 4096

#: Every public webhook path, and the HTTP methods each is public for. Pinned, not just compared:
#: widening this is a security decision, made here, on purpose, once.
EXPECTED_WEBHOOK_PATHS: dict[str, list[str]] = {
    WHATSAPP: ["GET", "POST"],
    DEAUTHORIZE: ["POST"],
    DATA_DELETION: ["POST"],
    DATA_DELETION_STATUS: ["GET"],
}
#: The body cap every public webhook route above must carry, in both route tables.
EXPECTED_BODY_CAPS: dict[str, int] = {
    WHATSAPP: WHATSAPP_BODY_CAP,
    DEAUTHORIZE: META_PRIVACY_BODY_CAP,
    DATA_DELETION: META_PRIVACY_BODY_CAP,
    DATA_DELETION_STATUS: META_PRIVACY_BODY_CAP,
}
#: Routed in the production table but deliberately NOT public in app-gateway: Razorpay's callback,
#: which the owner decides separately. A new `/api/v1/webhooks/*` route must be added here or
#: declared public — silently being neither is the defect this file exists to prevent.
ROUTED_BUT_NOT_PUBLIC = {"/api/v1/webhooks/razorpay"}

_POLICY_SCRIPT = """
import json
from app.main import policy
print(json.dumps({
    "public_paths": sorted(policy.public_paths),
    "public_path_prefixes": sorted(policy.public_path_prefixes),
    "webhook_paths": {path: sorted(methods) for path, methods in policy.webhook_paths.items()},
}))
"""

#: A `PUT` to a path that exists but takes only GET and/or POST is a 405; to one that does not
#: exist, a 404. Asked over ASGI rather than read from `app.routes`, which is not a stable shape
#: (included routers are not always flattened into it) and would test the framework's bookkeeping
#: instead of what a request meets.
_NOTIFICATION_SCRIPT = """
import asyncio
import json

import httpx

from app.api.webhooks import MAX_WEBHOOK_BODY_BYTES
from app.main import app
from app.meta_signed_request import MAX_SIGNED_REQUEST_BYTES


async def main() -> dict:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://probe"
    ) as client:
        paths = (
            "/api/v1/webhooks/whatsapp",
            "/webhooks/whatsapp",
            "/api/v1/webhooks/whatsapp/deauthorize",
            "/api/v1/webhooks/whatsapp/data-deletion",
            "/api/v1/webhooks/whatsapp/data-deletion-status",
            "/api/v1/nowhere",
        )
        status = {path: (await client.request("PUT", path)).status_code for path in paths}
    return {
        "max_webhook_body_bytes": MAX_WEBHOOK_BODY_BYTES,
        "max_signed_request_bytes": MAX_SIGNED_REQUEST_BYTES,
        "put_status": status,
    }


print(json.dumps(asyncio.run(main())))
"""


def _in_service(service: str, script: str) -> dict[str, Any]:
    """Run `script` with `services/<service>` as the import root, and read the JSON it prints."""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT / "services" / service,
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(json.loads(result.stdout.strip().splitlines()[-1]))


@functools.lru_cache(maxsize=1)
def _app_gateway_policy() -> dict[str, Any]:
    """This service's own `policy`, exactly as `app.main` builds it."""
    return _in_service("app-gateway", _POLICY_SCRIPT)


@functools.lru_cache(maxsize=1)
def _notification() -> dict[str, Any]:
    return _in_service("notification", _NOTIFICATION_SCRIPT)


def _webhook_routes(path: Path) -> dict[str, dict[str, object]]:
    return {
        str(route["prefix"]): route
        for route in _routes(path)
        if str(route["prefix"]).startswith(WEBHOOK_PREFIX)
    }


TABLES = [pytest.param(DEV_ROUTES, id="dev"), pytest.param(PROD_ROUTES, id="prod")]


def test_the_gateway_makes_exactly_the_expected_paths_public_as_webhooks() -> None:
    """Pinned, not just compared: widening this list is a security decision, and it should fail
    loudly here and be made on purpose."""
    assert _app_gateway_policy()["webhook_paths"] == {
        path: sorted(methods) for path, methods in EXPECTED_WEBHOOK_PATHS.items()
    }


def test_no_webhook_path_is_public_by_any_other_list() -> None:
    """`public_paths` and its prefixes are public for every method and pass a browser's credentials
    through; a webhook belongs in `webhook_paths` and nowhere else."""
    policy = _app_gateway_policy()
    listed = (*policy["public_paths"], *policy["public_path_prefixes"])
    assert [p for p in listed if "webhooks" in p] == []


@pytest.mark.parametrize("table", TABLES)
def test_every_public_webhook_is_routed_by_its_own_exact_entry(table: Path) -> None:
    """Public in the policy and unrouted (or swallowed by a broader or `blocked` prefix) in a
    table is a public 404 today and a public hole the day someone adds a broader prefix. It must
    also be a plain route: no `product` or `staff_only`, which the public branch never checks and
    which would only look like protection."""
    from platform_core.gateway import RouteTable

    routes = RouteTable.from_file(table)
    for path in _app_gateway_policy()["webhook_paths"]:
        route = routes.resolve(path)
        assert route is not None and route.prefix == path, f"{path} not routed by its own entry"
        assert not route.blocked
        assert route.product is None and not route.staff_only


def test_every_webhook_route_in_prod_is_public_or_knowingly_not() -> None:
    """The regression, as a build failure: a `/api/v1/webhooks/*` route served by the production
    gateway that the policy neither declares public nor names here answers every provider with a
    401 and nobody finds out until the first delivery."""
    declared = set(_app_gateway_policy()["webhook_paths"])
    undecided = set(_webhook_routes(PROD_ROUTES)) - declared - ROUTED_BUT_NOT_PUBLIC
    assert undecided == set(), f"routed in prod, neither public nor deliberately not: {undecided}"


def test_the_list_of_deliberately_not_public_webhooks_has_no_stale_entry() -> None:
    """Keeps the allowance above from outliving the route it excuses."""
    assert not ROUTED_BUT_NOT_PUBLIC - set(_webhook_routes(PROD_ROUTES))
    assert not ROUTED_BUT_NOT_PUBLIC & set(_app_gateway_policy()["webhook_paths"])


def test_every_webhook_route_in_dev_exists_in_prod() -> None:
    dev, prod = set(_webhook_routes(DEV_ROUTES)), set(_webhook_routes(PROD_ROUTES))
    assert dev <= prod, f"in dev only: {sorted(dev - prod)}"


def test_every_expected_webhook_path_has_an_expected_body_cap() -> None:
    """`test_every_public_webhook_carries_its_expected_body_cap` below only ever checks the paths
    named in `EXPECTED_BODY_CAPS`, and `test_the_gateway_makes_exactly_the_expected_paths_public_
    as_webhooks` only ever pins `EXPECTED_WEBHOOK_PATHS` against the live policy — so a path added
    to one map and not the other would be public, routed, and never have its body cap checked
    against anything, with no test here failing to say so. The two key sets must match exactly."""
    assert set(EXPECTED_BODY_CAPS) == set(EXPECTED_WEBHOOK_PATHS)


@pytest.mark.parametrize("table", TABLES)
def test_every_public_webhook_carries_its_expected_body_cap(table: Path) -> None:
    """The cap is the only thing between an unauthenticated request and a buffered body, so the
    value the box enforces must be the value the tests exercised, in both tables, and it must be
    the one each provider needs: without it the 2 MiB default would 413 a legitimate WhatsApp
    batch, which Meta answers by redelivering it."""
    from platform_core.gateway import MAX_ROUTE_BODY_BYTES

    for path, expected in EXPECTED_BODY_CAPS.items():
        cap = _webhook_routes(table)[path].get("max_body_bytes")
        assert cap == expected, f"{path} in {table.name}: {cap}"
        assert isinstance(cap, int) and cap <= MAX_ROUTE_BODY_BYTES


def test_notification_will_buffer_exactly_what_the_gateway_lets_through() -> None:
    """The second line of defence has to be at least as wide as the first, or a body the gateway
    legitimately forwarded would be refused behind it; and no wider, or it would not be a second
    line at all. Two ceilings, not one: the WhatsApp Business Platform callback's own, and the
    signed_request callbacks' own, much smaller one."""
    notification = _notification()
    assert notification["max_webhook_body_bytes"] == WHATSAPP_BODY_CAP
    assert notification["max_signed_request_bytes"] == META_PRIVACY_BODY_CAP


def test_notification_serves_the_path_the_gateway_forwards() -> None:
    """The gateway forwards the request path unchanged. Mounted only at the root, a callback
    answers every gateway-forwarded request with a 404. The whole router — the WhatsApp Business
    Platform callback and the three Facebook Login callbacks alike — is mounted twice
    (`app/main.py` includes the same router both under `/api/v1` and at the root), so all four are
    also reachable at their root-mounted path; only the WhatsApp Business Platform one is checked
    here, since the root mount exists for a developer's tunnel pointed straight at the service and
    every path behind it is served by the same include, not a distinct routing decision per path."""
    status = _notification()["put_status"]
    assert status["/api/v1/nowhere"] == 404, "probe cannot tell a mounted path from a missing one"
    for path in EXPECTED_WEBHOOK_PATHS:
        assert status[path] == 405, f"{path} not mounted where the gateway forwards it"
    assert status["/webhooks/whatsapp"] == 405, "the root mount for a developer's tunnel is gone"


@pytest.mark.parametrize("table", TABLES)
def test_every_public_webhook_is_routed_to_notification(table: Path) -> None:
    """Same upstream as the rest of notification's routes in that table: the wrong host or port is
    what a hand-edit of one file gets wrong."""
    routes = {str(r["prefix"]): r for r in _routes(table)}
    for path in EXPECTED_WEBHOOK_PATHS:
        assert routes[path]["upstream"] == routes["/api/v1/notifications"]["upstream"]
