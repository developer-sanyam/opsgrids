"""RFC 3464 and heuristic parser for bounce notifications and delivery failures (NDR / DSN)."""

from __future__ import annotations

import email
import email.message
import re
from dataclasses import dataclass
from typing import Final

_BOUNCE_SENDER_PATTERNS: Final = [
    re.compile(r"^mailer-daemon@", re.IGNORECASE),
    re.compile(r"^postmaster@", re.IGNORECASE),
    re.compile(r"^mail-delivery-subsystem@", re.IGNORECASE),
    re.compile(r"^bounce@", re.IGNORECASE),
    re.compile(r"^daemon@", re.IGNORECASE),
    re.compile(r"noreply.*bounce", re.IGNORECASE),
]

_BOUNCE_SUBJECT_PATTERNS: Final = [
    re.compile(r"delivery status notification", re.IGNORECASE),
    re.compile(r"undelivered mail returned to sender", re.IGNORECASE),
    re.compile(r"mail delivery failed", re.IGNORECASE),
    re.compile(r"failure notice", re.IGNORECASE),
    re.compile(r"returned mail:.*failure", re.IGNORECASE),
    re.compile(r"delivery failure", re.IGNORECASE),
    re.compile(r"undeliverable:", re.IGNORECASE),
    re.compile(r"message delivery failure", re.IGNORECASE),
    re.compile(r"delivery incomplete", re.IGNORECASE),
    re.compile(r"message not delivered", re.IGNORECASE),
    re.compile(r"problem delivering your message", re.IGNORECASE),
]

# Patterns to find the failed recipient inside bounce text or DSN headers
_FINAL_RECIPIENT_REGEX: Final = re.compile(
    r"(?:Final-Recipient|Original-Recipient):\s*(?:rfc822;\s*)?([^\s;<\r\n]+@[^\s;>\r\n]+)",
    re.IGNORECASE,
)
_FAILED_ADDRESS_REGEX: Final = re.compile(
    r"(?:failed to deliver to|recipient address rejected|user unknown|mailbox unavailable|"
    r"no such user|invalid recipient|unknown user|problem delivering your message to|"
    r"delivering your message to)[^a-zA-Z0-9]*"
    r"([a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})",
    re.IGNORECASE,
)
_STATUS_CODE_REGEX: Final = re.compile(r"Status:\s*([45]\.\d+\.\d+)", re.IGNORECASE)
_DIAGNOSTIC_CODE_REGEX: Final = re.compile(
    r"Diagnostic-Code:\s*(?:smtp;\s*)?([^\r\n]+(?:\r?\n\s+[^\r\n]+)*)",
    re.IGNORECASE,
)


@dataclass
class BounceReport:
    """Structured details of an email delivery failure."""

    is_bounce: bool
    failed_recipient: str | None = None
    status_code: str | None = None
    action: str = "failed"
    diagnostic_code: str = ""
    is_hard_bounce: bool = False
    original_subject: str = ""


def is_bounce_sender(from_address: str) -> bool:
    """Checks if sender address is a known MTA daemon / system address."""
    clean = from_address.strip().lower()
    # Extract email within angle brackets if present
    match = re.search(r"<([^>]+)>", clean)
    if match:
        clean = match.group(1).lower()
    return any(pat.search(clean) for pat in _BOUNCE_SENDER_PATTERNS)


def is_bounce_subject(subject: str) -> bool:
    """Checks if subject matches standard delivery failure notices."""
    clean = subject.strip()
    return any(pat.search(clean) for pat in _BOUNCE_SUBJECT_PATTERNS)


def is_bounce_message(
    msg: email.message.Message | None, subject: str = "", from_addr: str = ""
) -> bool:
    """Detects whether an email represents an inbound bounce/NDR."""
    if is_bounce_sender(from_addr):
        return True

    if is_bounce_subject(subject):
        return True

    if msg is not None:
        content_type = msg.get_content_type().lower()
        if content_type == "multipart/report":
            report_type = msg.get_param("report-type")
            if report_type and report_type.lower() == "delivery-status":
                return True

    return False


