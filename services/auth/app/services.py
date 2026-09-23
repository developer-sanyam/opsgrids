"""Authentication.

The flow this module exists to get right:

    password verified
        → no organization yet
        → MFA, if the account or its organization demands it
        → an organization chosen, or offered
        → tokens issued for that organization

**Login never assumes an organization.** The system this platform replaces passed a
configured constant into its lookup, which meant a second customer literally could not
sign in. Here the organization is resolved from an explicit slug, the host the gateway
saw, or the person's single membership — and when there is genuine ambiguity the answer is
an org picker, never a guess.

**Who commits is decided by whether a principal exists.** The pre-organization flows —
login, organization selection, the second factor, refresh, logout, password reset — run
on a bare session and commit for themselves, because some of them must persist
bookkeeping (a failed attempt, a consumed token) even when they go on to raise. Anything
a route with a principal calls — switching organization, changing a password, enrolling
a factor — commits nothing: the route's tenant session (`get_tenant_session`) opened the
transaction bound to the caller's organization, and it is the one that ends it
(ADR-0002 §1).
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import oidc, security
from app import tokens as tk
from app.config import Settings
from app.directory import Directory, DirectoryMembership, DirectoryUser
from app.entitlements import Entitlements
from app.keys import KeyPair
from app.models import (
    AuditLog,
    Credential,
    ExternalIdentity,
    MfaFactor,
    MfaRecoveryCode,
    OrgIdpConfig,
    OutboxEvent,
    PasswordResetToken,
    RefreshToken,
    Session,
    StaffAccount,
    StaffEnrollmentCode,
)
from platform_core import pii
from platform_core.audit import record_audit
from platform_core.db import set_tenant
from platform_core.errors import (
    BusinessRuleProblem,
    ForbiddenProblem,
    NotFoundProblem,
    UnauthorizedProblem,
    UpstreamUnavailableProblem,
)
from platform_core.events import publish_event
from platform_core.logging import get_logger

log = get_logger("auth")

EVT_SIGNED_IN = "core.session.signed_in"
EVT_SIGNED_OUT = "core.session.signed_out"
EVT_PASSWORD_CHANGED = "core.user.password_changed"  # pragma: allowlist secret
EVT_PASSWORD_RESET_REQUESTED = "core.user.password_reset_requested"  # pragma: allowlist secret
EVT_TOKEN_REUSE = "core.session.token_reuse_detected"
EVT_MFA_ENROLLED = "core.user.mfa_enrolled"

#: One message for every credential failure. Distinguishing "no such account" from "wrong
#: password" turns the login form into an account-enumeration oracle.
INVALID = "Those credentials are not valid"


@dataclass(frozen=True, slots=True)
class Issued:
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime
    session_id: uuid.UUID
    organization_id: uuid.UUID
    membership_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    """What happened, and what the client must do next.

    `status` is one of `complete`, `mfa_required`, `org_selection_required`. The last two
    carry a pre-auth token and nothing usable.
    """

    status: str
    issued: Issued | None = None
    pre_auth_token: str | None = None
    organizations: list[DirectoryMembership] | None = None


# --------------------------------------------------------------------- credentials


async def get_or_create_credential(session: AsyncSession, user_id: uuid.UUID) -> Credential:
    credential = await session.get(Credential, user_id)
    if credential is None:
        credential = Credential(user_id=user_id)
        session.add(credential)
        await session.flush()
    return credential


async def set_password(
    session: AsyncSession,
    user_id: uuid.UUID,
    raw: str,
    *,
    cfg: Settings,
    must_change: bool = False,
) -> Credential:
    security.validate_password(raw, min_length=cfg.password_min_length)
    credential = await get_or_create_credential(session, user_id)
    credential.password_hash = security.hash_password(raw)
    credential.password_changed_at = datetime.now(UTC)
    credential.must_change_password = must_change
    credential.failed_attempts = 0
    credential.locked_until = None
    return credential


# --------------------------------------------------------------------- sign-in


async def _verify_credentials(
    session: AsyncSession, cfg: Settings, directory: Directory, email: str, password: str
) -> tuple[DirectoryUser, Credential]:
    """Check an email and password, or raise the same error whichever part was wrong."""
    invalid = UnauthorizedProblem(INVALID)

    user = await directory.find_by_email(email)
    if user is None:
        # Still spend the time hashing, so a missing account is not detectable by how
        # quickly the answer comes back.
        security.verify_password(None, password)
        raise invalid

    credential = await get_or_create_credential(session, user.user_id)

    # Lockout is checked *before* the password, so hammering a locked account cannot be
    # used to test candidate passwords.
    now = datetime.now(UTC)
    if credential.locked_until and credential.locked_until > now:
        raise ForbiddenProblem(
            "This account is temporarily locked after several failed attempts. "
            "Try again shortly, or reset the password."
        )

    if not user.is_active:
        raise ForbiddenProblem("This account has been deactivated")

    if not security.verify_password(credential.password_hash, password):
        credential.failed_attempts += 1
        if credential.failed_attempts >= cfg.max_failed_attempts:
            credential.locked_until = now + timedelta(minutes=cfg.lockout_minutes)
            log.warning("auth.account_locked", user_id=str(user.user_id))
        # Committed *before* raising. Staging it in the caller's transaction meant the
        # rollback that accompanies a failed sign-in also discarded the attempt — so the
        # counter never grew and the lockout never fired. Rate limiting has to outlive
        # the request it is rate limiting.
        await session.commit()
        raise invalid

    # Raising the cost factor propagates as people sign in — the only moment the
    # plaintext is available.
    if credential.password_hash and security.needs_rehash(credential.password_hash):
        credential.password_hash = security.hash_password(password)

    credential.failed_attempts = 0
    credential.locked_until = None
    credential.last_login_at = now
    return user, credential


def resolve_organization(
    user: DirectoryUser, *, org_hint: str | None
) -> DirectoryMembership | None:
    """Which organization is this sign-in for?

    In order: an explicit hint (a slug from the request or the host the gateway saw), then
    a single active membership. Anything else is genuine ambiguity, and the caller offers
    a picker.

    Email domain is **not** consulted. It may select an identity *provider*, and only from
    a verified domain — inferring the organization from an address the user typed is how
    somebody lands in the wrong tenant.
    """
    active = user.active_memberships()
    if org_hint:
        return user.membership_by_slug(org_hint)
    if len(active) == 1:
        return active[0]
    return None


async def start_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    cfg: Settings,
    amr: list[str],
    idp: str = "local",
    request: Request | None = None,
) -> Session:
    row = Session(
        user_id=user_id,
        amr=amr,
        auth_time=datetime.now(UTC),
        idp=idp,
        expires_at=datetime.now(UTC) + timedelta(days=cfg.session_ttl_days),
        ip=request.client.host if request and request.client else None,
        user_agent=request.headers.get("user-agent") if request else None,
    )
    session.add(row)
    await session.flush()
    return row


async def issue_for(
    session: AsyncSession,
    *,
    pair: KeyPair,
    cfg: Settings,
    auth_session: Session,
    membership: DirectoryMembership,
    entitlements: Entitlements,
    audience: str,
    family: str | None = None,
) -> Issued:
    """Mint an access and refresh pair for one organization.

    Entitlements are resolved **here**, from the membership this function already holds,
    rather than passed in by each caller. There are five paths that issue a token — sign-in,
    organization selection, organization switching, refresh, and one more — and threading a
    list through all five is five chances for one of them to pass an empty list and silently
    strip a customer's products. Resolving once, where the organization is already known,
    makes that impossible rather than merely unlikely.
    """
    granted = await entitlements.for_organization(membership.organization_id)
    auth_session.active_org_id = membership.organization_id

    access = tk.mint_access(
        pair,
        issuer=cfg.jwt_issuer,
        user_id=auth_session.user_id,
        organization_id=membership.organization_id,
        membership_id=membership.membership_id,
        audience=audience,
        session_id=auth_session.id,
        entitlements=granted,
        roles=membership.roles,
        locations=membership.locations,
        amr=_dedupe_amr(auth_session.amr),
        permission_version=membership.permission_version,
        subject_ref=membership.employee_id,
        ttl_seconds=cfg.access_token_ttl_seconds,
    )
    refresh = tk.mint_refresh(
        pair,
        issuer=cfg.jwt_issuer,
        user_id=auth_session.user_id,
        family=family or secrets.token_urlsafe(24),
        session_id=auth_session.id,
        organization_id=membership.organization_id,
        ttl_seconds=cfg.refresh_token_ttl_seconds,
    )
    session.add(
        RefreshToken(
            jti=refresh.jti,
            family=family or _family_of(refresh),
            session_id=auth_session.id,
            user_id=auth_session.user_id,
            organization_id=membership.organization_id,
            expires_at=refresh.expires_at,
        )
    )
    return Issued(
        access_token=access.token,
        expires_in=access.expires_in,
        refresh_token=refresh.token,
        refresh_expires_at=refresh.expires_at,
        session_id=auth_session.id,
        organization_id=membership.organization_id,
        membership_id=membership.membership_id,
    )


def _family_of(minted: tk.MintedToken) -> str:
    """The family a freshly minted refresh token belongs to."""
    import jwt as _jwt

    claims = _jwt.decode(minted.token, options={"verify_signature": False})
    return str(claims["fam"])


async def login(
    session: AsyncSession,
    *,
    cfg: Settings,
    pair: KeyPair,
    directory: Directory,
    email: str,
    password: str,
    org_hint: str | None = None,
    audience: str = "app",
    entitlements: Entitlements,
    request: Request | None = None,
) -> LoginOutcome:
    """Sign in. Returns what happened rather than raising for the in-between states."""
    user, credential = await _verify_credentials(session, cfg, directory, email, password)

    active = user.active_memberships()
    if not active:
        # Authenticated, but belongs to nothing. Distinct from a credential failure
        # because the fix is different: somebody has to invite them.
        raise ForbiddenProblem("This account is not a member of any organization")

    auth_session = await start_session(
        session, user_id=user.user_id, cfg=cfg, amr=["pwd"], request=request
    )

    record_audit(
        session,
        AuditLog,
        organization_id=active[0].organization_id,
        actor_id=user.user_id,
        action="auth.password_verified",
        entity="session",
        entity_id=auth_session.id,
        request=request,
    )

    if await mfa_required(session, credential, user.user_id):
        token = tk.mint_pre_auth(
            pair,
            issuer=cfg.jwt_issuer,
            user_id=user.user_id,
            session_id=auth_session.id,
            reason="mfa",
        )
        # The session row has to survive this response — the next request presents the
        # pre-auth token against it.
        await session.commit()
        return LoginOutcome(status="mfa_required", pre_auth_token=token.token)

    membership = resolve_organization(user, org_hint=org_hint)
    if membership is None:
        token = tk.mint_pre_auth(
            pair,
            issuer=cfg.jwt_issuer,
            user_id=user.user_id,
            session_id=auth_session.id,
            reason="org_selection",
        )
        await session.commit()
        return LoginOutcome(
            status="org_selection_required", pre_auth_token=token.token, organizations=active
        )

    issued = await issue_for(
        session,
        pair=pair,
        cfg=cfg,
        auth_session=auth_session,
        membership=membership,
        entitlements=entitlements,
        audience=audience,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=membership.organization_id,
        event_type=EVT_SIGNED_IN,
        aggregate_type="session",
        aggregate_id=auth_session.id,
        payload={
            "user_id": str(user.user_id),
            "session_id": str(auth_session.id),
            "organization_id": str(membership.organization_id),
            "amr": list(auth_session.amr),
        },
    )
    await session.commit()
    return LoginOutcome(status="complete", issued=issued)


async def select_organization(
    session: AsyncSession,
    *,
    cfg: Settings,
    pair: KeyPair,
    directory: Directory,
    pre_auth_token: str,
    organization_id: uuid.UUID,
    audience: str = "app",
    entitlements: Entitlements,
) -> Issued:
    """Finish a sign-in that stopped to ask which organization."""
    claims = _decode_pre_auth(pair, cfg, pre_auth_token, expect_reason="org_selection")
    user_id = uuid.UUID(str(claims["sub"]))
    auth_session = await _live_session(session, uuid.UUID(str(claims["sid"])), user_id)

    user = await directory.get(user_id)
    if user is None:
        raise UnauthorizedProblem(INVALID)
    membership = user.membership_for(organization_id)
    if membership is None:
        # Not "no such organization" — that would confirm which ones exist.
        raise ForbiddenProblem("You are not a member of that organization")

    issued = await issue_for(
        session,
        pair=pair,
        cfg=cfg,
        auth_session=auth_session,
        membership=membership,
        entitlements=entitlements,
        audience=audience,
    )
    await session.commit()
    return issued


async def switch_organization(
    session: AsyncSession,
    *,
    cfg: Settings,
    pair: KeyPair,
    directory: Directory,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    audience: str = "app",
    entitlements: Entitlements,
) -> Issued:
    """Move a live session to another organization.

    The **same session** carries over — one browser sign-in, several organizations, one
    `sid`. That is what makes a single back-channel logout end all of it.
    """
    auth_session = await _live_session(session, session_id, user_id)
    user = await directory.get(user_id)
    if user is None:
        raise UnauthorizedProblem(INVALID)
    membership = user.membership_for(organization_id)
    if membership is None:
        raise ForbiddenProblem("You are not a member of that organization")

    # The old organization's refresh tokens stop working: a token is scoped to the
    # organization it was minted for.
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.session_id == auth_session.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    issued = await issue_for(
        session,
        pair=pair,
        cfg=cfg,
        auth_session=auth_session,
        membership=membership,
        entitlements=entitlements,
        audience=audience,
    )
    return issued


# --------------------------------------------------------------------- refresh


async def rotate_refresh(
    session: AsyncSession,
    *,
    cfg: Settings,
    pair: KeyPair,
    directory: Directory,
    raw_token: str,
    audience: str = "app",
    entitlements: Entitlements,
) -> Issued:
    """Exchange a refresh token for a new pair, detecting theft.

    Presenting a **spent** token means it was captured — the legitimate holder already
    rotated it. The whole family is revoked rather than just this link: the thief gets
    nothing, and the real user is forced to sign in again. An occasional forced re-login
    is a far better outcome than a silently shared session.
    """
    try:
        claims = tk.decode_local(pair, raw_token, issuer=cfg.jwt_issuer, expected_type=tk.REFRESH)
    except Exception as exc:
        raise UnauthorizedProblem("That session has expired. Please sign in again.") from exc

    jti = str(claims["jti"])
    stored = await session.get(RefreshToken, jti)
    if stored is None:
        raise UnauthorizedProblem("That session has expired. Please sign in again.")

    if stored.is_spent:
        await _revoke_family(session, stored.family, reason="token_reuse")
        log.warning(
            "auth.refresh_reuse_detected", family=stored.family, user_id=str(stored.user_id)
        )
        publish_event(
            session,
            OutboxEvent,
            organization_id=stored.organization_id or uuid.UUID(int=0),
            event_type=EVT_TOKEN_REUSE,
            aggregate_type="session",
            aggregate_id=stored.session_id,
            payload={"user_id": str(stored.user_id), "family": stored.family},
        )
        await session.commit()
        raise UnauthorizedProblem(
            "That session has been ended for security reasons. Please sign in again."
        )

    if stored.expires_at <= datetime.now(UTC):
        raise UnauthorizedProblem("That session has expired. Please sign in again.")

    auth_session = await _live_session(session, stored.session_id, stored.user_id)
    user = await directory.get(stored.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedProblem("That session is no longer valid.")

    org_id = stored.organization_id or auth_session.active_org_id
    membership = user.membership_for(org_id) if org_id else None
    if membership is None:
        # They were removed from the organization while signed in. Ending the session is
        # the correct outcome — access should not outlive membership.
        await _revoke_family(session, stored.family, reason="membership_revoked")
        await session.commit()
        raise UnauthorizedProblem("Your access to that organization has ended.")

    stored.rotated_at = datetime.now(UTC)
    auth_session.last_seen_at = datetime.now(UTC)

    issued = await issue_for(
        session,
        pair=pair,
        cfg=cfg,
        auth_session=auth_session,
        membership=membership,
        entitlements=entitlements,
        audience=audience,
        family=stored.family,
    )
    await session.commit()
    return issued


async def logout(session: AsyncSession, *, pair: KeyPair, cfg: Settings, raw_token: str) -> None:
    """End a session. Idempotent: signing out twice is not an error."""
    try:
        claims = tk.decode_local(pair, raw_token, issuer=cfg.jwt_issuer, expected_type=tk.REFRESH)
    except Exception:
        return

    stored = await session.get(RefreshToken, str(claims["jti"]))
    if stored is None:
        return
    await _revoke_family(session, stored.family, reason="logout")
    await session.execute(
        update(Session)
        .where(Session.id == stored.session_id, Session.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoked_reason="logout")
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=stored.organization_id or uuid.UUID(int=0),
        event_type=EVT_SIGNED_OUT,
        aggregate_type="session",
        aggregate_id=stored.session_id,
        payload={"user_id": str(stored.user_id), "session_id": str(stored.session_id)},
    )
    await session.commit()


async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID, *, reason: str) -> None:
    """Kill every session a person has.

    Called when a password changes, an account is deactivated, or a membership is revoked
    — anywhere "they should not still be signed in anywhere" is the answer.
    """
    now = datetime.now(UTC)
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await session.execute(
        update(Session)
        .where(Session.user_id == user_id, Session.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )


async def _revoke_family(session: AsyncSession, family: str, *, reason: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family == family, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def _live_session(
    session: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
) -> Session:
    row = await session.get(Session, session_id)
    if row is None or not row.is_live or row.user_id != user_id:
        raise UnauthorizedProblem("That session is no longer valid.")
    if row.expires_at <= datetime.now(UTC):
        raise UnauthorizedProblem("That session has expired. Please sign in again.")
    return row


async def _consume_pre_auth_session(
    session: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
) -> Session:
    """`_live_session`, plus the check that makes a second-factor pre-auth token single-use.

    A pre-auth token is a signed JWT: nothing about presenting it a second time within its TTL
    makes it invalid on its own (there is no server-side revocation of the token itself), so
    without this a captured or merely-replayed "mfa" or operator-enrolment token stays usable
    for its whole window even after it already produced a completed sign-in. Once a session has
    recorded `mfa_verified_at`, its second factor has already been proven and spent — a further
    presentation of a pre-auth token naming that session is a replay, not a redo, and is refused
    the same way an expired one is. Used everywhere a pre-auth token's *second-factor* step is
    consumed (tenant `verify_mfa`, every operator MFA function); **not** used for the
    organization-selection step, whose token is deliberately minted fresh, after
    `mfa_verified_at`, for the next leg of the same sign-in.
    """
    row = await _live_session(session, session_id, user_id)
    if row.mfa_verified_at is not None:
        raise UnauthorizedProblem("Please start signing in again.")
    return row


def _decode_pre_auth(
    pair: KeyPair, cfg: Settings, token: str, *, expect_reason: str
) -> dict[str, object]:
    try:
        claims = tk.decode_local(pair, token, issuer=cfg.jwt_issuer, expected_type=tk.PRE_AUTH)
    except Exception as exc:
        raise UnauthorizedProblem("Please start signing in again.") from exc
    if claims.get("reason") != expect_reason:
        # A pre-auth token issued for MFA must not be spent on org selection: that would
        # let somebody skip the second factor entirely.
        raise UnauthorizedProblem("Please start signing in again.")
    return claims


# --------------------------------------------------------------------- passwords


async def change_password(
    session: AsyncSession,
    *,
    cfg: Settings,
    user_id: uuid.UUID,
    current: str,
    new: str,
    request: Request | None = None,
) -> None:
    """Change a password, ending every other session.

    If the old password was captured, the sessions it opened are captured too — leaving
    them alive makes the change cosmetic.
    """
    credential = await get_or_create_credential(session, user_id)
    if not security.verify_password(credential.password_hash, current):
        raise UnauthorizedProblem("That is not the current password")
    if current == new:
        raise BusinessRuleProblem("The new password must be different from the old one")

    await set_password(session, user_id, new, cfg=cfg)
    await revoke_all_for_user(session, user_id, reason="password_changed")

    record_audit(
        session,
        AuditLog,
        organization_id=uuid.UUID(int=0),
        actor_id=user_id,
        action="auth.password_changed",
        entity="credential",
        entity_id=user_id,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=uuid.UUID(int=0),
        event_type=EVT_PASSWORD_CHANGED,
        aggregate_type="credential",
        aggregate_id=user_id,
        payload={"user_id": str(user_id)},
    )


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def request_password_reset(
    session: AsyncSession,
    *,
    cfg: Settings,
    directory: Directory,
    email: str,
    request: Request | None = None,
) -> None:
    """Start a reset. **Always succeeds from the caller's point of view.**

    Whether the address exists is not disclosed: a 404 here is an account-enumeration
    oracle that needs no password guessing at all.
    """
    user = await directory.find_by_email(email)
    if user is None or not user.is_active:
        return

    raw = secrets.token_urlsafe(32)
    session.add(
        PasswordResetToken(
            user_id=user.user_id,
            token_hash=_hash_token(raw),
            expires_at=datetime.now(UTC) + timedelta(minutes=cfg.password_reset_ttl_minutes),
            requested_ip=request.client.host if request and request.client else None,
        )
    )
    # The email goes out through notify-service, from the outbox — so a crash between
    # writing the token and sending the mail cannot lose the reset.
    publish_event(
        session,
        OutboxEvent,
        organization_id=uuid.UUID(int=0),
        event_type=EVT_PASSWORD_RESET_REQUESTED,
        aggregate_type="credential",
        aggregate_id=user.user_id,
        payload={
            "user_id": str(user.user_id),
            "email": user.email,
            "token": raw,
            # Ready to paste into a template. The dashboard owns the route; this service owns
            # the token; the platform's base URL is the one configured value that joins them,
            # so notification's template never has to know a hostname (ADR-0013 §3).
            "reset_url": f"{cfg.app_base_url.rstrip('/')}/reset-password?token={raw}",
        },
    )
    await session.commit()


async def confirm_password_reset(
    session: AsyncSession, *, cfg: Settings, raw_token: str, new_password: str
) -> None:
    invalid = NotFoundProblem("That reset link is not valid")
    stmt = select(PasswordResetToken).where(PasswordResetToken.token_hash == _hash_token(raw_token))
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= datetime.now(UTC):
        raise invalid

    await set_password(session, row.user_id, new_password, cfg=cfg)
    row.used_at = datetime.now(UTC)
    await revoke_all_for_user(session, row.user_id, reason="password_reset")
    await session.commit()


# --------------------------------------------------------------------- MFA


async def mfa_required(session: AsyncSession, credential: Credential, user_id: uuid.UUID) -> bool:
    """True when this person must present a second factor.

    Only counts **confirmed** factors: a half-finished enrolment must not lock somebody
    out of their own account.
    """
    if not credential.mfa_enforced:
        return False
    stmt = select(MfaFactor).where(MfaFactor.user_id == user_id, MfaFactor.confirmed_at.isnot(None))
    return (await session.execute(stmt)).first() is not None


def _factor_aad(user_id: uuid.UUID) -> bytes:
    """MINOR 8 of the 2026-09-24 review: bind a sealed TOTP secret to the account it belongs
    to, so a copied ciphertext (a bad migration, a restored backup pointed at the wrong row)
    cannot be opened under a different user's identity even with the right key."""
    return f"mfa_factor:{user_id}".encode()


