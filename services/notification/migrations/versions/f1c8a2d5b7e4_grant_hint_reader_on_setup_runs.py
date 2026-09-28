"""Directory reader: narrow SELECT on w1_setup_runs' hint columns

`e2b9f47a63d1`'s own docstring says the notification directory reader's grant is table-level on
`provider_accounts` alone, so `meta_user_id` needed no further `GRANT` -- true for that column,
but it left `w1_setup_runs.meta_user_id_hint` unreachable from the directory session. That did not
matter until now: `apply_meta_data_deletion`'s hint-clearing ran on the tenant-scoped `session`,
reached only once an account had already matched on the directory session. It missed the case
where an earlier deauthorize callback nulls `provider_accounts.meta_user_id` first -- the very
column `accounts_for_meta_user_id` matches on -- so a later data-deletion callback for the same
Facebook user id finds no account at all, and the hint on an orphaned `w1_setup_runs` row is never
cleared. `w1_selectors.setup_run_organizations_for_meta_user_id_hint` is the fix: it has to run on
the same directory session, independent of any account match, which is what this grant is for.

Two columns only (`organization_id`, `meta_user_id_hint`), not the whole table -- `a4b7c9e1f206`'s
own comment on the `provider_accounts` grant is explicit that this role gets exactly what it needs
"and nothing else", and every other column here (`status`, `steps`, `code_hash`,
`registration_pin_sealed`, ...) has no business being cross-tenant readable. `meta_user_id_hint`
itself is not a secret -- it is the same kind of Facebook-scoped identifier this role can already
read off `provider_accounts.meta_user_id`, and Meta's own callback names it as plaintext on every
delivery.

Grant only; no table or column is added, dropped, or altered.

Revision ID: f1c8a2d5b7e4
Revises: e2b9f47a63d1
Create Date: 2026-09-28 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f1c8a2d5b7e4"  # pragma: allowlist secret
down_revision: str | None = "e2b9f47a63d1"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "notification"
DIRECTORY_ROLE = "opsgrids_notification_directory"


def upgrade() -> None:
    # Guarded the same way `a4b7c9e1f206` guards its own grant: the role is created by
    # `tools/dev-bootstrap/init-db.sql`, which a bare database may not have run.
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{DIRECTORY_ROLE}') THEN
            GRANT SELECT (organization_id, meta_user_id_hint)
              ON {SCHEMA}.w1_setup_runs TO {DIRECTORY_ROLE};
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{DIRECTORY_ROLE}') THEN
            REVOKE SELECT (organization_id, meta_user_id_hint)
              ON {SCHEMA}.w1_setup_runs FROM {DIRECTORY_ROLE};
          END IF;
        END
        $$;
        """
    )
