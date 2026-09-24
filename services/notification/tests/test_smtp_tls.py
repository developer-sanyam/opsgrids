"""SMTP over TLS — the transport verifies who it sends a credential to.

The finding: `SmtpTransport` called `client.starttls()` with no context, and `smtplib` then uses
the stdlib's **unverified** one — any certificate accepted, and the very next call is `login()`.
So anyone on the path (or answering for a customer-typed hostname) got the SMTP credential and
the message. Three layers are pinned here, from the outside in:

* the transport, against a real TLS server (`platform_core.testing_smtp`): an untrusted or
  mis-named certificate ends the connection before AUTH is written, a trusted one carries the
  credential only after the upgrade, and the certificate is checked against the connection's
  *name* even though the socket dials a pinned address;
* the rules about which TLS modes may be stored and used: `none` never alongside a credential,
  verification off only in `local`/`test`/`ci`;
* the connection writers and the transport builder that apply those rules.

The mechanics of the connect-and-verify step itself are `libs/platform-core/tests/test_smtp_tls.py`.
"""

from __future__ import annotations

import errno
import smtplib
import uuid

import pytest
from pydantic import ValidationError

from app import domain, models, services, transports
from app.config import Settings
from platform_core import testing_smtp as fake_smtp
from platform_core.auth import Scope
from platform_core.errors import BusinessRuleProblem, ValidationProblem
from tests.helpers import ctx_for

NAME = "smtp.customer.test"
LOOPBACK = "127.0.0.1"

#: What `create_connection` needs to see resolve, so a customer-typed host passes the dial guard.
PUBLIC = {(NAME, "A"): ["8.8.8.8"]}

#: Distinct from the secrets `Settings` refuses in a deployed environment.
_REAL_SECRET = "z" * 48


class _Resolver:
    def __init__(self, answers: dict[tuple[str, str], list[str]]) -> None:
        self._answers = answers

    async def resolve(self, host: str, rtype: str) -> list[str]:
        return self._answers.get((host, rtype), [])


def _transport(
    server: fake_smtp.FakeSmtpServer,
    mode: str,
    *,
    host: str = NAME,
    verify: bool = True,
    username: str | None = "user",
    password: str | None = "s3cret",  # noqa: S107  # pragma: allowlist secret
) -> transports.SmtpTransport:
    return transports.SmtpTransport(
        host=host,
        dial_address=LOOPBACK,
        port=server.port,
        username=username,
        password=password,
        tls_mode=mode,  # type: ignore[arg-type]
        verify_tls=verify,
        timeout=5.0,
        from_address="from@example.com",
    )


def _message(to: str = "a@b.example.com") -> transports.Outgoing:
    return transports.Outgoing(to_address=to, subject="Hi", body="Body")


def _trusting(monkeypatch: pytest.MonkeyPatch, issued: fake_smtp.Issued | None) -> None:
    assert issued is not None
    monkeypatch.setenv("SSL_CERT_FILE", str(issued.ca_file))


# --------------------------------------------------------------------- the transport, live TLS


async def test_a_verified_starttls_send_authenticates_only_after_the_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with fake_smtp.running("starttls", names=[NAME]) as (server, issued):
        _trusting(monkeypatch, issued)
        receipt = await _transport(server, "starttls").send(_message())

    assert receipt.provider == "smtp"
    (connection,) = server.connections
    assert connection.tls is True
    assert connection.auth_over_tls is True
    assert connection.credentials == ("user", "s3cret")
    # The certificate was checked against the connection's name, not the address dialled.
    assert connection.sni == NAME
    assert connection.mail_from


async def test_an_untrusted_certificate_gets_no_credential_and_no_message() -> None:
    """The reported attack. A self-signed certificate — what something on the path would
    present — is refused, and the server is never sent AUTH or a single byte of mail."""
    with (
        fake_smtp.running("starttls", names=[NAME], self_signed=True) as (server, _),
        pytest.raises(transports.TransportTlsError) as failure,
    ):
        await _transport(server, "starttls").send(_message())

    (connection,) = server.connections
    assert connection.handshake_failed is True
    assert connection.auth_seen is False
    assert connection.mail_from == []
    assert str(failure.value).startswith("TLS certificate verification failed (code ")
    # A fixed shape with a numeric reason: nothing the handshake said about the host.
    assert "smtp.customer.test" not in str(failure.value)


