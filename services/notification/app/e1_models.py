"""Email open/click tracking tables (slice E1).

`email_links` is the table a signed click token points into: one row per distinct destination URL
in a tracked campaign's body. The token carries a link id, never the URL, and the redirect goes
only to a URL found here, which is exactly "a URL that was in the sent message". The click
endpoint therefore cannot be turned into an open redirect by anyone, signed token or not.

`email_events` is the append-only record of what recipients did with it. **No IP address and no
raw user agent is kept**: the request's user agent is reduced to a class (`desktop`, `mobile`,
`tablet`, `bot`, `unknown`) before it is written, and machine traffic (scanners, prefetchers,
Apple Mail Privacy Protection) is kept but flagged `is_machine` so human counts exclude it.
`UPDATE` and `DELETE` are revoked from the application role in `migrations/env.py`, the same
mechanism as `consent_events`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from platform_core.models import OrgScopedMixin, UUIDPrimaryKeyMixin, utcnow


class EmailLink(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One distinct destination URL of one tracked campaign. Insert-only in practice: a campaign
    is only editable as a draft, and links are registered when it starts sending."""

    __tablename__ = "email_links"

    #: Plain UUID, not a foreign key to `campaigns`: kept consistent with `email_events`.
    campaign_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    #: sha256 hex of `url`, what the per-campaign uniqueness is enforced on (a long URL cannot
    #: sit in a btree unique index safely).
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "campaign_id", "url_hash", name="uq_email_link_url"),
    )


class EmailEvent(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One open or click a recipient's mail client reported. **Append-only.**"""

    __tablename__ = "email_events"

    campaign_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    recipient_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: Null for an open; the `email_links` row for a click.
    link_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: open | click.
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    #: desktop | mobile | tablet | bot | unknown. The class only, never the raw user agent.
    user_agent_class: Mapped[str] = mapped_column(String(10), nullable=False)
    #: True for scanner/prefetch traffic; excluded from every human count.
    is_machine: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint("kind IN ('open', 'click')", name="kind"),
        CheckConstraint(
            "user_agent_class IN ('desktop', 'mobile', 'tablet', 'bot', 'unknown')",
            name="user_agent_class",
        ),
        CheckConstraint("(kind = 'click') = (link_id IS NOT NULL)", name="click_has_link"),
        Index("ix_email_events_org_campaign", "organization_id", "campaign_id", "kind"),
        Index("ix_email_events_org_occurred", "organization_id", "occurred_at"),
        Index("ix_email_events_org_recipient", "organization_id", "recipient_id", "kind"),
    )
