"""`app/dns_check.py` — the one place this service resolves a TXT record.

No real network here: `DnsPythonResolver` is exercised against a stand-in for
`dns.asyncresolver.Resolver`, the same seam `services/notification/tests/test_dns_check.py`
proves its own resolver through.
"""

from __future__ import annotations

from typing import Any

import dns.exception
import dns.resolver
import pytest

from app import dns_check

pytestmark = pytest.mark.integration


class _Rdata:
    """Stands in for a dnspython TXT rdata, which carries `strings` rather than plain text."""

    def __init__(self, strings: tuple[bytes, ...]) -> None:
        self.strings = strings


class _StubDnsResolver:
    """A `dns.asyncresolver.Resolver` that answers, or fails, without a socket."""

    def __init__(self, result: Any) -> None:
        self._result = result

    async def resolve(self, host: str, rtype: str) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def test_a_published_record_resolves() -> None:
    resolver = dns_check.DnsPythonResolver(_StubDnsResolver([_Rdata((b"opsgrids-verify=abc",))]))
    assert await resolver.resolve("_opsgrids-verify.acme.example.com", "TXT") == [
        "opsgrids-verify=abc"
    ]


async def test_a_chunked_txt_value_is_rejoined() -> None:
    resolver = dns_check.DnsPythonResolver(
        _StubDnsResolver([_Rdata((b"opsgrids-verify=", b"abc"))])
    )
    assert await resolver.resolve("_opsgrids-verify.acme.example.com", "TXT") == [
        "opsgrids-verify=abc"
    ]


@pytest.mark.parametrize(
    "failure",
    [
        dns.resolver.NXDOMAIN(),
        dns.resolver.NoAnswer(),
        dns.resolver.NoNameservers(),
        dns.exception.Timeout(),
        # MINOR4: the broad base class, not just the three named dnspython exceptions above —
        # a resolver failure this module's author did not enumerate by name must still answer
        # "no", never propagate as an unhandled exception (a 500 on a customer's claim).
        dns.exception.FormError(),
        dns.resolver.LifetimeTimeout(),
    ],
)
async def test_every_kind_of_dns_failure_resolves_to_nothing(failure: Exception) -> None:
    resolver = dns_check.DnsPythonResolver(_StubDnsResolver(failure))
    assert await resolver.resolve("_opsgrids-verify.acme.example.com", "TXT") == []


async def test_a_completely_unrelated_dns_exception_still_answers_no_rather_than_raising() -> None:
    """A subclass of `dns.exception.DNSException` this test did not think to name explicitly —
    exactly the case the broad catch exists for."""

    class _SomeFutureDnsException(dns.exception.DNSException):
        pass

    resolver = dns_check.DnsPythonResolver(_StubDnsResolver(_SomeFutureDnsException()))
    assert await resolver.resolve("_opsgrids-verify.acme.example.com", "TXT") == []


async def test_txt_contains_matches_a_substring_of_a_longer_answer() -> None:
    resolver = dns_check.DnsPythonResolver(
        _StubDnsResolver([_Rdata((b"v=spf1 include:_spf.example.com opsgrids-verify=abc ~all",))])
    )
    assert await dns_check.txt_contains(
        "_opsgrids-verify.acme.example.com", "opsgrids-verify=abc", resolver=resolver
    )


async def test_txt_contains_is_false_for_a_blank_expected_value() -> None:
    """An empty expected value must never match trivially against an unrelated TXT record."""
    resolver = dns_check.DnsPythonResolver(_StubDnsResolver([_Rdata((b"unrelated=1",))]))
    assert not await dns_check.txt_contains(
        "_opsgrids-verify.acme.example.com", "   ", resolver=resolver
    )


async def test_txt_contains_is_false_when_nothing_is_published() -> None:
    resolver = dns_check.DnsPythonResolver(_StubDnsResolver(dns.resolver.NXDOMAIN()))
    assert not await dns_check.txt_contains(
        "_opsgrids-verify.acme.example.com", "opsgrids-verify=abc", resolver=resolver
    )
