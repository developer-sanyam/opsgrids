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
import re
import uuid
from collections.abc import Iterator
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
    """The **first** number named anywhere in this webhook body.

    Kept only for the tests that predate batching and for a caller that already knows a body
    holds exactly one number. **Nothing that decides which organization a status or a message
    belongs to may use this** — Meta batches several WABAs' changes into one POST behind a single
    platform App Secret, and picking one number for the whole body is how one organization's
    webhook silently rewrites another's. `group_statuses_by_phone` and `group_inbound_by_phone`
    are the routing-safe replacement: they resolve the number **per `change`**, not per body.
    """
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            metadata = (change.get("value") or {}).get("metadata") or {}
            found = metadata.get("phone_number_id")
            if found:
                return str(found)
    return None


def _iter_changes(payload: dict[str, Any]) -> Iterator[tuple[str | None, dict[str, Any]]]:
    """Every `change.value` in this body, paired with **that change's own** `phone_number_id`.

    The one place both grouping functions below read `metadata.phone_number_id`, so there is
    exactly one line that decides which change belongs to which number — and it is scoped to the
    change, not to the payload, which is the whole fix for the cross-tenant misrouting a
    payload-wide lookup caused.
    """
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            found = metadata.get("phone_number_id")
            yield (str(found) if found else None, value)


def entry_ids_by_phone(payload: dict[str, Any]) -> dict[str, set[str]]:
    """Every `phone_number_id` named anywhere in this body, paired with the WABA id(s) — `entry.
    id` — of the entry that named it.

    Meta nests the WABA id one level above the change that carries a number, so this is the one
    place the route can ask "does the account we resolved this number to actually belong to the
    WABA this batch claims sent it" — a check `_iter_changes` itself has no reason to make, since
    the grouping functions below never look above `change.value`. A phone number renumbered to a
    different WABA without this service's own record being updated (or a forged `entry.id`, though
    the signature already rules that out) is exactly the shape a stale `ProviderAccount.waba_id`
    would miss silently; the route drops the change instead of trusting it.
    """
    result: dict[str, set[str]] = {}
    for entry in payload.get("entry") or []:
        entry_id = entry.get("id")
        if not entry_id:
            continue
        for change in entry.get("changes") or []:
            metadata = (change.get("value") or {}).get("metadata") or {}
            found = metadata.get("phone_number_id")
            if found:
                result.setdefault(str(found), set()).add(str(entry_id))
    return result


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
    #: The provider's own status object, whole. Never persisted in the clear — its only use is
    #: `services.record_dead_letter` sealing it into `InboundDeadLetter.raw_enc` so a parked
    #: status can be replayed after a fix, the same reasoning `InboundMessage.raw` documents.
    raw: dict[str, Any] | None = None

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
    """Every status update in one webhook body, flattened across every number it names.

    Kept for tests and any caller that already knows a body carries exactly one organization's
    changes. **The webhook route itself must use `group_statuses_by_phone`** — flattening across
    numbers is exactly the shape that let one organization's status update land against another's
    message before this was caught.
    """
    updates: list[StatusUpdate] = []
    for grouped in group_statuses_by_phone(payload).values():
        updates.extend(grouped)
    return updates


def group_statuses_by_phone(payload: dict[str, Any]) -> dict[str, list[StatusUpdate]]:
    """Every status update, grouped by **the phone number its own change named** — never by the
    first number found anywhere in the body.

    Meta batches several WABAs into one POST behind the platform's single App Secret, so a status
    update for organization B's number can arrive in the same body as one for organization A's.
    Resolving one organization for the whole payload (`phone_number_id`'s old role here) would
    file B's receipt against A's messages. A change with no `phone_number_id` at all is dropped —
    logged by the caller once it knows the number could not be resolved to an account, never here,
    because this module holds no logger and touches no PII.
    """
    grouped: dict[str, list[StatusUpdate]] = {}
    for phone_id, value in _iter_changes(payload):
        if phone_id is None:
            continue
        for raw in value.get("statuses") or []:
            update = _status_from(raw)
            if update is not None:
                grouped.setdefault(phone_id, []).append(update)
    return grouped


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
        raw=raw,
    )


def status_from_raw(raw: dict[str, Any]) -> StatusUpdate | None:
    """Rebuild one `StatusUpdate` from the raw object a dead letter sealed. Thin, public alias
    for `_status_from` — `app/replay_dead_letters.py`'s only route back to a typed value from a
    parked row's `raw_enc`, kept separate from the private name so the ordinary parsing path
    (`group_statuses_by_phone`) is not the thing a replay tool reaches into."""
    return _status_from(raw)


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
    """Every customer message in one webhook body, flattened across every number it names.

    Kept for tests and any caller that already knows a body carries exactly one organization's
    changes. **The webhook route itself must use `group_inbound_by_phone`** — the same
    cross-tenant reasoning `parse_statuses`'s docstring gives applies here identically: a stranger
    messaging organization B's number must never be filed as a message to organization A's
    conversation because both changes shared one POST.
    """
    messages: list[InboundMessage] = []
    for grouped in group_inbound_by_phone(payload).values():
        messages.extend(grouped)
    return messages


