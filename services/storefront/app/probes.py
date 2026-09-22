"""The three probes ADR-0012 §6 names, behind a small `Protocol` per kind so `app/cron.py`,
`app/services.py`'s "Check now" and the test suite all drive the same fake.

**The governing rule, restated because it decides what "failure" means in this file:**

    A check that cannot run never takes a domain off the air (§6).

That rule is enforced *above* this module, in `app/cron.py` and `app/services.py`: an
unexpected exception here propagates, and the caller's job is to catch it, log it, and leave
the claim row untouched rather than write a negative state from a bug. **This module therefore
never raises for an ordinary DNS or TLS outcome** — an `NXDOMAIN`, a resolver timeout after both
public resolvers have been tried, a refused handshake — because those are real, structural
answers about the world (the record is not there; nothing is listening), not a failure of this
process to ask the question. `ProbeResult.ok = False` is how each of those is reported, with a
stable `code` a client can switch on and a `detail` naming what was actually found — "every
failure shows what we looked for and what we found" (§6) starts here.

Each probe is deliberately narrow — one question, one answer — because a single "is it working"
boolean is exactly the pending-forever failure ADR-0012 was written against.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ssl
from dataclasses import dataclass
from typing import Any, Protocol

import dns.asyncresolver
import dns.exception
import dns.resolver
from cryptography import x509
from cryptography.hazmat.backends import default_backend

from app import domain
from platform_core.logging import get_logger

log = get_logger("storefront.probes")

#: Every way a lookup ends with nothing to compare against. Read together with
#: `services/notification/app/dns_check.py`'s identical list: `NXDOMAIN` and `NoAnswer` are the
#: ordinary "not published"; `NoNameservers` and `Timeout` are the resolver's or the zone's own
#: trouble. All four read as "no answer" to a caller deciding whether a record is live — the ADR
#: names this explicitly for DNS (§13: "5s per query, 2 resolvers, then a failure").
_NO_ANSWER = (
    dns.resolver.NXDOMAIN,
    dns.resolver.NoAnswer,
    dns.resolver.NoNameservers,
    dns.exception.Timeout,
)

#: Two public resolvers, queried independently rather than through whatever the VM's own
#: `/etc/resolv.conf` names (§13's "dangerous" failure mode: a negatively cached answer on our
#: own resolver would keep failing a merchant who just added the record for the length of that
#: cache's TTL, which we do not control and did not choose).
DEFAULT_PUBLIC_RESOLVERS: tuple[str, str] = ("1.1.1.1", "8.8.8.8")

#: Per-resolver timeout (§6, §13).
PROBE_TIMEOUT_SECONDS = 5.0

#: How much of a probe's raw answer is kept in `detail`. Matches
#: `models.LAST_ERROR_DETAIL_LENGTH` — truncating here as well as at the column means a probe
#: never depends on the database to protect it from an oversized or hostile answer.
_DETAIL_LIMIT = 500


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """One probe's answer. `code` is `None` exactly when `ok` is `True`."""

    ok: bool
    code: str | None = None
    detail: str | None = None
    #: The certificate probe's extra fact; `None` for the other two.
    not_after: dt.datetime | None = None


def _truncate(text: str) -> str:
    return text if len(text) <= _DETAIL_LIMIT else text[: _DETAIL_LIMIT - 1] + "…"


class OwnershipProbe(Protocol):
    async def check(self, host: str, *, token: str) -> ProbeResult: ...


class RoutingProbe(Protocol):
    async def check(self, host: str) -> ProbeResult: ...


class CertificateProbe(Protocol):
    async def check(self, host: str) -> ProbeResult: ...


@dataclass(frozen=True, slots=True)
class ProbeSet:
    """The three probes bundled, so `app/services.py` and `app/cron.py` take one dependency
    rather than three positional ones. Tests construct this from fakes."""

    ownership: OwnershipProbe
    routing: RoutingProbe
    certificate: CertificateProbe


