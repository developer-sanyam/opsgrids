"""A customer's messages go out on the customer's own connection — never the platform's.

`transports.build(cfg)` gives the process one SMTP relay and one WhatsApp number, and until
`app/tenant_transport.py` every rule-driven message rode them: one tenant's WhatsApp and email were
sent as OpsGrids, from another party's number. These tests drive the real `dispatcher.dispatch`
against real PostgreSQL as the **app** role (row-level security live, the tenant GUC bound per
message exactly as the worker binds it), with the platform transports built by the real
`transports.build` from *distinct, recognisable* platform credentials — so "it did not use the
platform's" is an assertion about what actually went over the wire, not about a recorder that
nothing could have called.

Nothing opens a socket. WhatsApp goes through an `httpx.MockTransport`; SMTP through a fake
`smtplib.SMTP` that keeps what it was dialled with and logged in as; the egress guard's DNS goes
through an injected resolver.
"""

from __future__ import annotations

import contextlib
import smtplib
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio
import structlog
from pydantic import SecretStr
from sqlalchemy import select

from app import dispatcher, models, selectors, services, tenant_transport, transports
from app.config import Settings, settings
from app.domain import STATE_FAILED, STATE_PENDING, STATE_SENT
from app.main import app
from platform_core import pii
from platform_core.auth import Principal, get_principal
from platform_core.db import dispose_engine, init_engine, session_scope
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

# Recognisable on the wire and in a log dump. None of them is a real credential.
TOKEN_A = "tenant-a-whatsapp-token-3f9c"
TOKEN_B = "tenant-b-whatsapp-token-81de"
PLATFORM_TOKEN = "platform-whatsapp-token-c0ffee"
PASSWORD_A = "tenant-a-smtp-password-aa11"  # pragma: allowlist secret
PASSWORD_B = "tenant-b-smtp-password-bb22"  # pragma: allowlist secret
PLATFORM_PASSWORD = "platform-smtp-password-cc33"  # pragma: allowlist secret

PHONE_A = "111111111111"
PHONE_B = "222222222222"
PLATFORM_PHONE = "999999999999"
PLATFORM_HOST = "smtp.platform.example"
PLATFORM_USER = "platform-relay-user"

PUBLIC_IP = "93.184.216.34"

M_READ = "notification.delivery.message.read"


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


# --------------------------------------------------------------------- the wire


@dataclass
class Wire:
    """What actually left the process."""

    whatsapp: list[httpx.Request] = field(default_factory=list)
    smtp: list[dict[str, Any]] = field(default_factory=list)
    whatsapp_status: int = 200
    whatsapp_text: str = ""
    smtp_login_error: Exception | None = None
    #: How many transactions the dispatcher holds open right now (only tracked by
    #: `_recording_scope`), and every network touch that happened while any was.
    open_scopes: int = 0
    touched_while_open: list[str] = field(default_factory=list)

    def touch(self, what: str) -> None:
        if self.open_scopes:
            self.touched_while_open.append(what)

    def whatsapp_calls(self) -> list[tuple[str, str]]:
        """`(phone_number_id, bearer token)` per Graph API call, in order."""
        return [
            (r.url.path.split("/")[-2], r.headers["Authorization"].removeprefix("Bearer "))
            for r in self.whatsapp
        ]

    def smtp_logins(self) -> list[tuple[str, str] | None]:
        return [s["login"] for s in self.smtp]


class _FakeSmtp:
    """Stands in for `smtplib.SMTP`: keeps how it was dialled, logged in, and what it sent."""

    wire: Wire

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.session: dict[str, Any] = {"host": host, "port": port, "login": None, "mail": []}
        self.wire.smtp.append(self.session)
        self.wire.touch("smtp")

    def starttls(self, context: Any = None) -> None:
        self.session["tls"] = True

    def login(self, user: str, password: str) -> None:
        if self.wire.smtp_login_error is not None:
            raise self.wire.smtp_login_error
        self.session["login"] = (user, password)

    def send_message(self, mail: Any) -> None:
        self.session["mail"].append({"from": mail["From"], "to": mail["To"]})

    def quit(self) -> None:
        return None


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    w = Wire()

    def handler(request: httpx.Request) -> httpx.Response:
        w.whatsapp.append(request)
        w.touch("whatsapp")
        if w.whatsapp_status != 200:
            return httpx.Response(w.whatsapp_status, text=w.whatsapp_text)
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{len(w.whatsapp)}"}]})

    mock = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=mock, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    _FakeSmtp.wire = w
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    return w


