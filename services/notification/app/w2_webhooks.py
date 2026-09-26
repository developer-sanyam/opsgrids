"""Meta's account-level webhooks: template status and quality, number quality, account updates.

`app/api/webhooks.py` folds message statuses and inbound messages, which name a *phone number*.
These changes name only the WhatsApp Business Account (`entry.id`) — a template and a number's
quality belong to the WABA, not to one sender — so the tenant here is resolved from the WABA, on
the same cross-tenant directory reader, and everything downstream is bound to that organization.

The trust story is the receive route's, unchanged: this is only reached after the signature over
the raw body has verified. What this module adds is *routing*: a change for a WABA nobody here has
connected touches nothing, and one for a WABA that is connected touches only the organization(s)
that connected it, and within them only the template Meta's own id (or name and language) names.

Per-item behaviour matches the existing folds. Each (organization, change) is its own short
transaction, idempotent on a derived `processed_events` id. A permanent failure is parked in the
dead-letter table and acknowledged; a transient one makes the whole request answer 5xx so Meta
redelivers, and only what did not fold does any work the second time.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app import services, w2_selectors, w2_services, webhooks
from app.models import ProcessedEvent
from platform_core.db import session_scope
from platform_core.logging import get_logger

log = get_logger("notification.w2_webhooks")

TEMPLATE_STATUS: Final = "message_template_status_update"
TEMPLATE_QUALITY: Final = "message_template_quality_update"
NUMBER_QUALITY: Final = "phone_number_quality_update"
ACCOUNT_UPDATE: Final = "account_update"
FIELDS: Final = frozenset({TEMPLATE_STATUS, TEMPLATE_QUALITY, NUMBER_QUALITY, ACCOUNT_UPDATE})

#: `update_number_quality(session, *, organization_id, waba_id, display_phone_number, event,
#: current_limit)`: how a `phone_number_quality_update` reaches the numbers cache, which another
#: slice owns. Registered by whichever module owns that cache; until one registers, the change is
#: logged and acknowledged (the cache refreshes from Graph on read, so nothing is lost).
NumberQualityHandler = Callable[..., Awaitable[None]]
_number_quality_handler: NumberQualityHandler | None = None


def register_number_quality_handler(handler: NumberQualityHandler | None) -> None:
    global _number_quality_handler
    _number_quality_handler = handler


@dataclass(frozen=True, slots=True)
class WabaChange:
    waba_id: str
    field: str
    value: dict[str, Any]
    time: str
    occurred_at: datetime

    @property
    def subject(self) -> str:
        """What the change is about, for a dead-letter's id: never a phone number."""
        v = self.value
        return str(v.get("message_template_id") or v.get("message_template_name") or self.field)

    @property
    def canonical(self) -> str:
        return json.dumps(self.value, sort_keys=True, separators=(",", ":"), default=str)


