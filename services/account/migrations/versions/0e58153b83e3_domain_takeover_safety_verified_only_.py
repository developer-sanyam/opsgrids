"""domain takeover safety: verified-only exclusivity

Replaces the single global `UNIQUE (domain)` with two narrower constraints (models.py's own
docstring on `OrgDomain` explains why the original one is wrong): `uq_org_domains_org_domain`
lets more than one organization hold the same domain **pending**, because an unproven claim is
not a fact worth protecting; `uq_org_domains_domain_verified`, a partial unique index on
`domain` where `verified_at IS NOT NULL`, is the real lock — at most one organization may ever
hold a given domain **verified**, and the database enforces that on the ordinary, tenant-scoped
`UPDATE` that sets `verified_at`, with no cross-tenant read or write anywhere in the path.

**No backfill is needed to move forward.** Every row already satisfies both new constraints
merely by having satisfied the old, stricter one: a global-unique `domain` is trivially unique
per organization too, and trivially unique among the (at most one) verified rows. The upgrade
still asserts this rather than assuming it, because "trivially true given the old constraint"
is an argument about the code, not a fact about the data in front of it.

The downgrade is the direction that can actually fail: re-imposing a single global unique
column is only well-defined if nothing has used the freedom this migration grants — no domain
claimed pending by more than one organization, and (structurally impossible, but checked for
the same reason 702ada644525's own guard checks the impossible-until-it-isn't case) no domain
verified by more than one. It refuses rather than guessing which row should lose.

Revision ID: 0e58153b83e3
Revises: 49beafe168c8
Create Date: 2026-09-23 21:52:53.256039
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0e58153b83e3"  # pragma: allowlist secret
down_revision: str | None = "49beafe168c8"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "account"


def upgrade() -> None:
    bind = op.get_bind()

    # Asserted, not assumed — see the module docstring. A failure here means the running
    # database's `org_domains` was never actually under the global unique constraint this
    # migration replaces, which is a different problem this migration should not paper over.
    org_dupes = bind.execute(
        sa.text(
            f"SELECT domain FROM {SCHEMA}.org_domains "
            "GROUP BY organization_id, domain HAVING count(*) > 1 LIMIT 1"
        )
    ).first()
    if org_dupes:
        raise RuntimeError(
            f"organization {org_dupes[0]!r} already has more than one row for the same "
            "domain — investigate before this migration can add a unique constraint on it."
        )
    verified_dupes = bind.execute(
        sa.text(
            f"SELECT domain FROM {SCHEMA}.org_domains WHERE verified_at IS NOT NULL "
            "GROUP BY domain HAVING count(*) > 1 LIMIT 1"
        )
    ).first()
    if verified_dupes:
        raise RuntimeError(
            f"{verified_dupes[0]!r} is already verified by more than one organization — "
            "investigate before this migration can add a unique index on it."
        )

    op.drop_constraint(op.f("uq_org_domains_domain"), "org_domains", schema=SCHEMA, type_="unique")
    op.create_unique_constraint(
        "uq_org_domains_org_domain", "org_domains", ["organization_id", "domain"], schema=SCHEMA
    )
    op.create_index(
        "uq_org_domains_domain_verified",
        "org_domains",
        ["domain"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("verified_at IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()

    # The direction that can lose data's *meaning*, if not the rows themselves: a global
    # unique column cannot represent two organizations both pending on the same domain, and
    # there is no honest way to pick a survivor here. Refuse instead of guessing.
    dupes = bind.execute(
        sa.text(
            f"SELECT domain, count(DISTINCT organization_id) AS holders "
            f"FROM {SCHEMA}.org_domains GROUP BY domain HAVING count(DISTINCT organization_id) > 1"
        )
    ).all()
    if dupes:
        names = ", ".join(f"{row.domain!r} ({row.holders} organizations)" for row in dupes)
        raise RuntimeError(
            "cannot downgrade: more than one organization holds the same domain for "
            f"{names}. Release or reassign the extra claims by hand, then re-run the "
            "downgrade — this migration will not choose a survivor for you."
        )

    op.drop_index(
        "uq_org_domains_domain_verified",
        table_name="org_domains",
        schema=SCHEMA,
        postgresql_where=sa.text("verified_at IS NOT NULL"),
    )
    op.drop_constraint("uq_org_domains_org_domain", "org_domains", schema=SCHEMA, type_="unique")
    op.create_unique_constraint(
        op.f("uq_org_domains_domain"),
        "org_domains",
        ["domain"],
        schema=SCHEMA,
        postgresql_nulls_not_distinct=False,
    )
