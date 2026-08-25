"""enable row level security on org_idp_configs

The one auth table ADR-0002 §6 directs to policy — "Enable RLS on org_idp_configs;
explicitly exempt the rest and say so here" — and, until this migration, the one part of
that instruction nobody had done. `org_idp_configs` is per-organization and holds the most
sensitive tenant-owned rows in this schema: encrypted IdP client secrets, SAML
certificates, and the `enforce_sso` switch that turns password login off for a customer.
No route touches it yet (SSO is modelled, not flowed), which is exactly when the net goes
in: before the first SSO endpoint can be written against an unpolicied table and pass its
tests on a fixture that hides the gap.

Everything else in this schema is exempted **deliberately, in writing**, the way account,
product and employee record their classifications:

- `credentials`, `sessions`, `refresh_tokens`, `mfa_factors`, `mfa_recovery_codes`,
  `password_reset_tokens`, `external_identities` — user-keyed, not organization-keyed.
  Authentication runs before an organization is chosen (ADR-0000 §1); these rows belong to
  a person, carry no `organization_id`, and a tenant policy is unexpressable on them.
- `signing_keys` — platform-global by design (ADR-0000 §2). No tenant dimension exists.
- `staff_accounts` — Atmora operators, who belong to no organization.
- `processed_events` — no organization column; a policy here silently defeats consumer
  idempotency (the same exemption every other service records).
- `outbox`, `audit_logs` — these *do* carry `organization_id`, but auth stages both from
  pre-organization flows (password reset, registration) under the nil-UUID platform
  sentinel on a bare, unbound session. A forced policy's WITH CHECK would refuse exactly
  those writes. The relay reads `outbox` as BYPASSRLS `atmora_outbox` regardless, and
  `audit_logs` is already append-only by privilege (`apply_schema_grants` revokes
  UPDATE/DELETE).

Revision ID: b7c41d9e02aa
Revises: d168582195d9
Create Date: 2026-08-25 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b7c41d9e02aa"  # pragma: allowlist secret
down_revision: str | None = "d168582195d9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "auth"
APP_ROLE = "atmora_auth"

#: Tenant-scoped: the only table in this schema owned by an organization rather than by a
#: person, the platform, or a pre-organization flow.
TENANT_TABLES = ("org_idp_configs",)

#: Deliberately not policied — the reasoning is the module docstring above, one line each.
UNPOLICIED = (
    "credentials",
    "sessions",
    "refresh_tokens",
    "mfa_factors",
    "mfa_recovery_codes",
    "password_reset_tokens",
    "external_identities",
    "signing_keys",
    "staff_accounts",
    "processed_events",
    "outbox",
    "audit_logs",
)


def _enable(table: str) -> None:
    """Enable, and FORCE — same rationale as the account/product/employee migrations."""
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    # `nullif(..., '')`: once a transaction that set the GUC locally has ended, the next
    # transaction on the same session reads it back as '' rather than NULL, and ''::uuid
    # raises. With nullif the predicate is simply false — fail-quiet at the database, with
    # the application asserting loudly instead (ADR-0002 §4).
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.current_org() RETURNS uuid
            LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT nullif(current_setting('app.organization_id', true), '')::uuid $$
        """
    )

    for table in TENANT_TABLES:
        _enable(table)
        # WITH CHECK is not optional: USING is not applied to the new row of an INSERT,
        # so without it a tenant could write a config that flips another organization's
        # `enforce_sso` — invisible to them, catastrophic for the victim.
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {SCHEMA}.{table}
                FOR ALL TO {APP_ROLE}
                USING      (organization_id = {SCHEMA}.current_org())
                WITH CHECK (organization_id = {SCHEMA}.current_org())
            """
        )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")

    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.current_org()")
