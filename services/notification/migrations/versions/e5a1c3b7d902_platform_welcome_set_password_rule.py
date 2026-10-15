"""the platform tenant's welcome email for a first password

A brand-new sign-up used to receive the generic reset email ("Someone - most likely you - asked
for a link..."), which reads like a security notice they never triggered and does not say which
organization was created. auth-service now publishes `core.user.password_setup_requested` for a
person with no password and no sign-in yet, carrying the organization's name; this adds the rule
and template that turn it into a welcome. The reset email and its rule are unchanged.

Rendering is strict, so `{{full_name}}`, `{{organization_name}}`, `{{email}}` and `{{reset_url}}`
must all be sent by the producer or the message fails loudly instead of mailing a broken link.

Revision ID: e5a1c3b7d902
Revises: d3b8f2a6c915
Create Date: 2026-10-15 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a1c3b7d902"  # pragma: allowlist secret
down_revision: str | None = "d3b8f2a6c915"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORM_ORG = "00000000-0000-0000-0000-000000000000"
CODE = "platform-welcome-set-password"

SUBJECT = "Welcome to OpsGrids: set your password"
BODY = """Hello {{full_name}},

Welcome to OpsGrids. Your organization, {{organization_name}}, has been created and you are its owner.

Choose a password for {{email}} to finish setting up (the link works once, for a limited time):

{{reset_url}}

If you did not sign up for OpsGrids, ignore this email: nothing happens until the link is used.

- OpsGrids
"""


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO notification.templates
                (id, organization_id, code, channel, subject, body, description,
                 created_at, updated_at)
            VALUES
                (gen_random_uuid(), CAST(:org AS uuid), :code, 'email', :subject, :body,
                 'Platform: the welcome / first-password email for a new sign-up', now(), now())
            ON CONFLICT (organization_id, code, channel) DO NOTHING
            """
        ).bindparams(org=PLATFORM_ORG, code=CODE, subject=SUBJECT, body=BODY)
    )
    op.execute(
        sa.text(
            """
            INSERT INTO notification.rules
                (id, organization_id, code, event_type, template_code, channel, category,
                 recipient_path, active, created_at, updated_at)
            VALUES
                (gen_random_uuid(), CAST(:org AS uuid), :code,
                 'core.user.password_setup_requested', :code, 'email', 'transactional',
                 'email', true, now(), now())
            ON CONFLICT (organization_id, code) DO NOTHING
            """
        ).bindparams(org=PLATFORM_ORG, code=CODE)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM notification.rules WHERE organization_id = CAST(:org AS uuid) AND code = :code"
        ).bindparams(org=PLATFORM_ORG, code=CODE)
    )
    op.execute(
        sa.text(
            "DELETE FROM notification.templates WHERE organization_id = CAST(:org AS uuid) AND code = :code"
        ).bindparams(org=PLATFORM_ORG, code=CODE)
    )
