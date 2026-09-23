"""The gate on certificate issuance (ADR-0012 §5): Caddy asks, and a 2xx costs a certificate.

Everything the buyer's bootstrap proves about *serving* a host, this proves about *issuing*
for it, because the two are one decision made in one place. Three properties carry the file:

* **the allowed set is closed** — a listed platform host, a published store's platform
  subdomain, a published store's proved custom domain (and its `www` partner), and nothing
  else;
* **refusal is the default** — a draft store, a suspended one, an unverified claim, a name
  nobody claimed and an empty platform list are all a refusal, because every allowed name is a
  validation against our ACME account's limits;
* **the answer needs no token**, because the caller is Caddy — and the gateway test in
  `app-gateway` proves the same path is *not* public through the edge.
"""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from tests.helpers import seed_store

pytestmark = pytest.mark.integration

PATH = "/api/v1/storefront/internal/tls-authorize"


def platform_host(slug: str) -> str:
    return f"{slug}.{settings().platform_host_suffix}"


async def ask(client, name: str):
    return await client.get(PATH, params={"domain": name})


class TestThePlatformsOwnNames:
    async def test_a_listed_platform_host_is_allowed_without_any_store(
        self, client, monkeypatch
    ) -> None:
        """`edge.opsgrids.com` is nobody's store. Without the list the edge could never obtain a
        certificate for itself, and `/_edge/health` over TLS would never answer."""
        monkeypatch.setattr(settings(), "tls_platform_hosts", "edge.opsgrids.com, API.opsgrids.com")

        allowed = await ask(client, "edge.opsgrids.com")
        also = await ask(client, "api.opsgrids.com")

        assert allowed.status_code == 200
        assert allowed.json() == {"host": "edge.opsgrids.com", "reason": "platform"}
        assert also.status_code == 200
        assert also.json()["reason"] == "platform"

    async def test_the_list_is_exact_not_a_suffix(self, client, monkeypatch) -> None:
        """A listed `opsgrids.com` must not admit `anything.opsgrids.com`: the platform
        subdomain space is decided by the store lookup, and a stranger's `evil.opsgrids.com`
        pointed at our address is exactly the name this endpoint exists to refuse."""
        monkeypatch.setattr(settings(), "tls_platform_hosts", "opsgrids.com")

        response = await ask(client, "anything.opsgrids.com")

        assert response.status_code == 403

    async def test_an_empty_list_allows_nothing_by_itself(self, client, monkeypatch) -> None:
        monkeypatch.setattr(settings(), "tls_platform_hosts", "")

        response = await ask(client, "edge.opsgrids.com")

        assert response.status_code == 403


class TestAStoresOwnNames:
    async def test_a_published_stores_platform_subdomain_is_allowed(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug)

        response = await ask(client, platform_host(slug))

        assert response.status_code == 200
        assert response.json() == {"host": platform_host(slug), "reason": "store"}

    @pytest.mark.parametrize("status", ["draft", "suspended"])
    async def test_a_store_that_is_not_published_gets_no_certificate(
        self, client, admin_session, org_id: uuid.UUID, slug: str, status: str
    ) -> None:
        """The origin refuses to serve it, so a certificate for it would be a certificate for
        an error page — and for `suspended`, a certificate for a store somebody switched off."""
        await seed_store(admin_session, org_id, slug, status=status)

        response = await ask(client, platform_host(slug))

        assert response.status_code == 403

    async def test_a_slug_nobody_owns_is_refused(self, client) -> None:
        response = await ask(client, platform_host("nobody-here"))

        assert response.status_code == 403

    async def test_a_proved_custom_domain_and_its_www_partner_are_allowed(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain="merchant.example",
            custom_domain_status="verified",
        )

        apex = await ask(client, "merchant.example")
        www = await ask(client, "www.merchant.example")

        assert apex.status_code == 200 and apex.json()["reason"] == "store"
        assert www.status_code == 200 and www.json()["host"] == "www.merchant.example"

    async def test_an_unverified_claim_gets_no_certificate(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """A claim is stored on the word of whoever typed it (ADR-0011 §5). Issuing for it
        would put a certificate for someone else's domain on our edge on the strength of a
        form field."""
        await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain="merchant.example",
            custom_domain_status="unverified",
        )

        response = await ask(client, "merchant.example")

        assert response.status_code == 403

    async def test_a_name_nobody_claimed_is_refused_and_says_nothing(self, client) -> None:
        """Same status and same sentence as an unverified claim: this endpoint is not an
        oracle for which domains are connected to which stores either."""
        response = await ask(client, "stranger.example")

        assert response.status_code == 403
        assert "store" in response.json()["detail"].lower()


class TestTheFullStateMachine:
    """ADR-0012 §6's table, widened from the two values ADR-0011 shipped: `verified`, `live`
    and `failing` all authorize a certificate — `failing` is still served, generously, because
    taking a domain off the air on the first bad check is worse than a stale TXT record — and
    `suspended` is the one state that now refuses, the same way an unpublished store already
    does. This is the same `SERVING_DOMAIN_STATUSES` filter `store-context` reads, exercised
    here against every value the column can now hold."""

    @pytest.mark.parametrize("status", ["verified", "live", "failing"])
    async def test_every_servable_state_is_authorized(
        self, client, admin_session, org_id: uuid.UUID, slug: str, status: str
    ) -> None:
        await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain="merchant.example",
            custom_domain_status=status,
        )

        response = await ask(client, "merchant.example")

        assert response.status_code == 200
        assert response.json()["reason"] == "store"

    async def test_a_suspended_domain_gets_no_certificate(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """Seven consecutive failures, or the store is unpublished — either way, `suspended`
        is the one state ADR-0012 §6 says `ask` must refuse (ADR-0012 §7's gate 1)."""
        await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain="merchant.example",
            custom_domain_status="suspended",
        )

        response = await ask(client, "merchant.example")

        assert response.status_code == 403

    async def test_a_suspended_domain_on_a_still_published_store_is_still_refused(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """The domain's own state governs independently of the store's — a domain can be
        `suspended` for seven days of DNS trouble on a store the merchant never unpublished."""
        await seed_store(
            admin_session,
            org_id,
            slug,
            status="published",
            custom_domain="merchant.example",
            custom_domain_status="suspended",
        )

        response = await ask(client, "merchant.example")

        assert response.status_code == 403


class TestTheInput:
    @pytest.mark.parametrize(
        "name",
        ["", "localhost", "203.0.113.7", "https://merchant.example/", "*.merchant.example"],
    )
    async def test_a_string_that_is_not_a_hostname_is_a_422(self, client, name: str) -> None:
        """Caddy treats any non-2xx as no, so 422 and 403 are the same decision to it. They are
        kept distinct for the log and for a human with curl: "that is not a hostname" and
        "nothing is published there" are different problems."""
        response = await ask(client, name)

        assert response.status_code == 422

    async def test_the_host_is_normalised_before_it_is_compared(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """Caddy passes the SNI as typed. A capitalised name with a root dot is the same host."""
        await seed_store(admin_session, org_id, slug)

        response = await ask(client, platform_host(slug).upper() + ".")

        assert response.status_code == 200
        assert response.json()["host"] == platform_host(slug)
