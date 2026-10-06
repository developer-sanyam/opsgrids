"""Operations' door into `services/payment`'s tenant collections (ADR-0024 A4; Fulfilment F10).

Operations owns the order — so it is the one service that knows what an order's payment is *for*
and how much it is (the order's own stored total, never anything a browser sends) — and it checks
the person's permission and the order's state. Payment owns everything about the money: the
tenant's sealed provider keys, the provider Order, the payment state, refunds. Operations holds no
provider secret and no payment state; the one payment fact on the order (`paid_at`) arrives only
by `payment.collections.payment.captured`, never from anything returned here.

Every call is a short-lived service token addressed to payment by name, over its internal API, and
**never inside a database transaction** (`docs/io-outside-transactions.md`): each route reads the
order in a short transaction, closes it, then calls.

**Never put an upstream response body into an exception message** without knowing what it is
(the rule `services/lead/app/clients.py` states). The only bodies relayed here are payment's own
problem `detail` strings on a 4xx — sentences payment writes about the collection, never provider
text or request data — and on a 502 payment's detail carries only the provider's stable error code.
"""

from __future__ import annotations

import uuid
from typing import Any, Final, Protocol

import httpx

from app.config import settings
from platform_core.config import ServiceSettings
from platform_core.errors import (
    ConflictProblem,
    NotFoundProblem,
    ProblemError,
    UpstreamUnavailableProblem,
    ValidationProblem,
)
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("operations.orders.payment_client")

PAYMENT_AUDIENCE: Final = "payment"
#: What an order is to payment's collections (`app.collections.domain.REFERENCE_OPERATIONS_ORDER`
#: over there — a contract string, copied by hand like every cross-service name).
ORDER_REFERENCE: Final = "operations.order"


class CollectionsUnavailable(UpstreamUnavailableProblem):
    """payment could not be asked — unreachable, timed out, or it said the provider could not be
    reached. Safe to retry: every collection call is idempotent on payment's side."""


class ProviderRefused(ProblemError):
    """The payment provider refused (bad merchant keys, an unknown payment). 502."""

    status_code = 502
    problem_type = "provider-refused"
    title = "The payment provider refused the request"


class PayerRequired(ValidationProblem):
    """payment's answer when the connected provider needs the paying customer on every Order
    (Cashfree: a phone) and the checkout was asked for without one. The route answers it by asking
    again with the order's own customer — the one time operations sends a phone to payment, and
    only to a provider that needs one (ADR-0033)."""

    problem_type = "payer-required"
    title = "The payment provider needs the paying customer's details"


class PhoneRequired(ValidationProblem):
    """The provider needs a customer phone and the order has no usable one. Never invented: the
    operator adds the customer's real phone to the order."""

    problem_type = "phone-required"
    title = "A customer phone number is required"


def _problem_kind(response: httpx.Response) -> str | None:
    """The last segment of payment's problem `type` (`payer-required`, …), or None."""
    try:
        body = response.json()
    except ValueError:
        return None
    kind = body.get("type") if isinstance(body, dict) else None
    return kind.rsplit("/", 1)[-1] if isinstance(kind, str) and kind else None


class CollectionsClient(Protocol):
    """What the order routes call. A `Protocol` so a test hands in a double — payment's own
    process cannot be imported here (every service exposes a top-level `app`)."""

    async def list_for_order(
        self, organization_id: uuid.UUID, order_id: uuid.UUID
    ) -> dict[str, Any]: ...

    async def checkout(
        self,
        organization_id: uuid.UUID,
        order_id: uuid.UUID,
        *,
        amount_minor: int,
        currency: str,
        payer: dict[str, str | None] | None = None,
    ) -> dict[str, Any]: ...

    async def sync(
        self, organization_id: uuid.UUID, order_id: uuid.UUID, charge_id: uuid.UUID
    ) -> dict[str, Any]: ...

    async def cancel(
        self,
        organization_id: uuid.UUID,
        order_id: uuid.UUID,
        charge_id: uuid.UUID,
        *,
        actor_id: uuid.UUID | None,
    ) -> dict[str, Any]: ...

    async def refund(
        self,
        organization_id: uuid.UUID,
        order_id: uuid.UUID,
        charge_id: uuid.UUID,
        *,
        amount_minor: int,
        reason: str | None,
        idempotency_key: str,
        actor_id: uuid.UUID | None,
    ) -> dict[str, Any]: ...


