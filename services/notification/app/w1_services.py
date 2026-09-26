"""Writes and rules for slice W1: start a signup, the numbers cache, health, default number,
test message, disconnect.

What this module never does: return, log or audit a token or a code. The token is opened only
inside `w1_setup` and `numbers_refresh_inputs` for the length of one Graph call. The signup
worker itself lives in `w1_setup`.

**One connection, several numbers.** `provider_accounts` keeps one row per organization (the WABA,
the sealed token, and the *default* number sends go out on — its `phone_number_id` is the
globally unique webhook routing key). `w1_numbers` caches every number on the WABA. Setting the
default mirrors that number into `provider_accounts`, after checking on the cross-tenant directory
reader that no other organization already holds it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors, services, w1_domain, w1_selectors, w2_selectors
from app.config import Settings, graph_version, meta_app_secret, settings
from app.models import AuditLog, OutboxEvent, ProviderAccount
from app.w1_models import ConnectionHealth, SetupRun, WhatsAppNumber
from app.w1_schemas import (
    HealthOut,
    NumberHealthOut,
    NumberOut,
    SignupConfigOut,
    SignupStatusOut,
    SignupStepOut,
    TemplatesHealthOut,
    TokenHealthOut,
    WebhookHealthOut,
)
from platform_core import pii
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ProblemError
from platform_core.events import publish_event
from platform_core.logging import get_logger
from platform_core.pii import mask as mask_pii

log = get_logger("notification.w1_services")

EVT_SETUP_REQUESTED: Final = "notification.whatsapp.setup_requested"

#: The five steps in the order the worker runs them, with the label each shows.
STEPS: Final = (
    ("token", "Connecting to Meta"),
    ("number", "Registering your number"),
    ("webhook", "Subscribing to message events"),
    ("templates", "Syncing message templates"),
    ("verify", "Testing the connection"),
)


def problem(cls: type[ProblemError], detail: str, code: str) -> ProblemError:
    """A problem+json error carrying a machine-readable `errors[0].code`."""
    return cls(detail, errors=[{"code": code}])


# --------------------------------------------------------------------- signup config


def signup_enabled(cfg: Settings) -> bool:
    return bool(cfg.meta_app_id and meta_app_secret(cfg) and cfg.meta_embedded_signup_config_id)


def signup_config(cfg: Settings) -> SignupConfigOut:
    """What the browser needs to open Meta's popup. The app secret is never part of this."""
    enabled = signup_enabled(cfg)
    return SignupConfigOut(
        enabled=enabled,
        app_id=cfg.meta_app_id if enabled else None,
        config_id=cfg.meta_embedded_signup_config_id if enabled else None,
        graph_version=graph_version(cfg),
        reason=None if enabled else "not_configured",
    )


# --------------------------------------------------------------------- signup runs


def initial_steps() -> list[dict[str, Any]]:
    return [{"key": k, "label": label, "state": "waiting", "detail": None} for k, label in STEPS]


def run_out(run: SetupRun, now: datetime | None = None) -> SignupStatusOut:
    """The run as the status route shows it. A run still `running` long past any real setup (its
    event was lost, or the worker is down) is reported `failed` with `timed_out` rather than as
    an endless spinner; a read writes nothing, and the run is superseded by the next
    `signup/complete`."""
    now = now or datetime.now(UTC)
    status, error_code = run.status, run.error_code
    if status == "running" and now - run.created_at > w1_domain.SETUP_STALE_AFTER:
        status, error_code = "failed", "timed_out"
    return SignupStatusOut(
        setup_id=run.id,
        status=status,
        steps=[SignupStepOut(**step) for step in run.steps],
        error_code=error_code,
    )


