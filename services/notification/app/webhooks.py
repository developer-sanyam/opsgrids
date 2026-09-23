"""Inbound webhooks from the channel provider — parsing and authenticity. No I/O.

Everything here is a function of its arguments, which matters more than usual: this is the one
surface on the platform that accepts input from outside the cluster with no token behind it, so
the rules that decide whether to trust a request should be verifiable without a database, a
network or a running service.

Every field name and header below was read off Meta's published pages, not recalled. The
envelope is the same for every webhook:

    {"object": "whatsapp_business_account",
     "entry": [{"id": "<WABA id>",
                "changes": [{"value": {"messaging_product": "whatsapp",
                                       "metadata": {"display_phone_number": "...",
                                                    "phone_number_id": "..."},
                                       "statuses": [...] | "messages": [...]},
                             "field": "messages"}]}]}

and a status entry is:

    {"id": "wamid...", "status": "delivered", "timestamp": "1750263773",
     "recipient_id": "16505551234", "conversation": {...}, "pricing": {...}}
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app import domain

#: The header Meta signs the raw body with.
SIGNATURE_HEADER: Final = "X-Hub-Signature-256"
_SIGNATURE_PREFIX: Final = "sha256="

#: Meta's message statuses, and what each means for a message we sent.
STATUS_SENT: Final = "sent"
STATUS_DELIVERED: Final = "delivered"
STATUS_READ: Final = "read"
STATUS_FAILED: Final = "failed"
PROVIDER_STATUSES: Final[tuple[str, ...]] = (
    STATUS_SENT,
    STATUS_DELIVERED,
    STATUS_READ,
    STATUS_FAILED,
)

#: A fixed namespace so the same provider event always derives the same idempotency id. It is a
#: constant rather than generated, because a namespace that changed between deploys would make
#: every redelivery look new.
_DEDUPE_NAMESPACE: Final = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


class WebhookError(ValueError):
    """The request is not a webhook this service can trust or understand."""


def verify_signature(raw_body: bytes, header_value: str | None, app_secret: str) -> bool:
    """Is this body genuinely from Meta?

    HMAC-SHA256 over the **raw request body**, keyed by the App Secret, compared to the hex
    digest after `sha256=` in `X-Hub-Signature-256`.

    Two details that are easy to get wrong and fatal if you do. The body must be the exact bytes
    received — parsing to JSON and re-serialising changes whitespace and key order, so the digest
    would never match and every webhook would be rejected. And the comparison is
    `compare_digest`, not `==`: a byte-at-a-time comparison leaks, through timing, how much of a
    forged signature was correct, which is enough to construct one.

    Returns False rather than raising, so the caller decides the status code. A missing or
    malformed header is not an error to explain — it is simply not a signature.
    """
    if not header_value or not header_value.startswith(_SIGNATURE_PREFIX):
        return False
    supplied = header_value[len(_SIGNATURE_PREFIX) :].strip()
    if not supplied:
        return False

    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


def verification_challenge(
    mode: str | None, token: str | None, challenge: str | None, *, expected_token: str
) -> str:
    """Answer Meta's `GET` handshake, or refuse it.

    Meta subscribes by calling the endpoint with `hub.mode=subscribe`, its own
    `hub.verify_token` and a `hub.challenge` to echo. Returning the challenge is what proves we
    own the endpoint.

    The token comparison is constant-time for the same reason the signature's is. It is a
    shared secret, and this route is unauthenticated by necessity.
    """
    if mode != "subscribe":
        raise WebhookError("not a subscription handshake")
    if not token or not expected_token or not hmac.compare_digest(token, expected_token):
        raise WebhookError("verification token does not match")
    if not challenge:
        raise WebhookError("no challenge to echo")
    return challenge


def phone_number_id(payload: dict[str, Any]) -> str | None:
    """The number that received this webhook — the only thing that says whose tenant it is.

    None when the payload carries no metadata, which is how a malformed or unfamiliar webhook
    presents. The caller acknowledges it rather than failing: Meta retries anything it does not
    get a 200 for, and retrying a payload we will never understand is a loop.
    """
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            metadata = (change.get("value") or {}).get("metadata") or {}
            found = metadata.get("phone_number_id")
            if found:
                return str(found)
    return None


@dataclass(frozen=True, slots=True)
class StatusUpdate:
    """One provider message-status event."""

    provider_message_id: str
    status: str
    recipient: str
    occurred_at: datetime
    #: Meta's own error code and title when `status` is `failed`, for an operator to act on.
    error_code: str | None = None
    error_detail: str | None = None

    @property
    def dedupe_id(self) -> uuid.UUID:
        """A stable id for `processed_events`, derived rather than stored.

        Meta redelivers, and a message legitimately produces several statuses — `sent`, then
        `delivered`, then `read` — so the identity of an event is the pair, not the message.
        Deriving a UUID5 from it lets the existing idempotency ledger do this job instead of a
        second table: the platform already has one, and two ledgers eventually disagree.
        """
        return uuid.uuid5(_DEDUPE_NAMESPACE, f"{self.provider_message_id}:{self.status}")


def parse_statuses(payload: dict[str, Any]) -> list[StatusUpdate]:
    """Every status update in one webhook body.

    A single POST can carry several, across several `entry` and `changes` objects, and Meta
    batches them freely — so this flattens rather than assuming one. Anything unparseable is
    skipped rather than raising: one malformed entry must not discard the others in the same
    body, because Meta will not send them again separately.
    """
    updates: list[StatusUpdate] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for raw in value.get("statuses") or []:
                update = _status_from(raw)
                if update is not None:
                    updates.append(update)
    return updates


def _status_from(raw: dict[str, Any]) -> StatusUpdate | None:
    provider_message_id = raw.get("id")
    status = raw.get("status")
    if not provider_message_id or status not in PROVIDER_STATUSES:
        # An unfamiliar status is not a failure. Meta adds them, and a build that rejected a
        # webhook for carrying one would start failing on their release schedule, not ours.
        return None

    errors = raw.get("errors") or []
    first = errors[0] if errors else {}

    return StatusUpdate(
        provider_message_id=str(provider_message_id),
        status=str(status),
        recipient=str(raw.get("recipient_id") or ""),
        occurred_at=_timestamp(raw.get("timestamp")),
        error_code=str(first.get("code")) if first.get("code") is not None else None,
        error_detail=(first.get("title") or first.get("message")) or None,
    )


def _timestamp(value: Any) -> datetime:
    """Meta sends a Unix timestamp as a string. Unparseable falls back to now.

    Falling back rather than raising because the status is the fact worth keeping; losing a
    delivery receipt over a malformed clock would be the wrong trade, and the row's own
    `created_at` records when we learned it regardless.
    """
    try:
        return datetime.fromtimestamp(int(str(value)), tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


# --------------------------------------------------------------------- inbound messages
#
# What a customer sends us. The envelope is the same as a status webhook; the payload carries
# `messages` instead of `statuses`.
#
# Only `text` is interpreted. Meta publishes the text shape — `{"type": "text", "text":
# {"body": "..."}}` — and every message object carries `from`, `id`, `timestamp` and `type`.
# The *inner* shapes of an interactive reply (`button_reply`, `list_reply`) and of media are
# not published on any page consulted, so they are stored whole and left uninterpreted rather
# than parsed on a guess. That is the rule that caught the `sub_type` bug: a field name comes
# from Meta's pages or it does not get written.
#
# Storing the raw object means nothing is lost. When the shapes are confirmed, the parser gains
# a branch and the history is already there to backfill from.

#: The message types Meta documents. Anything else is stored with `type` as given.
TYPE_TEXT: Final = "text"

#: How long after a customer's last message free-form replies are allowed. Meta's customer
#: service window. A constant here because the rule is Meta's, not a tenant's preference.
SERVICE_WINDOW = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """One message a customer sent."""

    provider_message_id: str
    from_address: str
    message_type: str
    occurred_at: datetime
    #: The text, for `type: "text"`. None for every other type, because the body is somewhere
    #: inside a shape this service does not claim to understand yet.
    body: str | None
    #: The provider's own message object, stored whole. The record of what arrived, and what a
    #: later parser will read when the interactive and media shapes are confirmed.
    raw: dict[str, Any]
    #: The sender's profile name, when Meta includes one. Display only.
    profile_name: str | None = None

    @property
    def dedupe_id(self) -> uuid.UUID:
        """Stable id for `processed_events`. An inbound message has one identity — unlike a
        status, which is identified by the message *and* the status — so the id is the message
        id alone."""
        return uuid.uuid5(_DEDUPE_NAMESPACE, f"inbound:{self.provider_message_id}")


def parse_inbound(payload: dict[str, Any]) -> list[InboundMessage]:
    """Every customer message in one webhook body.

    Flattened across `entry` and `changes` for the same reason statuses are: Meta batches
    freely and will not resend what a handler dropped. A message missing its id or sender is
    skipped — there is nothing to thread it onto and nothing to deduplicate it by.
    """
    messages: list[InboundMessage] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            # `contacts` carries the sender's profile name, keyed by `wa_id`.
            names = {
                str(c.get("wa_id")): ((c.get("profile") or {}).get("name"))
                for c in (value.get("contacts") or [])
                if c.get("wa_id")
            }
            for raw in value.get("messages") or []:
                parsed = _inbound_from(raw, names)
                if parsed is not None:
                    messages.append(parsed)
    return messages


def _inbound_from(raw: dict[str, Any], names: dict[str, str | None]) -> InboundMessage | None:
    provider_message_id = raw.get("id")
    sender = raw.get("from")
    if not provider_message_id or not sender:
        return None

    message_type = str(raw.get("type") or "unknown")
    body: str | None = None
    if message_type == TYPE_TEXT:
        body = ((raw.get("text") or {}).get("body")) or None

    return InboundMessage(
        provider_message_id=str(provider_message_id),
        from_address=str(sender),
        message_type=message_type,
        occurred_at=_timestamp(raw.get("timestamp")),
        body=body,
        raw=raw,
        profile_name=names.get(str(sender)),
    )


def within_service_window(
    last_customer_message_at: datetime | None, *, now: datetime | None = None
) -> bool:
    """Is free-form messaging allowed right now?

    Meta accepts free text only inside 24 hours of the customer's last message; outside it,
    only an approved template. So this is not a nicety — it decides whether a send is possible
    at all, and getting it wrong means either a refused message or a customer who hears
    nothing.

    A conversation with no customer message has never opened a window, so False. That is the
    common case for a proactive notification, which is exactly why templates exist.
    """
    if last_customer_message_at is None:
        return False
    reference = now or datetime.now(UTC)
    return reference - last_customer_message_at < SERVICE_WINDOW


def sender_e164(raw: str) -> str | None:
    """Meta's `from` as E.164, or None when it is not a plausible number.

    Meta sends the sender as bare digits with the country code and no separators — no leading
    `+`, unlike every other E.164 value this service handles. `domain.normalize_recipient`
    refuses exactly that shape on purpose (a bare number elsewhere in this codebase means a
    missing country code, and guessing one sends a stranger someone else's invoice), so this
    adds the `+` a genuine webhook implies before asking the same validator, rather than
    duplicating its digit rules.
    """
    try:
        return domain.normalize_recipient("whatsapp", f"+{raw.strip()}")
    except domain.RuleError:
        return None


#: Keywords Meta and the carriers treat as an opt-out request, upper-cased for a case-insensitive
#: exact match. Not a substring match: a customer asking "how do I stop my subscription" must not
#: be silently unsubscribed for using the word in a sentence.
STOP_KEYWORDS: Final[frozenset[str]] = frozenset({"STOP", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"})


def is_stop_keyword(body: str | None) -> bool:
    """Is this inbound text an opt-out request?

    Exact match against the trimmed, upper-cased body — the same convention every SMS/WhatsApp
    provider's own STOP handling uses, so a customer who has opted out of another business's
    messages this way has the same expectation here.
    """
    if body is None:
        return False
    return body.strip().upper() in STOP_KEYWORDS
