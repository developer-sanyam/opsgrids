"""Tests for database suppression and blocklist service."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tools.email_automation.models import Base
from tools.email_automation.suppression_service import (
    block_email,
    compute_email_index,
    is_email_blocked,
    list_suppressions,
    mask_email,
    unblock_email,
)


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_mask_and_index() -> None:
    email = "alex.smith@example.com"
    masked = mask_email(email)
    idx = compute_email_index(email)

    assert "@" in masked
    assert "alex.smith" not in masked  # Local part is masked
    assert isinstance(idx, str)
    assert len(idx) == 64  # SHA-256 hex string


def test_block_email_and_check(db_session: Session) -> None:
    org_id = "org-test-123"
    email = "bounced_customer@invalid-domain.com"

    # Initially not blocked
    assert not is_email_blocked(db_session, org_id, email)

    # Block email
    row = block_email(
        db_session,
        org_id,
        email,
        reason="bounced",
        diagnostic_detail="550 5.1.1 User unknown",
    )
    assert row.id is not None
    assert row.reason == "bounced"
    assert row.email == email

    # Now checked as blocked
    assert is_email_blocked(db_session, org_id, email)

    # Cross-tenant isolation check: different org is not affected
    assert not is_email_blocked(db_session, "different-org-456", email)


def test_unblock_email(db_session: Session) -> None:
    org_id = "org-test-123"
    manual_email = "manual.block@domain.com"
    bounce_email = "permanent.bounce@domain.com"

    block_email(db_session, org_id, manual_email, reason="manual")
    block_email(db_session, org_id, bounce_email, reason="bounced")

    # Manual unblock succeeds
    assert unblock_email(db_session, org_id, manual_email) is True
    assert not is_email_blocked(db_session, org_id, manual_email)

    # Bounced unblock fails without force=True
    assert unblock_email(db_session, org_id, bounce_email, force=False) is False
    assert is_email_blocked(db_session, org_id, bounce_email) is True

    # Forced unblock succeeds
    assert unblock_email(db_session, org_id, bounce_email, force=True) is True
    assert is_email_blocked(db_session, org_id, bounce_email) is False


def test_list_suppressions(db_session: Session) -> None:
    org_id = "org-list-test"
    for i in range(5):
        block_email(db_session, org_id, f"user{i}@bad.com", reason="invalid_syntax")

    items = list_suppressions(db_session, org_id, limit=10)
    assert len(items) == 5
