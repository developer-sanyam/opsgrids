"""add staff enrollment codes

The operator bootstrap the security review demanded: a staff account must present a code
issued out-of-band, not just its password, before it may enrol its first MFA factor — see
`app/models.py:StaffEnrollmentCode` for why a password alone is not enough. Un-policied like
every other table in this schema except `org_idp_configs` (ADR-0002 §6): this is vendor data,
keyed by no organization.

Revision ID: e2d93c244838
Revises: e91a4c2f6b3d
Create Date: 2026-09-23 21:55:05.908172
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2d93c244838"  # pragma: allowlist secret
down_revision: str | None = "e91a4c2f6b3d"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_enrollment_codes",
        sa.Column("staff_id", sa.UUID(), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("staff_id", name=op.f("pk_staff_enrollment_codes")),
        schema="auth",
    )


def downgrade() -> None:
    op.drop_table("staff_enrollment_codes", schema="auth")
