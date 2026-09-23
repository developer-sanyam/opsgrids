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

from enum import Enum
from typing import Any, Protocol

import dns.asyncresolver
import dns.exception
import dns.resolver

from platform_core.logging import get_logger

log = get_logger("account.dns_check")

#: The ordinary, confirmed "not published (yet)" — a real answer from a real resolver, saying
#: the record genuinely is not there. `NXDOMAIN` is the whole name not existing; `NoAnswer` is
#: the name existing with no record of the type asked for. Both are a fact about the record,
#: not about us or the network, and the caller that cares about that distinction
#: (`services.recheck_domain`, round 3) needs to be able to tell it apart from the tuple below.
_NOT_FOUND = (
    dns.resolver.NXDOMAIN,
    dns.resolver.NoAnswer,
)

#: Every way a lookup ends *without* an answer of any kind — ours or the network's problem, not
#: a fact about the record. `NoNameservers` and `Timeout` are the two dnspython names for it;
#: `dns.exception.DNSException` — its own common base for every exception the resolver can
#: raise, including ones neither of those two covers (`LifetimeTimeout`, a malformed response,
#: an unreachable resolver) — is caught last and broadest, deliberately: a customer's claim
#: must never 500 because a DNS library the verification depends on raised something this
#: module's author did not enumerate by name. Round 3's finding: this bucket used to be folded
#: into "not found" indistinguishably, which meant a sweep tick where *our own* resolver was
#: unreachable counted as a failed check against the customer's domain — `consecutive_failures`
#: climbing, and eventually a domain demoted, entirely because of an outage on this side of the
#: connection. `DnsLookupError` carries that distinction out of this module.
_RESOLVER_ERROR = (
    dns.resolver.NoNameservers,
    dns.exception.Timeout,
    dns.exception.DNSException,
)


class DnsLookupError(Exception):
    """The resolver could not get an answer at all — not the same fact as a confirmed absence.

    Raised by `Resolver.resolve` and caught inside this module's own two public functions,
    never let past them: nothing outside `dns_check.py` needs to know dnspython's exception
    hierarchy, only whether a check found the record, confirmed it is not there, or could not
    ask in the first place.
    """


class Outcome(Enum):
    """A TXT lookup's three possible answers, not two.

    `FOUND` and `NOT_FOUND` are both real facts about the record. `RESOLVER_ERROR` is not a
    fact about the record at all — it means DNS could not be asked, which must never be read
    as "asked, and it said no" by a caller deciding whether to hold a customer's domain
    accountable for what it found (see `_RESOLVER_ERROR` above and `services.recheck_domain`).
    """

    FOUND = "found"
    NOT_FOUND = "not_found"
    RESOLVER_ERROR = "resolver_error"


class Resolver(Protocol):
    """The port. One name and one record type in, whatever is published out.

    Raises `DnsLookupError` for the "could not ask" case; returns an empty list for a genuine,
    confirmed absence. The two are not the same thing to `check_txt`, below.
    """

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
        except _NOT_FOUND as exc:
            log.info("dns.no_answer", host=host, rtype=rtype, reason=type(exc).__name__)
            return []
        except _RESOLVER_ERROR as exc:
            log.warning("dns.resolver_error", host=host, rtype=rtype, reason=type(exc).__name__)
            raise DnsLookupError(str(exc)) from exc
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


async def check_txt(host: str, expected: str, *, resolver: Resolver | None = None) -> Outcome:
    """Whether `expected` appears among `host`'s published TXT answers — three-state.

    Containment, not equality — the same reasoning `notification`'s `check_records` documents:
    resolvers split long TXT strings and some registrars wrap or append their own text around
    what an operator pasted in. An empty `expected` is `NOT_FOUND`, never `FOUND`: a caller
    cannot pass a blank token and have it match trivially against an unrelated TXT record.

    Callers that need to hold a domain accountable for what a check finds — `recheck_domain`'s
    demotion policy, the whole reason this returns three states rather than `txt_contains`'
    plain bool — must branch on `RESOLVER_ERROR` separately from `NOT_FOUND`. A caller that
    only wants "should I tell the customer to try again" and does not otherwise act on the
    distinction can use `txt_contains` instead.
    """
    expected = expected.strip()
    if not expected:
        return Outcome.NOT_FOUND
    r = resolver or _default_resolver()
    try:
        answers = await r.resolve(host, "TXT")
    except DnsLookupError:
        return Outcome.RESOLVER_ERROR
    return Outcome.FOUND if any(expected in answer for answer in answers) else Outcome.NOT_FOUND


async def txt_contains(host: str, expected: str, *, resolver: Resolver | None = None) -> bool:
    """Whether `expected` appears among `host`'s published TXT answers.

    A resolver error collapses to the same `False` a confirmed absence gets: this is what
    `verify_domain` uses, and a customer mid-claim is told the same "check DNS and try again"
    either way — the distinction only matters to the periodic sweep's demotion policy, which
    uses `check_txt` directly instead of this wrapper.
    """
    return await check_txt(host, expected, resolver=resolver) is Outcome.FOUND
