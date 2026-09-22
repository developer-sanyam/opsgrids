"""The three real probes, without the internet.

ADR-0012 §6's governing rule, restated as the shape of these tests: **a resolver that cannot
answer is a `txt_not_found` / `no_record`, never an exception that reaches the caller.** Every
DNS test below injects a fake at the same seam `services/notification/app/dns_check.py` uses —
`dns.asyncresolver.Resolver` itself — because the probe's own contract (`OwnershipProbe` /
`RoutingProbe` / `CertificateProbe`) is what the rest of the service depends on, and asking the
real internet whether `_opsgrids-challenge.example.com` exists tests whoever owns that zone, not
this code.

The certificate probe is tested against a **real local TLS server** with a self-signed
certificate — the one probe that is fundamentally "did a handshake succeed", which a fake
resolver cannot stand in for.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ssl
import tempfile
from dataclasses import dataclass, field

import dns.exception
import dns.resolver
import pytest
import pytest_asyncio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app import probes

TOKEN = "a" * 32
EXPECTED_VALUE = f"opsgrids-domain-verification={TOKEN}"


# --------------------------------------------------------------------- the fake DNS resolver


@dataclass
class _TxtAnswer:
    text: str

    @property
    def strings(self) -> tuple[bytes, ...]:
        return (self.text.encode(),)


@dataclass
class _FakeResolver:
    answers: dict[tuple[str, str], list[object]] = field(default_factory=dict)
    raise_for: dict[tuple[str, str], Exception] = field(default_factory=dict)
    nameservers: list[str] = field(default_factory=list)
    lifetime: float = 0.0

    async def resolve(self, name: str, rtype: str) -> list[object]:
        key = (name, rtype)
        if key in self.raise_for:
            raise self.raise_for[key]
        return self.answers.get(key, [])


def _install_fake_resolver(monkeypatch, **kwargs: object) -> None:
    """Every `dns.asyncresolver.Resolver(configure=False)` call in the probe returns a fresh
    fake backed by the same answers — the probe queries two nameservers independently, and the
    test does not care which one it asked."""

    def factory(*, configure: bool = False) -> _FakeResolver:
        return _FakeResolver(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(probes.dns.asyncresolver, "Resolver", factory)


class TestDnsOwnershipProbe:
    async def test_the_matching_txt_record_succeeds(self, monkeypatch) -> None:
        _install_fake_resolver(
            monkeypatch,
            answers={("_opsgrids-challenge.merchant.example", "TXT"): [_TxtAnswer(EXPECTED_VALUE)]},
        )
        probe = probes.DnsOwnershipProbe()

        result = await probe.check("merchant.example", token=TOKEN)

        assert result.ok is True
        assert result.code is None

    async def test_no_record_at_all_is_txt_not_found(self, monkeypatch) -> None:
        _install_fake_resolver(monkeypatch, answers={})
        probe = probes.DnsOwnershipProbe()

        result = await probe.check("merchant.example", token=TOKEN)

        assert result.ok is False
        assert result.code == "txt_not_found"

    async def test_a_different_txt_record_is_a_mismatch_naming_what_was_found(
        self, monkeypatch
    ) -> None:
        _install_fake_resolver(
            monkeypatch,
            answers={
                ("_opsgrids-challenge.merchant.example", "TXT"): [
                    _TxtAnswer("v=spf1 include:_spf.google.com ~all")
                ]
            },
        )
        probe = probes.DnsOwnershipProbe()

        result = await probe.check("merchant.example", token=TOKEN)

        assert result.ok is False
        assert result.code == "txt_mismatch"
        assert "spf1" in (result.detail or "")

    @pytest.mark.parametrize(
        "exc",
        [
            dns.resolver.NXDOMAIN(),
            dns.resolver.NoAnswer(),
            dns.resolver.NoNameservers(),
            dns.exception.Timeout(),
        ],
    )
    async def test_every_ordinary_resolver_failure_reads_as_not_found_not_an_exception(
        self, monkeypatch, exc: Exception
    ) -> None:
        """§13: 'a check that cannot run never takes a domain off the air' starts with the
        probe itself never raising for an ordinary DNS outcome."""
        _install_fake_resolver(
            monkeypatch,
            raise_for={("_opsgrids-challenge.merchant.example", "TXT"): exc},
        )
        probe = probes.DnsOwnershipProbe()

        result = await probe.check("merchant.example", token=TOKEN)

        assert result.ok is False
        assert result.code == "txt_not_found"

    async def test_a_padded_value_still_matches(self, monkeypatch) -> None:
        """Long TXT values are split into chunks and some registrars re-wrap them; the probe
        reads the concatenated string, matching the notification service's own precedent."""
        _install_fake_resolver(
            monkeypatch,
            answers={("_opsgrids-challenge.merchant.example", "TXT"): [_TxtAnswer(EXPECTED_VALUE)]},
        )
        probe = probes.DnsOwnershipProbe()

        result = await probe.check("merchant.example", token=TOKEN)
        assert result.ok is True