class _Resolver:
    """The egress guard's DNS. Every name answers with `answers`, or with the public address."""

    def __init__(
        self, answers: dict[str, list[str]] | None = None, wire: Wire | None = None
    ) -> None:
        self._answers = answers or {}
        self._wire = wire

    async def resolve(self, host: str, rtype: str) -> list[str]:
        if self._wire is not None:
            self._wire.touch("dns")
        if rtype != "A":
            return []
        return self._answers.get(host, [PUBLIC_IP])


# --------------------------------------------------------------------- the process


def _cfg(**overrides: Any) -> Settings:
    """The worker's settings: a real SMTP relay and a real WhatsApp number **for the platform**,
    each with credentials no tenant in these tests shares."""
    base: dict[str, Any] = {
        "email_transport": "smtp",
        "smtp_host": PLATFORM_HOST,
        "smtp_username": PLATFORM_USER,
        "smtp_password": SecretStr(PLATFORM_PASSWORD),
        "smtp_use_tls": True,
        "email_from": "no-reply@platform.example",
        "whatsapp_transport": "whatsapp_cloud",
        "whatsapp_phone_number_id": PLATFORM_PHONE,
        "whatsapp_access_token": SecretStr(PLATFORM_TOKEN),
    }
    base.update(overrides)
    return settings().model_copy(update=base)


@pytest_asyncio.fixture
async def worker_engine(session) -> AsyncIterator[None]:
    """The engine the dispatcher's `session_scope` uses. Depends on `session` so the tables are
    truncated before anything is seeded."""
    init_engine(settings())
    yield
    await dispose_engine()


@pytest.fixture
def cfg() -> Settings:
    return _cfg()


@pytest.fixture
def platform(cfg: Settings) -> dict[str, transports.Transport]:
    """The process-wide transports, built exactly as `bin/worker` builds them."""
    return transports.build(cfg)


@pytest.fixture
def org_a(org_id: uuid.UUID) -> uuid.UUID:
    return org_id


@pytest.fixture
def org_b() -> uuid.UUID:
    return uuid.uuid4()


def _scope(organization_id: uuid.UUID) -> Any:
    return session_scope(organization_id=organization_id)


def _route(cfg: Settings, resolver: Any = None) -> Any:
    """`tenant_transport.resolve` with the egress guard's resolver injected."""

    async def route(organization_id: uuid.UUID, channel: str) -> Any:
        return await tenant_transport.resolve(
            organization_id, channel, scope=_scope, cfg=cfg, resolver=resolver
        )

    return route


# --------------------------------------------------------------------- seeding


async def _connect_whatsapp(
    org: uuid.UUID,
    *,
    phone: str,
    token: str | None,
    status: str = "connected",
    sealed: bytes | None = None,
) -> None:
    enc = (
        sealed
        if sealed is not None
        else pii.seal(token or "", kind="generic", settings=settings())[0]
    )
    async with _scope(org) as s:
        s.add(
            models.ProviderAccount(
                organization_id=org,
                channel="whatsapp",
                waba_id="123456789",
                phone_number_id=phone,
                display_number="+91 99999 00000",
                access_token_enc=enc if token is not None or sealed is not None else None,
                status=status,
            )
        )


async def _connect_email(
    org: uuid.UUID,
    *,
    kind: str,
    username: str,
    password: str,
    host: str = "",
    port: int = 587,
    status: str = "connected",
    sealed: bytes | None = None,
) -> None:
    enc = (
        sealed if sealed is not None else pii.seal(password, kind="generic", settings=settings())[0]
    )
    async with _scope(org) as s:
        s.add(
            models.EmailConnection(
                organization_id=org,
                kind=kind,
                host=host,
                port=port,
                use_tls=True,
                username=username,
                secret_enc=enc,
                daily_cap=2000,
                status=status,
            )
        )


async def _connect_both(
    org: uuid.UUID, *, phone: str, token: str, mailbox: str, password: str
) -> None:
    await _connect_whatsapp(org, phone=phone, token=token)
    await _connect_email(
        org, kind="google", host="smtp.gmail.com", username=mailbox, password=password
    )


async def _verified_sender(org: uuid.UUID, address: str) -> None:
    async with _scope(org) as s:
        email_domain = models.EmailDomain(
            organization_id=org, domain=address.split("@")[1], status="verified"
        )
        s.add(email_domain)
        await s.flush()
        s.add(
            models.Sender(
                organization_id=org,
                domain_id=email_domain.id,
                name="Support",
                address=address,
                verified_at=datetime.now(UTC),
            )
        )


