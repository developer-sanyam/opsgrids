"""plan version immutability trigger

A published plan version is immutable (ADR-0000 §9, ADR-0004 §2): a subscription pins one
version, and re-pricing anybody must not re-price everybody or make a past invoice
impossible to reconstruct. Editing a plan clones its active version into a new draft; the
published row is never touched.

The service layer refuses a mutation first, with a message a person can act on. This trigger
is the net under that: a `BEFORE UPDATE` that raises whenever the row being updated was
already `published`, so no code path — a stray script, a future endpoint, a psql session —
can quietly edit a frozen price. `DELETE` is left to the foreign-key/privilege layer; the one
thing that must never happen is a *silent change* to a version somebody is pinned to.

Revision ID: 549e7957161c
Revises: 2d459b6021d5
Create Date: 2026-08-24 18:27:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '549e7957161c'  # pragma: allowlist secret
down_revision: str | None = '2d459b6021d5'  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catalog"


def upgrade() -> None:
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
    op.execute(
        f"""
        CREATE TRIGGER trg_plan_versions_immutable
            BEFORE UPDATE ON {SCHEMA}.plan_versions
            FOR EACH ROW
            EXECUTE FUNCTION {SCHEMA}.forbid_published_version_update()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_plan_versions_immutable ON {SCHEMA}.plan_versions")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.forbid_published_version_update()")
