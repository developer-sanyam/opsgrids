"""Shared test scaffolding for insights."""

from __future__ import annotations

import uuid

from platform_core.auth import Principal


def principal_for(
    org_id: uuid.UUID,
    *,
    subject_ref: uuid.UUID | None = None,
    perms: list[str] | None = None,
    entitlements: list[str] | None = None,
) -> Principal:
    """The principal the gateway would have forwarded (`X-Perms`/`X-Entitlements`)."""
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        subject_ref=subject_ref,
        perms=perms or [],
        entitlements=entitlements or [],
    )
