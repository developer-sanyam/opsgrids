"""Writes for WhatsApp templates: sync from Meta, create and submit, fold a status webhook.

Three rules run through it.

**The credential is the organization's own.** Every Graph call uses the token sealed on that
organization's `provider_accounts` row, for that row's WABA. There is no platform-token fallback:
one tenant's templates are never read or written with another's authority.

**No transaction is held across Meta.** `sync_templates` (the entry point another slice calls with
its own session) is the one exception, and says so; the routes split the work into
`load_credentials` -> Graph -> `apply_sync` / `record_created`, each a short transaction of its own.

**Meta is the authority on a template's state.** A sync overwrites what we hold with what Meta
says, and removes what Meta no longer has. A create is recorded as `pending` and left for Meta's
webhook to move — never `approved` on our say-so.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors, w2_domain, w2_graph, w2_selectors
from app.config import settings
from app.models import AuditLog, ProviderTemplate
from platform_core import pii
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, ProblemError, ValidationProblem
from platform_core.logging import get_logger

log = get_logger("notification.w2_services")

SYSTEM_ACTOR: Final = "system:notification"

#: A webhook older than what a sync already saw, by more than this, is stale.
_STALE_SLACK: Final = timedelta(seconds=60)


class ChannelNotConnected(ProblemError):
    """No usable WhatsApp connection: nothing to ask Meta with."""

    status_code = 409
    problem_type = "channel-not-connected"
    title = "WhatsApp is not connected"

    def __init__(self) -> None:
        super().__init__(
            "Connect a WhatsApp Business account first.",
            errors=[{"code": "channel_not_connected"}],
        )


class MetaRefused(ProblemError):
    """Meta answered with an error, or could not be reached."""

    status_code = 502
    problem_type = "upstream-error"
    title = "Meta did not accept the request"

    def __init__(self, error: w2_graph.GraphError) -> None:
        super().__init__(
            error.public_message,
            errors=[{"code": "meta_unreachable" if error.retryable else "meta_refused"}],
        )


@dataclass(frozen=True, slots=True)
class Credentials:
    waba_id: str
    access_token: str = ""

    def __repr__(self) -> str:  # never let a log line or a traceback print the token
        return f"Credentials(waba_id={self.waba_id!r})"


@dataclass(frozen=True, slots=True)
class SyncResult:
    synced: int
    created: int
    updated: int
    removed: int


async def load_credentials(session: AsyncSession, organization_id: uuid.UUID) -> Credentials:
    """This organization's WABA and opened token, or `ChannelNotConnected`.

    Reads through the tenant-bound session and re-checks the row's own `organization_id`: RLS is
    the net, not permission to skip the check.
    """
    account = await selectors.get_whatsapp_connection(session, organization_id)
    if (
        account is None
        or account.organization_id != organization_id
        or account.status != "connected"
        or not account.access_token_enc
    ):
        raise ChannelNotConnected
    try:
        token = pii.open_sealed(account.access_token_enc, settings=settings())
    except pii.PiiError as exc:
        log.error("w2.token_unreadable", organization_id=str(organization_id))
        raise ChannelNotConnected from exc
    return Credentials(waba_id=account.waba_id, access_token=token)


def _graph(
    creds: Credentials, transport: httpx.AsyncBaseTransport | None
) -> w2_graph.TemplateGraph:
    return w2_graph.TemplateGraph(
        settings(), waba_id=creds.waba_id, access_token=creds.access_token, transport=transport
    )


async def fetch_remote(
    creds: Credentials, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[dict[str, Any]]:
    try:
        return await _graph(creds, transport).list_templates()
    except w2_graph.GraphError as exc:
        raise MetaRefused(exc) from exc


# --------------------------------------------------------------------- sync


async def sync_templates(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SyncResult:
    """Pull every template from Meta into this organization's rows. Importable by other slices.

    `session` must already be bound to `organization_id`. Unlike the route, this holds that
    session's transaction across the Graph call — acceptable for a background caller (signup's
    setup job), which is why it takes a session at all. Raises `ChannelNotConnected` when there
    is no connection and `MetaRefused` when Meta answers with an error; both are `ProblemError`s.
    """
    creds = await load_credentials(session, organization_id)
    remote = await fetch_remote(creds, transport=transport)
    return await apply_sync(session, organization_id, remote)


def _from_meta(item: dict[str, Any]) -> dict[str, Any] | None:
    """The fields of one Meta template we keep, or None for one too malformed to store."""
    meta_id = str(item.get("id") or "")
    name = str(item.get("name") or "")
    language = str(item.get("language") or "")
    if not (meta_id and name and language) or len(name) > 200 or len(language) > 20:
        return None
    components = [c for c in item.get("components") or [] if isinstance(c, dict)]
    extracted = w2_domain.extract(components)
    status = w2_domain.status_from_meta(item.get("status"))
    reason = str(item.get("rejected_reason") or "")
    return {
        "meta_id": meta_id,
        "name": name,
        "language": language,
        "status": status,
        "category": w2_domain.category_from_meta(item.get("category")),
        # None when Meta's answer carries no rating at all, so a sync does not erase the one a
        # quality webhook set.
        "quality": w2_domain.quality_from_meta(item.get("quality_score"))
        if "quality_score" in item
        else None,
        "reason": reason if reason and reason.upper() != "NONE" else None,
        "parameter_format": "named"
        if str(item.get("parameter_format") or "").upper() == "NAMED"
        else "positional",
        "components": components,
        "extracted": extracted,
    }


async def apply_sync(
    session: AsyncSession,
    organization_id: uuid.UUID,
    remote: list[dict[str, Any]],
    *,
    actor_id: uuid.UUID | None = None,
    request: Request | None = None,
) -> SyncResult:
    """Fold Meta's full template list into the organization's rows, in one transaction.

    One read of the organization's rows and one flush — never a query per template. `remote` must
    be **every** template Meta holds (`TemplateGraph.list_templates` returns all pages or raises),
    because a synced row missing from it is treated as deleted at Meta.
    """
    # Two syncs racing (a double click, signup's job beside an operator) would both see "no such
    # row" and both insert; the second would fail on the unique code. Serialise per organization.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"w2.sync:{organization_id}"}
    )

    now = datetime.now(UTC)
    who = str(actor_id) if actor_id else SYSTEM_ACTOR
    rows = await w2_selectors.all_whatsapp_rows(session, organization_id)
    by_meta = {r.provider_template_id: r for r in rows if r.provider_template_id}
    by_name = {(r.provider_name, r.language): r for r in rows}
    taken = {r.code for r in rows}

    created = updated = synced = 0
    touched: set[uuid.UUID] = set()
    seen_meta_ids: set[str] = set()

    for item in remote:
        parsed = _from_meta(item)
        if parsed is None:
            continue
        synced += 1
        meta_id: str = parsed["meta_id"]
        seen_meta_ids.add(meta_id)
        extracted: w2_domain.Extracted = parsed["extracted"]
        row = by_meta.get(meta_id) or by_name.get((parsed["name"], parsed["language"]))

        if row is None:
            status = parsed["status"] or "pending"
            code = w2_domain.unique_code(parsed["name"], parsed["language"], taken)
            taken.add(code)
            row = ProviderTemplate(
                organization_id=organization_id,
                created_by=who,
                code=code,
                channel=w2_selectors.CHANNEL,
                provider_name=parsed["name"],
                provider_template_id=meta_id,
                language=parsed["language"],
                category=parsed["category"] or "utility",
                parameter_format=parsed["parameter_format"],
                status=status,
                status_reason=parsed["reason"] if status == "rejected" else None,
                status_checked_at=now,
                variables=extracted.mapping,
                preview_text=extracted.body_preview,
                quality=parsed["quality"] or "unknown",
                components=parsed["components"],
                variable_specs=extracted.specs,
                history=[w2_domain.history_entry(status, "sync", at=now)],
            )
            session.add(row)
            created += 1
            continue

        before = (
            row.status,
            row.category,
            row.quality,
            row.status_reason,
            row.provider_template_id,
            row.components,
            row.deleted_at,
        )
        resurrected = row.deleted_at is not None
        row.deleted_at = None
        row.provider_template_id = meta_id
        if parsed["status"] is not None and parsed["status"] != row.status:
            row.history = w2_domain.appended_history(row.history, parsed["status"], "sync", at=now)
            row.status = parsed["status"]
        if parsed["category"]:
            row.category = parsed["category"]
        if parsed["quality"] is not None:
            row.quality = parsed["quality"]
        row.status_reason = parsed["reason"] if row.status == "rejected" else None
        row.parameter_format = parsed["parameter_format"]
        row.components = parsed["components"]
        row.preview_text = extracted.body_preview
        row.variable_specs = extracted.specs
        # The send path's mapping is the operator's to shape; Meta only has a say when the
        # placeholders themselves moved.
        if not w2_domain.same_slots(list(row.variables or []), extracted.mapping):
            row.variables = extracted.mapping
        row.status_checked_at = now
        row.updated_by = who
        touched.add(row.id)
        after = (
            row.status,
            row.category,
            row.quality,
            row.status_reason,
            row.provider_template_id,
            row.components,
            row.deleted_at,
        )
        if resurrected:
            created += 1
        elif before != after:
            updated += 1

    removed = 0
    for row in rows:
        if (
            row.deleted_at is None
            and row.provider_template_id
            and row.provider_template_id not in seen_meta_ids
        ):
            row.deleted_at = now
            row.updated_by = who
            removed += 1

    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=actor_id,
        action="provider_template.synced",
        entity="provider_template",
        after={"synced": synced, "created": created, "updated": updated, "removed": removed},
        request=request,
    )
    return SyncResult(synced=synced, created=created, updated=updated, removed=removed)


# --------------------------------------------------------------------- create + submit


async def check_can_create(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    name: str,
    category: str,
    language: str,
    components: dict[str, Any],
) -> Credentials:
    """Everything that can be refused before Meta is asked: local validation (all problems at
    once, per field), a template of this name and language already held, and no connection."""
    problems = w2_domain.validate_create(
        name=name, category=category, language=language, components=components
    )
    if problems:
        raise ValidationProblem("This template cannot be submitted as written.", errors=problems)

    rows = await w2_selectors.all_whatsapp_rows(session, ctx.organization_id)
    if any(
        r.provider_name == name and r.language == language and r.deleted_at is None for r in rows
    ):
        raise ConflictProblem(
            f"A {language} template named {name!r} already exists.",
            errors=[{"code": "template_exists", "field": "name"}],
        )
    return await load_credentials(session, ctx.organization_id)


async def submit_to_meta(
    creds: Credentials,
    *,
    name: str,
    category: str,
    language: str,
    components: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    try:
        return await _graph(creds, transport).create_template(
            name=name,
            language=language,
            category=category,
            components=w2_domain.to_meta_components(components),
        )
    except w2_graph.GraphError as exc:
        raise MetaRefused(exc) from exc


async def record_created(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    name: str,
    category: str,
    language: str,
    components: dict[str, Any],
    meta_response: dict[str, Any],
    request: Request | None = None,
) -> ProviderTemplate:
    """Store what Meta accepted, as `pending`. The status only ever moves on Meta's word."""
    now = datetime.now(UTC)
    meta_components = w2_domain.to_meta_components(components)
    extracted = w2_domain.extract(meta_components)
    status = w2_domain.status_from_meta(meta_response.get("status")) or "pending"
    rows = await w2_selectors.all_whatsapp_rows(session, ctx.organization_id)
    code = w2_domain.unique_code(name, language, {r.code for r in rows})

    row = ProviderTemplate(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        code=code,
        channel=w2_selectors.CHANNEL,
        provider_name=name,
        provider_template_id=str(meta_response["id"]),
        language=language,
        # Meta may re-categorise on submission; what it answers is what it will bill as.
        category=w2_domain.category_from_meta(meta_response.get("category")) or category,
        parameter_format="positional",
        status=status,
        status_reason=None,
        status_checked_at=now,
        variables=extracted.mapping,
        preview_text=extracted.body_preview,
        quality="unknown",
        components=meta_components,
        variable_specs=extracted.specs,
        submitted_at=now,
        history=[w2_domain.history_entry(status, "submit", at=now)],
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Meta accepted it, but a concurrent request stored the same code first. The next sync
        # reconciles the two; the caller is told it already exists.
        raise ConflictProblem(
            f"A {language} template named {name!r} already exists.",
            errors=[{"code": "template_exists", "field": "name"}],
        ) from exc
    await session.refresh(row)
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="provider_template.submitted",
        entity="provider_template",
        entity_id=row.id,
        after={"code": code, "name": name, "language": language, "category": row.category},
        request=request,
    )
    return row


