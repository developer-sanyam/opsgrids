"""End-to-end tests for the EmailOrchestrationEngine."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tools.email_automation import db as db_module
from tools.email_automation.config import EmailConfig
from tools.email_automation.models import Base
from tools.email_automation.orchestrator import EmailOrchestrationEngine
from tools.email_automation.reader import parse_mime_message
from tools.email_automation.suppression_service import block_email, is_email_blocked


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    test_db_file = tmp_path / "test_email.db"
    db_url = f"sqlite:///{test_db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sm = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, autoflush=False)

    monkeypatch.setattr(db_module, "_engine", engine)
    monkeypatch.setattr(db_module, "_sessionmaker", sm)
    yield
    engine.dispose()


def test_orchestrator_handles_bounce_and_blocks_failed_recipient() -> None:
    config = EmailConfig(
        organization_id="org-test-orch",
        dry_run=True,
    )
    engine = EmailOrchestrationEngine(config)

    # Inbound bounce message
    raw_bounce = (
        b"From: MAILER-DAEMON@mailserver.net\r\n"
        b"To: our-sender@opsgrids.com\r\n"
        b"Subject: Delivery Status Notification (Failure)\r\n"
        b'Content-Type: multipart/report; report-type=delivery-status; boundary="DSN_BOUND"\r\n'
        b"\r\n"
        b"--DSN_BOUND\r\n"
        b"Content-Type: text/plain\r\n\r\nFailure notice.\r\n"
        b"--DSN_BOUND\r\n"
        b"Content-Type: message/delivery-status\r\n\r\n"
        b"Final-Recipient: rfc822; bad_contact@external.org\r\n"
        b"Status: 5.1.1\r\n"
        b"Diagnostic-Code: smtp; 550 5.1.1 User unknown\r\n"
        b"--DSN_BOUND--\r\n"
    )
    bounce_msg = parse_mime_message(raw_bounce, uid="b1")

    # Run orchestrator
    result = engine.process_incoming_emails(
        messages=[bounce_msg],
        validate_domains=False,
    )

    assert result.emails_processed == 1
    assert len(result.bounces_detected) == 1
    assert result.bounces_detected[0]["failed_recipient"] == "bad_contact@external.org"
    assert len(result.replies_sent) == 0

    # Verify recipient is permanently blocked in database
    from tools.email_automation.db import get_session

    with get_session() as s:
        blocked = is_email_blocked(s, config.organization_id, "bad_contact@external.org")
        assert blocked is True


def test_orchestrator_blocks_wrong_email_syntax() -> None:
    config = EmailConfig(
        organization_id="org-test-orch",
        dry_run=True,
    )
    engine = EmailOrchestrationEngine(config)

    raw_msg = (
        b"From: illegal..syntax@@wrong.com\r\n"
        b"To: support@opsgrids.com\r\n"
        b"Subject: Test\r\n"
        b"\r\n"
        b"Hello\r\n"
    )
    msg = parse_mime_message(raw_msg, uid="m1")

    result = engine.process_incoming_emails(messages=[msg], validate_domains=False)

    assert len(result.wrong_emails_blocked) == 1
    assert len(result.replies_sent) == 0


def test_orchestrator_refuses_to_send_to_suppressed_user() -> None:
    config = EmailConfig(
        organization_id="org-test-orch",
        dry_run=True,
    )
    engine = EmailOrchestrationEngine(config)

    # Pre-block an email
    blocked_address = "previously.bounced@client.com"
    from tools.email_automation.db import get_session

    with get_session() as s:
        block_email(s, config.organization_id, blocked_address, reason="bounced")

    # Email arrives from this blocked address
    raw_msg = (
        f"From: {blocked_address}\r\n"
        f"To: support@opsgrids.com\r\n"
        f"Subject: Please reply to me\r\n"
        f"\r\n"
        f"Can you email me back?\r\n"
    ).encode()
    msg = parse_mime_message(raw_msg, uid="m2")

    result = engine.process_incoming_emails(messages=[msg], validate_domains=False)

    # Gated: should NOT send reply
    assert len(result.replies_sent) == 0
    assert any("blocked_in_database" in skip["reason"] for skip in result.replies_skipped)


def test_orchestrator_sends_automated_reply_and_rate_limits() -> None:
    config = EmailConfig(
        organization_id="org-test-orch",
        from_email="support@opsgrids.com",
        from_name="OpsGrids Support",
        dry_run=True,
        rate_limit_hours=24,
    )
    engine = EmailOrchestrationEngine(config)

    raw_msg = (
        b"From: Alex <alex.customer@valid-company.com>\r\n"
        b"To: support@opsgrids.com\r\n"
        b"Subject: Inquiring about enterprise tier\r\n"
        b"Message-ID: <inq-12345@valid-company.com>\r\n"
        b"\r\n"
        b"Hi, I would like to know more about the enterprise plan.\r\n"
    )
    msg1 = parse_mime_message(raw_msg, uid="m3")

    # First incoming message -> Should receive automated reply
    result1 = engine.process_incoming_emails(messages=[msg1], validate_domains=False)

    assert result1.emails_processed == 1
    assert len(result1.replies_sent) == 1
    assert result1.replies_sent[0]["recipient"] == "alex.customer@valid-company.com"

    # Second incoming message immediately after -> Should be skipped due to duplicate or
    # rate limiting
    msg2 = parse_mime_message(raw_msg, uid="m4")
    result2 = engine.process_incoming_emails(messages=[msg2], validate_domains=False)

    assert len(result2.replies_sent) == 0
    assert any(
        "duplicate_email" in skip["reason"] or "rate_limited" in skip["reason"]
        for skip in result2.replies_skipped
    )