async def test_a_certificate_for_another_name_is_refused_even_when_a_pinned_address_is_dialled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Customer connections dial the address the dial guard resolved. Verifying against that
    address instead of the name would either always fail (no certificate lists an IP) or, made to
    pass, check nothing — so the name is what the certificate must list."""
    with fake_smtp.running("starttls", names=[LOOPBACK]) as (server, issued):
        _trusting(monkeypatch, issued)
        with pytest.raises(transports.TransportTlsError):
            await _transport(server, "starttls", host=NAME).send(_message())

    assert server.connections[0].auth_seen is False


async def test_implicit_tls_is_supported_and_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    with fake_smtp.running("ssl", names=[NAME]) as (server, issued):
        _trusting(monkeypatch, issued)
        await _transport(server, "ssl").send(_message())

    (connection,) = server.connections
    assert connection.tls is True
    assert connection.auth_over_tls is True
    assert connection.sni == NAME


async def test_implicit_tls_with_an_untrusted_certificate_sends_nothing() -> None:
    with (
        fake_smtp.running("ssl", names=[NAME], self_signed=True) as (server, _),
        pytest.raises(transports.TransportTlsError),
    ):
        await _transport(server, "ssl").send(_message())

    assert server.connections[0].auth_seen is False


async def test_a_batch_fails_as_one_when_the_certificate_does_not_verify() -> None:
    """`send_many`'s contract: a connection-level failure fails every message in the batch, and
    here that is a TLS failure — retryable (a renewed certificate fixes it), not a refusal."""
    with fake_smtp.running("starttls", names=[NAME], self_signed=True) as (server, _):
        results = await _transport(server, "starttls").send_many(
            [_message(), _message("c@d.example.com")]
        )

    assert all(isinstance(r, transports.TransportTlsError) for r in results)
    assert all(isinstance(r, transports.TransportUnavailable) for r in results)


async def test_verification_off_lets_a_self_signed_server_through_and_only_then() -> None:
    """The development escape hatch (`SMTP_ALLOW_UNVERIFIED_TLS`, gated by environment below):
    still encrypted, identity not checked."""
    with fake_smtp.running("starttls", names=[NAME], self_signed=True) as (server, _):
        await _transport(server, "starttls", verify=False).send(_message())

    (connection,) = server.connections
    assert connection.tls is True
    assert connection.auth_over_tls is True


async def test_a_stripped_starttls_is_a_failure_never_a_plaintext_fallback() -> None:
    with (
        fake_smtp.running("starttls-not-offered", names=[NAME]) as (server, _),
        pytest.raises(transports.TransportUnavailable) as failure,
    ):
        await _transport(server, "starttls").send(_message())

    assert server.connections[0].auth_seen is False
    assert str(failure.value) == "SMTPNotSupportedError"


def test_a_transport_that_would_send_credentials_unencrypted_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="unencrypted"):
        transports.SmtpTransport(
            host=NAME,
            port=25,
            username="user",
            password="s3cret",  # pragma: allowlist secret
            tls_mode="none",
            timeout=1.0,
            from_address="from@example.com",
        )


async def test_no_encryption_is_still_possible_without_a_credential() -> None:
    """A local relay that authenticates by network position: not made impossible."""
    with fake_smtp.running("plain") as (server, _):
        receipt = await _transport(server, "none", username=None, password=None).send(_message())

    assert receipt.provider == "smtp"
    assert server.connections[0].tls is False
    assert server.connections[0].auth_seen is False


# ------------------------------------------- no text from the server reaches an error


async def test_a_recipient_refusal_does_not_carry_the_servers_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`550 5.1.1 <jane@doe.com>: Recipient address rejected` says whose mailbox it is. The
    error keeps the exception type and the reply code and nothing else."""
    refusal = smtplib.SMTPRecipientsRefused(
        {"jane@doe.example.com": (550, b"5.1.1 <jane@doe.example.com> user unknown")}
    )
    assert transports._describe_smtp_error(refusal) == "SMTPRecipientsRefused 550"

    auth = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    assert transports._describe_smtp_error(auth) == "SMTPAuthenticationError 535"

    assert transports._describe_smtp_error(
        ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
    ) == ("ConnectionRefusedError ECONNREFUSED")
    assert transports._describe_smtp_error(ValueError("secret header value")) == "ValueError"


# --------------------------------------------------------------------- the modes that may be used