async def _stage(org: uuid.UUID, channel: str = "whatsapp", to: str | None = None) -> uuid.UUID:
    """One pending message, staged the way the rules engine stages it."""
    default_to = {
        "whatsapp": "+919999900001",
        "email": "customer@example.com",
        "in_app": str(uuid.uuid4()),
    }[channel]
    async with _scope(org) as s:
        message = await services.stage_message(
            s,
            organization_id=org,
            channel=channel,
            category="operational",
            to_address=to or default_to,
            subject="Hello" if channel == "email" else None,
            body="Your order has shipped.",
            created_by=services.SYSTEM_ACTOR,
            actor={"type": "system", "id": services.SYSTEM_ACTOR},
        )
        assert message is not None
        return message.id


async def _dispatch(
    org: uuid.UUID,
    message_id: uuid.UUID,
    *,
    platform: dict[str, transports.Transport],
    cfg: Settings,
    route: Any = None,
) -> None:
    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_type": services.EVT_MESSAGE_QUEUED,
        "organization_id": str(org),
        "payload": {"message_id": str(message_id)},
    }
    await dispatcher.dispatch(envelope, transports=platform, cfg=cfg, tenant_route=route)


async def _outcome(org: uuid.UUID, message_id: uuid.UUID) -> tuple[models.Message, list[Any]]:
    async with _scope(org) as s:
        message = await selectors.get_message(s, org, message_id)
        assert message is not None
        return message, await selectors.get_deliveries(s, org, message_id)


async def _send(
    org: uuid.UUID,
    channel: str,
    *,
    platform: dict[str, transports.Transport],
    cfg: Settings,
    route: Any = None,
) -> tuple[models.Message, list[Any]]:
    message_id = await _stage(org, channel)
    await _dispatch(org, message_id, platform=platform, cfg=cfg, route=route)
    return await _outcome(org, message_id)


# --------------------------------------------------------------------- who owns a message


def test_only_the_nil_tenant_is_platform_owned() -> None:
    assert tenant_transport.is_platform_owned(uuid.UUID(int=0)) is True
    assert tenant_transport.is_platform_owned(uuid.uuid4()) is False
    assert uuid.UUID(int=0) == tenant_transport.PLATFORM_ORG


def test_a_customers_provider_channels_need_their_own_connection() -> None:
    customer = uuid.uuid4()
    assert tenant_transport.needs_own_connection(customer, "email") is True
    assert tenant_transport.needs_own_connection(customer, "whatsapp") is True
    # An unrecognised channel is not exempt: it reaches `resolve`, which refuses it.
    assert tenant_transport.needs_own_connection(customer, "sms") is True
    assert tenant_transport.needs_own_connection(customer, "in_app") is False


def test_the_platform_tenant_never_needs_a_customer_connection() -> None:
    platform_org = uuid.UUID(int=0)
    for channel in ("email", "whatsapp", "in_app"):
        assert tenant_transport.needs_own_connection(platform_org, channel) is False


# --------------------------------------------------------------------- WhatsApp


async def test_each_organization_sends_whatsapp_from_its_own_number_and_token(
    org_a, org_b, worker_engine, wire, cfg, platform
) -> None:
    await _connect_whatsapp(org_a, phone=PHONE_A, token=TOKEN_A)
    await _connect_whatsapp(org_b, phone=PHONE_B, token=TOKEN_B)

    message_a, _ = await _send(org_a, "whatsapp", platform=platform, cfg=cfg)
    message_b, _ = await _send(org_b, "whatsapp", platform=platform, cfg=cfg)

    assert wire.whatsapp_calls() == [(PHONE_A, TOKEN_A), (PHONE_B, TOKEN_B)]
    assert message_a.state == STATE_SENT
    assert message_b.state == STATE_SENT
    used = set(wire.whatsapp_calls())
    assert (PLATFORM_PHONE, PLATFORM_TOKEN) not in used
    assert all(phone != PLATFORM_PHONE for phone, _ in used)


async def test_a_template_send_still_carries_its_payload_on_the_tenants_number(
    org_a, worker_engine, wire, cfg, platform
) -> None:
    """The provider-template path is unchanged; only whose number it leaves from moved."""
    await _connect_whatsapp(org_a, phone=PHONE_A, token=TOKEN_A)
    async with _scope(org_a) as s:
        # The dispatcher re-checks the template's status right before sending, so it has to exist
        # and be approved — as it always is by the time a rule stages a message against it.
        s.add(
            models.ProviderTemplate(
                organization_id=org_a,
                code="order_shipped",
                channel="whatsapp",
                provider_name="order_shipped",
                language="en",
                category="utility",
                status="approved",
            )
        )
        message = await services.stage_message(
            s,
            organization_id=org_a,
            channel="whatsapp",
            category="operational",
            to_address="+919999900001",
            subject=None,
            body="Order shipped",
            created_by=services.SYSTEM_ACTOR,
            provider_template_code="order_shipped",
            provider_template_payload={"name": "order_shipped", "language": {"code": "en"}},
        )
        assert message is not None
        message_id = message.id

    await _dispatch(org_a, message_id, platform=platform, cfg=cfg)

    (request,) = wire.whatsapp
    assert request.url.path.endswith(f"/{PHONE_A}/messages")
    assert b'"type":"template"' in request.content.replace(b" ", b"")


