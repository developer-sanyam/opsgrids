"""Tables and indexes for the messaging-analytics slice (M1).

`MessagingRateCard` is what turns a message count into a cost *estimate*. It holds two kinds of
row, told apart by `organization_id`:

  * the **platform default**, under the nil UUID (the platform tenant, the same convention the
    platform password-reset rule uses). Shipped by the migration, `is_placeholder = true` and
    labelled `placeholder-2026-09`. These figures are **not** Meta's published rates for any
    particular country: they exist so an estimate is never blank, and they must be replaced with
    the organization's real rates (`PUT /engage/analytics/rate-card`) before anyone reads a
    cost off them as more than an order of magnitude.
  * an **organization override**, written by that organization. Precedence is decided in
    `m1_analytics.effective_rates`: for one category, an organization's own row that is already in
    force beats the platform default, and the latest `effective_from` wins within either.

Money is integer minor units per 1000 messages plus an ISO currency — never a float, never
`Numeric(_, 2)`. Rows are never edited to change history: a change of price is a new row with a
later `effective_from`; re-saving the same `effective_from` replaces that one row.

The indexes at the bottom belong to *other* tables (`messages`, `inbound_messages`,
`conversations`). They are declared here, against those tables, so the slice is one module to
review and one migration to ship, and so the models and the migration cannot drift apart.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, Conversation, InboundMessage, Message
from platform_core.models import TenantBase

#: The platform tenant. Default rate-card rows live under it; nothing else writes there.
PLATFORM_ORG_ID = "00000000-0000-0000-0000-000000000000"

#: The label the shipped defaults carry, so an API consumer (and the UI) can say what they are.
PLACEHOLDER_LABEL = "placeholder-2026-09"


class MessagingRateCard(Base, TenantBase):
    """One price: what `channel` charges per 1000 messages of `category`, from `effective_from`."""

    __tablename__ = "messaging_rate_cards"

    #: whatsapp today; a column so the same card can price SMS later.
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="whatsapp")
    #: marketing | utility | authentication | service. `service` is the free-text reply inside
    #: the 24-hour window, which Meta does not charge for.
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Integer minor units (paise) per 1000 messages. 78460 paise = 784.60 rupees per 1000 =
    #: 78.46 paise per message; the estimate is `messages * rate / 1000`, rounded half-up once
    #: per category.
    amount_minor_per_1000_messages: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: ISO 4217.
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    #: The first UTC day this price applies to.
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    #: `placeholder-2026-09` for the shipped defaults, `organization` for an override.
    label: Mapped[str] = mapped_column(String(60), nullable=False, default="organization")
    #: True only for the shipped defaults. Surfaced in the API so nobody mistakes them for Meta's
    #: real rates.
    is_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint(
            "category IN ('marketing', 'utility', 'authentication', 'service')",
            name="category",
        ),
        CheckConstraint("channel IN ('whatsapp')", name="channel"),
        CheckConstraint("amount_minor_per_1000_messages >= 0", name="amount_nonnegative"),
        UniqueConstraint(
            "organization_id",
            "channel",
            "category",
            "effective_from",
            name="uq_messaging_rate_cards_org_price_point",
        ),
    )


# --------------------------------------------------------------------- indexes on other tables

#: The outbound series and the by-category count: one org, one channel, a range of days.
#: `state` and `direction` ride along so the grouped counts do not need the heap row for the
#: filter. Plain btree order is `(organization_id, channel, created_at)`, which is exactly the
#: predicate every M1 message query carries.
Index(
    "ix_messages_org_channel_created",
    Message.__table__.c.organization_id,
    Message.__table__.c.channel,
    Message.__table__.c.created_at,
    postgresql_include=["state", "direction"],
)

#: Replies and inbound volume are counted by `occurred_at` across the whole organization. The
#: existing index leads with `conversation_id`, which is the wrong shape for a date-range scan.
Index(
    "ix_inbound_messages_org_occurred",
    InboundMessage.__table__.c.organization_id,
    InboundMessage.__table__.c.occurred_at,
)

#: New conversations in a window, for the recent-activity feed.
Index(
    "ix_conversations_org_created",
    Conversation.__table__.c.organization_id,
    Conversation.__table__.c.created_at,
)