def _txt_strings(rdata: Any) -> str:
    """One TXT answer's text, joined — a DNS TXT record is one or more <=255-byte chunks, and
    reading `to_text()` instead would hand back the chunks still quoted and separated, which
    matches nothing the merchant was told to publish (mirrors
    `services/notification/app/dns_check.py:_answer_text`)."""
    strings: tuple[bytes, ...] | None = getattr(rdata, "strings", None)
    if strings is not None:
        return b"".join(strings).decode("utf-8", "replace")
    return str(rdata.to_text())  # pragma: no cover - defensive, TXT always carries `strings`


class DnsOwnershipProbe:
    """The ownership probe (ADR-0012 §3, §6): a TXT lookup at
    `_opsgrids-challenge.<host>` against two public resolvers, independently."""

    def __init__(
        self,
        *,
        resolvers: tuple[str, ...] = DEFAULT_PUBLIC_RESOLVERS,
        timeout: float = PROBE_TIMEOUT_SECONDS,
    ) -> None:
        self._resolvers = resolvers
        self._timeout = timeout

    async def check(self, host: str, *, token: str) -> ProbeResult:
        name = domain.verification_txt_name(host)
        expected = domain.verification_txt_value(token)
        found: list[str] = []
        for nameserver in self._resolvers:
            resolver = dns.asyncresolver.Resolver(configure=False)
            resolver.nameservers = [nameserver]
            resolver.lifetime = self._timeout
            try:
                answer = await resolver.resolve(name, "TXT")
            except _NO_ANSWER:
                continue
            except Exception as exc:  # pragma: no cover - defensive, an unexpected dnspython error
                log.warning(
                    "storefront.domain.probe.ownership_resolver_error",
                    host=host,
                    nameserver=nameserver,
                    error=str(exc)[:200],
                )
                continue
            found.extend(_txt_strings(rdata) for rdata in answer)

        if not found:
            return ProbeResult(
                ok=False,
                code="txt_not_found",
                detail=f"No TXT record at {name}. Both resolvers returned no answer.",
            )
        if expected in found:
            return ProbeResult(ok=True, detail=f"Found {expected}")
        return ProbeResult(
            ok=False,
            code="txt_mismatch",
            detail=_truncate(found[0]),
        )


class DnsRoutingProbe:
    """The routing probe (ADR-0012 §4, §6): an A lookup for `<host>`, compared with the
    reserved IP. dnspython follows a CNAME (and therefore an ALIAS, which publishes as A/AAAA at
    the wire) to its final A answer on its own, so one query covers both routes §4 offers."""

    def __init__(
        self,
        *,
        reserved_ip: str,
        edge_host: str = "",
        resolvers: tuple[str, ...] = DEFAULT_PUBLIC_RESOLVERS,
        timeout: float = PROBE_TIMEOUT_SECONDS,
    ) -> None:
        self._reserved_ip = reserved_ip
        self._edge_host = edge_host
        self._resolvers = resolvers
        self._timeout = timeout

    async def _expected(self) -> set[str]:
        """What `<host>` must resolve to. The reserved IP when the box states one; otherwise
        whatever `edge_host` resolves to. The box's `.env` always carries `EDGE_HOST` (the deploy
        refuses to run without it) but not necessarily `EDGE_RESERVED_IP`, and an empty value
        here would otherwise fail every routing probe forever — no domain could ever go live."""
        if self._reserved_ip:
            return {self._reserved_ip}
        if not self._edge_host:
            return set()
        return set(await self._resolve_a(self._edge_host))

    async def check(self, host: str) -> ProbeResult:
        addresses = await self._resolve_a(host)
        if not addresses:
            target = self._reserved_ip or self._edge_host or "the platform address"
            return ProbeResult(
                ok=False,
                code="no_record",
                detail=f"{host} has no A record. Point it at {target}.",
            )
        expected = await self._expected()
        matched = expected.intersection(addresses)
        if matched:
            return ProbeResult(ok=True, detail=f"{host} resolves to {sorted(matched)[0]}")
        return ProbeResult(
            ok=False,
            code="points_elsewhere",
            detail=(
                f"{host} resolves to {addresses[0]}. It needs to resolve to "
                f"{self._reserved_ip or self._edge_host or 'the platform address'}."
            ),
        )

    async def _resolve_a(self, host: str) -> list[str]:
        addresses: list[str] = []
        for nameserver in self._resolvers:
            resolver = dns.asyncresolver.Resolver(configure=False)
            resolver.nameservers = [nameserver]
            resolver.lifetime = self._timeout
            try:
                answer = await resolver.resolve(host, "A")
            except _NO_ANSWER:
                continue
            except Exception as exc:  # pragma: no cover - defensive
                log.warning(
                    "storefront.domain.probe.routing_resolver_error",
                    host=host,
                    nameserver=nameserver,
                    error=str(exc)[:200],
                )
                continue
            addresses.extend(str(rdata) for rdata in answer)
            if addresses:
                break  # one resolver with an answer is enough; the second is a retry, not a vote
        return addresses


