"""The Embedded Signup worker (slice W1): runs one persisted setup, one step at a time.

`POST /engage/whatsapp/signup/complete` commits a `w1_setup_runs` row and a
`notification.whatsapp.setup_requested` outbox event together. The relay publishes it after
commit and `consumers.run_whatsapp_setup` calls `run_setup` here, on the worker's own tenant scope
(one transaction per step, the tenant GUC bound per message, the same shape as
`consumers.expand_campaign_send`). The five steps, in order:

1. **token**: exchange the popup's one-time code for a business token; seal it on the run and wipe
   the code.
2. **number**: discover the WABA and its numbers, prove the token controls the chosen one
   (`whatsapp_provider.verify_ownership`), then store the sealed token, the WABA and the number in
   the organization's `provider_accounts` row and fill the numbers cache.
3. **webhook**: subscribe the app to the WABA (`POST /{waba}/subscribed_apps`).
4. **templates**: `w2_services.sync_templates`.
5. **verify**: read the number and the subscription back from Meta.

Every step's result is committed before the next starts, so `GET signup/{id}` shows real progress
and a redelivered event resumes at the first step that is not `done`. A failure marks that step
and the run `failed` with a machine-readable code and stops; steps after it stay `waiting`. A
Graph call happens inside its step's transaction (a Graph timeout is ten seconds); that is the
worker's own pool, never a request's.

**Secrets.** The code and the token are sealed at rest, opened only for the call that needs them,
wiped from the run as soon as they are spent (and on any failure), and never in a log line, a
`detail` string or an audit row. Failure details are fixed sentences or Meta's own end-user
message, never an exception's text.
"""

from __future__ import annotations

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
from app.w1_graph import GraphError, MetaGraph
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
    ctx = Ctx(settings(), transport, organization_id, setup_id)

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
        now = datetime.now(UTC)
        _set(run, key, "failed", failure.detail)
        run.status = "failed"
        run.error_code = failure.code
        run.finished_at = now
        run.code_enc = None
        run.token_enc = None
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
    if not run.code_enc:
        raise StepFailed("code_exchange_failed", "The authorization code is no longer available.")
    code = pii.open_sealed(run.code_enc, settings=ctx.cfg)
    try:
        token = await ctx.graph().exchange_code(code)
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
            "waba_ambiguous", "More than one WhatsApp Business Account was shared; pick one."
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
        )
        session.add(account)
    else:
        account.waba_id = waba_id
        account.phone_number_id = phone_number_id
        account.display_number = display
        account.access_token_enc = sealed
        account.status = "connected"
        account.updated_by = run.started_by or SYSTEM_ACTOR
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
        session, org, state="valid", expires_at=expires_at, setup_id=run.id
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
    return f"Registered {len(remote)} number{'s' if len(remote) != 1 else ''}"


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
    graph = ctx.graph()
    try:
        number = await graph.get_number(account.phone_number_id, token)
        apps = await graph.subscribed_app_ids(account.waba_id, token)
    except GraphError as exc:
        raise _graph_failure("verify_failed", exc) from exc
    if str(number.get("id") or "") != account.phone_number_id:
        raise StepFailed("verify_failed", "Meta returned a different phone number than expected.")
    if ctx.cfg.meta_app_id and str(ctx.cfg.meta_app_id) not in apps:
        raise StepFailed(
            "webhook_not_subscribed", "Meta does not list this app as subscribed to the account."
        )
    await w1_services.apply_numbers(
        session,
        ctx.organization_id,
        waba_id=account.waba_id,
        remote=[number],
        default_phone_number_id=account.phone_number_id,
        mark_missing=False,
    )
    status = w1_domain.status_from_meta(number.get("status"))
    if status == "connected":
        return "Number connected and receiving events"
    return "Connected; the number is still pending verification with Meta"


_STEP_FUNCS.update(
    {
        "token": _step_token,
        "number": _step_number,
        "webhook": _step_webhook,
        "templates": _step_templates,
        "verify": _step_verify,
    }
)
