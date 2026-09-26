"""one running WhatsApp setup per organization

`start_signup` refused a second setup while one was running by reading the running rows and then
inserting: two requests with different codes could both read nothing and both insert. A partial
unique index makes the database the arbiter, and the insert turns the conflict into the same 409
`setup_in_progress` the read produces.

Any organization that already has more than one `running` row (only possible through that race)
keeps its newest and has the older ones failed as `abandoned`, sealed code and token wiped, so the
index can be built. If a row is still left after that (a role the tenant policy hides from this
migration), the index build fails loudly rather than being skipped.

Revision ID: c5e19a7d3f48
Revises: b3f8d20c71a5
Create Date: 2026-09-26 15:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5e19a7d3f48"  # pragma: allowlist secret
down_revision: str | None = "b3f8d20c71a5"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
INDEX = "uq_w1_setup_runs_org_running"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE {SCHEMA}.w1_setup_runs AS r
           SET status = 'failed',
               error_code = 'abandoned',
               finished_at = now(),
               code_enc = NULL,
               token_enc = NULL
          FROM (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY organization_id ORDER BY created_at DESC, id DESC
                       ) AS rank
                  FROM {SCHEMA}.w1_setup_runs
                 WHERE status = 'running'
               ) AS ranked
         WHERE r.id = ranked.id AND ranked.rank > 1
        """
    )
    op.create_index(
        INDEX,
        "w1_setup_runs",
        ["organization_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="w1_setup_runs", schema=SCHEMA)
