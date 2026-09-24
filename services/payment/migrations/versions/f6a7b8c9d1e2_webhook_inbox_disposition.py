"""webhook inbox: a stated outcome per event, and no empty event id (PAY-A)

Additive only — two columns (one nullable, one with a constant server default, so a catalogue
change on a live table) and three CHECK constraints, nothing dropped or rewritten. A rolling deploy
can run the old and the new code side by side: old code inserts rows without naming either column
and gets `processed` / NULL, which is what it always meant.

- **`disposition` / `disposition_reason`.** `provider_events` is insert-only (append-only trigger),
  so what the platform did with an authentic webhook has to be stated when the row is written. A
  webhook that names a charge this organization does not have (`ignored`), or contradicts the one
  it does — an outcome the state machine forbids, another amount, another currency (`conflict`) —
  is a *permanent* condition: answering it with a 4xx makes the provider redeliver it for days and
  records nothing. It is now recorded, audited and answered 200, with a stable reason code.
  `disposition_reason` is set exactly when `disposition` is not `processed`.

- **`provider_event_id` is never empty.** The dedupe key is `(organization_id, provider_event_id)`.
  Razorpay's body has no top-level `id` (the event id is the `X-Razorpay-Event-Id` header), and the
  adapter used to default the missing id to `''` — so every event after the first, per
  organization, was dropped as a duplicate. The service now resolves a real id or refuses the
  webhook; this CHECK makes the empty string impossible at the database as well.

  It is created `NOT VALID` on purpose: a database that already holds a row keyed `''` (the very
  defect this fixes) must still upgrade, and `NOT VALID` enforces the rule on every new or updated
  row without scanning or rejecting the old ones. An operator who has confirmed there are none can
  `VALIDATE CONSTRAINT` at leisure; nothing depends on it.

Revision ID: f6a7b8c9d1e2
Revises: e5f6a7b8c9d1
Create Date: 2026-09-24 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d1e2"  # pragma: allowlist secret
down_revision: str | None = "e5f6a7b8c9d1"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "payment"


def upgrade() -> None:
    op.add_column(
        "provider_events",
        sa.Column(
            "disposition",
            sa.String(length=16),
            server_default=sa.text("'processed'"),
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "provider_events",
        sa.Column("disposition_reason", sa.String(length=40), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_provider_events_disposition"),
        "provider_events",
        "disposition in ('processed', 'ignored', 'conflict')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_provider_events_disposition_reason_matches"),
        "provider_events",
        "(disposition = 'processed') = (disposition_reason IS NULL)",
        schema=SCHEMA,
    )
    # NOT VALID: enforced for every new row, without failing on a legacy '' (see above). Raw SQL
    # because `op.create_check_constraint` has no way to say it.
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.provider_events
            ADD CONSTRAINT ck_provider_events_provider_event_id_not_empty
            CHECK (length(provider_event_id) > 0) NOT VALID
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.provider_events
            DROP CONSTRAINT IF EXISTS ck_provider_events_provider_event_id_not_empty
        """
    )
    op.drop_constraint(
        op.f("ck_provider_events_disposition_reason_matches"),
        "provider_events",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_provider_events_disposition"), "provider_events", schema=SCHEMA, type_="check"
    )
    op.drop_column("provider_events", "disposition_reason", schema=SCHEMA)
    op.drop_column("provider_events", "disposition", schema=SCHEMA)