def test_none_is_refused_with_a_credential_and_the_default_follows_the_port() -> None:
    assert domain.resolve_tls_mode(kind="smtp", requested=None, port=587, has_credentials=True) == (
        "starttls"
    )
    assert domain.resolve_tls_mode(kind="smtp", requested=None, port=465, has_credentials=True) == (
        "ssl"
    )
    assert domain.resolve_tls_mode(
        kind="smtp", requested="ssl", port=587, has_credentials=True
    ) == ("ssl")
    with pytest.raises(ValueError, match="cannot carry a username and password"):
        domain.resolve_tls_mode(kind="smtp", requested="none", port=25, has_credentials=True)
    # No credential, so nothing to protect from being sent in the clear.
    assert domain.resolve_tls_mode(
        kind="smtp", requested="none", port=25, has_credentials=False
    ) == ("none")
    with pytest.raises(ValueError, match="tls_mode must be one of"):
        domain.resolve_tls_mode(kind="smtp", requested="tls1.3", port=587, has_credentials=True)


def test_a_named_provider_is_always_starttls_whatever_the_caller_asked() -> None:
    for kind in ("google", "microsoft"):
        assert domain.resolve_tls_mode(
            kind=kind, requested="none", port=25, has_credentials=True
        ) == ("starttls")


def test_a_row_from_before_the_column_is_read_from_use_tls_and_the_port() -> None:
    assert domain.effective_tls_mode(None, use_tls=True, port=587) == "starttls"
    assert domain.effective_tls_mode(None, use_tls=True, port=465) == "ssl"
    assert domain.effective_tls_mode(None, use_tls=False, port=25) == "none"
    assert domain.effective_tls_mode("ssl", use_tls=True, port=587) == "ssl"


# --------------------------------------------------------------------- settings


