"""Inbound webhooks for connected channels: verify, minimise, record once.

The shape of `services/payment`'s merchant webhook, for operations connections (a carrier's
tracking callbacks, an ERP's or POS's change notifications, a marketplace's order events):

1. The connection is named in the URL (`?connection=<channel id>`) because the secret that
   authenticates the body is that connection's own and has to be found before the body can be
   trusted. The lookup runs on the BYPASSRLS outbox role's column grant (`id`, `organization_id`,
   `provider`, `status`, `deleted_at` of `channels` -- `migrations/env.py`), never a secret.
2. **The provider's `WebhookVerifier` checks the signature over the raw bytes, with the
   connection's own sealed credential, before anything is parsed -- and fails closed**: no
   verifier for the provider, an unknown or disconnected connection, a provider that is not the
   connection's, a missing credential, a missing or wrong signature all answer the same 400, so a
   prober learns nothing about which connections exist.
3. The payload is minimised to the verifier's allow-list (scalars only) and inserted once per
   `(organization_id, provider, event_id)`; a redelivery lands no second row and is acknowledged.

No handler acts on an event yet: connectors add one with their adapter. Recording first, acting
second, is what makes redelivery safe.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.channels.shopify import unpack_credential
from app.models import Channel, IntegrationEvent
from platform_core.errors import PayloadTooLargeProblem, ProblemError
from platform_core.logging import get_logger

log = get_logger("operations.integration_webhooks")

#: The largest webhook body this service buffers, checked before any signature work. The gateway
#: enforces the same ceiling first (`routes.prod.json`).
MAX_WEBHOOK_BODY_BYTES = 256 * 1024
_MAX_FIELD_CHARS = 200
_MAX_EVENT_ID_CHARS = 120


class IntegrationWebhookProblem(ProblemError):
    """A webhook this service will not record: unverifiable, unidentifiable or for a connection it
    does not serve. One status and one message for all of them -- the reason is logged, not told."""

    status_code = 400
    problem_type = "invalid-webhook"
    title = "Invalid webhook"


@dataclass(frozen=True, slots=True)
class ParsedEvent:
    event_id: str
    event_name: str
    #: Every field the provider's body carried at the top level; `minimise` keeps the allow-list.
    fields: Mapping[str, Any]


class WebhookVerifier(Protocol):
    """One provider's webhook trust scheme. A structural `Protocol`, like `ChannelClient`."""

    provider: str
    #: Event names this provider's handlers act on; any other authentic event is recorded as
    #: `ignored`.
    known_events: frozenset[str]
    #: The only top-level body fields that may be stored. Scalars only; never a name, phone,
    #: address or card detail.
    allowed_fields: frozenset[str]

    def verify(self, *, headers: Mapping[str, str], body: bytes, secret: str) -> bool:
        """True only for a signature that matches `body` under `secret`. Constant-time; False for
        anything missing or malformed."""
        ...

    def parse(self, *, headers: Mapping[str, str], body: bytes) -> ParsedEvent | None:
        """The delivery's id, name and fields, or None if it cannot be identified."""
        ...


class MockMarketplaceVerifier:
    """The sandbox marketplace's scheme: hex `HMAC-SHA256(api_key, body)` in `x-mock-signature`,
    the delivery id in the body's `id`. Real providers add their own beside it."""

    provider = "mock_marketplace"
    known_events = frozenset({"order.created", "order.updated"})
    allowed_fields = frozenset({"order_id", "status"})
    signature_header = "x-mock-signature"

    def verify(self, *, headers: Mapping[str, str], body: bytes, secret: str) -> bool:
        signature = headers.get(self.signature_header)
        if not secret or not signature:
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse(self, *, headers: Mapping[str, str], body: bytes) -> ParsedEvent | None:
        try:
            data = json.loads(body)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        event_id, event_name = data.get("id"), data.get("event")
        if not isinstance(event_id, str) or not isinstance(event_name, str):
            return None
        return ParsedEvent(event_id=event_id, event_name=event_name, fields=data)