async def begin_mfa_enrolment(
    session: AsyncSession, user_id: uuid.UUID, *, account_email: str, cfg: Settings
) -> tuple[MfaFactor, str, str]:
    """Create an unconfirmed factor and hand back the secret and its QR URI.

    The secret is sealed with the platform PII envelope before it is ever staged — the same
    `platform_core.pii.seal` this schema already uses for `org_idp_configs.client_secret_enc`.
    A TOTP secret is a durable credential, not a session artefact: whoever reads it can produce
    a valid code forever, so it gets exactly the treatment a password hash or an OIDC client
    secret gets, not the plain bytes an earlier version of this function stored.
    """
    secret = security.generate_totp_secret()
    factor = MfaFactor(user_id=user_id, type="totp", label="Authenticator app")
    factor.secret_enc, _mask = pii.seal(
        secret, kind="generic", settings=cfg, aad=_factor_aad(user_id)
    )
    session.add(factor)
    # Flushed, not committed: the caller returns the factor's id, and the transaction
    # belongs to the route that opened it.
    await session.flush()
    return factor, secret, security.totp_uri(secret, account=account_email)


#: A pre-sealing factor row: the raw base32 secret stored as-is, before `platform_core.pii`
#: wrapped every TOTP secret in an envelope. Recognised by shape, not guessed at random — a
#: `pii.seal` envelope leads with a version byte outside this charset, so it never matches.
_LEGACY_TOTP_SECRET_RE = re.compile(rb"^[A-Z2-7]{16,32}$")


