"""Tables owned by the auth service — credentials, sessions and second factors.

Everything here lives in the `auth` schema and nothing outside it holds a foreign key into
it. In particular there is **no foreign key to `account.users`**: `user_id` is a bare UUID,
because a constraint across a schema boundary is what stops a later database-per-service
split being a config change.

The split with account-service is deliberate. account-service is the *directory* — who
exists, which organization they belong to, what they may do. This service is
*authentication* — proving somebody is who they say, and minting the token that says so.
A password hash living in the directory is one careless serialiser away from an API
response.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base

SCHEMA = "auth"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


# --------------------------------------------------------------------- credentials


class Credential(Base):
    """How one person proves who they are, plus the state that rate-limits guessing.

    `password_hash` is nullable on purpose: an account that only ever signs in through an
    identity provider has no password, and inventing one for it creates a second, weaker
    way in that nobody is watching.
    """

    __tablename__ = "credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))

    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Set per user; an organization's security policy can also demand it.
    mfa_enforced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --------------------------------------------------------------------- sessions


class Session(Base):
    """One sign-in, across however many organizations the person switches between.

    `id` is the `sid` claim. Keeping one session across an organization switch is what
    makes a single back-channel logout end everything — otherwise "sign this person out"
    has to find N sessions and will miss one.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: Mutable: this is what org switching changes.
    active_org_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: Authentication methods used, e.g. {"pwd"} or {"pwd","otp"}. Drives step-up.
    amr: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False, default=list)
    auth_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mfa_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idp: Mapped[str] = mapped_column(String(60), nullable=False, default="local")

    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    device_label: Mapped[str | None] = mapped_column(String(120))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(120))

    tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index(
            "ix_sessions_user_live",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None


class RefreshToken(Base):
    """One link in a rotating chain.

    Every use mints a replacement and marks this one rotated. Presenting a **spent** token
    means it was captured, so the whole family is revoked rather than just this link:
    the legitimate holder is forced to sign in again, and the thief gets nothing. That
    trade — an occasional forced re-login against silent session theft — is the right one.
    """

    __tablename__ = "refresh_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: Every token descended from one sign-in shares this.
    family: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[Session] = relationship(back_populates="tokens")

    @property
    def is_spent(self) -> bool:
        return self.rotated_at is not None or self.revoked_at is not None


# --------------------------------------------------------------------- second factors


class MfaFactor(Base):
    """A registered second factor.

    `secret_enc` is envelope-encrypted. **Losing the envelope key locks out every MFA user
    with no path back except recovery codes** — back it up before enabling MFA for anyone.
    """

    __tablename__ = "mfa_factors"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="totp")
    label: Mapped[str | None] = mapped_column(String(80))

    secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    #: WebAuthn, when that lands.
    credential_id: Mapped[bytes | None] = mapped_column(LargeBinary)
    public_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: A factor is not usable until the user proves they can generate a code from it.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The last counter step accepted, so a code cannot be replayed inside its window.
    last_used_step: Mapped[int | None] = mapped_column(Integer)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MfaRecoveryCode(Base):
    """A single-use way back in when the phone is gone. Stored hashed, like a password."""

    __tablename__ = "mfa_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------- resets and SSO


class PasswordResetToken(Base):
    """Short-lived, single-use, and stored only as a hash."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExternalIdentity(Base):
    """A link to an identity provider's subject.

    **Never linked on email alone.** Only when the provider asserts the address is verified
    *and* the domain is a verified domain of that provider's organization — matching on an
    unverified email is a documented account-takeover primitive.
    """

    __tablename__ = "external_identities"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    #: 'google' | 'microsoft' | 'saml:<org_id>'
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email_at_link: Mapped[str | None] = mapped_column(CITEXT)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_external_identities_provider_subject"),
    )


class OrgIdpConfig(Base):
    """One organization's identity provider.

    `enforce_sso` turning on means password login stops working for that organization —
    which is the point, and also why it is per-organization rather than global.
    """

    __tablename__ = "org_idp_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    protocol: Mapped[str] = mapped_column(String(10), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    issuer: Mapped[str | None] = mapped_column(String(255))
    discovery_url: Mapped[str | None] = mapped_column(String(500))
    client_id: Mapped[str | None] = mapped_column(String(255))
    client_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    scopes: Mapped[list[str] | None] = mapped_column(ARRAY(String(40)))

    idp_entity_id: Mapped[str | None] = mapped_column(String(255))
    idp_sso_url: Mapped[str | None] = mapped_column(String(500))
    idp_x509_cert: Mapped[str | None] = mapped_column(Text)

    jit_provisioning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_role_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(60)), nullable=False, default=list
    )
    attribute_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enforce_sso: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "protocol", "issuer", name="uq_org_idp_configs_org_protocol_issuer"
        ),
    )


class SigningKey(Base):
    """A published signing key.

    Two are live at all times: rotation publishes the new one, waits a full refresh
    lifetime so nothing in flight is orphaned, then switches signing to it. The private
    half is never in this table — it comes from the secret store.
    """

    __tablename__ = "signing_keys"

    kid: Mapped[str] = mapped_column(String(64), primary_key=True)
    algorithm: Mapped[str] = mapped_column(String(16), nullable=False, default="EdDSA")
    public_jwk: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Still accepted for verification, no longer used for signing.
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------- operator staff


class StaffAccount(Base):
    """An Atmora operator — vendor staff, not a tenant user.

    This is a **separate registry from the tenant user directory** (account-service) and
    from `Credential` above. Operators are the *vendor* looking across its tenants
    (ADR-0003 §2); they belong to no organization and hold no membership, so folding them
    into the tenant `users`/`credentials` tables — as a flag or an "operator org" — is
    exactly how a staff identity leaks into a tenant token or a tenant reaches across
    customers. Keeping the table separate is what makes an operator's audience (`operator`)
    and its `stf` claim a property of *which table authenticated it*, not of a role bit that
    a tenant path could ever set.

    **Platform/global, deliberately unpolicied.** The `auth` schema carries no row-level
    security (there is no tenant to bind before a token exists), and even under RLS this
    would sit in the UNPOLICIED set: it is vendor data, keyed by no `organization_id`, the
    same category ADR-0002 §5 reserves for platform vocabulary that must never get an org
    policy.

    Password hashing reuses the *same* Argon2id path tenant credentials use
    (`security.hash_password`/`verify_password`) — operators get no second, weaker way in.
    """

    __tablename__ = "staff_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: Case-insensitive and unique — the operator's login handle.
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    #: Always set: there is no operator SSO path, so unlike `Credential.password_hash`
    #: this is not nullable.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)

    #: The operator's roles, e.g. {"owner", "billing_ops", "support"}. These become the
    #: token's `roles` claim and are what the operator console authorizes against — they are
    #: NOT tenant permission grants and never cross into the `pv`/grant machinery.
    operator_roles: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), nullable=False, default=list
    )
    #: 'active' | 'disabled'. A disabled operator is refused at login the way a deactivated
    #: tenant user is — access should not outlive the decision to remove it.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    #: Guessing is rate-limited exactly as the tenant path rate-limits it.
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def is_active(self) -> bool:
        return self.status == "active"


# --------------------------------------------------------------------- platform tables


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""