class TlsCertificateProbe:
    """The certificate probe (ADR-0012 §6): a TLS handshake to **Caddy on the internal
    network**, SNI set to `<host>`. Deliberately not a request out through the internet — it
    tests the thing we control separately from the thing the merchant controls, and it doubles
    as the warm-up for on-demand issuance.

    A raw `ssl.SSLContext` over `asyncio.open_connection` rather than `httpx`, per ADR-0012
    §17's own uncertainty about whether `httpx` exposes the SNI override cleanly for this case —
    this is the fallback the ADR names, built directly rather than discovered later.
    """

    def __init__(
        self,
        *,
        probe_host: str,
        probe_port: int = 443,
        timeout: float = PROBE_TIMEOUT_SECONDS,
    ) -> None:
        self._probe_host = probe_host
        self._probe_port = probe_port
        self._timeout = timeout

    def _context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # We are not verifying that *this* box trusts the certificate — we are asking whether a
        # certificate for `host` exists and what it says. Caddy on-demand-issues per name, so
        # the identity check that matters is the origin's (§7's gate 2), not this handshake's.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def check(self, host: str) -> ProbeResult:
        try:
            async with asyncio.timeout(self._timeout):
                _reader, writer = await asyncio.open_connection(
                    self._probe_host,
                    self._probe_port,
                    ssl=self._context(),
                    server_hostname=host,
                )
        except (TimeoutError, OSError, ssl.SSLError) as exc:
            return ProbeResult(
                ok=False,
                code="handshake_failed",
                detail=_truncate(f"TLS handshake for {host} against the edge failed: {exc}"),
            )

        try:
            ssl_object = writer.get_extra_info("ssl_object")
            der = ssl_object.getpeercert(binary_form=True) if ssl_object is not None else None
        finally:
            writer.close()

        if not der:  # pragma: no cover - defensive, a completed TLS_CLIENT handshake always has one
            return ProbeResult(
                ok=False, code="handshake_failed", detail=f"No certificate presented for {host}."
            )

        cert = x509.load_der_x509_certificate(der, default_backend())
        not_after = cert.not_valid_after_utc
        return ProbeResult(ok=True, detail=f"Issued by {_issuer_name(cert)}", not_after=not_after)


def _issuer_name(cert: x509.Certificate) -> str:
    org = cert.issuer.get_attributes_for_oid(x509.NameOID.ORGANIZATION_NAME)
    if org:
        return str(org[0].value)
    common_name = cert.issuer.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    return str(common_name[0].value) if common_name else "an unknown issuer"


def default_probe_set() -> ProbeSet:
    """The real probes, built fresh from settings each call rather than cached at import time —
    this module is imported by the API process, which must start whether or not the box's
    resolvers or edge address are configured yet, and a test that monkeypatches `settings()`
    expects the next call to see it."""
    from app.config import settings

    cfg = settings()
    return ProbeSet(
        ownership=DnsOwnershipProbe(timeout=cfg.tls_probe_timeout_seconds),
        routing=DnsRoutingProbe(
            reserved_ip=cfg.edge_reserved_ip,
            edge_host=cfg.edge_host,
            timeout=cfg.tls_probe_timeout_seconds,
        ),
        certificate=TlsCertificateProbe(
            probe_host=cfg.tls_probe_host,
            probe_port=cfg.tls_probe_port,
            timeout=cfg.tls_probe_timeout_seconds,
        ),
    )