def _occurred_at(raw: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(str(raw)), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.now(UTC)


def extract_changes(payload: dict[str, Any]) -> list[WabaChange]:
    """Every account-level change in the body, each paired with **its own entry's** WABA id.

    Tolerant by construction — never raises on a shape it does not know, because Meta adds fields
    on its schedule and an unfamiliar body is acknowledged, not retried.
    """
    changes: list[WabaChange] = []
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return changes
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        raw_changes = entry.get("changes")
        if not isinstance(raw_changes, list):
            continue
        for change in raw_changes:
            if not isinstance(change, dict) or change.get("field") not in FIELDS:
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            changes.append(
                WabaChange(
                    waba_id=str(entry["id"]),
                    field=str(change["field"]),
                    value=value,
                    time=str(entry.get("time") or ""),
                    occurred_at=_occurred_at(entry.get("time")),
                )
            )
    return changes


async def _fold(organization_id: uuid.UUID, change: WabaChange) -> None:
    did = w2_services.dedupe_id(
        organization_id, change.field, change.waba_id, change.time, change.canonical
    )
    async with session_scope(organization_id=organization_id) as session:
        if await session.get(ProcessedEvent, did) is not None:
            log.info("webhook.redelivered", field=change.field)
            return
        if change.field == TEMPLATE_STATUS:
            found = await w2_services.apply_status_change(
                session,
                organization_id,
                value=change.value,
                occurred_at=change.occurred_at,
            )
            if not found:
                log.info("webhook.template_not_held", field=change.field)
        elif change.field == TEMPLATE_QUALITY:
            found = await w2_services.apply_quality_change(
                session, organization_id, value=change.value
            )
            if not found:
                log.info("webhook.template_not_held", field=change.field)
        elif change.field == NUMBER_QUALITY:
            await _number_quality(session, organization_id, change)
        # Marked processed even when nothing matched: the event has been handled, and re-running
        # it would reach the same conclusion. A template that appears later arrives by sync.
        session.add(ProcessedEvent(event_id=did, event_type=f"whatsapp.{change.field}"))


async def _number_quality(
    session: AsyncSession, organization_id: uuid.UUID, change: WabaChange
) -> None:
    handler = _number_quality_handler
    if handler is None:
        log.info("webhook.number_quality_no_cache", field=change.field)
        return
    await handler(
        session,
        organization_id=organization_id,
        waba_id=change.waba_id,
        display_phone_number=str(change.value.get("display_phone_number") or "") or None,
        event=str(change.value.get("event") or "") or None,
        current_limit=str(change.value.get("current_limit") or "") or None,
    )


async def _dead_letter(
    organization_id: uuid.UUID, change: WabaChange, error: BaseException
) -> bool:
    """Park one permanently-failing change in its own transaction. False if even that failed —
    the caller then treats the request as transient, so nothing is lost twice."""
    try:
        async with session_scope(organization_id=organization_id) as session:
            await services.record_dead_letter(
                session,
                organization_id=organization_id,
                kind="template",
                provider_message_id=f"{change.field}:{change.subject}:{change.time}",
                occurred_at=change.occurred_at,
                error=error,
                raw={"waba_id": change.waba_id, "field": change.field, "value": change.value},
            )
    except Exception as exc:
        log.error("webhook.dead_letter_write_failed", error_type=type(exc).__name__)
        return False
    return True


async def handle_changes(directory: AsyncSession, payload: dict[str, Any]) -> bool:
    """Fold every account-level change in an already signature-verified body.

    Returns True when at least one change hit a transient failure, so the route answers 5xx and
    Meta redelivers; everything that did fold is skipped by `processed_events` the second time.
    """
    changes = extract_changes(payload)
    if not changes:
        return False

    organizations: dict[str, list[uuid.UUID]] = {}
    had_transient = False

    for change in changes:
        if change.waba_id not in organizations:
            organizations[change.waba_id] = await w2_selectors.organizations_for_waba(
                directory, change.waba_id
            )
        targets = organizations[change.waba_id]
        if not targets:
            # Genuine and signed, but a WABA nobody here has connected: not ours to act on.
            log.warning("webhook.unknown_waba", field=change.field)
            continue
        if change.field == ACCOUNT_UPDATE:
            log.info("webhook.account_update", event=str(change.value.get("event") or ""))
            continue

        for organization_id in targets:
            try:
                await _fold(organization_id, change)
            except Exception as exc:
                permanent = services.is_permanent_fold_error(exc)
                exhausted = webhooks.transient_retry_exhausted(change.occurred_at)
                if not permanent and not exhausted:
                    log.error(
                        "webhook.template_fold_transient_error",
                        field=change.field,
                        error_type=type(exc).__name__,
                    )
                    had_transient = True
                    continue
                log.error(
                    "webhook.template_fold_failed",
                    field=change.field,
                    permanent=permanent,
                    error_type=type(exc).__name__,
                )
                if not await _dead_letter(organization_id, change, exc):
                    had_transient = True
    return had_transient
