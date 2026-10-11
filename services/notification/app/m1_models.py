"""Tables and indexes for the messaging-analytics slice (M1).

`MessagingRateCard` is what turns a message count into a cost *estimate*. It holds two kinds of
row, told apart by `organization_id`:

  * the **platform default**, under the nil UUID (the platform tenant, the same convention the
    platform password-reset rule uses): one row per recipient **market** (`calling_code`, `''`
    for "Other") and category, seeded from Meta's published INR rate card effective 2026-10-01
    (`META_RATE_LABEL`, migration `c7d2a9e4f1b6`) and maintained by staff through
    `PUT /admin/engage/whatsapp-rate-card`. The first shipped rows (`placeholder-2026-09`) were
    soft-deleted by that migration.
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

#: The label the first shipped defaults carried. Soft-deleted by `c7d2a9e4f1b6`; kept so a
#: reader of an old row (and that migration's downgrade) can still say what it was.
PLACEHOLDER_LABEL = "placeholder-2026-09"

#: The label of the platform rows seeded from Meta's published INR rate card, effective
#: 2026-10-01 (see migration `c7d2a9e4f1b6` for the source). Staff replace it through
#: `PUT /admin/engage/whatsapp-rate-card` when Meta publishes a new card.
META_RATE_LABEL = "meta-inr-2026-10-01"


class MessagingRateCard(Base, TenantBase):
    """One price: what `channel` charges per 1000 messages of `category`, from `effective_from`."""

    __tablename__ = "messaging_rate_cards"

    #: whatsapp today; a column so the same card can price SMS later.
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="whatsapp")
    #: The recipient market, as an ITU calling-code prefix (`91`, `1`, `77`); `''` is "Other",
    #: every number no longer prefix claims. A message is priced by its number's longest match.
    calling_code: Mapped[str] = mapped_column(
        String(4), nullable=False, default="", server_default=""
    )
    #: Meta's name for the market ("India", "North America", "Other"), for display only.
    market: Mapped[str] = mapped_column(String(60), nullable=False, default="", server_default="")
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
    #: Where the figures came from: `meta-inr-2026-10-01` for the seeded platform card,
    #: `organization` for an override.
    label: Mapped[str] = mapped_column(String(60), nullable=False, default="organization")
    #: True only for the first shipped (now soft-deleted) defaults. Surfaced in the API so nobody
    #: mistakes a placeholder for Meta's real rates.
    is_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint(
            "category IN ('marketing', 'utility', 'authentication', 'service')",
            name="category",
        ),
        CheckConstraint("channel IN ('whatsapp')", name="channel"),
        CheckConstraint("amount_minor_per_1000_messages >= 0", name="amount_nonnegative"),
        CheckConstraint("calling_code ~ '^[0-9]{0,4}$'", name="calling_code"),
        UniqueConstraint(
            "organization_id",
            "channel",
            "calling_code",
            "category",
            "effective_from",
            name="uq_messaging_rate_cards_org_market_price_point",
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
