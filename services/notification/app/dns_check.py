"""The one place this service performs a DNS lookup.

`app/domain.py` stays pure so the record set and the verification rules can be tested without a
network; everything that can fail because the internet is having a bad day lives here, behind an
injectable resolver — the same port-and-adapter split `transports.py` uses, for the same reason.

Nothing here raises. A record that cannot be read is a record that is not verified: the customer
is mid-setup far more often than the resolver is broken, and the two are indistinguishable from
outside anyway. Refusing the whole check because one name is missing would make the setup screen
unusable during exactly the period it exists for.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

import dns.asyncresolver
import dns.exception
import dns.resolver

from platform_core.logging import get_logger

log = get_logger("notification.dns_check")

#: Every way a lookup ends with nothing to compare against. `NXDOMAIN` and `NoAnswer` are the
#: ordinary "not published yet"; `NoNameservers` and `Timeout` are the zone's or our own network's
#: problem. All four mean the same thing to a caller asking whether the record is live: no.
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

    async def resolve(self, qname: str, rdtype: str, /) -> Iterable[Any]: ...


class DnsPythonResolver:
    """The real resolver. The only thing in this service that opens a socket to port 53.

    The lookup is injectable so the answer-shaping below — which is real logic, and wrong in a way
    that silently blocks verification — can be tested without a zone to point at.
    """

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
    """One published value as the customer pasted it.

    A TXT rdata carries `strings` because DNS caps a single character-string at 255 bytes and a
    2048-bit DKIM key is roughly twice that — the record is published in chunks and the value is
    their concatenation. Reading `to_text()` instead would hand back `"chunk one" "chunk two"`,
    quotes and separator included, which matches nothing the customer was shown.
    """
    strings: tuple[bytes, ...] | None = getattr(rdata, "strings", None)
    if strings is not None:
        return b"".join(strings).decode("utf-8", "replace")
    return str(rdata.to_text())


def _default_resolver() -> Resolver:
    """Built per check rather than at import: this module is imported by the API process, which
    must start whether or not the machine has a working resolver configured."""
    return DnsPythonResolver()


async def check_records(
    records: list[dict[str, Any]], *, resolver: Resolver | None = None
) -> list[dict[str, Any]]:
    """Return a copy of `records` with `verified` set from what DNS actually publishes.

    A record verifies when its expected value appears among the answers — `in`, not equality,
    because resolvers split long TXT strings and registrars wrap and append their own.

    A record with no expected value never verifies. DKIM is generated empty until the keypair
    exists, and the empty string is a substring of every answer, so the containment test on its
    own would report an unsigned domain as ready to send.
    """
    r = resolver or _default_resolver()
    out: list[dict[str, Any]] = []
    for record in records:
        answers = await r.resolve(record["host"], record["type"])
        expected = (record.get("value") or "").strip()
        verified = bool(expected) and any(expected in answer for answer in answers)
        out.append({**record, "verified": verified})
    return out