async def test_a_connected_account_sends_even_when_the_platforms_own_number_is_off(
    org_a, worker_engine, wire
) -> None:
    """`WHATSAPP_TRANSPORT` picks the *platform's* sender (production runs it as `log` until the
    platform has a number of its own). A tenant's connected account is what enables its own
    sending — connecting one already required a live Meta ownership check with its token."""
    off = _cfg(whatsapp_transport="log", whatsapp_phone_number_id=None, whatsapp_access_token=None)
    platform = transports.build(off)
    await _connect_whatsapp(org_a, phone=PHONE_A, token=TOKEN_A)

    tenant, _ = await _send(org_a, "whatsapp", platform=platform, cfg=off)
    platform_owned, deliveries = await _send(
        uuid.UUID(int=0), "whatsapp", platform=platform, cfg=off
    )

    assert tenant.state == STATE_SENT
    assert wire.whatsapp_calls() == [(PHONE_A, TOKEN_A)]
    # The platform's own message still takes the platform's (log) transport.
    assert platform_owned.state == STATE_SENT
    assert [d.provider for d in deliveries] == ["log"]


# --------------------------------------------------------------------- email


async def test_each_organization_sends_email_through_its_own_connection(
    org_a, org_b, worker_engine, wire, cfg, platform
) -> None:
    await _connect_email(
        org_a,
        kind="google",
        host="smtp.gmail.com",
        username="ops@a.example.com",
        password=PASSWORD_A,
    )
    await _connect_email(
        org_b,
        kind="smtp",
        host="mail.b.example",
        username="mailer@b.example.com",
        password=PASSWORD_B,
    )
    route = _route(cfg, _Resolver({"mail.b.example": [PUBLIC_IP]}))

    message_a, deliveries_a = await _send(org_a, "email", platform=platform, cfg=cfg, route=route)
    message_b, _ = await _send(org_b, "email", platform=platform, cfg=cfg, route=route)

    assert [s["host"] for s in wire.smtp] == ["smtp.gmail.com", PUBLIC_IP]
    assert wire.smtp_logins() == [
        ("ops@a.example.com", PASSWORD_A),
        ("mailer@b.example.com", PASSWORD_B),
    ]
    assert [s["mail"][0]["from"] for s in wire.smtp] == [
        "ops@a.example.com",
        "mailer@b.example.com",
    ]
    assert message_a.state == STATE_SENT
    assert message_b.state == STATE_SENT
    assert [d.provider for d in deliveries_a] == ["smtp"]
    # Not one connection was opened to the platform's relay, or logged in with its credentials.
    assert PLATFORM_HOST not in [s["host"] for s in wire.smtp]
    assert (PLATFORM_USER, PLATFORM_PASSWORD) not in wire.smtp_logins()


async def test_a_relay_whose_login_is_not_an_address_sends_as_a_verified_sender(
    org_a, worker_engine, wire, cfg, platform
) -> None:
    await _connect_email(
        org_a, kind="smtp", host="relay.a.example", username="apikey", password=PASSWORD_A
    )
    await _verified_sender(org_a, "support@a.example.com")

    message, _ = await _send(
        org_a, "email", platform=platform, cfg=cfg, route=_route(cfg, _Resolver())
    )

    assert message.state == STATE_SENT
    assert wire.smtp[0]["mail"][0]["from"] == "support@a.example.com"
    assert wire.smtp_logins() == [("apikey", PASSWORD_A)]


async def test_a_relay_with_nothing_to_send_as_fails_closed(
    org_a, worker_engine, wire, cfg, platform
) -> None:
    await _connect_email(
        org_a, kind="smtp", host="relay.a.example", username="apikey", password=PASSWORD_A
    )

    message, _ = await _send(
        org_a, "email", platform=platform, cfg=cfg, route=_route(cfg, _Resolver())
    )

    assert message.state == STATE_FAILED
    assert message.last_error == "sender_not_configured"
    assert wire.smtp == []


