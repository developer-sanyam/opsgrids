"""The one place this service performs a DNS lookup.

`app/domain.py` stays pure so the token, the record name and the expiry clock can be tested
with no network; this module is the injectable seam that talks to a real resolver, the same
split `services/notification/app/dns_check.py` uses and for the same reason: a test drives
`services.verify_domain` against a stub `Resolver` and never opens a socket, and production
wires the real one at process start.

Unlike notification's own module, there is no outbound dial guard here. That guard exists to
stop this process opening a *connection* to a name a customer typed into a form — the SSRF risk
of dialling `169.254.169.254` on the customer's word. A TXT lookup never does that: it asks
*this service's own configured resolver* a question, the same shape of call
`resolve_public_addresses` already makes safely, and the resolver — not the domain string — is
what decides which network gets touched.
"""

from __future__ import annotations

from typing import Any, Protocol

import dns.asyncresolver
import dns.exception
import dns.resolver

from platform_core.logging import get_logger

log = get_logger("account.dns_check")

#: Every way a lookup ends with nothing to compare against. `NXDOMAIN` and `NoAnswer` are the
#: ordinary "not published (yet)"; `NoNameservers` and `Timeout` are the zone's or our own
#: network's problem. All four mean the same thing to a caller asking whether a record is live:
#: no — never "verified", never a 500.
_NO_ANSWER = (
    dns.resolver.NXDOMAIN,
    dns.resolver.NoAnswer,
    dns.resolver.NoNameservers,
    dns.exception.Timeout,
)


class Resolver(Protocol):
    """The port. One name and one record type in, whatever is published out."""

    async def resolve(self, host: str, rtype: str) -> list[str]: ...


class _AsyncLookup(Protocol):
    """The slice of `dns.asyncresolver.Resolver` this module uses."""

    async def resolve(self, qname: str, rdtype: str, /) -> Any: ...


class DnsPythonResolver:
    """The real resolver. The only thing in this service that opens a socket to port 53."""

    def __init__(self, lookup: _AsyncLookup | None = None) -> None:
        self._lookup: _AsyncLookup = lookup or dns.asyncresolver.Resolver()

    async def resolve(self, host: str, rtype: str) -> list[str]:
        try:
            answer = await self._lookup.resolve(host, rtype)
        except _NO_ANSWER as exc:
            log.info("dns.no_answer", host=host, rtype=rtype, reason=type(exc).__name__)
            return []
        return [_answer_text(rdata) for rdata in answer]


def _answer_text(rdata: Any) -> str:
    """One published value as the organization pasted it.

    A TXT rdata carries `strings` because DNS caps a single character-string at 255 bytes; a
    longer value is published in chunks and the value is their concatenation. `to_text()`
    instead would hand back the quoted, space-separated chunks, which matches nothing anyone
    was shown to publish.
    """
    strings: tuple[bytes, ...] | None = getattr(rdata, "strings", None)
    if strings is not None:
        return b"".join(strings).decode("utf-8", "replace")
    return str(rdata.to_text())


def _default_resolver() -> Resolver:
    """Built per call rather than at import: this module is imported by the API process, which
    must start whether or not the machine has a working resolver configured."""
    return DnsPythonResolver()


async def txt_contains(host: str, expected: str, *, resolver: Resolver | None = None) -> bool:
    """Whether `expected` appears among `host`'s published TXT answers.

    Containment, not equality — the same reasoning `notification`'s `check_records` documents:
    resolvers split long TXT strings and some registrars wrap or append their own text around
    what an operator pasted in. An empty `expected` never verifies, so a caller cannot pass a
    blank token and have it match trivially against an unrelated TXT record.
    """
    expected = expected.strip()
    if not expected:
        return False
    r = resolver or _default_resolver()
    answers = await r.resolve(host, "TXT")
    return any(expected in answer for answer in answers)
