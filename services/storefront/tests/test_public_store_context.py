"""The buyer's bootstrap: a hostname in, a store out. No token, no account, no session.

This is the second anonymous path and it follows the first exactly — same exact path, same
bypass reader, same gateway policy entry, variables in the body. What is new is the input: the
serviceability check is named a slug by an application that already knows it, and this one is
handed **an arbitrary string from the open internet** and has to decide what a store's address
is.

Four properties are worth more than the rest, and each has its own class below:

* a stranger reaches it with no credentials at all, and reaches nothing else;
* `draft`, `suspended`, an unverified claim and a host nobody has ever claimed are **one
  answer**, byte for byte — anything less is an oracle for which tenants exist;
* a malformed host is a 422 and never a 404, because "that is not an address" and "nothing is
  published there" are different facts;
* a dependency that fails renders as unavailable, never as an absent store. **Unknown is not
  no** (ADR-0010 §13) applies to a whole shop the same way it applies to one postal code.
"""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.selectors import host_cache_key, slug_cache_key
from platform_core.cache import get_redis
from tests.helpers import all_perms, seed_store, seed_zone, store_payload

pytestmark = pytest.mark.integration

PATH = "/api/v1/storefront/public/store-context"
SERVICEABILITY = "/api/v1/storefront/public/serviceability"

#: What ADR-0012 §11 prints. Asserted as a set, so a field added without the contract being
#: regenerated fails here rather than in a frontend nobody has built yet.
BODY_FIELDS = {
    "store",
    "name",
    "tagline",
    "logo_text",
    "currency",
    "timezone",
    "canonical_host",
    "is_canonical",
    "checked_at",
}


def platform_host(slug: str) -> str:
    return f"{slug}.{settings().platform_host_suffix}"