def parse_bounce_report(
    msg: email.message.Message, subject: str = "", from_addr: str = ""
) -> BounceReport:
    """Parses an RFC 3464 DSN structure or heuristic bounce notice.

    Extracts:
    - Failed recipient address
    - DSN status code (e.g. 5.1.1, 5.0.0)
    - Diagnostic reason string
    - Whether it is a permanent hard bounce (5.x.x) or temporary soft bounce (4.x.x)
    """
    if not is_bounce_message(msg, subject=subject, from_addr=from_addr):
        return BounceReport(is_bounce=False)

    failed_recipient: str | None = None
    status_code: str | None = None
    action = "failed"
    diagnostic_code = ""
    full_body_text = ""

    # Walk message parts to inspect delivery-status subpart
    for part in msg.walk():
        c_type = part.get_content_type().lower()
        payload = part.get_payload()

        if c_type == "message/delivery-status":
            # RFC 3464 parsed message/delivery-status block
            subparts = payload if isinstance(payload, list) else [part]
            for sub in subparts:
                if hasattr(sub, "get"):
                    rcpt_hdr = sub.get("Final-Recipient") or sub.get("Original-Recipient")
                    if rcpt_hdr and not failed_recipient:
                        # e.g. "rfc822; wronguser@nowhere.com"
                        clean_hdr = str(rcpt_hdr)
                        if ";" in clean_hdr:
                            clean_hdr = clean_hdr.split(";", 1)[1]
                        failed_recipient = clean_hdr.strip("<> \r\n")

                    stat_hdr = sub.get("Status")
                    if stat_hdr and not status_code:
                        status_code = str(stat_hdr).strip()

                    diag_hdr = sub.get("Diagnostic-Code")
                    if diag_hdr and not diagnostic_code:
                        clean_d = str(diag_hdr)
                        if ";" in clean_d:
                            clean_d = clean_d.split(";", 1)[1]
                        diagnostic_code = " ".join(clean_d.split())

                    act_hdr = sub.get("Action")
                    if act_hdr:
                        action = str(act_hdr).strip().lower()

        text_content = ""
        dec_payload = part.get_payload(decode=True)
        if dec_payload:
            try:
                text_content = dec_payload.decode("utf-8", errors="replace")
            except Exception:
                text_content = str(dec_payload)
        elif isinstance(payload, str):
            text_content = payload

        if text_content:
            full_body_text += "\n" + text_content

    # Fallback to scanning body text if delivery-status block was missing
    if not failed_recipient:
        match_rcpt = _FINAL_RECIPIENT_REGEX.search(full_body_text)
        if match_rcpt:
            failed_recipient = match_rcpt.group(1).strip("<> ")
        else:
            match_failed = _FAILED_ADDRESS_REGEX.search(full_body_text)
            if match_failed:
                failed_recipient = match_failed.group(1).strip("<> ")

    if not status_code:
        match_stat = _STATUS_CODE_REGEX.search(full_body_text)
        if match_stat:
            status_code = match_stat.group(1)

    if not diagnostic_code:
        match_d = _DIAGNOSTIC_CODE_REGEX.search(full_body_text)
        if match_d:
            diagnostic_code = " ".join(match_d.group(1).split())
        else:
            # Check for Google Mail "The response was:" block
            resp_match = re.search(
                r"The response was:\s*([^\r\n]+(?:\r?\n\s+[^\r\n]+)*)",
                full_body_text,
                re.IGNORECASE,
            )
            if resp_match:
                diagnostic_code = " ".join(resp_match.group(1).split())
            else:
                # Look for 550 or 5xx error line
                err_match = re.search(r"(?:5\d\d\s+[^\r\n]+)", full_body_text)
                if err_match:
                    diagnostic_code = err_match.group(0).strip()

    # Determine if hard bounce
    is_hard = False
    lower_diag = diagnostic_code.lower()
    if (
        (status_code and status_code.startswith("5."))
        or "user unknown" in lower_diag
        or "no such user" in lower_diag
        or "mailbox unavailable" in lower_diag
        or "not accept our requests to connect" in lower_diag
        or "domain does not exist" in lower_diag
        or "timed out" in lower_diag
    ):
        is_hard = True
    elif not status_code and (is_bounce_sender(from_addr) or is_bounce_subject(subject)):
        # Default unclassified bounces with permanent error keywords to hard bounce
        is_hard = True

    return BounceReport(
        is_bounce=True,
        failed_recipient=failed_recipient.lower() if failed_recipient else None,
        status_code=status_code,
        action=action,
        diagnostic_code=diagnostic_code,
        is_hard_bounce=is_hard,
        original_subject=subject,
    )
