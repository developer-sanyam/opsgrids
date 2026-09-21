"""The service boots, answers, and **refuses to boot misconfigured**.

The last class here is the one that matters. ADR-0010 calls a missing directory role "the worst
failure in the design", and the reason is that it has no symptom: `get_directory_session` falls
back to an ordinary session, the slug lookup binds no tenant, row-level security matches nothing,
and every anonymous buyer in the world is told the store does not deliver to them — HTTP 200, a
well-formed body, nothing in a log. A startup assertion is the only thing standing between that
and production, so it gets a test that drives the real lifespan rather than the function alone.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, assert_directory_role_configured, settings
from app.main import app
from platform_core.cache import close_redis
from platform_core.config import get_settings
from platform_core.db import dispose_directory_engine, dispose_engine


def test_health_reports_this_service() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "storefront"}


def test_info_is_mounted_under_the_versioned_prefix() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/storefront/info")
    assert response.status_code == 200
    assert response.json()["schema_name"] == "storefront"


def test_unknown_route_returns_problem_json() -> None:
    # Every failure in the platform has the same shape, including 404s.
    with TestClient(app) as client:
        response = client.get("/api/v1/storefront/nope")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "request_id" in response.json()


#: The anonymous surface, in full. Written out rather than derived so that adding an entry is a
#: deliberate edit in two repositories' worth of policy rather than a route that quietly starts
#: asking strangers for a token. `public/catalogue` and `public/enquiry` are gaps 4 and 6's —
#: report their addition to the orchestrator for `STOREFRONT_PUBLIC_PATHS` in `app-gateway`.
PUBLIC_PATHS = {
    "/api/v1/storefront/public/serviceability",
    "/api/v1/storefront/public/store-context",
    "/api/v1/storefront/public/catalogue",
    "/api/v1/storefront/public/enquiry",
}


def test_the_public_paths_are_exactly_the_ones_the_gateway_allows() -> None:
    """The gateway matches `public_paths` **exactly**, against a `full_path` that excludes the
    query string. If these paths and `STOREFRONT_PUBLIC_PATHS` in `services/app-gateway` ever
    drift apart, the buyer-facing surface starts asking strangers for a token — with no error
    anywhere but a 401 at the edge."""
    paths = set(app.openapi()["paths"])
    assert paths >= PUBLIC_PATHS
    assert {p for p in paths if "/public/" in p} == PUBLIC_PATHS, (
        "a new public path needs a matching entry in the gateway's STOREFRONT_PUBLIC_PATHS"
    )


def test_no_public_path_carries_a_path_parameter() -> None:
    """`GatewayPolicy.public_paths` is an exact-path frozenset, so `/public/store/{slug}` is not
    expressible at all. Every anonymous storefront endpoint puts its variables in the query
    string or the body — a standing constraint on the whole buyer API, not a quirk of this
    route (ADR-0010 §4)."""
    public = [p for p in app.openapi()["paths"] if "/public/" in p]
    assert not any("{" in path for path in public)


class TestItRefusesToBootWithoutTheDirectoryRole:
    def test_the_assertion_itself_refuses(self) -> None:
        cfg = Settings(db_directory_role=None)
        with pytest.raises(RuntimeError, match="db_directory_role"):
            assert_directory_role_configured(cfg)

    def test_it_is_fatal_in_local_too_unlike_the_rls_role_guard(self) -> None:
        """`assert_app_role` warns in `local` so a developer on the wrong DSN is not locked out of
        their own machine. This one does not, because the wrong configuration here produces a
        service that looks perfectly healthy while giving every buyer a wrong answer."""
        cfg = Settings(db_directory_role=None, environment="local")
        with pytest.raises(RuntimeError):
            assert_directory_role_configured(cfg)

    async def test_the_real_lifespan_fails_rather_than_serving(self, monkeypatch) -> None:
        """Drives `create_app`'s actual lifespan, so this covers the wiring and not just the
        function: if somebody removes `on_startup=_startup` from `main.py`, this goes red."""
        # Not `setenv("DB_DIRECTORY_ROLE", "")`: an empty variable means "not configured" to
        # `platform_core` (`env_ignore_empty`), so it yields the pinned default and the service
        # boots. "No directory role" is the settings object saying None, which is what a
        # config file or an operator override would produce.
        get_settings.cache_clear()
        monkeypatch.setattr(settings(), "db_directory_role", None)
        try:
            with pytest.raises(RuntimeError, match="db_directory_role"):
                async with app.router.lifespan_context(app):
                    pass
        finally:
            await dispose_directory_engine()
            await dispose_engine()
            await close_redis()
            get_settings.cache_clear()