def _detail(response: httpx.Response) -> str | None:
    """payment's own problem `detail` — a sentence payment wrote — or None. Never the body."""
    try:
        body = response.json()
    except ValueError:
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail[:300] if isinstance(detail, str) and detail else None


class HttpCollectionsClient:
    """payment's `/internal/v1/organizations/{org}/collections/operations.order/{order}/…`."""

    def __init__(self, base_url: str, cfg: ServiceSettings, *, timeout: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._settings = cfg
        self._timeout = timeout

    def _path(self, organization_id: uuid.UUID, order_id: uuid.UUID, suffix: str = "") -> str:
        return (
            f"/internal/v1/organizations/{organization_id}/collections/"
            f"{ORDER_REFERENCE}/{order_id}{suffix}"
        )

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = {
            "X-Internal-Token": mint_service_token(self._settings, audience=PAYMENT_AUDIENCE)
        }
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as c:
                response = await c.request(method, path, json=json, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("orders.payment_client.unreachable", error=type(exc).__name__)
            raise CollectionsUnavailable("The payment service could not be reached") from None
        status = response.status_code
        if status < 400:
            body = response.json()
            if not isinstance(body, dict):  # pragma: no cover - payment always answers an object
                raise CollectionsUnavailable("The payment service answered unexpectedly")
            return body
        detail = _detail(response)
        log.info("orders.payment_client.refused", status=status)
        if status == 404:
            raise NotFoundProblem(detail or "No such online payment for this order")
        if status == 409:
            raise ConflictProblem(detail or "The order's online payment changed; reload it")
        if status == 422:
            kind = _problem_kind(response)
            if kind == PayerRequired.problem_type:
                raise PayerRequired(detail or "The payment provider needs the customer's phone")
            if kind == PhoneRequired.problem_type:
                raise PhoneRequired(detail or "Add the customer's phone number to this order")
            raise ValidationProblem(detail or "The payment service refused the request")
        if status == 502:
            raise ProviderRefused(detail or "The payment provider refused the request")
        if status in (401, 403):
            # A deployment fault (token audience, caller allow-list), not the user's.
            log.error("orders.payment_client.not_authorized", status=status)
        raise CollectionsUnavailable("The payment service is unavailable; try again")

    async def list_for_order(
        self, organization_id: uuid.UUID, order_id: uuid.UUID
    ) -> dict[str, Any]:
        return await self._request("GET", self._path(organization_id, order_id))

    async def checkout(
        self,
        organization_id: uuid.UUID,
        order_id: uuid.UUID,
        *,
        amount_minor: int,
        currency: str,
        payer: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        """`payer` (the order's customer) only when payment asked for it: it is passed through to
        the provider and is never logged here (no request body is)."""
        body: dict[str, Any] = {"amount_minor": amount_minor, "currency": currency}
        if payer is not None:
            body["payer"] = payer
        return await self._request(
            "POST", self._path(organization_id, order_id, "/checkout"), json=body
        )

    async def sync(
        self, organization_id: uuid.UUID, order_id: uuid.UUID, charge_id: uuid.UUID
    ) -> dict[str, Any]:
        return await self._request(
            "POST", self._path(organization_id, order_id, f"/charges/{charge_id}/sync")
        )

    async def cancel(
        self,
        organization_id: uuid.UUID,
        order_id: uuid.UUID,
        charge_id: uuid.UUID,
        *,
        actor_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            self._path(organization_id, order_id, f"/charges/{charge_id}/cancel"),
            json={"actor_id": str(actor_id) if actor_id else None},
        )

    async def refund(
        self,
        organization_id: uuid.UUID,
        order_id: uuid.UUID,
        charge_id: uuid.UUID,
        *,
        amount_minor: int,
        reason: str | None,
        idempotency_key: str,
        actor_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            self._path(organization_id, order_id, f"/charges/{charge_id}/refunds"),
            json={
                "amount_minor": amount_minor,
                "reason": reason,
                "idempotency_key": idempotency_key,
                "actor_id": str(actor_id) if actor_id else None,
            },
        )


def get_collections_client() -> CollectionsClient:
    """The configured client. A FastAPI dependency so a test overrides it."""
    cfg = settings()
    return HttpCollectionsClient(
        cfg.payment_service_url, cfg, timeout=cfg.payment_call_timeout_seconds
    )