def group_inbound_by_phone(payload: dict[str, Any]) -> dict[str, list[InboundMessage]]:
    """Every customer message, grouped by **the phone number its own change named**.

    See `group_statuses_by_phone`'s docstring for why a payload-wide number is unsafe. A change
    with no `phone_number_id` is dropped rather than guessed at.
    """
    grouped: dict[str, list[InboundMessage]] = {}
    for phone_id, value in _iter_changes(payload):
        if phone_id is None:
            continue
        # `contacts` carries the sender's profile name, keyed by `wa_id`, scoped to this same
        # change — a name from one number's `contacts` block must never be attached to a message
        # from a different number that happened to share the body.
        names = {
            str(c.get("wa_id")): ((c.get("profile") or {}).get("name"))
            for c in (value.get("contacts") or [])
            if c.get("wa_id")
        }
        for raw in value.get("messages") or []:
            parsed = _inbound_from(raw, names)
            if parsed is not None:
                grouped.setdefault(phone_id, []).append(parsed)
    return grouped


#: `InboundMessageRow.type`'s column width. Every type Meta documents ("text", "image", ...) fits
#: with room to spare; this is a defensive ceiling against a value that does not, so a message
#: with an unrecognised, oversized `type` is stored with a truncated one rather than raising a
#: `DataError` at the insert and being lost — the fact that a customer wrote in matters more than
#: the exact spelling of a `type` this service does not interpret regardless.
_TYPE_MAX_LENGTH: Final = 20


def _inbound_from(raw: dict[str, Any], names: dict[str, str | None]) -> InboundMessage | None:
    provider_message_id = raw.get("id")
    sender = raw.get("from")
    if not provider_message_id or not sender:
        return None

    message_type = str(raw.get("type") or "unknown")[:_TYPE_MAX_LENGTH]
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


def inbound_from_raw(
    raw: dict[str, Any], names: dict[str, str | None] | None = None
) -> InboundMessage | None:
    """Rebuild one `InboundMessage` from the raw object a dead letter sealed. Thin, public alias
    for `_inbound_from` — see `status_from_raw` for why this is not just made public outright.
    `names` is normally the batch's `contacts` block, which a dead letter does not keep (only the
    single message object is sealed); replaying without it just means the profile name is not
    re-attached, which is display-only and never blocks the fold itself.
    """
    return _inbound_from(raw, names or {})


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


#: How long a transient fold failure is left to raise into a 503 (so Meta redelivers the body)
#: before the item is parked instead. A dropped connection or a deadlock that is still happening
#: six hours after the event occurred is not "about to clear on the next redelivery" — Meta's own
#: retry schedule tapers off well before this — and asking for a 503 forever over one stuck item is
#: exactly the loop `record_dead_letter` exists to end.
TRANSIENT_RETRY_CAP: Final = timedelta(hours=6)


def transient_retry_exhausted(occurred_at: datetime, *, now: datetime | None = None) -> bool:
    """Has a transient failure on this item been left to redeliver long enough that parking it —
    not answering with one more 503 — is the safer default?

    Every other item in the same batch already dead-letters or folds regardless of this; it only
    changes the outcome for the one item still failing once it is old enough that "ask again in a
    moment" has stopped being a true description of it.
    """
    reference = now or datetime.now(UTC)
    return reference - occurred_at > TRANSIENT_RETRY_CAP


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

#: The re-subscribe half of the same convention. A customer who typed STOP by habit and now wants
#: messages again should not have to contact support to say so.
START_KEYWORDS: Final[frozenset[str]] = frozenset({"START", "UNSTOP", "SUBSCRIBE"})

#: Trailing punctuation a phone keyboard's autocapitalize/autopunctuate adds without the customer
#: noticing — "Stop." and "STOP!" mean the same thing "STOP" does, and a strict exact match would
#: silently keep messaging someone who typed it as an ordinary sentence.
_TRAILING_PUNCTUATION = re.compile(r"[.!?,;:]+$")


def _keyword(body: str | None) -> str | None:
    """`body`, trimmed, trailing punctuation dropped, upper-cased — or `None` if there is
    nothing to match a keyword against. The one normalisation both `is_stop_keyword` and
    `is_start_keyword` share, so the two can never drift on what counts as "the same word"."""
    if body is None:
        return None
    stripped = _TRAILING_PUNCTUATION.sub("", body.strip()).strip()
    return stripped.upper() if stripped else None


def is_stop_keyword(body: str | None) -> bool:
    """Is this inbound text an opt-out request?

    Exact match against the normalised body — the same convention every SMS/WhatsApp provider's
    own STOP handling uses, so a customer who has opted out of another business's messages this
    way has the same expectation here. Not a substring match, and not defeated by a trailing
    full stop or exclamation mark a keyboard added on its own.
    """
    keyword = _keyword(body)
    return keyword is not None and keyword in STOP_KEYWORDS


def is_start_keyword(body: str | None) -> bool:
    """Is this inbound text a re-subscribe request? See `is_stop_keyword` for the same rules."""
    keyword = _keyword(body)
    return keyword is not None and keyword in START_KEYWORDS
