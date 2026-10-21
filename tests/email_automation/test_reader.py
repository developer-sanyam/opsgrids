"""Tests for MIME parsing and reader models."""

from tools.email_automation.reader import (
    decode_mime_header,
    parse_email_address,
    parse_mime_message,
)


def test_decode_mime_header() -> None:
    # Encoded subject: =?UTF-8?B?VGVzdCBzdWJqZWN0?= -> Test subject
    encoded = "=?UTF-8?B?VGVzdCBzdWJqZWN0?="
    assert decode_mime_header(encoded) == "Test subject"
    assert decode_mime_header("Simple plain text") == "Simple plain text"


def test_parse_email_address() -> None:
    name, addr = parse_email_address("Support Team <support@opsgrids.com>")
    assert name == "Support Team"
    assert addr == "support@opsgrids.com"

    name2, addr2 = parse_email_address("plain@address.com")
    assert name2 == ""
    assert addr2 == "plain@address.com"


def test_parse_mime_message_multipart() -> None:
    raw_email = (
        b"From: John Doe <john@client.com>\r\n"
        b"To: Support <support@opsgrids.com>\r\n"
        b"Subject: Need help with invoice #999\r\n"
        b"Date: Mon, 20 Oct 2026 12:00:00 +0000\r\n"
        b"Message-ID: <msg-999@client.com>\r\n"
        b"In-Reply-To: <orig-111@opsgrids.com>\r\n"
        b"References: <orig-111@opsgrids.com>\r\n"
        b'Content-Type: multipart/alternative; boundary="BOUNDARY"\r\n'
        b"\r\n"
        b"--BOUNDARY\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Please check invoice #999 attached.\r\n"
        b"\r\n"
        b"--BOUNDARY\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<p>Please check <b>invoice #999</b> attached.</p>\r\n"
        b"\r\n"
        b"--BOUNDARY--\r\n"
    )

    msg_data = parse_mime_message(raw_email, uid="101")

    assert msg_data.uid == "101"
    assert msg_data.message_id == "msg-999@client.com"
    assert msg_data.from_name == "John Doe"
    assert msg_data.from_email == "john@client.com"
    assert msg_data.to_emails == ["support@opsgrids.com"]
    assert msg_data.subject == "Need help with invoice #999"
    assert "Please check invoice #999 attached." in msg_data.body_text
    assert "<b>invoice #999</b>" in msg_data.body_html
    assert msg_data.in_reply_to == "orig-111@opsgrids.com"
    assert msg_data.references == ["orig-111@opsgrids.com"]
    assert msg_data.is_auto_submitted is False
    assert msg_data.is_bounce is False


def test_parse_mime_message_auto_submitted() -> None:
    raw_email = (
        b"From: Bot <noreply@bot.com>\r\n"
        b"To: Support <support@opsgrids.com>\r\n"
        b"Subject: Auto: Away from office\r\n"
        b"Auto-Submitted: auto-replied\r\n"
        b"Precedence: bulk\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"I am out of the office until Friday.\r\n"
    )
    msg_data = parse_mime_message(raw_email, uid="102")
    assert msg_data.is_auto_submitted is True
