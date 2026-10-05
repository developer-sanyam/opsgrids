"""Tenant collections — the public surface (ADR-0024 §8, ADR-0030).

Two kinds of route, authenticated completely differently:

* **A tenant admin's** (`/payment/collections/...`): connect, rotate, verify or remove the
  organization's own payment-provider account (Razorpay or Cashfree), and run the read-only
  reconciliation report. A person behind the gateway, guarded by permissions minted in the
  operations catalogue and enforced here — the same "minted there, enforced here" shape core's
  `identity.sso.*` codes take into auth-service. The `operations` product must be active:
  collections serves its orders today. **Changing where a business's money goes** — connect,
  rotate, verify, disconnect — also needs a session that signed in with a one-time code
  (`amr` has `otp`, ADR-0023, the rule the put-away location reveal uses): a stolen
  password-only session cannot repoint a merchant's customers' payments.
* **The merchant's provider** (`/webhooks/collections?account=<id>`, and the original
  `/webhooks/razorpay/collections` kept forever as an alias): no token, no person — authenticated
  by the provider's signature over the raw body, under **that merchant's own** secret. The account
  is named in the URL because the secret has to be found before the body can be trusted; the
  account's organization is looked up on a narrow bypass read (`selectors.merchant_account_org`)
  and everything after it runs bound to that tenant. **The provider is the account row's**, never
  the path's: both paths reach the same handler.

Nothing here ever returns a secret. The browser only ever sees a Razorpay key id (in a checkout,
through operations), a Cashfree App ID here, and masked hints of the secrets.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.api.webhooks import TRANSIENT_DATABASE_ERRORS
from app.collections import domain, selectors, services
from app.config import settings
from app.deps import CollectionsProviderFactory, get_collections_provider_factory
from app.models import MerchantAccount
from app.schemas import (
    CollectionWiresOut,
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
from platform_core.errors import PayloadTooLargeProblem, UpstreamUnavailableProblem
from platform_core.logging import get_logger

log = get_logger("payment.collections.api")

router = APIRouter(prefix="/payment/collections", tags=["collections"])
webhook_router = APIRouter(prefix="/webhooks", tags=["webhooks"])

TenantSessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
#: The reconciliation report and the verification call the provider, so they must not run inside
#: the request-long tenant transaction — they open their own short ones around the call.
RawSessionDep = Annotated[AsyncSession, Depends(get_session)]
FactoryDep = Annotated[CollectionsProviderFactory, Depends(get_collections_provider_factory)]
Entitled = Depends(require_product("operations"))

AccountRead = Annotated[
    ScopeContext,
    Depends(require("operations.payments.merchant_account.read", minimum=Scope.ORG)),
]
#: Connect, rotate, verify and disconnect: the write permission **and** a one-time-code session
#: (`otp` alone, not the `stepup` marker: the operations SPA has no step-up flow yet — ADR-0023
#: risk 5 — so the marker would make the controls unusable; the same call the put-away reveal
#: made). The UI turns the 403 into "sign in again with your authenticator code".
AccountWrite = Annotated[
    ScopeContext,
    Depends(require("operations.payments.merchant_account.write", minimum=Scope.ORG, amr={"otp"})),
]
ReconciliationRead = Annotated[
    ScopeContext,
    Depends(require("operations.payments.reconciliation.read", minimum=Scope.ORG)),
]

#: The merchant webhook's neutral path, as the gateway forwards it and as a merchant registers it
#: (with `?account=<id>` appended) in their provider's dashboard.
COLLECTIONS_WEBHOOK_PATH = domain.WEBHOOK_PATH
#: The path Razorpay merchants registered before Cashfree: an alias onto the same handler.
LEGACY_RAZORPAY_WEBHOOK_PATH = domain.LEGACY_RAZORPAY_WEBHOOK_PATH
#: The largest merchant webhook body this service will buffer. Provider bodies are a few
#: kilobytes; the gateway enforces the same ceiling first (`routes.prod.json`), this is the second
#: line.
MAX_COLLECTION_WEBHOOK_BODY_BYTES = 256 * 1024


def _wires() -> CollectionWiresOut:
    cfg = settings()
    return CollectionWiresOut(
        razorpay=cfg.collections_wire_for(domain.PROVIDER_RAZORPAY),
        cashfree=cfg.collections_wire_for(domain.PROVIDER_CASHFREE),
    )


def _account_out(
    account: MerchantAccount | None, *, last_webhook_at: datetime | None = None
) -> MerchantAccountOut:
    cfg = settings()
    if account is None:
        return MerchantAccountOut(
            configured=False,
            live_keys_allowed=cfg.collections_allow_live_keys,
            wire=cfg.collections_wire,
            wires=_wires(),
        )
    spec = domain.spec_for(account.provider)
    return MerchantAccountOut(
        configured=True,
        id=account.id,
        provider=account.provider,
        key_id=account.key_id,
        mode=account.mode,
        key_secret_masked=account.key_secret_masked,
        webhook_secret_masked=account.webhook_secret_masked if spec.has_webhook_secret else None,
        webhook_path=f"{spec.webhook_path}?account={account.id}",
        webhook_url=domain.webhook_url(
            cfg.collections_public_api_base, account.id, spec.webhook_path
        ),
        webhook_events=list(spec.webhook_events),
        rotated_at=account.rotated_at,
        created_at=account.created_at,
        connection_state=services.connection_state(account),
        verified_at=account.verified_at,
        last_error_code=account.last_error_code,
        last_webhook_at=last_webhook_at,
        live_keys_allowed=cfg.collections_allow_live_keys,
        wire=cfg.collections_wire,
        wires=_wires(),
    )


@router.get(
    "/merchant-account",
    response_model=MerchantAccountOut,
    dependencies=[Entitled],
    summary="The organization's payment-provider connection (masked)",
)
async def get_merchant_account(ctx: AccountRead, session: TenantSessionDep) -> MerchantAccountOut:
    account = await selectors.live_merchant_account(session, ctx.organization_id)
    last = (
        await selectors.last_webhook_at(session, ctx.organization_id, account)
        if account is not None
        else None
    )
    return _account_out(account, last_webhook_at=last)


@router.put(
    "/merchant-account",
    response_model=MerchantAccountOut,
    dependencies=[Entitled],
    summary="Connect the organization's Razorpay or Cashfree account, or rotate its credentials",
)
async def put_merchant_account(
    payload: MerchantAccountIn, ctx: AccountWrite, session: TenantSessionDep, request: Request
) -> MerchantAccountOut:
    """Database only: the credentials are checked for shape and sealed, and the account is
    `unverified` until `POST …/merchant-account/verify` asks the provider."""
    account = await services.set_merchant_account(
        session,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        provider=payload.provider,
        key_id=payload.key_id,
        key_secret=payload.key_secret.get_secret_value(),
        webhook_secret=(
            payload.webhook_secret.get_secret_value() if payload.webhook_secret else None
        ),
        mode=payload.mode,
        request=request,
    )
    log.info(
        "payment.merchant_account.saved",
        organization_id=str(ctx.organization_id),
        account_id=str(account.id),
        provider=account.provider,
        mode=account.mode,
    )
    return _account_out(account)


@router.post(
    "/merchant-account/verify",
    response_model=MerchantAccountOut,
    dependencies=[Entitled],
    summary="Ask the provider whether the connected account's credentials work",
)
async def verify_merchant_account(
    ctx: AccountWrite, session: RawSessionDep, factory: FactoryDep, request: Request
) -> MerchantAccountOut:
    """Claim (open the key secret, in a short transaction), probe (one authenticated, read-only
    request to the provider, with no transaction open, within `collections_call_timeout_seconds`),
    record (`verified_at` or `last_error_code`, re-checked under the row lock, audited). A provider
    that refuses the credentials is recorded and then answered 422 `credentials-rejected`; one
    that cannot be reached records nothing and is a 503."""
    async with session.begin():
        await services.bind(session, ctx.organization_id)
        ticket = await services.claim_verification(session, organization_id=ctx.organization_id)
    outcome = await services.run_probe(factory, ticket)
    async with session.begin():
        await services.bind(session, ctx.organization_id)
        account = await services.record_verification(
            session,
            organization_id=ctx.organization_id,
            ticket=ticket,
            outcome=outcome,
            actor_id=ctx.principal.sub,
            request=request,
        )
        last = await selectors.last_webhook_at(session, ctx.organization_id, account)
        out = _account_out(account, last_webhook_at=last)
    if not outcome.accepted:
        hint = (
            " Check that the keys are for the environment you chose (Sandbox or Production)."
            if account.provider == domain.PROVIDER_CASHFREE
            else " Check that the key id and key secret are a pair, in the same mode."
        )
        raise services.CredentialsRejectedProblem(
            f"The payment provider did not accept these credentials ({outcome.error_code}).{hint}"
        )
    return out


@router.delete(
    "/merchant-account",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Entitled],
    summary="Disconnect the organization's payment-provider account",
)
async def delete_merchant_account(
    ctx: AccountWrite, session: TenantSessionDep, request: Request
) -> Response:
    """Refused (409, with the count) while any online payment taken into the account is open."""
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
    "/collections",
    response_model=WebhookAck,
    summary="A tenant's payment-provider webhook — per-merchant signature, idempotent, reconciling",
)
async def collections_webhook(
    request: Request,
    session: RawSessionDep,
    factory: FactoryDep,
    account: Annotated[str, Query(min_length=1, max_length=64)],
) -> WebhookAck:
    """One merchant's webhook, from whichever provider **the account row** names (Razorpay or
    Cashfree) — never from this path or the body. The body is read raw — the signature is over
    exactly these bytes. An unknown or removed account, a bad signature and an unparseable
    account id all answer the same 400, so a prober learns nothing about which accounts exist."""
    return await _merchant_webhook(request, session, factory, account)


@webhook_router.post(
    "/razorpay/collections",
    response_model=WebhookAck,
    summary="The original path of a tenant's webhook — an alias of /webhooks/collections",
)
async def merchant_webhook(
    request: Request,
    session: RawSessionDep,
    factory: FactoryDep,
    account: Annotated[str, Query(min_length=1, max_length=64)],
) -> WebhookAck:
    """Kept forever: every Razorpay merchant registered this path. The same handler as
    `/webhooks/collections`; the provider is the account row's, so a delivery here for a Cashfree
    account is verified as Cashfree's."""
    return await _merchant_webhook(request, session, factory, account)


async def _merchant_webhook(
    request: Request,
    session: AsyncSession,
    factory: CollectionsProviderFactory,
    account: str,
) -> WebhookAck:
    declared = request.headers.get("content-length")
    if (
        declared is not None
        and declared.isdigit()
        and int(declared) > MAX_COLLECTION_WEBHOOK_BODY_BYTES
    ):
        raise PayloadTooLargeProblem("webhook body too large")
    body = await request.body()
    if len(body) > MAX_COLLECTION_WEBHOOK_BODY_BYTES:
        raise PayloadTooLargeProblem("webhook body too large")
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
                headers={name.lower(): value for name, value in request.headers.items()},
                body=body,
                is_production=cfg.is_production,
                request=request,
            )
    except TRANSIENT_DATABASE_ERRORS as exc:
        log.error("payment.collections_webhook.unavailable", error=type(exc).__name__)
        raise UpstreamUnavailableProblem("temporarily unable to record the webhook") from exc
    log.info(
        "payment.collections_webhook",
        organization_id=str(organization_id),
        path=request.url.path,
        provider_event_id=ack.provider_event_id,
        duplicate=ack.duplicate,
        reconciled=ack.reconciled,
        ignored_reason=ack.ignored_reason,
    )
    return ack