# --------------------------------------------------------------------- webhooks


def dedupe_id(
    organization_id: uuid.UUID, field: str, waba_id: str, time: str, value: str
) -> uuid.UUID:
    """The `processed_events` id for one template webhook change **as delivered to one
    organization**: the same body redelivered derives the same id, and two organizations sharing a
    WABA each get their own, so one's ledger row never hides the event from the other."""
    digest = hashlib.sha256(f"{organization_id}|{field}|{waba_id}|{time}|{value}".encode()).digest()
    return uuid.UUID(bytes=digest[:16], version=5)


async def apply_status_change(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    value: dict[str, Any],
    occurred_at: datetime,
) -> bool:
    """Fold one `message_template_status_update` into this organization's row. False when the
    organization holds no such template (created straight in Meta's console — the next sync brings
    it in)."""
    row = await w2_selectors.find_for_change(
        session,
        organization_id,
        meta_template_id=str(value.get("message_template_id") or "") or None,
        name=str(value.get("message_template_name") or "") or None,
        language=str(value.get("message_template_language") or "") or None,
    )
    if row is None or row.organization_id != organization_id:
        return False

    event = str(value.get("event") or "").upper()
    new_status = w2_domain.status_from_meta(event)
    if event == w2_domain.UNARCHIVE_EVENT:
        # "Restored to its previous status": the status before the archive, from our own history.
        new_status = _status_before_archive(row.history)
    if row.provider_template_id is None and value.get("message_template_id"):
        row.provider_template_id = str(value["message_template_id"])
    if new_status is None:
        log.info("w2.template_status_no_change", event=event)
        return True
    if row.status_checked_at and occurred_at + _STALE_SLACK < row.status_checked_at:
        log.info("w2.template_status_stale", organization_id=str(organization_id))
        return True

    reason = str(value.get("reason") or "")
    if reason.upper() == "NONE":
        reason = ""
    if not reason and event in (w2_domain.ARCHIVE_EVENT, "DELETED"):
        reason = event
    archiving = event == w2_domain.ARCHIVE_EVENT
    if new_status != row.status or archiving:
        # An archive is always recorded, even over an already-disabled template, so that
        # `UNARCHIVED` can find the status the template held before it.
        row.history = w2_domain.appended_history(
            row.history,
            new_status,
            w2_domain.ARCHIVE_SOURCE if archiving else "meta_webhook",
            at=occurred_at,
        )
        row.status = new_status
    row.status_reason = (
        reason if new_status in ("rejected", "paused", "disabled") and reason else None
    )
    row.status_checked_at = max(occurred_at, row.status_checked_at or occurred_at)
    row.updated_by = SYSTEM_ACTOR
    await session.flush()
    return True