def _legacy_totp_secret(raw: bytes) -> str | None:
    if not _LEGACY_TOTP_SECRET_RE.match(raw):
        return None
    return raw.decode("ascii")


async def _open_factor_secret(
    session: AsyncSession,
    factor: MfaFactor,
    *,
    cfg: Settings,
    request: Request | None = None,
) -> str | None:
    """The TOTP secret behind a sealed factor, or `None` for a factor with none or one this
    call cannot (or, by policy, will not) make sense of.

    MAJOR 3 of the 2026-09-24 security review, tightened in the third round: a tolerant read
    with three tiers, each resealed in place with the per-row AAD (MINOR 8) and audited as
    `auth.mfa_factor_resealed`, so the next read takes the fast path:

    1. **Sealed with the row's AAD** — the normal case; returned as is.
    2. **Sealed with no AAD** (m5) — an envelope written before the AAD was introduced. It is
       authentic (GCM verified it under a key we hold), so it is accepted unconditionally and
       resealed with the AAD.
    3. **Raw base32 plaintext** (m4) — a row from before PII sealing existed. Unlike the other
       two, nothing authenticates it: anyone able to write `mfa_factors.secret_enc` could pick a
       factor's secret. So it is honoured only while `cfg.mfa_accept_legacy_plaintext` is set —
       a box turns that on deliberately, until `bin/reseal-mfa-factors` prints 0, then removes it.
       Otherwise it is refused and logged, like any other unreadable row.

    `pii.PiiError` is caught here, not propagated: one unopenable or malformed factor must skip
    *that* factor, never crash the sign-in outright and never skip the recovery-code fallback
    the caller still tries afterwards.
    """
    if factor.secret_enc is None:
        return None
    aad = _factor_aad(factor.user_id)
    try:
        return pii.open_sealed(factor.secret_enc, settings=cfg, aad=aad)
    except pii.PiiError:
        pass

    secret: str | None = None
    try:
        secret = pii.open_sealed(factor.secret_enc, settings=cfg)
    except pii.PiiError:
        if cfg.mfa_accept_legacy_plaintext:
            secret = _legacy_totp_secret(factor.secret_enc)
        elif _legacy_totp_secret(factor.secret_enc) is not None:
            log.error("auth.mfa_factor_plaintext_refused", factor_id=str(factor.id))
            return None
    if secret is None:
        log.error("auth.mfa_factor_unreadable", factor_id=str(factor.id))
        return None

    factor.secret_enc, _mask = pii.seal(secret, kind="generic", settings=cfg, aad=aad)
    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=factor.user_id,
        action="auth.mfa_factor_resealed",
        entity="mfa_factor",
        entity_id=factor.id,
        request=request,
    )
    return secret


async def reseal_legacy_mfa_factors(session: AsyncSession, *, cfg: Settings) -> int:
    """The one-off deploy step behind `bin/reseal-mfa-factors` (MAJOR 3): force every factor
    with a stored secret through `_open_factor_secret`'s tolerant read, so a row this
    deployment happens to hold from before PII sealing existed is resealed now rather than
    waiting for its account's next sign-in. Idempotent — a row that is already sealed just
    opens and reseals to an equivalent envelope — and safe to run against a database with none:
    it counts only what it actually touched.
    """
    ids = (
        (await session.execute(select(MfaFactor.id).where(MfaFactor.secret_enc.isnot(None))))
        .scalars()
        .all()
    )
    resealed = 0
    for factor_id in ids:
        factor = await session.get(MfaFactor, factor_id)
        if factor is None:
            continue
        before = factor.secret_enc
        secret = await _open_factor_secret(session, factor, cfg=cfg)
        if secret is not None and factor.secret_enc != before:
            resealed += 1
    return resealed


async def confirm_mfa_enrolment(
    session: AsyncSession, *, factor_id: uuid.UUID, user_id: uuid.UUID, code: str, cfg: Settings
) -> list[str]:
    """Prove the factor works, turn it on, and issue recovery codes.

    Requiring a working code before enabling is what stops somebody locking themselves out
    with a mis-scanned QR.
    """
    factor = await session.get(MfaFactor, factor_id)
    secret = await _open_factor_secret(session, factor, cfg=cfg) if factor is not None else None
    if factor is None or factor.user_id != user_id or secret is None:
        raise NotFoundProblem("No such enrolment")
    if factor.confirmed_at is not None:
        # Re-confirming an already-confirmed factor is not a redo of a prior success; it is
        # either a replay or a race, and either way there is nothing left for this call to
        # legitimately do — the factor already works and recovery codes already exist for it.
        raise UnauthorizedProblem("This factor is already confirmed.")

    step = security.verify_totp(secret, code, last_used_step=factor.last_used_step)
    if step is None:
        raise UnauthorizedProblem("That code is not valid. Check your device's clock.")

    factor.confirmed_at = datetime.now(UTC)
    factor.last_used_step = step
    factor.last_used_at = datetime.now(UTC)

    credential = await get_or_create_credential(session, user_id)
    credential.mfa_enforced = True

    # Replace any previous codes: leaving old ones live means a set the user believes is
    # retired still opens the account.
    await session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    codes = security.generate_recovery_codes()
    for code_value in codes:
        session.add(
            MfaRecoveryCode(user_id=user_id, code_hash=security.hash_recovery_code(code_value))
        )

    publish_event(
        session,
        OutboxEvent,
        organization_id=uuid.UUID(int=0),
        event_type=EVT_MFA_ENROLLED,
        aggregate_type="credential",
        aggregate_id=user_id,
        payload={"user_id": str(user_id)},
    )
    return codes


async def verify_mfa(
    session: AsyncSession,
    *,
    cfg: Settings,
    pair: KeyPair,
    directory: Directory,
    pre_auth_token: str,
    code: str,
    org_hint: str | None = None,
    audience: str = "app",
    entitlements: Entitlements,
) -> LoginOutcome:
    """Second step of sign-in: check the factor, then continue where login left off.

    BLOCKER 1 of the 2026-09-24 security review, "also": this had no failure counter at all —
    an unlimited number of TOTP or recovery-code guesses against a pre-auth session that had
    already survived the password step. `Credential.mfa_failed_attempts`/`mfa_locked_until`
    (the same fields the operator path uses, added for both in one migration) close that; a
    lockout here also revokes the session and its refresh tokens, matching what
    `_register_operator_mfa_failure` does on the operator side.
    """
    claims = _decode_pre_auth(pair, cfg, pre_auth_token, expect_reason="mfa")
    user_id = uuid.UUID(str(claims["sub"]))
    auth_session = await _consume_pre_auth_session(session, uuid.UUID(str(claims["sid"])), user_id)

    # `with_for_update`: serialises concurrent guesses against the same credential row so the
    # failure counter and the lockout it trips cannot lose an update to a race (MAJOR 4).
    credential = await session.get(Credential, user_id, with_for_update=True)
    if credential is None:
        credential = await get_or_create_credential(session, user_id)

    now = datetime.now(UTC)
    if credential.mfa_locked_until and credential.mfa_locked_until > now:
        raise ForbiddenProblem(
            "This account is temporarily locked after several failed authentication "
            "attempts. Try again shortly."
        )

    if await _accept_second_factor(session, user_id, code, cfg=cfg) is None:
        credential.mfa_failed_attempts += 1
        if credential.mfa_failed_attempts >= cfg.max_failed_attempts:
            credential.mfa_locked_until = now + timedelta(minutes=cfg.lockout_minutes)
            # m7: restart the count with the lock, so expiry is a real unlock (see the
            # operator-side `_register_operator_mfa_failure`). For a tenant member the lock
            # expiring after `lockout_minutes` *is* the unlock — there is deliberately no
            # org-admin "clear this member's MFA lock" endpoint: it would need a new
            # permission and a cross-service membership check to guard a fifteen-minute wait.
            credential.mfa_failed_attempts = 0
            auth_session.revoked_at = now
            auth_session.revoked_reason = "mfa_failed_attempts"
            await _revoke_session_refresh_tokens(session, auth_session.id)
            log.warning("auth.account_locked", user_id=str(user_id), reason="mfa_failed_attempts")
        await session.commit()
        raise UnauthorizedProblem("That code is not valid")

    credential.mfa_failed_attempts = 0
    credential.mfa_locked_until = None

    if not await _claim_mfa_verified(session, auth_session):
        raise UnauthorizedProblem("Please start signing in again.")
    auth_session.amr = _dedupe_amr([*auth_session.amr, "otp"])

    user = await directory.get(user_id)
    if user is None:
        raise UnauthorizedProblem(INVALID)

    membership = resolve_organization(user, org_hint=org_hint)
    if membership is None:
        token = tk.mint_pre_auth(
            pair,
            issuer=cfg.jwt_issuer,
            user_id=user_id,
            session_id=auth_session.id,
            reason="org_selection",
        )
        await session.commit()
        return LoginOutcome(
            status="org_selection_required",
            pre_auth_token=token.token,
            organizations=user.active_memberships(),
        )

    issued = await issue_for(
        session,
        pair=pair,
        cfg=cfg,
        auth_session=auth_session,
        membership=membership,
        entitlements=entitlements,
        audience=audience,
    )
    await session.commit()
    return LoginOutcome(status="complete", issued=issued)


