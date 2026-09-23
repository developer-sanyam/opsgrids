"""custom domain claims

ADR-0012 slice 1. Two things, in one migration because the second only makes sense with the
first: **`custom_domain_claims`**, the evidence table behind `storefronts.custom_domain_status`
(one claim per organization, one row's worth of per-probe state, the token, the 14-day expiry
clock); and **widening `custom_domain_status` from two values to five** — `unverified` and
`verified` keep their exact meanings, and `live`, `failing` and `suspended` are the rest of the
state machine `app/domain.py` and `app/cron.py` implement. ADR-0011's own column comment
predicted this widening: "the hosting slice that adds DNS verification is then a validation
change and a new endpoint, not a migration against a table with live tenant rows." There are
still zero live rows behind a custom domain — nothing in this platform has ever served one — so
as far as this codebase's own history is concerned it is exactly that: a check-constraint edit
and nothing else. This migration does not *rely* on that being true, though —
`_backfill_claims_for_pre_existing_domains` below gives any `storefronts` row that already
carries a `custom_domain` (ADR-0011's older, evidence-free path) a real claim rather than
leaving it a row the sweep can never pick up.

**Why a second table rather than more columns on `storefronts`** (ADR-0012 §9): `storefronts` is
the row every anonymous request reads and the row the 300-second Redis cache is built from, and a
failed probe bumping its `updated_at` on every check would put write churn on the platform's
most-read table for no reader's benefit. The cost, paid here rather than hidden: a state
transition now writes two tables in one transaction (`app/services.py`'s `claim_custom_domain`,
`release_custom_domain`; `app/cron.py`'s sweep), and a reader has to know that
`custom_domain_status` is the state and this table is the evidence.

**One index — `UNIQUE (organization_id)`** — and no others, following ADR-0012 §9's own
index-timing register: the sweep discovers *organizations* from `storefronts.custom_domain IS
NOT NULL` on the bypass reader and then reads at most one claim row per tenant here, so this
unique index already answers both the merchant's own read and the sweep's. A global
`next_check_at` index is rejected in the ADR for the reason every rejected index in this codebase
is — it would not lead with `organization_id`, which would mean widening
`opsgrids_storefront_directory` to a second table, the exact chokepoint ADR-0010 §4 exists to
keep at one.

Row-level security: `ENABLE` **and** `FORCE`, `USING` **and** `WITH CHECK`, the same shape
`c71a5e0f4b82` and `c3fcc6691235` already used for every other table in this schema.

`storefront_id` is an in-schema `ON DELETE CASCADE` foreign key — allowed; only *cross-schema*
foreign keys are banned (ADR-0000 §10) — so a storefront row can never outlive its own claim in
an inconsistent way.

Revision ID: ea0b8b84b84d
Revises: c3fcc6691235
Create Date: 2026-09-22 09:00:00.000000
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision: str = 'ea0b8b84b84d'  # pragma: allowlist secret — an alembic revision id
down_revision: str | None = 'c3fcc6691235'  # pragma: allowlist secret — an alembic revision id
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "storefront"
APP_ROLE = "opsgrids_storefront"
TABLE = "custom_domain_claims"


def upgrade() -> None:
    # The check constraint predates this migration and has never had a live row use anything
    # but 'unverified' or null — ADR-0011's own comment names this exact moment. Drop and
    # recreate under the same name so downstream tooling (and the model's own `__table_args__`)
    # never has to know it moved.
    op.drop_constraint(op.f('ck_storefronts_custom_domain_status'), 'storefronts', schema=SCHEMA, type_='check')
    op.create_check_constraint(
        op.f('ck_storefronts_custom_domain_status'),
        'storefronts',
        "(custom_domain IS NULL AND custom_domain_status IS NULL)"
        " OR (custom_domain IS NOT NULL"
        " AND custom_domain_status IN ('unverified', 'verified', 'live', 'failing', 'suspended'))",
        schema=SCHEMA,
    )

    op.create_table(
        'custom_domain_claims',
        sa.Column('storefront_id', sa.UUID(), nullable=False),
        sa.Column('verification_token', sa.String(length=64), nullable=False),
        sa.Column('token_issued_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('claim_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('live_since', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_check_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consecutive_failures', sa.Integer(), nullable=False),
        sa.Column('last_failure_counted_on', sa.Date(), nullable=True),
        sa.Column('ownership_state', sa.String(length=16), nullable=False),
        sa.Column('routing_state', sa.String(length=16), nullable=False),
        sa.Column('certificate_state', sa.String(length=16), nullable=False),
        sa.Column('last_error_code', sa.String(length=40), nullable=True),
        sa.Column('last_error_detail', sa.String(length=500), nullable=True),
        sa.Column('tls_not_after', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tls_last_ok_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['storefront_id'], [f'{SCHEMA}.storefronts.id'],
            name=op.f('fk_custom_domain_claims_storefront_id_storefronts'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_custom_domain_claims')),
        # One claim per organization — the merchant's own read and the sweep's per-tenant read
        # both key on it (ADR-0012 §9's index-timing register). A `UniqueConstraint` here rather
        # than `op.create_index(..., unique=True)`, to match `CustomDomainClaim.__table_args__`
        # exactly — the two render identically in Postgres but only one of them is what the ORM
        # model declares, and autogenerate diffs on that.
        sa.UniqueConstraint('organization_id', name='uq_custom_domain_claims_organization_id'),
        sa.CheckConstraint("ownership_state IN ('pending', 'ok', 'failing')", name=op.f('ck_custom_domain_claims_ownership_state')),
        sa.CheckConstraint("routing_state IN ('pending', 'ok', 'failing')", name=op.f('ck_custom_domain_claims_routing_state')),
        sa.CheckConstraint("certificate_state IN ('pending', 'ok', 'failing')", name=op.f('ck_custom_domain_claims_certificate_state')),
        sa.CheckConstraint("consecutive_failures >= 0", name=op.f('ck_custom_domain_claims_consecutive_failures_not_negative')),
        schema=SCHEMA,
    )
    op.create_index(
        op.f('ix_storefront_custom_domain_claims_organization_id'), 'custom_domain_claims',
        ['organization_id'], unique=False, schema=SCHEMA,
    )
    # No index on `storefront_id`: this table is never looked up by it (always by
    # `organization_id`, at most one row), and Postgres needs no index on a referencing column
    # for the foreign key itself to work — ADR-0012 §9's "one index on a new table is unusual
    # enough to be worth the sentence" is the register this follows.

    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.{TABLE}
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )

    _backfill_claims_for_pre_existing_domains(op.get_bind())


def _backfill_claims_for_pre_existing_domains(connection: sa.engine.Connection) -> None:
    """A defensive backfill, not a data fix this environment currently needs.

    ADR-0011 shipped `storefronts.custom_domain` before this table existed, and this migration's
    own history records "there are zero live rows behind a custom domain — nothing in this
    platform has ever served one" as true *as this was written*. A migration that only behaves
    correctly while that stays true is a landmine for whichever hotfix, manual seed or restored
    backup makes it stop being true: a `storefronts` row with `custom_domain` set and no matching
    claim is exactly the shape `app/cron.py`'s sweep already documents skipping forever
    (`TestTheStorePutSeam`) — silent, permanent squatting on a name nobody can ever verify or
    release, because `PUT /storefront/store/domain` is now the only writer that can accept the
    value in the first place, and this row is not new (see finding 2b's fix to `StorePut`).
    **Every such row gets a real claim instead**: `unverified`, a freshly minted token — the
    checks have not run yet on this specific claim, so `pending` and not `verified` is the honest
    starting `ownership_state` even for a row ADR-0011 believed proved control at the time it was
    set — and a 14-day expiry from *now*, exactly what claiming it through the dedicated route
    would have produced. The very next sweep tick picks these up instead of ignoring them.
    """
    rows = connection.execute(
        sa.text(
            f"SELECT id, organization_id FROM {SCHEMA}.storefronts WHERE custom_domain IS NOT NULL"
        )
    ).fetchall()
    if not rows:
        return

    now = datetime.now(UTC)
    connection.execute(
        sa.text(
            f"""
            INSERT INTO {SCHEMA}.{TABLE}
                (id, organization_id, storefront_id, verification_token, token_issued_at,
                 claim_expires_at, next_check_at, consecutive_failures,
                 ownership_state, routing_state, certificate_state, created_at, updated_at)
            VALUES
                (:id, :organization_id, :storefront_id, :verification_token, :now,
                 :claim_expires_at, :now, 0, 'pending', 'pending', 'pending', :now, :now)
            """
        ),
        [
            {
                "id": str(uuid.uuid4()),
                "organization_id": str(organization_id),
                "storefront_id": str(storefront_id),
                "verification_token": secrets.token_hex(16),
                "now": now,
                "claim_expires_at": now + timedelta(days=14),
            }
            for storefront_id, organization_id in rows
        ],
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{TABLE}")
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{TABLE} DISABLE ROW LEVEL SECURITY")

    op.drop_index(op.f('ix_storefront_custom_domain_claims_organization_id'), table_name='custom_domain_claims', schema=SCHEMA)
    op.drop_table('custom_domain_claims', schema=SCHEMA)

    op.drop_constraint(op.f('ck_storefronts_custom_domain_status'), 'storefronts', schema=SCHEMA, type_='check')
    # A domain currently `live`, `failing` or `suspended` cannot survive this downgrade with its
    # status intact — there is nowhere in the two-value world for it to go. Rather than fail the
    # downgrade outright (leaving the migration unrunnable in either direction) or silently pick
    # a value, any row outside the old two values is pulled back to `unverified`: honest about
    # having lost verified/served history, and never leaves a row the old constraint would
    # reject.
    op.execute(
        f"UPDATE {SCHEMA}.storefronts SET custom_domain_status = 'unverified' "
        "WHERE custom_domain_status NOT IN ('unverified', 'verified')"
    )
    op.create_check_constraint(
        op.f('ck_storefronts_custom_domain_status'),
        'storefronts',
        "(custom_domain IS NULL AND custom_domain_status IS NULL)"
        " OR (custom_domain IS NOT NULL AND custom_domain_status IN ('unverified', 'verified'))",
        schema=SCHEMA,
    )
