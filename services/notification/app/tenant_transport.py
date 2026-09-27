"""Whose connection a message goes out on.

A message is either **platform-owned** or **tenant-owned**, and the two must never be mixed up:
the platform's one SMTP relay and one WhatsApp number are the platform's own voice, and a
customer's ticket acknowledgement or invoice reminder sent through them is sent *as OpsGrids*
rather than as that customer — from another party's number, over another party's relay.

**How the two are told apart.** Platform-owned means `Message.organization_id` is the nil UUID
(`PLATFORM_ORG`) — the platform tenant ADR-0013 §3 seeds. It is the only tenant that owns platform
mail: auth publishes `core.user.password_reset_requested` (the set-password and reset email) under
the nil id, and the rule and template that turn it into mail live under that same tenant
(`b8c1d2e3f4a5_platform_password_reset_rule`). Every other organization id is a customer.

That is a persisted marker rather than an inference made at send time: `organization_id` is
written when the message is staged, from the event envelope, and the row-level policy binds every
write to it — a customer cannot stage a row under the nil id, and nothing about a rule, a template
or a category can promote a customer's message to platform-owned. It also fails closed: the shared
transport is reached only by a message that is *provably* the platform's, so a new customer-facing
flow needs no change here to be safe, and a new platform flow must publish under the platform
tenant to get the platform's voice (which is also how auth already does it).

**What a tenant-owned message does.** `email` and `whatsapp` are sent through that organization's
own connection — `email_connections` and `provider_accounts` — built for this one send. With no
usable connection there is **no fallback**: `resolve` returns a `ChannelUnavailable` naming why,
and the dispatcher fails the message on the spot. `in_app` has no provider and is never routed
here (see `needs_own_connection`).

**Tenant context.** The rows are read inside `scope(organization_id)` — the dispatcher's own
per-message scope: the NOBYPASSRLS app role with the tenant GUC bound for that transaction only
(ADR-0002; both tables are FORCE RLS). Every selector below still filters on `organization_id`
explicitly, and the row that comes back is checked against it once more — RLS is the net, not
permission to skip the filter. The transaction ends before anything touches a network: opening a
sealed credential is local, but the egress guard for a tenant-named SMTP host resolves DNS.

**No cache, deliberately.** A transport is built per send and holds no open socket between sends
(`WhatsAppCloudTransport` opens and closes its own HTTP client inside `send`; `SmtpTransport`
opens and quits its own session). A cache would let a disconnected, replaced or rotated credential
keep sending for as long as the entry lived — the wrong trade for a secret whose whole lifecycle
is "the customer can revoke it now" — to save one indexed read per send.

**Secrets.** The plaintext token or password exists only inside the transport built here, and the
transport is wrapped so that an error string a provider hands back is scrubbed of it before it can
reach `messages.last_error` (which a wider audience can read than can connect a provider) or a
log line.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app import dns_check, domain, selectors, services
from app.config import Settings
from app.models import EmailConnection, ProviderAccount
from app.transports import (
    LogTransport,
    Outgoing,
    Receipt,
    Transport,
    TransportError,
    TransportUnavailable,
    WhatsAppCloudTransport,
)
from platform_core import pii
from platform_core.errors import BusinessRuleProblem
from platform_core.logging import get_logger

log = get_logger("notification.tenant_transport")

#: The platform tenant. The same nil UUID auth publishes credential events under and notification
#: seeds the reset-email rule for (ADR-0013 §3). Defined here rather than imported: services do not
#: import each other, and this is one line.
PLATFORM_ORG: Final = uuid.UUID(int=0)

#: Same shape as `app.dispatcher.WorkScope` — defined again, not imported, so this module does not
#: reach up into the process that calls it.
WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]

#: The machine-readable reasons a tenant-owned message can be refused a transport. They are what
#: lands in `messages.last_error` (and the failed `deliveries` row), so the message list API shows
#: them as-is and a UI can map each to its own wording. Exactly these strings; never a sentence.
NOT_CONNECTED: Final = "channel_not_connected"
CREDENTIALS_UNREADABLE: Final = "channel_credentials_unreadable"
SMTP_HOST_NOT_ALLOWED: Final = "smtp_host_not_allowed"
SENDER_NOT_CONFIGURED: Final = "sender_not_configured"

Reason = Literal[
    "channel_not_connected",
    "channel_credentials_unreadable",
    "smtp_host_not_allowed",
    "sender_not_configured",
]

#: What the failed attempt's `deliveries.provider` says when no transport ran at all.
NO_PROVIDER: Final = "none"

#: A WhatsApp phone number id is digits — `schemas.WhatsAppConnectionIn` enforces this when it is
#: saved. Checked again here because the value is interpolated into the Graph API URL path, and a
#: row written any other way must not be able to steer the request somewhere else.
_PHONE_NUMBER_ID: Final = re.compile(r"^\d{1,30}$")

_REDACTED: Final = "<redacted>"


@dataclass(frozen=True, slots=True)
class ChannelUnavailable:
    """This organization cannot send on this channel right now, and why. Never retryable: the
    fix is a connection, not time."""

    reason: Reason


def is_platform_owned(organization_id: uuid.UUID) -> bool:
    """Is this the platform's own message? See the module docstring for what makes it so."""
    return organization_id == PLATFORM_ORG


