"""Orchestration engine coordinating reading, auto-replying, and suppression blocking."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from tools.email_automation.bounce_parser import parse_bounce_report
from tools.email_automation.config import EmailConfig
from tools.email_automation.db import get_session, init_db
from tools.email_automation.reader import EmailMessageData, EmailReader
from tools.email_automation.responder import (
    AntiLoopGuard,
    EmailResponder,
    draft_automated_reply,
)
from tools.email_automation.suppression_service import (
    block_email,
    is_email_blocked,
)
from tools.email_automation.validator import (
    validate_email_domain,
    validate_email_syntax,
)

logger = logging.getLogger("email_automation.orchestrator")


@dataclass
class OrchestrationResult:
    """Summary metrics of an email processing run."""

    emails_processed: int = 0
    bounces_detected: list[dict[str, Any]] = field(default_factory=list)
    wrong_emails_blocked: list[dict[str, Any]] = field(default_factory=list)
    replies_sent: list[dict[str, Any]] = field(default_factory=list)
    replies_skipped: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "emails_processed": self.emails_processed,
            "bounces_detected_count": len(self.bounces_detected),
            "bounces_detected": self.bounces_detected,
            "wrong_emails_blocked_count": len(self.wrong_emails_blocked),
            "wrong_emails_blocked": self.wrong_emails_blocked,
            "replies_sent_count": len(self.replies_sent),
            "replies_sent": self.replies_sent,
            "replies_skipped_count": len(self.replies_skipped),
            "replies_skipped": self.replies_skipped,
        }


class EmailOrchestrationEngine:
    """End-to-end coordinator for email automation and suppression management."""

    def __init__(self, config: EmailConfig | None = None) -> None:
        self.config = config or EmailConfig.from_env()
        self.responder = EmailResponder(self.config)

    def process_incoming_emails(
        self,
        messages: list[EmailMessageData] | None = None,
        reader: EmailReader | None = None,
        limit: int = 50,
        validate_domains: bool = True,
        mark_as_read: bool = True,
    ) -> OrchestrationResult:
        """Processes a batch of incoming emails.

        1. Fetches from IMAP if messages are not directly provided.
        2. Detects bounces and blocks bad recipient addresses in the database.
        3. Validates sender addresses and blocks invalid/wrong addresses.
        4. Gating check: skips blocked addresses so we never send to them again.
        5. Sends automated replies for eligible incoming emails.
        """
        init_db()
        result = OrchestrationResult()

        # Step 1: Read emails if not passed in directly
        if messages is None:
            rdr = reader or EmailReader(self.config)
            try:
                rdr.connect()
                messages = rdr.fetch_unseen_emails(limit=limit, mark_as_read=mark_as_read)
            finally:
                if reader is None:
                    rdr.close()

        result.emails_processed = len(messages)

        with get_session() as session:
            for msg in messages:
                self._process_single_message(
                    msg, session, result, validate_domains=validate_domains
                )

        return result

    def _process_single_message(
        self,
        msg: EmailMessageData,
        session: Session,
        result: OrchestrationResult,
        validate_domains: bool = True,
    ) -> None:
        """Processes one incoming email through the full pipeline."""
        sender = msg.from_email.lower().strip()

        # Branch 1: Inbound is a Bounce / Delivery Status Notification
        if msg.is_bounce and msg.raw_message is not None:
            bounce = parse_bounce_report(
                msg.raw_message, subject=msg.subject, from_addr=msg.from_email
            )
            if bounce.failed_recipient:
                bad_email = bounce.failed_recipient.lower().strip()
                diag = bounce.diagnostic_code or f"DSN Status {bounce.status_code}"
                suppression = block_email(
                    session,
                    self.config.organization_id,
                    bad_email,
                    reason="bounced",
                    diagnostic_detail=diag,
                )
                result.bounces_detected.append(
                    {
                        "failed_recipient": bad_email,
                        "status_code": bounce.status_code,
                        "diagnostic": diag,
                        "suppression_id": suppression.id,
                    }
                )
                logger.info("Blocked bounced email in database: %s (reason: %s)", bad_email, diag)
            result.replies_skipped.append(
                {"sender": sender, "reason": "inbound_bounce_notification"}
            )
            return

        # Branch 2: Sender Email Address Verification (Detect Wrong Emails Early)
        syntax_ok, syntax_reason = validate_email_syntax(sender)
        if not syntax_ok:
            suppression = block_email(
                session,
                self.config.organization_id,
                sender,
                reason="invalid_syntax",
                diagnostic_detail=syntax_reason,
            )
            result.wrong_emails_blocked.append(
                {"email": sender, "reason": "invalid_syntax", "detail": syntax_reason}
            )
            result.replies_skipped.append(
                {"sender": sender, "reason": f"invalid_syntax: {syntax_reason}"}
            )
            logger.warning("Blocked wrong email syntax in database: %s", sender)
            return

        if validate_domains:
            domain_ok, domain_reason = validate_email_domain(sender, check_mx=True)
            if not domain_ok:
                suppression = block_email(
                    session,
                    self.config.organization_id,
                    sender,
                    reason="domain_not_found",
                    diagnostic_detail=domain_reason,
                )
                result.wrong_emails_blocked.append(
                    {"email": sender, "reason": "domain_not_found", "detail": domain_reason}
                )
                result.replies_skipped.append(
                    {"sender": sender, "reason": f"domain_not_found: {domain_reason}"}
                )
                logger.warning("Blocked non-existent email domain in database: %s", sender)
                return

        # Branch 3: Pre-Send Database Suppression Gate
        # Verify if address is already suppressed/blocked in database
        blocked = is_email_blocked(session, self.config.organization_id, sender)
        if blocked:
            result.replies_skipped.append(
                {"sender": sender, "reason": "blocked_in_database_suppression_list"}
            )
            logger.info("Refusing to send reply: %s is blocked in database", sender)
            return

        # Branch 4: Auto-Reply Policy & Anti-Loop Guard
        if not self.config.auto_reply_enabled:
            result.replies_skipped.append({"sender": sender, "reason": "auto_reply_disabled"})
            return

        should_reply, reason = AntiLoopGuard.should_reply(msg, self.config, session)
        if not should_reply:
            result.replies_skipped.append({"sender": sender, "reason": reason})
            logger.info("Skipped auto-reply for %s: %s", sender, reason)
            return

        # Branch 5: Draft Contextual Reply and Dispatch
        reply_body = draft_automated_reply(msg, self.config)
        sent, status_msg, reply_msg_id = self.responder.send_automated_reply(
            msg, reply_body, session
        )

        if sent:
            result.replies_sent.append(
                {
                    "recipient": sender,
                    "subject": msg.subject,
                    "reply_message_id": reply_msg_id,
                    "status": status_msg,
                }
            )
            logger.info("Auto-reply sent to %s: %s", sender, reply_msg_id)
        else:
            result.replies_skipped.append({"sender": sender, "reason": status_msg})
            logger.error("Failed to send auto-reply to %s: %s", sender, status_msg)
