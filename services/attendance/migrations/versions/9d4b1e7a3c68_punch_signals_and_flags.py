"""punch signals, flags and per-organization signal settings (R22)

Three new tables for the attendance anti-spoofing stack:

* `signal_settings` — one row per organization: which rules are on and their thresholds. **Every
  rule defaults off**, and an organization with no row has every rule off, so this migration changes
  nothing about what an existing tenant's punches do.
* `punch_signals` — what a client and its network said about one punch. The IP address and the
  device id are **sealed** (`ip_enc`/`device_id_enc`, AES-GCM through `platform_core.pii`) with a
  mask beside each; `device_index` is a keyed blind index used only by the shared-device rule.
* `attendance_flags` — a rule fired on a punch: a reason code and a few counts, no personal data.

Additive only: three new tables and their indexes, nothing changed on an existing table, so the old
code runs safely against this schema for the length of a deploy. The two foreign keys
(`attendance_id`, `signal_id`) stay inside the `attendance` schema.

**Policied in the same migration that creates each table**, with `FORCE` and both `USING` and
`WITH CHECK` — a business table with no policy for even one deploy is a table row-level security
is not yet protecting, and `WITH CHECK` is what stops a tenant inserting a row that belongs to
another. `attendance.current_org()` already exists (`a1b2c3d4e5f6`). Every index leads with
`organization_id`.

Revision ID: 9d4b1e7a3c68
Revises: 7c2e9a4b1d05
Create Date: 2026-09-24 09:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9d4b1e7a3c68"  # pragma: allowlist secret
down_revision: str | None = "7c2e9a4b1d05"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "attendance"
APP_ROLE = "opsgrids_attendance"
TABLES = ("signal_settings", "punch_signals", "attendance_flags")


def upgrade() -> None:
    op.create_table(
        "signal_settings",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("detect_mock_location", sa.Boolean(), nullable=False),
        sa.Column("detect_impossible_travel", sa.Boolean(), nullable=False),
        sa.Column("detect_shared_device", sa.Boolean(), nullable=False),
        sa.Column("detect_ip_outside_allowed", sa.Boolean(), nullable=False),
        sa.Column("max_travel_speed_kmh", sa.Integer(), nullable=False),
        sa.Column("shared_device_window_hours", sa.Integer(), nullable=False),
        sa.Column("allowed_cidrs", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "cardinality(allowed_cidrs) <= 50",
            name=op.f("ck_signal_settings_allowed_cidrs_bounded"),
        ),
        sa.CheckConstraint(
            "max_travel_speed_kmh between 10 and 3000",
            name=op.f("ck_signal_settings_max_travel_speed_range"),
        ),
        sa.CheckConstraint(
            "shared_device_window_hours between 1 and 168",
            name=op.f("ck_signal_settings_shared_device_window_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signal_settings")),
        sa.UniqueConstraint("organization_id", name="uq_signal_settings_organization_id"),
        schema=SCHEMA,
    )

    op.create_table(
        "punch_signals",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("attendance_id", sa.UUID(), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("punched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lat", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("lng", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("accuracy_m", sa.Numeric(precision=9, scale=2), nullable=True),
        sa.Column("mock_location", sa.Boolean(), nullable=True),
        sa.Column("user_agent_class", sa.String(length=20), nullable=True),
        sa.Column("ip_enc", sa.LargeBinary(), nullable=True),
        sa.Column("ip_masked", sa.String(length=128), nullable=True),
        sa.Column("device_id_enc", sa.LargeBinary(), nullable=True),
        sa.Column("device_id_masked", sa.String(length=128), nullable=True),
        sa.Column("device_index", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind in ('check_in','check_out')", name=op.f("ck_punch_signals_kind")),
        sa.ForeignKeyConstraint(
            ["attendance_id"],
            ["attendance.attendance_records.id"],
            name=op.f("fk_punch_signals_attendance_id_attendance_records"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_punch_signals")),
        sa.UniqueConstraint(
            "organization_id", "attendance_id", "kind", name="uq_punch_signals_attendance_kind"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_punch_signals_org_device_punched",
        "punch_signals",
        ["organization_id", "device_index", sa.literal_column("punched_at DESC")],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("device_index is not null"),
    )
    op.create_index(
        "ix_punch_signals_org_employee_punched",
        "punch_signals",
        ["organization_id", "employee_id", sa.literal_column("punched_at DESC")],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("lat is not null and lng is not null"),
    )

    op.create_table(
        "attendance_flags",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("attendance_id", sa.UUID(), nullable=False),
        sa.Column("signal_id", sa.UUID(), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("reason_code", sa.String(length=30), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "reason_code in ('mock_location','impossible_travel','shared_device',"
            "'ip_outside_allowed')",
            name=op.f("ck_attendance_flags_reason_code"),
        ),
        sa.ForeignKeyConstraint(
            ["attendance_id"],
            ["attendance.attendance_records.id"],
            name=op.f("fk_attendance_flags_attendance_id_attendance_records"),
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["attendance.punch_signals.id"],
            name=op.f("fk_attendance_flags_signal_id_punch_signals"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attendance_flags")),
        sa.UniqueConstraint(
            "organization_id", "signal_id", "reason_code", name="uq_attendance_flags_signal_reason"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_attendance_flags_org_created",
        "attendance_flags",
        ["organization_id", sa.literal_column("created_at DESC")],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_attendance_flags_org_employee_created",
        "attendance_flags",
        ["organization_id", "employee_id", sa.literal_column("created_at DESC")],
        unique=False,
        schema=SCHEMA,
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {SCHEMA}.{table}
                FOR ALL TO {APP_ROLE}
                USING      (organization_id = {SCHEMA}.current_org())
                WITH CHECK (organization_id = {SCHEMA}.current_org())
            """
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
    op.drop_index("ix_attendance_flags_org_employee_created", table_name="attendance_flags", schema=SCHEMA)
    op.drop_index("ix_attendance_flags_org_created", table_name="attendance_flags", schema=SCHEMA)
    op.drop_table("attendance_flags", schema=SCHEMA)
    op.drop_index(
        "ix_punch_signals_org_employee_punched",
        table_name="punch_signals",
        schema=SCHEMA,
        postgresql_where=sa.text("lat is not null and lng is not null"),
    )
    op.drop_index(
        "ix_punch_signals_org_device_punched",
        table_name="punch_signals",
        schema=SCHEMA,
        postgresql_where=sa.text("device_index is not null"),
    )
    op.drop_table("punch_signals", schema=SCHEMA)
    op.drop_table("signal_settings", schema=SCHEMA)
