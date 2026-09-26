"""Tables for WhatsApp Embedded Signup, the numbers cache and connection health (slice W1).

Three small tables, all tenant-scoped with FORCE row-level security, none holding anything a
webhook routes on (that stays `provider_accounts.phone_number_id`, globally unique):

* `w1_setup_runs` — one row per `signup/complete`. It is the persisted state the worker advances
  and `GET /engage/whatsapp/signup/{id}` reads. It briefly holds two secrets, **both sealed**: the
  popup's one-time authorization `code` (wiped the moment it is exchanged) and the business token
  between the exchange and the moment it is stored on `provider_accounts` (wiped then, and on any
  failure). Neither is ever returned.
* `w1_numbers` — a cache of the phone numbers on the organization's WABA, refreshed from Graph on
  read when older than ten minutes and by `phone_number_quality_update` webhooks. `is_default`
  marks the one mirrored into `provider_accounts` (the number sends go out on).
* `w1_connection_health` — one row per organization: what was last learned about the token and the
  webhook subscription, so the health screen never has to call Meta to answer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, LargeBinary, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from platform_core.models import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class SetupRun(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """One Embedded Signup completion and how far the worker has got."""

    __tablename__ = "w1_setup_runs"

    #: running | done | failed.
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="running")
    #: `[{key, label, state, detail}]`, in execution order. `detail` is a sentence safe to show.
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    #: Machine-readable reason a run failed (`code_exchange_failed`, `number_claimed`, ...).
    error_code: Mapped[str | None] = mapped_column(String(60))

    #: Sealed one-time code from the popup; null once exchanged. Never returned, never logged.
    code_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    #: Blind index of the code: makes `signup/complete` idempotent on a client retry without
    #: keeping the code readable.
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Sealed business token, from the exchange until it lands on `provider_accounts`.
    token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    #: The ids the browser reported. Hints: the server discovers and verifies the real ones.
    waba_hint: Mapped[str | None] = mapped_column(String(80))
    phone_number_hint: Mapped[str | None] = mapped_column(String(80))
    started_by: Mapped[str | None] = mapped_column(String(80))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("organization_id", "code_hash", name="uq_w1_setup_runs_code"),
        Index("ix_w1_setup_runs_org_status", "organization_id", "status", "created_at"),
        # At most one running setup per organization, enforced here rather than by a read that
        # two concurrent requests can both pass (`w1_services.start_signup`).
        Index(
            "uq_w1_setup_runs_org_running",
            "organization_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )


class WhatsAppNumber(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """One phone number on the organization's WABA, as last read from Meta."""

    __tablename__ = "w1_numbers"

    waba_id: Mapped[str] = mapped_column(String(80), nullable=False)
    #: Unique per organization here; global uniqueness (the webhook routing key) lives on
    #: `provider_accounts`, which only ever holds the default number.
    phone_number_id: Mapped[str] = mapped_column(String(80), nullable=False)
    #: The business's own published number, not a customer's; masked in responses regardless.
    display_phone_number: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    verified_name: Mapped[str | None] = mapped_column(String(200))
    #: approved | pending_review | declined | none
    name_status: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    #: green | yellow | red | unknown
    quality_rating: Mapped[str] = mapped_column(String(10), nullable=False, default="unknown")
    #: TIER_250 | TIER_1K | TIER_10K | TIER_100K | UNLIMITED | unknown
    messaging_limit: Mapped[str] = mapped_column(String(12), nullable=False, default="unknown")
    #: connected | pending_verification | disconnected
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_verification")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: When Meta was last asked. Null forces the next read to refresh (a quality webhook sets it).
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("organization_id", "phone_number_id", name="uq_w1_numbers_org_number"),
    )


class ConnectionHealth(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """What was last learned about this organization's WhatsApp token and webhook subscription."""

    __tablename__ = "w1_connection_health"

    #: valid | expiring | invalid | unknown
    token_state: Mapped[str] = mapped_column(String(10), nullable=False, default="unknown")
    token_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When Meta confirmed the app is subscribed to the WABA's webhooks; null = never verified.
    webhook_subscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The setup run that last touched this connection (informational).
    last_setup_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    __table_args__ = (UniqueConstraint("organization_id", name="uq_w1_connection_health_org"),)
