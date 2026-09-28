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

import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import httpx
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors, services, w1_domain, w1_selectors, w2_selectors, whatsapp_provider
from app.config import Settings, graph_version, meta_app_secret, settings
from app.models import AuditLog, OutboxEvent, ProviderAccount
from app.w1_graph import CodeExpiredError, GraphError, MetaGraph
from app.w1_models import ConnectionHealth, MetaDataDeletionRequest, SetupRun, WhatsAppNumber
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
    meta_user_id: str | None = None,
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
            meta_user_id=meta_user_id,
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
    meta_user_id: str | None,
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
        meta_user_id_hint=meta_user_id,
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
    run.meta_user_id_hint = None


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
            # Advisory only -- shown whether or not the connection is otherwise healthy. See
            # `ConnectionHealth.identity_revoked_at`'s docstring for why this never feeds `state`.
            identity_revoked_at=_iso(health.identity_revoked_at) if health is not None else None,
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
    # The identity link goes with the connection: a future reconnect (by this person or another)
    # must not inherit an old Facebook login's attribution, and a deauthorize/data-deletion
    # callback for the login that made *this* connection has nothing left here to act on.
    account.meta_user_id = None
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


# --------------------------------------------------------------------- Meta deauthorize / data
# deletion (app/api/webhooks.py's `/whatsapp/deauthorize` and `/whatsapp/data-deletion`)
#
# Both react to a `signed_request` naming a Facebook user id (`app/meta_signed_request.py`
# verifies it before either function below is called, and refuses outright anything shaped like
# the Facebook JS SDK's own login response -- see that module's docstring).
#
# **Neither function may flip `token_state`.** Embedded Signup mints a business-integration
# token; a person removing the app from *their own* Facebook settings does not necessarily revoke
# that token, so a genuine callback is not proof the connection has stopped working. Worse, the
# same app secret signs `FB.login`'s own response (`authResponse.signedRequest`, and the
# `fbsr_<appid>` cookie) in a shape indistinguishable from a real callback but for the fields
# `meta_signed_request` refuses on sight -- so anyone who was ever shown Embedded Signup's popup
# for a connection (a departed employee, a consultant who set several customers up) could
# otherwise mint one on demand and stop that organization's sends at will, with no OpsGrids
# account. Both functions instead record `ConnectionHealth.identity_revoked_at` -- an advisory
# timestamp `app/dispatcher.py` never reads, shown on the health card as a plain, informational
# note. Only Meta's own Graph API answering for the *token itself* (`w1_setup`'s own token checks,
# `record_token_check`) may ever flip `token_state`. Neither function disconnects the account
# (`disconnect`, above, is the full wipe a person asks for explicitly) or touches any business
# record -- numbers, templates, conversations, message history.
#
# **Both are one-shot per connection.** `ProviderAccount.meta_user_id` is cleared the moment either
# effect is applied, so a second callback naming the same Facebook user id -- Meta genuinely
# redelivering, or a fresh, validly-signed blob for the same id minted the way the paragraph above
# describes -- finds nothing left to act on: `selectors.accounts_for_meta_user_id`'s own lookup no
# longer matches the row. This is idempotency *and* the fix for the lever above: closing the
# identity link is what makes a repeat, however it was obtained, powerless.
#
# **Both re-check the identity before acting.** The account is loaded `FOR UPDATE` and the
# callback's own `user_id` is compared against the row's current `meta_user_id` a second time,
# under the lock -- not just by the directory reader's own `WHERE` a moment earlier, which a
# concurrent reconnect (a new token, a new identity link) could have already raced past. A mismatch
# is treated exactly like "no match at all": nothing is touched, nothing is audited.

#: Meta's deauthorize callback reported that the Facebook login behind a connection was
#: deauthorized from our app. The dedicated source/reason pair `apply_meta_deauthorize`'s audit
#: row carries, in the shape `consent.SOURCE_META_PREFERENCE`'s own docstring documents: a fixed,
#: non-PII evidence sentence, never anything read from the callback's own payload.
SOURCE_META_DEAUTHORIZE: Final = "meta_deauthorize"
_META_DEAUTHORIZE_REASON: Final = (
    "Meta's deauthorize callback reported that the Facebook login behind this WhatsApp "
    "connection was deauthorized from our app."
)

