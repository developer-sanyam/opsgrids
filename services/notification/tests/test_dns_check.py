"""Domain verification, without a network.

The resolver is injected for the same reason the transports are: a test that asks the real DNS
tests whoever owns that zone, not this code, and fails on a train. Every answer below was written
down here.
"""

from __future__ import annotations

from typing import Any

import dns.exception
import dns.resolver
import pytest

from app import dns_check

SPF = "v=spf1 include:_spf.google.com ~all"


class FakeResolver:
    """Answers from what the test wrote down. A name that is absent publishes nothing."""

    def __init__(self, answers: dict[tuple[str, str], list[str]]) -> None:
        self._answers = answers

    async def resolve(self, host: str, rtype: str) -> list[str]:
        return self._answers.get((host, rtype), [])


# --------------------------------------------------------------------- normalize_domain_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("company.com", "company.com"),
        ("Company.COM", "company.com"),
        ("  company.com  ", "company.com"),
        ("company.com.", "company.com"),
        ("https://opsgrids.com/", "opsgrids.com"),
        ("http://company.com", "company.com"),
        ("HTTPS://Company.COM/", "company.com"),
        ("mail.company.co.uk", "mail.company.co.uk"),
    ],
)
def test_normalizes_every_shape_a_customer_pastes(raw: str, expected: str) -> None:
    assert dns_check.normalize_domain_name(raw) == expected


def test_a_unicode_name_is_idna_encoded() -> None:
    assert dns_check.normalize_domain_name("café.com") == "xn--caf-dma.com"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "company.com/path",
        "company.com:8080",
        "user@company.com",
        "company.com?x=1",
        "company.com#frag",
        "*.company.com",
        "exa mple.com",
        "127.0.0.1",
        "[::1]",
        "localhost",
        "singlelabel",
    ],
)
def test_refuses_anything_that_is_not_a_plain_domain(raw: str) -> None:
    with pytest.raises(ValueError, match=dns_check.INVALID_DOMAIN_MESSAGE):
        dns_check.normalize_domain_name(raw)


def test_every_refusal_reads_the_same_sentence() -> None:
    """A path, a port and a single label must not be distinguishable from the outside — that
    distinction is exactly what a probe would use the error to learn."""
    for raw in ("company.com/path", "company.com:8080", "singlelabel"):
        try:
            dns_check.normalize_domain_name(raw)
        except ValueError as exc:
            assert str(exc) == dns_check.INVALID_DOMAIN_MESSAGE
        else:  # pragma: no cover - the parametrized test above already proves these raise
            pytest.fail(f"{raw!r} did not raise")


# --------------------------------------------------------------------- check_domain_records


async def test_no_connection_performs_no_lookup_and_returns_nothing() -> None:
    calls: list[tuple[str, str]] = []

    class _Counting:
        async def resolve(self, host: str, rtype: str) -> list[str]:
            calls.append((host, rtype))
            return []

    out = await dns_check.check_domain_records(
        "company.com",
        connection_kind=None,
        dkim_selector="google",
        spf_include=None,
        resolver=_Counting(),
    )
    assert out == []
    assert calls == []


async def test_google_domain_verifies_when_both_records_are_published() -> None:
    resolver = FakeResolver(
        {
            ("company.com", "TXT"): [SPF],
            ("google._domainkey.company.com", "TXT"): ["v=DKIM1; k=rsa; p=AAA"],
        }
    )
    out = await dns_check.check_domain_records(
        "company.com",
        connection_kind="google",
        dkim_selector="google",
        spf_include=None,
        resolver=resolver,
    )
    by_kind = {r["kind"]: r for r in out}
    assert by_kind["spf"]["status"] == "verified"
    assert by_kind["dkim"]["status"] == "verified"
    assert by_kind["dmarc"]["status"] == "missing"


async def test_google_domain_stays_pending_with_nothing_published() -> None:
    out = await dns_check.check_domain_records(
        "company.com",
        connection_kind="google",
        dkim_selector="google",
        spf_include=None,
        resolver=FakeResolver({}),
    )
    by_kind = {r["kind"]: r for r in out}
    assert by_kind["spf"]["status"] == "missing"
    assert by_kind["dkim"]["status"] == "missing"


async def test_microsoft_dkim_needs_both_selector_cnames() -> None:
    resolver = FakeResolver(
        {
            ("company.com", "TXT"): ["v=spf1 include:spf.protection.outlook.com ~all"],
            ("selector1._domainkey.company.com", "CNAME"): [
                "selector1-company-com._domainkey.example.onmicrosoft.com."
            ],
        }
    )
    out = await dns_check.check_domain_records(
        "company.com",
        connection_kind="microsoft",
        dkim_selector="google",
        spf_include=None,
        resolver=resolver,
    )
    dkim = {r["fqdn"]: r["status"] for r in out if r["kind"] == "dkim"}
    assert dkim["selector1._domainkey.company.com"] == "verified"
    assert dkim["selector2._domainkey.company.com"] == "missing"


