"""`app/dns_check.py` — the one place this service resolves a TXT record.

No real network here: `DnsPythonResolver` is exercised against a stand-in for
`dns.asyncresolver.Resolver`, the same seam `services/notification/tests/test_dns_check.py`
proves its own resolver through.

Round 3's finding: a confirmed absence (`NXDOMAIN`, `NoAnswer`) and our own resolver failing
to get an answer at all (`NoNameservers`, `Timeout`, anything else) used to mean the same thing
to every caller — `[]`, "not found". That let a DNS outage on *our* side of the connection
count as a failed check against a customer's domain, in `services.recheck_domain`'s demotion
policy. `resolve()` now raises `DnsLookupError` for the second bucket instead of returning an
empty list, and `check_txt` turns that into its own third `Outcome`.
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


@pytest.mark.parametrize("failure", [dns.resolver.NXDOMAIN(), dns.resolver.NoAnswer()])
async def test_a_confirmed_absence_resolves_to_an_empty_list(failure: Exception) -> None:
    """A real answer saying the record genuinely is not there — a fact about the record."""
    resolver = dns_check.DnsPythonResolver(_StubDnsResolver(failure))
    assert await resolver.resolve("_opsgrids-verify.acme.example.com", "TXT") == []


@pytest.mark.parametrize(
    "failure",
    [
        dns.resolver.NoNameservers(),
        dns.exception.Timeout(),
        # The broad base class, not just the two named dnspython exceptions above — a
        # resolver failure this module's author did not enumerate by name must still be told
        # apart from a confirmed absence, never silently folded into "not found".
        dns.exception.FormError(),
        dns.resolver.LifetimeTimeout(),
    ],
)
async def test_a_resolver_failure_raises_rather_than_answering_not_found(
    failure: Exception,
) -> None:
    resolver = dns_check.DnsPythonResolver(_StubDnsResolver(failure))
    with pytest.raises(dns_check.DnsLookupError):
        await resolver.resolve("_opsgrids-verify.acme.example.com", "TXT")


async def test_a_completely_unrelated_dns_exception_still_raises_dns_lookup_error() -> None:
    """A subclass of `dns.exception.DNSException` this test did not think to name explicitly —
    exactly the case the broad catch exists for."""

    class _SomeFutureDnsException(dns.exception.DNSException):
        pass

    resolver = dns_check.DnsPythonResolver(_StubDnsResolver(_SomeFutureDnsException()))
    with pytest.raises(dns_check.DnsLookupError):
        await resolver.resolve("_opsgrids-verify.acme.example.com", "TXT")


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


async def test_txt_contains_is_also_false_on_a_resolver_error() -> None:
    """`txt_contains` is the customer-facing "check DNS and try again" path — it collapses a
    resolver error to the same `False` a confirmed absence gets, deliberately: the distinction
    only matters to the periodic sweep, which uses `check_txt` directly instead."""
    resolver = dns_check.DnsPythonResolver(_StubDnsResolver(dns.resolver.NoNameservers()))
    assert not await dns_check.txt_contains(
        "_opsgrids-verify.acme.example.com", "opsgrids-verify=abc", resolver=resolver
    )


# --------------------------------------------------------------------- check_txt: three states


async def test_check_txt_found() -> None:
    resolver = dns_check.DnsPythonResolver(_StubDnsResolver([_Rdata((b"opsgrids-verify=abc",))]))
    outcome = await dns_check.check_txt(
        "_opsgrids-verify.acme.example.com", "opsgrids-verify=abc", resolver=resolver
    )
    assert outcome is dns_check.Outcome.FOUND


async def test_check_txt_not_found_on_a_confirmed_absence() -> None:
    resolver = dns_check.DnsPythonResolver(_StubDnsResolver(dns.resolver.NXDOMAIN()))
    outcome = await dns_check.check_txt(
        "_opsgrids-verify.acme.example.com", "opsgrids-verify=abc", resolver=resolver
    )
    assert outcome is dns_check.Outcome.NOT_FOUND


async def test_check_txt_not_found_when_the_record_is_there_but_wrong() -> None:
    resolver = dns_check.DnsPythonResolver(_StubDnsResolver([_Rdata((b"unrelated=1",))]))
    outcome = await dns_check.check_txt(
        "_opsgrids-verify.acme.example.com", "opsgrids-verify=abc", resolver=resolver
    )
    assert outcome is dns_check.Outcome.NOT_FOUND


@pytest.mark.parametrize(
    "failure", [dns.resolver.NoNameservers(), dns.exception.Timeout(), dns.exception.FormError()]
)
async def test_check_txt_resolver_error_is_its_own_outcome_not_not_found(
    failure: Exception,
) -> None:
    """The whole point of this function existing alongside `txt_contains`: a caller that acts
    on the difference (`services.recheck_domain`) must be able to tell "our resolver could not
    ask" apart from "asked, and the answer was no"."""
    resolver = dns_check.DnsPythonResolver(_StubDnsResolver(failure))
    outcome = await dns_check.check_txt(
        "_opsgrids-verify.acme.example.com", "opsgrids-verify=abc", resolver=resolver
    )
    assert outcome is dns_check.Outcome.RESOLVER_ERROR


async def test_check_txt_a_blank_expected_value_is_not_found_never_resolver_error() -> None:
    resolver = dns_check.DnsPythonResolver(_StubDnsResolver([_Rdata((b"unrelated=1",))]))
    outcome = await dns_check.check_txt(
        "_opsgrids-verify.acme.example.com", "   ", resolver=resolver
    )
    assert outcome is dns_check.Outcome.NOT_FOUND
