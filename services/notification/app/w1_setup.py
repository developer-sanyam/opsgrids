"""The Embedded Signup worker (slice W1): runs one persisted setup, one step at a time.

`POST /engage/whatsapp/signup/complete` exchanges the popup's one-time code for a business token
**inside the request** (Meta's code lives about thirty seconds, so it cannot wait behind a queue;
see `w1_services.start_signup`), seals the token on a `w1_setup_runs` row and commits it together
with a `notification.whatsapp.setup_requested` outbox event. The relay publishes it after commit
and `consumers.run_whatsapp_setup` calls `run_setup` here, on the worker's own tenant scope (one
transaction per step, the tenant GUC bound per message, the same shape as
`consumers.expand_campaign_send`). The six steps, in order:

1. **token**: already done by the request. (A run queued by an older release still carries a
   sealed code instead, and this step exchanges it; new runs never hold one.)
2. **number**: discover the WABA and its numbers, prove the token controls the chosen one
   (`whatsapp_provider.verify_ownership`), then store the sealed token, the WABA and the number in
   the organization's `provider_accounts` row and fill the numbers cache. When this replaces a
   connection on another WABA, that WABA's cached numbers are marked disconnected and the app is
   unsubscribed from it (best effort, after everything else in the step).
3. **webhook**: subscribe the app to the WABA (`POST /{waba}/subscribed_apps`).
4. **register**: register the number for the Cloud API (`POST /{phone_number_id}/register` with a
   six-digit PIN), unless Meta already reports it `CONNECTED`. Idempotent and never blind: the PIN
   is generated once, sealed, and reused, and the attempts are counted against Meta's ten per 72
   hours.
5. **templates**: `w2_services.sync_templates`.
6. **verify**: read the number and the subscription back from Meta; fails, with the reason, when
   the number is still not `CONNECTED`.

Every step's result is committed before the next starts, so `GET signup/{id}` shows real progress
and a redelivered event resumes at the first step that is not `done`. A failure marks that step
and the run `failed` with a machine-readable code and stops; steps after it stay `waiting`. A
Graph call happens inside its step's transaction (a Graph timeout is ten seconds); that is the
worker's own pool, never a request's.

**Secrets.** The token and the registration PIN are sealed at rest, opened only for the call that
needs them, the token wiped from the run as soon as it is spent (and on any failure), and none of
them in a log line, a `detail` string or an audit row. Failure details are fixed sentences or
Meta's own end-user message, never an exception's text.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import (
    selectors,
    services,
    w1_domain,
    w1_selectors,
    w1_services,
    w2_services,
    whatsapp_provider,
)
from app.config import Settings, settings
from app.models import AuditLog, OutboxEvent, ProcessedEvent, ProviderAccount
from app.w1_graph import CodeExpiredError, GraphError, MetaError, MetaGraph
from app.w1_models import SetupRun
from platform_core import pii
from platform_core.audit import record_audit
from platform_core.db import session_scope
from platform_core.errors import ProblemError
from platform_core.events import already_processed, mark_processed, publish_event
from platform_core.logging import get_logger

log = get_logger("notification.w1_setup")

WorkScope = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]

SYSTEM_ACTOR: Final = "system:whatsapp-signup"

#: Re-exported from `w1_services` (the shared rate limit `reserve_registration_pin` enforces,
#: also used by a manual connection's `register_number_if_needed`) so this module's own tests
#: keep working unchanged.
REGISTER_ATTEMPT_CAP: Final = w1_services.REGISTER_ATTEMPT_CAP
REGISTER_WINDOW: Final = w1_services.REGISTER_WINDOW
#: After registering, Meta takes a moment to report the number `CONNECTED`: the verify step reads
#: it up to this many times, this many seconds apart, before it calls the number not ready.
VERIFY_READS: Final = 4
VERIFY_WAIT_SECONDS: float = 2.0


class StepFailed(Exception):
    """One step could not finish. `code` is the machine-readable reason the run fails with;
    `detail` is a sentence safe to show the person who is connecting."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Ctx:
    cfg: Settings
    transport: httpx.AsyncBaseTransport | None
    organization_id: uuid.UUID
    setup_id: uuid.UUID
    #: The worker's tenant scope, for the one step (`register`) that must commit something of its
    #: own before it calls Meta.
    scope: WorkScope

    def graph(self) -> MetaGraph:
        return MetaGraph(self.cfg, transport=self.transport)


