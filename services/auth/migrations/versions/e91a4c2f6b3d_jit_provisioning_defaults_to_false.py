"""jit_provisioning defaults to false, and existing rows are corrected

`org_idp_configs.jit_provisioning` was added `nullable=False, default=True` — a Python-side
default only, applied by the ORM on insert, with nothing at the database enforcing it. The flag
is inert today (`services.sso_callback` refuses an unknown email regardless of what it says; see
the column's docstring in `app/models.py`), but it names what happens the day it stops being
inert: an external identity provider gaining the ability to create a directory user, a
membership and role grants for anyone it vouches for, with no invitation and no administrator
decision in between. Defaulting that to *on* is the wrong failure mode for a flag that currently
does nothing but will eventually do something dangerous — a row nobody explicitly configured
must land on "provisioning stays a door only an invitation opens", not on whatever the ORM
happened to default to.

Two changes, both in this migration:

* every existing row is corrected to `false` — there is no production configuration where this
  was ever meant to be `true` today, because the code that would honour it does not exist yet;
* the default moves to the database itself (`server_default`), so a row inserted by anything
  that is not this service's ORM — a fixture, a repair script, a future migration — lands on the
  safe answer too, matching the model change in the same commit.

Revision ID: e91a4c2f6b3d
Revises: c4f7a1e9d3b2
Create Date: 2026-09-23 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e91a4c2f6b3d"  # pragma: allowlist secret
down_revision: str | None = "c4f7a1e9d3b2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "auth"
TABLE = "org_idp_configs"
COLUMN = "jit_provisioning"


def upgrade() -> None:
    op.alter_column(
        TABLE,
        COLUMN,
        server_default=sa.text("false"),
        schema=SCHEMA,
    )
    op.execute(
        sa.text(f'UPDATE "{SCHEMA}"."{TABLE}" SET {COLUMN} = false WHERE {COLUMN} = true')
    )


def downgrade() -> None:
    # The data correction is deliberately not reversed: there is no row for which `true` was
    # ever the intended value, so "downgrade" restores the column's old default, not the old
    # (wrong) data.
    op.alter_column(
        TABLE,
        COLUMN,
        server_default=None,
        schema=SCHEMA,
    )