async def test_a_rule_driven_send_is_held_to_the_egress_guard(
    org_a, worker_engine, wire, cfg, platform
) -> None:
    """A customer named this host. It is re-resolved on every send, and a name that has been
    repointed at the compose network is never dialled — and never falls back to the platform."""
    await _connect_email(
        org_a,
        kind="smtp",
        host="mail.a.example",
        username="mailer@a.example.com",
        password=PASSWORD_A,
    )
    route = _route(cfg, _Resolver({"mail.a.example": ["10.0.0.5"]}))

    message, deliveries = await _send(org_a, "email", platform=platform, cfg=cfg, route=route)

    assert wire.smtp == []
    assert message.state == STATE_FAILED
    assert message.last_error == "smtp_host_not_allowed"
    assert [(d.provider, d.outcome, d.detail) for d in deliveries] == [
        ("none", "failed", "smtp_host_not_allowed")
    ]


async def test_a_single_label_host_is_refused_without_a_lookup(
    org_a, worker_engine, wire, cfg, platform
) -> None:
    await _connect_email(
        org_a, kind="smtp", host="postgres", username="mailer@a.example.com", password=PASSWORD_A
    )
    message, _ = await _send(
        org_a, "email", platform=platform, cfg=cfg, route=_route(cfg, _Resolver())
    )
    assert message.last_error == "smtp_host_not_allowed"
    assert wire.smtp == []


async def test_the_platforms_send_nothing_switch_holds_for_tenant_email_too(
    org_a, org_b, worker_engine, wire, platform
) -> None:
    """`EMAIL_TRANSPORT=log` is honoured exactly as `campaign_transport` honours it — but a
    connection is still required, so the same message fails the same way in every environment."""
    log_cfg = _cfg(email_transport="log")
    await _connect_email(
        org_a,
        kind="google",
        host="smtp.gmail.com",
        username="ops@a.example.com",
        password=PASSWORD_A,
    )

    connected, deliveries = await _send(org_a, "email", platform=platform, cfg=log_cfg)
    unconnected, _ = await _send(org_b, "email", platform=platform, cfg=log_cfg)

    assert connected.state == STATE_SENT
    assert [d.provider for d in deliveries] == ["log"]
    assert wire.smtp == []
    assert unconnected.state == STATE_FAILED
    assert unconnected.last_error == "channel_not_connected"


# --------------------------------------------------------------------- no connection, no send


@pytest.mark.parametrize("channel", ["whatsapp", "email"])
async def test_an_organization_without_a_connection_is_never_sent_as_the_platform(
    channel, org_a, org_b, worker_engine, wire, cfg, platform
) -> None:
    # Another organization *is* connected on both channels: a mix-up would find something.
    await _connect_both(
        org_b, phone=PHONE_B, token=TOKEN_B, mailbox="ops@b.example.com", password=PASSWORD_B
    )

    message, deliveries = await _send(org_a, channel, platform=platform, cfg=cfg)

    assert wire.whatsapp == []
    assert wire.smtp == []
    assert message.state == STATE_FAILED
    assert message.last_error == "channel_not_connected"
    assert message.attempts == 1
    assert [(d.attempt_no, d.provider, d.outcome, d.detail) for d in deliveries] == [
        (1, "none", "failed", "channel_not_connected")
    ]
    async with _scope(org_a) as s:
        events = (
            (
                await s.execute(
                    select(models.OutboxEvent).where(
                        models.OutboxEvent.event_type == services.EVT_MESSAGE_FAILED
                    )
                )
            )
            .scalars()
            .all()
        )
    assert [e.payload["last_error"] for e in events] == ["channel_not_connected"]


@pytest.mark.parametrize("channel", ["whatsapp", "email"])
async def test_a_disconnected_account_is_not_used(
    channel, org_a, worker_engine, wire, cfg, platform
) -> None:
    if channel == "whatsapp":
        await _connect_whatsapp(org_a, phone=PHONE_A, token=TOKEN_A, status="disconnected")
    else:
        await _connect_email(
            org_a,
            kind="google",
            host="smtp.gmail.com",
            username="ops@a.example.com",
            password=PASSWORD_A,
            status="disconnected",
        )

    message, _ = await _send(org_a, channel, platform=platform, cfg=cfg)

    assert (wire.whatsapp, wire.smtp) == ([], [])
    assert message.last_error == "channel_not_connected"


async def test_a_platform_kind_connection_is_not_a_way_back_onto_the_shared_relay(
    org_a, worker_engine, wire, cfg, platform
) -> None:
    await _connect_email(org_a, kind="platform", username="", password="x")
    message, _ = await _send(org_a, "email", platform=platform, cfg=cfg)
    assert wire.smtp == []
    assert message.last_error == "channel_not_connected"


async def test_a_whatsapp_account_with_no_token_is_not_connected(
    org_a, worker_engine, wire, cfg, platform
) -> None:
    await _connect_whatsapp(org_a, phone=PHONE_A, token=None)
    message, _ = await _send(org_a, "whatsapp", platform=platform, cfg=cfg)
    assert wire.whatsapp == []
    assert message.last_error == "channel_not_connected"


