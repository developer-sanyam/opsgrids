"""carrier booking: the BOOKING / BOOKING_FAILED shipment states and the AWB lookup index

Additive. `shipments.status` accepts two more values (the claim -> call -> record split of a live
carrier booking); the CHECK is replaced, no row changes. An index serves the lookup an inbound
carrier tracking event does: `(organization_id, channel_id, tracking_reference)`.

Downgrade refuses while a shipment is in either new state (the old CHECK would reject the row).

Revision ID: a7c3e9d1b5f8
Revises: f4a8c2e6b0d1
Create Date: 2026-10-16 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3e9d1b5f8"  # pragma: allowlist secret
down_revision: str | None = "f4a8c2e6b0d1"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "operations"
CHECK = "ck_shipments_status_known"
OLD = "status IN ('PICKUP_HANDOVER', 'IN_TRANSIT', 'DELIVERED', 'FAILED', 'RETURNED')"
NEW = (
    "status IN ('BOOKING', 'BOOKING_FAILED', 'PICKUP_HANDOVER', 'IN_TRANSIT', "
    "'DELIVERED', 'FAILED', 'RETURNED')"
)


def upgrade() -> None:
    op.drop_constraint(op.f(CHECK), "shipments", schema=SCHEMA, type_="check")
    op.create_check_constraint(op.f(CHECK), "shipments", NEW, schema=SCHEMA)
    op.create_index(
        "ix_shipments_org_channel_awb",
        "shipments",
        ["organization_id", "channel_id", "tracking_reference"],
        schema=SCHEMA,
        postgresql_where=sa.text("channel_id IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text(
            "SELECT 1 FROM operations.shipments "
            "WHERE status IN ('BOOKING', 'BOOKING_FAILED') LIMIT 1"
        )
    ).first():
        raise RuntimeError("shipments in BOOKING / BOOKING_FAILED exist; resolve them first")
    op.drop_index("ix_shipments_org_channel_awb", table_name="shipments", schema=SCHEMA)
    op.drop_constraint(op.f(CHECK), "shipments", schema=SCHEMA, type_="check")
    op.create_check_constraint(op.f(CHECK), "shipments", OLD, schema=SCHEMA)
