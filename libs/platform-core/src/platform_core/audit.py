"""The audit trail.

Written inside the same transaction as the change it describes, so a rolled-back write
never leaves a record claiming it happened.

Two things this deliberately covers that are commonly missed: **reads** of sensitive data
are auditable events in their own right (unmasking PII writes a row), and redaction walks
nested structures rather than only top-level keys.
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
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
    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: Set when a platform operator is acting on a customer's behalf. Never null on an
    #: impersonated action — an override with no named human is not an audit trail.
    acting_as: Mapped[str | None] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:
        """Every index leads with `organization_id`, and that is not stylistic.

        A row-level security policy qual is ANDed into every query, so an index that does not
        lead with the tenant column stops doing its job the moment policies are on: the scan
        reads the whole platform's rows for a value and then discards almost all of them.

        This is the highest-volume table in any service, so it is the one that hurts first —
        and it hurts silently, as a query that used to be fast quietly becoming a scan.
        """
        return (
            Index(f"ix_{cls.__tablename__}_org_at", "organization_id", sa.desc("at")),
            Index(f"ix_{cls.__tablename__}_org_entity", "organization_id", "entity", "entity_id"),
            Index(f"ix_{cls.__tablename__}_org_actor", "organization_id", "actor_id"),
            Index(f"ix_{cls.__tablename__}_org_action", "organization_id", "action"),
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


def jsonable(data: Any) -> Any:
    """Coerce a snapshot into what the JSONB column can actually store, at any depth.

    ``Base.as_dict`` returns column values as they are — UUIDs, timestamps, dates, Decimals —
    which is precisely what an audit snapshot should record, and precisely what JSONB's default
    serializer refuses: ``json.dumps`` accepts none of them. Coerced here, once, rather than at
    every ``before=row.as_dict()`` call site, so that method works the way its docstring promises.

    Found the hard way: the one route that snapshotted a row this way answered 500 to every
    caller, and no test had ever exercised it.
    """
    if isinstance(data, dict):
        return {k: jsonable(v) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return [jsonable(v) for v in data]
    if isinstance(data, uuid.UUID):
        return str(data)
    if isinstance(data, datetime | date):
        return data.isoformat()
    if isinstance(data, Decimal):
        # A string, never a float — the same representation money takes everywhere else here.
        return str(data)
    return data


def _client_ip(request: Request | None) -> str | None:
    """The client address for an audit row, or None when there is not a usable one.

    Returned only if it **parses as an IP address**. The `ip` column is Postgres `INET`, and a
    forwarded header is text a client typed: `unknown`, an empty entry or `203.0.113.9:443` would
    otherwise reach the INSERT and be rejected by the database, turning a header the caller chose
    into a 500 on every audited endpoint (found when the attendance signals started reading the
    same value). An address that cannot be read is recorded as unknown, never guessed and never
    replaced by the socket peer — behind a proxy that is the proxy's own address, which would be
    worse than a null.
    """
    if request is None:
        return None
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # First hop is the client, if the proxy is trusted to rewrite this header.
        candidate: str | None = forwarded.split(",")[0].strip()
    else:
        candidate = request.client.host if request.client else None
    if not candidate:
        return None
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    # `fe80::1%eth0` parses in Python and is refused by PostgreSQL's INET, so it would turn an
    # audited request into a 500 by another route. A zone id names an interface on the sender's own
    # host and is no routable client address; it is recorded as unknown.
    if isinstance(address, ipaddress.IPv6Address) and address.scope_id:
        return None
    return str(address)


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
            before=jsonable(redact(before)) if before is not None else None,
            after=jsonable(redact(after)) if after is not None else None,
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
