"""WhatsApp cost from Meta's per-message pricing (ENGAGE-10)

Expand-only:

* `messages.pricing_category` and `messages.billable` (both nullable): what the `pricing` object on
  Meta's status webhook said this message was billed as. Null until a receipt carrying one lands.
* `messaging_rate_cards.calling_code` and `.market`: a price is now per **market**, identified by
  the recipient number's longest matching ITU calling-code prefix (`91` India, `1` North America,
  `77` Kazakhstan inside `7` Russia, and `''` for "Other" -- every number no row claims). Existing
  rows default to `''`, i.e. they apply to every market, which is exactly what they meant before.
  The unique price-point constraint gains `calling_code`.
* The platform's shipped `placeholder-2026-09` rows are soft-deleted (`deleted_at`), never edited,
  and Meta's published INR rate card is seeded under the platform tenant, label
  `meta-inr-2026-10-01`. Source: "Cost per message in INR on the WhatsApp Business Platform,
  effective October 1, 2026" (developers.facebook.com/docs/whatsapp/pricing, the INR rates file,
  downloaded 2026-10-12). Rupees per message x 100000 = paise per 1000 messages, exact.
  `service` is seeded at 0: Meta does not bill a service message (`billable: false`,
  `free_customer_service`), so pricing one would only ever be wrong.

Downgrade deletes the seeded Meta rows, restores the placeholders, removes any row priced for a
specific market (the old table cannot say which market it was for), and drops the columns.

Revision ID: c7d2a9e4f1b6
Revises: f1c8a2d5b7e4
Create Date: 2026-10-12 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d2a9e4f1b6"  # pragma: allowlist secret
down_revision: str | None = "f1c8a2d5b7e4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
LOCK_TIMEOUT = "5s"
PLATFORM_ORG = "00000000-0000-0000-0000-000000000000"
PLACEHOLDER_LABEL = "placeholder-2026-09"
META_LABEL = "meta-inr-2026-10-01"
META_EFFECTIVE_FROM = "2026-10-01"

#: (calling code, market, marketing, utility, authentication) in INR paise per 1000 messages,
#: generated from Meta's INR rate card effective 2026-10-01. Kazakhstan is +76/+77 inside
#: Russia's +7. shortcut: Meta's "Rest of ..." regions have no row here and are priced as "Other";
#: add their calling codes through the staff endpoint if a customer sends there in volume.
META_INR_RATES: tuple[tuple[str, str, int, int, int], ...] = (
    ("54", "Argentina", 452930, 190620, 190620),
    ("880", "Bangladesh", 536640, 27120, 27120),
    ("55", "Brazil", 458080, 49840, 49840),
    ("56", "Chile", 651350, 146540, 146540),
    ("57", "Colombia", 91610, 5880, 5880),
    ("20", "Egypt", 471910, 26390, 26390),
    ("33", "France", 629900, 219940, 219940),
    ("49", "Germany", 1000730, 403220, 403220),
    ("852", "Hong Kong", 536640, 190560, 190560),
    ("36", "Hungary", 629970, 255830, 255830),
    ("91", "India", 86310, 11500, 11500),
    ("62", "Indonesia", 301110, 183160, 183160),
    ("964", "Iraq", 249930, 57860, 57860),
    ("972", "Israel", 258710, 38850, 38850),
    ("39", "Italy", 582060, 219740, 219740),
    ("76", "Kazakhstan", 442480, 131870, 131870),
    ("77", "Kazakhstan", 442480, 131870, 131870),
    ("965", "Kuwait", 580590, 322260, 322260),
    ("60", "Malaysia", 630370, 102620, 102620),
    ("52", "Mexico", 290640, 62260, 62260),
    ("212", "Morocco", 303540, 168480, 168480),
    ("31", "Netherlands", 1170780, 366560, 366560),
    ("977", "Nepal", 536640, 24920, 24920),
    ("234", "Nigeria", 378510, 49150, 49150),
    ("968", "Oman", 249930, 180910, 180910),
    ("92", "Pakistan", 346830, 110000, 110000),
    ("51", "Peru", 515360, 219930, 219930),
    ("48", "Poland", 267540, 89180, 89180),
    ("974", "Qatar", 249930, 87890, 87890),
    ("40", "Romania", 629970, 211970, 211970),
    ("7", "Russia", 587670, 293110, 293110),
    ("966", "Saudi Arabia", 421140, 78340, 78340),
    ("65", "Singapore", 536640, 117270, 117270),
    ("27", "South Africa", 278210, 69740, 69740),
    ("34", "Spain", 518010, 146480, 146480),
    ("94", "Sri Lanka", 536640, 14660, 14660),
    ("90", "Turkey", 79890, 6600, 6600),
    ("380", "Ukraine", 629970, 217820, 217820),
    ("971", "United Arab Emirates", 421140, 115090, 115090),
    ("44", "United Kingdom", 464960, 161220, 161220),
    ("1", "North America", 183130, 24910, 24910),
    ("", "Other", 442480, 56410, 56410),
)


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.add_column(
        "messages",
        sa.Column("pricing_category", sa.String(length=40), nullable=True),
        schema=SCHEMA,
    )
    op.add_column("messages", sa.Column("billable", sa.Boolean(), nullable=True), schema=SCHEMA)
    op.add_column(
        "messaging_rate_cards",
        sa.Column("calling_code", sa.String(length=4), server_default="", nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "messaging_rate_cards",
        sa.Column("market", sa.String(length=60), server_default="", nullable=False),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_messaging_rate_cards_calling_code"),
        "messaging_rate_cards",
        "calling_code ~ '^[0-9]{0,4}$'",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "uq_messaging_rate_cards_org_price_point", "messaging_rate_cards", schema=SCHEMA
    )
    op.create_unique_constraint(
        "uq_messaging_rate_cards_org_market_price_point",
        "messaging_rate_cards",
        ["organization_id", "channel", "calling_code", "category", "effective_from"],
        schema=SCHEMA,
    )

    op.execute(
        sa.text(
            "UPDATE notification.messaging_rate_cards SET deleted_at = now() "
            "WHERE organization_id = CAST(:org AS uuid) AND label = :label"
        ).bindparams(org=PLATFORM_ORG, label=PLACEHOLDER_LABEL)
    )
    insert = sa.text(
        """
        INSERT INTO notification.messaging_rate_cards
            (id, organization_id, channel, calling_code, market, category,
             amount_minor_per_1000_messages, currency, effective_from, label, is_placeholder,
             created_at, updated_at)
        VALUES
            (gen_random_uuid(), CAST(:org AS uuid), 'whatsapp', :code, :market, :category, :amount,
             'INR', CAST(:effective AS date), :label, false, now(), now())
        ON CONFLICT ON CONSTRAINT uq_messaging_rate_cards_org_market_price_point DO NOTHING
        """
    )
    for code, market, marketing, utility, authentication in META_INR_RATES:
        for category, amount in (
            ("marketing", marketing),
            ("utility", utility),
            ("authentication", authentication),
            ("service", 0),
        ):
            op.execute(
                insert.bindparams(
                    org=PLATFORM_ORG,
                    code=code,
                    market=market,
                    category=category,
                    amount=amount,
                    effective=META_EFFECTIVE_FROM,
                    label=META_LABEL,
                )
            )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute(
        sa.text(
            "DELETE FROM notification.messaging_rate_cards WHERE calling_code <> '' "
            "OR (organization_id = CAST(:org AS uuid) AND label <> :label)"
        ).bindparams(org=PLATFORM_ORG, label=PLACEHOLDER_LABEL)
    )
    op.execute(
        sa.text(
            "UPDATE notification.messaging_rate_cards SET deleted_at = NULL "
            "WHERE organization_id = CAST(:org AS uuid) AND label = :label"
        ).bindparams(org=PLATFORM_ORG, label=PLACEHOLDER_LABEL)
    )
    op.drop_constraint(
        "uq_messaging_rate_cards_org_market_price_point", "messaging_rate_cards", schema=SCHEMA
    )
    op.create_unique_constraint(
        "uq_messaging_rate_cards_org_price_point",
        "messaging_rate_cards",
        ["organization_id", "channel", "category", "effective_from"],
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("ck_messaging_rate_cards_calling_code"), "messaging_rate_cards", schema=SCHEMA
    )
    op.drop_column("messaging_rate_cards", "market", schema=SCHEMA)
    op.drop_column("messaging_rate_cards", "calling_code", schema=SCHEMA)
    op.drop_column("messages", "billable", schema=SCHEMA)
    op.drop_column("messages", "pricing_category", schema=SCHEMA)
