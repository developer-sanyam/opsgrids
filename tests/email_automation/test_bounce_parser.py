"""Tests for bounce detection and RFC 3464 DSN parsing."""

import email
import email.message

from tools.email_automation.bounce_parser import (
    is_bounce_sender,
    is_bounce_subject,
    parse_bounce_report,
)


def test_is_bounce_sender() -> None:
    assert is_bounce_sender("MAILER-DAEMON@mail.example.com")
    assert is_bounce_sender("Mail Delivery Subsystem <mailer-daemon@googlemail.com>")
    assert is_bounce_sender("postmaster@company.com")
    assert not is_bounce_sender("customer@client.com")


def test_is_bounce_subject() -> None:
    assert is_bounce_subject("Delivery Status Notification (Failure)")
    assert is_bounce_subject("Undelivered Mail Returned to Sender")
    assert is_bounce_subject("Mail delivery failed: returning message to sender")
    assert not is_bounce_subject("Invoice inquiry for order #1234")


def test_parse_rfc3464_dsn_multipart() -> None:
    raw_dsn = (
        b"From: MAILER-DAEMON@example.com\r\n"
        b"To: sender@opsgrids.com\r\n"
        b"Subject: Delivery Status Notification (Failure)\r\n"
        b'Content-Type: multipart/report; report-type=delivery-status; boundary="BOUNDARY"\r\n'
        b"\r\n"
        b"--BOUNDARY\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"This is an automated delivery failure notice.\r\n"
        b"\r\n"
        b"--BOUNDARY\r\n"
        b"Content-Type: message/delivery-status\r\n"
        b"\r\n"
        b"Reporting-MTA: dns; mail.example.com\r\n"
        b"Action: failed\r\n"
        b"Final-Recipient: rfc822; wronguser@nowhere.com\r\n"
        b"Status: 5.1.1\r\n"
        b"Diagnostic-Code: smtp; 550 5.1.1 User unknown in virtual mailbox table\r\n"
        b"\r\n"
        b"--BOUNDARY--\r\n"
    )
    msg = email.message_from_bytes(raw_dsn)
    report = parse_bounce_report(
        msg,
        subject="Delivery Status Notification (Failure)",
        from_addr="MAILER-DAEMON@example.com",
    )

    assert report.is_bounce is True
    assert report.failed_recipient == "wronguser@nowhere.com"
    assert report.status_code == "5.1.1"
    assert report.is_hard_bounce is True
    assert "User unknown" in report.diagnostic_code


def test_parse_plain_text_heuristic_bounce() -> None:
    raw_bounce = (
        b"From: postmaster@mailserver.net\r\n"
        b"To: sender@opsgrids.com\r\n"
        b"Subject: Undeliverable: Welcome to OpsGrids\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Delivery failed to deliver to absent_person@nowhere.com\r\n"
        b"Remote server replied: 550 Recipient address rejected: mailbox unavailable\r\n"
    )
    msg = email.message_from_bytes(raw_bounce)
    report = parse_bounce_report(
        msg,
        subject="Undeliverable: Welcome to OpsGrids",
        from_addr="postmaster@mailserver.net",
    )

    assert report.is_bounce is True
    assert report.failed_recipient == "absent_person@nowhere.com"
    assert report.is_hard_bounce is True


def test_non_bounce_message() -> None:
    raw_msg = (
        b"From: alice@client.com\r\n"
        b"To: support@opsgrids.com\r\n"
        b"Subject: Question about pricing\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Hello, can you explain the pricing tiers?\r\n"
    )
    msg = email.message_from_bytes(raw_msg)
    report = parse_bounce_report(
        msg,
        subject="Question about pricing",
        from_addr="alice@client.com",
    )
    assert report.is_bounce is False


def test_parse_gmail_delivery_subsystem_timeout_bounce() -> None:
    raw_gmail_bounce = (
        b"From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\r\n"
        b"To: zionna@opsgrids.com\r\n"
        b"Subject: Delivery incomplete: One unified operating system for Abhay Pharma\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Message not delivered\r\n"
        b"There was a problem delivering your message to info@abhaypharma.com. "
        b"See the technical details below.\r\n"
        b"The response was:\r\n"
        b"The recipient server did not accept our requests to connect. "
        b"For more information, go to https://support.google.com/mail/answer/7720. "
        b"[abhaypharma.com 216.198.79.1: timed out]\r\n"
    )
    msg = email.message_from_bytes(raw_gmail_bounce)
    report = parse_bounce_report(
        msg,
        subject="Delivery incomplete: One unified operating system for Abhay Pharma",
        from_addr="Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
    )

    assert report.is_bounce is True
    assert report.failed_recipient == "info@abhaypharma.com"
    assert report.is_hard_bounce is True
    assert "timed out" in report.diagnostic_code
