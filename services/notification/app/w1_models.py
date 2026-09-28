"""Tables for WhatsApp Embedded Signup, the numbers cache and connection health (slice W1).

Four tables. The first three are tenant-scoped with FORCE row-level security, and none of them
holds anything a webhook routes on (that stays `provider_accounts.phone_number_id`, globally
unique):

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

The fourth, `w1_meta_deletion_requests`, is deliberately **not** tenant-scoped — see its own
docstring for why a Meta data-deletion callback has no single organization to belong to.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
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
    #: The Facebook-scoped user id `FB.login`'s response carried, from the same browser round
    #: trip as the two hints above. Unlike them it is never verified against Meta (there is no
    #: Graph call that confirms "this id completed this login" -- Meta does not publish one) and
    #: is mirrored onto `provider_accounts.meta_user_id` as-is once the connection is written
    #: (`w1_setup._step_number`). A hint in the same sense as the other two: informational, not
    #: load-bearing for anything this run itself does.
    meta_user_id_hint: Mapped[str | None] = mapped_column(String(40))
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
    #: Meta's `code_verification_status` (VERIFIED | NOT_VERIFIED | EXPIRED ...), verbatim.
    code_verification_status: Mapped[str | None] = mapped_column(String(24))
    #: Meta's `platform_type` (CLOUD_API | ON_PREMISE | NOT_APPLICABLE), verbatim. `CLOUD_API`
    #: means registered for the Cloud API; `NOT_APPLICABLE` means not registered yet.
    platform_type: Mapped[str | None] = mapped_column(String(24))
    #: Meta's `throughput.level` (STANDARD | HIGH | NOT_APPLICABLE), verbatim.
    throughput_level: Mapped[str | None] = mapped_column(String(24))

    #: The six-digit PIN this platform chose when it registered the number
    #: (`POST /{id}/register`), **sealed** like the token, never returned or logged. It is kept
    #: because a registration that half-succeeded has set that PIN as the number's two-step
    #: verification PIN, and a second attempt with a different one would be refused.
    registration_pin_sealed: Mapped[bytes | None] = mapped_column(LargeBinary)
    #: How many `register` calls were made inside the current 72-hour window (Meta allows ten).
    registration_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: When the last `register` call was made; the window is measured from it.
    registration_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    #: When Meta's deauthorize or data-deletion callback last reported the Facebook login behind
    #: this connection as revoked. **Advisory only** -- `app/dispatcher.py` never reads this
    #: column, and it never flips `token_state`. Embedded Signup mints a business-integration
    #: token; a person removing the app from their own Facebook settings does not necessarily
    #: revoke that token, so a callback naming this connection is not proof the connection has
    #: stopped working, and must not be able to stop sends on its own. It is shown on the health
    #: card as a plain, informational note. See `docs/whatsapp/webhooks.md`.
    identity_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("organization_id", name="uq_w1_connection_health_org"),)


class MetaDataDeletionRequest(Base):
    """The whole answer `GET .../data-deletion-status` may ever give a stranger holding a
    confirmation code -- and, separately, what makes a retried Meta data-deletion callback return
    that same code rather than mint a fresh one.

    **Deliberately not tenant-scoped**, on purpose and for a reason specific to this callback
    rather than an oversight: one Meta data-deletion request can fan out to more than one
    organization (`meta_user_id` is not unique to one connection) or to none at all (an id we
    cannot attribute still owes Meta an answer), so no single `organization_id` would be the right
    scope, and a policy that let it through for "every organization or none" would not be a tenant
    boundary at all -- the same judgement call `processed_events` already makes, for a related
    reason (see `migrations/versions/b2c3d4e5f6a7_enable_row_level_security.py`). It carries no
    `organization_id` column, and no migration ever enables row-level security on it.

    **Deliberately minimal**, so that this table -- the one place an unauthenticated `GET` can
    reach -- reveals nothing if it leaked whole: no Meta user id, in any form, and no
    organization id. `request_key` is the one exception, and it is not the id either: a blind
    index (`platform_core.pii.blind_index`, the same primitive `w1_setup_runs.code_hash` uses for
    a one-time code) of the callback's own `f"{user_id}:{issued_at}"`, keyed by
    `Settings.pii_encryption_key`. It cannot be inverted back to who asked, and it exists only so
    a retried callback -- Meta resending the identical POST, not a person submitting a second,
    genuinely new request -- finds its own earlier row instead of minting another confirmation
    code. `issued_at` is Meta's own timestamp on the signed request and does not change on a mere
    retry of the same POST; a fresh request from the same person (a new "Send Request" click) gets
    a new `issued_at` and, correctly, its own confirmation code.
    """

    __tablename__ = "w1_meta_deletion_requests"

    #: Alphanumeric, per Meta's own contract for the field (`docs/development/create-an-app/
    #: app-dashboard/data-deletion-callback`) -- `secrets.token_hex(16)`, 32 hex characters.
    confirmation_code: Mapped[str] = mapped_column(String(43), primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
