"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so importing
*from* it is fragile — this module is a plain import that works the same way everywhere.

`razorpay_webhook_body` builds a Razorpay-shaped webhook (the `notes` metadata payment stamps at
charge-create time is echoed back here, which is how a callback names its own tenant). `sign` is
provided by the adapter itself, so a test produces an authentic signature without a real Razorpay
and no live keys — the whole point of the stubbed seam.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from platform_core.internal_auth import mint_service_token


def service_token() -> str:
    """A short-lived service token addressed to payment — what billing would present to the
    internal charge endpoint."""
    return mint_service_token(settings(), audience="payment", caller="billing")


def razorpay_webhook_body(
    *,
    event: str,
    provider_payment_id: str,
    organization_id: uuid.UUID,
    charge_id: uuid.UUID,
    subscription_id: uuid.UUID | None = None,
    product_key: str | None = None,
    event_id: str | None = None,
    amount_minor: int = 1000,
    currency: str = "USD",
    error_description: str | None = None,
    order_id: str | None = None,
    body_id: bool = True,
    created_at: int | None = 1_700_000_000,
) -> bytes:
    """A Razorpay webhook body as raw bytes, shaped exactly as `parse_webhook` expects.

    `notes` carries the platform metadata Razorpay echoes on every callback about a payment; the
    tenant and charge are read from there during reconciliation.

    **`body_id=False` is the real shape.** A genuine Razorpay body has no top-level `id` — the event
    id is the `X-Razorpay-Event-Id` *header* — and carries `entity`, `account_id` and `created_at`.
    The default keeps a synthetic body `id` because a dozen older tests key their inbox rows on it;
    anything about event identity must pass `body_id=False`, or it tests a shape Razorpay never
    sends (which is exactly how the empty-id collision went unseen).
    """
    notes: dict[str, str] = {
        "organization_id": str(organization_id),
        "charge_id": str(charge_id),
    }
    if subscription_id is not None:
        notes["subscription_id"] = str(subscription_id)
    if product_key is not None:
        notes["product_key"] = product_key

    entity: dict[str, Any] = {
        "id": provider_payment_id,
        "amount": amount_minor,
        "currency": currency,
        "notes": notes,
    }
    if error_description is not None:
        entity["error_description"] = error_description
    if order_id is not None:
        entity["order_id"] = order_id

    body: dict[str, Any] = {}
    if body_id:
        body["id"] = event_id or f"evt_{uuid.uuid4().hex[:16]}"
    body.update({"entity": "event", "account_id": "acc_test", "event": event})
    body["payload"] = {"payment": {"entity": entity}}
    if created_at is not None:
        body["created_at"] = created_at
    return json.dumps(body).encode()


async def seed_charge(
    client: Any,
    org_id: uuid.UUID,
    *,
    key: str,
    amount_minor: int = 1000,
    currency: str = "USD",
    invoice_id: uuid.UUID | None = None,
    subscription_id: uuid.UUID | None = None,
    product_key: str | None = None,
) -> dict[str, Any]:
    """Create a charge through the internal route, exactly as billing does, and return it. The stub
    provider answers `authorized` with a deterministic payment id — the state a real charge is in
    when its capture or failure webhook arrives."""
    payload: dict[str, Any] = {
        "organization_id": str(org_id),
        "amount_minor": amount_minor,
        "currency": currency,
        "idempotency_key": key,
    }
    if invoice_id is not None:
        payload["invoice_id"] = str(invoice_id)
    if subscription_id is not None:
        payload["subscription_id"] = str(subscription_id)
    if product_key is not None:
        payload["product_key"] = product_key
    response = await client.post(
        "/internal/v1/charges", headers={"X-Internal-Token": service_token()}, json=payload
    )
    assert response.status_code == 201, response.text
    charge: dict[str, Any] = response.json()
    return charge


