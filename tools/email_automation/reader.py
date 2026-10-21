"""IMAP email client and MIME structure parser for incoming emails."""

from __future__ import annotations

import contextlib
import email
import email.header
import email.message
import email.utils
import imaplib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from tools.email_automation.bounce_parser import is_bounce_message
from tools.email_automation.config import EmailConfig


@dataclass
class EmailMessageData:
    """Structured representation of an incoming email."""

    uid: str
    message_id: str
    date: datetime
    from_name: str
    from_email: str
    to_emails: list[str] = field(default_factory=list)
    subject: str = ""
    body_text: str = ""
    body_html: str = ""
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    is_auto_submitted: bool = False
    precedence: str = ""
    is_bounce: bool = False
    raw_message: email.message.Message | None = None

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "message_id": self.message_id,
            "date": self.date.isoformat(),
            "from_name": self.from_name,
            "from_email": self.from_email,
            "to_emails": self.to_emails,
            "subject": self.subject,
            "body_snippet": self.body_text[:200] if self.body_text else "",
            "in_reply_to": self.in_reply_to,
            "is_auto_submitted": self.is_auto_submitted,
            "is_bounce": self.is_bounce,
        }


def decode_mime_header(header_value: str | None) -> str:
    """Decodes MIME encoded-words (e.g. =?UTF-8?B?...?=) into unicode text."""
    if not header_value:
        return ""
    decoded_fragments = []
    for fragment, charset in email.header.decode_header(header_value):
        if isinstance(fragment, bytes):
            enc = charset or "utf-8"
            try:
                decoded_fragments.append(fragment.decode(enc, errors="replace"))
            except LookupError:
                decoded_fragments.append(fragment.decode("utf-8", errors="replace"))
        else:
            decoded_fragments.append(str(fragment))
    return "".join(decoded_fragments).strip()


def parse_email_address(raw_header: str | None) -> tuple[str, str]:
    """Parses 'Display Name <user@domain.com>' into (name, email)."""
    if not raw_header:
        return "", ""
    decoded = decode_mime_header(raw_header)
    name, addr = email.utils.parseaddr(decoded)
    return name.strip(), addr.strip().lower()


def parse_mime_message(raw_bytes: bytes, uid: str = "") -> EmailMessageData:
    """Parses raw RFC 822 email bytes into structured EmailMessageData."""
    msg = email.message_from_bytes(raw_bytes)

    # Message-ID
    msg_id = decode_mime_header(msg.get("Message-ID", "")).strip("<> ")
    if not msg_id:
        msg_id = f"generated-{uid}-{hash(raw_bytes)}"

    # Subject & From
    subject = decode_mime_header(msg.get("Subject", ""))
    from_name, from_email = parse_email_address(msg.get("From", ""))

    # Recipients
    to_emails = []
    raw_to = msg.get("To", "")
    if raw_to:
        for part in raw_to.split(","):
            _, addr = parse_email_address(part)
            if addr:
                to_emails.append(addr)

    # Date
    date_val = datetime.now(UTC)
    raw_date = msg.get("Date")
    if raw_date:
        with contextlib.suppress(Exception):
            parsed_tuple = email.utils.parsedate_to_datetime(raw_date)
            if parsed_tuple:
                date_val = parsed_tuple

    # Threading Headers
    in_reply_to = decode_mime_header(msg.get("In-Reply-To", "")).strip("<> ") or None
    raw_refs = decode_mime_header(msg.get("References", ""))
    references = [r.strip("<> ") for r in raw_refs.split() if r.strip("<> ")]

    # Auto-Submitted & Loop Headers
    auto_sub = decode_mime_header(msg.get("Auto-Submitted", "")).lower()
    is_auto = auto_sub != "" and auto_sub != "no"
    precedence = decode_mime_header(msg.get("Precedence", "")).lower()
    x_auto = decode_mime_header(msg.get("X-Autoreply", "")).lower()
    if x_auto in ("yes", "true", "1") or precedence in ("bulk", "junk", "list", "auto_reply"):
        is_auto = True

    # Body extraction
    body_text_parts = []
    body_html_parts = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type().lower()
            disposition = str(part.get("Content-Disposition", "")).lower()
            if "attachment" in disposition:
                continue

            payload = part.get_payload(decode=True)
            if not payload:
                continue

            charset = part.get_content_charset() or "utf-8"
            try:
                decoded_str = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                decoded_str = payload.decode("utf-8", errors="replace")

            if content_type == "text/plain":
                body_text_parts.append(decoded_str)
            elif content_type == "text/html":
                body_html_parts.append(decoded_str)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                decoded_str = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                decoded_str = payload.decode("utf-8", errors="replace")

            if msg.get_content_type().lower() == "text/html":
                body_html_parts.append(decoded_str)
            else:
                body_text_parts.append(decoded_str)

    body_text = "\n".join(body_text_parts).strip()
    body_html = "\n".join(body_html_parts).strip()

    # Fallback: if only HTML available, strip tags for plaintext body
    if not body_text and body_html:
        body_text = re.sub(r"<[^>]+>", " ", body_html).strip()
        body_text = " ".join(body_text.split())

    is_bounce = is_bounce_message(msg, subject=subject, from_addr=from_email)

    return EmailMessageData(
        uid=uid,
        message_id=msg_id,
        date=date_val,
        from_name=from_name,
        from_email=from_email,
        to_emails=to_emails,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        in_reply_to=in_reply_to,
        references=references,
        is_auto_submitted=is_auto,
        precedence=precedence,
        is_bounce=is_bounce,
        raw_message=msg,
    )


