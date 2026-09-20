"""the platform tenant's password-reset email

auth-service publishes `core.user.password_reset_requested` with `organization_id` set to the
nil UUID: a credential belongs to a person, not to any one of their organisations. Rules and
templates are tenant rows, so that event has always folded under a tenant that owned nothing
and no reset email has ever been rendered. Sign-up (ADR-0013 §3) sets the owner's first
password through exactly this path, so the platform tenant now owns one rule and one template.

The nil UUID is the platform tenant. Nothing else writes rows under it, and the app role reads
them under the same row-level policy as any customer's — bound to the nil id for the one
message that carries it.

The template uses `{{reset_url}}`, which auth-service builds from `APP_BASE_URL`; `{{email}}` is
the address itself. Rendering is strict, so a producer that stops sending either field fails
loudly here rather than mailing a customer a broken link.

Revision ID: b8c1d2e3f4a5
Revises: a4b7c9e1f206
Create Date: 2026-09-20 15:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c1d2e3f4a5"  # pragma: allowlist secret
down_revision: str | None = "a4b7c9e1f206"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORM_ORG = "00000000-0000-0000-0000-000000000000"
CODE = "platform-password-reset"

SUBJECT = "Set your OpsGrids password"
BODY = """Hello,

Someone — most likely you — asked for a link to set the password for {{email}} on OpsGrids.

Set it here (the link works once, for a limited time):

{{reset_url}}

If you did not ask for this, ignore this email: nothing changes until the link is used.

— OpsGrids
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
                 'Platform: the set-password / reset-password email', now(), now())
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
                 'core.user.password_reset_requested', :code, 'email', 'transactional',
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
