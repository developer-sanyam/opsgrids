# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""`f4a8c2d6e913` (campaign `expanded_at`), run for real against every shape of pre-fix data.

Drives alembic down to the revision right before this one, seeds campaigns the way the pre-fix
schema held them — one already expanded (holding `campaign_recipients` rows), one genuinely sent
with recipients, one caught mid-send with nothing expanded yet, and one that is the bug itself:
`sent`, `recipient_count = 0`, no recipient rows at all — then upgrades to head and checks each
one landed where the migration's own docstring says it should. Also covers the empty-table case,
and an upgrade/downgrade/upgrade round trip.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.models import SCHEMA

pytestmark = pytest.mark.integration

SERVICE_DIR = Path(__file__).resolve().parent.parent
BEFORE = "d8f3a52c9e14"  # revision right before f4a8c2d6e913  # pragma: allowlist secret
TABLES = ("campaign_recipients", "campaigns", "audit_logs", "outbox")


def alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  (fixed argv, sys.executable, no shell)
        [sys.executable, "-m", "alembic", *args],
        cwd=SERVICE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
async def admin():  # type: ignore[no-untyped-def]
    engine = create_async_engine(
        settings().admin_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    async with engine.begin() as conn:
        existing = [
            t
            for t in TABLES
            if (await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{SCHEMA}.{t}"})).scalar()
        ]
        if existing:
            await conn.execute(
                text(f"TRUNCATE {', '.join(f'{SCHEMA}.{t}' for t in existing)} CASCADE")
            )
    try:
        down = alembic("downgrade", BEFORE)
        assert down.returncode == 0, down.stderr[-2000:]
        yield engine
    finally:
        up = alembic("upgrade", "head")
        await engine.dispose()
        assert up.returncode == 0, up.stderr[-2000:]


async def _insert_campaign(
    conn,
    *,
    campaign_id: uuid.UUID,
    org: uuid.UUID,
    status: str,
    started_at: datetime | None,
    recipient_count: int,
) -> None:
    """A campaign row exactly as the pre-fix schema held it — no `expanded_at` column exists yet
    at `BEFORE`, so this never mentions it."""
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.campaigns
                (id, organization_id, name, sender_id, subject, html_body, text_body, audience,
                 list_id, status, scheduled_at, started_at, completed_at, cancelled_at,
                 pause_reason, paused_at, recipient_count, sent_count, failed_count,
                 suppressed_count, created_at, updated_at, created_by, updated_by, deleted_at)
            VALUES
                (:id, :org, 'Autumn', :sender_id, 'News', '<p>Hello</p>', 'Hello', 'all_sendable',
                 NULL, :status, NULL, :started_at, :completed_at, NULL, '', NULL,
                 :recipient_count, 0, 0, 0, now(), now(), NULL, NULL, NULL)
            """
        ),
        {
            "id": campaign_id,
            "org": org,
            "sender_id": uuid.uuid4(),
            "status": status,
            "started_at": started_at,
            "completed_at": started_at,
            "recipient_count": recipient_count,
        },
    )


async def _insert_recipient(conn, *, campaign_id: uuid.UUID, org: uuid.UUID, status: str) -> None:
    await conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.campaign_recipients
                (id, organization_id, campaign_id, contact_id, email_enc, email_masked,
                 email_index, status, claimed_at, provider_message_id, error, sent_at,
                 created_at, updated_at, created_by, updated_by, deleted_at, erased_at)
            VALUES
                (:id, :org, :campaign_id, :contact_id, :email_enc, 'r•••@example.com', :idx,
                 :status, NULL, NULL, '', NULL, now(), now(), 'system:engage-campaign', NULL,
                 NULL, NULL)
            """
        ),
        {
            "id": uuid.uuid4(),
            "org": org,
            "campaign_id": campaign_id,
            "contact_id": uuid.uuid4(),
            "email_enc": b"not-real-ciphertext",
            "idx": uuid.uuid4().hex,
            "status": status,
        },
    )