class ShopifyVerifier:
    """Shopify's scheme: base64 `HMAC-SHA256(webhook secret, body)` in `X-Shopify-Hmac-Sha256`,
    the delivery id in `X-Shopify-Webhook-Id` (the same across Shopify's own retries), the topic in
    `X-Shopify-Topic`. `secret` is the connection's opened credential; the webhook secret is its
    second half, and a connection without one verifies nothing."""

    provider = "shopify"
    known_events = frozenset({"orders/create"})
    #: The order id and its display number ("#1001") only -- never the buyer.
    allowed_fields = frozenset({"id", "name"})

    def verify(self, *, headers: Mapping[str, str], body: bytes, secret: str) -> bool:
        signature = headers.get("x-shopify-hmac-sha256")
        _, webhook_secret = unpack_credential(secret)
        if not webhook_secret or not signature:
            return False
        digest = hmac.new(webhook_secret.encode(), body, hashlib.sha256).digest()
        return hmac.compare_digest(base64.b64encode(digest).decode(), signature)

    def parse(self, *, headers: Mapping[str, str], body: bytes) -> ParsedEvent | None:
        event_id, topic = headers.get("x-shopify-webhook-id"), headers.get("x-shopify-topic")
        if not event_id or not topic:
            return None
        try:
            data = json.loads(body)
        except ValueError:
            return None
        return ParsedEvent(
            event_id=event_id, event_name=topic, fields=data if isinstance(data, dict) else {}
        )


#: Provider name -> verifier. A provider with no entry has no public webhook: its route answers 400.
VERIFIERS: dict[str, WebhookVerifier] = {
    "mock_marketplace": MockMarketplaceVerifier(),
    "shopify": ShopifyVerifier(),
}


def minimise(fields: Mapping[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    """The allow-listed, scalar, bounded subset of `fields` -- all that is stored."""
    kept: dict[str, Any] = {}
    for key in sorted(allowed & fields.keys()):
        value = fields[key]
        if value is None or isinstance(value, bool | int | float):
            kept[key] = value
        elif isinstance(value, str):
            kept[key] = value[:_MAX_FIELD_CHARS]
    return kept


async def read_capped_body(request: Request, limit: int = MAX_WEBHOOK_BODY_BYTES) -> bytes:
    """The raw body, refused (413) above `limit` -- from the declared length when there is one,
    and otherwise by counting the stream and stopping at the first byte over."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise PayloadTooLargeProblem("webhook body too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise PayloadTooLargeProblem("webhook body too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def webhook_channel(
    bypass_session: AsyncSession, channel_id: uuid.UUID
) -> tuple[uuid.UUID, str, str] | None:
    """`(organization_id, provider, status)` of a live channel -- **on the bypass session**, before
    any tenant is bound, reading only the columns the outbox role is granted."""
    row = (
        await bypass_session.execute(
            select(Channel.organization_id, Channel.provider, Channel.status).where(
                Channel.id == channel_id, Channel.deleted_at.is_(None)
            )
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1], row[2])


@dataclass(frozen=True, slots=True)
class Recorded:
    event_id: str
    outcome: str
    duplicate: bool
    #: The minimised payload that was stored (what a handler may act on).
    payload: Mapping[str, Any]


async def record_event(
    session: AsyncSession,
    *,
    channel: Channel,
    verifier: WebhookVerifier,
    headers: Mapping[str, str],
    body: bytes,
    secret: str,
) -> Recorded:
    """Verify, parse, minimise and insert once. `session` is tenant-bound and in a transaction;
    `secret` is the connection's opened credential. Raises `IntegrationWebhookProblem`."""
    if not verifier.verify(headers=headers, body=body, secret=secret):
        log.warning(
            "integration_webhook.bad_signature",
            organization_id=str(channel.organization_id),
            channel_id=str(channel.id),
            provider=channel.provider,
        )
        raise IntegrationWebhookProblem("could not verify the webhook")
    parsed = verifier.parse(headers=headers, body=body)
    if parsed is None or not parsed.event_id or len(parsed.event_id) > _MAX_EVENT_ID_CHARS:
        raise IntegrationWebhookProblem("could not verify the webhook")
    known = parsed.event_name in verifier.known_events
    stmt = (
        pg_insert(IntegrationEvent)
        .values(
            organization_id=channel.organization_id,
            channel_id=channel.id,
            provider=channel.provider,
            event_id=parsed.event_id,
            event_name=parsed.event_name[:80],
            payload=minimise(parsed.fields, verifier.allowed_fields),
            outcome="received" if known else "ignored",
            outcome_reason=None if known else "unknown_event",
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "provider", "event_id"])
        .returning(IntegrationEvent.id)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    return Recorded(
        event_id=parsed.event_id,
        outcome="received" if known else "ignored",
        duplicate=inserted is None,
        payload=minimise(parsed.fields, verifier.allowed_fields),
    )
