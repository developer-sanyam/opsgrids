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


# --------------------------------------------------------------------- single sign-on


class SsoStartOut(BaseModel):
    """Where to send the browser to authenticate.

    JSON rather than a 302 because the caller is a single-page app: it needs to stash its own
    return location before leaving, and a redirect the fetch layer follows invisibly gives it
    nowhere to do that. `state` is echoed so the client can recognise its own callback; it is
    not a credential — the value that matters is stored server-side against it, and it is
    single-use.
    """

    authorization_url: str
    state: str
    expires_in: int
    #: For the "signing you in to Acme via Okta" line, so the person can tell before they
    #: leave whether they are being sent somewhere they expect.
    organization_slug: str
    provider_name: str


class SsoConfigIn(BaseModel):
    """The administrative form. **The secret goes in and never comes back.**

    `client_secret` is optional on update: leaving it out keeps the stored one, so changing a
    display name does not mean re-typing a credential.
    """

    display_name: str = Field(min_length=1, max_length=120)
    issuer: str = Field(min_length=1, max_length=255)
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: str | None = Field(default=None, min_length=1, max_length=500)
    #: Fetched once, on save, to fill the three endpoints below.
    discovery_url: str | None = Field(default=None, max_length=500)
    authorization_endpoint: str | None = Field(default=None, max_length=500)
    token_endpoint: str | None = Field(default=None, max_length=500)
    jwks_uri: str | None = Field(default=None, max_length=500)
    scopes: list[str] | None = None
    #: Which of the organization's **verified** domains route here. Empty means all of them.
    #: Listing one the organization has not verified does not make it usable.
    allowed_domains: list[str] = Field(default_factory=list)
    enabled: bool = False
    #: Turns password sign-in off for this organization. Per-organization, because it is a
    #: customer's decision about their own people.
    enforce_sso: bool = False


class SsoConfigOut(BaseModel):
    """What the console renders. Note the absence: there is no secret field at all.

    Not a masked one either — a mask of a client secret is a hint about a credential and
    buys the reader nothing they need. `client_secret_set` says whether one is stored, which
    is the only question the form has to answer.
    """

    id: uuid.UUID
    protocol: str
    display_name: str
    enabled: bool
    issuer: str | None
    client_id: str | None
    client_secret_set: bool
    discovery_url: str | None
    authorization_endpoint: str | None
    token_endpoint: str | None
    jwks_uri: str | None
    scopes: list[str]
    allowed_domains: list[str]
    enforce_sso: bool
    #: Reported so the console can say what it does today rather than what the column
    #: implies: unknown users are refused, and an invitation is the way in.
    jit_provisioning: bool


# --------------------------------------------------------------------- operator staff


class OperatorLoginIn(BaseModel):
    """An OpsGrids operator signing in. No organization: staff belong to none."""

    email: EmailStr
    password: Password


class OperatorTokenOut(BaseModel):
    """A completed operator sign-in.

    Carries `aud="operator"` and `stf=true` inside the token (not repeated here); the
    refresh token is set as an httpOnly cookie, never in this body. `operator_roles` is
    surfaced so the console can render without decoding the token.
    """

    status: Literal["complete"] = "complete"
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105
    expires_in: int
    operator_roles: list[str]
