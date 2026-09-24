"""Opening an SMTP connection that checks who it is talking to.

`smtplib.SMTP.starttls()` with no `context=` uses `ssl._create_stdlib_context()`, which is the
**unverified** context: `verify_mode` is `CERT_NONE` and `check_hostname` is off. Anyone on the
path who presents any certificate at all is then accepted, and the very next thing the caller does
is `login()` — so the SMTP credential and the message go to whoever answered. That is what this
module exists to make impossible to do by accident:

* every TLS connection is made with a **default, verifying** context (`ssl.create_default_context`:
  `CERT_REQUIRED`, hostname checked, system trust store);
* the certificate is checked against the connection's **name**, which is not always the address
  that gets dialled. A caller that has already resolved a name to one address (to pin a
  DNS-rebinding guard, so the socket layer never makes a second lookup) passes that address as
  `dial_address` and keeps the name as `host`: the socket connects to the former, and SNI and the
  hostname check use the latter. Substituting the address for the name instead would make the
  check run against an IP no certificate lists;
* `login()` is refused on a connection that ended up unencrypted;
* verification can be switched off only by the caller saying so (`verify=False`), and callers are
  expected to gate that on `UNVERIFIED_TLS_ENVIRONMENTS` — a development server with a
  self-signed certificate is the case it exists for, never a deployed environment.

Three modes, chosen per connection because the servers a customer can point at differ:

  `starttls`  plain connect, then upgrade (port 587 by convention)
  `ssl`       encrypted from the first byte, "SMTPS" (port 465 by convention)
  `none`      no encryption. Only ever without credentials — see `login`.

`ops_monitor/mailer.py` applies the same policy for its own, differently configured transport and
keeps its own copy: its tests replace `smtplib.SMTP` wholesale and it never dials a pinned
address, so sharing would have meant changing a working, separately tested path for no gain.
"""

from __future__ import annotations

import smtplib
import ssl
from typing import Final, Literal, get_args

TlsMode = Literal["starttls", "ssl", "none"]

TLS_MODES: Final[tuple[str, ...]] = get_args(TlsMode)

#: The only environments in which a caller may pass `verify=False`. Anywhere else the
#: certificate is always checked, whatever a setting says — `ServiceSettings`-style validators
#: refuse the flag outright outside these, so a value that leaks into a deployed `.env` fails at
#: startup instead of quietly turning verification off.
UNVERIFIED_TLS_ENVIRONMENTS: Final[frozenset[str]] = frozenset({"local", "test", "ci"})


class PlaintextLoginRefused(ValueError):
    """`login()` was asked for on a connection with no encryption. The credential is never sent."""


def tls_context(*, verify: bool = True) -> ssl.SSLContext:
    """The context every TLS connection here is made with.

    `verify=True` is `ssl.create_default_context()` untouched: `CERT_REQUIRED`, `check_hostname`,
    the system trust store, TLS 1.2 or later. `verify=False` keeps the encryption and drops the
    identity check — for an environment that has no certificate worth checking, never a
    deployed one (`UNVERIFIED_TLS_ENVIRONMENTS`).
    """
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


class _PinnedSMTP(smtplib.SMTP):
    """`smtplib.SMTP` that dials `dial_address` while remembering `host` as the name.

    `smtplib` passes `self._host` to `wrap_socket(server_hostname=...)` in `starttls()`, so the
    name kept in `host` is what SNI and the certificate check see. Only the address handed to
    `socket.create_connection` changes.
    """

    def __init__(self, host: str, port: int, *, dial_address: str, timeout: float) -> None:
        # Set before `super().__init__`, which connects.
        self._dial_address = dial_address
        super().__init__(host, port, timeout=timeout)

    def _get_socket(self, host: str, port: int, timeout: float) -> object:
        return super()._get_socket(self._dial_address, port, timeout)  # type: ignore[misc]


class _PinnedSMTPSSL(smtplib.SMTP_SSL):
    """The implicit-TLS twin of `_PinnedSMTP`. `SMTP_SSL._get_socket` wraps the connected socket
    with `server_hostname=self._host`, so it needs the same address/name split."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        dial_address: str,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        self._dial_address = dial_address
        super().__init__(host, port, timeout=timeout, context=context)

    def _get_socket(self, host: str, port: int, timeout: float) -> object:
        return super()._get_socket(self._dial_address, port, timeout)  # type: ignore[misc]


def open_smtp(
    host: str,
    port: int,
    *,
    tls_mode: TlsMode,
    timeout: float,
    dial_address: str | None = None,
    verify: bool = True,
) -> smtplib.SMTP:
    """Connect, and — for `starttls` — upgrade, returning a client ready to `login`.

    `host` is the name the certificate is checked against; `dial_address` (default: `host`) is
    where the socket actually connects. Raises what `smtplib` raises — `SMTPException`, `OSError`,
    and `ssl.SSLCertVerificationError` (an `OSError`) when the certificate is wrong — and, for
    `starttls`, `SMTPNotSupportedError` if the server does not offer the upgrade: a downgrade is
    a failure here, never a fall back to plaintext.
    """
    if tls_mode not in TLS_MODES:
        raise ValueError(f"tls_mode must be one of {', '.join(TLS_MODES)}")
    dial = dial_address or host
    if tls_mode == "ssl":
        return _PinnedSMTPSSL(
            host, port, dial_address=dial, timeout=timeout, context=tls_context(verify=verify)
        )
    client = _PinnedSMTP(host, port, dial_address=dial, timeout=timeout)
    if tls_mode == "starttls":
        try:
            client.starttls(context=tls_context(verify=verify))
        except BaseException:
            client.close()
            raise
    return client


def login(client: smtplib.SMTP, username: str, password: str, *, tls_mode: TlsMode) -> None:
    """`client.login`, refused unless the connection is encrypted.

    `tls_mode` is the mode the connection was opened with, not something read back off the
    client: a caller that asks to authenticate has said what it believes the channel is, and if
    that is "nothing" the answer is no.
    """
    if tls_mode == "none":
        raise PlaintextLoginRefused(
            "refusing to send SMTP credentials over an unencrypted connection"
        )
    client.login(username, password)


__all__ = [
    "TLS_MODES",
    "UNVERIFIED_TLS_ENVIRONMENTS",
    "PlaintextLoginRefused",
    "TlsMode",
    "login",
    "open_smtp",
    "tls_context",
]
