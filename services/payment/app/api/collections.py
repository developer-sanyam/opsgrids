"""Tenant collections — the public surface (ADR-0024 §8).

Two kinds of route, authenticated completely differently:

* **A tenant admin's** (`/payment/collections/...`): connect, rotate or remove the organization's
  own Razorpay account, and run the read-only reconciliation report. A person behind the gateway,
  guarded by permissions minted in the operations catalogue and enforced here — the same "minted
  there, enforced here" shape core's `identity.sso.*` codes take into auth-service. The
  `operations` product must be active: collections serves its orders today.
* **The merchant's provider** (`/webhooks/razorpay/collections?account=<id>`): no token, no
  person — authenticated by Razorpay's HMAC over the raw body, under **that merchant's own**
  webhook secret. The account is named in the URL because the secret has to be found before the
  body can be trusted; the account's organization is looked up on a narrow bypass read
  (`selectors.merchant_account_org`) and everything after it runs bound to that tenant.

Nothing here ever returns a secret. The browser only ever sees the public key id (in a checkout,
through operations) and masked hints of the two secrets.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.api.webhooks import (
    EVENT_ID_HEADER,
    SIGNATURE_HEADER,
    TRANSIENT_DATABASE_ERRORS,
    read_capped_body,
)
from app.collections import selectors, services
from app.config import settings
from app.deps import CollectionsProviderFactory, get_collections_provider_factory
from app.models import MerchantAccount
from app.schemas import (
    MerchantAccountIn,
    MerchantAccountOut,
    ReconciliationOut,
    ReconciliationRowOut,
    WebhookAck,
)
from app.services import WebhookSignatureProblem
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.config import ServiceSettings
from platform_core.db import get_outbox_sessionmaker, get_session, get_tenant_session
from platform_core.errors import UpstreamUnavailableProblem
from platform_core.logging import get_logger

log = get_logger("payment.collections.api")

router = APIRouter(prefix="/payment/collections", tags=["collections"])
webhook_router = APIRouter(prefix="/webhooks", tags=["webhooks"])

TenantSessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
#: The reconciliation report calls the provider, so it must not run inside the request-long
#: tenant transaction — it opens its own short ones around the calls (claim → call).
RawSessionDep = Annotated[AsyncSession, Depends(get_session)]
FactoryDep = Annotated[CollectionsProviderFactory, Depends(get_collections_provider_factory)]
Entitled = Depends(require_product("operations"))

AccountRead = Annotated[
    ScopeContext,
    Depends(require("operations.payments.merchant_account.read", minimum=Scope.ORG)),
]
AccountWrite = Annotated[
    ScopeContext,
    Depends(require("operations.payments.merchant_account.write", minimum=Scope.ORG)),
]
ReconciliationRead = Annotated[
    ScopeContext,
    Depends(require("operations.payments.reconciliation.read", minimum=Scope.ORG)),
]

#: The merchant webhook's path, as the gateway forwards it and as a merchant registers it (with
#: `?account=<id>` appended) in their Razorpay dashboard.
COLLECTIONS_WEBHOOK_PATH = "/api/v1/webhooks/razorpay/collections"
#: The largest merchant webhook body this service will buffer. Razorpay's are a few kilobytes;
#: the gateway enforces the same ceiling first (`routes.prod.json`), this is the second line.
MAX_COLLECTION_WEBHOOK_BODY_BYTES = 256 * 1024


def _account_out(account: MerchantAccount | None) -> MerchantAccountOut:
    cfg = settings()
    if account is None:
        return MerchantAccountOut(
            configured=False,
            live_keys_allowed=cfg.collections_allow_live_keys,
            wire=cfg.collections_wire,
        )
    return MerchantAccountOut(
        configured=True,
        id=account.id,
        provider=account.provider,
        key_id=account.key_id,
        mode=account.mode,
        key_secret_masked=account.key_secret_masked,
        webhook_secret_masked=account.webhook_secret_masked,
        webhook_path=f"{COLLECTIONS_WEBHOOK_PATH}?account={account.id}",
        rotated_at=account.rotated_at,
        created_at=account.created_at,
        live_keys_allowed=cfg.collections_allow_live_keys,
        wire=cfg.collections_wire,
    )


@router.get(
    "/merchant-account",
    response_model=MerchantAccountOut,
    dependencies=[Entitled],
    summary="The organization's payment-provider connection (masked)",
)
async def get_merchant_account(ctx: AccountRead, session: TenantSessionDep) -> MerchantAccountOut:
    account = await selectors.live_merchant_account(session, ctx.organization_id)
    return _account_out(account)


@router.put(
    "/merchant-account",
    response_model=MerchantAccountOut,
    dependencies=[Entitled],
    summary="Connect the organization's Razorpay account, or rotate its credentials",
)
async def put_merchant_account(
    payload: MerchantAccountIn, ctx: AccountWrite, session: TenantSessionDep, request: Request
) -> MerchantAccountOut:
    account = await services.set_merchant_account(
        session,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        provider=payload.provider,
        key_id=payload.key_id,
        key_secret=payload.key_secret.get_secret_value(),
        webhook_secret=payload.webhook_secret.get_secret_value(),
        request=request,
    )
    log.info(
        "payment.merchant_account.saved",
        organization_id=str(ctx.organization_id),
        account_id=str(account.id),
        mode=account.mode,
    )
    return _account_out(account)


@router.delete(
    "/merchant-account",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Entitled],
    summary="Disconnect the organization's payment-provider account",
)
async def delete_merchant_account(
    ctx: AccountWrite, session: TenantSessionDep, request: Request
) -> Response:
    await services.remove_merchant_account(
        session, organization_id=ctx.organization_id, actor_id=ctx.principal.sub, request=request
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/reconciliation",
    response_model=ReconciliationOut,
    dependencies=[Entitled],
    summary="Compare recent online payments with the provider's records (read-only)",
)
async def reconciliation(
    ctx: ReconciliationRead, session: RawSessionDep, factory: FactoryDep
) -> ReconciliationOut:
    """Asks the provider, server to server, about the most recent collections and lists every
    disagreement. **Corrects nothing** — a status check from the order, or the provider's own
    dashboard, is what settles a mismatch."""
    async with session.begin():
        await services.bind(session, ctx.organization_id)
        claim = await services.claim_reconciliation(
            session,
            organization_id=ctx.organization_id,
            limit=settings().collections_reconciliation_limit,
        )
    rows = await services.run_reconciliation(factory, claim)
    out = [
        ReconciliationRowOut(
            charge_id=row.item.charge_id,
            reference_type=row.item.reference_type,
            reference_id=row.item.reference_id,
            state=row.item.state,
            amount_minor=row.item.amount_minor,
            currency=row.item.currency,
            provider_order_id=row.item.provider_order_id,
            provider_payment_id=row.item.provider_payment_id,
            refunded_minor=row.item.refunded_minor,
            provider_status=row.provider_status,
            mismatches=row.mismatches,
            lookup_failed=row.lookup_failed,
        )
        for row in rows
    ]
    mismatched = sum(1 for r in out if r.mismatches)
    unchecked = sum(1 for r in out if r.lookup_failed)
    if mismatched:
        log.warning(
            "payment.collection.reconciliation_mismatches",
            organization_id=str(ctx.organization_id),
            mismatched=mismatched,
        )
    return ReconciliationOut(
        generated_at=datetime.now(UTC),
        checked=len(out) - unchecked,
        mismatched=mismatched,
        unchecked=unchecked,
        rows=out,
    )


# ============================================================================ merchant webhook


async def _organization_of_account(account_id: uuid.UUID) -> uuid.UUID | None:
    """The pre-tenant lookup, on the BYPASSRLS outbox role's column grant (`id`,
    `organization_id`, `deleted_at` of `merchant_accounts` — never a secret). Its own short
    transaction; nothing else runs on this session."""
    maker = get_outbox_sessionmaker()
    async with maker() as bypass, bypass.begin():
        return await selectors.merchant_account_org(bypass, account_id)


@webhook_router.post(
    "/razorpay/collections",
    response_model=WebhookAck,
    summary="A tenant's Razorpay webhook — per-merchant signature, idempotent, reconciling",
)
async def merchant_webhook(
    request: Request,
    session: RawSessionDep,
    factory: FactoryDep,
    account: Annotated[str, Query(min_length=1, max_length=64)],
) -> WebhookAck:
    """One merchant's Razorpay webhook. The body is read raw — the signature is over exactly
    these bytes. An unknown or removed account, a bad signature and an unparseable account id all
    answer the same 400, so a prober learns nothing about which accounts exist."""
    body = await read_capped_body(request, MAX_COLLECTION_WEBHOOK_BODY_BYTES)
    try:
        account_id = uuid.UUID(account)
    except ValueError:
        raise WebhookSignatureProblem("could not verify the webhook signature") from None
    cfg: ServiceSettings = request.app.state.settings

    try:
        organization_id = await _organization_of_account(account_id)
        if organization_id is None:
            raise WebhookSignatureProblem("could not verify the webhook signature")
        async with session.begin():
            ack = await services.ingest_merchant_webhook(
                session,
                factory,
                organization_id=organization_id,
                account_id=account_id,
                signature=request.headers.get(SIGNATURE_HEADER),
                body=body,
                event_id=request.headers.get(EVENT_ID_HEADER),
                is_production=cfg.is_production,
                request=request,
            )
    except TRANSIENT_DATABASE_ERRORS as exc:
        log.error("razorpay.collections_webhook.unavailable", error=type(exc).__name__)
        raise UpstreamUnavailableProblem("temporarily unable to record the webhook") from exc
    log.info(
        "razorpay.collections_webhook",
        organization_id=str(organization_id),
        provider_event_id=ack.provider_event_id,
        duplicate=ack.duplicate,
        reconciled=ack.reconciled,
        ignored_reason=ack.ignored_reason,
    )
    return ack
