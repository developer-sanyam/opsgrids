"""`platform_core.egress` — the SSRF guard every admin-supplied-URL fetch goes through.

No socket is ever opened in this file. DNS is a stub `Resolver`, and the transport underneath
the pinning wrapper is `httpx.MockTransport`, which is also the seam that proves the pin: its
handler inspects the request it was actually asked to send, which is the only way to tell "the
guard connected to the address it checked" from "the guard connected to whatever the hostname
resolved to this time" — precisely the distinction DNS rebinding lives in.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from platform_core.egress import (
    EgressBlockedError,
    EgressGuard,
    Resolver,
    check_url_shape,
)

URL = "https://idp.example.test/.well-known/openid-configuration"


def _resolver(*addresses: str) -> Resolver:
    async def resolve(host: str) -> list[str]:
        return list(addresses)

    return resolve


def _sequenced_resolver(*answers: list[str]) -> Resolver:
    """A resolver that gives a different answer on each call — a rebinding DNS server."""
    calls = iter(answers)

    async def resolve(host: str) -> list[str]:
        return next(calls)

    return resolve


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


class TestUrlShape:
    def test_https_is_permitted(self) -> None:
        assert check_url_shape("https://idp.example.test/x") == "idp.example.test"

    def test_plain_http_is_refused_by_default(self) -> None:
        with pytest.raises(EgressBlockedError):
            check_url_shape("http://idp.example.test/x")

    def test_plain_http_is_permitted_when_explicitly_allowed(self) -> None:
        assert (
            check_url_shape("http://idp.example.test/x", allow_insecure_http=True)
            == "idp.example.test"
        )

    def test_an_unrecognised_scheme_is_refused(self) -> None:
        with pytest.raises(EgressBlockedError):
            check_url_shape("ftp://idp.example.test/x")

    def test_userinfo_is_refused(self) -> None:
        """`https://trusted.example@169.254.169.254/` names the host after `@`, not before
        it — the whole point of the check."""
        with pytest.raises(EgressBlockedError):
            check_url_shape("https://trusted.example@169.254.169.254/")

    def test_a_password_alone_is_also_refused(self) -> None:
        with pytest.raises(EgressBlockedError):
            check_url_shape("https://:secret@idp.example.test/")

    def test_a_bare_ip_host_is_refused_by_default(self) -> None:
        with pytest.raises(EgressBlockedError):
            check_url_shape("https://93.184.216.34/x")

    def test_a_bare_ip_host_is_permitted_when_explicitly_allowed(self) -> None:
        assert (
            check_url_shape("https://93.184.216.34/x", allow_ip_literal_host=True)
            == "93.184.216.34"
        )

    def test_a_url_with_no_host_is_refused(self) -> None:
        with pytest.raises(EgressBlockedError):
            check_url_shape("https:///no-host")


class TestAddressClasses:
    """Every class ADR-mandated refusal names, reached through a stub resolver so this needs
    no real DNS and no real socket."""

    @pytest.mark.parametrize(
        ("label", "address"),
        [
            ("loopback v4", "127.0.0.1"),
            ("loopback v6", "::1"),
            ("rfc1918 10/8", "10.1.2.3"),
            ("rfc1918 172.16/12", "172.16.5.9"),
            ("rfc1918 192.168/16", "192.168.1.1"),
            ("link-local incl. cloud metadata", "169.254.169.254"),
            ("link-local v6", "fe80::1"),
            ("carrier-grade nat", "100.64.0.1"),
            ("unique-local v6", "fd12:3456:789a::1"),
            ("multicast v4", "224.0.0.1"),
            ("multicast v6", "ff02::1"),
            ("unspecified v4", "0.0.0.0"),  # noqa: S104 - an address under test, not a bind
            ("unspecified v6", "::"),
            ("ipv4-mapped loopback", "::ffff:127.0.0.1"),
            ("ipv4-mapped metadata", "::ffff:169.254.169.254"),
        ],
    )
    async def test_a_disallowed_address_is_refused_with_no_request_sent(
        self, label: str, address: str
    ) -> None:
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200)  # pragma: no cover - unreached

        guard = EgressGuard(
            resolver=_resolver(address), inner_transport=httpx.MockTransport(handler)
        )
        with pytest.raises(EgressBlockedError):
            await guard.request("GET", URL)
        assert called is False, label

    async def test_an_ordinary_public_address_is_permitted(self) -> None:
        guard = EgressGuard(
            resolver=_resolver("93.184.216.34"), inner_transport=httpx.MockTransport(_ok_handler)
        )
        response = await guard.request("GET", URL)
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    async def test_an_ipv4_mapped_public_address_is_permitted(self) -> None:
        guard = EgressGuard(
            resolver=_resolver("::ffff:93.184.216.34"),
            inner_transport=httpx.MockTransport(_ok_handler),
        )
        response = await guard.request("GET", URL)
        assert response.status_code == 200

    async def test_a_mix_of_public_and_disallowed_addresses_refuses_the_whole_host(self) -> None:
        """A name that answers with several addresses is refused if *any* of them is
        disallowed — never trusted to only ever route the public one at connect time."""
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200)  # pragma: no cover - unreached

        guard = EgressGuard(
            resolver=_resolver("93.184.216.34", "127.0.0.1"),
            inner_transport=httpx.MockTransport(handler),
        )
        with pytest.raises(EgressBlockedError):
            await guard.request("GET", URL)
        assert called is False

    async def test_a_host_resolving_to_nothing_is_refused(self) -> None:
        guard = EgressGuard(resolver=_resolver(), inner_transport=httpx.MockTransport(_ok_handler))
        with pytest.raises(EgressBlockedError):
            await guard.request("GET", URL)


class TestDnsRebindingIsImpossible:
    async def test_the_connection_is_pinned_to_the_address_the_check_saw(self) -> None:
        """The resolver would answer differently on a second call — a public address for the
        check, a private one moments later, exactly what rebinding is. Nothing here gives it
        a second chance: the guard resolves once, and the request it sends is provably to
        that one address, not to the hostname."""
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["host"] = request.url.host
            seen["header_host"] = request.headers.get("host")
            seen["sni"] = request.extensions.get("sni_hostname")
            return httpx.Response(200, json={"ok": True})

        resolver = _sequenced_resolver(["93.184.216.34"], ["169.254.169.254"])
        guard = EgressGuard(resolver=resolver, inner_transport=httpx.MockTransport(handler))

        response = await guard.request("GET", URL)

        assert response.status_code == 200
        # The socket target is the vetted address, never the hostname (which is what a
        # transport doing its own DNS lookup a second time could turn into 169.254.169.254).
        assert seen["host"] == "93.184.216.34"
        # ...while the Host header and TLS SNI still carry the real name, so virtual hosting
        # and certificate validation are unaffected by the pin.
        assert seen["header_host"] == "idp.example.test"
        assert seen["sni"] == "idp.example.test"

    async def test_a_second_request_through_the_same_guard_re_resolves(self) -> None:
        """Pinning is per-request, not permanent: a guard that cached a stale address forever
        would just move the rebinding window rather than closing it. A provider that turns
        genuinely malicious between two calls is caught by the *next* vet, not remembered
        as safe from the first one."""
        resolver = _sequenced_resolver(["93.184.216.34"], ["127.0.0.1"])
        guard = EgressGuard(resolver=resolver, inner_transport=httpx.MockTransport(_ok_handler))

        first = await guard.request("GET", URL)
        assert first.status_code == 200

        with pytest.raises(EgressBlockedError):
            await guard.request("GET", URL)


class TestRedirectsAreRefused:
    async def test_a_3xx_response_is_refused_rather_than_followed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://elsewhere.test/"})

        guard = EgressGuard(
            resolver=_resolver("93.184.216.34"), inner_transport=httpx.MockTransport(handler)
        )
        with pytest.raises(EgressBlockedError):
            await guard.request("GET", URL)


class TestResponseSizeIsCapped:
    async def test_a_response_over_the_cap_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"x" * 2048)

        guard = EgressGuard(
            resolver=_resolver("93.184.216.34"),
            inner_transport=httpx.MockTransport(handler),
            max_response_bytes=1024,
        )
        with pytest.raises(EgressBlockedError):
            await guard.request("GET", URL)

    async def test_a_response_at_the_cap_is_allowed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"x" * 1024)

        guard = EgressGuard(
            resolver=_resolver("93.184.216.34"),
            inner_transport=httpx.MockTransport(handler),
            max_response_bytes=1024,
        )
        response = await guard.request("GET", URL)
        assert len(response.content) == 1024