async def start_signup(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code: str,
    waba_id: str | None,
    phone_number_id: str | None,
    request: Request | None = None,
) -> SetupRun:
    """Persist one setup run and stage the event that starts it, in one transaction.

    The worker only ever sees the run after this commits (the event goes through the outbox), so
    no Graph call happens inside the request. Idempotent on the code: a client that retries the
    same `signup/complete` (a dropped response) gets the run it already started, never a second
    one, and never a second exchange of a single-use code.
    """
    cfg = settings()
    if not signup_enabled(cfg):
        raise problem(
            ConflictProblem, "WhatsApp signup is not configured on this platform.", "not_configured"
        )
    org = ctx.organization_id
    code_hash = pii.blind_index(code, kind="generic", settings=cfg)

    existing = await w1_selectors.run_by_code_hash(session, org, code_hash)
    if existing is not None:
        return existing

    now = datetime.now(UTC)
    for other in await w1_selectors.running_runs(session, org):
        if now - other.created_at < w1_domain.SETUP_STALE_AFTER:
            raise problem(
                ConflictProblem,
                "A WhatsApp setup is already running for this organization.",
                "setup_in_progress",
            )
        _fail(other, "abandoned", now)

    sealed, _ = pii.seal(code, kind="generic", settings=cfg)
    run = SetupRun(
        organization_id=org,
        status="running",
        steps=initial_steps(),
        code_enc=sealed,
        code_hash=code_hash,
        waba_hint=waba_id,
        phone_number_hint=phone_number_id,
        started_by=str(ctx.principal.sub),
    )
    try:
        async with session.begin_nested():
            session.add(run)
            await session.flush()
    except IntegrityError:
        # A retry that raced this one past the read above: hand back the winner.
        winner = await w1_selectors.run_by_code_hash(session, org, code_hash)
        if winner is None:
            raise
        return winner

    record_audit(
        session,
        AuditLog,
        organization_id=org,
        actor_id=ctx.principal.sub,
        action="engage.whatsapp.signup_started",
        entity="w1_setup_run",
        entity_id=run.id,
        # Ids only: never the code, never a token.
        after={"setup_id": str(run.id)},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org,
        event_type=EVT_SETUP_REQUESTED,
        aggregate_type="w1_setup_run",
        aggregate_id=run.id,
        payload={"organization_id": str(org), "setup_id": str(run.id)},
        actor=services._actor(ctx),
    )
    return run


def _fail(run: SetupRun, code: str, now: datetime) -> None:
    run.status = "failed"
    run.error_code = code
    run.finished_at = now
    run.code_enc = None
    run.token_enc = None


async def get_run_or_404(session: AsyncSession, ctx: ScopeContext, setup_id: uuid.UUID) -> SetupRun:
    run = await w1_selectors.get_run(session, ctx.organization_id, setup_id)
    if run is None:
        raise NotFoundProblem("No such setup.")
    return run


# --------------------------------------------------------------------- numbers cache


def number_out(row: WhatsAppNumber) -> NumberOut:
    return NumberOut(
        id=row.id,
        phone_number_id=row.phone_number_id,
        display_phone_number=mask_pii(row.display_phone_number, kind="phone")
        if row.display_phone_number
        else "",
        verified_name=row.verified_name,
        name_status=row.name_status,
        quality_rating=row.quality_rating,
        messaging_limit=row.messaging_limit,
        status=row.status,
        is_default=row.is_default,
    )


