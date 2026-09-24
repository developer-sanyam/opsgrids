"""The one place this service performs a DNS lookup.

`app/domain.py` stays pure so the record set and the verification rules can be tested without a
network; everything that can fail because the internet is having a bad day lives here, behind an
injectable resolver — the same port-and-adapter split `transports.py` uses, for the same reason.

Three things live here, and they answer different questions about the same mechanism.
`normalize_domain_name` asks "is this a domain name at all" — pure shape, no lookup, but it lives
beside `is_public_fqdn` because it is built entirely from that one check. Checking a domain's
published records (`check_domain_records` and below) asks "what does DNS say about a name this
organization already owns, given how it says it sends", and nothing there raises — a record that
cannot be read is a record that is not verified, and the customer is mid-setup far more often than
the resolver is broken. The outbound dial guard (`assert_dialable` and below) asks "is it safe to
open a socket to a name a customer typed into a form", and it raises deliberately: a submission
host that resolves inside this service's own compose network is not a record that failed to
verify, it is a request this process should refuse to make.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from typing import Any, Final, Protocol

import dns.asyncresolver
import dns.exception
import dns.resolver

from app import domain as _rules
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


#: Every reason `normalize_domain_name` refuses an input reads as this one sentence. The
#: distinction between "that's a path", "that's a port" and "that's a single label" is exactly
#: the shape of feedback a customer does not need to fix a typo, and — more importantly — the
#: shape of feedback that would tell a probe which check it tripped.
INVALID_DOMAIN_MESSAGE: Final = "Enter just the domain, like example.com"

#: A URL scheme prefix — `https://`, `http://`, or anything else shaped like one — stripped
#: before the rest of the checks run, so a customer who pasted a URL from their browser's address
#: bar gets the domain out of it rather than a refusal.
_SCHEME: Final = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def normalize_domain_name(raw: str) -> str:
    """Canonicalise a customer-typed sending domain, or raise `ValueError`.

    Trims whitespace, strips a leading URL scheme, a trailing slash and a trailing dot, lowercases
    and IDNA-encodes a unicode name — `https://opsgrids.com/` and `OpsGrids.COM.` both become
    `opsgrids.com`. What is left after that must be a plain public hostname, and `is_public_fqdn`
    is the one test for that: a path, a port, userinfo, a query, a fragment, a wildcard, embedded
    whitespace, an IP literal and a single label are all refused by its label grammar, not by a
    separate check here for each — so this function never has to enumerate them, and every one of
    them reads back the same fixed sentence rather than a hint about which rule it tripped.
    """
    value = raw.strip()
    value = _SCHEME.sub("", value, count=1)
    value = value.rstrip("/")
    value = value.rstrip(".")
    value = value.lower()
    if not value:
        raise ValueError(INVALID_DOMAIN_MESSAGE)
    try:
        encoded = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(INVALID_DOMAIN_MESSAGE) from exc
    if not is_public_fqdn(encoded):
        raise ValueError(INVALID_DOMAIN_MESSAGE)
    return encoded


async def check_domain_records(
    domain_name: str,
    *,
    connection_kind: str | None,
    dkim_selector: str,
    spf_include: str | None,
    resolver: Resolver | None = None,
) -> list[dict[str, Any]]:
    """Verify a sending domain's DNS against what its *current* connection requires.

    Provider-aware, and re-derived from `connection_kind` on every call rather than from
    whatever the domain's own row remembers — a customer who switches from Google Workspace to
    their own SMTP relay must be re-checked against Microsoft's... against *their new*
    requirements, not have yesterday's Google check quietly keep passing. `connection_kind is
    None` (no provider connected yet) does no lookups at all and returns nothing: there is
    nothing to verify against, and `expected_records` agrees by returning an empty list.

    SPF is one apex lookup, folded by `domain.evaluate_spf`. DKIM's shape depends on the
    provider: Google publishes a TXT at the selector, Microsoft two CNAMEs at fixed selector
    names, a customer's own SMTP provider either a TXT or a CNAME at a selector they name
    themselves. DMARC is one apex-adjacent TXT lookup, never required.
    """
    if connection_kind is None:
        return []
    r = resolver or _default_resolver()
    expected = _rules.expected_records(
        domain_name,
        connection_kind=connection_kind,
        dkim_selector=dkim_selector,
        spf_include=spf_include,
    )

    out: list[dict[str, Any]] = []
    for record in expected:
        kind = record["kind"]
        if kind == "spf":
            target = _rules.spf_target(connection_kind, spf_include)
            if target is None:
                out.append({**record, "status": "missing"})
                continue
            txts = await r.resolve(record["fqdn"], "TXT")
            result = _rules.evaluate_spf(txts, required_include=target)
            out.append(
                {
                    **record,
                    "status": result.status,
                    "suggested_value": result.suggested_value,
                    "instruction": result.message,
                }
            )
        elif kind == "dkim" and connection_kind == "google":
            txts = await r.resolve(record["fqdn"], "TXT")
            out.append({**record, "status": _rules.evaluate_dkim_txt(txts)})
        elif kind == "dkim" and connection_kind == "microsoft":
            cnames = await r.resolve(record["fqdn"], "CNAME")
            out.append({**record, "status": "verified" if cnames else "missing"})
        elif kind == "dkim" and connection_kind == "smtp":
            if not dkim_selector:
                out.append({**record, "status": "missing"})
                continue
            txts = await r.resolve(record["fqdn"], "TXT")
            cnames = await r.resolve(record["fqdn"], "CNAME")
            out.append({**record, "status": _rules.evaluate_dkim_smtp(txts, cnames)})
        elif kind == "dmarc":
            txts = await r.resolve(record["fqdn"], "TXT")
            out.append(
                {**record, "status": "verified" if _rules.evaluate_dmarc(txts) else "missing"}
            )
        else:  # pragma: no cover - defensive; expected_records emits only the kinds above
            out.append({**record, "status": "missing"})
    return out


# --------------------------------------------------------------------- the outbound dial guard
#
# `check_records` above asks what DNS publishes for a name this organization already owns. This
# section asks a different question of a name a customer typed into a form: is it safe for this
# process to open a socket to it at all. The two never share a call site — conflating them would
# let a customer's SPF record and a customer's SMTP relay live under the same rules, and they do
# not carry the same risk. `services.py` is the only caller, at connection-time and again
# immediately before every dial, so a name that resolved somewhere public when it was saved and
# somewhere on this VM's compose network by the time a test send runs is refused either way.


class HostNotAllowed(Exception):
    """A submission host or port failed the outbound dial guard.

    Deliberately message-less to callers outside this module: `services.py` catches this and
    always answers with the same fixed sentence, because the difference between "that's a
    private address", "that's not a real domain" and "that port isn't for mail" is exactly the
    oracle a port scan is looking for. The reason is in `args`, for the log line only.
    """


#: What a mail submission connection may dial. Anything else is refused before DNS is even
#: asked, so a probe that only varies the port learns nothing.
ALLOWED_SUBMISSION_PORTS: Final[frozenset[int]] = frozenset({25, 465, 587, 2525})

#: One DNS label: letters, digits and interior hyphens, 1-63 octets — the wire format a
#: registrar can actually publish.
_LABEL: Final = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")

#: A label shaped like one component of the numeric address forms `socket.getaddrinfo` (via the
#: platform's `inet_aton` grammar) still resolves even though `ipaddress.ip_address` refuses the
#: assembled string outright: decimal (`2130706433`), octal (a leading zero, `017700000001`) or
#: hex (`0x7f000001`). `0177.0.0.1` and `127.1` both reach `127.0.0.1` this way despite neither
#: being a literal `ipaddress` recognises — this is the gap that closes.
_NUMERIC_LABEL: Final = re.compile(r"^(0[xX][0-9a-fA-F]+|[0-9]+)$")


def is_public_fqdn(host: str) -> bool:
    """Syntax only: a plausible, publishable, multi-label hostname — never a literal.

    Refuses every shape of IP literal this service has to care about (bracketed, plain IPv4,
    plain IPv6, and the decimal/octal/hex whole-string encodings a plain `ipaddress.ip_address`
    call does not recognise as one) and every single-label name — `localhost`, `postgres`,
    `redis`, `rabbitmq`, `account`, every hostname the compose network answers to without a
    search domain appended. It says nothing about whether the name resolves anywhere, let alone
    anywhere public; `resolve_public_addresses` and `is_global_address` answer that.
    """
    host = host.strip().rstrip(".")
    if not host or len(host) > 253 or host.startswith("[") or host.endswith("]"):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return False
    if ":" in host:
        # Not accepted above, so this is a malformed IPv6 literal rather than a hostname — ':'
        # is not a legal character in a DNS label either way.
        return False
    labels = host.split(".")
    if len(labels) < 2:
        return False
    if len(labels) <= 4 and all(_NUMERIC_LABEL.match(label) for label in labels):
        # Every label numeric-ish — `inet_aton`'s grammar, not DNS's — even though no single
        # label failed the ordinary check below.
        return False
    return all(_LABEL.match(label) for label in labels)


def is_global_address(address: str) -> bool:
    """Whether one resolved address is reachable only from the public internet.

    Built on `ipaddress.is_global`, which is not quite the same claim: it reports `True` for
    multicast and for several IETF-reserved ranges, because "global" there means "not carved out
    for a private or link-local use", not "an ordinary unicast host anyone can dial" — so both
    are excluded explicitly. Loopback, RFC 1918, link-local (`169.254.0.0/16` and `fe80::/10`),
    carrier-grade NAT (`100.64.0.0/10`), unique-local (`fc00::/7`) and the unspecified address
    are already `is_global is False`.

    An IPv6 address that merely carries an IPv4 one — IPv4-mapped (`::ffff:a.b.c.d`), 6to4,
    Teredo — is judged by the IPv4 address inside it, unwrapped here rather than left to
    `ipaddress`: whether the standard library looks through the wrapper has changed between
    Python releases, and a guard whose answer depends on the interpreter's patch level is not
    a guard.
    """
    try:
        parsed: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address):
        if parsed.ipv4_mapped is not None:
            parsed = parsed.ipv4_mapped
        else:
            embedded = [parsed.sixtofour, *(parsed.teredo or ())]
            if any(inner is not None and not is_global_address(str(inner)) for inner in embedded):
                return False
    if parsed.is_multicast or parsed.is_reserved or parsed.is_unspecified:
        return False
    return parsed.is_global


async def resolve_public_addresses(host: str, *, resolver: Resolver | None = None) -> list[str]:
    """Every A and AAAA answer for `host`, an unresolved name producing an empty list.

    The same injectable resolver `check_records` uses, for the same reason: a caller deciding
    whether to dial `host` needs this answer at two different moments — when the connection is
    saved and again immediately before the socket opens — and a test proving the second moment
    catches a renamed record needs to hand back a different answer the second time it is called.
    """
    r = resolver or _default_resolver()
    a = await r.resolve(host, "A")
    aaaa = await r.resolve(host, "AAAA")
    return [*a, *aaaa]


async def assert_dialable(host: str, port: int, *, resolver: Resolver | None = None) -> str:
    """The outbound dial guard. Raises `HostNotAllowed` unless every check passes; otherwise
    returns one validated public address to dial.

    Returning an address rather than only validating is the point: a caller that goes on to
    dial the *name* trusts a second DNS answer to whoever controls that name's zone, given at
    the exact moment it matters most. Dialling the address this function already checked closes
    that window instead of merely shrinking it.
    """
    if not is_public_fqdn(host):
        raise HostNotAllowed(f"{host!r} is not a public hostname")
    if port not in ALLOWED_SUBMISSION_PORTS:
        raise HostNotAllowed(f"port {port} is not a mail submission port")
    addresses = await resolve_public_addresses(host, resolver=resolver)
    if not addresses:
        raise HostNotAllowed(f"{host!r} did not resolve to any address")
    for address in addresses:
        if not is_global_address(address):
            raise HostNotAllowed(f"{host!r} resolves to a non-public address")
    return addresses[0]