async def _accept_second_factor(
    session: AsyncSession, user_id: uuid.UUID, code: str, *, cfg: Settings
) -> str | None:
    """A TOTP code, or a single-use recovery code. Returns which kind matched, or `None`.

    The discriminator — `"totp"` vs `"recovery"` — is not cosmetic: a recovery-code sign-in is
    its own audited action (MINOR 11), because presenting the break-glass code is a materially
    different event from an ordinary authenticator check and an operator reading the audit trail
    needs to be able to tell them apart without decoding anything.
    """
    stmt = select(MfaFactor).where(MfaFactor.user_id == user_id, MfaFactor.confirmed_at.isnot(None))
    for factor in (await session.execute(stmt)).scalars().all():
        secret = await _open_factor_secret(session, factor, cfg=cfg)
        if secret is None:
            continue
        step = security.verify_totp(secret, code, last_used_step=factor.last_used_step)
        if step is not None:
            # Remember the step so the same code cannot be replayed inside its window.
            factor.last_used_step = step
            factor.last_used_at = datetime.now(UTC)
            return "totp"

    recovery_stmt = select(MfaRecoveryCode).where(
        MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None)
    )
    for recovery in (await session.execute(recovery_stmt)).scalars().all():
        if security.verify_recovery_code(recovery.code_hash, code):
            recovery.used_at = datetime.now(UTC)
            return "recovery"
    return None


# --------------------------------------------------------------------- operator staff
#
# A wholly separate identity from everything above. The tenant flows authenticate a
# `DirectoryUser` from account-service and mint a token for one of that person's
# organizations. Operators are OpsGrids staff (ADR-0003 §2): they live in this service's own
# `staff_accounts` table, belong to no organization, and are minted a token on the
# `operator` audience carrying `stf=true`. The two never cross — a tenant email is not in
# `staff_accounts`, and a staff account is not in the directory.
#
# **MFA is mandatory for staff, not opt-in.** A tenant's `Credential.mfa_enforced` is a flag
# somebody turns on; there is no equivalent flag here — every staff account requires a second
# factor, unconditionally, because the account that can suspend a subscription or read across
# every tenant does not get a grace period (ADR-0003 addendum, 2026-09-23). `operator_login`
# therefore never returns a usable token from a password alone: it returns an
# `OperatorLoginOutcome` naming whichever of the two next steps applies — `mfa_required` for a
# staff account with an already-confirmed factor, `mfa_enrollment_required` for one with none.
# The enrolment and verification factor tables (`MfaFactor`, `MfaRecoveryCode`) are the exact
# ones the tenant path uses — a factor row is keyed by `user_id` alone and carries nothing
# tenant-specific — so `begin_mfa_enrolment` is reused verbatim; only *confirming* an
# operator's enrolment differs, because it must not create a `Credential` row (operators have
# none) and it completes the sign-in immediately rather than asking the caller to present the
# code again.

EVT_OPERATOR_SIGNED_IN = "core.operator.signed_in"
EVT_OPERATOR_MFA_ENROLLED = "core.operator.mfa_enrolled"

#: Pre-auth token reasons unique to the operator path. Deliberately not the tenant `"mfa"` /
#: `"org_selection"` strings: `_decode_pre_auth`'s `expect_reason` check is what stops a token
#: minted for one flow being spent on the other, and a shared reason string would rely on the
#: `sub` namespace never colliding instead of the token itself refusing to cross.
OPERATOR_MFA_REASON = "operator_mfa"
OPERATOR_MFA_ENROLLMENT_REASON = "operator_mfa_enrollment"

#: The audience the operator gateway accepts. A product/app token names its product here and
#: is rejected at that gateway's signature-and-audience check before any route is consulted.
OPERATOR_AUDIENCE = "operator"

#: Operators belong to no organization. The gateway structurally requires a non-empty
#: `act_org` on every access token (it is how a tenant token names its tenant), so an
#: operator token carries the platform sentinel org — the same `uuid.UUID(int=0)` this
#: service already stamps on platform-scoped audit and outbox rows. It is not a tenant: the
#: console's cross-tenant reads run on the directory bypass role, never on this claim.
PLATFORM_ORG = uuid.UUID(int=0)


@dataclass(frozen=True, slots=True)
class OperatorLoginOutcome:
    """What happened, and what the client must do next.

    `status` is one of `mfa_required`, `mfa_enrollment_required`, `complete`. The first two
    carry a pre-auth token and nothing usable; `operator_login` itself never produces
    `complete` — see the module note above — but `operator_verify_mfa` and
    `confirm_operator_mfa_enrolment` do, and share this shape so the route's response model
    is one union rather than three near-identical ones.
    """

    status: str
    issued: OperatorIssued | None = None
    pre_auth_token: str | None = None


@dataclass(frozen=True, slots=True)
class OperatorIssued:
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime
    session_id: uuid.UUID
    staff_id: uuid.UUID
    operator_roles: list[str]


async def _verify_staff(
    session: AsyncSession, cfg: Settings, email: str, password: str
) -> StaffAccount:
    """Check an operator's email and password, or raise the same generic error.

    MINOR 11 of the 2026-09-24 security review: **one** message and **one** status for every
    failure — unknown email, wrong password, locked, disabled — so none of them is an
    enumeration oracle. Distinct messages ("temporarily locked", "has been disabled") used to
    tell an anonymous caller which of those was true; now every path raises the identical
    `INVALID` 401. Two things follow from that:

    * The real password check runs even for a locked or disabled account, so the response
      time does not itself reveal "this account exists and is in a special state" by skipping
      the Argon2 comparison the way an early return would.
    * `is_active` is checked **after** the password, not before — a disabled account still
      proves its own password wrong or right at the same cost as an active one, and only then
      is refused, rather than short-circuiting on a cheaper check first.

    Row-locked (`with_for_update`) for the duration of this transaction (MAJOR 4): two
    concurrent login attempts against the same account serialise here, so the failed-attempt
    counter and the lockout it trips cannot lose an update to a race.
    """
    invalid = UnauthorizedProblem(INVALID)

    staff = (
        await session.execute(
            select(StaffAccount).where(StaffAccount.email == email).with_for_update()
        )
    ).scalar_one_or_none()
    if staff is None:
        # Burn a hash so a missing operator is not detectable by response time.
        security.verify_password(None, password)
        raise invalid

    now = datetime.now(UTC)
    locked = bool(staff.locked_until and staff.locked_until > now)

    password_ok = security.verify_password(staff.password_hash, password)

    if locked:
        # Already locked: nothing to count, and no distinct answer to give — the same
        # generic invalid as a wrong password, whatever `password_ok` came out to.
        raise invalid

    if not password_ok:
        staff.failed_attempts += 1
        if staff.failed_attempts >= cfg.max_failed_attempts:
            staff.locked_until = now + timedelta(minutes=cfg.lockout_minutes)
            log.warning("auth.operator_locked", staff_id=str(staff.id), reason="password")
        await session.commit()
        raise invalid

    # Password correct. Only now does a disabled account get refused — after paying the
    # same Argon2 cost every other path pays, not before it.
    if not staff.is_active:
        raise invalid

    if security.needs_rehash(staff.password_hash):
        staff.password_hash = security.hash_password(password)
    staff.failed_attempts = 0
    staff.locked_until = None
    staff.last_login_at = now
    return staff


def _issue_operator(
    session: AsyncSession,
    *,
    pair: KeyPair,
    cfg: Settings,
    auth_session: Session,
    staff: StaffAccount,
    family: str | None = None,
) -> OperatorIssued:
    """Mint an operator access+refresh pair. The single place `stf=true` is minted.

    Same signing key, same JWKS, same verify path as every tenant token — it differs only
    in `aud="operator"`, the `stf` flag, and carrying the staff account's operator-roles
    instead of a membership's grants. No `mem`, so the gateway resolves no tenant
    permissions for it.
    """
    auth_session.active_org_id = None
    roles = list(staff.operator_roles)

    access = tk.mint_access(
        pair,
        issuer=cfg.jwt_issuer,
        user_id=staff.id,
        organization_id=PLATFORM_ORG,
        membership_id=None,
        audience=OPERATOR_AUDIENCE,
        session_id=auth_session.id,
        entitlements=[],
        roles=roles,
        locations=[],
        amr=_dedupe_amr(auth_session.amr),
        permission_version=0,
        subject_ref=None,
        ttl_seconds=cfg.access_token_ttl_seconds,
        staff=True,
    )
    refresh = tk.mint_refresh(
        pair,
        issuer=cfg.jwt_issuer,
        user_id=staff.id,
        family=family or secrets.token_urlsafe(24),
        session_id=auth_session.id,
        organization_id=None,
        ttl_seconds=cfg.refresh_token_ttl_seconds,
    )
    session.add(
        RefreshToken(
            jti=refresh.jti,
            family=family or _family_of(refresh),
            session_id=auth_session.id,
            user_id=staff.id,
            organization_id=None,
            expires_at=refresh.expires_at,
        )
    )
    return OperatorIssued(
        access_token=access.token,
        expires_in=access.expires_in,
        refresh_token=refresh.token,
        refresh_expires_at=refresh.expires_at,
        session_id=auth_session.id,
        staff_id=staff.id,
        operator_roles=roles,
    )


async def _operator_has_confirmed_factor(session: AsyncSession, staff_id: uuid.UUID) -> bool:
    """True once this staff account has proven it can generate a code from a factor.

    No `mfa_enforced` flag to consult — see the module note: MFA is unconditional for every
    staff account, so the only question is whether enrolment has finished yet. This is also
    the guard against re-enrolment: once true, neither `operator_begin_mfa_enrolment` nor
    `confirm_operator_mfa_enrolment` will proceed, regardless of what pre-auth token or
    enrolment code is presented — enrolling a *second*, uninvited factor once a real one
    exists is exactly the attack this closes (the security review's R1/R2).
    """
    stmt = select(MfaFactor).where(
        MfaFactor.user_id == staff_id, MfaFactor.confirmed_at.isnot(None)
    )
    return (await session.execute(stmt)).first() is not None


#: How many never-confirmed factor rows one staff account may accumulate before the oldest are
#: pruned. Enrolment can legitimately be started more than once — a lost phone, a login racing
#: an older one that never finished — and each attempt creates a fresh unconfirmed row that
#: nothing ever cleaned up before this; a cap bounds that growth without discarding an attempt
#: that is still plausibly in flight (MINOR 12 of the security review).
MAX_UNCONFIRMED_FACTORS_PER_STAFF = 3


