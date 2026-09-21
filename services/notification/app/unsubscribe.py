"""The one-click unsubscribe token — the trust boundary for Engage's only public write.

A stranger with a link, not a caller with a token, so there is no principal to authorize. The
signature *is* the authorization: `sign` mints `org_id:contact_id` under an HMAC keyed by a
**platform** secret (`Settings.engage_unsubscribe_secret`) and `verify` accepts only a token this
process minted, for the contact and organization it names. There is deliberately no
organization-specific secret — CLAUDE.md is explicit that the tenant GUC and every other tenancy
mechanism here is a chokepoint the platform owns, and a per-org secret would mean *n* places a key
could leak from instead of one place to rotate.

The token is unguessable rather than merely unlisted: HMAC-SHA256 gives an attacker no shortcut
to a valid signature short of the key itself, so a contact id someone guesses (they are UUIDs,
which is already not guessable) still yields nothing without the tag alongside it.

Not a JWT. A JWT here would carry a library's parsing surface and a header an attacker can
tamper with (`alg: none` is the perennial mistake) for two fields that need none of it — this is
four lines of `hmac.compare_digest` and there is nothing in it to get wrong twice.
"""

from __future__ import annotations

import base64
import hmac
import uuid
from dataclasses import dataclass
from hashlib import sha256

from app.config import Settings


class UnsubscribeTokenError(Exception):
    """The token is malformed, or its signature does not verify under the configured secret.

    One exception for every way a token can fail to check: a client should not learn *which*
    part of a forged or corrupted link was wrong, only that the link did not work.
    """


@dataclass(frozen=True, slots=True)
class UnsubscribeTarget:
    organization_id: uuid.UUID
    contact_id: uuid.UUID


def _mac(payload: bytes, *, settings: Settings) -> bytes:
    key = settings.engage_unsubscribe_secret.get_secret_value().encode("utf-8")
    return hmac.new(key, payload, sha256).digest()


def sign(organization_id: uuid.UUID, contact_id: uuid.UUID, *, settings: Settings) -> str:
    """The token a suppression email's unsubscribe link carries in its query string."""
    payload = f"{organization_id}:{contact_id}".encode()
    tag = _mac(payload, settings=settings)
    body = base64.urlsafe_b64encode(payload).rstrip(b"=")
    sig = base64.urlsafe_b64encode(tag).rstrip(b"=")
    return f"{body.decode()}.{sig.decode()}"


def verify(token: str, *, settings: Settings) -> UnsubscribeTarget:
    """The organization and contact a genuine, unmodified token names.

    Raises `UnsubscribeTokenError` on anything else — a missing segment, un-decodable base64, or
    a signature that does not verify under the current secret. `hmac.compare_digest` is what
    makes the signature check itself safe against a timing attack; nothing here builds the
    comparison out of `==`.
    """
    try:
        body_part, sig_part = token.split(".", 1)
        payload = _pad_b64decode(body_part)
        tag = _pad_b64decode(sig_part)
        org_str, contact_str = payload.decode("utf-8").split(":", 1)
        organization_id = uuid.UUID(org_str)
        contact_id = uuid.UUID(contact_str)
    except (ValueError, UnicodeDecodeError) as exc:
        raise UnsubscribeTokenError("Malformed unsubscribe token") from exc

    expected = _mac(payload, settings=settings)
    if not hmac.compare_digest(tag, expected):
        raise UnsubscribeTokenError("Unsubscribe token signature does not verify")
    return UnsubscribeTarget(organization_id=organization_id, contact_id=contact_id)


def _pad_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
