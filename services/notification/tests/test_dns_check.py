"""Domain verification, without a network.

The resolver is injected for the same reason the transports are: a test that asks the real DNS
tests whoever owns that zone, not this code, and fails on a train. Every answer below was written
down here.

The rule under test is deliberately "is the expected value *in* the answer" rather than equality.
Resolvers hand back a long TXT value in chunks, registrars wrap and append, and a DKIM public key
is always over the 255-byte limit that forces the split — so equality would mean the one record
that matters never verifies.
"""

from __future__ import annotations

from typing import Any

import dns.exception
import dns.resolver
import pytest

from app import dns_check

SPF = "v=spf1 include:_spf.opsgrids.com ~all"


class FakeResolver:
    """Answers from what the test wrote down. A name that is absent publishes nothing."""

    def __init__(self, answers: dict[tuple[str, str], list[str]]) -> None:
        self._answers = answers

    async def resolve(self, host: str, rtype: str) -> list[str]:
        return self._answers.get((host, rtype), [])


def _spf_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "kind": "spf",
        "type": "TXT",
        "host": "company.com",
        "value": SPF,
        "required": True,
        "verified": False,
    }
    record.update(overrides)
    return record


# --------------------------------------------------------------------- check_records


async def test_a_published_txt_record_verifies() -> None:
    out = await dns_check.check_records(
        [_spf_record()], resolver=FakeResolver({("company.com", "TXT"): [SPF]})
    )
    assert out[0]["verified"] is True


async def test_a_missing_record_does_not_verify() -> None:
    out = await dns_check.check_records([_spf_record()], resolver=FakeResolver({}))
    assert out[0]["verified"] is False


async def test_a_different_value_at_the_same_name_does_not_verify() -> None:
    """A domain that already carries someone else's SPF is the common case, not an edge one."""
    out = await dns_check.check_records(
        [_spf_record()],
        resolver=FakeResolver({("company.com", "TXT"): ["v=spf1 include:_spf.google.com ~all"]}),
    )
    assert out[0]["verified"] is False


async def test_a_value_the_registrar_has_padded_still_verifies() -> None:
    out = await dns_check.check_records(
        [_spf_record()], resolver=FakeResolver({("company.com", "TXT"): [f'"{SPF}" ']})
    )
    assert out[0]["verified"] is True


async def test_a_record_with_no_value_yet_cannot_verify() -> None:
    """DKIM is generated with an empty value until the keypair exists. An empty string is a
    substring of every answer, so without this guard a domain with no key would read as verified
    and mail would go out unsigned."""
    dkim = {
        "kind": "dkim",
        "type": "TXT",
        "host": "eng1._domainkey.company.com",
        "value": "",
        "required": True,
        "verified": False,
    }
    out = await dns_check.check_records(
        [dkim],
        resolver=FakeResolver({("eng1._domainkey.company.com", "TXT"): ["v=DKIM1; k=rsa; p=AAA"]}),
    )
    assert out[0]["verified"] is False


async def test_a_cname_is_asked_for_as_a_cname() -> None:
    """The record's own type drives the lookup. Asking for the wrong type is how a correctly
    configured domain reads as broken."""
    return_path = {
        "kind": "return_path",
        "type": "CNAME",
        "host": "bounce.company.com",
        "value": "bounce.opsgrids.com",
        "required": True,
        "verified": False,
    }
    right = FakeResolver({("bounce.company.com", "CNAME"): ["bounce.opsgrids.com."]})
    wrong = FakeResolver({("bounce.company.com", "TXT"): ["bounce.opsgrids.com."]})

    assert (await dns_check.check_records([return_path], resolver=right))[0]["verified"] is True
    assert (await dns_check.check_records([return_path], resolver=wrong))[0]["verified"] is False


async def test_every_record_is_judged_on_its_own_answer() -> None:
    records = [
        _spf_record(),
        {
            "kind": "dmarc",
            "type": "TXT",
            "host": "_dmarc.company.com",
            "value": "v=DMARC1; p=none",
            "required": False,
            "verified": False,
        },
    ]
    out = await dns_check.check_records(
        records, resolver=FakeResolver({("company.com", "TXT"): [SPF]})
    )
    assert [r["verified"] for r in out] == [True, False]


async def test_a_record_that_verified_yesterday_can_stop_verifying() -> None:
    """Verification is a fact about now. A customer who deletes the record must see that."""
    out = await dns_check.check_records([_spf_record(verified=True)], resolver=FakeResolver({}))
    assert out[0]["verified"] is False


async def test_check_does_not_mutate_its_input() -> None:
    records = [_spf_record()]
    out = await dns_check.check_records(
        records, resolver=FakeResolver({("company.com", "TXT"): [SPF]})
    )
    assert records[0]["verified"] is False
    assert out[0] is not records[0]


async def test_the_rest_of_the_record_survives_the_check() -> None:
    """The stored record set is what the setup screen shows. Dropping a field here would show the
    customer a different DKIM selector from the one they already pasted into their registrar."""
    out = await dns_check.check_records(
        [_spf_record()], resolver=FakeResolver({("company.com", "TXT"): [SPF]})
    )
    assert out[0] == _spf_record(verified=True)


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