async def apply_numbers(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    waba_id: str,
    remote: list[dict[str, Any]],
    default_phone_number_id: str | None,
    mark_missing: bool = True,
    now: datetime | None = None,
) -> list[WhatsAppNumber]:
    """Fold Meta's list of the WABA's numbers into the cache: one read, one flush.

    A number Meta no longer lists is kept and marked `disconnected` rather than deleted, so a
    default that vanished is visible instead of silently re-pointed. The default flag is set from
    `default_phone_number_id` (the number `provider_accounts` sends on); it is never invented.
    `mark_missing=False` is for a partial list (one number read back), which says nothing about
    the others.
    """
    now = now or datetime.now(UTC)
    rows = {r.phone_number_id: r for r in await w1_selectors.numbers(session, organization_id)}
    seen: set[str] = set()
    for item in remote:
        pnid = str(item.get("id") or "")
        if not pnid or len(pnid) > 80:
            continue
        seen.add(pnid)
        row = rows.get(pnid)
        if row is None:
            row = WhatsAppNumber(organization_id=organization_id, phone_number_id=pnid)
            session.add(row)
            rows[pnid] = row
        row.waba_id = waba_id
        row.display_phone_number = str(item.get("display_phone_number") or "")[:40]
        verified = str(item.get("verified_name") or "").strip()
        row.verified_name = verified[:200] or None
        row.name_status = w1_domain.name_status_from_meta(item.get("name_status"))
        row.quality_rating = w1_domain.quality_from_meta(item.get("quality_rating"))
        row.messaging_limit = w1_domain.limit_from_meta(item.get("messaging_limit_tier"))
        row.status = w1_domain.status_from_meta(item.get("status"))
        row.refreshed_at = now
    for pnid, row in rows.items():
        if mark_missing and pnid not in seen and row.waba_id == waba_id:
            row.status = "disconnected"
            row.refreshed_at = now
    if default_phone_number_id is not None:
        for pnid, row in rows.items():
            row.is_default = pnid == default_phone_number_id
    await session.flush()
    return sorted(rows.values(), key=lambda r: (not r.is_default, r.display_phone_number, r.id))


async def record_token_check(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    state: str,
    expires_at: datetime | None = None,
    setup_id: uuid.UUID | None = None,
    subscribed: bool = False,
    reset_expiry: bool = False,
) -> ConnectionHealth:
    """Remember what Meta last said about the token (and, from a setup, the subscription).
    The expiry is kept as recorded unless a new one is given or `reset_expiry` says the token
    is a new one (a signup: `None` then means "does not expire")."""
    now = datetime.now(UTC)
    health = await w1_selectors.get_health(session, organization_id)
    if health is None:
        health = ConnectionHealth(organization_id=organization_id)
        health.token_state = "unknown"  # noqa: S105 - a state word, not a credential
        session.add(health)
    health.token_state = state
    health.token_checked_at = now
    if expires_at is not None or reset_expiry:
        health.token_expires_at = expires_at
    if subscribed:
        health.webhook_subscribed_at = now
    if setup_id is not None:
        health.last_setup_id = setup_id
    await session.flush()
    return health


def numbers_stale(rows: list[WhatsAppNumber], now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    if not rows:
        return True
    return any(r.refreshed_at is None or now - r.refreshed_at > w1_domain.NUMBERS_TTL for r in rows)


# --------------------------------------------------------------------- default number


async def set_default_number(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    directory: AsyncSession,
    number_id: uuid.UUID,
    request: Request | None = None,
) -> None:
    """Make one cached number the one sends go out on.

    Mirrors it into `provider_accounts` (the routing row), so the number's global uniqueness is
    checked on `directory`, the cross-tenant reader — the tenant session cannot see another
    organization's row — with the unique constraint as the net for the race after that check.
    """
    org = ctx.organization_id
    number = await w1_selectors.get_number(session, org, number_id)
    if number is None:
        raise NotFoundProblem("No such number.")
    account = await selectors.get_whatsapp_connection(session, org)
    if account is None or account.status != "connected":
        raise problem(ConflictProblem, "Connect WhatsApp first.", "channel_not_connected")
    if number.status == "disconnected":
        raise problem(
            ConflictProblem, "That number is not active on the WhatsApp account.", "number_inactive"
        )
    if number.is_default and account.phone_number_id == number.phone_number_id:
        return

    claimed = await selectors.account_for_phone_number(directory, number.phone_number_id)
    if claimed is not None and claimed.organization_id != org:
        raise problem(
            ConflictProblem,
            "This WhatsApp phone number is already connected elsewhere.",
            "number_claimed",
        )

    before = {"phone_number_id": account.phone_number_id}
    account.phone_number_id = number.phone_number_id
    account.display_number = number.display_phone_number
    account.updated_by = str(ctx.principal.sub)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise problem(
            ConflictProblem,
            "This WhatsApp phone number is already connected elsewhere.",
            "number_claimed",
        ) from exc
    for row in await w1_selectors.numbers(session, org):
        row.is_default = row.id == number.id
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org,
        actor_id=ctx.principal.sub,
        action="engage.whatsapp.default_number_changed",
        entity="provider_account",
        entity_id=account.id,
        before=before,
        after={"phone_number_id": number.phone_number_id},
        request=request,
    )


