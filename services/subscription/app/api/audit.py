"""`GET /api/v1/audit/subscription`: this service's slice of the organization audit log.

The shared route (`platform_core.audit_read`) over this schema's own `audit_logs`. It is gated on
`core.identity.audit.read` at ORG scope, filters on the caller's organization in the query, and
returns field names but never values. The portal merges this page with the other sources';
nothing reads across schemas.
"""

from __future__ import annotations

from app.models import AuditLog
from platform_core.audit_read import build_audit_router

#: An operator's note (free text in `reason`) is staff-only (`ExtendTrialIn.note`): only machine
#: reason codes such as `dunning_retry` are returned to the organization.
router = build_audit_router(
    AuditLog, service="subscription", product="core", reason_codes_only=True
)