def signed_post(
    provider: Any, body: bytes, *, event_id: str | None = None, signature: str | None = None
) -> dict[str, Any]:
    """The `client.post` keyword arguments for delivering `body` as Razorpay would: the raw bytes,
    the HMAC over them, and — when given — the `X-Razorpay-Event-Id` delivery header (the only place
    a real delivery carries its id). `signature` overrides the HMAC, to send a wrong one."""
    headers = {"X-Razorpay-Signature": signature if signature is not None else provider.sign(body)}
    if event_id is not None:
        headers["X-Razorpay-Event-Id"] = event_id
    return {"content": body, "headers": headers}


# --------------------------------------------------------------------------- reading the truth
#
# State is asserted from the database on a separate BYPASSRLS session (`admin_session`), never from
# the shared test session's identity map, which would happily show a rolled-back attribute as still
# set.


async def rows(admin: AsyncSession, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with admin.begin():
        return [dict(r) for r in (await admin.execute(text(sql), params)).mappings().all()]


async def inbox_rows(admin: AsyncSession, org: uuid.UUID) -> list[dict[str, Any]]:
    return await rows(
        admin,
        "SELECT provider_event_id, event_name, charge_id, disposition, disposition_reason, id, "
        "payload FROM payment.provider_events WHERE organization_id = :org "
        "ORDER BY received_at, provider_event_id",
        org=org,
    )


async def audit_rows(admin: AsyncSession, org: uuid.UUID) -> list[dict[str, Any]]:
    return await rows(
        admin,
        "SELECT action, entity, entity_id, acting_as, before, after FROM payment.audit_logs "
        "WHERE organization_id = :org ORDER BY at, action",
        org=org,
    )


def ignored_audits(all_audits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in all_audits if a["action"] == "payment.webhook.ignored"]


async def emitted(admin: AsyncSession, org: uuid.UUID) -> list[str]:
    found = await rows(
        admin,
        "SELECT event_type FROM payment.outbox WHERE organization_id = :org ORDER BY created_at",
        org=org,
    )
    return [r["event_type"] for r in found]


async def charge_row(admin: AsyncSession, charge_id: str | uuid.UUID) -> dict[str, Any]:
    found = await rows(
        admin,
        "SELECT state, failure_reason, provider_payment_id, provider_order_id "
        "FROM payment.charges WHERE id = :id",
        id=uuid.UUID(str(charge_id)),
    )
    return found[0]


async def force_charge(admin: AsyncSession, charge_id: str | uuid.UUID, **columns: Any) -> None:
    """Put a charge in a state the API alone would take a sweep to reach."""
    sets = ", ".join(f"{name} = :{name}" for name in columns)
    async with admin.begin():
        await admin.execute(
            text(f"UPDATE payment.charges SET {sets} WHERE id = :id"),  # noqa: S608 - test names
            {**columns, "id": uuid.UUID(str(charge_id))},
        )


# --------------------------------------------------------------------------- building deliveries


def webhook_body(
    charge: dict[str, Any],
    org_id: uuid.UUID,
    *,
    event: str,
    created_at: int = 1_700_000_000,
    **kw: Any,
) -> bytes:
    """A real-shaped (id-less) Razorpay body for `charge`, naming `org_id` in its notes."""
    kw.setdefault("provider_payment_id", charge["provider_payment_id"])
    return razorpay_webhook_body(
        event=event,
        organization_id=org_id,
        charge_id=uuid.UUID(charge["id"]),
        body_id=False,
        created_at=created_at,
        **kw,
    )


def edited_body(body: bytes, edit: Any) -> bytes:
    """`body` with `edit(parsed_dict)` applied — used to remove or corrupt one field of an
    otherwise genuine body. The caller signs the result, so it is authentic as far as the service
    can tell."""
    parsed = json.loads(body)
    edit(parsed)
    return json.dumps(parsed).encode()


async def post_webhook(client: Any, provider: Any, body: bytes, event_id: str | None) -> Any:
    return await client.post(
        "/api/v1/webhooks/razorpay", **signed_post(provider, body, event_id=event_id)
    )
