"""Request and response contracts for the auth service.

The refresh token is **never** in a response body — it is set as an httpOnly cookie, so
JavaScript cannot read it and an XSS payload cannot steal a long-lived credential. Only the
short-lived access token is returned, and the SPA keeps that in memory.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field

Password = Annotated[str, Field(min_length=8, max_length=200)]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


class LoginIn(BaseModel):
    email: EmailStr
    password: Password
    #: An organization slug. The gateway also supplies one from the request host; an
    #: explicit value here wins. Absent, and a person in one organization goes straight
    #: in while a person in several is offered a picker.
    organization: str | None = Field(default=None, max_length=60)


class OrganizationChoice(BaseModel):
    organization_id: uuid.UUID
    slug: str
    name: str
    roles: list[str]


class TokenOut(BaseModel):
    """A completed sign-in. No refresh token here — see the module docstring."""

    status: Literal["complete"] = "complete"
    access_token: str

    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105
    expires_in: int
    organization_id: uuid.UUID
    membership_id: uuid.UUID


class MfaRequired(BaseModel):
    status: Literal["mfa_required"] = "mfa_required"
    pre_auth_token: str


class OrgSelectionRequired(BaseModel):
    status: Literal["org_selection_required"] = "org_selection_required"
    pre_auth_token: str
    organizations: list[OrganizationChoice]


LoginResult = TokenOut | MfaRequired | OrgSelectionRequired


class SelectOrgIn(BaseModel):
    pre_auth_token: str
    organization_id: uuid.UUID


class SwitchOrgIn(BaseModel):
    organization_id: uuid.UUID


class MfaVerifyIn(BaseModel):
    pre_auth_token: str
    #: Six digits from an authenticator app, or a recovery code.
    code: str = Field(min_length=6, max_length=32)
    organization: str | None = Field(default=None, max_length=60)


class MfaEnrolStart(BaseModel):
    factor_id: uuid.UUID
    secret: str
    #: Render this as a QR code; the secret is shown as a fallback for manual entry.
    otpauth_uri: str


class MfaEnrolConfirmIn(BaseModel):
    factor_id: uuid.UUID
    code: str = Field(min_length=6, max_length=8)


class MfaEnrolled(BaseModel):
    """Recovery codes are shown **once**. They are stored hashed and never retrievable."""

    recovery_codes: list[str]


class PasswordChangeIn(BaseModel):
    current_password: Password
    new_password: Password


class PasswordForgotIn(BaseModel):
    email: EmailStr


class PasswordResetIn(BaseModel):
    token: str = Field(min_length=16, max_length=200)
    new_password: Password


class SessionOut(BaseModel):
    id: uuid.UUID
    active_org_id: uuid.UUID | None
    amr: list[str]
    idp: str
    device_label: str | None
    ip: str | None
    created_at: str
    last_seen_at: str


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    organization_id: uuid.UUID | None
    membership_id: uuid.UUID | None
    roles: list[str]
    amr: list[str]
    must_change_password: bool
    mfa_enabled: bool
    organizations: list[OrganizationChoice]
