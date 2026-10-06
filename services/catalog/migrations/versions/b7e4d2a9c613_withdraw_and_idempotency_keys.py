"""withdraw a published version, freeze grandfathered rows, and claim idempotency keys

Two changes for the operator Plans & prices page (docs/superpowers/specs/
2026-10-06-admin-plans-page-design.md, ADR-0030).

**The version-row trigger learns exactly one transition.** `forbid_published_version_update`
(migration 549e7957161c) refused every UPDATE of a `published` row and nothing else, which left
two problems:

- there was no way to stop selling a version at all — `published -> grandfathered` (keep every
  existing pin, refuse new ones) was blocked like any other edit; and
- a `grandfathered` row was not covered, so a raw UPDATE could rewrite a pinned version's
  currency or interval, or flip it back to `published`, with nothing below the application to
  stop it.

The replacement allows one change on a published row — `state` from `published` to
`grandfathered` with **every other column identical** (compared as whole rows, so a column added
later is covered without editing this function) — and refuses any UPDATE of a grandfathered row.
Drafts stay freely editable, except that a draft cannot become `grandfathered` (only a published
version can be withdrawn). The delete trigger (e3f8a91c7b2d) already treats `grandfathered` as
frozen and is unchanged.

**The child-row trigger also judges the parent a row is written under.** The e3f8a91c7b2d
function (on `prices`, `features`, and since 8fef75a4a797 `seat_classes`) fired on UPDATE and
DELETE and checked OLD's parent only, so an INSERT under a frozen version — or an UPDATE moving a
draft's row onto one — changed a pinned sheet with nothing below the application to stop it. The
function now checks NEW's parent on INSERT and UPDATE as well, and a second trigger per table
(`trg_<table>_frozen_no_insert`) runs it on INSERT.

**`idempotency_keys`** is the claim table behind the now-required `Idempotency-Key` on every
`/admin/catalog` write: unique on `(organization_id, key)`, claimed before the change in the same
transaction, carrying the fingerprint of the request and the response to replay. It records a
platform act like `audit_logs`, so it carries the platform sentinel organization and the same
tenant policy (ENABLE + FORCE).

Revision ID: b7e4d2a9c613
Revises: c5f1e8a2b7d4
Create Date: 2026-10-06 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7e4d2a9c613"  # pragma: allowlist secret
down_revision: str | None = "c5f1e8a2b7d4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catalog"
APP_ROLE = "opsgrids_catalog"
FROZEN_STATES = "('published', 'grandfathered')"
#: Every table whose rows belong to one version (e3f8a91c7b2d, 8fef75a4a797).
CHILD_TABLES = ("prices", "features", "seat_classes")


def _child_function(*, check_new: bool) -> str:
    """`forbid_frozen_version_child_mutation`: with `check_new`, the parent a row is written
    *under* is judged too (INSERT, and the target of an UPDATE); without it, e3f8a91c7b2d's
    body exactly, for the downgrade."""
    new_check = (
        f"""
            IF TG_OP IN ('INSERT', 'UPDATE') THEN
                SELECT state INTO parent_state
                    FROM {SCHEMA}.plan_versions
                    WHERE id = NEW.plan_version_id;
                IF parent_state IN {FROZEN_STATES} THEN
                    RAISE EXCEPTION
                        'plan_version % is % and its % rows are immutable; clone into a new draft'
                        , NEW.plan_version_id
                        , parent_state
                        , TG_TABLE_NAME
                        USING ERRCODE = 'restrict_violation';
                END IF;
            END IF;"""
        if check_new
        else ""
    )
    old_guard = "IF TG_OP IN ('UPDATE', 'DELETE') THEN" if check_new else "IF TRUE THEN"
    return f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.forbid_frozen_version_child_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
        AS $$
        DECLARE
            parent_state text;
        BEGIN
            {old_guard}
                SELECT state INTO parent_state
                    FROM {SCHEMA}.plan_versions
                    WHERE id = OLD.plan_version_id;
                IF parent_state IN {FROZEN_STATES} THEN
                    RAISE EXCEPTION
                        'plan_version % is % and its % rows are immutable; clone into a new draft'
                        , OLD.plan_version_id
                        , parent_state
                        , TG_TABLE_NAME
                        USING ERRCODE = 'restrict_violation';
                END IF;
            END IF;{new_check}
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """


def _insert_trigger(table: str) -> str:
    """A second trigger for INSERT beside the existing UPDATE-or-DELETE one, rather than dropping
    and recreating that one: `CREATE TRIGGER` takes a lock that does not wait on readers, while
    `DROP TRIGGER` takes ACCESS EXCLUSIVE and would queue behind every open read of the table."""
    return f"""
        CREATE TRIGGER trg_{table}_frozen_no_insert
            BEFORE INSERT ON {SCHEMA}.{table}
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.forbid_frozen_version_child_mutation()
        """


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.forbid_published_version_update()
            RETURNS trigger
            LANGUAGE plpgsql
        AS $$
        DECLARE
            probe {SCHEMA}.plan_versions;
        BEGIN
            IF OLD.state = 'grandfathered' THEN
                RAISE EXCEPTION
                    'plan_version % is grandfathered and immutable'
                    , OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.state = 'published' THEN
                -- The one carve-out: withdraw. Only `state` may differ, and only to
                -- 'grandfathered'; the probe is NEW with OLD's state put back, so any other
                -- changed column makes it differ from OLD.
                probe := NEW;
                probe.state := OLD.state;
                IF NEW.state = 'grandfathered' AND probe IS NOT DISTINCT FROM OLD THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION
                    'plan_version % is published and immutable; clone it into a new draft'
                    , OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            -- Only a published version can be withdrawn. A draft flipped straight to
            -- grandfathered would be frozen and undeletable without ever passing publish
            -- validation.
            IF NEW.state = 'grandfathered' THEN
                RAISE EXCEPTION
                    'plan_version % is %; only a published version can become grandfathered'
                    , OLD.id
                    , OLD.state
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )

    # The child rows of a frozen version were protected against UPDATE and DELETE only, judged
    # by OLD's parent: a new price could still be INSERTed under a published version, or a
    # draft's price moved onto one, changing every pinned subscription's sheet. Judge the NEW
    # parent on INSERT and UPDATE as well.
    op.execute(_child_function(check_new=True))
    for table in CHILD_TABLES:
        op.execute(_insert_trigger(table))

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint("organization_id", "key", name="uq_idempotency_keys_org_key"),
        schema=SCHEMA,
    )
    op.execute(f"ALTER TABLE {SCHEMA}.idempotency_keys ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.idempotency_keys FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {SCHEMA}.idempotency_keys
            FOR ALL TO {APP_ROLE}
            USING      (organization_id = {SCHEMA}.current_org())
            WITH CHECK (organization_id = {SCHEMA}.current_org())
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.idempotency_keys")
    op.drop_table("idempotency_keys", schema=SCHEMA)

    # The child guard back to e3f8a91c7b2d / 8fef75a4a797: UPDATE or DELETE, judged by OLD.
    for table in CHILD_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_frozen_no_insert ON {SCHEMA}.{table}")
    op.execute(_child_function(check_new=False))

    # Back to 549e7957161c's body exactly. A version withdrawn while this revision was live
    # stays `grandfathered` — data is not rewritten on the way down — and is simply no longer
    # protected by the row trigger, which is the state the older revision always had.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.forbid_published_version_update()
            RETURNS trigger
            LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.state = 'published' THEN
                RAISE EXCEPTION
                    'plan_version % is published and immutable; clone it into a new draft'
                    , OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
