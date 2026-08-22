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
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import security
from app import tokens as tk
from app.config import Settings
from app.directory import Directory, DirectoryMembership, DirectoryUser
from app.entitlements import Entitlements
from app.keys import KeyPair
from app.models import (
    AuditLog,
    Credential,
    MfaFactor,
    MfaRecoveryCode,
    OutboxEvent,
    PasswordResetToken,
    RefreshToken,
    Session,
)
from platform_core.audit import record_audit
from platform_core.errors import (
    BusinessRuleProblem,
    ForbiddenProblem,
    NotFoundProblem,
    UnauthorizedProblem,
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
        amr=list(auth_session.amr),
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
        payload={"user_id": str(user.user_id), "email": user.email, "token": raw},
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


async def begin_mfa_enrolment(
    session: AsyncSession, user_id: uuid.UUID, *, account_email: str
) -> tuple[MfaFactor, str, str]:
    """Create an unconfirmed factor and hand back the secret and its QR URI."""
    secret = security.generate_totp_secret()
    factor = MfaFactor(user_id=user_id, type="totp", label="Authenticator app")
    # Envelope encryption belongs here; the dev path stores the secret directly and the
    # column is bytes so the shape does not change when the KMS lands.
    factor.secret_enc = secret.encode()
    session.add(factor)
    # Flushed, not committed: the caller returns the factor's id, and the transaction
    # belongs to the route that opened it.
    await session.flush()
    return factor, secret, security.totp_uri(secret, account=account_email)


async def confirm_mfa_enrolment(
    session: AsyncSession, *, factor_id: uuid.UUID, user_id: uuid.UUID, code: str
) -> list[str]:
    """Prove the factor works, turn it on, and issue recovery codes.

    Requiring a working code before enabling is what stops somebody locking themselves out
    with a mis-scanned QR.
    """
    factor = await session.get(MfaFactor, factor_id)
    if factor is None or factor.user_id != user_id or factor.secret_enc is None:
        raise NotFoundProblem("No such enrolment")

    step = security.verify_totp(factor.secret_enc.decode(), code)
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
    """Second step of sign-in: check the factor, then continue where login left off."""
    claims = _decode_pre_auth(pair, cfg, pre_auth_token, expect_reason="mfa")
    user_id = uuid.UUID(str(claims["sub"]))
    auth_session = await _live_session(session, uuid.UUID(str(claims["sid"])), user_id)

    if not await _accept_second_factor(session, user_id, code):
        raise UnauthorizedProblem("That code is not valid")

    auth_session.amr = [*auth_session.amr, "otp"]
    auth_session.mfa_verified_at = datetime.now(UTC)

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


async def _accept_second_factor(session: AsyncSession, user_id: uuid.UUID, code: str) -> bool:
    """A TOTP code, or a single-use recovery code."""
    stmt = select(MfaFactor).where(MfaFactor.user_id == user_id, MfaFactor.confirmed_at.isnot(None))
    for factor in (await session.execute(stmt)).scalars().all():
        if factor.secret_enc is None:
            continue
        step = security.verify_totp(
            factor.secret_enc.decode(), code, last_used_step=factor.last_used_step
        )
        if step is not None:
            # Remember the step so the same code cannot be replayed inside its window.
            factor.last_used_step = step
            factor.last_used_at = datetime.now(UTC)
            return True

    recovery_stmt = select(MfaRecoveryCode).where(
        MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None)
    )
    for recovery in (await session.execute(recovery_stmt)).scalars().all():
        if security.verify_recovery_code(recovery.code_hash, code):
            recovery.used_at = datetime.now(UTC)
            return True
    return False