async def test_backfill_covers_every_shape_of_pre_fix_campaign(admin) -> None:
    org = uuid.uuid4()
    started = datetime(2026, 9, 20, 6, 0, 0, tzinfo=UTC)

    already_expanded = uuid.uuid4()  # holds recipient rows -- expanded_at backfills from started_at
    sent_with_recipients = uuid.uuid4()  # a genuinely finished send -- same backfill, still `sent`
    mid_send_unexpanded = uuid.uuid4()  # caught between `request_send` and expansion -- left alone
    the_bug_itself = uuid.uuid4()  # `sent`, recipient_count 0, no rows at all -- reset to draft

    async with admin.begin() as conn:
        await _insert_campaign(
            conn,
            campaign_id=already_expanded,
            org=org,
            status="sending",
            started_at=started,
            recipient_count=1,
        )
        await _insert_recipient(conn, campaign_id=already_expanded, org=org, status="queued")

        await _insert_campaign(
            conn,
            campaign_id=sent_with_recipients,
            org=org,
            status="sent",
            started_at=started,
            recipient_count=1,
        )
        await _insert_recipient(conn, campaign_id=sent_with_recipients, org=org, status="sent")

        await _insert_campaign(
            conn,
            campaign_id=mid_send_unexpanded,
            org=org,
            status="sending",
            started_at=started,
            recipient_count=0,
        )

        await _insert_campaign(
            conn,
            campaign_id=the_bug_itself,
            org=org,
            status="sent",
            started_at=started,
            recipient_count=0,
        )

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    async with admin.begin() as conn:
        rows = {
            r.id: r
            for r in (
                await conn.execute(
                    text(
                        f"""
                        SELECT id, status, started_at, completed_at, expanded_at, recipient_count,
                               sent_count, failed_count, suppressed_count
                          FROM {SCHEMA}.campaigns WHERE organization_id = :org
                        """
                    ),
                    {"org": org},
                )
            ).all()
        }

    expanded = rows[already_expanded]
    assert expanded.status == "sending"  # untouched -- only draft-with-recipients is never true
    assert expanded.expanded_at == started

    finished = rows[sent_with_recipients]
    assert finished.status == "sent"
    assert finished.expanded_at == started

    unexpanded = rows[mid_send_unexpanded]
    assert unexpanded.status == "sending"  # not reset: only a `sent` campaign qualifies
    assert unexpanded.expanded_at is None  # no recipient rows to backfill from either

    reset = rows[the_bug_itself]
    assert reset.status == "draft"
    assert reset.started_at is None
    assert reset.completed_at is None
    counts = (reset.recipient_count, reset.sent_count, reset.failed_count, reset.suppressed_count)
    assert counts == (0, 0, 0, 0)
    assert reset.expanded_at is None


async def test_upgrade_on_an_empty_campaigns_table_is_a_no_op(admin) -> None:
    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    async with admin.begin() as conn:
        count = (await conn.execute(text(f"SELECT count(*) FROM {SCHEMA}.campaigns"))).scalar_one()
    assert count == 0


async def test_downgrade_drops_only_the_column_and_upgrade_restores_it(admin) -> None:
    org = uuid.uuid4()
    campaign_id = uuid.uuid4()
    async with admin.begin() as conn:
        await _insert_campaign(
            conn,
            campaign_id=campaign_id,
            org=org,
            status="draft",
            started_at=None,
            recipient_count=0,
        )

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]

    down = alembic("downgrade", BEFORE)
    assert down.returncode == 0, down.stderr[-2000:]

    async with admin.begin() as conn:
        column = (
            await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'campaigns' "
                    "AND column_name = 'expanded_at'"
                ),
                {"schema": SCHEMA},
            )
        ).scalar_one_or_none()
        assert column is None
        # The row itself survived the round trip untouched.
        name = (
            await conn.execute(
                text(f"SELECT name FROM {SCHEMA}.campaigns WHERE id = :id"), {"id": campaign_id}
            )
        ).scalar_one()
        assert name == "Autumn"

    up_again = alembic("upgrade", "head")
    assert up_again.returncode == 0, up_again.stderr[-2000:]
