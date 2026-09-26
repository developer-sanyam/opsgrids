"""Pure mappings from Meta's phone-number vocabulary to the contract's (slice W1). No I/O.

Meta spells things in capitals and in more variants than the numbers screen has words for; every
unfamiliar value maps to the safe, non-committal word (`unknown`, `none`, `disconnected`) rather
than raising, because Meta adds values on its own schedule and a cached row must never fail to
render.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

QUALITY: Final = frozenset({"green", "yellow", "red", "unknown"})
LIMITS: Final = frozenset({"TIER_250", "TIER_1K", "TIER_10K", "TIER_100K", "UNLIMITED", "unknown"})

#: A cached number list older than this is refreshed from Graph on read.
NUMBERS_TTL: Final = timedelta(minutes=10)
#: A token that expires within this long is reported `expiring`.
TOKEN_EXPIRING_WITHIN: Final = timedelta(days=7)
#: A webhook subscription with no event newer than this is reported `silent`.
WEBHOOK_SILENT_AFTER: Final = timedelta(days=7)
#: A setup run still `running` after this long is treated as abandoned.
SETUP_STALE_AFTER: Final = timedelta(minutes=15)


def quality_from_meta(raw: object) -> str:
    value = str(raw or "").strip().lower()
    return value if value in QUALITY else "unknown"


def limit_from_meta(raw: object) -> str:
    """`TIER_UNLIMITED` is the contract's `UNLIMITED`; a tier the contract has no word for (Meta's
    `TIER_50`, say) is `unknown` rather than a value the frontend cannot render."""
    value = str(raw or "").strip().upper()
    if value == "TIER_UNLIMITED":
        return "UNLIMITED"
    return value if value in LIMITS else "unknown"


def name_status_from_meta(raw: object) -> str:
    value = str(raw or "").strip().lower()
    if value in ("approved", "available_without_review"):
        return "approved"
    if value in ("pending_review", "pending"):
        return "pending_review"
    if value in ("declined", "rejected"):
        return "declined"
    return "none"


def status_from_meta(raw: object) -> str:
    value = str(raw or "").strip().upper()
    if value == "CONNECTED":
        return "connected"
    if value in ("PENDING", "UNVERIFIED", "ONBOARDING"):
        return "pending_verification"
    return "disconnected"


def digits(value: str | None) -> str:
    """The digits of a phone number, for comparing `+91 98765 43210` with `919876543210`."""
    return "".join(ch for ch in (value or "") if ch.isdigit())


def token_state(stored: str, *, expires_at: datetime | None, now: datetime | None = None) -> str:
    """The state the health card shows: what was stored, adjusted by the token's own expiry."""
    now = now or datetime.now(UTC)
    if stored == "invalid":
        return "invalid"
    if expires_at is not None:
        if expires_at <= now:
            return "invalid"
        if expires_at - now <= TOKEN_EXPIRING_WITHIN and stored == "valid":
            return "expiring"
    return stored if stored == "valid" else "unknown"


def webhook_state(
    *, subscribed_at: datetime | None, last_event_at: datetime | None, now: datetime | None = None
) -> str:
    """`unverified` until Meta has confirmed the subscription (or an event has arrived);
    `receiving` with a recent event; `silent` when subscribed but nothing has arrived lately."""
    now = now or datetime.now(UTC)
    if subscribed_at is None and last_event_at is None:
        return "unverified"
    if last_event_at is not None and now - last_event_at <= WEBHOOK_SILENT_AFTER:
        return "receiving"
    return "silent"
