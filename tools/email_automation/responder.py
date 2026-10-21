"""Automated email reply generator and SMTP delivery engine with anti-loop guards."""

from __future__ import annotations

import contextlib
import email.message
import email.utils
import hashlib
import re
import smtplib
import uuid
from datetime import UTC, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from tools.email_automation.config import EmailConfig
from tools.email_automation.models import InboundEmailLog, utc_now
from tools.email_automation.reader import EmailMessageData
from tools.email_automation.suppression_service import is_email_blocked


def compute_body_hash(text: str) -> str:
    """Computes a normalized SHA-256 hash of email body text for duplicate detection."""
    cleaned = " ".join((text or "").split()).strip().lower()
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


# RFC 3834 loop protection headers and subjects
_OUT_OF_OFFICE_REGEX: Final = re.compile(
    r"^(?:auto(?:matic)?\s*reply|out\s*of\s*office|vacation\s*notice|absent\s*until)",
    re.IGNORECASE,
)


class AntiLoopGuard:
    """Evaluates whether an incoming email is safe to send an automated reply to."""

    @staticmethod
    def should_reply(
        incoming: EmailMessageData,
        config: EmailConfig,
        session: Session,
    ) -> tuple[bool, str]:
        """Runs checks to prevent loops, spam cascades, and sending to blocked addresses.

        Returns (should_reply: bool, reason: str).
        """
        # 1. Blocked / Suppressed Address Check (Fail-Closed)
        if not incoming.from_email:
            return False, "missing_sender_address"

        is_blocked = is_email_blocked(session, config.organization_id, incoming.from_email)
        if is_blocked:
            return False, f"suppressed_in_database: {incoming.from_email} is blocked"

        # 2. Bounce Detection Guard
        if incoming.is_bounce:
            return False, "inbound_is_bounce_notice"

        # 3. Auto-Submitted Header Check (RFC 3834)
        if incoming.is_auto_submitted:
            return False, "inbound_is_auto_submitted"

        # 4. Out of Office / Vacation Notice Subject
        if _OUT_OF_OFFICE_REGEX.search(incoming.subject.strip()):
            return False, "inbound_is_out_of_office_notice"

        # 5. Precedence Bulk / Junk
        if incoming.precedence in ("bulk", "junk", "list", "auto_reply"):
            return False, f"inbound_precedence_{incoming.precedence}"

        # 6. Self-Loop Guard (do not reply to ourselves)
        if config.from_email and incoming.from_email.lower() == config.from_email.lower():
            return False, "self_address_loop_prevention"

        # 7. Duplicate Content Detection
        body_h = compute_body_hash(incoming.body_text)
        if body_h:
            dup_stmt = (
                select(InboundEmailLog.id)
                .where(
                    InboundEmailLog.organization_id == config.organization_id,
                    InboundEmailLog.sender_email == incoming.from_email,
                    InboundEmailLog.body_hash == body_h,
                )
                .limit(1)
            )
            if session.execute(dup_stmt).scalar_one_or_none():
                return (
                    False,
                    "duplicate_email: identical content received within deduplication window",
                )

        # 8. Deduplication & Rate Limiting Check
        stmt = (
            select(InboundEmailLog)
            .where(
                InboundEmailLog.organization_id == config.organization_id,
                InboundEmailLog.sender_email == incoming.from_email,
                InboundEmailLog.is_auto_replied.is_(True),
            )
            .order_by(InboundEmailLog.replied_at.desc())
            .limit(1)
        )
        last_log = session.execute(stmt).scalar_one_or_none()
        if last_log and last_log.replied_at:
            # Ensure replied_at is aware UTC
            replied_at = last_log.replied_at
            if replied_at.tzinfo is None:
                replied_at = replied_at.replace(tzinfo=UTC)
            time_since_reply = utc_now() - replied_at
            if time_since_reply < timedelta(hours=config.rate_limit_hours):
                return False, (
                    f"rate_limited: already replied {time_since_reply.total_seconds() / 3600:.1f}h "
                    f"ago (window is {config.rate_limit_hours}h)"
                )

        return True, "eligible"