def needs_own_connection(organization_id: uuid.UUID, channel: str) -> bool:
    """Must a message on `channel` for `organization_id` go out on that organization's own
    connection?

    True for every customer's message on any channel that leaves the building. The one channel
    that does not is `in_app`: it is the platform's own feed (`app/inbox.py`), a row read back by
    the recipient, with no provider and so no one whose identity it could be sent as. An
    unrecognised channel is *not* exempt — it falls to `resolve`, which refuses it — so a channel
    added to `domain.CHANNELS` tomorrow is safe until someone deliberately wires it.
    """
    if is_platform_owned(organization_id):
        return False
    return channel != domain.IN_APP_CHANNEL


class _ScrubbedTransport:
    """Wraps a tenant's transport so a failure's text never carries its credential.

    Providers' error bodies are written by someone else and are recorded against the message,
    where far more people can read them than can connect a provider. A well-behaved provider does
    not echo a token or a password; this does not rely on that. Anything raised is re-raised as
    the same class of failure — refused stays refused, unavailable stays unavailable, and an
    exception nobody classified is unavailable, which is what the dispatcher would have decided
    anyway — with every occurrence of the secrets replaced.
    """

    def __init__(self, inner: Transport, secrets: tuple[str, ...]) -> None:
        self._inner = inner
        self._secrets = tuple(s for s in secrets if s)

    @property
    def name(self) -> str:
        return self._inner.name

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, _REDACTED)
        return text

    async def send(self, message: Outgoing) -> Receipt:
        try:
            return await self._inner.send(message)
        except TransportError as exc:
            # `with_message` keeps the class *and* the provider's code and reason: the dispatcher
            # decides on those (and flips the connection's health on a rejected credential).
            raise exc.with_message(self._scrub(str(exc))) from None
        except Exception as exc:
            raise TransportUnavailable(self._scrub(str(exc))) from None


async def resolve(
    organization_id: uuid.UUID,
    channel: str,
    *,
    scope: WorkScope,
    cfg: Settings,
    resolver: dns_check.Resolver | None = None,
) -> Transport | ChannelUnavailable:
    """The transport for one tenant-owned message, or the reason there is none.

    Never returns the platform's transport and never falls back to it. `resolver` exists only
    for the egress guard's own tests, the same seam `services.send_test` offers.
    """
    if channel == "whatsapp":
        return await _resolve_whatsapp(organization_id, scope=scope, cfg=cfg)
    if channel == "email":
        return await _resolve_email(organization_id, scope=scope, cfg=cfg, resolver=resolver)
    log.error(
        "tenant_transport.unsupported_channel",
        organization_id=str(organization_id),
        channel=channel,
    )
    return ChannelUnavailable(NOT_CONNECTED)


def _refuse(
    organization_id: uuid.UUID, channel: str, reason: Reason, **detail: str
) -> ChannelUnavailable:
    """One structured line per refusal. `detail` is for the operator reading the log — a state, a
    connection kind — never a credential, an address, or a provider's own text."""
    log.warning(
        "tenant_transport.unavailable",
        organization_id=str(organization_id),
        channel=channel,
        reason=reason,
        **detail,
    )
    return ChannelUnavailable(reason)