# --------------------------------------------------------------------- health


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


async def health_snapshot(session: AsyncSession, organization_id: uuid.UUID) -> HealthOut:
    """Everything the health card shows, from our own tables: it never calls Meta, so it is cheap
    enough to poll. Token and subscription state are what the last setup or refresh learned."""
    account = await selectors.get_whatsapp_connection(session, organization_id)
    connected = bool(
        account is not None
        and account.organization_id == organization_id
        and account.status == "connected"
        and account.access_token_enc
    )
    health = await w1_selectors.get_health(session, organization_id)
    count, synced_at = await w2_selectors.template_summary(session, organization_id)
    last_event = await w1_selectors.last_webhook_event_at(session, organization_id)

    stored = health.token_state if (health is not None and connected) else "unknown"
    token_state = w1_domain.token_state(
        stored, expires_at=health.token_expires_at if health is not None else None
    )
    subscribed_at = health.webhook_subscribed_at if (health is not None and connected) else None
    webhook = w1_domain.webhook_state(
        subscribed_at=subscribed_at, last_event_at=last_event if connected else None
    )

    number: NumberHealthOut | None = None
    if connected and account is not None:
        for row in await w1_selectors.numbers(session, organization_id):
            if row.phone_number_id == account.phone_number_id:
                number = NumberHealthOut(
                    quality_rating=row.quality_rating, messaging_limit=row.messaging_limit
                )
                break
    return HealthOut(
        connected=connected,
        token=TokenHealthOut(
            state=token_state,
            checked_at=_iso(health.token_checked_at) if health is not None else None,
        ),
        webhook=WebhookHealthOut(
            state=webhook,
            last_event_at=_iso(last_event) if connected else None,
        ),
        templates=TemplatesHealthOut(count=count, synced_at=_iso(synced_at)),
        number=number,
    )


# --------------------------------------------------------------------- disconnect


async def disconnect(
    session: AsyncSession, ctx: ScopeContext, *, request: Request | None = None
) -> None:
    """Mark the connection disconnected and wipe the sealed token. History (templates, messages,
    conversations, the numbers cache) is kept. The row keeps its number, so an inbound message
    still routes here and is kept, exactly as `ProviderAccount` documents for a disconnected
    account; nothing is sent from it."""
    org = ctx.organization_id
    account = await selectors.get_whatsapp_connection(session, org)
    if account is None or account.organization_id != org or account.status != "connected":
        raise problem(ConflictProblem, "WhatsApp is not connected.", "channel_not_connected")
    account.status = "disconnected"
    account.access_token_enc = None
    account.updated_by = str(ctx.principal.sub)
    for row in await w1_selectors.numbers(session, org):
        row.status = "disconnected"
    health = await w1_selectors.get_health(session, org)
    if health is not None:
        health.token_state = "unknown"  # noqa: S105 - a state word, not a credential
        health.token_expires_at = None
        health.webhook_subscribed_at = None
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=org,
        actor_id=ctx.principal.sub,
        action="engage.whatsapp.disconnected",
        entity="provider_account",
        entity_id=account.id,
        after={"waba_id": account.waba_id, "phone_number_id": account.phone_number_id},
        request=request,
    )


# --------------------------------------------------------------------- test message


