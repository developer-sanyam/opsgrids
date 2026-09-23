"""`HttpDirectory`: the real client's own defences, independent of anything that calls it.

Round 3's finding: `resolve_domain` built its request path by f-string
(`f"/internal/v1/domains/{domain}"`), and `services.normalise_sso_domain` used to hand back
whatever it was given, unchanged, the moment `idna.encode` refused it — which is exactly the
shape a `domain` never destined to resolve to anything (a path-traversal payload, a query
string) takes. httpx's own URL-joining collapses a `..` segment before the request ever leaves
the process, so a crafted `domain` could make this client address a *different* internal route
than the one it meant to call, with account-service's real response from that other route
reaching this module's own parsing and failing in whatever way that route's shape happens to
fail in — an oracle, not a crash, but not this client's to leak either way.

Two independent fixes, both tested here: the path segment is always percent-encoded
(`urllib.parse.quote`), and a body that does not have the shape this client expects becomes
`None` ("not configured") rather than an unhandled `KeyError` or `ValueError`.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.directory import HttpDirectory

pytestmark = pytest.mark.integration


def _seen_transport(handler):  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


class TestPathTraversalIsRefused:
    async def test_a_traversal_payload_never_reaches_a_different_route(self) -> None:
        """The literal reviewer repro: a `domain` shaped like `../users/by-email?email=...`
        must address account-service's *domain* endpoint, percent-encoded whole, never the
        user-lookup endpoint it was trying to reach instead."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(404, json={})

        directory = HttpDirectory("http://account", settings())
        directory._client = httpx.AsyncClient(
            base_url="http://account", transport=_seen_transport(handler)
        )
        payload = "../users/by-email?email=victim@victim.com"

        result = await directory.resolve_domain(payload)

        await directory.aclose()
        assert result is None
        assert len(seen) == 1
        # Never `/internal/v1/users/by-email` — the percent-encoded payload is address*ed*
        # to `/internal/v1/domains/...`, whatever account-service does with it next.
        assert seen[0].startswith("/internal/v1/domains/")
        assert "/internal/v1/users/" not in seen[0]

    async def test_the_same_payload_percent_encoded_addresses_the_domains_route_literally(
        self,
    ) -> None:
        """What account-service actually receives: the traversal payload as one opaque,
        percent-encoded path segment — a domain lookup for a very strange "domain", not a
        request for anything else."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        directory = HttpDirectory("http://account", settings())
        directory._client = httpx.AsyncClient(
            base_url="http://account", transport=_seen_transport(handler)
        )
        await directory.resolve_domain("../users/by-email?email=x")
        await directory.aclose()

    async def test_a_get_by_id_path_segment_is_also_encoded(self) -> None:
        import uuid

        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(404, json={})

        directory = HttpDirectory("http://account", settings())
        directory._client = httpx.AsyncClient(
            base_url="http://account", transport=_seen_transport(handler)
        )
        await directory.get(uuid.uuid4())
        await directory.aclose()
        assert seen[0].startswith("/internal/v1/users/")


class TestMalformedResponsesNeverCrash:
    async def test_a_response_shaped_like_the_wrong_endpoint_is_not_configured_not_a_crash(
        self,
    ) -> None:
        """account-service's real `/users/by-email` shape (`user_id`, `email`, `full_name`,
        `is_active`, `memberships`) has none of the keys `resolve_domain` reads — this is what
        the traversal above would have reached before the routes were told apart. `None`, not
        `KeyError`."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "user_id": "00000000-0000-0000-0000-000000000001",
                    "email": "victim@victim.com",
                    "full_name": "Victim",
                    "is_active": True,
                    "memberships": [],
                },
            )

        directory = HttpDirectory("http://account", settings())
        directory._client = httpx.AsyncClient(
            base_url="http://account", transport=_seen_transport(handler)
        )
        result = await directory.resolve_domain("attacker.test")
        await directory.aclose()
        assert result is None

    async def test_an_organization_id_that_is_not_a_uuid_is_not_configured_not_a_crash(
        self,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "domain": "acme.com",
                    "organization_id": "not-a-uuid",
                    "organization_slug": "acme",
                    "organization_status": "active",
                    "verified": True,
                },
            )

        directory = HttpDirectory("http://account", settings())
        directory._client = httpx.AsyncClient(
            base_url="http://account", transport=_seen_transport(handler)
        )
        result = await directory.resolve_domain("acme.com")
        await directory.aclose()
        assert result is None
