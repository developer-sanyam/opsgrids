"""A merchant's PhonePe PG account (Standard Checkout **V2**, OAuth client credentials), behind the
standalone `CollectionsProvider` seam (ADR-0033).

Written against PhonePe's public V2 reference (developer.phonepe.com, "Standard Checkout → API
reference": Authorization, Create Payment, Order Status, Refund, Refund Status, Webhook Handling)
and its official Python SDK (`phonepe_sdk` 2.1.5: base URLs, `is_callback_valid`). Nothing here is
the V1 salt-key / `X-VERIFY` API.

* **Auth.** `POST {oauth}/v1/oauth/token` (form-encoded `client_id`, `client_version`,
  `client_secret`, `grant_type=client_credentials`) gives an `access_token` sent as
  `Authorization: O-Bearer <token>`. The token is fetched once per adapter — and an adapter lives
  for one call (built per call from the merchant's sealed keys, never cached), so it is never kept
  past the call either. A refusal at the token endpoint is the merchant's credentials being wrong
  (`ProviderMisconfigured`).
* **The Order.** `merchantOrderId` is the collection's own id (36 characters, hyphens allowed;
  PhonePe takes 63, `_` and `-`) and it is what this platform records as the Order id. Looked up
  first (`GET /checkout/v2/order/{id}/status`); created (`POST /checkout/v2/pay`, `PG_CHECKOUT`,
  amount in paise, `expireAfter` clamped to PhonePe's 300-3600 seconds, the return page as
  `merchantUrls.redirectUrl`) when PhonePe holds none. The answer's `redirectUrl` is the browser's
  handle: a token-bearing URL, **in memory only**, never stored. An Order still `PENDING` is asked
  for a fresh one by posting the same `merchantOrderId` again — PhonePe documents that a duplicate
  of an Order no longer in `CREATED` is refused `BAD_REQUEST`, so this never makes a second Order;
  the refusal is "can no longer be reopened" (sandbox to confirm what a `CREATED` duplicate
  answers). A reopen of a recorded Order the account does not hold is refused, never re-minted.
* **Payments.** `paymentDetails[]` of the status answer (`details=true`): `COMPLETED` is captured,
  `FAILED` failed (with `errorCode`), `PENDING` is no observation.
* **Refunds.** `POST /payments/v2/refund` with our own `merchantRefundId` (`rfd` and 36 hex). The
  reference does not say what a duplicate id answers, so a refusal is followed by
  `GET /payments/v2/refund/{id}/status`: the same refund, for the same amount, is used; anything
  else re-raises the refusal.
* **Webhooks.** PhonePe's documented scheme (and the SDK's `is_callback_valid`) is an
  `Authorization` header equal to `hex(SHA256("<username>:<password>"))`, the pair the merchant
  sets on the webhook in PhonePe's dashboard. Compared in constant time, failing closed. **It does
  not cover the body**: it is a static shared credential over TLS, not a per-body signature (the
  HMAC option PhonePe mentions names no algorithm or signed string, so it is not built). The
  header is therefore never stored (`ProviderSpec.signature_header` is None). The events are
  `checkout.order.completed` / `checkout.order.failed` (bound to the attempt's `transactionId`)
  and `pg.refund.completed` / `pg.refund.failed` (keyed by our `merchantRefundId`); the root
  `payload.state` is what is believed, never the deprecated `type`. What the inbox keeps is an
  allow-list: ids, states, paise, error codes — never a VPA, UTR, account or instrument.
* **Probe.** A status lookup of an Order nobody made: the token being issued and PhonePe answering
  "no such Order" proves the credentials.

Money is integer paise on the wire and everywhere else: no conversion at all. INR only, at least
100 paise, refused before any request.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import urllib.parse
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from app.providers.base import (
    CheckoutCustomer,
    CheckoutRedirect,
    CollectionRefundFacts,
    CollectionWebhookFacts,
    CredentialsMode,
    MalformedWebhook,
    OrderResult,
    PaymentSnapshot,
    RefundResult,
    WebhookEvent,
)
from app.providers.wire import (
    WIRE_REQUEST_TIMEOUT_SECONDS,
    HttpWire,
    ProviderRejected,
    ProviderTransient,
    ProviderWire,
)

PROVIDER = "phonepe"

#: PhonePe's API roots, by environment (the SDK's `base_urls.py`). Platform configuration, never
#: tenant input. In the sandbox the token endpoint shares the PG root; in production it does not.
PHONEPE_PG_BASE: dict[str, str] = {
    "sandbox": "https://api-preprod.phonepe.com/apis/pg-sandbox",
    "production": "https://api.phonepe.com/apis/pg",
}
PHONEPE_OAUTH_BASE: dict[str, str] = {
    "sandbox": "https://api-preprod.phonepe.com/apis/pg-sandbox",
    "production": "https://api.phonepe.com/apis/identity-manager",
}
TOKEN_PATH = "/v1/oauth/token"  # noqa: S105 - a URL path, not a secret
PAY_PATH = "/checkout/v2/pay"
REFUND_PATH = "/payments/v2/refund"

#: The webhook's credential header (lower-cased: the service hands an adapter its headers so).
AUTH_HEADER = "authorization"
ORDER_EVENTS: dict[str, str] = {
    "checkout.order.completed": "captured",
    "checkout.order.failed": "failed",
}
REFUND_EVENTS: frozenset[str] = frozenset(
    {"pg.refund.completed", "pg.refund.failed", "pg.refund.accepted"}
)
#: What a merchant subscribes to on the webhook it creates in PhonePe's dashboard.
WEBHOOK_EVENTS: tuple[str, ...] = (
    "checkout.order.completed",
    "checkout.order.failed",
    "pg.refund.completed",
    "pg.refund.failed",
)
#: The codes PhonePe answers a lookup of an Order (or refund) it does not hold with. The HTTP
#: status is not documented, so the code decides (sandbox to confirm).
NOT_FOUND_CODES: frozenset[str] = frozenset(
    {"INVALID_MERCHANT_ORDER_ID", "ORDER_NOT_FOUND", "NOT_FOUND", "INVALID_REFUND_ID"}
)

#: The token, the Order lookup, the create: the most requests one collections call makes (a refund
#: is the token, the create and the lookup after a refusal).
REQUESTS_PER_CALL = 3
MIN_AMOUNT_MINOR = 100
EXPIRE_AFTER_MIN_SECONDS = 300
EXPIRE_AFTER_MAX_SECONDS = 3600

_MERCHANT_ID = re.compile(r"^[A-Za-z0-9_-]{1,63}$")
_REFUND_ID = re.compile(r"^[A-Za-z0-9_-]{1,63}$")
_EVENT_NAME_MAX = 80
_CODE_MAX = 64

_REFUND_STATES: dict[str, str] = {
    "PENDING": "created",
    "CONFIRMED": "created",
    "ACCEPTED": "created",
    "COMPLETED": "processed",
    "FAILED": "failed",
}
_ATTEMPT_STATES: dict[str, str] = {"COMPLETED": "captured", "FAILED": "failed"}


def refund_key(digest: str) -> str:
    """PhonePe's `merchantRefundId` for a refund whose identity hashes to `digest`: `rfd` and 36
    hex, inside the 63 characters PhonePe allows."""
    return f"rfd{digest[:36]}"


def webhook_authorization(username: str, password: str) -> str:
    """The `Authorization` header PhonePe sends with every webhook: `hex(SHA256(user:password))`
    (the SDK's `is_callback_valid`). For verification and for tests — never logged or stored."""
    return hashlib.sha256(f"{username}:{password}".encode()).hexdigest()


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _code(body: Any) -> str | None:
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) and code.isascii() and len(code) <= _CODE_MAX else None