async def test_a_phone_number_id_that_is_not_digits_never_reaches_a_url(
    org_a, worker_engine, wire, cfg, platform
) -> None:
    """Written any way but the API (which only accepts digits), it must not steer the request."""
    await _connect_whatsapp(org_a, phone="123/../../other", token=TOKEN_A)
    message, _ = await _send(org_a, "whatsapp", platform=platform, cfg=cfg)
    assert wire.whatsapp == []
    assert message.last_error == "channel_not_connected"


@pytest.mark.parametrize("channel", ["whatsapp", "email"])
async def test_a_credential_that_cannot_be_opened_is_a_named_failure_not_a_platform_send(
    channel, org_a, worker_engine, wire, cfg, platform
) -> None:
    garbage = b"\x00" * 64
    if channel == "whatsapp":
        await _connect_whatsapp(org_a, phone=PHONE_A, token=TOKEN_A, sealed=garbage)
    else:
        await _connect_email(
            org_a,
            kind="google",
            host="smtp.gmail.com",
            username="ops@a.example.com",
            password="unused",  # pragma: allowlist secret
            sealed=garbage,
        )

    message, _ = await _send(org_a, channel, platform=platform, cfg=cfg)

    assert (wire.whatsapp, wire.smtp) == ([], [])
    assert message.state == STATE_FAILED
    assert message.last_error == "channel_credentials_unreadable"


async def test_an_unsupported_channel_for_a_customer_fails_closed(
    org_a, worker_engine, wire, cfg, platform
) -> None:
    async with _scope(org_a) as s:
        message = models.Message(
            organization_id=org_a,
            channel="sms",
            category="operational",
            to_address="+919999900001",
            body="Hi",
            state="pending",
            attempts=0,
        )
        s.add(message)
        await s.flush()
        message_id = message.id

    await _dispatch(org_a, message_id, platform=platform, cfg=cfg)

    stored, _ = await _outcome(org_a, message_id)
    assert stored.state == STATE_FAILED
    assert stored.last_error == "channel_not_connected"


async def test_resolution_that_crashes_is_transient_and_still_never_the_platform(
    org_a, worker_engine, wire, cfg, platform
) -> None:
    async def broken(organization_id: uuid.UUID, channel: str) -> Any:
        raise RuntimeError("the database went away")

    message, deliveries = await _send(org_a, "whatsapp", platform=platform, cfg=cfg, route=broken)

    assert wire.whatsapp == []
    assert message.state == STATE_PENDING
    assert message.last_error == "channel_resolution_failed"
    assert [d.outcome for d in deliveries] == ["failed"]


# --------------------------------------------------------------------- what stays the platform's


async def test_platform_owned_messages_still_use_the_platform_transport(
    worker_engine, wire, cfg, platform
) -> None:
    platform_org = uuid.UUID(int=0)

    email, _ = await _send(platform_org, "email", platform=platform, cfg=cfg)
    whatsapp, _ = await _send(platform_org, "whatsapp", platform=platform, cfg=cfg)

    assert email.state == STATE_SENT
    assert whatsapp.state == STATE_SENT
    assert [s["host"] for s in wire.smtp] == [PLATFORM_HOST]
    assert wire.smtp_logins() == [(PLATFORM_USER, PLATFORM_PASSWORD)]
    assert wire.smtp[0]["mail"][0]["from"] == "no-reply@platform.example"
    assert wire.whatsapp_calls() == [(PLATFORM_PHONE, PLATFORM_TOKEN)]


async def test_the_platform_tenants_own_connection_rows_are_not_consulted(
    worker_engine, wire, cfg, platform
) -> None:
    """Platform mail is the environment's transport. A connection row under the nil id — there is
    none in practice — would change nothing."""
    platform_org = uuid.UUID(int=0)
    await _connect_email(
        platform_org, kind="google", host="smtp.gmail.com", username="x@y.example.com", password="x"
    )
    await _send(platform_org, "email", platform=platform, cfg=cfg)
    assert [s["host"] for s in wire.smtp] == [PLATFORM_HOST]


async def test_the_in_app_feed_needs_no_connection(
    org_a, worker_engine, wire, cfg, platform
) -> None:
    message, deliveries = await _send(org_a, "in_app", platform=platform, cfg=cfg)
    assert message.state == STATE_SENT
    assert [d.provider for d in deliveries] == ["in_app"]
    assert (wire.whatsapp, wire.smtp) == ([], [])


# --------------------------------------------------------------------- tenant context


