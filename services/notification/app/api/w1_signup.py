"""WhatsApp Embedded Signup, numbers and connection health (slice W1).

Mounted beside `engage.py`'s router under `/engage/whatsapp/...`, gated the same way: the `engage`
entitlement plus Engage's own `engage.setup.whatsapp.read` / `.write` permissions, floored at ORG.
Every route resolves the actor and the tenant through `require(...)` and the tenant session before
it reads anything.

**Nothing here returns a credential.** `signup/complete` takes the popup's one-time code, seals it
on a run row and returns 202; the exchange and every other Meta call happen in the worker
(`app/w1_setup.py`) after the request has committed. The one route that calls Meta on a request
(`GET numbers`, when its cache is stale) binds two short transactions of its own around the call,
the idiom `w2_templates.sync_whatsapp_templates` uses, so a slow Meta never pins a connection.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import w1_selectors, w1_services, w2_webhooks
from app.config import settings
from app.w1_graph import GraphError, MetaGraph
from app.w1_schemas import (
    HealthOut,
    NumbersOut,
    OkOut,
    SendTestMessageIn,
    SendTestMessageOut,
    SignupCompleteIn,
    SignupCompleteOut,
    SignupConfigOut,
    SignupStatusOut,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.cache import enforce_rate_limit
from platform_core.config import ServiceSettings
from platform_core.db import (
    assert_tenant,
    get_directory_session,
    get_session,
    get_tenant_session,
    set_tenant,
)
from platform_core.errors import RateLimitedProblem
from platform_core.logging import get_logger

log = get_logger("notification.api.w1_signup")

router = APIRouter(tags=["engage"])

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
RawSessionDep = Annotated[AsyncSession, Depends(get_session)]
DirectorySessionDep = Annotated[AsyncSession, Depends(get_directory_session)]
Entitled = Depends(require_product("engage"))

WhatsAppRead = Annotated[
    ScopeContext, Depends(require("engage.setup.whatsapp.read", minimum=Scope.ORG))
]
WhatsAppWrite = Annotated[
    ScopeContext, Depends(require("engage.setup.whatsapp.write", minimum=Scope.ORG))
]

#: Per organization per minute. `signup/complete` spends a single-use code and starts a job;
#: `test-message` sends a real message to an arbitrary number; the numbers refresh spends Meta's
#: per-WABA rate limit. Not configuration: the right numbers are Meta's, not a deployment's.
SIGNUP_PER_MINUTE = 6
TEST_MESSAGE_PER_MINUTE = 5

# A `phone_number_quality_update` webhook reaches the numbers cache through this registration; the
# webhook module (W2) owns the receive path and calls whatever registers here.
w2_webhooks.register_number_quality_handler(w1_services.update_number_quality)


def graph_transport() -> httpx.AsyncBaseTransport | None:
    """The HTTP transport for the one Graph call a request makes. `None` is the real network;
    tests override this dependency with an `httpx.MockTransport`."""
    return None


GraphTransportDep = Annotated[httpx.AsyncBaseTransport | None, Depends(graph_transport)]


# --------------------------------------------------------------------- signup


@router.get(
    "/engage/whatsapp/signup/config",
    response_model=SignupConfigOut,
    summary="What the browser needs to open Meta's Embedded Signup",
    dependencies=[Entitled],
)
async def signup_config(ctx: WhatsAppRead) -> SignupConfigOut:
    """`enabled: false` (with `reason: not_configured`) when the platform has no Meta app set up,
    so the screen shows an honest state instead of a button that cannot work. The app secret is
    never part of this."""
    return w1_services.signup_config(settings())


@router.post(
    "/engage/whatsapp/signup/complete",
    response_model=SignupCompleteOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Finish Embedded Signup: start the background setup",
    dependencies=[Entitled],
)
async def signup_complete(
    body: SignupCompleteIn, session: SessionDep, ctx: WhatsAppWrite, request: Request
) -> SignupCompleteOut:
    """202: the setup is persisted and queued, not done. Poll `GET signup/{setup_id}`. A repeat of
    the same `code` returns the setup it already started."""
    await enforce_rate_limit(
        f"engage.wa_signup:{ctx.organization_id}", limit=SIGNUP_PER_MINUTE, window_seconds=60
    )
    run = await w1_services.start_signup(
        session,
        ctx,
        code=body.code,
        waba_id=body.waba_id,
        phone_number_id=body.phone_number_id,
        request=request,
    )
    return SignupCompleteOut(setup_id=run.id, status="running")


@router.get(
    "/engage/whatsapp/signup/{setup_id}",
    response_model=SignupStatusOut,
    summary="How far a WhatsApp setup has got",
    dependencies=[Entitled],
)
async def signup_status(
    setup_id: uuid.UUID, session: SessionDep, ctx: WhatsAppRead
) -> SignupStatusOut:
    return w1_services.run_out(await w1_services.get_run_or_404(session, ctx, setup_id))


# --------------------------------------------------------------------- numbers


@router.get(
    "/engage/whatsapp/numbers",
    response_model=NumbersOut,
    summary="The phone numbers on this organization's WhatsApp Business Account",
    dependencies=[Entitled],
)
async def list_numbers(
    session: RawSessionDep, ctx: WhatsAppRead, request: Request, transport: GraphTransportDep
) -> NumbersOut:
    """The cached list, refreshed from Meta first when it is older than ten minutes. A refresh
    that fails does not fail the read: the cache is served, and a refused token is recorded so
    the health card says so."""
    cfg: ServiceSettings = request.app.state.settings
    org = ctx.organization_id

    async def _bind() -> None:
        await set_tenant(session, org)
        if not cfg.is_production:
            await assert_tenant(session, org)

    async with session.begin():
        await _bind()
        rows = await w1_selectors.numbers(session, org)
        inputs = None
        if w1_services.numbers_stale(rows):
            inputs = await w1_services.numbers_refresh_inputs(session, org)
        if inputs is None:
            return NumbersOut(items=[w1_services.number_out(r) for r in rows])

    waba_id, token, default_id = inputs
    remote: list[dict[str, Any]] | None = None
    check_state: str | None = None
    try:
        # Bounds what a failing Meta costs: past the limit the cache is served as it is.
        await enforce_rate_limit(f"engage.wa_numbers:{org}", limit=6, window_seconds=60)
    except RateLimitedProblem:
        pass
    else:
        try:
            remote = await MetaGraph(settings(), transport=transport).list_numbers(waba_id, token)
            check_state = "valid"
        except GraphError as exc:
            log.warning("w1.numbers_refresh_failed", status=exc.status, code=exc.code)
            if exc.status in (401, 403) or exc.code == 190:
                check_state = "invalid"

    async with session.begin():
        await _bind()
        if remote is not None:
            fresh = await w1_services.apply_numbers(
                session,
                org,
                waba_id=waba_id,
                remote=remote,
                default_phone_number_id=default_id,
                now=datetime.now(UTC),
            )
        else:
            fresh = await w1_selectors.numbers(session, org)
        if check_state is not None:
            await w1_services.record_token_check(session, org, state=check_state)
        return NumbersOut(items=[w1_services.number_out(r) for r in fresh])


@router.post(
    "/engage/whatsapp/numbers/{number_id}/default",
    response_model=OkOut,
    summary="Send from this number by default",
    dependencies=[Entitled],
)
async def set_default(
    number_id: uuid.UUID,
    session: SessionDep,
    directory: DirectorySessionDep,
    ctx: WhatsAppWrite,
    request: Request,
) -> OkOut:
    await w1_services.set_default_number(
        session, ctx, directory=directory, number_id=number_id, request=request
    )
    return OkOut()


# --------------------------------------------------------------------- health, test, disconnect


@router.get(
    "/engage/whatsapp/health",
    response_model=HealthOut,
    summary="Is WhatsApp connected and healthy",
    dependencies=[Entitled],
)
async def health(session: SessionDep, ctx: WhatsAppRead) -> HealthOut:
    """From our own tables only; never calls Meta, so it is cheap to poll."""
    return await w1_services.health_snapshot(session, ctx.organization_id)


@router.post(
    "/engage/whatsapp/test-message",
    response_model=SendTestMessageOut,
    summary="Send a template message to one number to prove the connection",
    dependencies=[Entitled],
)
async def test_message(
    body: SendTestMessageIn, session: SessionDep, ctx: WhatsAppWrite
) -> SendTestMessageOut:
    """Goes through the consent decision and the organization's own connection like any send.
    409 `channel_not_connected` when there is no connection, 409 `consent_denied` when the
    recipient may not be messaged."""
    await enforce_rate_limit(
        f"engage.wa_test_message:{ctx.organization_id}",
        limit=TEST_MESSAGE_PER_MINUTE,
        window_seconds=60,
    )
    message_id = await w1_services.stage_test_message(
        session,
        ctx,
        to=body.to,
        template_name=body.template_name,
        language=body.language,
    )
    return SendTestMessageOut(message_id=message_id)


@router.post(
    "/engage/whatsapp/disconnect",
    response_model=OkOut,
    summary="Disconnect WhatsApp and wipe the stored token",
    dependencies=[Entitled],
)
async def disconnect(session: SessionDep, ctx: WhatsAppWrite, request: Request) -> OkOut:
    await w1_services.disconnect(session, ctx, request=request)
    return OkOut()