async def _prune_unconfirmed_factors(session: AsyncSession, staff_id: uuid.UUID) -> None:
    """Delete the oldest unconfirmed factors once the account is at the cap, making room for
    the one `operator_begin_mfa_enrolment` is about to create."""
    stale_ids = (
        (
            await session.execute(
                select(MfaFactor.id)
                .where(MfaFactor.user_id == staff_id, MfaFactor.confirmed_at.is_(None))
                .order_by(MfaFactor.created_at.desc())
                .offset(MAX_UNCONFIRMED_FACTORS_PER_STAFF - 1)
            )
        )
        .scalars()
        .all()
    )
    if stale_ids:
        await session.execute(delete(MfaFactor).where(MfaFactor.id.in_(stale_ids)))


#: How long an out-of-band enrolment code stays redeemable. Short on purpose: it exists to
#: prove the person enrolling is who they say within a narrow window right after it is handed
#: to them, not to be a second long-lived credential sitting around.
ENROLLMENT_CODE_TTL_MINUTES = 30

EVT_OPERATOR_ENROLLMENT_CODE_ISSUED = "core.operator.enrollment_code_issued"


async def issue_staff_enrollment_code(
    session: AsyncSession,
    *,
    staff_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    request: Request | None = None,
) -> str:
    """Issue (or replace) the one redeemable enrolment code for a staff account — the
    out-of-band channel `operator_begin_mfa_enrolment` requires before a password is enough
    to reach enrolment at all.

    `actor_id` is `None` only from `bootstrap.py`'s dev/deploy seeding, which has no operator
    caller to attribute the issuance to; every other call is an `owner` acting through the
    audited route, and is recorded as such. One row per staff account (`StaffEnrollmentCode`
    is keyed by `staff_id`): issuing a new code invalidates whatever the previous one was,
    rather than letting two live at once.

    Returns the raw code — shown once, exactly like a recovery code, and never retrievable
    again; only its hash is stored.
    """
    staff = await session.get(StaffAccount, staff_id)
    if staff is None:
        raise NotFoundProblem("No such staff account")

    code = security.generate_recovery_codes(count=1)[0]
    now = datetime.now(UTC)
    row = await session.get(StaffEnrollmentCode, staff_id)
    if row is None:
        row = StaffEnrollmentCode(staff_id=staff_id)
        session.add(row)
    row.code_hash = security.hash_recovery_code(code)
    row.expires_at = now + timedelta(minutes=ENROLLMENT_CODE_TTL_MINUTES)
    row.used_at = None
    row.issued_by = actor_id

    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=actor_id,
        action="auth.operator_enrollment_code_issued",
        entity="staff_account",
        entity_id=staff_id,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=PLATFORM_ORG,
        event_type=EVT_OPERATOR_ENROLLMENT_CODE_ISSUED,
        aggregate_type="staff_account",
        aggregate_id=staff_id,
        payload={"staff_id": str(staff_id)},
    )
    await session.flush()
    return code


EVT_OPERATOR_MFA_RESET = "core.operator.mfa_reset"