async def test_smtp_with_no_include_or_selector_never_looks_up_spf_or_dkim() -> None:
    """Nothing to check until the customer has named their provider's SPF host and DKIM
    selector — a lookup with an empty required value would be meaningless."""
    calls: list[tuple[str, str]] = []

    class _Counting:
        async def resolve(self, host: str, rtype: str) -> list[str]:
            calls.append((host, rtype))
            return []

    out = await dns_check.check_domain_records(
        "company.com",
        connection_kind="smtp",
        dkim_selector="",
        spf_include=None,
        resolver=_Counting(),
    )
    by_kind = {r["kind"]: r for r in out}
    assert by_kind["spf"]["status"] == "missing"
    assert by_kind["dkim"]["status"] == "missing"
    # DMARC is still checked — it does not depend on either customer-entered value.
    assert calls == [("_dmarc.company.com", "TXT")]


async def test_smtp_dkim_verifies_on_a_cname_too() -> None:
    resolver = FakeResolver(
        {
            ("company.com", "TXT"): ["v=spf1 include:amazonses.com ~all"],
            ("mysel._domainkey.company.com", "CNAME"): ["dkim.amazonses.com."],
        }
    )
    out = await dns_check.check_domain_records(
        "company.com",
        connection_kind="smtp",
        dkim_selector="mysel",
        spf_include="amazonses.com",
        resolver=resolver,
    )
    by_kind = {r["kind"]: r for r in out}
    assert by_kind["spf"]["status"] == "verified"
    assert by_kind["dkim"]["status"] == "verified"


async def test_a_provider_switch_is_judged_against_the_new_provider_only() -> None:
    """The same domain, checked once as `google` and once as `microsoft`: each check asks only
    the questions its own provider requires — a Microsoft answer must not leak into a Google
    verdict or vice versa."""
    resolver = FakeResolver({("company.com", "TXT"): ["v=spf1 include:_spf.google.com ~all"]})
    google_out = await dns_check.check_domain_records(
        "company.com",
        connection_kind="google",
        dkim_selector="google",
        spf_include=None,
        resolver=resolver,
    )
    microsoft_out = await dns_check.check_domain_records(
        "company.com",
        connection_kind="microsoft",
        dkim_selector="google",
        spf_include=None,
        resolver=resolver,
    )
    assert next(r for r in google_out if r["kind"] == "spf")["status"] == "verified"
    # A record is published — it is just Google's, not Microsoft's — so this is a `mismatch`
    # (something is there and wrong), not a `missing` (nothing is there at all).
    assert next(r for r in microsoft_out if r["kind"] == "spf")["status"] == "mismatch"


# --------------------------------------------------------------------- the dnspython adapter


class _Rdata:
    """Stands in for a dnspython rdata: a TXT carries `strings`, everything else only text."""

    def __init__(self, text: str, strings: tuple[bytes, ...] | None = None) -> None:
        self._text = text
        if strings is not None:
            self.strings = strings

    def to_text(self) -> str:
        return self._text


class _StubDnsResolver:
    """A `dns.asyncresolver.Resolver` that answers, or fails, without a socket."""

    def __init__(self, result: Any) -> None:
        self._result = result

    async def resolve(self, host: str, rtype: str) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def test_a_chunked_txt_value_is_rejoined_before_matching() -> None:
    """DNS caps one TXT string at 255 bytes, so a 2048-bit DKIM key is always published in
    pieces. Matching against a piece would mean the one required record never verifies."""
    key = "v=DKIM1; k=rsa; p=" + "A" * 400
    resolver = dns_check.DnsPythonResolver(
        _StubDnsResolver([_Rdata('"chunked"', (key[:255].encode(), key[255:].encode()))])
    )

    assert await resolver.resolve("eng1._domainkey.company.com", "TXT") == [key]


async def test_a_cname_answer_comes_back_as_its_target() -> None:
    resolver = dns_check.DnsPythonResolver(_StubDnsResolver([_Rdata("bounce.opsgrids.com.")]))

    assert await resolver.resolve("bounce.company.com", "CNAME") == ["bounce.opsgrids.com."]


@pytest.mark.parametrize(
    "failure",
    [
        dns.resolver.NXDOMAIN(),
        dns.resolver.NoAnswer(),
        dns.resolver.NoNameservers(),
        dns.exception.Timeout(),
    ],
)
async def test_a_name_that_will_not_resolve_answers_nothing(failure: Exception) -> None:
    """A domain mid-setup has not published its return-path CNAME yet, and a resolver having a
    bad day looks the same from here. Neither is something the caller can act on: the record is
    simply not verified, and the customer reads "not found yet" rather than a 500."""
    resolver = dns_check.DnsPythonResolver(_StubDnsResolver(failure))

    assert await resolver.resolve("bounce.company.com", "CNAME") == []