def _default_scope(organization_id: uuid.UUID) -> Any:
    return session_scope(organization_id=organization_id)


def _graph_failure(code: str, exc: GraphError) -> StepFailed:
    """A Graph refusal as a step failure. `public_message` is Meta's end-user text or a fixed
    sentence (see `w2_graph.GraphError`); never the exception chain."""
    return StepFailed("meta_unreachable" if exc.retryable else code, exc.public_message)


# --------------------------------------------------------------------- the run


async def run_setup(
    envelope: dict[str, Any],
    *,
    scope: WorkScope | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Advance one setup run to `done` or `failed`. Returns `done`, `failed`, `redelivered`,
    `missing` or `finished` (the run was already terminal).

    Idempotent: the event id goes through `processed_events` in the transaction that finishes the
    run, and a redelivery that arrives mid-run resumes at the first step not `done` — a step
    already done is never repeated (in particular the code is never exchanged twice).
    """
    scope = scope or _default_scope
    event_id = uuid.UUID(str(envelope["event_id"]))
    event_type = str(envelope["event_type"])
    organization_id = uuid.UUID(str(envelope["organization_id"]))
    payload = envelope.get("payload") or {}
    if str(payload.get("organization_id", organization_id)) != str(organization_id):
        log.error("w1.setup.envelope_payload_org_mismatch")
        return "missing"
    setup_id = uuid.UUID(str(payload["setup_id"]))
    ctx = Ctx(settings(), transport, organization_id, setup_id, scope)

    async with scope(organization_id) as session:
        if await already_processed(session, ProcessedEvent, event_id):
            return "redelivered"
        run = await w1_selectors.get_run(session, organization_id, setup_id)
        if run is None or run.organization_id != organization_id:
            mark_processed(session, ProcessedEvent, event_id, event_type)
            log.warning("w1.setup.run_missing")
            return "missing"
        if run.status != "running":
            mark_processed(session, ProcessedEvent, event_id, event_type)
            return "finished"

    outcome = "done"
    for key, _label in w1_services.STEPS:
        result = await _run_step(scope, ctx, key)
        if result == "skip":
            continue
        if result != "ok":
            outcome = result
            break

    async with scope(organization_id) as session:
        run = await w1_selectors.get_run(session, organization_id, setup_id, for_update=True)
        if run is not None and run.status == "running" and outcome == "done":
            run.status = "done"
            run.finished_at = datetime.now(UTC)
            run.code_enc = None
            run.token_enc = None
        mark_processed(session, ProcessedEvent, event_id, event_type)
    log.info("w1.setup.finished", outcome=outcome)
    return outcome


_STEP_FUNCS: dict[str, Callable[[AsyncSession, SetupRun, Ctx], Any]] = {}


async def _run_step(scope: WorkScope, ctx: Ctx, key: str) -> str:
    """One step in its own two short transactions: mark it running, then do it and record how it
    went. Returns `ok`, `skip` (already done), `failed`, or `finished` (run no longer running)."""
    org, setup_id = ctx.organization_id, ctx.setup_id
    async with scope(org) as session:
        run = await w1_selectors.get_run(session, org, setup_id, for_update=True)
        if run is None or run.status != "running":
            return "finished"
        _ensure_steps(run)
        step = _step(run, key)
        if step["state"] == "done":
            return "skip"
        _set(run, key, "running", None)

    failure: StepFailed | None = None
    async with scope(org) as session:
        run = await w1_selectors.get_run(session, org, setup_id, for_update=True)
        if run is None or run.status != "running":
            return "finished"
        try:
            # A savepoint: whatever a failed step half-wrote is undone, the failure recorded.
            async with session.begin_nested():
                detail = await _STEP_FUNCS[key](session, run, ctx)
        except StepFailed as exc:
            failure = exc
        except GraphError as exc:
            failure = _graph_failure(f"{key}_failed", exc)
        except ProblemError as exc:
            failure = StepFailed(_problem_code(exc, f"{key}_failed"), _safe(exc))
        except Exception as exc:
            log.error("w1.setup.step_error", step=key, error_type=type(exc).__name__)
            failure = StepFailed("internal_error", "Something went wrong on our side.")
        if failure is None:
            _set(run, key, "done", detail)
            return "ok"
        # The savepoint's rollback expired whatever the step had touched, `run` included; reload
        # it (awaited) before writing the failure, rather than lazy-loading inside a sync setter.
        await session.refresh(run)
        now = datetime.now(UTC)
        _set(run, key, "failed", failure.detail)
        run.status = "failed"
        run.error_code = failure.code
        run.finished_at = now
        run.code_enc = None
        run.token_enc = None
        # A run that never reached the step that copies this onto the connection must not leave
        # it sitting in `w1_setup_runs` forever either -- it is exactly as done with as the code
        # and the token wiped on the same lines above.
        run.meta_user_id_hint = None
        log.warning("w1.setup.step_failed", step=key, code=failure.code)
        return "failed"


def _problem_code(exc: ProblemError, default: str) -> str:
    for item in exc.errors or []:
        code = item.get("code") if isinstance(item, dict) else None
        if isinstance(code, str) and code:
            return code
    return default


def _safe(exc: ProblemError) -> str:
    """A `ProblemError`'s detail is written for the client by the code that raised it."""
    return exc.detail[:300]


def _ensure_steps(run: SetupRun) -> None:
    """A run queued by an older release lacks the steps added since (`register`): give it them,
    `waiting`, in their place, rather than crash on the missing key."""
    have = {step["key"]: step for step in run.steps}
    if all(key in have for key, _ in w1_services.STEPS):
        return
    fresh = {s["key"]: s for s in w1_services.initial_steps()}
    run.steps = [have.get(key, fresh[key]) for key, _ in w1_services.STEPS]


def _step(run: SetupRun, key: str) -> dict[str, Any]:
    for step in run.steps:
        if step["key"] == key:
            return step
    raise KeyError(key)


def _set(run: SetupRun, key: str, state: str, detail: str | None) -> None:
    # Reassign the list: SQLAlchemy does not see an in-place edit of a JSONB value.
    run.steps = [
        {**s, "state": state, "detail": detail} if s["key"] == key else s for s in run.steps
    ]


# --------------------------------------------------------------------- steps


async def _step_token(session: AsyncSession, run: SetupRun, ctx: Ctx) -> str:
    """Normally already `done` (the request exchanged the code); reached only by a run an older
    release queued with a sealed code and no token."""
    if run.token_enc:
        return "Connected to Meta"
    if not run.code_enc:
        raise StepFailed("code_exchange_failed", "The authorization code is no longer available.")
    code = pii.open_sealed(run.code_enc, settings=ctx.cfg)
    try:
        token = await ctx.graph().exchange_code(code)
    except CodeExpiredError as exc:
        raise StepFailed("code_expired", exc.public_message) from exc
    except GraphError as exc:
        if exc.public_message.startswith("WhatsApp signup is not configured"):
            raise StepFailed("not_configured", exc.public_message) from exc
        raise _graph_failure("code_exchange_failed", exc) from exc
    sealed, _ = pii.seal(token, kind="generic", settings=ctx.cfg)
    run.token_enc = sealed
    run.code_enc = None  # single use, and spent
    return "Connected to Meta"


def _open_run_token(run: SetupRun, cfg: Settings) -> str:
    if not run.token_enc:
        raise StepFailed("token_missing", "The connection token is no longer available.")
    try:
        return pii.open_sealed(run.token_enc, settings=cfg)
    except pii.PiiError as exc:
        raise StepFailed("token_missing", "The connection token could not be read.") from exc


async def _discover_waba(
    graph: MetaGraph, token: str, hint: str | None
) -> tuple[str, datetime | None]:
    granted: list[str] = []
    expires_at: datetime | None = None
    try:
        data = await graph.debug_token(token)
        granted = MetaGraph.wabas_from_debug(data)
        raw_expiry = data.get("expires_at")
        if isinstance(raw_expiry, int) and raw_expiry > 0:
            expires_at = datetime.fromtimestamp(raw_expiry, tz=UTC)
    except GraphError as exc:
        # Discovery by debug_token is best effort: the browser's hint, verified against the
        # token below, is enough. A token Meta refuses outright fails at `list_numbers`.
        log.info("w1.setup.debug_token_unavailable", status=exc.status)
    if granted:
        if hint is not None and hint not in granted:
            raise StepFailed(
                "waba_not_accessible", "That WhatsApp Business Account was not shared with us."
            )
        if hint is not None:
            return hint, expires_at
        if len(granted) == 1:
            return granted[0], expires_at
        raise StepFailed(
            "waba_ambiguous",
            "More than one WhatsApp Business Account was shared with us, and we could not tell "
            "which to use. Connect again and share just one.",
        )
    if hint is None:
        raise StepFailed("waba_not_found", "No WhatsApp Business Account was shared with us.")
    return hint, expires_at


def _choose_number(numbers: list[dict[str, Any]], hint: str | None) -> dict[str, Any]:
    if not numbers:
        raise StepFailed("no_phone_number", "This WhatsApp Business Account has no phone number.")
    if hint is not None:
        for item in numbers:
            if str(item.get("id")) == hint:
                return item
        raise StepFailed("number_not_found", "That phone number is not on the account.")
    if len(numbers) == 1:
        return numbers[0]
    for item in numbers:
        if w1_domain.status_from_meta(item.get("status")) == "connected":
            return item
    return numbers[0]


async def _step_number(session: AsyncSession, run: SetupRun, ctx: Ctx) -> str:
    org = ctx.organization_id
    token = _open_run_token(run, ctx.cfg)
    graph = ctx.graph()

    waba_id, expires_at = await _discover_waba(graph, token, run.waba_hint)
    remote = await graph.list_numbers(waba_id, token)
    chosen = _choose_number(remote, run.phone_number_hint)
    phone_number_id = str(chosen["id"])
    display = str(chosen.get("display_phone_number") or "")[:40]

    async with httpx.AsyncClient(
        timeout=ctx.cfg.whatsapp_timeout_seconds, transport=ctx.transport
    ) as client:
        try:
            await whatsapp_provider.verify_ownership(
                ctx.cfg,
                waba_id=waba_id,
                phone_number_id=phone_number_id,
                access_token=token,
                client=client,
            )
        except whatsapp_provider.WhatsAppOwnershipError as exc:
            raise StepFailed(
                "ownership_check_failed",
                "Meta would not confirm this account controls that number.",
            ) from exc

    sealed, _ = pii.seal(token, kind="generic", settings=ctx.cfg)
    account = await selectors.get_whatsapp_connection(session, org)
    created = account is None
    # A signup on a different WABA replaces the connection: the token that could still speak for
    # the old one is opened now, before it is overwritten, so the app can be unsubscribed from it.
    left_waba: str | None = None
    left_token: str | None = None
    if account is not None and account.status == "connected" and account.waba_id != waba_id:
        left_waba = account.waba_id
        left_token = w1_services._account_token(account)
    if account is None:
        account = ProviderAccount(
            organization_id=org,
            created_by=run.started_by or SYSTEM_ACTOR,
            channel="whatsapp",
            waba_id=waba_id,
            phone_number_id=phone_number_id,
            display_number=display,
            access_token_enc=sealed,
            status="connected",
            meta_user_id=run.meta_user_id_hint,
        )
        session.add(account)
    else:
        account.waba_id = waba_id
        account.phone_number_id = phone_number_id
        account.display_number = display
        account.access_token_enc = sealed
        account.status = "connected"
        account.updated_by = run.started_by or SYSTEM_ACTOR
        # Only overwritten when this run actually carried one: an older client (or the manual
        # "paste a token" path, which never touches this run shape at all) reconnecting must not
        # blank out an id a previous signup already captured.
        if run.meta_user_id_hint is not None:
            account.meta_user_id = run.meta_user_id_hint
    # Copied onto the connection (or deliberately left alone above) either way: the hint has done
    # its one job. Left in place, a completed run would keep naming this id in `w1_setup_runs`
    # forever -- a stale copy a data-deletion request would have to know to clear separately, and
    # would still show up in the run's own history after it should be forgotten.
    run.meta_user_id_hint = None
    try:
        # `provider_accounts.phone_number_id` is globally unique (the webhook routing key), so the
        # database is what refuses a number another organization already holds.
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise StepFailed(
            "number_claimed", "This WhatsApp phone number is already connected elsewhere."
        ) from exc

    await w1_services.apply_numbers(
        session,
        org,
        waba_id=waba_id,
        remote=remote,
        default_phone_number_id=phone_number_id,
    )
    await w1_services.record_token_check(
        session, org, state="valid", expires_at=expires_at, setup_id=run.id, reset_expiry=True
    )
    run.token_enc = None  # it now lives, sealed, on the provider account

    record_audit(
        session,
        AuditLog,
        organization_id=org,
        actor_id=None,
        action="engage.whatsapp.signup_connected",
        entity="provider_account",
        entity_id=account.id,
        # Ids only. Never the token or its ciphertext.
        after={"waba_id": waba_id, "phone_number_id": phone_number_id, "setup_id": str(run.id)},
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org,
        event_type=(
            services.EVT_WHATSAPP_CONNECTION_CREATED
            if created
            else services.EVT_WHATSAPP_CONNECTION_REPLACED
        ),
        aggregate_type="provider_account",
        aggregate_id=account.id,
        payload={
            "organization_id": str(org),
            "provider_account_id": str(account.id),
            "channel": "whatsapp",
        },
    )
    detail = f"Registered {len(remote)} number{'s' if len(remote) != 1 else ''}"
    # Last, so a step that fails before here has not cut the old WABA off. Best effort: the new
    # connection stands whether or not Meta answers.
    released = (
        await w1_services.unsubscribe_waba(left_waba, left_token, transport=ctx.transport)
        if left_waba is not None and left_token is not None
        else True
    )
    if not released:
        detail += "; the previous account's event subscription could not be removed"
    return detail


async def _connected_credentials(
    session: AsyncSession, run: SetupRun, ctx: Ctx
) -> tuple[ProviderAccount, str]:
    account = await selectors.get_whatsapp_connection(session, ctx.organization_id)
    if account is None or account.organization_id != ctx.organization_id:
        raise StepFailed("channel_not_connected", "WhatsApp is not connected.")
    token = w1_services._account_token(account)
    if token is None:
        raise StepFailed("channel_not_connected", "WhatsApp is not connected.")
    return account, token


def _number_id(account: ProviderAccount) -> str:
    """The routing key of a connected account. A connected account always has one; a disconnect
    releases it, so an account without one is not connected."""
    if account.phone_number_id is None:
        raise StepFailed("channel_not_connected", "WhatsApp is not connected.")
    return account.phone_number_id


async def _step_webhook(session: AsyncSession, run: SetupRun, ctx: Ctx) -> str:
    account, token = await _connected_credentials(session, run, ctx)
    try:
        await ctx.graph().subscribe_app(account.waba_id, token)
    except GraphError as exc:
        raise _graph_failure("webhook_subscribe_failed", exc) from exc
    await w1_services.record_token_check(
        session, ctx.organization_id, state="valid", setup_id=run.id, subscribed=True
    )
    return "Subscribed to WhatsApp events"


# Meta's error codes for `register`, from its error-code reference (unverified: that page did not
# load when this was written; the mapping is deliberately small and everything else is the generic
# `register_failed` with Meta's own end-user sentence).
_PIN_MISMATCH: Final = frozenset({133005})  # two-step verification PIN mismatch
_NEEDS_REVERIFY: Final = frozenset({133006})  # phone number re-verification needed
_REGISTER_LIMITED: Final = frozenset({133008, 133009, 133015, 133016})  # guesses, waits, 10/72h


def _register_outcome(exc: GraphError) -> StepFailed | None:
    """How a refused `register` reads: None when Meta says the number is already registered (a
    success for our purposes), otherwise the failure to record. Never blindly retried."""
    if exc.retryable:
        # Outcome unknown (timeout, 5xx): the run fails and the person restarts on purpose. The
        # attempt is already counted.
        return StepFailed("meta_unreachable", exc.public_message)
    message = exc.meta_message.lower() if isinstance(exc, MetaError) else ""
    if "already registered" in message or "already been registered" in message:
        return None
    if exc.code in _PIN_MISMATCH:
        return StepFailed(
            "pin_required",
            "This number already has two-step verification turned on, with a PIN we do not have. "
            "Turn two-step verification off for it in WhatsApp Manager, then connect again.",
        )
    if exc.code in _NEEDS_REVERIFY:
        return StepFailed(
            "number_not_verified",
            "Meta needs this number verified again before it can be registered. Verify it in "
            "WhatsApp Manager, then connect again.",
        )
    if exc.code in _REGISTER_LIMITED:
        return StepFailed(
            "register_rate_limited",
            "Meta is limiting registration attempts for this number. Wait a while (up to 72 "
            "hours) before connecting again.",
        )
    return StepFailed("register_failed", exc.public_message)


async def _reserve_registration(ctx: Ctx, phone_number_id: str) -> str:
    """Commit, in a transaction of its own, the fact that a `register` call is about to be made,
    and return the PIN to make it with (never logged).

    Committed *before* the call: the step's own transaction rolls back when the step fails, and a
    forgotten attempt or a forgotten PIN is exactly what would burn Meta's ten calls per 72 hours
    or leave a number registered under a PIN nobody kept. The rate limit and the PIN reuse are
    `w1_services.reserve_registration_pin` (shared with a manual connection's own registration);
    what stays here is only the transaction of its own that call needs, and translating its
    `ProblemError` into this step's `StepFailed`."""
    org = ctx.organization_id
    async with ctx.scope(org) as session:
        try:
            return await w1_services.reserve_registration_pin(
                session, ctx.cfg, org, phone_number_id
            )
        except ProblemError as exc:
            raise StepFailed(_problem_code(exc, "register_failed"), _safe(exc)) from exc


async def _step_register(session: AsyncSession, run: SetupRun, ctx: Ctx) -> str:
    """Register the number for the Cloud API, once, when Meta does not already call it connected.

    Meta's onboarding steps for Embedded Signup end with `POST /{phone_number_id}/register` and a
    six-digit PIN. A number Meta reports `CONNECTED` is left alone (idempotent: a second signup
    never re-registers, so never spends an attempt). Whether Embedded Signup alone registers
    every number is not stated in Meta's docs (unverified), which is why this asks first."""
    account, token = await _connected_credentials(session, run, ctx)
    pnid = _number_id(account)
    graph = ctx.graph()
    try:
        number = await graph.get_number(pnid, token)
    except GraphError as exc:
        raise _graph_failure("register_failed", exc) from exc
    if w1_domain.status_from_meta(number.get("status")) == "connected":
        return "Number already registered"
    pin = await _reserve_registration(ctx, pnid)
    try:
        await graph.register_number(pnid, token, pin)
    except GraphError as exc:
        outcome = _register_outcome(exc)
        if outcome is not None:
            raise outcome from exc
        return "Number already registered"
    return "Number registered with WhatsApp"


async def _step_templates(session: AsyncSession, run: SetupRun, ctx: Ctx) -> str:
    try:
        result = await w2_services.sync_templates(
            session, ctx.organization_id, transport=ctx.transport
        )
    except ProblemError as exc:
        raise StepFailed("templates_sync_failed", _safe(exc)) from exc
    return f"{result.synced} template{'s' if result.synced != 1 else ''} synced"


async def _step_verify(session: AsyncSession, run: SetupRun, ctx: Ctx) -> str:
    account, token = await _connected_credentials(session, run, ctx)
    pnid = _number_id(account)
    graph = ctx.graph()
    try:
        apps = await graph.subscribed_app_ids(account.waba_id, token)
        number = await graph.get_number(pnid, token)
        # Registration takes Meta a moment to show: ask a few times before calling it not ready.
        for _ in range(VERIFY_READS - 1):
            if w1_domain.status_from_meta(number.get("status")) == "connected":
                break
            await asyncio.sleep(VERIFY_WAIT_SECONDS)
            number = await graph.get_number(pnid, token)
    except GraphError as exc:
        raise _graph_failure("verify_failed", exc) from exc
    if str(number.get("id") or "") != pnid:
        raise StepFailed("verify_failed", "Meta returned a different phone number than expected.")
    if ctx.cfg.meta_app_id and str(ctx.cfg.meta_app_id) not in apps:
        raise StepFailed(
            "webhook_not_subscribed", "Meta does not list this app as subscribed to the account."
        )
    if w1_domain.status_from_meta(number.get("status")) != "connected":
        # Not a success: the number cannot send yet. Meta's own status word, sanitised, so the
        # person can act on it (a name still in review, a number still being verified).
        state = w1_domain.status_word(number.get("status"))
        raise StepFailed(
            "number_not_connected",
            f"Meta reports this number as {state}, so it cannot send or receive messages yet. "
            "Finish its setup in WhatsApp Manager, then connect again.",
        )
    await w1_services.apply_numbers(
        session,
        ctx.organization_id,
        waba_id=account.waba_id,
        remote=[number],
        default_phone_number_id=pnid,
        mark_missing=False,
    )
    return "Number connected and receiving events"


_STEP_FUNCS.update(
    {
        "token": _step_token,
        "number": _step_number,
        "webhook": _step_webhook,
        "register": _step_register,
        "templates": _step_templates,
        "verify": _step_verify,
    }
)