async def reset_staff_mfa(
    session: AsyncSession,
    *,
    target_staff_id: uuid.UUID,
    actor_id: uuid.UUID,
    request: Request | None = None,
) -> None:
    """The lost-device path (MINOR 5 of the 2026-09-24 security review): an owner wipes a
    colleague's MFA outright when they can no longer produce a code or a recovery code —
    deletes every factor and recovery code, revokes every live session and refresh token, and
    audits the act. Only after this has run is `issue_staff_enrollment_code` meaningful again
    for this account: with a confirmed factor still in place, enrolment would simply refuse
    (`_operator_has_confirmed_factor`), exactly as it should for an account that is not
    actually locked out.

    Refuses to reset the caller's own MFA — the one case this route exists for is a *colleague*
    who cannot get in, not a way to bypass one's own second factor. An owner who has locked
    themself out is a `bin/reset-staff-mfa` (local/test/ci only) or an incident, not a self-service
    button in the product.
    """
    if target_staff_id == actor_id:
        raise BusinessRuleProblem("You cannot reset your own MFA. Ask another owner.")

    staff = await session.get(StaffAccount, target_staff_id, with_for_update=True)
    if staff is None:
        raise NotFoundProblem("No such staff account")

    await session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == target_staff_id))
    await session.execute(delete(MfaFactor).where(MfaFactor.user_id == target_staff_id))
    # m7: a colleague locked out of MFA *because* they lost their device is the whole reason
    # this route exists — leaving the lock (or a near-threshold counter) behind would make the
    # reset unusable until it expired on its own.
    staff.mfa_failed_attempts = 0
    staff.mfa_locked_until = None

    live_session_ids = (
        (
            await session.execute(
                select(Session.id).where(
                    Session.user_id == target_staff_id, Session.revoked_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    for session_id in live_session_ids:
        await _revoke_session_refresh_tokens(session, session_id)
    if live_session_ids:
        await session.execute(
            update(Session)
            .where(Session.id.in_(live_session_ids))
            .values(revoked_at=datetime.now(UTC), revoked_reason="operator_mfa_reset")
        )

    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=actor_id,
        action="auth.operator_mfa_reset",
        entity="staff_account",
        entity_id=target_staff_id,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=PLATFORM_ORG,
        event_type=EVT_OPERATOR_MFA_RESET,
        aggregate_type="staff_account",
        aggregate_id=target_staff_id,
        payload={"staff_id": str(target_staff_id), "reset_by": str(actor_id)},
    )
    await session.commit()


#: One message for every enrolment-code failure — missing, expired, already spent, or simply
#: wrong. Distinguishing them would tell a caller which half of their guess was right.
_ENROLLMENT_CODE_INVALID = UnauthorizedProblem("That enrolment code is not valid or has expired.")


async def _redeem_enrollment_code(
    session: AsyncSession,
    staff_id: uuid.UUID,
    code: str,
    *,
    staff: StaffAccount,
    auth_session: Session,
    cfg: Settings,
) -> None:
    """Consume the staff account's enrolment code, or refuse.

    MAJOR 4 of the 2026-09-24 review: claims the row atomically —
    `UPDATE ... WHERE staff_id = :id AND used_at IS NULL AND expires_at > now() RETURNING
    code_hash` — before comparing the hash, not after. That is a real single-attempt trade:
    two concurrent redemptions of the same code cannot both succeed (the second finds no row
    to claim, whatever it typed), but it also means a *typo* burns the code exactly the way a
    correct-then-wrong-on-replay guess would, rather than leaving it usable for a retry. That
    matches how the enrolment code is documented (`issue_staff_enrollment_code`) — a single-use
    proof, not a short password to be attempted a few times.

    MINOR 6: a wrong guess counts toward the same MFA lockout a wrong TOTP or recovery code
    does. This is a raw grind on the account's second-factor gate exactly like the others, and
    letting it run uncounted would make it the softest of the three ways to attack this step.
    """
    claimed_hash = (
        await session.execute(
            update(StaffEnrollmentCode)
            .where(
                StaffEnrollmentCode.staff_id == staff_id,
                StaffEnrollmentCode.used_at.is_(None),
                StaffEnrollmentCode.expires_at > datetime.now(UTC),
            )
            .values(used_at=datetime.now(UTC))
            .returning(StaffEnrollmentCode.code_hash)
        )
    ).scalar_one_or_none()

    if claimed_hash is None or not security.verify_recovery_code(claimed_hash, code):
        await _register_operator_mfa_failure(
            session, staff=staff, auth_session=auth_session, cfg=cfg
        )
        await session.commit()
        raise _ENROLLMENT_CODE_INVALID


def _staff_locked(staff: StaffAccount) -> bool:
    """MFA-side lockout only — separate from the password lockout `_verify_staff` guards
    (BLOCKER 1 of the 2026-09-24 review). A staff account can be blocked from the *second*
    factor step without a correct password ever having tripped or cleared this."""
    return bool(staff.mfa_locked_until and staff.mfa_locked_until > datetime.now(UTC))


async def _claim_mfa_verified(session: AsyncSession, auth_session: Session) -> bool:
    """Atomically mark a session's second factor as proven, or discover someone already did.

    MAJOR 4 of the 2026-09-24 review: the code is still checked correct *first* — that stays a
    plain read, so a typo can be retried — but turning "correct" into "this session is now
    verified" is a single `UPDATE ... WHERE mfa_verified_at IS NULL RETURNING id`, not a Python
    assignment on a row that might have been claimed by a concurrent, equally-correct submission
    of the same code a moment earlier. `synchronize_session=False`: this writes straight to the
    row and the in-memory object is updated by hand below, rather than asking the ORM to guess
    whether its evaluator can reproduce an `IS NULL` predicate. Returns `False` on a lost race,
    so the caller can refuse the second winner rather than mint it a second, redundant token
    pair.
    """
    now = datetime.now(UTC)
    claimed = (
        await session.execute(
            update(Session)
            .where(Session.id == auth_session.id, Session.mfa_verified_at.is_(None))
            .values(mfa_verified_at=now)
            .execution_options(synchronize_session=False)
            .returning(Session.id)
        )
    ).scalar_one_or_none()
    if claimed is None:
        return False
    auth_session.mfa_verified_at = now
    return True


async def _revoke_session_refresh_tokens(session: AsyncSession, session_id: uuid.UUID) -> None:
    """Every live refresh token issued against one session. Used alongside revoking the
    session itself, so a lockout actually ends what the attacker is holding rather than just
    marking the session and leaving a still-rotatable token in their hands."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.session_id == session_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def _register_operator_mfa_failure(
    session: AsyncSession, *, staff: StaffAccount, auth_session: Session, cfg: Settings
) -> None:
    """Count a wrong MFA or enrolment code against the account.

    BLOCKER 1 of the 2026-09-24 review: this used to share `failed_attempts`/`locked_until`
    with `_verify_staff`'s password check, and a correct password there reset both to zero —
    so a password right, code wrong, repeated, never crossed the threshold: the next correct
    password wiped the count every time. `mfa_failed_attempts`/`mfa_locked_until` are a
    separate pair that only this function and `_clear_operator_failures` touch; a password
    success no longer clears an MFA lock, and an MFA success no longer clears a password one.

    Crossing the threshold additionally revokes the session that produced the failures and
    every refresh token issued against it, so the account is not merely closed to new attempts
    while a session an attacker already opened stays alive.
    """
    staff.mfa_failed_attempts += 1
    if staff.mfa_failed_attempts >= cfg.max_failed_attempts:
        staff.mfa_locked_until = datetime.now(UTC) + timedelta(minutes=cfg.lockout_minutes)
        # m7: the count restarts from zero when the lock trips. Left at the threshold, the
        # first wrong code after the lock expired would push it straight back over and re-lock
        # the account for one typo — the lock's expiry would be a formality, not an unlock.
        staff.mfa_failed_attempts = 0
        auth_session.revoked_at = datetime.now(UTC)
        auth_session.revoked_reason = "operator_mfa_failed_attempts"
        await _revoke_session_refresh_tokens(session, auth_session.id)
        log.warning("auth.operator_locked", staff_id=str(staff.id), reason="mfa_failed_attempts")


def _clear_operator_failures(staff: StaffAccount) -> None:
    """Only a successful MFA check calls this — never `_verify_staff` on a correct password.
    See `_register_operator_mfa_failure` for why the two counters must stay independent."""
    staff.mfa_failed_attempts = 0
    staff.mfa_locked_until = None


def _dedupe_amr(amr: list[str]) -> list[str]:
    """`amr` in first-seen order, without duplicates. `_issue_operator` mints from a session's
    accumulated `amr`, and a step-up appends rather than replaces; this is what keeps a token's
    claim honest (`["pwd", "otp"]`, never `["pwd", "otp", "otp"]`) if a step is ever retried."""
    return list(dict.fromkeys(amr))


async def operator_login(
    session: AsyncSession,
    *,
    cfg: Settings,
    pair: KeyPair,
    email: str,
    password: str,
    request: Request | None = None,
) -> OperatorLoginOutcome:
    """Authenticate a staff account. Never returns a usable token by itself.

    There is no organization step — an operator belongs to nothing to pick between — but
    there is always a second-factor step: a correct password only ever earns a pre-auth
    token naming which one applies next, `mfa_required` or `mfa_enrollment_required`. The
    session and refresh chain reuse the same tables and rotation-family theft detection the
    tenant path uses; the lockout on repeated failures is `_verify_staff`'s and is untouched.
    """
    staff = await _verify_staff(session, cfg, email, password)

    auth_session = await start_session(
        session, user_id=staff.id, cfg=cfg, amr=["pwd"], request=request
    )
    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=staff.id,
        action="auth.operator_password_verified",
        entity="staff_account",
        entity_id=staff.id,
        request=request,
    )

    if await _operator_has_confirmed_factor(session, staff.id):
        reason, status_ = OPERATOR_MFA_REASON, "mfa_required"
    else:
        reason, status_ = OPERATOR_MFA_ENROLLMENT_REASON, "mfa_enrollment_required"
    token = tk.mint_pre_auth(
        pair, issuer=cfg.jwt_issuer, user_id=staff.id, session_id=auth_session.id, reason=reason
    )
    # The session row has to survive this response — the next request presents the
    # pre-auth token against it, exactly as the tenant MFA step does.
    await session.commit()
    return OperatorLoginOutcome(status=status_, pre_auth_token=token.token)


async def operator_begin_mfa_enrolment(
    session: AsyncSession,
    *,
    cfg: Settings,
    pair: KeyPair,
    pre_auth_token: str,
    enrollment_code: str,
    request: Request | None = None,
) -> tuple[MfaFactor, str, str]:
    """Start enrolling an operator's first factor.

    A correct password is not enough to reach this: `enrollment_code` must also redeem a
    code issued out-of-band (`issue_staff_enrollment_code`), and the account must not already
    have a confirmed factor. Both checks exist for the same reason — a password alone proves
    nothing about who is typing it, and the account that can suspend a subscription or read
    across every tenant does not get to be enrolled by whoever guesses or reuses a password
    first (the security review's R1/R2, and the ADR-0003 addendum's operator bootstrap).

    Bare session, self-committing: there is no principal yet, only a pre-auth token, the
    same shape every other pre-organization/pre-token flow in this module uses.
    """
    claims = _decode_pre_auth(
        pair, cfg, pre_auth_token, expect_reason=OPERATOR_MFA_ENROLLMENT_REASON
    )
    staff_id = uuid.UUID(str(claims["sub"]))
    auth_session = await _consume_pre_auth_session(session, uuid.UUID(str(claims["sid"])), staff_id)

    # `with_for_update`: MAJOR 4 of the 2026-09-24 review. Two enrolment attempts against the
    # same account — a stale token and a fresh one, or a plain double-submit — serialise here
    # instead of both reading "no confirmed factor yet" and both proceeding.
    staff = await session.get(StaffAccount, staff_id, with_for_update=True)
    if staff is None or not staff.is_active:
        raise UnauthorizedProblem("That session is no longer valid.")
    if _staff_locked(staff):
        raise ForbiddenProblem(
            "This operator account is temporarily locked after several failed attempts. "
            "Try again shortly."
        )
    if await _operator_has_confirmed_factor(session, staff_id):
        raise UnauthorizedProblem("MFA is already enrolled for this account. Sign in normally.")

    await _redeem_enrollment_code(
        session, staff_id, enrollment_code, staff=staff, auth_session=auth_session, cfg=cfg
    )
    await _prune_unconfirmed_factors(session, staff_id)

    factor, secret, otpauth_uri = await begin_mfa_enrolment(
        session, staff_id, account_email=staff.email, cfg=cfg
    )
    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=staff_id,
        action="auth.operator_mfa_enrol_started",
        entity="staff_account",
        entity_id=staff_id,
        request=request,
    )
    await session.commit()
    return factor, secret, otpauth_uri


async def confirm_operator_mfa_enrolment(
    session: AsyncSession,
    *,
    cfg: Settings,
    pair: KeyPair,
    pre_auth_token: str,
    factor_id: uuid.UUID,
    code: str,
    request: Request | None = None,
) -> tuple[OperatorIssued, list[str]]:
    """Prove the enrolled factor works, and complete the sign-in that started it.

    Deliberately does **not** call `confirm_mfa_enrolment`: that helper creates/updates a
    `Credential` row, and operators have none — folding one in here is exactly how a staff
    identity starts leaking into the tenant credential table. This mints tokens immediately
    rather than asking the caller to present the code a second time through
    `operator_verify_mfa`: a freshly-proven TOTP code already *is* the second factor for
    this sign-in, so re-asking would only doubt a check that just passed.
    """
    claims = _decode_pre_auth(
        pair, cfg, pre_auth_token, expect_reason=OPERATOR_MFA_ENROLLMENT_REASON
    )
    staff_id = uuid.UUID(str(claims["sub"]))
    auth_session = await _consume_pre_auth_session(session, uuid.UUID(str(claims["sid"])), staff_id)

    staff = await session.get(StaffAccount, staff_id, with_for_update=True)
    if staff is None or not staff.is_active:
        raise UnauthorizedProblem("That session is no longer valid.")
    if _staff_locked(staff):
        raise ForbiddenProblem(
            "This operator account is temporarily locked after several failed attempts. "
            "Try again shortly."
        )
    # Re-checked here, not only at `operator_begin_mfa_enrolment`: two enrolment attempts
    # (an attacker's stale token and the real owner's fresh one) can both pass `begin` before
    # either confirms. Whichever `confirm` call lands first wins the account; the other must
    # not be allowed to also confirm a *second* factor and silently take it over afterwards.
    if await _operator_has_confirmed_factor(session, staff_id):
        raise UnauthorizedProblem("MFA is already enrolled for this account. Sign in normally.")

    factor = await session.get(MfaFactor, factor_id)
    secret = await _open_factor_secret(session, factor, cfg=cfg) if factor is not None else None
    if factor is None or factor.user_id != staff_id or secret is None:
        raise NotFoundProblem("No such enrolment")
    if factor.confirmed_at is not None:
        raise UnauthorizedProblem("This factor is already confirmed.")

    step = security.verify_totp(secret, code, last_used_step=factor.last_used_step)
    if step is None:
        await _register_operator_mfa_failure(
            session, staff=staff, auth_session=auth_session, cfg=cfg
        )
        record_audit(
            session,
            AuditLog,
            organization_id=PLATFORM_ORG,
            actor_id=staff_id,
            action="auth.operator_mfa_enrol_failed",
            entity="staff_account",
            entity_id=staff_id,
            request=request,
        )
        await session.commit()
        raise UnauthorizedProblem("That code is not valid. Check your device's clock.")

    factor.confirmed_at = datetime.now(UTC)
    factor.last_used_step = step
    factor.last_used_at = datetime.now(UTC)
    _clear_operator_failures(staff)

    # The factor being confirmed here is the account's *first* — `_operator_has_confirmed_factor`
    # above refused otherwise — so there are no prior recovery codes to leave live by mistake.
    # Generated fresh regardless of what (unconfirmed, unused) rows might exist.
    await session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == staff_id))
    codes = security.generate_recovery_codes()
    for code_value in codes:
        session.add(
            MfaRecoveryCode(user_id=staff_id, code_hash=security.hash_recovery_code(code_value))
        )

    if not await _claim_mfa_verified(session, auth_session):
        # Lost a race against another confirmation of the same session — refuse rather than
        # mint a second token pair for a step that has already completed once.
        raise UnauthorizedProblem("Please start signing in again.")
    auth_session.amr = _dedupe_amr([*auth_session.amr, "otp"])
    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=staff_id,
        action="auth.operator_mfa_enrolled",
        entity="staff_account",
        entity_id=staff_id,
        request=request,
    )
    issued = _issue_operator(session, pair=pair, cfg=cfg, auth_session=auth_session, staff=staff)
    publish_event(
        session,
        OutboxEvent,
        organization_id=PLATFORM_ORG,
        event_type=EVT_OPERATOR_MFA_ENROLLED,
        aggregate_type="staff_account",
        aggregate_id=staff_id,
        payload={"staff_id": str(staff_id)},
    )
    await session.commit()
    return issued, codes


async def operator_verify_mfa(
    session: AsyncSession,
    *,
    cfg: Settings,
    pair: KeyPair,
    pre_auth_token: str,
    code: str,
    request: Request | None = None,
) -> OperatorIssued:
    """Second step of an operator sign-in, for a staff account that already has a confirmed
    factor. Mirrors the tenant `verify_mfa`, minus the organization-selection branch an
    operator never has.

    The lockout check runs **before** `_accept_second_factor` — which, for a recovery code,
    means before any Argon2 hash comparison — so a locked account cannot be used to keep
    spending CPU on guesses (MAJOR 4, the review's R3) merely by presenting enough wrong
    codes to matter before the lock trips; once it trips, every further call fails at this
    check alone.
    """
    claims = _decode_pre_auth(pair, cfg, pre_auth_token, expect_reason=OPERATOR_MFA_REASON)
    staff_id = uuid.UUID(str(claims["sub"]))
    auth_session = await _consume_pre_auth_session(session, uuid.UUID(str(claims["sid"])), staff_id)

    staff = await session.get(StaffAccount, staff_id, with_for_update=True)
    if staff is None or not staff.is_active:
        raise UnauthorizedProblem("That session is no longer valid.")
    if _staff_locked(staff):
        raise ForbiddenProblem(
            "This operator account is temporarily locked after several failed attempts. "
            "Try again shortly."
        )

    factor_kind = await _accept_second_factor(session, staff_id, code, cfg=cfg)
    if factor_kind is None:
        await _register_operator_mfa_failure(
            session, staff=staff, auth_session=auth_session, cfg=cfg
        )
        record_audit(
            session,
            AuditLog,
            organization_id=PLATFORM_ORG,
            actor_id=staff_id,
            action="auth.operator_mfa_failed",
            entity="staff_account",
            entity_id=staff_id,
            request=request,
        )
        await session.commit()
        raise UnauthorizedProblem("That code is not valid")

    _clear_operator_failures(staff)
    if not await _claim_mfa_verified(session, auth_session):
        raise UnauthorizedProblem("Please start signing in again.")
    auth_session.amr = _dedupe_amr([*auth_session.amr, "otp"])

    # A recovery-code sign-in is its own audited action (MINOR 11): a break-glass code being
    # spent is a materially different event from an ordinary authenticator check, and an
    # operator reading the trail should not have to guess which one happened.
    action = (
        "auth.operator_mfa_verified"
        if factor_kind == "totp"
        else "auth.operator_mfa_recovery_code_used"
    )
    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=staff_id,
        action=action,
        entity="staff_account",
        entity_id=staff_id,
        request=request,
    )
    issued = _issue_operator(session, pair=pair, cfg=cfg, auth_session=auth_session, staff=staff)
    publish_event(
        session,
        OutboxEvent,
        organization_id=PLATFORM_ORG,
        event_type=EVT_OPERATOR_SIGNED_IN,
        aggregate_type="staff_account",
        aggregate_id=staff_id,
        payload={"staff_id": str(staff_id), "session_id": str(auth_session.id)},
    )
    await session.commit()
    return issued


async def operator_rotate_refresh(
    session: AsyncSession, *, cfg: Settings, pair: KeyPair, raw_token: str
) -> OperatorIssued:
    """Rotate an operator refresh token, resolving the staff account rather than the directory.

    The same theft detection as the tenant path: a spent token ends the whole family. A
    *tenant* refresh token presented here resolves to no `staff_accounts` row and is
    refused — and a staff token presented to the tenant `rotate_refresh` resolves to no
    directory user and is refused there. Neither crosses into the other.

    Refuses outright when the session has no `mfa_verified_at` (MAJOR 5): every operator
    session that ever completes sign-in now records one, so a session without it was either
    minted before MFA became mandatory or reached this function by some path that never
    should have. Either way, refreshing it would keep an `amr=["pwd"]` session alive
    indefinitely through rotation — the one channel `require_operator`'s own `amr` check
    cannot see, because it only inspects the token in front of it, not the session behind it.
    """
    try:
        claims = tk.decode_local(pair, raw_token, issuer=cfg.jwt_issuer, expected_type=tk.REFRESH)
    except Exception as exc:
        raise UnauthorizedProblem("That session has expired. Please sign in again.") from exc

    stored = await session.get(RefreshToken, str(claims["jti"]))
    if stored is None:
        raise UnauthorizedProblem("That session has expired. Please sign in again.")

    if stored.is_spent:
        await _revoke_family(session, stored.family, reason="token_reuse")
        log.warning(
            "auth.operator_refresh_reuse_detected",
            family=stored.family,
            staff_id=str(stored.user_id),
        )
        await session.commit()
        raise UnauthorizedProblem(
            "That session has been ended for security reasons. Please sign in again."
        )

    if stored.expires_at <= datetime.now(UTC):
        raise UnauthorizedProblem("That session has expired. Please sign in again.")

    auth_session = await _live_session(session, stored.session_id, stored.user_id)
    if auth_session.mfa_verified_at is None:
        raise UnauthorizedProblem("That session has expired. Please sign in again.")
    staff = await session.get(StaffAccount, stored.user_id)
    if staff is None or not staff.is_active:
        raise UnauthorizedProblem("That session is no longer valid.")

    stored.rotated_at = datetime.now(UTC)
    auth_session.last_seen_at = datetime.now(UTC)
    issued = _issue_operator(
        session, pair=pair, cfg=cfg, auth_session=auth_session, staff=staff, family=stored.family
    )
    await session.commit()
    return issued


# --------------------------------------------------------------------- single sign-on
#
# The OIDC authorization-code flow with PKCE, for tenant sign-in. Two halves — one that
# starts an authorization request, one that finishes it — and a small administrative surface
# for the per-organization configuration they read.
#
# Three properties are load-bearing, and each is here because the obvious shortcut is a
# documented break:
#
# **The verified domain selects the identity provider, never the organization.** ADR-0000 §1
# is explicit: "Email domain never silently selects an organization; it may only select an
# identity provider, and only from a domain that has been DNS-verified." So the address
# somebody types is used for exactly one thing — asking account-service which organization
# proved ownership of that domain — and the *organization* comes from the provider
# configuration that organization created. The sign-in still fails unless the person the
# provider vouched for holds an active membership there.
#
# **The tenant is bound before `org_idp_configs` is read.** That table is the one table in
# this schema under row-level security (ADR-0002 §6), and these flows carry no principal —
# there is no token yet. So they use bypass-then-narrow the same way invitation acceptance
# does: resolve the organization through the cross-tenant directory lookup, then bind the
# transaction to it and read under the policy. The bind derives from a DNS-verified fact, not
# from the request.
#
# **Tokens are minted through `issue_for`, like every other path.** There is no second
# minting route: same audience handling, same `pv`, same entitlement resolution, same session
# and refresh family. A federated sign-in differs from a password sign-in in exactly two
# visible ways — `amr` says `sso` instead of `pwd`, and the session records which provider
# vouched — and in nothing else.

EVT_SSO_SIGNED_IN = "core.session.sso_signed_in"

#: The protocol discriminator in `org_idp_configs.protocol`. SAML is the deferred sibling
#: (docs/backlog.md); rows carrying it are ignored by everything here rather than
#: half-handled.
OIDC = "oidc"

#: What the `amr` claim says after a federated sign-in. Deliberately **not** `pwd`: a step-up
#: gate that demands a second factor (`require(..., amr={"otp"})`) must not be satisfiable by
#: an assertion from an external issuer, and a claim that lied about how somebody
#: authenticated would make every such gate a decoration.
SSO_AMR = "sso"


@dataclass(frozen=True, slots=True)
class SsoRedirect:
    """Where to send the browser, and how long the request stays open."""

    authorization_url: str
    state: str
    expires_in: int
    organization_slug: str
    provider_name: str


def domain_of(email: str) -> str:
    """The domain half of an address, lowercased. Empty when there is no `@`."""
    _, sep, domain = email.strip().lower().rpartition("@")
    return domain if sep else ""


async def _config_for_domain(
    session: AsyncSession, organization_id: uuid.UUID, domain: str
) -> OrgIdpConfig | None:
    """The organization's enabled OIDC configuration covering `domain`, if any.

    Read **after** the transaction is bound to `organization_id`, so row-level security is
    doing its job rather than being trusted to be irrelevant.

    An explicit `allowed_domains` entry wins over a catch-all. That ordering matters for the
    organization running two providers — one for staff, one for a subsidiary's domain — where
    the catch-all would otherwise swallow the more specific one depending on insertion order.
    """
    rows = (
        (
            await session.execute(
                select(OrgIdpConfig).where(
                    OrgIdpConfig.organization_id == organization_id,
                    OrgIdpConfig.protocol == OIDC,
                    OrgIdpConfig.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    catch_all: OrgIdpConfig | None = None
    for row in rows:
        allowed = [d.lower() for d in (row.allowed_domains or [])]
        if domain in allowed:
            return row
        if not allowed and catch_all is None:
            catch_all = row
    return catch_all


def _usable_for_oidc(config: OrgIdpConfig) -> bool:
    """Every field the flow will need, checked once, before the browser leaves.

    A half-configured provider that fails at the callback strands somebody at the identity
    provider with no way back and no message. Failing at `start` at least says so.
    """
    return all(
        (
            config.issuer,
            config.client_id,
            config.client_secret_enc,
            config.authorization_endpoint,
            config.token_endpoint,
            config.jwks_uri,
        )
    )


async def sso_start(
    session: AsyncSession,
    *,
    cfg: Settings,
    directory: Directory,
    store: oidc.StateStore,
    email: str | None = None,
    domain: str | None = None,
) -> SsoRedirect:
    """Begin an authorization-code flow for whoever owns this email domain.

    Either an address or a bare domain: the address is only ever reduced to its domain here
    and passed to the provider as a `login_hint`, so the two entry points differ by
    convenience and not by trust.

    Every refusal is the same `SsoNotConfiguredProblem` with the same message — unknown
    domain, unverified domain, suspended organization, no configuration, disabled
    configuration, incomplete configuration. The cause is logged, not returned: an unverified
    domain answering differently from an unknown one turns this endpoint into a map of which
    customers exist and which are mid-onboarding.
    """
    target = (domain or (domain_of(email) if email else "")).strip().lower()
    refuse = oidc.SsoNotConfiguredProblem("Single sign-on is not set up for that address")
    if not target:
        raise refuse

    resolved = await directory.resolve_domain(target)
    if resolved is None:
        log.info("sso.start_refused", reason="unknown_domain", domain=target)
        raise refuse
    if not resolved.verified:
        # The refusal ADR-0000 §1 exists for. A domain row anybody could add would otherwise
        # let them route another organization's sign-ins to a provider they control.
        log.warning("sso.start_refused", reason="unverified_domain", domain=target)
        raise refuse
    if not resolved.is_usable:
        log.info("sso.start_refused", reason="organization_suspended", domain=target)
        raise refuse

    # Bypass-then-narrow: the organization came from the cross-tenant domain lookup, and from
    # here on this transaction is that organization's. `org_idp_configs` is unreadable
    # otherwise — the policy is FORCEd and this connection has no BYPASSRLS.
    await set_tenant(session, resolved.organization_id)

    config = await _config_for_domain(session, resolved.organization_id, target)
    if config is None:
        log.info("sso.start_refused", reason="no_configuration", domain=target)
        raise refuse
    if not _usable_for_oidc(config):
        log.warning(
            "sso.start_refused",
            reason="incomplete_configuration",
            domain=target,
            config_id=str(config.id),
        )
        raise refuse

    verifier = oidc.new_verifier()
    nonce = secrets.token_urlsafe(24)
    state = await store.issue(
        oidc.AuthorizationRequest(
            organization_id=str(resolved.organization_id),
            config_id=str(config.id),
            nonce=nonce,
            code_verifier=verifier,
            domain=target,
            created_at=oidc.now_iso(),
        ),
        ttl_seconds=cfg.sso_state_ttl_seconds,
    )

    log.info(
        "sso.started",
        domain=target,
        organization_id=str(resolved.organization_id),
        config_id=str(config.id),
    )
    return SsoRedirect(
        authorization_url=oidc.authorization_url(
            authorization_endpoint=str(config.authorization_endpoint),
            client_id=str(config.client_id),
            redirect_uri=cfg.sso_redirect_uri,
            scopes=list(config.scopes or oidc.DEFAULT_SCOPES),
            state=state,
            nonce=nonce,
            code_challenge=oidc.challenge_for(verifier),
            login_hint=email,
        ),
        state=state,
        expires_in=cfg.sso_state_ttl_seconds,
        organization_slug=resolved.organization_slug,
        provider_name=config.display_name,
    )


async def sso_callback(
    session: AsyncSession,
    *,
    cfg: Settings,
    pair: KeyPair,
    directory: Directory,
    entitlements: Entitlements,
    idp: oidc.IdpClient,
    store: oidc.StateStore,
    code: str,
    state: str,
    audience: str = "app",
    request: Request | None = None,
) -> Issued:
    """Finish the flow: validate, exchange, verify, map to a member, mint.

    In order, and the order is the point — nothing that costs a network call happens before
    the cheap check that this callback belongs to a request we started:

    1. **Consume the state.** Single-use and atomic, so a replayed callback dies here.
    2. **Re-bind the tenant** from the state's organization and re-read the configuration.
       The state is opaque and server-side, so this is our own earlier decision read back,
       not the caller's claim about which organization they want.
    3. **Exchange the code** with the PKCE verifier that never left this service.
    4. **Verify the ID token** — signature against the provider's JWKS, issuer, audience,
       expiry, nonce, and a *verified* email.
    5. **Map to a member.** An address the directory does not know, or knows without an
       active membership in this organization, is refused with one message either way.
    6. **Mint through `issue_for`**, the same call sign-in, org selection, org switching and
       refresh all use.
    """
    pending = await store.consume(state)
    organization_id = uuid.UUID(pending.organization_id)
    await set_tenant(session, organization_id)

    # Named by id *and* by organization. The id came from our own server-side state so it is
    # not caller input, but writing the tenant predicate out is the house rule (RLS is the
    # net, not the filter) — and a `session.get` would answer from the identity map, which is
    # a read the policy never sees.
    config = (
        await session.execute(
            select(OrgIdpConfig).where(
                OrgIdpConfig.id == uuid.UUID(pending.config_id),
                OrgIdpConfig.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    if config is None or not config.enabled or not _usable_for_oidc(config):
        # The configuration was disabled or torn down between the redirect and the callback.
        # Honouring the in-flight request would let a revoked provider still mint sessions.
        log.warning(
            "sso.callback_refused", reason="configuration_gone", state_domain=pending.domain
        )
        raise oidc.SsoExchangeProblem("That sign-in could not be completed. Please start again.")

    try:
        client_secret = pii.open_sealed(config.client_secret_enc or b"", settings=cfg)
    except pii.PiiError as exc:
        # An operational fault, not a rejection: the row is there and no configured key opens
        # it, which means a rotation dropped a key. 503, so it reads as "us", not "you".
        log.error("sso.client_secret_unopenable", config_id=str(config.id))
        raise UpstreamUnavailableProblem(
            "Single sign-on is temporarily unavailable. Please try again."
        ) from exc

    tokens = await idp.exchange_code(
        str(config.token_endpoint),
        code=code,
        # Verbatim from configuration — the provider compares it against the value sent to
        # `authorize`, and it is also why nothing may take it from the request.
        redirect_uri=cfg.sso_redirect_uri,
        client_id=str(config.client_id),
        client_secret=client_secret,
        code_verifier=pending.code_verifier,
    )
    jwks = await idp.fetch_jwks(str(config.jwks_uri))
    identity = oidc.verify_id_token(
        tokens.id_token,
        jwks=jwks,
        issuer=str(config.issuer),
        audience=str(config.client_id),
        nonce=pending.nonce,
        leeway_seconds=cfg.sso_clock_skew_seconds,
    )

    no_access = oidc.SsoNoAccessProblem(
        "That account cannot sign in to this organization. Ask an administrator to invite it."
    )
    user = await directory.find_by_email(identity.email)
    if user is None or not user.is_active:
        # Unknown, or deactivated — one answer. `jit_provisioning` is recorded as asked-for
        # and not acted on: creating a directory user, a membership and role grants is
        # account-service's job and reaches it through an invitation, deliberately.
        log.warning(
            "sso.callback_refused",
            reason="no_directory_user",
            organization_id=str(organization_id),
            jit_requested=bool(config.jit_provisioning),
        )
        raise no_access

    membership = user.membership_for(organization_id)
    if membership is None:
        log.warning(
            "sso.callback_refused",
            reason="not_a_member",
            organization_id=str(organization_id),
            user_id=str(user.user_id),
        )
        raise no_access

    provider = f"{OIDC}:{organization_id}"
    await _link_external_identity(
        session, provider=provider, subject=identity.subject, user=user, email=identity.email
    )

    auth_session = await start_session(
        session, user_id=user.user_id, cfg=cfg, amr=[SSO_AMR], idp=provider, request=request
    )
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=user.user_id,
        action="auth.sso_signed_in",
        entity="session",
        entity_id=auth_session.id,
        request=request,
    )
    issued = await issue_for(
        session,
        pair=pair,
        cfg=cfg,
        auth_session=auth_session,
        membership=membership,
        entitlements=entitlements,
        audience=audience,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=organization_id,
        event_type=EVT_SSO_SIGNED_IN,
        aggregate_type="session",
        aggregate_id=auth_session.id,
        payload={
            "user_id": str(user.user_id),
            "session_id": str(auth_session.id),
            "organization_id": str(organization_id),
            "idp": provider,
            "amr": [SSO_AMR],
        },
    )
    await session.commit()
    log.info(
        "sso.signed_in",
        organization_id=str(organization_id),
        user_id=str(user.user_id),
        session_id=str(auth_session.id),
    )
    return issued


async def _link_external_identity(
    session: AsyncSession,
    *,
    provider: str,
    subject: str,
    user: DirectoryUser,
    email: str,
) -> None:
    """Bind the provider's subject to this account, or check the binding that exists.

    The provider's `sub` is stable where an email address is not, so the link is the stronger
    of the two identifiers and worth keeping even though the *lookup* is by address. Two
    mismatches are refused rather than repaired:

    * the same subject already pointing at a **different** user — one person's provider
      account cannot be walked onto another's platform account;
    * this user already linked to a **different** subject — the address was reassigned at the
      provider, and the new holder must not inherit the old account. This is the takeover
      `ExternalIdentity` was written to prevent.

    Both need an administrator to unlink deliberately, which is a decision rather than a side
    effect of somebody signing in.
    """
    conflict = oidc.SsoIdentityConflictProblem(
        "That provider account is linked to a different user. Ask an administrator to unlink it."
    )

    by_subject = (
        await session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.provider == provider, ExternalIdentity.subject == subject
            )
        )
    ).scalar_one_or_none()
    if by_subject is not None:
        if by_subject.user_id != user.user_id:
            log.warning(
                "sso.identity_conflict", reason="subject_bound_elsewhere", provider=provider
            )
            raise conflict
        return

    by_user = (
        await session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.provider == provider, ExternalIdentity.user_id == user.user_id
            )
        )
    ).scalar_one_or_none()
    if by_user is not None:
        log.warning("sso.identity_conflict", reason="subject_changed", provider=provider)
        raise conflict

    session.add(
        ExternalIdentity(
            user_id=user.user_id, provider=provider, subject=subject, email_at_link=email
        )
    )


# --------------------------------------------------------------------- SSO administration


async def get_sso_config(session: AsyncSession, organization_id: uuid.UUID) -> OrgIdpConfig | None:
    """This organization's OIDC configuration, or None.

    Called from a route that already holds a principal, so the transaction is bound by the
    chokepoint and the policy filters this. The `organization_id` predicate is still written
    out: RLS is the net, not the filter (CLAUDE.md).
    """
    return (
        await session.execute(
            select(OrgIdpConfig).where(
                OrgIdpConfig.organization_id == organization_id, OrgIdpConfig.protocol == OIDC
            )
        )
    ).scalar_one_or_none()


async def save_sso_config(
    session: AsyncSession,
    *,
    cfg: Settings,
    organization_id: uuid.UUID,
    actor_id: uuid.UUID,
    display_name: str,
    issuer: str,
    client_id: str,
    client_secret: str | None,
    discovery_url: str | None,
    authorization_endpoint: str | None,
    token_endpoint: str | None,
    jwks_uri: str | None,
    scopes: list[str] | None,
    allowed_domains: list[str],
    enabled: bool,
    enforce_sso: bool,
    idp: oidc.IdpClient | None = None,
    request: Request | None = None,
) -> OrgIdpConfig:
    """Create or update the organization's OIDC provider.

    One row per organization for this protocol, upserted rather than versioned: an IdP
    configuration is current state rather than a ledger, and the audit row records who
    changed it.

    **The client secret is write-only.** It is sealed with the platform PII envelope
    (`platform_core.pii.seal`, AES-256-GCM under a rotatable key) before it touches a column,
    it is never returned by any endpoint, and passing `None` here leaves whatever is stored
    untouched — so editing the display name does not require re-typing a credential, and does
    not tempt an administrator into pasting it somewhere it can be logged.

    Discovery is resolved **here**, once, and the endpoints are pinned into the row rather
    than fetched on every sign-in. When the document names a different issuer than the one
    configured that is refused rather than silently preferred: the issuer is what every ID
    token is validated against, and adopting whatever a URL returned would make the check
    circular.
    """
    if discovery_url and idp is not None:
        document = await idp.discover(discovery_url)
        found = str(document.get("issuer", "")).rstrip("/")
        if found and issuer and found != issuer.rstrip("/"):
            # The mismatch itself — not the issuer a URL we do not control claimed to be — is
            # what the caller learns. Echoing `found` back would put fetched content straight
            # into an error message, which is a reflection an attacker-controlled discovery
            # document could use to probe what this service saw. The comparison stays useful
            # to whoever reads the log line, where it belongs.
            log.warning(
                "sso.discovery_issuer_mismatch",
                organization_id=str(organization_id),
                expected_issuer=issuer[:200],
                found_issuer=found[:200],
            )
            raise BusinessRuleProblem(
                "That discovery document does not match the configured issuer. "
                "Check the URL, or correct the issuer."
            )
        authorization_endpoint = authorization_endpoint or document.get("authorization_endpoint")
        token_endpoint = token_endpoint or document.get("token_endpoint")
        jwks_uri = jwks_uri or document.get("jwks_uri")

    row = await get_sso_config(session, organization_id)
    created = row is None
    if row is None:
        row = OrgIdpConfig(organization_id=organization_id, protocol=OIDC)
        session.add(row)

    row.display_name = display_name
    row.issuer = issuer
    row.client_id = client_id
    row.discovery_url = discovery_url
    row.authorization_endpoint = authorization_endpoint
    row.token_endpoint = token_endpoint
    row.jwks_uri = jwks_uri
    row.scopes = scopes or list(oidc.DEFAULT_SCOPES)
    row.allowed_domains = [d.strip().lower() for d in allowed_domains if d.strip()]
    row.enforce_sso = enforce_sso

    if client_secret:
        # `seal` returns (ciphertext, mask); the mask is discarded deliberately. A hint of a
        # client secret is a hint of a secret, and nothing in the product displays one.
        sealed, _ = pii.seal(client_secret, kind="generic", settings=cfg)
        row.client_secret_enc = sealed

    if enabled and not _usable_for_oidc(row):
        # Refused at save rather than at sign-in. An organization that switches this on and
        # finds out at 9am that half the fields are blank was failed by the absence of this
        # check, not helped by it.
        raise BusinessRuleProblem(
            "Single sign-on cannot be enabled until the issuer, client id, client secret, "
            "authorization endpoint, token endpoint and JWKS URI are all set."
        )
    row.enabled = enabled

    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=actor_id,
        action="auth.sso_config_created" if created else "auth.sso_config_updated",
        entity="org_idp_config",
        entity_id=row.id,
        request=request,
        # Never the secret, and never the ciphertext either. What changed is enough for an
        # audit reader; the value is what the envelope exists to keep out of reach.
        after={
            "issuer": issuer,
            "client_id": client_id,
            "enabled": enabled,
            "enforce_sso": enforce_sso,
            "allowed_domains": row.allowed_domains,
            "client_secret_rotated": bool(client_secret),
        },
    )
    return row