def _not_found(exc: ProviderRejected) -> bool:
    return exc.status_code == 404 or (exc.code or "") in NOT_FOUND_CODES


def _paise(value: Any) -> int | None:
    """An integer paise amount as PhonePe sends it, or None — never a float, a bool or text."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text(value: Any, limit: int = 128) -> str | None:
    if isinstance(value, str) and value and value.isprintable():
        return value[:limit]
    return None


def _epoch_seconds(value: Any) -> int | None:
    """PhonePe's timestamps are epoch milliseconds; the platform keeps seconds."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    return value // 1000 if value > 10**11 else value


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- the HTTP wire


class _PhonePeOAuthWire(HttpWire):
    """The token endpoint. Any refusal there (a wrong client id, secret or version is a `400` or
    `401`) is the merchant's credentials, not a transient fault."""

    provider = PROVIDER

    def _error_code(self, body: Any) -> str | None:
        return _code(body)

    def _invalid_credentials(self, status_code: int, body: Any) -> bool:
        return 400 <= status_code < 500 and status_code not in (408, 425, 429)


class PhonePeHttpWire(HttpWire):
    """The one class in this service that opens a socket to PhonePe's PG API."""

    provider = PROVIDER

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        client_version: str,
        env: Literal["sandbox", "production"],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(base_url=PHONEPE_PG_BASE[env], transport=transport)
        self._oauth = _PhonePeOAuthWire(base_url=PHONEPE_OAUTH_BASE[env], transport=transport)
        self._client_id = client_id
        self._client_secret = client_secret
        self._client_version = client_version
        self._token: str | None = None

    def _error_code(self, body: Any) -> str | None:
        return _code(body)

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"O-Bearer {self._token}", "accept": "application/json"}

    def _fetch_token(self) -> str:
        form = urllib.parse.urlencode(
            {
                "client_id": self._client_id,
                "client_version": self._client_version,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            }
        ).encode()
        body = self._oauth.request(
            "POST",
            TOKEN_PATH,
            content=form,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token.isascii() or not token.isprintable():
            raise ProviderTransient(PROVIDER, 200, "token_unreadable")
        return token

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if self._token is None:
            self._token = self._fetch_token()
        return super().request(
            method, path, params=params, json=json, content=content, headers=headers
        )


# --------------------------------------------------------------------------- the adapter


class PhonePeCollectionsProvider:
    """One merchant account's PhonePe adapter, built per call from that account's opened keys."""

    name = PROVIDER
    #: One connect + read timeout per request, three at worst (the token, the lookup, the create):
    #: 3 x 7s = 21s, well inside the collection's right-to-call lease (`CollectionsContract`).
    request_timeouts_seconds: tuple[float, ...] = (
        WIRE_REQUEST_TIMEOUT_SECONDS,
    ) * REQUESTS_PER_CALL

    def __init__(
        self,
        *,
        wire: ProviderWire,
        mode: Literal["stub", "sandbox", "production"],
        webhook_username: str = "",
        webhook_password: str = "",
    ) -> None:
        self._wire = wire
        self._mode = mode
        #: The pair the merchant set on the webhook in PhonePe's dashboard. Never in a repr or log.
        self._webhook_username = webhook_username
        self._webhook_password = webhook_password

    def __repr__(self) -> str:  # never a credential
        return f"PhonePeCollectionsProvider(mode={self._mode!r})"

    @property
    def is_live(self) -> bool:
        return self._mode != "stub"

    @property
    def credentials_mode(self) -> CredentialsMode:
        if self._mode == "stub":
            return "stub"
        return "live" if self._mode == "production" else "test"

    # ------------------------------------------------------------------ orders

    def _status(self, order_id: str, *, details: bool = False) -> dict[str, Any] | None:
        """The Order as PhonePe holds it, or None when it holds none."""
        try:
            body = self._wire.request(
                "GET",
                f"/checkout/v2/order/{_quote(order_id)}/status",
                params={"details": "true" if details else "false"},
            )
        except ProviderRejected as exc:
            if _not_found(exc):
                return None
            raise
        return _dict(body)

    def ensure_order(
        self,
        *,
        receipt: str,
        amount_minor: int,
        currency: str,
        notes: dict[str, str],
        payer: CheckoutCustomer | None = None,
        expires_at: datetime | None = None,
        notify_url: str | None = None,
        recorded_order_id: str | None = None,
        return_url: str | None = None,
    ) -> OrderResult:
        """The Order for `receipt` — looked up first, created only if PhonePe holds none — with the
        browser's `redirectUrl` in `redirect`. Refused before any request: a currency other than
        INR, under 100 paise, no return URL, or an id PhonePe would not take. An Order found for
        another amount, or one that is no longer `PENDING`, is refused rather than reused; a
        recorded Order this account does not hold is refused, never made again."""
        if currency.upper() != "INR":
            raise ProviderRejected(PROVIDER, 0, "currency_not_supported")
        if amount_minor < MIN_AMOUNT_MINOR:
            raise ProviderRejected(PROVIDER, 0, "amount_below_minimum")
        if not return_url:
            raise ProviderRejected(PROVIDER, 0, "return_url_required")
        if not _MERCHANT_ID.fullmatch(receipt):
            raise ProviderRejected(PROVIDER, 0, "order_id_invalid")
        if recorded_order_id is not None and recorded_order_id != receipt:
            # Every PhonePe Order this platform makes is named by its collection's id.
            raise ProviderTransient(PROVIDER, 0, "recorded_order_not_found")

        found = self._status(receipt)
        if found is None and recorded_order_id is not None:
            raise ProviderTransient(PROVIDER, 0, "recorded_order_not_found")
        if found is not None:
            held = _paise(found.get("amount"))
            if held is not None and held != amount_minor:
                raise ProviderRejected(PROVIDER, 200, "order_amount_mismatch")
            if str(found.get("state") or "").upper() != "PENDING":
                raise ProviderRejected(PROVIDER, 200, "order_not_payable")

        body: dict[str, Any] = {
            "merchantOrderId": receipt,
            "amount": amount_minor,
            "paymentFlow": {"type": "PG_CHECKOUT", "merchantUrls": {"redirectUrl": return_url}},
        }
        if expires_at is not None:
            seconds = int((expires_at - datetime.now(UTC)).total_seconds())
            body["expireAfter"] = min(
                max(seconds, EXPIRE_AFTER_MIN_SECONDS), EXPIRE_AFTER_MAX_SECONDS
            )
        answer = _dict(self._wire.request("POST", PAY_PATH, json=body))
        url = answer.get("redirectUrl")
        if not isinstance(url, str) or not url.startswith("https://") or not url.isprintable():
            # Whatever happened, the Order may exist now: the lookup finds it on the next call.
            raise ProviderTransient(PROVIDER, 200, "redirect_url_unreadable")
        return OrderResult(
            provider=PROVIDER,
            provider_order_id=receipt,
            receipt=receipt,
            redirect=CheckoutRedirect(url=url, method="GET"),
        )

    def find_order(self, *, receipt: str) -> OrderResult | None:
        """The lookup alone, never a create."""
        if not _MERCHANT_ID.fullmatch(receipt):
            return None
        if self._status(receipt) is None:
            return None
        return OrderResult(provider=PROVIDER, provider_order_id=receipt, receipt=receipt)

    def order_payments(self, *, order_id: str) -> list[PaymentSnapshot]:
        """Every settled attempt on the Order (`details=true`), in the neutral vocabulary; a
        `PENDING` attempt is not in the answer, so it can never move a collection."""
        found = self._status(order_id, details=True)
        if found is None:
            return []
        snapshots: list[PaymentSnapshot] = []
        for attempt in found.get("paymentDetails") or []:
            attempt = _dict(attempt)
            status = _ATTEMPT_STATES.get(str(attempt.get("state") or "").upper())
            payment_id = _text(attempt.get("transactionId"))
            amount = _paise(attempt.get("amount"))
            if status is None or payment_id is None or amount is None:
                continue
            snapshots.append(
                PaymentSnapshot(
                    provider_payment_id=payment_id,
                    status=status,
                    amount_minor=amount,
                    currency="INR",
                    error_code=_text(attempt.get("errorCode"), _CODE_MAX)
                    if status == "failed"
                    else None,
                    created_at=_epoch_seconds(attempt.get("timestamp")),
                )
            )
        return snapshots

    # ------------------------------------------------------------------ refunds

    def refund(
        self,
        *,
        idempotency_key: str,
        provider_payment_id: str,
        amount_minor: int,
        currency: str,
        provider_order_id: str | None = None,
    ) -> RefundResult:
        """`POST /payments/v2/refund` against the Order. A refusal is answered by looking the
        refund up by our `merchantRefundId`: the same refund for the same amount is used (an
        earlier call landed and its answer was lost); anything else re-raises the refusal."""
        if not provider_order_id:
            raise ProviderRejected(PROVIDER, 0, "order_required")
        if not _REFUND_ID.fullmatch(idempotency_key):
            raise ProviderRejected(PROVIDER, 0, "refund_id_invalid")
        if currency.upper() != "INR":
            raise ProviderRejected(PROVIDER, 0, "currency_not_supported")
        if amount_minor < MIN_AMOUNT_MINOR:
            raise ProviderRejected(PROVIDER, 0, "amount_below_minimum")
        try:
            body = self._wire.request(
                "POST",
                REFUND_PATH,
                json={
                    "merchantRefundId": idempotency_key,
                    "originalMerchantOrderId": provider_order_id,
                    "amount": amount_minor,
                },
            )
        except ProviderRejected as refused:
            try:
                body = self._wire.request("GET", f"{REFUND_PATH}/{_quote(idempotency_key)}/status")
            except ProviderRejected:
                raise refused from None
            if _dict(body).get("originalMerchantOrderId") not in (None, provider_order_id):
                raise refused from None
        entity = _dict(body)
        found_amount = _paise(entity.get("amount"))
        if found_amount is not None and found_amount != amount_minor:
            raise ProviderRejected(PROVIDER, 200, "refund_amount_mismatch")
        return RefundResult(
            provider=PROVIDER,
            provider_refund_id=idempotency_key,
            state=_REFUND_STATES.get(str(entity.get("state") or "").upper(), "created"),
            amount_minor=amount_minor,
            currency="INR",
        )

    # ------------------------------------------------------------------ webhooks

    def verify_webhook(self, *, headers: Mapping[str, str] | None = None, body: bytes) -> bool:
        """`Authorization == hex(SHA256(user:password))`, constant time, failing closed: no
        configured pair, no header, a non-ASCII header or any mismatch is False."""
        if not self._webhook_username or not self._webhook_password:
            return False
        given = (headers or {}).get(AUTH_HEADER)
        if not given:
            return False
        try:
            presented = given.strip().encode("ascii")
        except UnicodeEncodeError:
            return False
        expected = webhook_authorization(self._webhook_username, self._webhook_password)
        return hmac.compare_digest(expected.encode("ascii"), presented)

    def webhook_delivery_id(self, headers: Mapping[str, str]) -> str | None:
        return None  # PhonePe documents no delivery id: the body's own identity is derived

    def parse_webhook(self, *, body: bytes) -> WebhookEvent:
        """A verified PhonePe webhook in the collections vocabulary. An order event's outcome is
        `captured` for `checkout.order.completed` and `failed` for `checkout.order.failed`, and
        only when the root `payload.state` agrees; it binds the deciding attempt's
        `transactionId`. A refund event is identified by the refund and keyed by our
        `merchantRefundId`. `raw` is the allow-listed body."""
        try:
            parsed = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise MalformedWebhook("the webhook body is not valid JSON") from None
        if not isinstance(parsed, dict):
            raise MalformedWebhook("the webhook body is not a JSON object")
        raw = redact_webhook_body(parsed)
        event_name = str(raw.get("event") or "")[:_EVENT_NAME_MAX]
        payload = _dict(raw.get("payload"))
        state = str(payload.get("state") or "").upper()

        if event_name in REFUND_EVENTS:
            return WebhookEvent(
                provider=PROVIDER,
                provider_event_id="",
                event_name=event_name,
                outcome_state="unknown",
                organization_id=None,
                charge_id=None,
                provider_payment_id=None,
                raw=raw,
                provider_order_id=_text(payload.get("originalMerchantOrderId")),
                entity_id=_text(payload.get("refundId")) or _text(payload.get("merchantRefundId")),
                created_at=_epoch_seconds(payload.get("timestamp")),
                amount_minor=_paise(payload.get("amount")),
                currency="INR",
            )

        outcome = ORDER_EVENTS.get(event_name, "unknown")
        if (outcome == "captured" and state != "COMPLETED") or (
            outcome == "failed" and state != "FAILED"
        ):
            outcome = "unknown"
        attempt = _deciding_attempt(payload, outcome)
        payment_id = _text(attempt.get("transactionId"))
        return WebhookEvent(
            provider=PROVIDER,
            provider_event_id="",
            event_name=event_name,
            outcome_state=outcome,
            organization_id=None,
            charge_id=None,
            provider_payment_id=payment_id if outcome in ("captured", "failed") else None,
            failure_reason=_text(attempt.get("errorCode"), _CODE_MAX)
            if outcome == "failed"
            else None,
            raw=raw,
            provider_order_id=_text(payload.get("merchantOrderId")),
            entity_id=payment_id or _text(payload.get("orderId")),
            created_at=_epoch_seconds(attempt.get("timestamp"))
            or _epoch_seconds(payload.get("expireAt")),
            amount_minor=_paise(payload.get("amount")),
            currency="INR",
        )

    def webhook_facts(self, event: WebhookEvent) -> CollectionWebhookFacts:
        payload = _dict(event.raw.get("payload"))
        refund: CollectionRefundFacts | None = None
        if event.event_name in REFUND_EVENTS:
            refund_id = _text(payload.get("merchantRefundId"))
            if refund_id is not None:
                refund = CollectionRefundFacts(
                    provider_refund_id=refund_id,
                    provider_payment_id=None,
                    amount_minor=_paise(payload.get("amount")),
                    currency="INR",
                    state=_REFUND_STATES.get(str(payload.get("state") or "").upper(), "created"),
                )
        outcome = event.outcome_state if event.outcome_state in ("captured", "failed") else None
        attempt = _deciding_attempt(payload, event.outcome_state)
        failure = _text(attempt.get("errorCode"), _CODE_MAX) or _text(
            payload.get("errorCode"), _CODE_MAX
        )
        return CollectionWebhookFacts(
            outcome=outcome, refund=refund, failure_code=failure, stored=event.raw
        )

    # ------------------------------------------------------------------ verification

    def probe(self) -> None:
        """A status lookup of an Order nobody made. The token endpoint refusing the credentials
        propagates as `ProviderMisconfigured`; "no such Order" is success."""
        self._status(f"probe-{uuid.uuid4().hex[:24]}")


def _deciding_attempt(payload: dict[str, Any], outcome: str) -> dict[str, Any]:
    """The attempt an order event is about: the last one in the state the outcome names, else the
    last one."""
    attempts = [_dict(a) for a in payload.get("paymentDetails") or [] if isinstance(a, dict)]
    wanted = {"captured": "COMPLETED", "failed": "FAILED"}.get(outcome)
    matching = [a for a in attempts if str(a.get("state") or "").upper() == wanted]
    if matching:
        return matching[-1]
    return attempts[-1] if attempts else {}


_PAYLOAD_KEYS = (
    "merchantId",
    "merchantOrderId",
    "orderId",
    "state",
    "amount",
    "expireAt",
    "merchantRefundId",
    "refundId",
    "originalMerchantOrderId",
    "timestamp",
    "errorCode",
    "detailedErrorCode",
)
_ATTEMPT_KEYS = (
    "transactionId",
    "paymentMode",
    "state",
    "amount",
    "timestamp",
    "errorCode",
    "detailedErrorCode",
)


def _kept(source: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key in keys:
        value = source.get(key)
        if isinstance(value, str):
            kept[key] = value[:128]
        elif isinstance(value, int) and not isinstance(value, bool):
            kept[key] = value
    return kept


def redact_webhook_body(parsed: dict[str, Any]) -> dict[str, Any]:
    """What the inbox keeps of a PhonePe webhook: the event name, ids, states, paise, error codes
    and timestamps. Never `metaInfo` (free text), an instrument, a rail (VPA, UTR, account) or
    anything else. Re-parses to the same identity (`CollectionsContract`)."""
    payload = _dict(parsed.get("payload"))
    kept = _kept(payload, _PAYLOAD_KEYS)
    attempts = [
        _kept(attempt, _ATTEMPT_KEYS)
        for attempt in (payload.get("paymentDetails") or [])
        if isinstance(attempt, dict)
    ]
    if attempts:
        kept["paymentDetails"] = attempts
    event = parsed.get("event")
    return {"event": event[:_EVENT_NAME_MAX] if isinstance(event, str) else "", "payload": kept}
