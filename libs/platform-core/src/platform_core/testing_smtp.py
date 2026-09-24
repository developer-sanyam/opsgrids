"""A real SMTP server on a loopback socket, with real TLS, that writes down what it was sent.

Test-only support, in the way `platform_core.testing` is: nothing in a request, consumer, relay or
cron path imports it, and it exists so that every service whose tests need to watch a real TLS
handshake — `libs/platform-core` for the shared client, `services/notification` for the transport
built on it — uses one server rather than each growing its own.

Tests of TLS *verification* cannot use a mock: what they assert is what a certificate check does
to a live handshake, and what the server was — or was not — sent afterwards. So this speaks just
enough SMTP (EHLO, STARTTLS, AUTH PLAIN, MAIL/RCPT/DATA, QUIT) over `socket` + `ssl`, in a thread,
and records for each connection whether TLS was established, which SNI name it saw, and whether a
credential ever arrived — the last being the thing a man-in-the-middle would be after.

Certificates are minted here with `cryptography` (already a dependency): a throwaway CA, and
server certificates it signs for whatever names a test asks for. Trusting the CA is the test's job
(`SSL_CERT_FILE`), so the client under test still builds its context the real way.
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import ipaddress
import socket
import ssl
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

Mode = Literal["starttls", "ssl", "plain", "starttls-not-offered"]


@dataclass
class Issued:
    """A server certificate and its key on disk, plus the CA that signed it."""

    cert_file: Path
    key_file: Path
    ca_file: Path


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _write(directory: Path, name: str, data: bytes) -> Path:
    path = directory / name
    path.write_bytes(data)
    return path


def issue(
    directory: Path, names: list[str], *, self_signed: bool = False, label: str = "srv"
) -> Issued:
    """A server certificate valid for `names` (DNS names or IPs).

    `self_signed=True` signs it with its own key, so it chains to nothing a client trusts — the
    certificate an attacker on the path would present. Otherwise it is signed by a fresh CA whose
    PEM is written alongside, for the test to trust.
    """
    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(_name("test ca"))
        .issuer_name(_name("test ca"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    sans: list[x509.GeneralName] = []
    for name in names:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            sans.append(x509.DNSName(name))
    signer_key, issuer = (key, _name(names[0])) if self_signed else (ca_key, ca_cert.subject)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(names[0]))
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .sign(signer_key, hashes.SHA256())
    )
    return Issued(
        cert_file=_write(directory, f"{label}.crt", cert.public_bytes(serialization.Encoding.PEM)),
        key_file=_write(
            directory,
            f"{label}.key",
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        ),
        ca_file=_write(
            directory, f"{label}-ca.pem", ca_cert.public_bytes(serialization.Encoding.PEM)
        ),
    )


@dataclass
class Connection:
    """What one client connection did, as the server saw it."""

    tls: bool = False
    sni: str | None = None
    #: The client's handshake failed (a certificate it would not accept, most usefully).
    handshake_failed: bool = False
    #: True if AUTH arrived, and whether that was on an encrypted channel.
    auth_seen: bool = False
    auth_over_tls: bool = False
    credentials: tuple[str, str] | None = None
    commands: list[str] = field(default_factory=list)
    mail_from: list[str] = field(default_factory=list)


class FakeSmtpServer:
    """One listening socket, one thread, connections handled one after another."""

    def __init__(self, mode: Mode, issued: Issued | None) -> None:
        self.mode = mode
        self.connections: list[Connection] = []
        self._server_context: ssl.SSLContext | None = None
        if issued is not None:
            self._server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self._server_context.load_cert_chain(str(issued.cert_file), str(issued.key_file))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.2)
        self.port: int = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._sock.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                raw, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            connection = Connection()
            self.connections.append(connection)
            try:
                self._handle(raw, connection)
            except (OSError, ssl.SSLError):
                pass
            finally:
                with contextlib.suppress(OSError):
                    raw.close()

    def _wrap(self, raw: socket.socket, connection: Connection) -> ssl.SSLSocket:
        assert self._server_context is not None

        def record_sni(_sock: object, name: str | None, _ctx: object) -> None:
            connection.sni = name

        self._server_context.sni_callback = record_sni
        try:
            wrapped = self._server_context.wrap_socket(raw, server_side=True)
        except (OSError, ssl.SSLError):
            connection.handshake_failed = True
            raise
        connection.tls = True
        return wrapped

    def _handle(self, raw: socket.socket, connection: Connection) -> None:
        raw.settimeout(5)
        sock: socket.socket = raw
        if self.mode == "ssl":
            sock = self._wrap(raw, connection)
        reader = _Reader(sock)
        sock.sendall(b"220 fake ESMTP\r\n")
        in_data = False
        while True:
            line = reader.line()
            if line is None:
                return
            if in_data:
                if line == ".":
                    in_data = False
                    sock.sendall(b"250 queued\r\n")
                continue
            verb = line.split(" ", 1)[0].upper()
            connection.commands.append(verb)
            if verb in ("EHLO", "HELO"):
                lines = ["250-fake"]
                if self.mode == "starttls" and not connection.tls:
                    lines.append("250-STARTTLS")
                lines.append("250 AUTH PLAIN")
                sock.sendall(("\r\n".join(lines) + "\r\n").encode())
            elif verb == "STARTTLS":
                if self.mode != "starttls":
                    sock.sendall(b"502 not offered\r\n")
                    continue
                sock.sendall(b"220 ready\r\n")
                sock = self._wrap(raw, connection)
                reader = _Reader(sock)
            elif verb == "AUTH":
                connection.auth_seen = True
                connection.auth_over_tls = connection.tls
                parts = line.split(" ")
                if len(parts) >= 3 and parts[1].upper() == "PLAIN":
                    _, user, password = base64.b64decode(parts[2]).decode().split("\0")
                    connection.credentials = (user, password)
                sock.sendall(b"235 ok\r\n")
            elif verb == "MAIL":
                connection.mail_from.append(line)
                sock.sendall(b"250 ok\r\n")
            elif verb == "RCPT":
                sock.sendall(b"250 ok\r\n")
            elif verb == "DATA":
                in_data = True
                sock.sendall(b"354 go\r\n")
            elif verb == "QUIT":
                sock.sendall(b"221 bye\r\n")
                return
            else:
                sock.sendall(b"250 ok\r\n")


class _Reader:
    """Lines off a socket, CRLF-terminated, buffered."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buffer = b""

    def line(self) -> str | None:
        while b"\r\n" not in self._buffer:
            chunk = self._sock.recv(4096)
            if not chunk:
                return None
            self._buffer += chunk
        raw, _, self._buffer = self._buffer.partition(b"\r\n")
        return raw.decode(errors="replace")


@contextlib.contextmanager
def running(
    mode: Mode, *, names: list[str] | None = None, self_signed: bool = False
) -> Iterator[tuple[FakeSmtpServer, Issued | None]]:
    """A started server, stopped afterwards. `names` are what its certificate is valid for; a
    `plain` server has no certificate at all."""
    with tempfile.TemporaryDirectory() as tmp:
        issued = None
        if mode != "plain":
            issued = issue(Path(tmp), names or ["127.0.0.1"], self_signed=self_signed)
        server = FakeSmtpServer(mode, issued)
        server.start()
        try:
            yield server, issued
        finally:
            server.stop()