def _deployed(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "production",
        "internal_service_token": _REAL_SECRET,
        "pii_encryption_key": _REAL_SECRET,
        # The dev `.env` this suite runs against turns it on; a deployed environment refuses it.
        "debug": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize("environment", ["production", "staging", "uat"])
def test_unverified_tls_is_refused_at_startup_in_every_deployed_environment(
    environment: str,
) -> None:
    with pytest.raises((ValueError, ValidationError), match="smtp_allow_unverified_tls"):
        _deployed(environment=environment, smtp_allow_unverified_tls=True)


@pytest.mark.parametrize("environment", ["local", "test", "ci"])
def test_unverified_tls_is_allowed_only_where_nothing_real_is_sent(environment: str) -> None:
    cfg = Settings(environment=environment, smtp_allow_unverified_tls=True)
    assert cfg.smtp_verify_tls is False


def test_verification_is_on_by_default_and_the_flag_alone_is_not_enough_outside_dev() -> None:
    assert Settings().smtp_verify_tls is True
    assert Settings(environment="test").smtp_verify_tls is True
    # Even if the startup check were bypassed (assignment does not re-run it), the environment
    # is consulted again here.
    forced = Settings()
    forced.environment = "production"
    forced.smtp_allow_unverified_tls = True
    assert forced.smtp_verify_tls is True


def test_starttls_and_implicit_tls_are_mutually_exclusive() -> None:
    with pytest.raises((ValueError, ValidationError), match="mutually exclusive"):
        Settings(smtp_use_tls=True, smtp_use_ssl=True)


def test_the_platform_smtp_tls_mode_follows_the_flags() -> None:
    assert Settings().smtp_tls_mode == "none"
    assert Settings(smtp_use_tls=True).smtp_tls_mode == "starttls"
    assert Settings(smtp_use_ssl=True).smtp_tls_mode == "ssl"


def test_build_passes_the_tls_settings_to_the_smtp_transport() -> None:
    built = transports.build(Settings(email_transport="smtp", smtp_use_ssl=True))["email"]
    assert isinstance(built, transports.SmtpTransport)
    assert built._tls_mode == "ssl"
    assert built._verify_tls is True


def test_build_refuses_to_start_with_smtp_credentials_and_no_tls() -> None:
    """A deployed service that would send the platform's SMTP password unencrypted does not
    start, the same way it does not start half-configured for WhatsApp."""
    cfg = Settings(email_transport="smtp", smtp_username="mailer", smtp_password="pw")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unencrypted"):
        transports.build(cfg)


# --------------------------------------------------------------------- connections, end to end


def _ctx(org_id: uuid.UUID):  # type: ignore[no-untyped-def]
    return ctx_for(org_id, "engage.setup.connection.write", Scope.ORG)


@pytest.mark.integration
async def test_a_custom_smtp_connection_is_stored_with_the_mode_its_port_implies(
    session, org_id
) -> None:
    async with session.begin():
        conn = await services.create_connection(
            session,
            _ctx(org_id),
            kind="smtp",
            username="ops@company.com",
            secret="pw",  # pragma: allowlist secret
            host=NAME,
            port=465,
            resolver=_Resolver(PUBLIC),
        )
    assert (conn.tls_mode, conn.use_tls) == ("ssl", True)


@pytest.mark.integration
async def test_an_unencrypted_connection_with_a_credential_is_refused_and_nothing_is_stored(
    session, org_id
) -> None:
    from sqlalchemy import select

    with pytest.raises(ValidationProblem) as refusal:
        async with session.begin():
            await services.create_connection(
                session,
                _ctx(org_id),
                kind="smtp",
                username="ops@company.com",
                secret="pw",  # pragma: allowlist secret
                host=NAME,
                port=25,
                tls_mode="none",
                resolver=_Resolver(PUBLIC),
            )

    assert "cannot carry a username and password" in str(refusal.value)
    assert (await session.execute(select(models.EmailConnection))).scalars().all() == []


@pytest.mark.integration
async def test_replacing_a_connection_cannot_downgrade_it_to_plaintext(session, org_id) -> None:
    async with session.begin():
        await services.create_connection(
            session,
            _ctx(org_id),
            kind="google",
            username="info@company.com",
            secret="pw",  # pragma: allowlist secret
        )
    with pytest.raises(ValidationProblem):
        async with session.begin():
            await services.replace_connection(
                session,
                _ctx(org_id),
                kind="smtp",
                username="ops@company.com",
                secret="pw",  # pragma: allowlist secret
                host=NAME,
                port=25,
                tls_mode="none",
                resolver=_Resolver(PUBLIC),
            )
    async with session.begin():
        row = await services.selectors.get_connection(session, org_id)
    assert row is not None
    assert (row.kind, row.tls_mode) == ("google", "starttls")


@pytest.mark.integration
async def test_a_named_provider_ignores_the_callers_tls_mode(session, org_id) -> None:
    """Like `host` and `port`: the provider's endpoint is not the caller's to describe."""
    async with session.begin():
        conn = await services.create_connection(
            session,
            _ctx(org_id),
            kind="google",
            username="info@company.com",
            secret="pw",  # pragma: allowlist secret
            tls_mode="none",
        )
    assert (conn.tls_mode, conn.use_tls) == ("starttls", True)


@pytest.mark.integration
async def test_the_transport_built_for_a_connection_verifies_the_name_and_dials_the_address(
    session, org_id
) -> None:
    async with session.begin():
        conn = await services.create_connection(
            session,
            _ctx(org_id),
            kind="smtp",
            username="ops@company.com",
            secret="pw",  # pragma: allowlist secret
            host=NAME,
            port=587,
            resolver=_Resolver(PUBLIC),
        )
        built = await services._transport_for_connection(
            conn, from_address="a@company.com", resolver=_Resolver(PUBLIC)
        )

    assert isinstance(built, transports.SmtpTransport)
    assert built._host == NAME
    assert built._dial_address == "8.8.8.8"
    assert built._tls_mode == "starttls"
    assert built._verify_tls is True


@pytest.mark.integration
async def test_a_legacy_row_with_no_tls_mode_is_still_sent_over_starttls(session, org_id) -> None:
    """Every connection written before the column existed has `use_tls` true and no mode."""
    async with session.begin():
        legacy = models.EmailConnection(
            organization_id=org_id,
            kind="smtp",
            host=NAME,
            port=587,
            use_tls=True,
            tls_mode=None,
            username="ops@company.com",
            secret_enc=None,
        )
        session.add(legacy)
        await session.flush()
        built = await services._transport_for_connection(
            legacy, from_address="a@company.com", resolver=_Resolver(PUBLIC)
        )

    assert isinstance(built, transports.SmtpTransport)
    assert built._tls_mode == "starttls"


@pytest.mark.integration
async def test_a_stored_plaintext_connection_with_a_credential_is_refused_at_use(
    session, org_id
) -> None:
    """Cannot be created any more — but a row is a row, and `send_test` and the campaign driver
    both build a transport from whatever is stored."""
    from platform_core.pii import seal

    sealed, _ = seal("pw", kind="generic", settings=services.settings())
    async with session.begin():
        row = models.EmailConnection(
            organization_id=org_id,
            kind="smtp",
            host=NAME,
            port=25,
            use_tls=False,
            tls_mode="none",
            username="ops@company.com",
            secret_enc=sealed,
        )
        session.add(row)
        await session.flush()
        with pytest.raises(BusinessRuleProblem, match="without encryption"):
            await services._transport_for_connection(
                row, from_address="a@company.com", resolver=_Resolver(PUBLIC)
            )
