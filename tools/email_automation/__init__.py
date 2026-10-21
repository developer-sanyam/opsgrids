"""Email automation and suppression guard package.

Follows Claude Code / Agentic workflow best practice:
- Command -> Agent -> Skill orchestration
- IMAP inbox reader & MIME parser
- Contextual automated reply generator with anti-loop guards
- RFC 3464 bounce & DSN parser
- Database suppression & blocklist ledger (fail-closed send gate)
"""

from tools.email_automation.bounce_parser import (
    BounceReport,
    is_bounce_message,
    parse_bounce_report,
)
from tools.email_automation.config import EmailConfig
from tools.email_automation.models import EmailSuppression, InboundEmailLog
from tools.email_automation.orchestrator import EmailOrchestrationEngine, OrchestrationResult
from tools.email_automation.reader import EmailMessageData, EmailReader
from tools.email_automation.responder import AntiLoopGuard, EmailResponder
from tools.email_automation.suppression_service import (
    block_email,
    get_suppression,
    is_email_blocked,
    list_suppressions,
    unblock_email,
)
from tools.email_automation.validator import (
    validate_email_domain,
    validate_email_syntax,
    verify_email,
)

__all__ = [
    "AntiLoopGuard",
    "BounceReport",
    "EmailConfig",
    "EmailMessageData",
    "EmailOrchestrationEngine",
    "EmailReader",
    "EmailResponder",
    "EmailSuppression",
    "InboundEmailLog",
    "OrchestrationResult",
    "block_email",
    "get_suppression",
    "is_bounce_message",
    "is_email_blocked",
    "list_suppressions",
    "parse_bounce_report",
    "unblock_email",
    "validate_email_domain",
    "validate_email_syntax",
    "verify_email",
]