# --------------------------------------------------------------------- the outbound dial guard


class TestIsPublicFqdn:
    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1",
            "10.0.0.5",
            "::1",
            "fe80::1",
            "[::1]",
            "localhost",
            "postgres",
            "redis",
            "rabbitmq",
            "account",
            "2130706433",  # 127.0.0.1, decimal
            "0x7f000001",  # 127.0.0.1, hex
            "017700000001",  # 127.0.0.1, octal
            "0177.0.0.1",  # 127.0.0.1, per-octet octal
            "127.1",  # 127.0.0.1, shorthand
            "",
            "a" * 254,
        ],
    )
    def test_refuses_every_shape_of_ip_literal_and_single_label_name(self, host: str) -> None:
        assert dns_check.is_public_fqdn(host) is False

    @pytest.mark.parametrize(
        "host",
        ["mail.company.com", "smtp.gmail.com", "smtp.office365.com", "mail.example.co.uk"],
    )
    def test_accepts_an_ordinary_multi_label_hostname(self, host: str) -> None:
        assert dns_check.is_public_fqdn(host) is True


class TestIsGlobalAddress:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",  # loopback
            "10.1.2.3",  # RFC 1918
            "172.16.0.5",  # RFC 1918
            "192.168.1.1",  # RFC 1918
            "169.254.1.1",  # link-local
            "100.64.0.1",  # carrier-grade NAT
            "0.0.0.0",  # noqa: S104 - unspecified, asserted as a value here, never bound
            "224.0.0.1",  # multicast — `ipaddress.is_global` alone says `True` for this
            "::1",  # loopback
            "fc00::1",  # unique-local
            "fe80::1",  # link-local
            "ff02::1",  # multicast
            "::ffff:127.0.0.1",  # IPv4-mapped loopback
            "::ffff:10.0.0.1",  # IPv4-mapped RFC 1918
            "::ffff:169.254.1.1",  # IPv4-mapped link-local
            "2002:0a00:0001::1",  # 6to4 carrying 10.0.0.1
            "2002:7f00:0001::1",  # 6to4 carrying 127.0.0.1
            "not-an-address",
        ],
    )
    def test_refuses_every_non_public_address(self, address: str) -> None:
        assert dns_check.is_global_address(address) is False

    @pytest.mark.parametrize("address", ["8.8.8.8", "2001:4860:4860::8888", "::ffff:8.8.8.8"])
    def test_accepts_an_ordinary_public_address(self, address: str) -> None:
        assert dns_check.is_global_address(address) is True


class TestAssertDialable:
    async def test_a_literal_ip_is_refused_before_any_lookup(self) -> None:
        with pytest.raises(dns_check.HostNotAllowed):
            await dns_check.assert_dialable("127.0.0.1", 587)

    async def test_a_forbidden_port_is_refused_before_any_lookup(self) -> None:
        with pytest.raises(dns_check.HostNotAllowed):
            await dns_check.assert_dialable("mail.company.com", 5432)

    async def test_a_name_resolving_to_a_private_address_is_refused(self) -> None:
        resolver = FakeResolver({("mail.company.com", "A"): ["10.0.0.5"]})
        with pytest.raises(dns_check.HostNotAllowed):
            await dns_check.assert_dialable("mail.company.com", 587, resolver=resolver)

    async def test_refuses_if_any_one_of_several_addresses_is_private(self) -> None:
        """A dual-stack name with one public and one private answer is refused entirely —
        `SmtpTransport`'s own address selection is not this guard's to predict."""
        resolver = FakeResolver(
            {
                ("mail.company.com", "A"): ["8.8.8.8"],
                ("mail.company.com", "AAAA"): ["fc00::1"],
            }
        )
        with pytest.raises(dns_check.HostNotAllowed):
            await dns_check.assert_dialable("mail.company.com", 587, resolver=resolver)

    async def test_a_name_that_does_not_resolve_at_all_is_refused(self) -> None:
        with pytest.raises(dns_check.HostNotAllowed):
            await dns_check.assert_dialable("mail.company.com", 587, resolver=FakeResolver({}))

    async def test_a_public_name_on_an_allowed_port_returns_an_address_to_dial(self) -> None:
        resolver = FakeResolver({("mail.company.com", "A"): ["8.8.8.8"]})
        address = await dns_check.assert_dialable("mail.company.com", 587, resolver=resolver)
        assert address == "8.8.8.8"