def _recording_scope(wire: Wire, opened: list[uuid.UUID]) -> Any:
    """The dispatcher's session scope, wrapped to say which tenants it was opened for and to tell
    `wire` whether a transaction is open while something touches the network."""

    @contextlib.asynccontextmanager
    async def scope(organization_id: uuid.UUID) -> AsyncIterator[Any]:
        opened.append(organization_id)
        wire.open_scopes += 1
        try:
            async with session_scope(organization_id=organization_id) as s:
                yield s
        finally:
            wire.open_scopes -= 1

    return scope


async def test_the_default_route_reads_through_the_scope_the_dispatcher_was_given(
    org_a, org_b, worker_engine, wire, cfg, platform
) -> None:
    """The worker swaps the dispatcher onto a bulkhead scope when it is folded into the API
    process. The connection read must follow that swap — and be bound to the message's tenant,
    like the claim and the record either side of it — or it would run on the wrong pool."""
    await _connect_both(
        org_a, phone=PHONE_A, token=TOKEN_A, mailbox="ops@a.example.com", password=PASSWORD_A
    )
    await _connect_both(
        org_b, phone=PHONE_B, token=TOKEN_B, mailbox="ops@b.example.com", password=PASSWORD_B
    )
    message_id = await _stage(org_a, "whatsapp")
    opened: list[uuid.UUID] = []
    dispatcher.use_session_scope(_recording_scope(wire, opened))
    try:
        await _dispatch(org_a, message_id, platform=platform, cfg=cfg)
    finally:
        dispatcher.use_session_scope(dispatcher._default_session_scope)

    # Claim, connection read, record — each bound to the one tenant, none to any other.
    assert opened == [org_a, org_a, org_a]
    assert wire.whatsapp_calls() == [(PHONE_A, TOKEN_A)]


async def test_no_transaction_is_open_while_a_tenant_send_touches_the_network(
    org_a, worker_engine, wire, cfg, platform
) -> None:
    """The dispatcher's whole design is that a provider that hangs costs a connection, not a lock.
    Resolving a customer's connection must not quietly break that: the read is its own short
    transaction, and the egress guard's DNS, the SMTP session and the Graph API call all happen
    after it has closed."""
    await _connect_whatsapp(org_a, phone=PHONE_A, token=TOKEN_A)
    await _connect_email(
        org_a,
        kind="smtp",
        host="mail.a.example",
        username="mailer@a.example.com",
        password=PASSWORD_A,
    )
    opened: list[uuid.UUID] = []
    scope = _recording_scope(wire, opened)
    resolver = _Resolver(wire=wire)

    async def route(organization_id: uuid.UUID, channel: str) -> Any:
        return await tenant_transport.resolve(
            organization_id, channel, scope=scope, cfg=cfg, resolver=resolver
        )

    dispatcher.use_session_scope(scope)
    try:
        for channel in ("whatsapp", "email"):
            message_id = await _stage(org_a, channel)
            await _dispatch(org_a, message_id, platform=platform, cfg=cfg, route=route)
    finally:
        dispatcher.use_session_scope(dispatcher._default_session_scope)

    assert len(wire.whatsapp) == 1
    assert len(wire.smtp) == 1
    assert wire.touched_while_open == []


# --------------------------------------------------------------------- another tenant's rows


async def test_another_organizations_connection_is_invisible_under_the_tenant_binding(
    org_a, org_b, worker_engine
) -> None:
    await _connect_both(
        org_b, phone=PHONE_B, token=TOKEN_B, mailbox="ops@b.example.com", password=PASSWORD_B
    )

    async with _scope(org_a) as s:
        assert await selectors.get_whatsapp_connection(s, org_b) is None
        assert await selectors.get_connection(s, org_b) is None
        assert (await s.execute(select(models.ProviderAccount))).scalars().all() == []
        assert (await s.execute(select(models.EmailConnection))).scalars().all() == []