def draft_automated_reply(incoming: EmailMessageData, config: EmailConfig) -> str:
    """Generates an automated, context-aware reply body using AI (ChatGPT / Gemini) or templates."""
    from tools.email_automation.ai_engine import AIReplyEngine

    ai_engine = AIReplyEngine(
        openai_key=config.openai_api_key,
        gemini_key=config.gemini_api_key,
        openai_model=config.openai_model,
        gemini_model=config.gemini_model,
        provider=config.ai_provider,
    )

    if ai_engine.has_ai_configured():
        with contextlib.suppress(Exception):
            return ai_engine.generate_reply(
                incoming_message=incoming.body_text or incoming.subject,
                sender_name=incoming.from_name,
                subject=incoming.subject,
                channel="email",
            )

    subject = incoming.subject or "(no subject)"

    # Clean subject for display
    clean_subj = subject
    if clean_subj.lower().startswith("re:"):
        clean_subj = clean_subj[3:].strip()

    greeting = f"Hello {incoming.from_name}," if incoming.from_name else "Hello,"

    template = config.auto_reply_template
    formatted = template.format(
        subject=clean_subj,
        from_name=config.from_name,
        recipient_name=incoming.from_name or "there",
    )

    if not formatted.startswith("Hello"):
        formatted = f"{greeting}\n\n{formatted}"

    return formatted


class EmailResponder:
    """Handles dispatch of automated email replies over SMTP."""

    def __init__(self, config: EmailConfig | None = None) -> None:
        self.config = config or EmailConfig.from_env()

    def send_automated_reply(
        self,
        incoming: EmailMessageData,
        reply_body: str,
        session: Session,
    ) -> tuple[bool, str, str | None]:
        """Prepares and dispatches an automated reply with proper RFC headers.

        Returns (success: bool, status_message: str, reply_message_id: str | None).
        """
        # Final pre-flight suppression check
        if is_email_blocked(session, self.config.organization_id, incoming.from_email):
            return False, f"refused_send: {incoming.from_email} is suppressed in database", None

        # Build clean Re: subject
        clean_subject = incoming.subject or ""
        if not clean_subject.lower().startswith("re:"):
            clean_subject = f"Re: {clean_subject}".strip()

        # Generate unique Message-ID for this reply
        domain = (
            self.config.from_email.split("@")[-1] if "@" in self.config.from_email else "localhost"
        )
        reply_msg_id = f"<{uuid.uuid4()}@{domain}>"

        # Construct MIME Message
        msg = email.message.EmailMessage()
        msg["From"] = f"{self.config.from_name} <{self.config.from_email}>"
        msg["To"] = incoming.from_email
        msg["Subject"] = clean_subject
        msg["Date"] = email.utils.formatdate(localtime=False, usegmt=True)
        msg["Message-ID"] = reply_msg_id

        # Threading Headers
        if incoming.message_id:
            msg["In-Reply-To"] = f"<{incoming.message_id.strip('<> ')}>"
            # References chain
            refs = list(incoming.references)
            refs.append(incoming.message_id.strip("<> "))
            msg["References"] = " ".join(f"<{r}>" for r in refs if r)

        # RFC 3834 Header: Mark as auto-replied so recipient mail systems do not loop
        msg["Auto-Submitted"] = "auto-replied"
        msg["Precedence"] = "auto_reply"

        msg.set_content(reply_body)

        # Dry-run execution
        if self.config.dry_run:
            self._record_log(session, incoming, reply_msg_id)
            return True, f"dry_run: reply staged for {incoming.from_email}", reply_msg_id

        # Live SMTP delivery
        try:
            self._dispatch_smtp(msg, incoming.from_email)
            self._record_log(session, incoming, reply_msg_id)
            return (
                True,
                f"sent: reply successfully dispatched to {incoming.from_email}",
                reply_msg_id,
            )
        except Exception as exc:
            return False, f"smtp_error: {exc!s}", None

    def _dispatch_smtp(self, msg: email.message.EmailMessage, recipient: str) -> None:
        """Synchronously delivers MIME message via SMTP (handles TLS/SSL)."""
        if self.config.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                self.config.smtp_host, self.config.smtp_port, timeout=10.0
            ) as server:
                if self.config.username and self.config.password:
                    server.login(self.config.username, self.config.password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=10.0) as server:
                if self.config.smtp_use_tls:
                    server.starttls()
                if self.config.username and self.config.password:
                    server.login(self.config.username, self.config.password)
                server.send_message(msg)

    def _record_log(
        self,
        session: Session,
        incoming: EmailMessageData,
        reply_message_id: str,
    ) -> None:
        """Records inbound log and marks auto_replied = True."""
        body_h = compute_body_hash(incoming.body_text)
        log_entry = InboundEmailLog(
            id=str(uuid.uuid4()),
            organization_id=self.config.organization_id,
            message_id=incoming.message_id,
            sender_email=incoming.from_email,
            subject=incoming.subject[:500] if incoming.subject else "",
            body_hash=body_h,
            is_bounce=incoming.is_bounce,
            is_auto_replied=True,
            reply_message_id=reply_message_id,
            replied_at=utc_now(),
        )
        session.add(log_entry)
        session.flush()
