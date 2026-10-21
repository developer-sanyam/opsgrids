"""Database models for email suppression, blocklist, and inbound email tracking."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


class EmailSuppression(Base):
    """Stores email addresses blocked from receiving any further emails.

    Reasons include:
    - `bounced`: Inbound bounce (RFC 3464 DSN / NDR delivery failure)
    - `invalid_syntax`: Malformed / illegal email format (RFC 5322)
    - `domain_not_found`: Non-existent domain or missing MX/A records
    - `mailbox_not_found`: Specific 5.1.1 mailbox unavailable refusal
    - `manual`: Administratively blocked
    """

    __tablename__ = "email_suppressions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    email_masked: Mapped[str] = mapped_column(String(255), nullable=False)
    email_index: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    diagnostic_detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "email_index", name="uq_email_suppression_org_index"),
        Index("ix_email_suppression_org_email", "organization_id", "email"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "email": self.email,
            "email_masked": self.email_masked,
            "email_index": self.email_index,
            "reason": self.reason,
            "diagnostic_detail": self.diagnostic_detail,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class InboundEmailLog(Base):
    """Tracks processed incoming emails to avoid duplicate replies and ensure idempotency."""

    __tablename__ = "email_inbound_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sender_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    is_bounce: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_auto_replied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reply_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "message_id", name="uq_inbound_log_org_message_id"),
        Index("ix_inbound_log_org_sender", "organization_id", "sender_email"),
        Index("ix_inbound_log_org_body_hash", "organization_id", "body_hash"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "message_id": self.message_id,
            "sender_email": self.sender_email,
            "subject": self.subject,
            "is_bounce": self.is_bounce,
            "is_auto_replied": self.is_auto_replied,
            "reply_message_id": self.reply_message_id,
            "replied_at": self.replied_at.isoformat() if self.replied_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
