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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors, services, w1_domain, w1_selectors, w2_selectors, whatsapp_provider
from app.config import Settings, graph_version, meta_app_secret, settings
from app.models import AuditLog, OutboxEvent, ProviderAccount
from app.w1_graph import CodeExpiredError, GraphError, MetaGraph
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
from platform_core.errors import (
    ConflictProblem,
    NotFoundProblem,
    ProblemError,
    UpstreamUnavailableProblem,
)
from platform_core.events import publish_event
from platform_core.logging import get_logger
from platform_core.pii import mask as mask_pii

log = get_logger("notification.w1_services")

EVT_SETUP_REQUESTED: Final = "notification.whatsapp.setup_requested"

#: The five steps in the order the worker runs them, with the label each shows.
STEPS: Final = (
    ("token", "Connecting to Meta"),
    ("number", "Linking your number"),
    ("webhook", "Subscribing to message events"),
    ("register", "Registering your number"),
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


async def _noop_bind() -> None:
    return None


async def start_signup(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code: str,
    waba_id: str | None,
    phone_number_id: str | None,
    request: Request | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    bind: Callable[[], Awaitable[None]] = _noop_bind,
) -> uuid.UUID:
    """Persist one setup run, **exchange the popup's code for a token now**, and stage the event
    that runs the rest. Returns the run's id.

    Meta's code lives about thirty seconds, so the exchange cannot wait behind the outbox and the
    relay: it happens inside this request, between two short transactions of its own (the same
    idiom as `GET numbers`, so a slow Meta never pins a pooled connection). `session` therefore
    must not be inside a transaction when this is called; `bind` re-binds the tenant at the start
    of each one (the GUC is transaction-local).

    1. *Reserve.* One transaction records the run (`running`, the code never stored, only its
       blind index). The unique index on `(organization_id, code_hash)` and the one-running-per-
       organization index arbitrate two requests that race, so a code is exchanged by exactly one
       of them.
    2. *Exchange*, outside any transaction. A refused code fails the run with a specific code
       (`code_expired`, `code_exchange_failed`, `meta_unreachable`) and answers with that code
       in the problem body; the token, if any, is sealed straight away.
    3. *Hand over.* One transaction stores the sealed token on the run, marks the token step done
       and stages `setup_requested`: the worker only ever sees a run that already holds a token,
       and only after this commits.

    Idempotent on the code: a repeat (a dropped response, a double click) returns the run it
    already started and never exchanges a spent code again. A Meta outage during the exchange
    deletes the reservation, so the same code can be tried again; the code may or may not be spent
    at Meta then, which is Meta's to say on the retry.
    """
    cfg = settings()
    if not signup_enabled(cfg):
        raise problem(
            ConflictProblem, "WhatsApp signup is not configured on this platform.", "not_configured"
        )
    org = ctx.organization_id
    code_hash = pii.blind_index(code, kind="generic", settings=cfg)

    async with session.begin():
        await bind()
        reserved = await _reserve_run(
            session,
            ctx,
            code_hash=code_hash,
            waba_id=waba_id,
            phone_number_id=phone_number_id,
            request=request,
        )
    run_id, fresh = reserved
    if not fresh:
        return run_id

    try:
        token = await MetaGraph(cfg, transport=transport).exchange_code(code)
    except GraphError as exc:
        expired = isinstance(exc, CodeExpiredError)
        retryable = exc.retryable and not expired
        failure_code = (
            "code_expired"
            if expired
            else "meta_unreachable"
            if retryable
            else "code_exchange_failed"
        )
        async with session.begin():
            await bind()
            run = await w1_selectors.get_run(session, org, run_id, for_update=True)
            if run is not None and run.status == "running":
                if retryable:
                    await session.delete(run)
                else:
                    _fail(run, failure_code, datetime.now(UTC))
                    _set_step(run, "token", "failed", exc.public_message)
        raise problem(
            UpstreamUnavailableProblem if retryable else ConflictProblem,
            exc.public_message,
            failure_code,
        ) from exc

    sealed, _ = pii.seal(token, kind="generic", settings=cfg)
    async with session.begin():
        await bind()
        run = await w1_selectors.get_run(session, org, run_id, for_update=True)
        if run is None or run.status != "running":
            # Superseded while Meta was being asked (the stale-run sweep of a newer signup): the
            # token is dropped with this frame, and the newer run is the one that counts.
            raise problem(
                ConflictProblem,
                "This WhatsApp setup was replaced by a newer one.",
                "setup_in_progress",
            )
        run.token_enc = sealed
        _set_step(run, "token", "done", "Connected to Meta")
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
    return run_id


async def _reserve_run(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code_hash: str,
    waba_id: str | None,
    phone_number_id: str | None,
    request: Request | None,
) -> tuple[uuid.UUID, bool]:
    """Step 1 of `start_signup`, inside its transaction: `(run id, created)`. `created` is False
    when this code already started a run."""
    org = ctx.organization_id
    existing = await w1_selectors.run_by_code_hash(session, org, code_hash)
    if existing is not None:
        return existing.id, False

    now = datetime.now(UTC)
    for other in await w1_selectors.running_runs(session, org):
        if now - other.created_at < w1_domain.SETUP_STALE_AFTER:
            raise problem(
                ConflictProblem,
                "A WhatsApp setup is already running for this organization.",
                "setup_in_progress",
            )
        _fail(other, "abandoned", now)

    steps = initial_steps()
    run = SetupRun(
        organization_id=org,
        status="running",
        steps=steps,
        code_enc=None,  # the code is exchanged in this request and never stored
        code_hash=code_hash,
        waba_hint=waba_id,
        phone_number_hint=phone_number_id,
        started_by=str(ctx.principal.sub),
    )
    _set_step(run, "token", "running", None)
    try:
        async with session.begin_nested():
            session.add(run)
            await session.flush()
    except IntegrityError as exc:
        # A retry that raced this one past the read above: hand back the winner.
        winner = await w1_selectors.run_by_code_hash(session, org, code_hash)
        if winner is not None:
            return winner.id, False
        # Not the same code, so it is the unique index on running setups: another request got a
        # setup in between the read above and this insert.
        raise problem(
            ConflictProblem,
            "A WhatsApp setup is already running for this organization.",
            "setup_in_progress",
        ) from exc

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
    return run.id, True


def _set_step(run: SetupRun, key: str, state: str, detail: str | None) -> None:
    # Reassign the list: SQLAlchemy does not see an in-place edit of a JSONB value.
    run.steps = [
        {**s, "state": state, "detail": detail} if s["key"] == key else s for s in run.steps
    ]


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
        registration=w1_domain.registration_state(row.status, row.platform_type),
        code_verification_status=row.code_verification_status,
        throughput_level=row.throughput_level,
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
    the other numbers of this WABA. A cached number of a different WABA (one this organization has
    left) is marked `disconnected` either way: it is not on the account any more.
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
        row.code_verification_status = w1_domain.code_verification_from_meta(
            item.get("code_verification_status")
        )
        row.platform_type = w1_domain.platform_type_from_meta(item.get("platform_type"))
        row.throughput_level = w1_domain.throughput_from_meta(item.get("throughput"))
        row.refreshed_at = now
    for pnid, row in rows.items():
        if pnid in seen:
            continue
        # Not listed on this WABA: a number the list dropped (only a full list says so), or a row
        # cached from a WABA this organization has since left, which is never live whatever the
        # list was.
        if mark_missing or row.waba_id != waba_id:
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


@dataclass(frozen=True, slots=True)
class DefaultCandidate:
    """What one default-number change was checked against, and the token to prove it with. Built
    in one short transaction, used for the Graph call outside any, and compared again in the
    transaction that writes. Holds an opened token: never log it, never return it."""

    waba_id: str
    phone_number_id: str
    token: str = field(repr=False)


async def _default_preconditions(
    session: AsyncSession, org: uuid.UUID, number_id: uuid.UUID
) -> tuple[WhatsAppNumber, ProviderAccount]:
    """The number and the account it would become the default of, or the refusal. The number must
    be on the account's own WABA: a cached row of a WABA the organization has left is not its
    number any more, whatever its status says."""
    number = await w1_selectors.get_number(session, org, number_id)
    if number is None:
        raise NotFoundProblem("No such number.")
    account = await selectors.get_whatsapp_connection(session, org)
    if account is None or account.status != "connected":
        raise problem(ConflictProblem, "Connect WhatsApp first.", "channel_not_connected")
    if number.status == "disconnected" or number.waba_id != account.waba_id:
        raise problem(
            ConflictProblem, "That number is not active on the WhatsApp account.", "number_inactive"
        )
    return number, account


async def prepare_default_number(
    session: AsyncSession, ctx: ScopeContext, *, number_id: uuid.UUID
) -> DefaultCandidate | None:
    """First half of setting the default: check the request and hand back what Meta must confirm.

    `None` when the number already is the default (nothing to change, nothing to prove). Read-only,
    so the caller can end its transaction before it calls Meta."""
    number, account = await _default_preconditions(session, ctx.organization_id, number_id)
    if number.is_default and account.phone_number_id == number.phone_number_id:
        return None
    token = _account_token(account)
    if token is None:
        raise problem(ConflictProblem, "Connect WhatsApp first.", "channel_not_connected")
    return DefaultCandidate(
        waba_id=account.waba_id, phone_number_id=number.phone_number_id, token=token
    )


async def verify_default_candidate(
    candidate: DefaultCandidate, *, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    """Ask Meta, with the connection's own token, whether the number is on the WABA and readable.
    The webhook routing key is about to move to it, so a stale cache row is not evidence enough.
    Raises 409 `ownership_check_failed` when Meta does not confirm, or cannot be reached."""
    cfg = settings()
    async with httpx.AsyncClient(
        timeout=cfg.whatsapp_timeout_seconds, transport=transport
    ) as client:
        try:
            await whatsapp_provider.verify_ownership(
                cfg,
                waba_id=candidate.waba_id,
                phone_number_id=candidate.phone_number_id,
                access_token=candidate.token,
                client=client,
            )
        except whatsapp_provider.WhatsAppOwnershipError as exc:
            raise problem(
                ConflictProblem,
                "Meta would not confirm that this WhatsApp account controls that number.",
                "ownership_check_failed",
            ) from exc


async def set_default_number(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    directory: AsyncSession,
    number_id: uuid.UUID,
    verified: DefaultCandidate,
    request: Request | None = None,
) -> None:
    """Make one cached number the one sends go out on, after Meta confirmed it (`verified`).

    Mirrors it into `provider_accounts` (the routing row), so the number's global uniqueness is
    checked on `directory`, the cross-tenant reader — the tenant session cannot see another
    organization's row — with the unique constraint as the net for the race after that check.
    Everything the first half read is checked again here, because the connection may have been
    replaced while Meta was being asked: the confirmation only counts for the WABA and number it
    was given for.
    """
    org = ctx.organization_id
    number, account = await _default_preconditions(session, org, number_id)
    if (account.waba_id, number.phone_number_id) != (verified.waba_id, verified.phone_number_id):
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


#: What a disconnect tells the person when Meta could not be asked to stop the old account's events.
SUBSCRIPTION_NOT_RELEASED: Final = (
    "WhatsApp was disconnected here, but Meta could not be asked to stop sending that account's "
    "events to us. Remove the app from the account in Meta Business settings to finish."
)


@dataclass(frozen=True, slots=True)
class Released:
    """The WABA a disconnect left, and the token that could still speak for it, opened before it
    was wiped so the app can be unsubscribed from that WABA after the commit."""

    waba_id: str
    token: str | None = field(repr=False)


async def disconnect(
    session: AsyncSession, ctx: ScopeContext, *, request: Request | None = None
) -> Released:
    """Mark the connection disconnected, wipe the sealed token and **release the routing key**.

    History (templates, messages, conversations, the numbers cache, the audit trail) is kept. The
    row's `phone_number_id` is the globally unique webhook routing key, and keeping it made the
    number unclaimable by any other organization forever (`number_claimed`): it is moved to
    `released_phone_number_id` and the key is nulled, so the number is free to connect elsewhere
    and a webhook for it no longer resolves to this organization (it is dropped as an unknown
    number). Reconnecting sets the key again, subject to the same uniqueness check as any claim.

    Returns what `release_subscription` needs to stop the old WABA's events after the commit."""
    org = ctx.organization_id
    account = await selectors.get_whatsapp_connection(session, org)
    if account is None or account.organization_id != org or account.status != "connected":
        raise problem(ConflictProblem, "WhatsApp is not connected.", "channel_not_connected")
    released = Released(waba_id=account.waba_id, token=_account_token(account))
    released_number = account.phone_number_id
    account.status = "disconnected"
    account.access_token_enc = None
    account.released_phone_number_id = released_number
    account.phone_number_id = None
    account.updated_by = str(ctx.principal.sub)
    for row in await w1_selectors.numbers(session, org):
        row.status = "disconnected"
        row.is_default = False
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
        # Ids only. The number is recorded as released, so the history says which one it was.
        after={"waba_id": account.waba_id, "released_phone_number_id": released_number},
        request=request,
    )
    return released


async def subscribe_waba(
    waba_id: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> bool:
    """Best-effort: subscribe this app to `waba_id`'s webhooks. True when Meta confirmed.

    Embedded Signup does this as its own setup step (`w1_setup.py`'s `_step_webhook`); a manually
    connected or replaced account (`services.create_whatsapp_connection`,
    `replace_whatsapp_connection`) needs the same call, or its inbound messages and delivery
    statuses never arrive here at all — the connection looks `connected` and nothing about sending
    fails, but nothing sent to it is ever heard back from. Never raises: a token that can read the
    number (already proved by `verify_ownership`) but lacks `whatsapp_business_management` for
    this one call is not a reason to refuse the whole connection, so a refusal is logged (status
    and code only, never the token or Meta's body) and reported as False.
    """
    try:
        await MetaGraph(settings(), transport=transport).subscribe_app(waba_id, token)
    except GraphError as exc:
        log.warning("w1.subscribe_failed", status=exc.status, code=exc.code)
        return False
    return True


async def unsubscribe_waba(
    waba_id: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> bool:
    """Best-effort: stop `waba_id`'s webhooks reaching this app. True when Meta confirmed.

    Never raises for Meta's answer: an organization that has left a WABA must be able to, whether
    or not Meta is reachable, so a refusal is logged (status and code only, never the token or
    Meta's body) and reported as False."""
    try:
        await MetaGraph(settings(), transport=transport).unsubscribe_app(waba_id, token)
    except GraphError as exc:
        log.warning("w1.unsubscribe_failed", status=exc.status, code=exc.code)
        return False
    return True


async def release_subscription(
    directory: AsyncSession,
    organization_id: uuid.UUID,
    released: Released,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """After a disconnect committed: unsubscribe the app from the WABA it left. True when there
    was nothing left to do (the WABA is still another organization's connection, so its events
    must keep coming) or Meta confirmed; False when the person should be told to remove the app
    from that account themselves."""
    if released.token is None:
        return False
    try:
        holders = await w2_selectors.organizations_for_waba(directory, released.waba_id)
    except Exception as exc:
        # Not knowing whether another organization shares the WABA is not a reason to cut it off.
        log.warning("w1.release_lookup_failed", error_type=type(exc).__name__)
        return False
    if any(holder != organization_id for holder in holders):
        log.info("w1.unsubscribe_skipped_shared_waba")
        return True
    return await unsubscribe_waba(released.waba_id, released.token, transport=transport)


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

    Refused with `channel_not_connected` when there is no connected account, `template_not_found`
    when the template is not one this account has synced, and `consent_denied` when the consent
    ledger does not allow this send (a marketing template needs an opt-in like any marketing
    send)."""
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
    if row is None:
        # A template we have not synced has no category we can check, and the category decides
        # whether the recipient must have opted in. Sync, then send.
        raise problem(
            NotFoundProblem,
            "That template has not been synced for this WhatsApp account.",
            "template_not_found",
        )
    body = row.preview_text or f"Test message using template {template_name} ({language})"
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
        # The row's code is what `consent.purpose_for_send` reads the template's category from:
        # a marketing template needs an opt-in on record, here and again when it is claimed.
        provider_template_code=row.code,
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
    if token is None or account.phone_number_id is None:
        return None
    return account.waba_id, token, account.phone_number_id