class EmailReader:
    """Connects to IMAP mailbox and fetches incoming messages."""

    def __init__(self, config: EmailConfig | None = None) -> None:
        self.config = config or EmailConfig.from_env()
        self._client: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None

    def connect(self) -> None:
        """Opens IMAP connection and authenticates."""
        if not self.config.username or not self.config.password:
            raise ValueError("EMAIL_USERNAME and EMAIL_PASSWORD must be configured for IMAP.")

        if self.config.imap_use_ssl:
            self._client = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
        else:
            self._client = imaplib.IMAP4(self.config.imap_host, self.config.imap_port)

        self._client.login(self.config.username, self.config.password)

    def fetch_unseen_emails(
        self, folder: str = "INBOX", limit: int = 50, mark_as_read: bool = False
    ) -> list[EmailMessageData]:
        """Fetches unread (UNSEEN) emails from the given folder."""
        if self._client is None:
            self.connect()
        assert self._client is not None

        self._client.select(folder)
        status, data = self._client.search(None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return []

        message_uids = data[0].split()
        if limit:
            message_uids = message_uids[-limit:]  # take the most recent ones

        messages: list[EmailMessageData] = []
        for uid_bytes in message_uids:
            uid_str = uid_bytes.decode("ascii", errors="replace")
            status, fetch_data = self._client.fetch(uid_bytes, "(RFC822)")
            if status != "OK" or not fetch_data:
                continue

            for part in fetch_data:
                if isinstance(part, tuple) and len(part) >= 2:
                    raw_bytes = part[1]
                    msg_obj = parse_mime_message(raw_bytes, uid=uid_str)
                    messages.append(msg_obj)

            if not mark_as_read:
                # Remove Seen flag if we do not wish to mark as read immediately
                self._client.store(uid_bytes, "-FLAGS", "\\Seen")

        return messages

    def mark_as_read(self, uid: str, folder: str = "INBOX") -> None:
        """Marks a specific email UID as read/seen."""
        if self._client is None:
            self.connect()
        assert self._client is not None
        self._client.select(folder)
        self._client.store(uid.encode("ascii"), "+FLAGS", "\\Seen")

    def close(self) -> None:
        """Logs out and closes the IMAP session."""
        if self._client is not None:
            try:
                with contextlib.suppress(Exception):
                    self._client.close()
                with contextlib.suppress(Exception):
                    self._client.logout()
            finally:
                self._client = None
