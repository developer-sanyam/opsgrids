"""Request and response contracts for the auth service.

The refresh token is **never** in a response body — it is set as an httpOnly cookie, so
JavaScript cannot read it and an XSS payload cannot steal a long-lived credential. Only the
short-lived access token is returned, and the SPA keeps that in memory.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from platform_core.egress import EgressBlockedError, check_url_shape

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
    #: Index-aligned with `roles` — display labels from account-service's `roles` table,
    #: falling back to the code itself for one it has no name for. Additive: `roles` is
    #: unchanged, this rides alongside it (`DirectoryMembership.display_names_for`).
    role_names: list[str] = Field(default_factory=list)


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
    #: Index-aligned with `roles`, same source and fallback as `OrganizationChoice.role_names`.
    role_names: list[str] = Field(default_factory=list)
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

    @field_validator("discovery_url", "authorization_endpoint", "token_endpoint", "jwks_uri")
    @classmethod
    def _https_no_userinfo(cls, value: str | None) -> str | None:
        """Refused at write time, not on the next sign-in: `https`, no embedded credentials,
        no bare IP. These four are exactly what `HttpIdpClient` (or a browser redirect, for
        `authorization_endpoint`) later uses — see `platform_core.egress` for why each of
        those three is a documented SSRF bypass and not a style preference. This is the shape
        half only; whether the host resolves somewhere this service must not reach is a
        network question, checked at fetch time by the same module.
        """
        if value is None:
            return value
        try:
            check_url_shape(value)
        except EgressBlockedError as exc:
            raise ValueError(str(exc)) from exc
        return value


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


class OperatorMfaRequired(BaseModel):
    """A staff account with an already-confirmed factor must present it."""

    status: Literal["mfa_required"] = "mfa_required"
    pre_auth_token: str


class OperatorMfaEnrollmentRequired(BaseModel):
    """A staff account with no confirmed factor. There is no further path but enrolling one —
    unlike the tenant flow, an operator cannot land here and simply keep going."""

    status: Literal["mfa_enrollment_required"] = "mfa_enrollment_required"
    pre_auth_token: str


OperatorLoginResult = OperatorMfaRequired | OperatorMfaEnrollmentRequired


class OperatorMfaEnrolIn(BaseModel):
    pre_auth_token: str
    #: The out-of-band code issued for this staff account — a password alone is not enough to
    #: reach enrolment (ADR-0003 addendum). Shaped like a recovery code, because it is issued
    #: through the same generator.
    enrollment_code: str = Field(min_length=8, max_length=40)


class OperatorMfaEnrolConfirmIn(BaseModel):
    pre_auth_token: str
    factor_id: uuid.UUID
    code: str = Field(min_length=6, max_length=8)


class OperatorMfaEnrolled(BaseModel):
    """Enrolling completes the sign-in that started it — see
    `services.confirm_operator_mfa_enrolment` for why re-presenting the same code through
    `/mfa/verify` would be redundant. Recovery codes are shown once, exactly as the tenant
    flow's are."""

    status: Literal["complete"] = "complete"
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105
    expires_in: int
    operator_roles: list[str]
    recovery_codes: list[str]


class OperatorMfaVerifyIn(BaseModel):
    pre_auth_token: str
    code: str = Field(min_length=6, max_length=32)


class OperatorEnrollmentCodeIn(BaseModel):
    """An `owner` issuing another staff account's out-of-band MFA enrolment code."""

    staff_id: uuid.UUID


class OperatorEnrollmentCodeOut(BaseModel):
    """Shown once — stored only as a hash, and never retrievable again."""

    staff_id: uuid.UUID
    enrollment_code: str
    expires_in_minutes: int


class OperatorMeOut(BaseModel):
    """Staff identity and operator roles — the operator console's equivalent of `/auth/me`."""

    staff_id: uuid.UUID
    email: str
    display_name: str
    operator_roles: list[str]
    amr: list[str]