#: Meta's data-deletion callback asked that the data tied to one Facebook login be deleted. Scoped
#: to *that person's identity link* only -- see `apply_meta_data_deletion`'s docstring for what
#: this deliberately does not touch.
SOURCE_META_DATA_DELETION: Final = "meta_data_deletion"
_META_DATA_DELETION_REASON: Final = (
    "Meta's data-deletion callback asked that the data tied to the Facebook login behind this "
    "WhatsApp connection be deleted."
)


async def _record_identity_revoked(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Advisory only -- see the module-level note above. Never writes `token_state`; creates the
    health row (as `unknown`, the same default `record_token_check` gives a fresh one) if a
    callback somehow arrives before any setup or token check ever has."""
    now = datetime.now(UTC)
    health = await w1_selectors.get_health(session, organization_id)
    if health is None:
        health = ConnectionHealth(organization_id=organization_id)
        health.token_state = "unknown"  # noqa: S105 - a state word, not a credential
        session.add(health)
    health.identity_revoked_at = now
    await session.flush()


async def apply_meta_deauthorize(
    session: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    user_id: str,
    request: Request | None = None,
) -> bool:
    """One connection's reaction to a deauthorize callback naming its `meta_user_id`. Records the
    advisory `identity_revoked_at` signal (never `token_state` -- see the module-level note above)
    and writes an audit row with a fixed, non-PII evidence sentence -- never the Meta user id
    itself.

    One-shot: `meta_user_id` is cleared here, so a second callback for the same id -- genuine or
    not -- finds this row un-matched by `selectors.accounts_for_meta_user_id` and never reaches
    this function again. The account is loaded `FOR UPDATE` and `user_id` is re-checked against it
    under the lock, which is what makes two overlapping deliveries of the same callback resolve to
    exactly one audit row without a separate `processed_events` entry: the loser's own re-check
    fails once the winner has committed. Returns whether this call actually did anything, for the
    route's own logging.
    """
    stmt = select(ProviderAccount).where(ProviderAccount.id == account_id).with_for_update()
    account = (await session.execute(stmt)).scalar_one_or_none()
    if (
        account is None
        or account.organization_id != organization_id
        or account.meta_user_id != user_id
    ):
        return False
    account.meta_user_id = None
    await _record_identity_revoked(session, organization_id)
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        action="engage.whatsapp.meta_deauthorized",
        entity="provider_account",
        entity_id=account.id,
        reason=_META_DEAUTHORIZE_REASON,
        request=request,
    )
    return True


async def apply_meta_data_deletion(
    session: AsyncSession,
    organization_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    user_id: str,
    request: Request | None = None,
) -> bool:
    """One organization's reaction to a data-deletion callback matching one of its connections.

    Nulls `ProviderAccount.meta_user_id` -- the connection can no longer be tied to that Facebook
    login -- and records the advisory `identity_revoked_at` signal the same way
    `apply_meta_deauthorize` does (never `token_state`; see the module-level note above).

    **Scope, stated plainly because it matters for App Review as much as for correctness.** This
    touches only the identity link: `meta_user_id`, the connection health note, and every
    `w1_setup_runs.meta_user_id_hint` this organization's runs carried for the same id (a run still
    in progress must not copy a since-deleted identity onto the connection later). It does not
    delete, disconnect, or otherwise touch the organization's own WhatsApp business records --
    `w1_numbers`, `provider_templates`, `conversations`, `inbound_messages`, `messages` -- which
    are the tenant's business data, not personal data about the person who once completed Embedded
    Signup. See `docs/whatsapp/webhooks.md`.

    The account is loaded `FOR UPDATE` and `user_id` is re-checked against it under the lock --
    the same re-check, and for the same race, `apply_meta_deauthorize`'s docstring explains.
    Idempotent by absence: once `meta_user_id` is nulled, a retried callback's own account lookup
    (`selectors.accounts_for_meta_user_id`, run again by the caller before this is reached) simply
    no longer finds this row, so a second call for the same account never happens. Returns whether
    this call actually did anything (False when the account was already cleared, replaced, or
    removed by the time this ran, or the id no longer matches under the lock).
    """
    stmt = select(ProviderAccount).where(ProviderAccount.id == account_id).with_for_update()
    account = (await session.execute(stmt)).scalar_one_or_none()
    if (
        account is None
        or account.organization_id != organization_id
        or account.meta_user_id != user_id
    ):
        return False
    account.meta_user_id = None
    await _record_identity_revoked(session, organization_id)
    # Same tenant transaction as the rest of this effect: a run still `running` that copies
    # `meta_user_id_hint` onto the connection after this commits (`w1_setup._step_number`) would
    # otherwise re-arm the identity link this same request just asked to have forgotten.
    await session.execute(
        sa_update(SetupRun)
        .where(SetupRun.organization_id == organization_id, SetupRun.meta_user_id_hint == user_id)
        .values(meta_user_id_hint=None)
    )
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        action="engage.whatsapp.meta_data_deletion",
        entity="provider_account",
        entity_id=account.id,
        reason=_META_DATA_DELETION_REASON,
        request=request,
    )
    return True


# --------------------------------------------------------------------- data-deletion confirmation


def _data_deletion_request_key(payload_segment: str, cfg: Settings) -> str:
    """Blind index of the still-encoded `signed_request` payload segment -- see
    `MetaDataDeletionRequest`'s docstring for why this, and not the id itself, is what
    `w1_meta_deletion_requests` carries.

    Hashing the raw segment rather than `f"{user_id}:{issued_at}"` means this key never depends on
    `issued_at` being present or well-typed: Meta fixes the *whole* payload at sign time and
    resends the identical bytes on a genuine redelivery, so this key is exactly as idempotent as
    the old one for a real retry, and unlike it never collapses two distinct requests that both
    happen to omit `issued_at` onto the shared key `f"{user_id}:None"` would have produced.
    """
    return pii.blind_index(payload_segment, kind="generic", settings=cfg)


def data_deletion_status_url(cfg: Settings, confirmation_code: str) -> str:
    """The URL this platform's own answer to Meta names -- `engage_public_api_base` is the
    gateway's public base (the same one campaign unsubscribe links use), not an Engage-specific
    one despite its name."""
    base = cfg.engage_public_api_base.rstrip("/")
    return f"{base}/webhooks/whatsapp/data-deletion-status?code={confirmation_code}"


async def mint_or_reuse_confirmation(
    session: AsyncSession, *, payload_segment: str, cfg: Settings
) -> MetaDataDeletionRequest:
    """The confirmation record for one data-deletion callback -- minted fresh, or the one an
    earlier, identical callback already minted.

    Idempotent on `request_key` (`_data_deletion_request_key`): "the same request" is a
    byte-identical `signed_request` payload segment, which is what a genuine Meta redelivery of
    one callback carries and what a person's second, later "Send Request" click does not (Meta
    stamps a fresh `issued_at` into the payload on each, which changes the segment). This session
    carries no tenant GUC (`session_scope(organization_id=None)`, the caller's own choice) -- see
    `MetaDataDeletionRequest`'s docstring for why this table has no organization to bind one to in
    the first place.

    The insert races the same way `_reserve_run` above does, and is fixed the same way: `add`
    happens *inside* the savepoint, so a losing `IntegrityError` leaves the outer transaction
    exactly as usable as it was before this was called, and the winner's own row is still there to
    read back.
    """
    request_key = _data_deletion_request_key(payload_segment, cfg)
    existing = (
        await session.execute(
            select(MetaDataDeletionRequest).where(
                MetaDataDeletionRequest.request_key == request_key
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    try:
        async with session.begin_nested():
            row = MetaDataDeletionRequest(
                confirmation_code=secrets.token_hex(16), request_key=request_key
            )
            session.add(row)
            await session.flush()
    except IntegrityError:
        # Lost a race with another request carrying the identical key (two concurrent redeliveries
        # of the same callback): the winner's row is the one to hand back. The savepoint's own
        # rollback already undid the loser's failed insert, so the outer transaction this session
        # is still in is exactly as usable as it was before `begin_nested` -- the bug this fixes
        # was adding the row *outside* the savepoint, so the outer transaction was left aborted by
        # the time this `SELECT` ran.
        winner = (
            await session.execute(
                select(MetaDataDeletionRequest).where(
                    MetaDataDeletionRequest.request_key == request_key
                )
            )
        ).scalar_one()
        return winner
    return row


async def data_deletion_confirmation_status(
    session: AsyncSession, confirmation_code: str
) -> MetaDataDeletionRequest | None:
    """The one thing `GET .../data-deletion-status` may answer with: this code's own row, or
    None. See `MetaDataDeletionRequest`'s docstring for why the row itself carries nothing else."""
    stmt = select(MetaDataDeletionRequest).where(
        MetaDataDeletionRequest.confirmation_code == confirmation_code
    )
    return (await session.execute(stmt)).scalar_one_or_none()