async def test_a_row_belonging_to_another_organization_is_refused_even_if_a_read_returned_it(
    org_a, org_b, worker_engine, wire, cfg, platform, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defence in depth under the selector and the policy: were either ever wrong, the row that
    comes back is checked against the organization being sent for."""
    await _connect_both(
        org_b, phone=PHONE_B, token=TOKEN_B, mailbox="ops@b.example.com", password=PASSWORD_B
    )
    async with _scope(org_b) as s:
        their_account = await selectors.get_whatsapp_connection(s, org_b)
        their_connection = await selectors.get_connection(s, org_b)
    assert their_account is not None and their_connection is not None

    async def wrong_account(session: Any, org_id: uuid.UUID) -> Any:
        return their_account

    async def wrong_connection(session: Any, org_id: uuid.UUID, **kw: Any) -> Any:
        return their_connection

    monkeypatch.setattr(selectors, "get_whatsapp_connection", wrong_account)
    monkeypatch.setattr(selectors, "get_connection", wrong_connection)

    for channel in ("whatsapp", "email"):
        message, _ = await _send(org_a, channel, platform=platform, cfg=cfg)
        assert message.state == STATE_FAILED
        assert message.last_error == "channel_not_connected"

    assert (wire.whatsapp, wire.smtp) == ([], [])


# --------------------------------------------------------------------- secrets


async def test_a_provider_that_echoes_the_token_never_lands_it_on_the_message(
    org_a, worker_engine, wire, cfg, platform
) -> None:
    await _connect_whatsapp(org_a, phone=PHONE_A, token=TOKEN_A)
    wire.whatsapp_status = 400
    wire.whatsapp_text = f"Invalid OAuth access token {TOKEN_A}"

    with structlog.testing.capture_logs() as logs:
        message, deliveries = await _send(org_a, "whatsapp", platform=platform, cfg=cfg)

    assert message.state == STATE_FAILED
    assert "<redacted>" in (message.last_error or "")
    assert TOKEN_A not in (message.last_error or "")
    assert all(TOKEN_A not in (d.detail or "") for d in deliveries)
    assert any(entry["event"] == "dispatch.refused" for entry in logs)
    assert TOKEN_A not in repr(logs)


async def test_a_relay_that_echoes_the_password_never_lands_it_on_the_message(
    org_a, worker_engine, wire, cfg, platform
) -> None:
    await _connect_email(
        org_a,
        kind="google",
        host="smtp.gmail.com",
        username="ops@a.example.com",
        password=PASSWORD_A,
    )
    wire.smtp_login_error = smtplib.SMTPAuthenticationError(
        535, f"5.7.8 rejected {PASSWORD_A}".encode()
    )

    with structlog.testing.capture_logs() as logs:
        message, deliveries = await _send(org_a, "email", platform=platform, cfg=cfg)

    assert message.last_error is not None
    assert PASSWORD_A not in message.last_error
    assert all(PASSWORD_A not in (d.detail or "") for d in deliveries)
    assert any(entry["event"] == "dispatch.unavailable" for entry in logs)
    assert PASSWORD_A not in repr(logs)


async def test_no_credential_appears_in_any_log_line_of_a_full_run(
    org_a, org_b, worker_engine, wire, cfg, platform
) -> None:
    await _connect_both(
        org_a, phone=PHONE_A, token=TOKEN_A, mailbox="ops@a.example.com", password=PASSWORD_A
    )
    # org_b is not connected: that refusal is logged too.
    with structlog.testing.capture_logs() as logs:
        await _send(org_a, "whatsapp", platform=platform, cfg=cfg)
        await _send(org_a, "email", platform=platform, cfg=cfg)
        await _send(org_b, "whatsapp", platform=platform, cfg=cfg)
        await _send(org_b, "email", platform=platform, cfg=cfg)

    events = {entry["event"] for entry in logs}
    assert "dispatch.sent" in events
    assert "dispatch.channel_unavailable" in events
    dump = repr(logs)
    for secret in (TOKEN_A, PASSWORD_A, PLATFORM_TOKEN, PLATFORM_PASSWORD):
        assert secret not in dump
    refusal = next(e for e in logs if e["event"] == "dispatch.channel_unavailable")
    assert refusal["reason"] == "channel_not_connected"
    assert refusal["organization_id"] == str(org_b)


async def test_the_reason_is_visible_through_the_message_list_api(
    client, session, org_a, worker_engine, wire, cfg, platform
) -> None:
    message_id = await _stage(org_a, "whatsapp")
    await _dispatch(org_a, message_id, platform=platform, cfg=cfg)

    with as_principal(principal_for(org_a, [f"{M_READ}:ORG"])):
        response = await client.get("/api/v1/notifications/messages", params={"state": "failed"})

    assert response.status_code == 200
    (item,) = response.json()["items"]
    assert item["id"] == str(message_id)
    assert item["state"] == "failed"
    assert item["last_error"] == "channel_not_connected"
    assert [(d["provider"], d["outcome"], d["detail"]) for d in item["deliveries"]] == [
        ("none", "failed", "channel_not_connected")
    ]


async def test_another_organizations_failed_message_is_not_in_my_list(
    client, session, org_a, org_b, worker_engine, wire, cfg, platform
) -> None:
    theirs = await _stage(org_b, "whatsapp")
    await _dispatch(org_b, theirs, platform=platform, cfg=cfg)

    with as_principal(principal_for(org_a, [f"{M_READ}:ORG"])):
        response = await client.get("/api/v1/notifications/messages")

    assert response.status_code == 200
    assert response.json()["items"] == []
