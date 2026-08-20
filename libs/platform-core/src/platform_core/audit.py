"""The audit trail.

Written inside the same transaction as the change it describes, so a rolled-back write
never leaves a record claiming it happened.

Two things this deliberately covers that are commonly missed: **reads** of sensitive data
are auditable events in their own right (unmasking PII writes a row), and redaction walks
nested structures rather than only top-level keys.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from starlette.requests import Request

REDACTED = "***"

#: Never store these, at any depth, in `before`/`after`.
REDACTED_KEYS = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "client_secret",
        "secret",
        "api_key",
        "private_key",
        "bank_account",
        "bank_account_enc",
        "pan",
        "pan_enc",
        "aadhaar",
        "aadhaar_enc",
        "card_number",
        "cvv",
        "otp",
        "mfa_secret",
    }
)


class AuditLogMixin:
    """Mix into a service's Base:  ``class AuditLog(Base, AuditLogMixin): ...``"""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    #: Set when a platform operator is acting on a customer's behalf. Never null on an
    #: impersonated action — an override with no named human is not an audit trail.
    acting_as: Mapped[str | None] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


def redact(data: Any) -> Any:
    """Replace sensitive values at any depth.

    Shallow redaction is a trap: a nested ``{"payload": {"password": ...}}`` survives it
    in cleartext, and audit rows are exactly the thing that gets exported to a ticket.
    """
    if isinstance(data, dict):
        return {k: (REDACTED if k.lower() in REDACTED_KEYS else redact(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(v) for v in data]
    return data


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # First hop is the client, if the proxy is trusted to rewrite this header.
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def record_audit(
    session: AsyncSession,
    audit_cls: type[Any],
    *,
    organization_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    action: str,
    entity: str,
    entity_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    acting_as: str | None = None,
    request: Request | None = None,
) -> None:
    """Stage an audit row in the caller's transaction. Does not commit."""
    session.add(
        audit_cls(
            organization_id=organization_id,
            actor_id=actor_id,
            acting_as=acting_as,
            action=action,
            entity=entity,
            entity_id=entity_id,
            before=redact(before) if before is not None else None,
            after=redact(after) if after is not None else None,
            reason=reason,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent") if request else None,
            request_id=getattr(request.state, "request_id", None) if request else None,
            correlation_id=getattr(request.state, "correlation_id", None) if request else None,
        )
    )


def record_sensitive_read(
    session: AsyncSession,
    audit_cls: type[Any],
    *,
    organization_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    entity: str,
    entity_id: uuid.UUID,
    fields: list[str],
    request: Request | None = None,
) -> None:
    """Reading unmasked PII is itself an auditable event.

    Call this wherever a `read_sensitive` permission unmasks a value. A previous system
    documented this rule and never implemented it, so an unmasked bank account left no
    trace at all — which is the same as not having the control.
    """
    record_audit(
        session,
        audit_cls,
        organization_id=organization_id,
        actor_id=actor_id,
        action="entity.sensitive_read",
        entity=entity,
        entity_id=entity_id,
        after={"fields": fields},
        request=request,
    )
