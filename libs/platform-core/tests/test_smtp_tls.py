"""SMTP over TLS that checks who it is talking to — against a real TLS server, not a mock.

What is being pinned is what a certificate check does to a live handshake, and above all what the
server on the other end was and was not sent afterwards: a man-in-the-middle presenting a
certificate the client should not trust must end the connection **before** the SMTP credential is
written, and `platform_core.testing_smtp` records exactly that.

The client builds its context the real way (`ssl.create_default_context()`); a test that wants a
certificate trusted points `SSL_CERT_FILE` at the throwaway CA, exactly as an operator would add
a private CA, rather than substituting the context.
"""

from __future__ import annotations

import smtplib
import ssl

import pytest

from platform_core import smtp_tls
from platform_core import testing_smtp as fake_smtp

NAME = "smtp.mail.test"
LOOPBACK = "127.0.0.1"


def _trusting(monkeypatch: pytest.MonkeyPatch, issued: fake_smtp.Issued) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", str(issued.ca_file))


def _open(
    server: fake_smtp.FakeSmtpServer,
    mode: smtp_tls.TlsMode,
    *,
    host: str = NAME,
    dial_address: str = LOOPBACK,
    verify: bool = True,
) -> smtplib.SMTP:
    return smtp_tls.open_smtp(
        host, server.port, tls_mode=mode, timeout=5, dial_address=dial_address, verify=verify
    )


# --------------------------------------------------------------------- the context


def test_the_default_context_verifies_the_certificate_and_the_hostname() -> None:
    context = smtp_tls.tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_only_an_explicit_request_turns_verification_off() -> None:
    context = smtp_tls.tls_context(verify=False)
    assert context.verify_mode == ssl.CERT_NONE
    assert context.check_hostname is False


def test_unverified_tls_is_for_development_environments_only() -> None:
    assert {"local", "test", "ci"} == smtp_tls.UNVERIFIED_TLS_ENVIRONMENTS


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="tls_mode"):
        smtp_tls.open_smtp("x", 25, tls_mode="tls13", timeout=1)  # type: ignore[arg-type]


# --------------------------------------------------------------------- STARTTLS


def test_starttls_to_a_trusted_certificate_upgrades_then_authenticates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with fake_smtp.running("starttls", names=[NAME]) as (server, issued):
        assert issued is not None
        _trusting(monkeypatch, issued)
        client = _open(server, "starttls")
        smtp_tls.login(client, "user", "s3cret", tls_mode="starttls")
        client.quit()

    (connection,) = server.connections
    assert connection.tls is True
    # The credential arrived only after the channel was encrypted, and it is the name — not the
    # address that was dialled — that the certificate was checked against and SNI carried.
    assert connection.auth_over_tls is True
    assert connection.credentials == ("user", "s3cret")
    assert connection.sni == NAME


def test_a_certificate_no_client_trusts_ends_the_connection_before_any_credential() -> None:
    """The attack: a self-signed certificate from something on the path. The default context
    refuses it, and the server never sees AUTH."""
    with (
        fake_smtp.running("starttls", names=[NAME], self_signed=True) as (server, _),
        pytest.raises(ssl.SSLCertVerificationError),
    ):
        _open(server, "starttls")

    (connection,) = server.connections
    assert connection.handshake_failed is True
    assert connection.auth_seen is False
    assert "MAIL" not in connection.commands


def test_a_trusted_certificate_for_another_name_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chaining to a trusted CA is not enough: the name being connected to must be one the
    certificate lists, or any certificate that CA ever issued would do."""
    with fake_smtp.running("starttls", names=["some.other.test"]) as (server, issued):
        assert issued is not None
        _trusting(monkeypatch, issued)
        with pytest.raises(ssl.SSLCertVerificationError):
            _open(server, "starttls")

    assert server.connections[0].auth_seen is False


def test_the_name_is_checked_not_the_dialled_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller that pinned the address (a DNS-rebinding guard) still gets the name checked. The
    certificate here lists the address and not the name: checking the address would pass, and
    it is refused."""
    with fake_smtp.running("starttls", names=[LOOPBACK]) as (server, issued):
        assert issued is not None
        _trusting(monkeypatch, issued)
        with pytest.raises(ssl.SSLCertVerificationError):
            _open(server, "starttls", host=NAME, dial_address=LOOPBACK)


def test_verification_can_be_switched_off_by_the_caller_alone() -> None:
    with fake_smtp.running("starttls", names=[NAME], self_signed=True) as (server, _):
        client = _open(server, "starttls", verify=False)
        smtp_tls.login(client, "user", "pw", tls_mode="starttls")
        client.quit()

    (connection,) = server.connections
    assert connection.tls is True
    assert connection.auth_over_tls is True


def test_a_server_that_does_not_offer_starttls_is_a_failure_not_a_downgrade() -> None:
    """STARTTLS stripped by something on the path: never a quiet fall back to plaintext."""
    with (
        fake_smtp.running("starttls-not-offered", names=[NAME]) as (server, _),
        pytest.raises(smtplib.SMTPNotSupportedError),
    ):
        _open(server, "starttls")

    assert server.connections[0].auth_seen is False


# --------------------------------------------------------------------- implicit TLS


def test_implicit_tls_to_a_trusted_certificate_works_with_a_pinned_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with fake_smtp.running("ssl", names=[NAME]) as (server, issued):
        assert issued is not None
        _trusting(monkeypatch, issued)
        client = _open(server, "ssl")
        smtp_tls.login(client, "user", "s3cret", tls_mode="ssl")
        client.quit()

    (connection,) = server.connections
    assert connection.tls is True
    assert connection.auth_over_tls is True
    assert connection.sni == NAME


def test_implicit_tls_refuses_an_untrusted_certificate() -> None:
    with (
        fake_smtp.running("ssl", names=[NAME], self_signed=True) as (server, _),
        pytest.raises(ssl.SSLCertVerificationError),
    ):
        _open(server, "ssl")

    assert server.connections[0].auth_seen is False


def test_implicit_tls_can_be_unverified_when_the_caller_says_so() -> None:
    with fake_smtp.running("ssl", names=[NAME], self_signed=True) as (server, _):
        client = _open(server, "ssl", verify=False)
        client.quit()

    assert server.connections[0].tls is True


# --------------------------------------------------------------------- login


def test_login_is_refused_without_encryption_and_the_credential_is_never_sent() -> None:
    with fake_smtp.running("plain") as (server, _):
        client = _open(server, "none")
        with pytest.raises(smtp_tls.PlaintextLoginRefused):
            smtp_tls.login(client, "user", "s3cret", tls_mode="none")
        client.quit()

    (connection,) = server.connections
    assert connection.tls is False
    assert connection.auth_seen is False


def test_an_unauthenticated_plain_connection_still_works() -> None:
    """No credential, no encryption asked for: the local-relay case is not made impossible."""
    with fake_smtp.running("plain") as (server, _):
        client = _open(server, "none")
        client.mail("a@example.com")
        client.quit()

    assert server.connections[0].mail_from


def test_the_pinned_address_is_dialled_and_the_name_is_kept() -> None:
    """Without `dial_address` the name would be resolved; with it, the socket goes where it was
    told and `_host` — what `smtplib` hands `server_hostname` — stays the name."""
    with fake_smtp.running("plain") as (server, _):
        client = smtp_tls.open_smtp(
            "unresolvable.invalid",
            server.port,
            tls_mode="none",
            timeout=5,
            dial_address=LOOPBACK,
        )
        assert vars(client)["_host"] == "unresolvable.invalid"
        client.quit()
