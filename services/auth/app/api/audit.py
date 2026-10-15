"""`GET /api/v1/audit/auth`: this service's slice of the organization audit log.

The shared route (`platform_core.audit_read`) over this schema's own `audit_logs`. It is gated on
`core.identity.audit.read` at ORG scope, filters on the caller's organization in the query, and
returns field names but never values. The portal merges this page with the other sources';
nothing reads across schemas.
"""

from __future__ import annotations

from app.models import AuditLog
from platform_core.audit_read import build_audit_router

#: The sign-in trail: who signed in (password, OAuth, SSO) and who changed or failed to change a
#: password. Sign-out and password-reset requests write no audit row today (they publish events
#: only), and a failed sign-in has no organization to file under. Every other `auth` row (MFA,
#: step-up) stays out, and rows are returned bare: actor, action and time.
SIGN_IN_ACTIONS = frozenset(
    {
        "auth.password_verified",
        "auth.oauth_signed_in",
        "auth.sso_signed_in",
        "auth.password_changed",
        "auth.password_change_failed",
    }
)

router = build_audit_router(AuditLog, service="auth", product="core", only_actions=SIGN_IN_ACTIONS)