async def _resolve_whatsapp(
    organization_id: uuid.UUID, *, scope: WorkScope, cfg: Settings
) -> Transport | ChannelUnavailable:
    async with scope(organization_id) as session:
        account = await selectors.get_whatsapp_connection(session, organization_id)
    # The transaction is closed. From here the row is a detached snapshot.

    if account is None:
        return _refuse(organization_id, "whatsapp", NOT_CONNECTED, why="no_account")
    if not _belongs_to(account, organization_id):
        return _refuse(organization_id, "whatsapp", NOT_CONNECTED, why="row_org_mismatch")
    if account.status != "connected":
        return _refuse(organization_id, "whatsapp", NOT_CONNECTED, why="not_connected")
    if not account.access_token_enc:
        return _refuse(organization_id, "whatsapp", NOT_CONNECTED, why="no_token")
    if not account.phone_number_id or not _PHONE_NUMBER_ID.match(account.phone_number_id):
        return _refuse(organization_id, "whatsapp", NOT_CONNECTED, why="bad_phone_number_id")

    try:
        token = pii.open_sealed(account.access_token_enc, settings=cfg)
    except pii.PiiError:
        return _refuse(organization_id, "whatsapp", CREDENTIALS_UNREADABLE, why="unseal_failed")

    # The platform's `WHATSAPP_TRANSPORT` picks the *platform's own* sender and is not consulted:
    # a connected account is the switch here, and connecting one already required a live Meta
    # ownership check with this token (`whatsapp_provider.verify_ownership`).
    wire = WhatsAppCloudTransport(
        api_base=cfg.whatsapp_api_base,
        phone_number_id=account.phone_number_id,
        access_token=token,
        timeout=cfg.whatsapp_timeout_seconds,
    )
    return _ScrubbedTransport(wire, (token,))


async def _resolve_email(
    organization_id: uuid.UUID,
    *,
    scope: WorkScope,
    cfg: Settings,
    resolver: dns_check.Resolver | None,
) -> Transport | ChannelUnavailable:
    async with scope(organization_id) as session:
        connection = await selectors.get_connection(session, organization_id)
        # Only asked when the connection's own username cannot be the From address.
        fallback = None
        if connection is not None and _mailbox(connection.username) is None:
            fallback = await selectors.first_usable_sender(session, organization_id)
    # The transaction is closed. Everything below may touch DNS.

    if connection is None:
        return _refuse(organization_id, "email", NOT_CONNECTED, why="no_connection")
    if not _belongs_to(connection, organization_id):
        return _refuse(organization_id, "email", NOT_CONNECTED, why="row_org_mismatch")
    if connection.status != "connected":
        return _refuse(organization_id, "email", NOT_CONNECTED, why="not_connected")
    if connection.kind == "platform":
        # Modelled and unreachable (`services.create_connection` refuses it): platform-managed
        # sending would *be* the shared relay this module exists to keep tenants off.
        return _refuse(organization_id, "email", NOT_CONNECTED, why="platform_kind")

    from_address = _mailbox(connection.username) or (fallback.address if fallback else None)
    if from_address is None:
        return _refuse(organization_id, "email", SENDER_NOT_CONFIGURED, why="no_address_to_send_as")

    if cfg.email_transport == "log":
        # The platform's "send nothing real" switch, honoured exactly as `services.
        # campaign_transport` honours it: an environment set to `log` must not mail real
        # recipients because a customer's credential is in its database. The connection is still
        # required above, so the same message fails the same way in every environment.
        return LogTransport("email")

    try:
        # The one place the egress guard for a tenant-named SMTP host runs (ADR-0016): campaigns
        # and test sends build their transport here too, so a rule-driven send is held to exactly
        # the same dial guard — re-resolved now, and the address it returns is what gets dialled.
        wire = await services._transport_for_connection(
            connection, from_address=from_address, resolver=resolver
        )
    except BusinessRuleProblem:
        return _refuse(
            organization_id,
            "email",
            SMTP_HOST_NOT_ALLOWED,
            why="egress_guard",
            kind=connection.kind,
        )
    except pii.PiiError:
        return _refuse(organization_id, "email", CREDENTIALS_UNREADABLE, why="unseal_failed")

    return _ScrubbedTransport(wire, (_secret_of(connection, cfg),))


def _belongs_to(row: EmailConnection | ProviderAccount, organization_id: uuid.UUID) -> bool:
    """The last check on a row a selector already scoped: an organization's send is never built
    from another organization's connection, whatever a query or a policy did or did not do."""
    if row.organization_id != organization_id:
        log.error(
            "tenant_transport.row_org_mismatch",
            organization_id=str(organization_id),
            row_organization_id=str(row.organization_id),
        )
        return False
    return True


def _mailbox(username: str) -> str | None:
    """`username` as an address, if it is one. Google Workspace and Microsoft 365 log in as the
    mailbox itself, which is by construction an address they will let this connection send as;
    an SMTP relay's username (an API key id, the literal `apikey`) is not one."""
    try:
        return domain.normalize_recipient("email", username)
    except domain.RuleError:
        return None


def _secret_of(connection: EmailConnection, cfg: Settings) -> str:
    """The connection's password, for scrubbing only. `_transport_for_connection` opened it
    already for the transport; this is the same value, held for as long as one send takes."""
    if not connection.secret_enc:
        return ""
    return pii.open_sealed(connection.secret_enc, settings=cfg)
