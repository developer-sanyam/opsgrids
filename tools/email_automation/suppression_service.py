"""Database suppression and blocklist service.

Ensures invalid, wrong, or bounced email addresses are recorded into the database
and permanently blocked so the platform never sends to them again.
"""

from __future__ import annotations

import contextlib
import hashlib
import uuid
from typing import Final

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tools.email_automation.models import EmailSuppression
from tools.email_automation.validator import normalize_email

# Attempt to use OpsGrids platform_core PII envelope if available
try:
    from platform_core import pii

    _HAVE_PLATFORM_PII = True
except ImportError:
    _HAVE_PLATFORM_PII = False

_FALLBACK_SALT: Final = b"opsgrids-email-suppression-salt-v1"


def compute_email_index(email: str) -> str:
    """Computes a deterministic blind index for email lookups."""
    norm = normalize_email(email)
    if _HAVE_PLATFORM_PII:
        with contextlib.suppress(Exception):
            return pii.blind_index(norm, kind="email")

    # Deterministic SHA-256 hash fallback
    h = hashlib.sha256(_FALLBACK_SALT + norm.encode("utf-8"))
    return h.hexdigest()


def mask_email(email: str) -> str:
    """Masks local part of email for logging and safe audit display."""
    norm = normalize_email(email)
    if _HAVE_PLATFORM_PII:
        with contextlib.suppress(Exception):
            return pii.mask(norm, kind="email")

    if "@" not in norm:
        return "***"
    local, domain = norm.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[0] + ("*" * (len(local) - 2)) + local[-1]
    return f"{masked_local}@{domain}"


def is_email_blocked(session: Session, organization_id: str, email: str) -> bool:
    """Checks whether an email address is blocked/suppressed in the database.

    Fail-closed gate: if an email is present in the suppression table,
    it MUST NOT be sent to under any circumstances.
    """
    if not email:
        return True

    norm = normalize_email(email)
    idx = compute_email_index(norm)

    stmt = select(EmailSuppression.id).where(
        EmailSuppression.organization_id == organization_id,
        EmailSuppression.email_index == idx,
    )
    result = session.execute(stmt)
    return result.scalar_one_or_none() is not None


def get_suppression(session: Session, organization_id: str, email: str) -> EmailSuppression | None:
    """Retrieves suppression record for an email if blocked."""
    norm = normalize_email(email)
    idx = compute_email_index(norm)

    stmt = select(EmailSuppression).where(
        EmailSuppression.organization_id == organization_id,
        EmailSuppression.email_index == idx,
    )
    result = session.execute(stmt)
    return result.scalar_one_or_none()


def block_email(
    session: Session,
    organization_id: str,
    email: str,
    reason: str,
    diagnostic_detail: str = "",
) -> EmailSuppression:
    """Blocks an email in the database suppression ledger.

    Reasons:
    - 'bounced': Recipient server bounced the message
    - 'invalid_syntax': Malformed RFC syntax
    - 'mailbox_not_found': User unknown / 5.1.1
    - 'domain_not_found': Non-existent domain or lack of MX records
    - 'manual': Operator block
    """
    norm = normalize_email(email)
    idx = compute_email_index(norm)
    masked = mask_email(norm)

    existing = get_suppression(session, organization_id, norm)
    if existing:
        if diagnostic_detail and not existing.diagnostic_detail:
            existing.diagnostic_detail = diagnostic_detail
            session.flush()
        return existing

    suppression = EmailSuppression(
        id=str(uuid.uuid4()),
        organization_id=organization_id,
        email=norm,
        email_masked=masked,
        email_index=idx,
        reason=reason,
        diagnostic_detail=diagnostic_detail[:1000] if diagnostic_detail else "",
    )
    session.add(suppression)
    session.flush()
    return suppression


def list_suppressions(
    session: Session,
    organization_id: str,
    limit: int = 100,
    offset: int = 0,
) -> list[EmailSuppression]:
    """Returns paginated list of blocked emails."""
    stmt = (
        select(EmailSuppression)
        .where(EmailSuppression.organization_id == organization_id)
        .order_by(EmailSuppression.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = session.execute(stmt)
    return list(result.scalars().all())


def unblock_email(session: Session, organization_id: str, email: str, force: bool = False) -> bool:
    """Unblocks an email if manually blocked or forced by admin."""
    norm = normalize_email(email)
    idx = compute_email_index(norm)

    suppression = get_suppression(session, organization_id, norm)
    if not suppression:
        return False

    if suppression.reason in ("bounced", "mailbox_not_found") and not force:
        # Refuse to unblock permanent delivery failure without admin override
        return False

    stmt = delete(EmailSuppression).where(
        EmailSuppression.organization_id == organization_id,
        EmailSuppression.email_index == idx,
    )
    session.execute(stmt)
    session.flush()
    return True