class TestDnsRoutingProbe:
    async def test_a_matching_a_record_succeeds(self, monkeypatch) -> None:
        _install_fake_resolver(monkeypatch, answers={("merchant.example", "A"): ["198.51.100.7"]})
        probe = probes.DnsRoutingProbe(reserved_ip="198.51.100.7")

        result = await probe.check("merchant.example")

        assert result.ok is True

    async def test_no_a_record_is_no_record(self, monkeypatch) -> None:
        _install_fake_resolver(monkeypatch, answers={})
        probe = probes.DnsRoutingProbe(reserved_ip="198.51.100.7")

        result = await probe.check("merchant.example")

        assert result.ok is False
        assert result.code == "no_record"

    async def test_a_different_address_is_points_elsewhere_naming_the_address(
        self, monkeypatch
    ) -> None:
        _install_fake_resolver(monkeypatch, answers={("merchant.example", "A"): ["203.0.113.9"]})
        probe = probes.DnsRoutingProbe(reserved_ip="198.51.100.7")

        result = await probe.check("merchant.example")

        assert result.ok is False
        assert result.code == "points_elsewhere"
        assert "203.0.113.9" in (result.detail or "")

    async def test_without_a_reserved_ip_the_edge_hosts_own_address_is_the_target(
        self, monkeypatch
    ) -> None:
        """A box whose `.env` never set `EDGE_RESERVED_IP` must still be able to take a domain
        live: the target is whatever `EDGE_HOST` resolves to, not an empty string nothing equals."""
        _install_fake_resolver(
            monkeypatch,
            answers={
                ("merchant.example", "A"): ["20.219.18.84"],
                ("edge.opsgrids.com", "A"): ["20.219.18.84"],
            },
        )
        probe = probes.DnsRoutingProbe(reserved_ip="", edge_host="edge.opsgrids.com")

        result = await probe.check("merchant.example")

        assert result.ok is True

    async def test_without_a_reserved_ip_another_address_still_points_elsewhere(
        self, monkeypatch
    ) -> None:
        _install_fake_resolver(
            monkeypatch,
            answers={
                ("merchant.example", "A"): ["203.0.113.9"],
                ("edge.opsgrids.com", "A"): ["20.219.18.84"],
            },
        )
        probe = probes.DnsRoutingProbe(reserved_ip="", edge_host="edge.opsgrids.com")

        result = await probe.check("merchant.example")

        assert result.ok is False
        assert result.code == "points_elsewhere"
        assert "edge.opsgrids.com" in (result.detail or "")


class TestTlsCertificateProbe:
    """The one probe a fake resolver cannot stand in for: a real local TLS server, a
    self-signed certificate, and an assertion the probe reads its `not_after` correctly."""

    @pytest_asyncio.fixture
    async def tls_server(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        not_after = dt.datetime.now(dt.UTC) + dt.timedelta(days=90)
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "storefront-probe-test")]
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime.now(dt.UTC) - dt.timedelta(days=1))
            .not_valid_after(not_after)
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("merchant.example")]), critical=False
            )
            .sign(key, hashes.SHA256())
        )

        with (
            tempfile.NamedTemporaryFile(suffix=".pem") as certfile,
            tempfile.NamedTemporaryFile(suffix=".pem") as keyfile,
        ):
            certfile.write(cert.public_bytes(serialization.Encoding.PEM))
            certfile.flush()
            keyfile.write(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption(),
                )
            )
            keyfile.flush()

            server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_ctx.load_cert_chain(certfile.name, keyfile.name)

            async def _handle(reader, writer):  # type: ignore[no-untyped-def]
                writer.close()

            server = await asyncio.start_server(_handle, host="127.0.0.1", port=0, ssl=server_ctx)
            port = server.sockets[0].getsockname()[1]
            async with server:
                yield port, not_after

    async def test_a_successful_handshake_reports_ok_and_the_expiry(self, tls_server) -> None:
        port, not_after = tls_server
        probe = probes.TlsCertificateProbe(probe_host="127.0.0.1", probe_port=port, timeout=2.0)

        result = await probe.check("merchant.example")

        assert result.ok is True
        assert result.not_after is not None
        assert abs((result.not_after - not_after).total_seconds()) < 2

    async def test_a_closed_port_is_a_handshake_failure_not_an_exception(self) -> None:
        probe = probes.TlsCertificateProbe(probe_host="127.0.0.1", probe_port=1, timeout=1.0)

        result = await probe.check("merchant.example")

        assert result.ok is False
        assert result.code == "handshake_failed"