async def stage_test_message(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    to: str,
    template_name: str,
    language: str,
) -> uuid.UUID:
    """Stage one template message to `to` exactly the way any send is staged: the consent decision
    (`consent.check_send`, inside `services.stage_message`) first, then the dispatcher sends it on
    this organization's own connection through the tenant transport. Nothing is sent from here.

    Refused with `channel_not_connected` when there is no connected account, and with
    `consent_denied` when the consent ledger does not allow this send."""
    org = ctx.organization_id
    account = await selectors.get_whatsapp_connection(session, org)
    if (
        account is None
        or account.organization_id != org
        or account.status != "connected"
        or not account.access_token_enc
    ):
        raise problem(
            ConflictProblem, "Connect a WhatsApp Business account first.", "channel_not_connected"
        )

    row = await w1_selectors.provider_template(session, org, template_name, language)
    body = (row.preview_text if row is not None and row.preview_text else "") or (
        f"Test message using template {template_name} ({language})"
    )
    template: dict[str, Any] = {"name": template_name, "language": {"code": language}}
    examples = _body_examples(row)
    if examples:
        template["components"] = [
            {"type": "body", "parameters": [{"type": "text", "text": v} for v in examples]}
        ]

    message = await services.stage_message(
        session,
        organization_id=org,
        channel="whatsapp",
        category="operational",
        to_address=to,
        subject=None,
        body=body,
        created_by=str(ctx.principal.sub),
        rule_code="w1.test_message",
        dedupe_key=f"w1-test:{uuid.uuid4()}",
        actor=services._actor(ctx),
        provider_template_payload=template,
    )
    if message is None:  # pragma: no cover - a fresh uuid dedupe key never collides
        raise problem(ConflictProblem, "The test message was not queued.", "not_queued")
    if message.state == "suppressed":
        raise problem(
            ConflictProblem,
            "This recipient has not agreed to receive WhatsApp messages from you.",
            "consent_denied",
        )
    return message.id


def _body_examples(row: Any) -> list[str]:
    """The example value for each body placeholder of a synced template, in order, so a test send
    of a template with variables carries something Meta will accept."""
    if row is None:
        return []
    specs = [s for s in (row.variable_specs or []) if isinstance(s, dict)]
    ordered = sorted(specs, key=lambda s: int(s.get("index") or 0))
    return [str(s.get("example") or "Test") for s in ordered]


# --------------------------------------------------------------------- number quality webhook


async def update_number_quality(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    waba_id: str,
    display_phone_number: str | None,
    event: str | None,
    current_limit: str | None,
) -> None:
    """Fold one `phone_number_quality_update` into the numbers cache (registered with
    `w2_webhooks.register_number_quality_handler`).

    The change names the number by its display string. A messaging-tier change is recorded as
    Meta sent it; the *quality rating* is not guessed from the event name — the row's freshness is
    cleared instead, so the next read pulls the authoritative rating from Graph. A change for a
    number this organization does not hold is ignored."""
    target = w1_domain.digits(display_phone_number)
    if not target:
        return
    stmt = select(WhatsAppNumber).where(
        WhatsAppNumber.organization_id == organization_id,
        WhatsAppNumber.waba_id == waba_id,
    )
    rows = (await session.execute(stmt)).scalars().all()
    for row in rows:
        if w1_domain.digits(row.display_phone_number) != target:
            continue
        if current_limit:
            limit = w1_domain.limit_from_meta(current_limit)
            if limit != "unknown":
                row.messaging_limit = limit
        row.refreshed_at = None
        log.info("w1.number_quality_update", change=event or "", limit=row.messaging_limit)
    await session.flush()


def _account_token(account: ProviderAccount) -> str | None:
    """The opened token of a connected account, or None. Local to this module's Graph callers."""
    if account.status != "connected" or not account.access_token_enc:
        return None
    try:
        return pii.open_sealed(account.access_token_enc, settings=settings())
    except pii.PiiError:
        return None


async def numbers_refresh_inputs(
    session: AsyncSession, organization_id: uuid.UUID
) -> tuple[str, str, str] | None:
    """`(waba_id, token, default_phone_number_id)` for a refresh from Graph, or None when there
    is no usable connection. Read in one short transaction; the caller calls Graph after it."""
    account = await selectors.get_whatsapp_connection(session, organization_id)
    if account is None or account.organization_id != organization_id:
        return None
    token = _account_token(account)
    if token is None:
        return None
    return account.waba_id, token, account.phone_number_id