class TestTheAnonymousContract:
    async def test_a_stranger_gets_the_whole_payload_with_no_token_at_all(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """**No `Authorization` header, no principal override, nothing.** The unit suite proves
        the handler works; this is what proves a buyer can reach it."""
        await seed_store(
            admin_session,
            org_id,
            slug,
            name="Greenleaf Organics",
            tagline="Fresh from the farm, daily",
            logo_text="GLO",
        )

        response = await client.post(PATH, json={"host": platform_host(slug)})

        assert response.status_code == 200
        body = response.json()
        assert set(body) == BODY_FIELDS
        assert body["store"] == slug
        assert body["name"] == "Greenleaf Organics"
        assert body["tagline"] == "Fresh from the farm, daily"
        assert body["logo_text"] == "GLO"
        assert body["currency"] == "INR"
        assert body["timezone"] == "Asia/Kolkata"
        assert body["canonical_host"] == platform_host(slug)
        assert body["is_canonical"] is True
        assert body["checked_at"]

    async def test_the_slug_it_returns_is_the_one_the_next_call_names(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """**ADR-0010's wire format does not change**, and this is the whole reason the host is
        resolved in a bootstrap rather than added as a field to every public endpoint. The buyer
        asks once which store this address is, and then asks about that store by name."""
        await seed_store(admin_session, org_id, slug)
        await seed_zone(admin_session, org_id, postal_codes=("400050",))

        store = (await client.post(PATH, json={"host": platform_host(slug)})).json()["store"]
        answer = await client.post(SERVICEABILITY, json={"store": store, "postal_code": "400050"})

        assert answer.status_code == 200
        assert answer.json()["serviceable"] is True

    async def test_a_store_with_no_tagline_sends_null_rather_than_omitting_it(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """ "Never written" is one state on the wire, not a missing key the client has to guess
        at. The renderer owns the fallback — `logo_text` falling back to `name` is its decision,
        and this service does not copy one column into the other."""
        await seed_store(admin_session, org_id, slug, tagline=None, logo_text=None)

        body = (await client.post(PATH, json={"host": platform_host(slug)})).json()

        assert body["tagline"] is None
        assert body["logo_text"] is None

    async def test_brand_text_comes_back_verbatim_and_unescaped(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """**Output encoding belongs to the renderer** (ADR-0011 §6), and this payload is the
        public face of those columns. Escaping here would double-escape the moment anything else
        read the column, and would still not protect a client that injected the value as HTML.
        `test_store.py` pins the same rule on the merchant's own read; this pins it on the
        stranger's."""
        await seed_store(
            admin_session, org_id, slug, name="Ben & Jerry's <Shop>", tagline="Tea & coffee"
        )

        body = (await client.post(PATH, json={"host": platform_host(slug)})).json()

        assert body["name"] == "Ben & Jerry's <Shop>"
        assert body["tagline"] == "Tea & coffee"

    async def test_an_unknown_field_on_the_body_is_refused(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """The anonymous body arrives from a stranger. Silently dropping what it does not
        recognise is how an undocumented field becomes a contract."""
        await seed_store(admin_session, org_id, slug)

        response = await client.post(
            PATH, json={"host": platform_host(slug), "organization_id": str(uuid.uuid4())}
        )

        assert response.status_code == 422

    async def test_a_missing_host_is_refused(self, client) -> None:
        assert (await client.post(PATH, json={})).status_code == 422


class TestTheHostIsAnUntrustedString:
    """It arrives from the open internet, it is the key of an unauthenticated cache, and the
    only thing standing between it and Redis is `domain.normalise_request_host`."""

    @pytest.mark.parametrize("form", ["upper", "trailing_dot", "port", "padded"])
    async def test_the_forms_a_browser_sends_all_reach_the_same_store(
        self, client, admin_session, org_id: uuid.UUID, slug: str, form: str
    ) -> None:
        await seed_store(admin_session, org_id, slug)
        host = platform_host(slug)
        sent = {
            "upper": host.upper(),
            "trailing_dot": f"{host}.",
            "port": f"{host}:8443",
            "padded": f"  {host} ",
        }[form]

        response = await client.post(PATH, json={"host": sent})

        assert response.status_code == 200
        assert response.json()["store"] == slug

    @pytest.mark.parametrize(
        "host",
        [
            "https://merchant.com",
            "merchant.com/shop",
            "mer chant.com",
            "localhost",
            "198.51.100.7",
            "[2001:db8::1]",
            "-merchant.com",
            "merchant..com",
            "a" * 64 + ".com",
            "",
        ],
    )
    async def test_a_host_that_is_not_a_hostname_is_422_and_never_404(
        self, client, host: str
    ) -> None:
        """**The distinction the whole route turns on.** A 404 means we looked and there is no
        such store; a 422 means that was never an address. A client that collapses them shows a
        buyer "this shop does not exist" for its own bug."""
        response = await client.post(PATH, json={"host": host})

        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        assert "store" not in response.json()

    async def test_an_oversized_host_is_refused_by_the_schema_before_anything_runs(
        self, client
    ) -> None:
        """253 characters is RFC 1035's ceiling and the bound on the cache key. A body that
        exceeds it is refused by the contract, so nothing a caller sends decides how much work
        this endpoint does."""
        assert (await client.post(PATH, json={"host": "a." * 500 + "com"})).status_code == 422

    async def test_a_refused_host_never_becomes_a_cache_key(self, client) -> None:
        """An attacker can mint unlimited distinct hostnames, and each one that resolves to
        nothing is negatively cached. What bounds that is this: garbage is refused *before* the
        lookup, so only values of hostname shape and at most 253 bytes can ever be keys, each
        for `store_host_cache_seconds`, behind the gateway's 60/min per-address limit."""
        await client.post(PATH, json={"host": "not a host at all"})

        redis = get_redis()
        assert [key async for key in redis.scan_iter(match="storefront:host:*")] == []

    async def test_a_host_that_is_merely_unknown_does_become_one(self, client) -> None:
        """The negative entry is the point of the bound above: it is what keeps an enumeration
        attempt off the directory pool's five connections, which is this path's second
        saturation point."""
        await client.post(PATH, json={"host": "nobody-has-claimed-this.example"})

        assert await get_redis().get(host_cache_key("nobody-has-claimed-this.example"))


class TestOnlyPublishedStoresResolve:
    """ADR-0011 made publishing a deliberate act. A stranger must not be able to tell a store
    that is not published from a store that does not exist — that is a tenant-enumeration
    oracle, and the slug space is already globally unique and guessable."""

    @pytest.mark.parametrize("status", ["draft", "suspended"])
    async def test_an_unpublished_store_is_404(
        self, client, admin_session, org_id: uuid.UUID, slug: str, status: str
    ) -> None:
        await seed_store(admin_session, org_id, slug, status=status)

        response = await client.post(PATH, json={"host": platform_host(slug)})

        assert response.status_code == 404
        assert "store" not in response.json()

    @pytest.mark.parametrize("status", ["draft", "suspended"])
    async def test_it_is_indistinguishable_from_a_host_nobody_has_ever_claimed(
        self, client, admin_session, org_id: uuid.UUID, slug: str, status: str
    ) -> None:
        """Byte for byte, less the correlation id. If these two ever diverge, "is there a tenant
        here?" becomes a question a stranger can ask 60 times a minute."""
        await seed_store(admin_session, org_id, slug, status=status)

        unpublished = await client.post(PATH, json={"host": platform_host(slug)})
        unknown = await client.post(PATH, json={"host": platform_host("t" + uuid.uuid4().hex[:8])})

        assert unpublished.status_code == unknown.status_code == 404
        assert _without_request_id(unpublished.json()) == _without_request_id(unknown.json())

    async def test_a_draft_store_leaks_nothing_about_itself(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(
            admin_session, org_id, slug, name="Secret Shop", status="draft", tagline="Not yet"
        )

        body = (await client.post(PATH, json={"host": platform_host(slug)})).json()

        serialised = str(body)
        assert "Secret Shop" not in serialised
        assert "Not yet" not in serialised
        assert slug not in serialised

    async def test_publishing_makes_the_same_host_resolve(
        self, client, admin_session, org_id: uuid.UUID, slug: str, as_principal
    ) -> None:
        """And the negative entry the first 404 wrote does not outlive it: publishing forgets
        the key in the same request that writes the state, or a merchant publishes and watches
        their own store stay invisible for a TTL with nothing to show for it."""
        host = platform_host(slug)
        with as_principal(all_perms(org_id)):
            await client.put("/api/v1/storefront/store", json=store_payload(slug))
            assert (await client.post(PATH, json={"host": host})).status_code == 404
            await client.post("/api/v1/storefront/store/publish")

        assert (await client.post(PATH, json={"host": host})).status_code == 200


class TestBothAddressesResolveToOneStore:
    """A store is reachable at its platform subdomain and, once the claim is proved, at the
    merchant's own domain. Neither retires (ADR-0012 §8)."""

    async def test_a_verified_custom_domain_resolves(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain="merchant.example",
            custom_domain_status="verified",
        )

        body = (await client.post(PATH, json={"host": "merchant.example"})).json()

        assert body["store"] == slug

    async def test_the_platform_host_keeps_working_alongside_it(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """Links, QR codes and printed material outlive DNS changes, and the platform host is
        the fallback when a custom domain breaks. A design that retires it makes a DNS mistake
        unrecoverable except by us."""
        await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain="merchant.example",
            custom_domain_status="verified",
        )

        by_domain = (await client.post(PATH, json={"host": "merchant.example"})).json()
        by_slug = (await client.post(PATH, json={"host": platform_host(slug)})).json()

        assert by_domain["store"] == by_slug["store"] == slug
        assert by_domain["canonical_host"] == by_slug["canonical_host"]

    @pytest.mark.parametrize(
        ("claimed", "asked"),
        [
            ("merchant.example", "www.merchant.example"),
            ("www.merchant.example", "merchant.example"),
        ],
    )
    async def test_the_www_pair_is_served_as_a_unit(
        self, client, admin_session, org_id: uuid.UUID, slug: str, claimed: str, asked: str
    ) -> None:
        """Purely syntactic, with no Public Suffix List. The residual is named and small:
        verification proved control of the claimed name, not literally of its `www` sibling, and
        the two are under the same zone operator in every case that occurs in practice."""
        await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain=claimed,
            custom_domain_status="verified",
        )

        response = await client.post(PATH, json={"host": asked})

        assert response.status_code == 200
        assert response.json()["store"] == slug

    async def test_an_unverified_claim_does_not_serve(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """**The one refusal in this file that is about a takeover rather than about privacy.**
        A claim is stored on the word of whoever typed it (ADR-0011 §5), so serving one is how a
        merchant takes another merchant's traffic — including that of a business which has never
        heard of this platform. Verification is a precondition of serving, not an enhancement."""
        await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain="merchant.example",
            custom_domain_status="unverified",
        )

        response = await client.post(PATH, json={"host": "merchant.example"})

        assert response.status_code == 404

    async def test_an_unpublished_store_does_not_serve_its_verified_domain_either(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """Two independent gates, and proving the domain does not pass the other one."""
        await seed_store(
            admin_session,
            org_id,
            slug,
            status="draft",
            custom_domain="merchant.example",
            custom_domain_status="verified",
        )

        assert (await client.post(PATH, json={"host": "merchant.example"})).status_code == 404

    async def test_a_domain_another_organization_holds_does_not_leak(
        self, client, admin_session, org_id: uuid.UUID, other_org: uuid.UUID, slug: str
    ) -> None:
        """The bypass reader buys **discovery only**, and discovery is scoped to the host that
        was asked about. Nothing about the neighbouring tenant reaches this response."""
        await seed_store(admin_session, org_id, slug)
        await seed_store(
            admin_session,
            other_org,
            f"t{uuid.uuid4().hex[:16]}",
            name="Someone Else",
            custom_domain="theirs.example",
            custom_domain_status="verified",
        )

        body = (await client.post(PATH, json={"host": platform_host(slug)})).json()

        assert body["store"] == slug
        assert "Someone Else" not in str(body)
        assert "theirs.example" not in str(body)


class TestWhichHostIsCanonical:
    """Slice 1 does not redirect, so `canonical_host` and `is_canonical` are the entire
    mechanism: the bundle renders a `<link rel="canonical">` from the first and may show a
    one-line banner from the second."""

    async def test_the_platform_host_is_canonical_while_the_domain_is_not_live(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """`verified` is the cutover state — the claim is proved and the DNS has not landed.
        Pointing a canonical link at it would aim search engines at a name that does not yet
        answer, and the platform host is what makes a DNS mistake recoverable (ADR-0012 §8)."""
        await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain="merchant.example",
            custom_domain_status="verified",
        )

        body = (await client.post(PATH, json={"host": "merchant.example"})).json()

        assert body["canonical_host"] == platform_host(slug)
        assert body["is_canonical"] is False

    async def test_a_store_on_its_platform_host_alone_is_canonical(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(admin_session, org_id, slug)

        body = (await client.post(PATH, json={"host": platform_host(slug)})).json()

        assert body["canonical_host"] == platform_host(slug)
        assert body["is_canonical"] is True

    async def test_the_www_partner_knows_it_is_not_the_canonical_address(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain="merchant.example",
            custom_domain_status="verified",
        )

        body = (await client.post(PATH, json={"host": "www.merchant.example"})).json()

        assert body["is_canonical"] is False


class TestUnknownIsNotNo:
    """ADR-0010 §13's rule, applied to a whole shop rather than to one postal code.

    A lookup that cannot complete must not render as "this store does not exist". A 404 means
    *we looked and there is nothing here*; anything else is a failure, and the client's job is
    to say so and offer a retry.
    """

    async def test_a_failing_lookup_is_a_failure_and_not_an_absent_store(
        self, client, admin_session, org_id: uuid.UUID, slug: str, monkeypatch
    ) -> None:
        """With the directory reader unreachable mid-request there is no path that turns the
        failure into a 404 — the module contains no `try` at all, which
        `test_public_serviceability.py` asserts against its syntax tree."""
        await seed_store(admin_session, org_id, slug)

        async def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("postgres went away")

        monkeypatch.setattr("app.api.public.selectors.resolve_public_store_by_host", _boom)

        with pytest.raises(RuntimeError):
            await client.post(PATH, json={"host": platform_host(slug)})

    async def test_an_unreachable_cache_degrades_to_the_query_rather_than_to_a_404(
        self, client, admin_session, org_id: uuid.UUID, slug: str, monkeypatch
    ) -> None:
        """The cache is the one dependency here that is *allowed* to fail quietly, and the
        direction it fails in is the whole distinction: a miss falls through to one indexed
        query on the source of truth. A cache read that answered "no such store" on an outage
        would take every published shop off the air for a TTL.

        Redis is broken at its real seam — `platform_core.cache.get_redis` — so this exercises
        `cache_get`'s own swallow-and-report-a-miss rather than a stand-in for it."""
        await seed_store(admin_session, org_id, slug)

        def _unreachable() -> None:
            raise RuntimeError("redis went away")

        monkeypatch.setattr("platform_core.cache.get_redis", _unreachable)

        response = await client.post(PATH, json={"host": platform_host(slug)})

        assert response.status_code == 200
        assert response.json()["store"] == slug


class TestTheHostCacheIsInvalidatedByTheWritesThatMoveIt:
    """ADR-0012 §2: every transition that changes what a host resolves to deletes the host key.
    Without it, releasing a domain leaves it serving a store for five minutes."""

    async def test_unpublishing_stops_the_custom_domain_serving(
        self, client, admin_session, org_id: uuid.UUID, slug: str, as_principal
    ) -> None:
        await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain="merchant.example",
            custom_domain_status="verified",
        )
        assert (await client.post(PATH, json={"host": "merchant.example"})).status_code == 200

        with as_principal(all_perms(org_id)):
            await client.post("/api/v1/storefront/store/unpublish")

        assert (await client.post(PATH, json={"host": "merchant.example"})).status_code == 404

    async def test_releasing_a_domain_forgets_both_halves_of_the_www_pair(
        self, client, admin_session, org_id: uuid.UUID, slug: str, as_principal
    ) -> None:
        """The lookup caches under the key it was *asked* about, so a buyer who arrived at the
        `www` sibling has an entry under that key. Forgetting only the claimed value would leave
        a released domain serving at its partner."""
        await seed_store(
            admin_session,
            org_id,
            slug,
            custom_domain="merchant.example",
            custom_domain_status="verified",
        )
        await client.post(PATH, json={"host": "merchant.example"})
        await client.post(PATH, json={"host": "www.merchant.example"})

        with as_principal(all_perms(org_id)):
            await client.put(
                "/api/v1/storefront/store", json=store_payload(slug, custom_domain=None)
            )

        redis = get_redis()
        assert await redis.get(host_cache_key("merchant.example")) is None
        assert await redis.get(host_cache_key("www.merchant.example")) is None

    async def test_the_platform_host_rides_on_the_slug_key_rather_than_a_second_one(
        self, client, admin_session, org_id: uuid.UUID, slug: str
    ) -> None:
        """One host, one invalidation. Caching the platform branch under its own key would mean
        two entries for one store and an invalidation that has to remember both."""
        await seed_store(admin_session, org_id, slug)

        await client.post(PATH, json={"host": platform_host(slug)})

        redis = get_redis()
        assert await redis.get(slug_cache_key(slug))
        assert await redis.get(host_cache_key(platform_host(slug))) is None


def _without_request_id(problem: dict) -> dict:
    return {k: v for k, v in problem.items() if k != "request_id"}
