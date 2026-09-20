"""Entry point for the storefront service. No logic lives here.

The one thing that is not a wiring line is `_startup`, and it is not defensive polish: with no
directory role configured, every anonymous buyer is told the store does not deliver to them,
with HTTP 200, a well-formed body and nothing in a log. A startup assertion is the only thing
standing between that and production (ADR-0010 §4, §13).
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.internal import router as internal_router
from app.api.public import router as public_router
from app.api.routes import router
from app.config import assert_directory_role_configured, settings
from app.models import OutboxEvent
from platform_core.app import create_app


async def _startup(_: FastAPI) -> None:
    """Refuse to serve without the bypass reader the anonymous route depends on.

    Runs after the lifespan has opened the engines, so by the time a request can arrive the
    process has either the role or no process at all.
    """
    assert_directory_role_configured(settings())


app = create_app(
    settings(),
    title="Storefront service",
    description=(
        "The storefront product's first service, and the platform's **first anonymous read of "
        "tenant data** (ADR-0010). A buyer with no token, no account and no session asks "
        "whether a store delivers to their postal code, and gets a real answer: served with a "
        "promise, served with slots (slice 2), or deliberately not served — with the reason. "
        "The tenant is resolved from a store slug in the request body on a BYPASSRLS, "
        "SELECT-only reader confined to one table, and every statement after that runs "
        "tenant-bound under row-level security. **A check that fails renders as 'we couldn't "
        "check', never as 'we don't deliver here'** — the server never returns an outcome it "
        "did not compute. Behind the public route, the merchant configures zones, maps postal "
        "codes, imports them in bulk, tests one, and reads which postal codes were turned away."
    ),
    routers=[router, public_router, internal_router],
    on_startup=_startup,
    # Redis is a declared dependency because the store-slug lookup is cached in it (ADR-0010
    # §12) — the cache degrades to a query and is never fatal — and because the gateway's
    # anonymous rate limiter in front of the public route fails closed by design.
    redis=True,
    outbox_model=OutboxEvent,
)