def _status_before_archive(history: list[dict[str, Any]]) -> str | None:
    """The status held just before the most recent archive, or None if history has no archive."""
    for index in range(len(history) - 1, -1, -1):
        if history[index].get("source") == w2_domain.ARCHIVE_SOURCE:
            before = history[index - 1].get("status") if index > 0 else None
            return str(before) if before else None
    return None


async def apply_category_change(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    value: dict[str, Any],
) -> bool:
    """Fold one `template_category_update` into this organization's row: the template's category is
    what `consent.purpose_for_send` reads, so a UTILITY template Meta has reclassified as MARKETING
    stops being sent as transactional the moment this commits. False when the organization holds
    no such template. See `w2_domain.category_after_update` for the two payload shapes."""
    row = await w2_selectors.find_for_change(
        session,
        organization_id,
        meta_template_id=str(value.get("message_template_id") or "") or None,
        name=str(value.get("message_template_name") or "") or None,
        language=str(value.get("message_template_language") or "") or None,
    )
    if row is None or row.organization_id != organization_id:
        return False
    category = w2_domain.category_after_update(value)
    if category is None or category == row.category:
        return True
    log.info(
        "w2.template_category_changed",
        organization_id=str(organization_id),
        previous=row.category,
        new=category,
    )
    row.category = category
    row.updated_by = SYSTEM_ACTOR
    await session.flush()
    return True


async def apply_quality_change(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    value: dict[str, Any],
) -> bool:
    row = await w2_selectors.find_for_change(
        session,
        organization_id,
        meta_template_id=str(value.get("message_template_id") or "") or None,
        name=str(value.get("message_template_name") or "") or None,
        language=str(value.get("message_template_language") or "") or None,
    )
    if row is None or row.organization_id != organization_id:
        return False
    row.quality = w2_domain.quality_from_meta(value.get("new_quality_score"))
    row.updated_by = SYSTEM_ACTOR
    await session.flush()
    return True
