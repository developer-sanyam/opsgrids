"""Configuration for email connection, responder, and suppression engine."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field


@dataclass
class EmailConfig:
    """Holds all configuration parameters for email operations."""

    # IMAP Configuration
    imap_host: str = field(default_factory=lambda: os.getenv("EMAIL_IMAP_HOST", "imap.gmail.com"))
    imap_port: int = field(default_factory=lambda: int(os.getenv("EMAIL_IMAP_PORT", "993")))
    imap_use_ssl: bool = field(
        default_factory=lambda: (
            os.getenv("EMAIL_IMAP_USE_SSL", "true").lower() in ("true", "1", "yes")
        )
    )

    # SMTP Configuration
    smtp_host: str = field(default_factory=lambda: os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("EMAIL_SMTP_PORT", "587")))
    smtp_use_tls: bool = field(
        default_factory=lambda: (
            os.getenv("EMAIL_SMTP_USE_TLS", "true").lower() in ("true", "1", "yes")
        )
    )
    smtp_use_ssl: bool = field(
        default_factory=lambda: (
            os.getenv("EMAIL_SMTP_USE_SSL", "false").lower() in ("true", "1", "yes")
        )
    )

    # Authentication
    username: str = field(default_factory=lambda: os.getenv("EMAIL_USERNAME", ""))
    password: str = field(default_factory=lambda: os.getenv("EMAIL_PASSWORD", ""))

    # Identity
    from_name: str = field(
        default_factory=lambda: os.getenv("EMAIL_FROM_NAME", "Customer Operations")
    )
    from_email: str = field(
        default_factory=lambda: os.getenv("EMAIL_FROM_EMAIL", os.getenv("EMAIL_USERNAME", ""))
    )

    # Multi-tenancy & Database
    organization_id: str = field(
        default_factory=lambda: os.getenv("EMAIL_ORGANIZATION_ID", str(uuid.UUID(int=0)))
    )
    db_url: str = field(
        default_factory=lambda: os.getenv(
            "EMAIL_DATABASE_URL",
            os.getenv("DATABASE_URL", "sqlite+aiosqlite:///email_automation.db"),
        )
    )

    # AI Configuration (ChatGPT / Google Gemini)
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    ai_provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "auto"))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-1.5-flash"))

    # Behavior & Policies
    dry_run: bool = field(
        default_factory=lambda: os.getenv("EMAIL_DRY_RUN", "false").lower() in ("true", "1", "yes")
    )
    auto_reply_enabled: bool = field(
        default_factory=lambda: (
            os.getenv("EMAIL_AUTO_REPLY_ENABLED", "true").lower() in ("true", "1", "yes")
        )
    )
    rate_limit_hours: int = field(
        default_factory=lambda: int(os.getenv("EMAIL_RATE_LIMIT_HOURS", "24"))
    )
    auto_reply_template: str = field(
        default_factory=lambda: os.getenv(
            "EMAIL_AUTO_REPLY_TEMPLATE",
            (
                "Hello,\n\n"
                "Thank you for contacting us. We have received your email regarding '{subject}' "
                "and our team is reviewing it. We will get back to you shortly.\n\n"
                "Best regards,\n"
                "{from_name}"
            ),
        )
    )

    # PII Encryption Key (optional, defaults to empty or platform key)
    pii_encryption_key: str = field(default_factory=lambda: os.getenv("PII_ENCRYPTION_KEY", ""))

    @classmethod
    def from_env(cls) -> EmailConfig:
        return cls()
