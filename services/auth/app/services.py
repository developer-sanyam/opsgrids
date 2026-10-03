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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import idna
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
    PlatformOAuthProvider,
    RefreshToken,
    Session,
    StaffAccount,
    StaffEnrollmentCode,
)
from app.provisioning import ProvisioningClient
from platform_core import pii
from platform_core.audit import record_audit
from platform_core.auth import Principal
from platform_core.cache import cache_delete, cache_get, cache_set
from platform_core.db import assert_tenant, set_tenant
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
    ProblemError,
    UnauthorizedProblem,
    UpstreamUnavailableProblem,
    ValidationProblem,
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
class AccessOnlyIssued:
    """What a grace-window hit on `rotate_refresh` answers with (M1 of the 2026-09-24 SSO
    review): a fresh access token, and nothing else. No refresh token is minted, no
    `RefreshToken` row is written, and the route layer sets no cookie for it — the browser that
    is racing to land here already holds the cookie the *winning* rotation's response set, from
    the same host every app refreshes against. Minting a second refresh token here would fork
    the family for no reason the grace window exists to serve, and is exactly the plaintext
    Redis previously held to be able to answer with one."""

    access_token: str
    expires_in: int
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


#: One message for a locked password, shared by sign-in and the re-authentication that account
#: security changes (enrolling a second factor) ask for, so the two can never drift apart.
PASSWORD_LOCKED_MESSAGE = (
    "This account is temporarily locked after several failed attempts. "
    "Try again shortly, or reset the password."
)


def _register_password_failure(credential: Credential, cfg: Settings) -> bool:
    """Count one wrong password against the account and lock it at `max_failed_attempts`.
    Returns whether this call tripped the lock. The caller commits — before it raises, or the
    count is rolled back with the request it was counting.

    The one implementation behind sign-in (`_verify_credentials`), `begin_member_enrolment` and
    `change_password`: they share `Credential.failed_attempts` / `locked_until` on purpose,
    because a password guessed at through a signed-in session is the same password guessed at
    through the login form and must not get a second allowance.

    **The count restarts with the lock**, as `_register_mfa_failure` does for the code counter, so
    that the lock expiring is a real unlock: with the count left at `max_failed_attempts`, the
    first wrong password after `lockout_minutes` would lock the account again — and in
    `change_password` and `begin_member_enrolment`, where the tripping attempt also ends the
    session it came from, that one typo would end the member's session. (Second review, m1.)
    """
    credential.failed_attempts += 1
    if credential.failed_attempts < cfg.max_failed_attempts:
        return False
    credential.locked_until = datetime.now(UTC) + timedelta(minutes=cfg.lockout_minutes)
    credential.failed_attempts = 0
    log.warning("auth.account_locked", user_id=str(credential.user_id))
    return True


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
        raise ForbiddenProblem(PASSWORD_LOCKED_MESSAGE)

    if not user.is_active:
        raise ForbiddenProblem("This account has been deactivated")

    if not security.verify_password(credential.password_hash, password):
        _register_password_failure(credential, cfg)
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
    sso_domain: str | None = None,
    request: Request | None = None,
) -> Session:
    row = Session(
        user_id=user_id,
        amr=amr,
        auth_time=datetime.now(UTC),
        idp=idp,
        sso_domain=sso_domain,
        expires_at=datetime.now(UTC) + timedelta(days=cfg.session_ttl_days),
        ip=request.client.host if request and request.client else None,
        user_agent=request.headers.get("user-agent") if request else None,
    )
    session.add(row)
    await session.flush()
    return row


#: The `amr` value that says "this token came from `POST /auth/mfa/step-up`" (ADR-0023). `otp`
#: alone cannot say it: a session that presented a code **at sign-in** carries `otp` in
#: `Session.amr` for its whole life (`verify_mfa`), so every token it ever mints passes
#: `require(..., amr={"otp"})` with no code presented since. The reveal routes therefore require
#: **both** — `require` demands every member of the set — and only `step_up` can supply this one:
#: it goes into the *token's* `amr` (`_mint_access_only(extra_amr=...)`) and nowhere else. It is
#: never written to `Session.amr`, and `_session_amr` strips it from a session that somehow held
#: it, so no sign-in, refresh, organization switch or grace-window rotation can ever produce it.
#:
#: The literal is duplicated in lead- and customer-service's reveal guards (`STEP_UP_AMR` in
#: their `app/api/routes.py`), because a shared constant would live in `platform-core` and
#: change every service's image. `tests/infra/test_step_up_marker.py` pins the three together.
STEP_UP_AMR = "stepup"


def _session_amr(auth_session: Session) -> list[str]:
    """What a session's tokens may carry in `amr`: its own record, minus the step-up marker."""
    return [value for value in auth_session.amr if value != STEP_UP_AMR]


def _refresh_ttl_seconds(cfg: Settings, auth_session: Session) -> int:
    """The refresh token's own lifetime, clamped to whatever is left of the *session's*
    absolute cap (owner decision, 2026-09-24: 7 days, then a new sign-in, however active the
    person was).

    `refresh_token_ttl_seconds` is a sliding window — it renews on every rotation — and left
    unclamped it would keep doing that forever, past the day `session.expires_at` was minted
    for. This is the one place every token-issuing path funnels through (`issue_for`'s own
    docstring), so it is also the one place that cap actually bites: a refresh on day 6 of 7
    gets a successor good for about a day, not another full `refresh_token_ttl_seconds`. Never
    negative — `_live_session` already refuses a session whose `expires_at` has passed before
    any caller reaches this — but clamped at zero anyway rather than trusting that invariant to
    never move.
    """
    remaining = (auth_session.expires_at - datetime.now(UTC)).total_seconds()
    return max(min(cfg.refresh_token_ttl_seconds, int(remaining)), 0)


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

    **The federated-session pin is asserted here too** (round 3, NIT), not only by the two
    callers (`switch_organization`, `rotate_refresh`) that already check it before reaching
    this point. Those early checks stay — `switch_organization`'s runs before it revokes the
    old organization's refresh tokens, and `rotate_refresh`'s has its own bespoke
    theft-response (revoking the whole family, not just refusing) — but this is the one
    function every path that mints a token actually calls, so it is also the one place a
    *future* caller cannot forget to check it: minting for the wrong organization from a
    federated session is refused here even if whatever called this never thought to ask.
    """
    _assert_organization_unpinned_or_matches(auth_session, membership.organization_id)
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
        amr=_dedupe_amr(_session_amr(auth_session)),
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
        ttl_seconds=_refresh_ttl_seconds(cfg, auth_session),
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


async def _mint_access_only(
    *,
    pair: KeyPair,
    cfg: Settings,
    auth_session: Session,
    membership: DirectoryMembership,
    entitlements: Entitlements,
    audience: str,
    extra_amr: Sequence[str] = (),
    ttl_override: int | None = None,
    granted: list[str] | None = None,
) -> AccessOnlyIssued:
    """The access-only half of `issue_for`, for a grace-window hit on `rotate_refresh`: the
    same claims and the same entitlement resolution, but no refresh token and no `RefreshToken`
    row — see `AccessOnlyIssued`.

    Three optional knobs exist for the member step-up (`step_up`, ADR-0023) and default to the
    grace-window behaviour, so that caller is unchanged:

    * `extra_amr` is added to the *token's* `amr` claim only. It is deliberately not written to
      `Session.amr` — the session is what a refresh mints from, so anything appended there is
      carried by every later token for the whole session; anything appended here dies with this
      one token.
    * `ttl_override` replaces `access_token_ttl_seconds` for this token.
    * `granted` is an entitlement list the caller already resolved. The step-up resolves it
      *before* it takes the credential row lock, so a slow product-service never holds that lock.
    """
    _assert_organization_unpinned_or_matches(auth_session, membership.organization_id)
    if granted is None:
        granted = await entitlements.for_organization(membership.organization_id)
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
        amr=_dedupe_amr([*_session_amr(auth_session), *extra_amr]),
        permission_version=membership.permission_version,
        subject_ref=membership.employee_id,
        ttl_seconds=ttl_override if ttl_override is not None else cfg.access_token_ttl_seconds,
    )
    return AccessOnlyIssued(
        access_token=access.token,
        expires_in=access.expires_in,
        session_id=auth_session.id,
        organization_id=membership.organization_id,
        membership_id=membership.membership_id,
    )


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
    from_organization_id: uuid.UUID | None = None,
) -> Issued:
    """Move a live session to another organization.

    The **same session** carries over — one browser sign-in, several organizations, one
    `sid`. That is what makes a single back-channel logout end all of it.

    **This is the one place an access token alone is exchanged for a refresh token** (it takes a
    bearer, not a cookie or a password), which is why a stolen access token is worth more than
    its fifteen minutes to anyone who can reach it. `from_organization_id` — the organization the
    presenting token is already for — closes the cheapest form of that: switching to the
    organization you are already in changes nothing, has no legitimate caller (the dashboard's
    switcher offers only the *other* organizations), and could only ever have been made to
    collect a fresh refresh cookie. It is refused (`already-in-organization`, 409). It is checked
    after the federated-session re-validation below, so a dead federated session is still ended
    rather than merely refused.

    It does **not** close the same exchange for a member of two or more organizations, who can
    still be moved to another one and back: doing that needs this route to demand the refresh
    cookie the session already holds (recorded in ADR-0023 as a residual risk).
    `from_organization_id=None` (service-level callers and tests, which have no token) skips it.
    """
    auth_session = await _live_session(session, session_id, user_id)
    _assert_organization_unpinned_or_matches(auth_session, organization_id)

    # **The same re-validation `rotate_refresh` runs, for the same reason.** Switching into
    # the session's *own* pinned organization mints a brand-new access and refresh pair, so a
    # federated session whose domain has since been released or demoted, or whose provider has
    # since been turned off, must not be able to get one here just because it never happened
    # to call refresh first. The session is ended, not merely refused — and committed, because
    # this runs on the route's tenant session, which rolls back on the exception below.
    pinned = _federated_organization_pin(auth_session)
    if pinned is not None and not await _pinned_sso_still_valid(
        session,
        directory=directory,
        organization_id=pinned,
        sso_domain=auth_session.sso_domain,
    ):
        await _revoke_session(session, auth_session.id, reason="sso_no_longer_valid")
        log.warning(
            "auth.switch_sso_no_longer_valid",
            session_id=str(auth_session.id),
            organization_id=str(pinned),
        )
        await session.commit()
        raise UnauthorizedProblem("That session has expired. Please sign in again.")

    if from_organization_id is not None and from_organization_id == organization_id:
        raise AlreadyInOrganizationProblem("You are already in that organization.")

    user = await directory.get(user_id)
    if user is None:
        raise UnauthorizedProblem(INVALID)
    if not user.is_active:
        # The check `rotate_refresh` already makes, and the same class of bug when it is
        # missing: a deactivated person's still-live session must not be able to mint fresh
        # tokens through a route that only ever asked whether they exist.
        raise UnauthorizedProblem("That session is no longer valid.")
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


def _grace_marker_key(jti: str) -> str:
    """`sha256(jti)`, not `jti` itself (M1 of the 2026-09-24 SSO review): Redis on this box has
    no password and is reachable from every one of the ~34 service containers on the same
    compose network, and AOF persists whatever it holds to disk. The jti is not the secret the
    refresh token itself is, but there is no reason for a value that exists purely as a marker
    to be a literal, greppable session identifier either — hashing it costs nothing and the
    lookup only ever needs to ask "is *this* jti's own marker present", never to enumerate them.
    """
    return f"auth:refresh_grace:{hashlib.sha256(jti.encode()).hexdigest()}"


async def _remember_grace_marker(cfg: Settings, *, jti: str) -> None:
    """A marker, and nothing else: no token, no user id, no session id — none of the token
    material the grace cache used to hold live in Redis, in plaintext, for
    `refresh_reuse_grace_seconds` (M1 of the 2026-09-24 SSO review). A grace hit re-derives and
    re-validates the session, the user and the membership from `stored`'s own row — the same
    checks a fresh rotation runs — and mints a brand new access token from them; the marker's
    only job is answering "was this jti legitimately rotated a moment ago", which a bare
    presence check answers as well as a payload would. `cache_set` degrades to a no-op on a
    Redis outage, same as every other soft cache here — the worst case is that the grace window
    does not apply and the older, stricter behaviour (revoke on replay) stands.
    """
    await cache_set(_grace_marker_key(jti), "1", ttl=cfg.refresh_reuse_grace_seconds)


async def _grace_marker_present(cfg: Settings, *, jti: str, rotated_at: datetime | None) -> bool:
    """`True` only if `jti` was rotated within the grace window *and* its marker is still
    cached — anything else falls through to the ordinary reuse response. `rotated_at` is
    `stored`'s own column, read by the caller before this is asked — a family already revoked
    is never passed here at all (grace does not apply to it, marker or not)."""
    if rotated_at is None:
        return False
    if datetime.now(UTC) - rotated_at > timedelta(seconds=cfg.refresh_reuse_grace_seconds):
        return False
    return await cache_get(_grace_marker_key(jti)) is not None


async def rotate_refresh(
    session: AsyncSession,
    *,
    cfg: Settings,
    pair: KeyPair,
    directory: Directory,
    raw_token: str,
    audience: str = "app",
    entitlements: Entitlements,
) -> Issued | AccessOnlyIssued:
    """Exchange a refresh token for a new pair, detecting theft.

    Presenting a **spent** token means it was captured — the legitimate holder already
    rotated it. The whole family is revoked rather than just this link: the thief gets
    nothing, and the real user is forced to sign in again. An occasional forced re-login
    is a far better outcome than a silently shared session.

    **Unless it was spent moments ago by rotation, not revocation.** A browser holds one
    refresh cookie shared by every tab and every product SPA (ADR-0003 addendum), and two of
    them racing — a proactive refresh in one landing mid-flight of a 401 retry in another —
    present the identical token within milliseconds of each other. `_grace_marker_present` is
    what tells them apart from a thief, by whether the rotation is recent enough and its own
    marker is still cached. A token whose family is already revoked skips this entirely — that
    is a family theft already responded to, or a logout, and grace never applies to it.

    A grace hit does **not** reuse the successor's own tokens (there is no cached copy of them
    to reuse — see `AccessOnlyIssued`, M1 of the 2026-09-24 SSO review). It re-validates the
    session, the user and the membership from `stored`'s own row — every check a fresh rotation
    below also runs — and returns a brand new access token and nothing else: no refresh token,
    no `RefreshToken` row, no `Set-Cookie` from the route layer. The browser racing to land here
    already holds the cookie the winning rotation's response set, since every app refreshes
    against the same host.
    """
    try:
        claims = tk.decode_local(pair, raw_token, issuer=cfg.jwt_issuer, expected_type=tk.REFRESH)
    except Exception as exc:
        raise UnauthorizedProblem("That session has expired. Please sign in again.") from exc

    jti = str(claims["jti"])
    # `with_for_update`: two truly concurrent *first* rotations of the same still-live token
    # (MINOR 3 of the 2026-09-24 SSO review) would otherwise both read `is_spent = False`,
    # both mint a successor, and fork the family into two live chains — a race no `is_spent`
    # check can see, because neither transaction has committed yet when the other reads. The
    # lock serialises them: the second transaction's `SELECT … FOR UPDATE` blocks until the
    # first commits, then reads the row *with* `rotated_at` already set, and takes the grace or
    # reuse path like any other second presenter would.
    stored = await session.get(RefreshToken, jti, with_for_update=True)
    if stored is None:
        raise UnauthorizedProblem("That session has expired. Please sign in again.")

    grace_hit = False
    if stored.is_spent:
        if stored.revoked_at is None and await _grace_marker_present(
            cfg, jti=jti, rotated_at=stored.rotated_at
        ):
            grace_hit = True
        else:
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
    pinned = _federated_organization_pin(auth_session)
    if pinned is not None and org_id is not None and pinned != org_id:
        # Defence in depth: `switch_organization` is the path that would normally let this
        # diverge, and it already refuses to move a federated session anywhere but its own
        # pin. Reaching here means something else changed `active_org_id`, or minted a refresh
        # token for the wrong organization — treated the same as a stolen token, since a
        # refresh that could cross into an organization the identity provider never vouched
        # for is exactly the takeover this pin exists to close.
        await _revoke_family(session, stored.family, reason="sso_pin_violation")
        log.warning(
            "auth.refresh_pin_violation", session_id=str(auth_session.id), family=stored.family
        )
        await session.commit()
        raise UnauthorizedProblem("That session has expired. Please sign in again.")

    if pinned is not None and not await _pinned_sso_still_valid(
        session,
        directory=directory,
        organization_id=pinned,
        sso_domain=auth_session.sso_domain,
    ):
        # The pin itself (above) still held — this is the periodic re-validation of what made
        # it true in the first place. Either the domain is no longer verified to this
        # organization (released, demoted, lost a re-verification race) or the organization
        # turned its identity provider off — either way, a session this refresh would keep
        # alive was minted by a fact that is no longer true, and continuing to trust it would
        # outlive the very thing that once justified it.
        #
        # The **session row** is ended too, not just this refresh family: revoking only the
        # family left `sessions.revoked_at` NULL, so the session still passed `_live_session`
        # and could go on to mint through `switch_organization`.
        await _revoke_session(session, auth_session.id, reason="sso_no_longer_valid")
        log.warning(
            "auth.refresh_sso_no_longer_valid",
            session_id=str(auth_session.id),
            family=stored.family,
            organization_id=str(pinned),
        )
        await session.commit()
        raise UnauthorizedProblem("That session has expired. Please sign in again.")

    membership = user.membership_for(org_id) if org_id else None
    if membership is None:
        # They were removed from the organization while signed in. Ending the session is
        # the correct outcome — access should not outlive membership.
        await _revoke_family(session, stored.family, reason="membership_revoked")
        await session.commit()
        raise UnauthorizedProblem("Your access to that organization has ended.")

    if grace_hit:
        # Every check above just re-ran against `stored`'s own row — nothing left to write:
        # the rotation that actually spent this token already recorded `rotated_at`, and this
        # presenter mints its own access token rather than reusing anything from that one.
        access_only = await _mint_access_only(
            pair=pair,
            cfg=cfg,
            auth_session=auth_session,
            membership=membership,
            entitlements=entitlements,
            audience=audience,
        )
        await session.commit()
        return access_only

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
    # Written *before* the commit, deliberately, and cleaned up if the commit then fails
    # (MINOR 3 of the 2026-09-24 SSO review asked for the opposite order; this is the one place
    # this review's fix deviates from the letter of a finding to keep its intent — see the
    # module-level note above `_remember_grace_marker` for the race that ordering opens up: the
    # `SELECT … FOR UPDATE` lock above releases the instant this transaction commits, and a
    # second presenter blocked on it can resume, reach `_grace_marker_present` and find no
    # marker yet — an event-loop race between "read the marker" and "this coroutine gets back
    # to writing it" — before this coroutine's own next line runs. Writing it first closes that
    # window; the `try`/`except` is what keeps a failed commit from leaving a marker that
    # describes a rotation Postgres never actually recorded.
    await _remember_grace_marker(cfg, jti=jti)
    try:
        await session.commit()
    except Exception:
        await cache_delete(_grace_marker_key(jti))
        raise
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


async def _revoke_session(session: AsyncSession, session_id: uuid.UUID, *, reason: str) -> None:
    """End a session outright: every refresh token it holds, and the session row itself.

    `_revoke_family` alone ends one refresh chain and leaves `sessions.revoked_at` NULL, which
    is what `_live_session` — and so every route that takes a session id — actually checks.
    Idempotent: a session already revoked keeps its original time and reason.
    """
    now = datetime.now(UTC)
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.session_id == session_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await session.execute(
        update(Session)
        .where(Session.id == session_id, Session.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
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


def _federated_organization_pin(auth_session: Session) -> uuid.UUID | None:
    """The one organization a federated session may ever act for, or `None` for a password
    session, which may move between every organization the person belongs to.

    `auth_session.idp` carries `"oidc:<organization_id>"` (`sso_callback`, below) the moment a
    sign-in was asserted by an external identity provider **for that organization's own
    verified domain**. That assertion proves nothing about any *other* organization the same
    person happens to belong to — the provider that vouched for them in organization A never
    proved anything about organization B — so a session minted that way must not be movable to
    B on the strength of a token minted for A. `saml:` is recognised for the same reason,
    ahead of SAML itself shipping (docs/backlog.md): the prefix is deliberately future-proof.
    """
    prefix, sep, rest = auth_session.idp.partition(":")
    if not sep or prefix not in ("oidc", "saml"):
        return None
    try:
        return uuid.UUID(rest)
    except ValueError:
        return None


def _assert_organization_unpinned_or_matches(
    auth_session: Session, organization_id: uuid.UUID
) -> None:
    """Refuse `switch_organization`/`rotate_refresh` from moving a federated session to any
    organization other than the one it was asserted for. See `_federated_organization_pin`.

    A fresh primary sign-in is the only way into a different organization from here — this
    never tells the caller which organization it is pinned to, or that pinning is even why it
    was refused, for the same reason `switch_organization`'s own membership check does not say
    "no such organization": the answer must not double as an oracle for what a session can
    reach.
    """
    pinned = _federated_organization_pin(auth_session)
    if pinned is not None and pinned != organization_id:
        raise ForbiddenProblem(
            "This session was started through single sign-on and is bound to one "
            "organization. Sign in again to reach a different one."
        )


def _pinned_sso_cache_key(organization_id: uuid.UUID, sso_domain: str) -> str:
    """Per organization **and per domain**: one organization can hold several verified
    domains, and one healthy domain's cached "valid" must never answer for a sibling that has
    since been demoted (nor a demoted sibling's "invalid" for a healthy one). `sso_domain` is
    already `[a-z0-9.-]` — `normalise_sso_domain`'s output — so it is safe in a key as it is."""
    return f"auth:sso_pin_ok:{organization_id}:{sso_domain}"


async def _pinned_sso_still_valid(
    session: AsyncSession,
    *,
    directory: Directory,
    organization_id: uuid.UUID,
    sso_domain: str | None,
) -> bool:
    """Whether a federated session's own pin is still something real, right now.

    Being pinned (`_federated_organization_pin`) only ever proved that a sign-in *once*
    happened through this organization's identity provider, for a domain that was verified
    *then*. `rotate_refresh` mints a fresh access token on every call, often for weeks after
    that original sign-in, and neither fact is permanent: the domain can be released or lose
    its verification (`account`'s own demotion sweep, ADR-0018), and the organization can turn
    its provider off (`save_sso_config(enabled=False)`) without ever touching this session.
    Without this check, a session pinned to an organization that no longer has any working SSO
    path for it would still refresh forever — the pin would keep it out of every *other*
    organization, correctly, but say nothing about whether it still belongs in *this* one.

    **`sso_domain is None` fails closed.** A federated session with no recorded domain cannot
    be re-checked against anything, and a fact that cannot be re-checked must not be assumed
    to still hold. No such session exists in practice — `sso_start` could never succeed for
    a real customer before this slice, so no federated session was ever minted before
    `sessions.sso_domain` existed (migration `0c1a721d4214`) — which is exactly why refusing
    it costs nothing real and leaving it open would only ever be a hole. The answer is not
    cached: there is nothing per-domain to key it by.

    The provider check is `_config_for_domain` — the very lookup `sso_start` uses to route this
    sign-in in the first place — so it honours `allowed_domains` (narrowing a provider to
    exclude a domain ends that domain's existing sessions, not just its new sign-ins) and
    cannot raise when an organization has more than one OIDC row.

    Cached for up to five minutes per organization **and domain** (`platform_core.cache`,
    soft — a Redis outage falls through to the real check below, never to "valid"): this runs
    on every single refresh for every federated session in the platform, and "did this
    organization's SSO just get turned off" does not need a fresher answer than that. A stale
    `True` for a few minutes only delays a revocation that was always going to need the *next*
    refresh anyway; a stale `False` never happens on a cache miss, only ever computed fresh.
    """
    if sso_domain is None:
        return False

    key = _pinned_sso_cache_key(organization_id, sso_domain)
    cached = await cache_get(key)
    if cached is not None:
        return cached == "1"

    resolved = await directory.resolve_domain(sso_domain)
    domain_ok = (
        resolved is not None and resolved.is_usable and resolved.organization_id == organization_id
    )
    idp_ok = False
    if domain_ok:
        # Bypass-then-narrow, same as `sso_start`: `org_idp_configs` is RLS-protected and this
        # organization is already the one this session is pinned to, not the caller's claim.
        await set_tenant(session, organization_id)
        config = await _config_for_domain(session, organization_id, sso_domain)
        idp_ok = config is not None and _usable_for_oidc(config)

    ok = domain_ok and idp_ok
    await cache_set(key, "1" if ok else "0", ttl=300)
    return ok


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


#: `sessions.revoked_reason` when wrong current passwords typed into the password-change form trip
#: the lockout — distinct from sign-in's and enrolment's, so an operator can tell which door was
#: used.
PASSWORD_CHANGE_LOCKOUT_REASON = "password_change_failed_attempts"  # pragma: allowlist secret

EVT_PASSWORD_CHANGE_FAILED = "auth.password_change_failed"  # pragma: allowlist secret


async def change_password(
    session: AsyncSession,
    *,
    cfg: Settings,
    principal: Principal,
    current: str,
    new: str,
    request: Request | None = None,
) -> None:
    """Change the signed-in member's password, ending every session they have.

    If the old password was captured, the sessions it opened are captured too — leaving
    them alive makes the change cosmetic.

    **This is a password oracle unless it is treated as one** (ADR-0023, risk 9, the second review
    round): a signed-in caller can send any candidate as `current` and be told whether it is right,
    and the password is the trust anchor for adding an authenticator. So it is guarded like
    sign-in is:

    1. **Nothing that depends on the answer is checked first.** The new password's own rules
       (`current == new`, the strength rules) are decided *before* the current password is
       looked at. Checked afterwards, `current=new=candidate` was refused for the wrong reason
       when the guess was right (422) and for another when it was wrong (401) — a free
       yes/no on every guess, with nothing counted.
    2. **The lockout is checked before the password**, so a locked account cannot be used to test
       candidates, and a wrong one is **counted** against the same `failed_attempts` /
       `locked_until` as sign-in (`_register_password_failure`). The attempt that trips the lock
       ends the session the guesses came from.
    3. **A wrong password is a 422 `password-incorrect` with field `current_password`, never a
       401.** The shared client answers a 401 on a bearer call by refreshing the token, sending
       the request again and then signing the member out: a mistyped password would be counted
       twice and end the session.
    4. **A failure is audited** (`auth.password_change_failed`: session, reason, whether it
       locked — never the password) and **committed before it is raised**, or the count would be
       rolled back with the request it was counting.

    Runs on a bare session and binds the tenant itself as its first statement, like `step_up` and
    `begin_member_enrolment` — the rolling-back tenant chokepoint would undo the count. The
    session must be a live one of this person: a revoked session's leftover access token can no
    longer change the password.
    """
    session_id = _principal_session_id(principal)

    # 1. Decided by the new password alone, so they say nothing about the current one.
    if current == new:
        message = "The new password must be different from the current one."
        raise BusinessRuleProblem(
            message, errors=_field_error("new_password", message, kind="same_as_current")
        )
    try:
        security.validate_password(new, min_length=cfg.password_min_length)
    except ValidationProblem as exc:
        # `validate_password` names its field `password`, the reset form's; this form's is
        # `new_password`.
        raise ValidationProblem(
            exc.detail,
            errors=[{**error, "field": "new_password"} for error in exc.errors or []],
        ) from exc

    await _bind_tenant(session, cfg, principal.organization_id)
    await _live_session(session, session_id, principal.sub)

    # Serialises concurrent guesses, so the counter and the lock it trips cannot lose an update.
    credential = await session.get(Credential, principal.sub, with_for_update=True)
    if credential is None:
        credential = await get_or_create_credential(session, principal.sub)

    if credential.locked_until and credential.locked_until > datetime.now(UTC):
        raise PasswordLockedProblem(PASSWORD_LOCKED_MESSAGE)

    if not security.verify_password(credential.password_hash, current):
        locked = _register_password_failure(credential, cfg)
        if locked:
            await _revoke_session(session, session_id, reason=PASSWORD_CHANGE_LOCKOUT_REASON)
        # Outcomes and identifiers only: neither password is ever an input to this row.
        record_audit(
            session,
            AuditLog,
            organization_id=principal.organization_id,
            actor_id=principal.sub,
            action=EVT_PASSWORD_CHANGE_FAILED,
            entity="credential",
            entity_id=principal.sub,
            after={
                "session_id": str(session_id),
                "reason": "invalid_current_password",
                "locked": locked,
            },
            request=request,
        )
        log.warning(
            "auth.password_change_failed",
            locked=locked,
            user_id=str(principal.sub),
            organization_id=str(principal.organization_id),
            session_id=str(session_id),
        )
        await session.commit()
        message = "That is not your current password."
        raise PasswordIncorrectProblem(
            message, errors=_field_error("current_password", message, kind="password_incorrect")
        )

    await set_password(session, principal.sub, new, cfg=cfg)
    await revoke_all_for_user(session, principal.sub, reason="password_changed")

    # Against the member's organization, like the failure row above — the two halves of one
    # trail must be findable together. (The outbox event below stays on the nil organization: a
    # credential belongs to a person, not to one of their organizations.)
    record_audit(
        session,
        AuditLog,
        organization_id=principal.organization_id,
        actor_id=principal.sub,
        action="auth.password_changed",
        entity="credential",
        entity_id=principal.sub,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=uuid.UUID(int=0),
        event_type=EVT_PASSWORD_CHANGED,
        aggregate_type="credential",
        aggregate_id=principal.sub,
        payload={"user_id": str(principal.sub)},
    )
    await session.commit()


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


# --------------------------------------------------------------------- second-factor problems
#
# What the member's second-factor endpoints (enrolment, step-up) refuse with. Every one has its
# own RFC 7807 slug, because the UI does something different for each: point at the Security page,
# ask for a code, say "sign in again", show a lockout. **A wrong password or code is 422, never
# 401**: these are bearer-authenticated calls, and the shared client answers a 401 on one by
# refreshing the token and re-sending the *same request* — counting a typo twice — and then
# signing the user out if that fails. 401 is kept for a bearer or session that is genuinely bad,
# where nothing has been counted.


class MfaNotEnrolledProblem(ProblemError):
    """The member has no confirmed second factor, so there is nothing to step up *with*.

    409, with a stable slug the UI keys on to send them to the Security page rather than show a
    dead-end error: the caller's session and permissions are fine, the account state is not.
    """

    status_code = 409
    problem_type = "mfa-not-enrolled"
    title = "Two-factor authentication is not set up"


class MfaUnavailableForSsoProblem(ForbiddenProblem):
    """A session started through an identity provider cannot manage or use a second factor.

    It cannot enrol one either (`_require_primary_session`), and a factor enrolled on some
    other, password sign-in is not something an external issuer's assertion may spend: the same
    reasoning that keeps `sso` out of `amr` as `pwd` (`SSO_AMR`). 403 with its own slug so the
    UI can say "sign in with your password" instead of offering a form that can never succeed.
    """

    problem_type = "mfa-unavailable-for-sso"
    title = "Two-factor authentication is not available for single sign-on sessions"


class MfaReauthenticationRequiredProblem(ForbiddenProblem):
    """The member's factor is confirmed, but was added after this session began and not through
    it, so this session may not spend it (`_factor_usable_in_session`). Nothing is wrong with the
    code; the answer is to sign in again, which starts a session that postdates the factor."""

    problem_type = "mfa-reauthentication-required"
    title = "Sign in again to use your two-factor authentication"


class MfaLockedProblem(ForbiddenProblem):
    """`verify_mfa`'s lockout, verbatim (same status, same message) with a slug of its own."""

    problem_type = "mfa-locked"
    title = "Two-factor authentication is temporarily locked"


class PasswordLockedProblem(ForbiddenProblem):
    """Sign-in's password lockout, verbatim, answered by an endpoint that asks for the password
    again (`begin_member_enrolment`)."""

    problem_type = "password-locked"
    title = "This account is temporarily locked"


class PasswordIncorrectProblem(ProblemError):
    """The password typed into a re-authentication prompt was wrong. Counted against the same
    lockout as sign-in."""

    status_code = 422
    problem_type = "password-incorrect"
    title = "That password is not correct"


class MfaCurrentCodeRequiredProblem(ProblemError):
    """Adding a factor to an account that already has one needs a code from an existing one.
    Refused before the password is even hashed, and not counted: the client has not tried
    anything yet, it has left a field empty."""

    status_code = 422
    problem_type = "mfa-current-code-required"
    title = "A code from your current authenticator is required"


class MfaCodeInvalidProblem(ProblemError):
    """A wrong code. Counted."""

    status_code = 422
    problem_type = "mfa-code-invalid"
    title = "That code is not valid"


class MfaCodeReplayedProblem(ProblemError):
    """A correct code for a 30-second step this factor already spent (typically the one that
    signed the member in a moment ago). Counted like any other refusal; the message tells them
    what to do instead of implying they mistyped."""

    status_code = 422
    problem_type = "mfa-code-replayed"
    title = "That code has already been used"


class AlreadyInOrganizationProblem(ConflictProblem):
    """`switch_organization` to the organization the caller's token is already in. Not a fault
    in the caller's data: a request that could only ever have been made to get a fresh refresh
    token out of an access token — see `switch_organization`."""

    problem_type = "already-in-organization"
    title = "Already in that organization"


def _field_error(field: str, message: str, *, kind: str) -> list[dict[str, str]]:
    """The `errors[]` shape `RequestValidationError` produces, so a form can attach the message
    to the field that caused it whichever layer refused it."""
    return [{"field": field, "code": kind, "message": message}]


def _is_password_session(auth_session: Session) -> bool:
    """A session opened with a password. `local` is the only value `start_session` gives a
    password sign-in; everything else (`oidc:<org>`, `saml:<org>`, whatever comes next) is a
    session an external issuer vouched for. Positive, not `not _federated_organization_pin`: an
    identity provider this code has not heard of yet must fail closed, not be mistaken for a
    password."""
    return auth_session.idp == "local"


async def _bind_tenant(session: AsyncSession, cfg: Settings, organization_id: uuid.UUID) -> None:
    """The tenant chokepoint's two steps (`platform_core.db.get_tenant_session`) for a route
    that has a principal but has to take a bare session — because a wrong password or code must
    be *counted*, and the chokepoint rolls the transaction back on the exception that refuses
    the request. Must be the transaction's first statement; the read-back probe runs outside
    production, exactly as the chokepoint's does."""
    await set_tenant(session, organization_id)
    if not cfg.is_production:
        await assert_tenant(session, organization_id)


def _principal_session_id(principal: Principal) -> uuid.UUID:
    """The session a bearer token names, or the same 401 every dead session gets. A token with no
    `sid`, one that is not a UUID, and an operator token (which is never a member's) are all the
    same answer: this is not a live member session."""
    if principal.session_id is None or principal.audience == OPERATOR_AUDIENCE:
        raise UnauthorizedProblem("That session is no longer valid.")
    try:
        return uuid.UUID(principal.session_id)
    except ValueError as exc:
        raise UnauthorizedProblem("That session is no longer valid.") from exc


async def _require_primary_session(
    session: AsyncSession, session_id: str | None, user_id: uuid.UUID
) -> Session | None:
    """Refuse an MFA-management call that does not come from a **live password session of this
    person**, and return that session.

    Enrolling, confirming or (once it exists) disabling a second factor or regenerating
    recovery codes changes how *every* organization this person belongs to can be reached —
    it is an account-wide credential change, not something scoped to the one organization a
    federated session is pinned to. A session that only ever proved "this organization's
    identity provider vouches for this person, for this one sign-in" has no standing to make
    an account-wide decision like that; a primary (password) sign-in is required first, the
    same way a password change already requires re-entering the current one.

    **Live**, not merely present (B1 of the step-up review): this used to read the row and only
    look at its `idp`, so a revoked or expired session — or one that did not exist — passed. It
    now goes through `_live_session`, which also checks the session belongs to `user_id`.

    `session_id is None` means *no session context*: operator enrolment (a pre-auth token, no
    session yet) and tests that set a factor up directly. It returns `None` and skips every
    check. It is not reachable from a member route, which refuses a token with no `sid` before
    calling in (`_principal_session_id`).
    """
    if session_id is None:
        return None
    try:
        sid = uuid.UUID(session_id)
    except ValueError as exc:
        raise UnauthorizedProblem("That session is no longer valid.") from exc
    auth_session = await _live_session(session, sid, user_id)
    if not _is_password_session(auth_session):
        raise MfaUnavailableForSsoProblem(
            "This requires a password sign-in. Sign in with your password to manage "
            "two-factor authentication."
        )
    return auth_session


async def begin_mfa_enrolment(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    account_email: str,
    cfg: Settings,
    session_id: str | None = None,
) -> tuple[MfaFactor, str, str]:
    """Create an unconfirmed factor and hand back the secret and its QR URI.

    **This authenticates nobody.** It is the primitive: operator enrolment (whose pre-auth token
    is the proof) and tests set factors up through it. A member's request goes through
    `begin_member_enrolment`, which proves the password — and a code from any existing factor —
    first; `tests/test_mfa_enrolment.py` fails if a route calls this directly.

    The secret is sealed with the platform PII envelope before it is ever staged — the same
    `platform_core.pii.seal` this schema already uses for `org_idp_configs.client_secret_enc`.
    A TOTP secret is a durable credential, not a session artefact: whoever reads it can produce
    a valid code forever, so it gets exactly the treatment a password hash or an OIDC client
    secret gets, not the plain bytes an earlier version of this function stored.

    `session_id` is the caller's own session: it must be a live password session of `user_id`
    (`_require_primary_session`), and the factor is recorded against it (`enrolled_session_id`)
    so only that session can confirm it. `None` skips both — operator enrolment has no sessions.
    """
    auth_session = await _require_primary_session(session, session_id, user_id)
    secret = security.generate_totp_secret()
    factor = MfaFactor(
        user_id=user_id,
        type="totp",
        label="Authenticator app",
        enrolled_session_id=auth_session.id if auth_session is not None else None,
        # Set here rather than left to the column's `now()`: that is the *transaction's* start, so
        # a factor begun late in a long transaction would look older than a factor confirmed
        # earlier in it, and `confirm_mfa_enrolment` compares the two (`superseded`).
        created_at=datetime.now(UTC),
    )
    factor.secret_enc, _mask = pii.seal(
        secret, kind="generic", settings=cfg, aad=_factor_aad(user_id)
    )
    session.add(factor)
    # Flushed, not committed: the caller returns the factor's id, and the transaction
    # belongs to the route that opened it.
    await session.flush()
    return factor, secret, security.totp_uri(secret, account=account_email)


#: `sessions.revoked_reason` when wrong passwords or codes typed into the enrolment gate trip a
#: lockout — distinct from sign-in's and step-up's, so an operator can tell which door was used.
ENROLMENT_LOCKOUT_REASON = "enrolment_failed_attempts"

#: How long after it was begun a pending authenticator can still be confirmed (ADR-0023, m1).
#: Scanning a QR code and typing six digits takes a minute; a factor still pending after ten is
#: abandoned, and leaving it confirmable is only an invitation to guess at it.
MFA_PENDING_FACTOR_TTL = timedelta(minutes=10)

#: Wrong codes one pending factor takes at confirm before it is voided and has to be begun again
#: (behind the password, and a code from any existing factor). The count lives on the factor
#: (`MfaFactor.confirm_failed_attempts`): mistyping a code while setting an app up must not lock
#: anybody out of signing in, which the credential's counters would.
MFA_CONFIRM_MAX_ATTEMPTS = 5

EVT_MFA_ENROLMENT_STARTED = "auth.mfa_enrolment_started"
EVT_MFA_ENROLMENT_FAILED = "auth.mfa_enrolment_failed"
EVT_MFA_ENROLMENT_CONFIRMED = "auth.mfa_enrolment_confirmed"
EVT_MFA_RECOVERY_CODES_ISSUED = "auth.mfa_recovery_codes_issued"
EVT_MFA_RECOVERY_CODE_USED = "auth.mfa_recovery_code_used"


async def begin_member_enrolment(
    session: AsyncSession,
    *,
    cfg: Settings,
    directory: Directory,
    principal: Principal,
    password: str,
    current_code: str | None,
    request: Request | None = None,
) -> tuple[MfaFactor, str, str]:
    """Start enrolling an authenticator for the signed-in member — **only** after they prove who
    they are again (ADR-0023, B1).

    An access token proves a sign-in happened, not that the person holding it is the person who
    signed in. Before this, a bearer token alone could enrol *its own* authenticator, confirm it,
    and pass step-up with it — so `amr={"otp"}` protected nothing against a stolen token. Now:

    1. **The current password**, checked with the sign-in verifier and counted against the same
       `failed_attempts` / `locked_until` as sign-in (`_register_password_failure`). Nothing is
       skipped for a member who signed in some other way: an SSO session is refused outright
       (`_require_primary_session`), because there is no password to prove and no standing to
       change an account-wide credential.
    2. **When a factor is already confirmed, a code from it** — a TOTP code, or a recovery code
       (which is then spent, and audited). A member with no factor has only the password to
       offer, and that is all there is to ask; it is the first factor's whole trust anchor. Wrong
       codes count against the same `mfa_failed_attempts` / `mfa_locked_until` as login MFA and
       step-up (`_register_mfa_failure`).

    Either lockout trips also **end the session** the guesses came from. Both counters are per
    person, so an attacker guessing through a stolen bearer can lock the real member out of
    sign-in for `lockout_minutes`; that is the same exposure the login form already has, and is
    recorded as a risk in the ADR rather than avoided.

    **Runs on a bare session and commits for itself**, for the reason `step_up` does: a wrong
    password or code has to be counted even though the request answers an error. The tenant is
    bound first (`_bind_tenant`); the directory is asked *before* that, so a slow account-service
    never holds a database connection.

    Every attempt is audited against the organization — the refusals as `auth.mfa_enrolment_failed`
    and a success as `auth.mfa_enrolment_started` — with the actor, session, IP and request id and
    never a password, code or secret.
    """
    session_id = _principal_session_id(principal)
    gone = UnauthorizedProblem("That session is no longer valid.")

    # Everything that talks to another service happens before any database work.
    user = await directory.get(principal.sub)
    if user is None or not user.is_active:
        raise gone
    if user.membership_for(principal.organization_id) is None:
        raise UnauthorizedProblem("Your access to that organization has ended.")

    await _bind_tenant(session, cfg, principal.organization_id)
    auth_session = await _require_primary_session(session, str(session_id), principal.sub)
    assert auth_session is not None  # `session_id` was given

    # Serialises concurrent attempts, so the counters and the locks they trip cannot lose an
    # update to a race — and two enrolments cannot both spend one code.
    credential = await session.get(Credential, principal.sub, with_for_update=True)
    if credential is None:
        credential = await get_or_create_credential(session, principal.sub)

    now = datetime.now(UTC)
    if credential.locked_until and credential.locked_until > now:
        raise PasswordLockedProblem(PASSWORD_LOCKED_MESSAGE)
    if credential.mfa_locked_until and credential.mfa_locked_until > now:
        raise MfaLockedProblem(MFA_LOCKED_MESSAGE)

    has_factor = (
        await session.execute(
            select(MfaFactor.id)
            .where(MfaFactor.user_id == principal.sub, MfaFactor.confirmed_at.isnot(None))
            .limit(1)
        )
    ).first() is not None
    if has_factor and current_code is None:
        message = "Enter a code from your current authenticator app to add another."
        raise MfaCurrentCodeRequiredProblem(
            message, errors=_field_error("current_code", message, kind="required")
        )

    def _audit_failure(reason: str, *, locked: bool) -> None:
        # Outcomes and identifiers only: the password and the code are never inputs here.
        record_audit(
            session,
            AuditLog,
            organization_id=principal.organization_id,
            actor_id=principal.sub,
            action=EVT_MFA_ENROLMENT_FAILED,
            entity="session",
            entity_id=session_id,
            after={"session_id": str(session_id), "reason": reason, "locked": locked},
            request=request,
        )
        log.warning(
            "auth.mfa_enrolment_failed",
            reason=reason,
            locked=locked,
            user_id=str(principal.sub),
            organization_id=str(principal.organization_id),
            session_id=str(session_id),
        )

    if not security.verify_password(credential.password_hash, password):
        locked = _register_password_failure(credential, cfg)
        if locked:
            await _revoke_session(session, session_id, reason=ENROLMENT_LOCKOUT_REASON)
        _audit_failure("invalid_password", locked=locked)
        # Committed *before* raising: the count, the lock and the audit row must outlive the
        # request that refuses.
        await session.commit()
        message = "That password is not correct."
        raise PasswordIncorrectProblem(
            message, errors=_field_error("password", message, kind="password_incorrect")
        )
    credential.failed_attempts = 0
    credential.locked_until = None

    verified_with = ["password"]
    if has_factor:
        assert current_code is not None
        # `MfaResealPendingProblem` (a legacy factor this deployment cannot read) propagates as
        # the 503 it is, uncounted: our state, not their code.
        method = await _accept_second_factor(session, principal.sub, current_code, cfg=cfg)
        if method is None:
            replayed = await _totp_was_replayed(session, principal.sub, current_code, cfg=cfg)
            locked = await _register_mfa_failure(
                session,
                credential=credential,
                auth_session=auth_session,
                cfg=cfg,
                reason=ENROLMENT_LOCKOUT_REASON,
            )
            _audit_failure("replayed_code" if replayed else "invalid_code", locked=locked)
            await session.commit()
            if replayed:
                message = (
                    "That code was already used. Wait for the next code in your authenticator "
                    "app, then try again."
                )
                raise MfaCodeReplayedProblem(
                    message, errors=_field_error("current_code", message, kind="code_replayed")
                )
            message = "That code is not valid. Check your device's clock and try again."
            raise MfaCodeInvalidProblem(
                message, errors=_field_error("current_code", message, kind="code_invalid")
            )
        credential.mfa_failed_attempts = 0
        credential.mfa_locked_until = None
        verified_with.append("recovery_code" if method == "recovery" else "totp")
        if method == "recovery":
            # A recovery code is spent when it matches, so its use is a recovery-code change.
            record_audit(
                session,
                AuditLog,
                organization_id=principal.organization_id,
                actor_id=principal.sub,
                action=EVT_MFA_RECOVERY_CODE_USED,
                entity="credential",
                entity_id=principal.sub,
                after={"session_id": str(session_id), "purpose": "add_factor"},
                request=request,
            )

    # One pending enrolment at a time: whatever was begun before — abandoned, expired, or begun
    # in another window — is replaced rather than left to accumulate, and can no longer be
    # confirmed. (The operator path has its own cap, `_prune_unconfirmed_factors`.)
    await session.execute(
        delete(MfaFactor).where(
            MfaFactor.user_id == principal.sub, MfaFactor.confirmed_at.is_(None)
        )
    )
    factor, secret, uri = await begin_mfa_enrolment(
        session,
        principal.sub,
        account_email=user.email,
        cfg=cfg,
        session_id=str(session_id),
    )
    record_audit(
        session,
        AuditLog,
        organization_id=principal.organization_id,
        actor_id=principal.sub,
        action=EVT_MFA_ENROLMENT_STARTED,
        entity="mfa_factor",
        entity_id=factor.id,
        after={"session_id": str(session_id), "verified_with": verified_with},
        request=request,
    )
    await session.commit()
    return factor, secret, uri


#: A pre-sealing factor row: the raw base32 secret stored as-is, before `platform_core.pii`
#: wrapped every TOTP secret in an envelope. Recognised by shape, not guessed at random — a
#: `pii.seal` envelope leads with a version byte outside this charset, so it never matches.
_LEGACY_TOTP_SECRET_RE = re.compile(rb"[A-Z2-7]{16,32}")


def _legacy_totp_secret(raw: bytes) -> str | None:
    # `fullmatch`, not `match(...$)`: Python's `$` also matches before a trailing newline,
    # Postgres's does not, and the deploy's SQL count and this check must agree on every row.
    if not _LEGACY_TOTP_SECRET_RE.fullmatch(raw):
        return None
    return raw.decode("ascii")


def _is_refused_plaintext(factor: MfaFactor, cfg: Settings) -> bool:
    """True when `_open_factor_secret` returned `None` for this factor *because* it is a raw
    plaintext row and `cfg.mfa_accept_legacy_plaintext` is off. Exact, not a heuristic: a real
    `pii.seal` envelope leads with a version byte outside the base32 charset, so it can never
    match `_LEGACY_TOTP_SECRET_RE`."""
    return (
        not cfg.mfa_accept_legacy_plaintext
        and factor.secret_enc is not None
        and _legacy_totp_secret(factor.secret_enc) is not None
    )


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
       a box turns that on deliberately (the deploy's one-off reseal does), until
       `bin/reseal-mfa-factors` reports `remaining=0`. Otherwise it is refused and logged — and,
       for a confirmed factor, `_accept_second_factor` answers `MfaResealPendingProblem` instead
       of counting a failure, because the miss may not have been the member's.

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


@dataclass(frozen=True, slots=True)
class ResealResult:
    """What one `reseal_legacy_mfa_factors` pass did. `remaining` is the number of factors that
    are *still* raw plaintext afterwards — what a deploy has to see reach zero before the new
    containers serve — and is not the same as "rows that were not touched": a row that was
    already sealed is neither resealed nor remaining."""

    resealed: int
    remaining: int


async def reseal_legacy_mfa_factors(session: AsyncSession, *, cfg: Settings) -> ResealResult:
    """The one-off deploy step behind `bin/reseal-mfa-factors` (MAJOR 3): force every factor
    with a stored secret through `_open_factor_secret`'s tolerant read, so a row this
    deployment holds from before PII sealing (or sealed before the per-row AAD) is resealed now
    rather than waiting for its account's next sign-in. Idempotent — an already-sealed row just
    opens and is left alone — and safe to run against a database with none.

    Reports what it *left* as well as what it did (R1(b) of the final review): with
    `mfa_accept_legacy_plaintext` off, plaintext rows are refused, so a run without the flag
    used to print "resealed 0" and look like success while every legacy factor was still
    unreadable. `remaining` is the plaintext rows still there after the pass.
    """
    ids = (
        (await session.execute(select(MfaFactor.id).where(MfaFactor.secret_enc.isnot(None))))
        .scalars()
        .all()
    )
    resealed = 0
    remaining = 0
    for factor_id in ids:
        factor = await session.get(MfaFactor, factor_id)
        if factor is None or factor.secret_enc is None:
            continue
        before = factor.secret_enc
        await _open_factor_secret(session, factor, cfg=cfg)
        if factor.secret_enc != before:
            resealed += 1
        if factor.secret_enc is not None and _legacy_totp_secret(factor.secret_enc) is not None:
            remaining += 1
    return ResealResult(resealed=resealed, remaining=remaining)


def _record_enrolment_failure(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    auth_session: Session | None,
    factor_id: uuid.UUID,
    reason: str,
    request: Request | None,
) -> None:
    """One `auth.mfa_enrolment_failed` row for a refused confirmation. Outcomes and identifiers
    only — the code is never an input, and neither is the seed."""
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=user_id,
        action=EVT_MFA_ENROLMENT_FAILED,
        entity="mfa_factor",
        entity_id=factor_id,
        after={
            "session_id": str(auth_session.id) if auth_session is not None else None,
            "reason": reason,
            "locked": False,
        },
        request=request,
    )
    log.warning(
        "auth.mfa_enrolment_failed",
        reason=reason,
        user_id=str(user_id),
        organization_id=str(organization_id),
        session_id=str(auth_session.id) if auth_session is not None else None,
    )


async def confirm_member_enrolment(
    session: AsyncSession,
    *,
    cfg: Settings,
    principal: Principal,
    factor_id: uuid.UUID,
    code: str,
    request: Request | None = None,
) -> list[str]:
    """`POST /auth/mfa/enrol/confirm` for the signed-in member: the session and the organization
    come from the verified token, never from the request, and the tenant is bound first.

    **Bare session, and it commits for itself**, like `begin_member_enrolment` and `step_up`: a
    wrong code is counted against the pending factor and the factor is voided at the cap, and both
    have to outlive the request that answers an error — which the rolling-back tenant chokepoint
    would undo.
    """
    session_id = _principal_session_id(principal)
    await _bind_tenant(session, cfg, principal.organization_id)
    return await confirm_mfa_enrolment(
        session,
        factor_id=factor_id,
        user_id=principal.sub,
        code=code,
        cfg=cfg,
        session_id=str(session_id),
        organization_id=principal.organization_id,
        request=request,
    )


async def confirm_mfa_enrolment(
    session: AsyncSession,
    *,
    factor_id: uuid.UUID,
    user_id: uuid.UUID,
    code: str,
    cfg: Settings,
    session_id: str | None = None,
    organization_id: uuid.UUID | None = None,
    request: Request | None = None,
) -> list[str]:
    """Prove the factor works, turn it on, and — for the member's **first** factor only — issue
    recovery codes. Returns the codes shown to the caller, once; **empty** when the member
    already had a working factor, because their recovery codes are then left exactly as they
    were. **Commits** — on success, and before raising on every refusal that changed something.

    Requiring a working code before enabling is what stops somebody locking themselves out
    with a mis-scanned QR.

    With a `session_id` (every member request), the call must come from a **live password
    session of `user_id`** *and from the session that began this enrolment*
    (`MfaFactor.enrolled_session_id`): a pending factor cannot be finished from anywhere the
    password was not just typed, and a request naming one that belongs to a different session —
    or to nobody's, or to a different person — is the same "No such enrolment" as one that does
    not exist. Without one (operator enrolment does not come through here; tests set factors up)
    none of that is checked.

    **What else makes a pending factor unconfirmable** (ADR-0023, m1 and m2) — each the same
    404, and each *voids* the factor, so it has to be begun again behind the password and, where
    the account has one, a code from an existing authenticator:

    * it is more than `MFA_PENDING_FACTOR_TTL` old (`expired`);
    * a working factor was confirmed after it was begun (`superseded`). `begin` asks for a code
      from an existing factor, but a factor begun while the account had none was asked for only
      the password, and must not turn into a way round that question once one exists;
    * it has taken `MFA_CONFIRM_MAX_ATTEMPTS` wrong codes (`attempts_exhausted`, answered as the
      wrong code it was). Before this, confirm counted nothing, and a pending factor could be
      guessed at for as long as it stayed pending — with ten recovery codes as the prize.

    **The credential row is locked first** (m3), so two confirmations for one person are
    serialised: without it both could read "no other working factor", and each would mint —
    and the second replace — the member's recovery codes. It also serialises the guess count.

    **Recovery codes are not regenerated by adding a second factor** (B1): the old behaviour
    deleted the member's existing codes and handed a fresh set to whoever confirmed — so a
    caller who could only *add* a factor could also silently replace the break-glass credential
    the real member had written down. Only the first factor mints codes. The one exception is
    a factor this deployment cannot read being replaced (`_is_refused_plaintext`): with nothing
    usable left, the new factor is the first, and the old codes are replaced with it. Every
    recovery-code change, and the enrolment itself, is audited against `organization_id`.
    """
    auth_session = await _require_primary_session(session, session_id, user_id)
    audit_org = organization_id if organization_id is not None else PLATFORM_ORG

    credential = await session.get(Credential, user_id, with_for_update=True)
    if credential is None:
        credential = await get_or_create_credential(session, user_id)

    factor = await session.get(MfaFactor, factor_id, populate_existing=True)
    secret = await _open_factor_secret(session, factor, cfg=cfg) if factor is not None else None
    if factor is None or factor.user_id != user_id or secret is None:
        raise NotFoundProblem("No such enrolment")
    if auth_session is not None and factor.enrolled_session_id != auth_session.id:
        raise NotFoundProblem("No such enrolment")
    if factor.confirmed_at is not None:
        # Re-confirming an already-confirmed factor is not a redo of a prior success; it is
        # either a replay or a race, and either way there is nothing left for this call to
        # legitimately do — the factor already works and recovery codes already exist for it.
        raise UnauthorizedProblem("This factor is already confirmed.")

    async def _void(reason: str) -> None:
        """Delete the pending factor, say why, and make both stick before the 404."""
        await session.delete(factor)
        _record_enrolment_failure(
            session,
            organization_id=audit_org,
            user_id=user_id,
            auth_session=auth_session,
            factor_id=factor.id,
            reason=reason,
            request=request,
        )
        await session.commit()

    if datetime.now(UTC) - factor.created_at > MFA_PENDING_FACTOR_TTL:
        await _void("expired")
        raise NotFoundProblem("No such enrolment")

    others = (
        (
            await session.execute(
                select(MfaFactor).where(
                    MfaFactor.user_id == user_id,
                    MfaFactor.id != factor.id,
                    MfaFactor.confirmed_at.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if any(
        other.confirmed_at is not None
        and other.confirmed_at > factor.created_at
        and not _is_refused_plaintext(other, cfg)
        for other in others
    ):
        await _void("superseded")
        raise NotFoundProblem("No such enrolment")

    step = security.verify_totp(secret, code, last_used_step=factor.last_used_step)
    if step is None:
        factor.confirm_failed_attempts += 1
        exhausted = factor.confirm_failed_attempts >= MFA_CONFIRM_MAX_ATTEMPTS
        message = "That code is not valid. Check your device's clock."
        if exhausted:
            message = (
                "That code is not valid, and this setup has been cancelled after too many wrong "
                "codes. Start again."
            )
            await session.delete(factor)
        _record_enrolment_failure(
            session,
            organization_id=audit_org,
            user_id=user_id,
            auth_session=auth_session,
            factor_id=factor.id,
            reason="attempts_exhausted" if exhausted else "invalid_code",
            request=request,
        )
        # Committed *before* raising: the count, the void and the audit row must outlive the
        # request that refuses.
        await session.commit()
        raise MfaCodeInvalidProblem(
            message, errors=_field_error("code", message, kind="code_invalid")
        )

    factor.confirmed_at = datetime.now(UTC)
    factor.last_used_step = step
    factor.last_used_at = datetime.now(UTC)

    # A confirmed legacy-plaintext factor this deployment refuses can only ever get in the way
    # now that a working one exists: it makes a six-digit miss look like "we could not check",
    # and it is a factor nobody can use. Dropped, and audited, at the source rather than left to
    # be worked around in `_accept_second_factor`. Only while acceptance is off — with the
    # legacy flag on it still works, and the reseal will seal it.
    working_others = 0
    for old in others:
        if _is_refused_plaintext(old, cfg):
            record_audit(
                session,
                AuditLog,
                organization_id=audit_org,
                actor_id=user_id,
                action="auth.mfa_factor_legacy_dropped",
                entity="mfa_factor",
                entity_id=old.id,
                request=request,
            )
            await session.delete(old)
        else:
            working_others += 1

    credential.mfa_enforced = True

    # Only the first working factor mints recovery codes. Replacing them on every confirmation
    # let anyone who could merely *add* a factor swap out the set the member had written down.
    codes: list[str] = []
    replaced_previous = False
    if working_others == 0:
        removed = await session.execute(
            delete(MfaRecoveryCode)
            .where(MfaRecoveryCode.user_id == user_id)
            .returning(MfaRecoveryCode.id)
        )
        replaced_previous = bool(removed.all())
        codes = security.generate_recovery_codes()
        for code_value in codes:
            session.add(
                MfaRecoveryCode(user_id=user_id, code_hash=security.hash_recovery_code(code_value))
            )
        record_audit(
            session,
            AuditLog,
            organization_id=audit_org,
            actor_id=user_id,
            action=EVT_MFA_RECOVERY_CODES_ISSUED,
            entity="credential",
            entity_id=user_id,
            # A count and a flag: never the codes themselves.
            after={
                "session_id": str(auth_session.id) if auth_session is not None else None,
                "count": len(codes),
                "replaced_previous": replaced_previous,
            },
            request=request,
        )

    record_audit(
        session,
        AuditLog,
        organization_id=audit_org,
        actor_id=user_id,
        action=EVT_MFA_ENROLMENT_CONFIRMED,
        entity="mfa_factor",
        entity_id=factor.id,
        after={
            "session_id": str(auth_session.id) if auth_session is not None else None,
            "first_factor": working_others == 0,
            "recovery_codes_issued": bool(codes),
        },
        request=request,
    )
    # The platform's one notification hook for this: `core.user.mfa_enrolled` is on
    # notification's `SUBSCRIBABLE_EVENTS`, so a rule can email the member when it fires. No rule
    # is bound to it today — see ADR-0023. The nil organization is deliberate and unchanged: a
    # credential belongs to a person, not to any one of their organizations.
    publish_event(
        session,
        OutboxEvent,
        organization_id=uuid.UUID(int=0),
        event_type=EVT_MFA_ENROLLED,
        aggregate_type="credential",
        aggregate_id=user_id,
        payload={
            "user_id": str(user_id),
            "first_factor": working_others == 0,
            "recovery_codes_issued": bool(codes),
        },
    )
    await session.commit()
    return codes


#: One message for a locked second factor, shared by `verify_mfa` and `step_up` so the two
#: entry points that count against the same `Credential.mfa_locked_until` can never drift apart.
MFA_LOCKED_MESSAGE = (
    "This account is temporarily locked after several failed authentication "
    "attempts. Try again shortly."
)


async def _register_mfa_failure(
    session: AsyncSession,
    *,
    credential: Credential,
    auth_session: Session,
    cfg: Settings,
    reason: str,
) -> bool:
    """Count one wrong second-factor code against the member; lock, and end `auth_session`,
    when that reaches `max_failed_attempts`. Returns whether this call tripped the lock.

    The one implementation behind both `verify_mfa` (the login step) and `step_up` (a signed-in
    member proving a code): they share `Credential.mfa_failed_attempts` / `mfa_locked_until`
    deliberately — a second, separate counter for the same secret would double an attacker's
    guesses. The caller commits (it must, before it raises, or the failure it just counted is
    rolled back with the request) and supplies `reason`, which lands in
    `sessions.revoked_reason` so an operator can tell which door the guesses came through.
    """
    credential.mfa_failed_attempts += 1
    if credential.mfa_failed_attempts < cfg.max_failed_attempts:
        return False
    now = datetime.now(UTC)
    credential.mfa_locked_until = now + timedelta(minutes=cfg.lockout_minutes)
    # m7: restart the count with the lock, so expiry is a real unlock (see the
    # operator-side `_register_operator_mfa_failure`). For a tenant member the lock
    # expiring after `lockout_minutes` *is* the unlock — there is deliberately no
    # org-admin "clear this member's MFA lock" endpoint: it would need a new
    # permission and a cross-service membership check to guard a fifteen-minute wait.
    credential.mfa_failed_attempts = 0
    auth_session.revoked_at = now
    auth_session.revoked_reason = reason
    await _revoke_session_refresh_tokens(session, auth_session.id)
    log.warning("auth.account_locked", user_id=str(credential.user_id), reason=reason)
    return True


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
        raise ForbiddenProblem(MFA_LOCKED_MESSAGE)

    if await _accept_second_factor(session, user_id, code, cfg=cfg) is None:
        await _register_mfa_failure(
            session,
            credential=credential,
            auth_session=auth_session,
            cfg=cfg,
            reason="mfa_failed_attempts",
        )
        await session.commit()
        raise UnauthorizedProblem("That code is not valid")

    credential.mfa_failed_attempts = 0
    credential.mfa_locked_until = None

    if not await _claim_mfa_verified(session, auth_session):
        raise UnauthorizedProblem("Please start signing in again.")
    # `otp` and nothing else: the step-up marker is not something a sign-in can earn
    # (`STEP_UP_AMR`), so a session that presented a code here still needs `step_up` before a
    # reveal route will open for it.
    auth_session.amr = _dedupe_amr([*_session_amr(auth_session), "otp"])

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


class MfaResealPendingProblem(ProblemError):
    """A factor this deployment refuses to read yet — a legacy-plaintext TOTP row awaiting
    `bin/reseal-mfa-factors` — stopped a code that may well have been correct.

    503-class on purpose: it is *our* state that is wrong, not the caller's code, and it must
    not be counted against them (R1 of the final review — without this, every tenant member with
    a pre-sealing factor was locked out on the first deploy, five correct codes and a fifteen
    minute lock). A recovery code still signs them in."""

    status_code = 503
    problem_type = "mfa-reseal-pending"
    title = "Multi-factor re-seal pending"


async def _accept_second_factor(
    session: AsyncSession,
    user_id: uuid.UUID,
    code: str,
    *,
    cfg: Settings,
    allow_recovery: bool = True,
    factors: Sequence[MfaFactor] | None = None,
) -> str | None:
    """A TOTP code, or a single-use recovery code. Returns which kind matched, or `None`.

    `allow_recovery=False` (the member step-up, ADR-0023) takes the recovery loop out of the
    function altogether rather than relying on the caller to have filtered its input: a recovery
    code is a break-glass credential that is *spent* when it matches, and burning one to reveal a
    phone number would be a poor trade the member never agreed to. With it off, this can only
    ever answer `"totp"` or `None`, and can never write to `mfa_recovery_codes`.

    `factors` narrows which of the member's confirmed factors a TOTP code is checked against.
    `None` (login, enrolment) means all of them. The step-up passes only the factors the calling
    session may spend (`_factor_usable_in_session`); a member with a confirmed factor that this
    session may not use therefore gets the plain "no match", not a second chance through it.

    The discriminator — `"totp"` vs `"recovery"` — is not cosmetic: a recovery-code sign-in is
    its own audited action (MINOR 11), because presenting the break-glass code is a materially
    different event from an ordinary authenticator check and an operator reading the audit trail
    needs to be able to tell them apart without decoding anything.

    **Raises `MfaResealPendingProblem`, and does not return `None`, only when a six-digit code
    missed *and there was no factor it could have been checked against***: every confirmed factor
    was a legacy-plaintext row this deployment refuses (`mfa_accept_legacy_plaintext` off). The
    caller counts a `None` as a failed attempt and eventually locks the account; here the code
    may have been right and *we* could not check it, so it must count for nothing.

    The moment any confirmed factor *is* readable — a member who re-enrolled a sealed factor
    while an old plaintext one lingered — a miss is a real miss against a real factor and
    returns `None` like any other, so it counts. (Raising whenever *any* factor was refused made
    every six-digit guess free on such an account: sixty wrong guesses, counter 0, no lock, then
    the right code signed in.) Anything that is not six digits cannot have been a code for a
    factor at all — it was a recovery-code guess — and also returns `None` and counts.
    """
    plaintext_refused = False
    readable = False
    if factors is None:
        stmt = select(MfaFactor).where(
            MfaFactor.user_id == user_id, MfaFactor.confirmed_at.isnot(None)
        )
        factors = (await session.execute(stmt)).scalars().all()
    for factor in factors:
        secret = await _open_factor_secret(session, factor, cfg=cfg)
        if secret is None:
            plaintext_refused = plaintext_refused or _is_refused_plaintext(factor, cfg)
            continue
        readable = True
        step = security.verify_totp(secret, code, last_used_step=factor.last_used_step)
        if step is not None:
            # Remember the step so the same code cannot be replayed inside its window.
            factor.last_used_step = step
            factor.last_used_at = datetime.now(UTC)
            return "totp"

    looks_like_totp = security.looks_like_totp_code(code)
    if allow_recovery and not looks_like_totp:
        # A six-digit input cannot be a recovery code (`xxxx-xxxx-xxxx`), so a TOTP-shaped miss
        # skips the recovery loop — up to ten Argon2 verifies saved per guess.
        recovery_stmt = select(MfaRecoveryCode).where(
            MfaRecoveryCode.user_id == user_id, MfaRecoveryCode.used_at.is_(None)
        )
        for recovery in (await session.execute(recovery_stmt)).scalars().all():
            if security.verify_recovery_code(recovery.code_hash, code):
                recovery.used_at = datetime.now(UTC)
                return "recovery"

    if plaintext_refused and not readable and looks_like_totp:
        log.error("auth.mfa_reseal_pending", user_id=str(user_id))
        raise MfaResealPendingProblem(
            "Multi-factor re-seal pending — use a recovery code to sign in."
        )
    return None


# --------------------------------------------------------------------- member step-up
#
# ADR-0023. A signed-in member proves a one-time code and receives a short-lived, access-only
# token whose `amr` includes `otp` and the step-up marker (`STEP_UP_AMR`) — what the "reveal"
# routes demand (phone numbers, employee PII, unmasked inbox messages). Before this a member
# session could only gain `otp` by presenting a code at *login*, and then kept it for the whole
# session; there was no way to prove a code later, and an organization that never enrolled its
# members' second factor at sign-in could not unmask anything. The marker is what tells a token
# that proved a code *just now* from one whose session proved one at sign-in, days ago.

EVT_STEP_UP_VERIFIED = "auth.step_up_verified"
EVT_STEP_UP_FAILED = "auth.step_up_failed"

#: `sessions.revoked_reason` when guesses made through the step-up door trip the lockout —
#: distinct from `verify_mfa`'s `mfa_failed_attempts`, so an operator reading a revoked session
#: can tell which endpoint the guessing came through.
STEP_UP_LOCKOUT_REASON = "step_up_failed_attempts"


def _factor_usable_in_session(factor: MfaFactor, auth_session: Session) -> bool:
    """May this session spend this confirmed factor at step-up? (ADR-0023, B1.)

    Yes if the factor **predates the session** — confirmed at or before the moment this sign-in
    happened, so every sign-in that could have required it did — or if it was **enrolled through
    this very session**, which `begin_member_enrolment` only allows after the password (and a code
    from any existing factor) was presented. No otherwise: a factor confirmed after the session
    began, by anything other than that gate, is exactly what a stolen bearer token would have
    slipped in. A member in that position signs in again (`mfa-reauthentication-required`).

    Two comparisons and no state, so it can be reasoned about, and it fails closed: a factor
    from before `enrolled_session_id` existed carries `NULL` there and is judged on its
    timestamp alone.
    """
    if factor.enrolled_session_id == auth_session.id:
        return True
    return factor.confirmed_at is not None and factor.confirmed_at <= auth_session.auth_time


async def _totp_was_replayed(
    session: AsyncSession, user_id: uuid.UUID, code: str, *, cfg: Settings
) -> bool:
    """Whether a code `_accept_second_factor` just refused is a *valid* code for a step some
    confirmed factor has already spent — i.e. a replay, not a typo. Only ever called after a
    miss, to choose the message; it changes nothing about whether the miss is counted."""
    stmt = select(MfaFactor).where(MfaFactor.user_id == user_id, MfaFactor.confirmed_at.isnot(None))
    for factor in (await session.execute(stmt)).scalars().all():
        secret = await _open_factor_secret(session, factor, cfg=cfg)
        if secret is None or factor.last_used_step is None:
            continue
        matched = security.verify_totp(secret, code)
        if matched is not None and matched <= factor.last_used_step:
            return True
    return False


async def step_up(
    session: AsyncSession,
    *,
    cfg: Settings,
    pair: KeyPair,
    directory: Directory,
    entitlements: Entitlements,
    principal: Principal,
    code: str,
    request: Request | None = None,
) -> AccessOnlyIssued:
    """Verify a TOTP code for the signed-in member and mint an `otp` access token.

    **Identity comes from the verified bearer token, never the body**: the session is the one
    the token's `sid` names, and must belong to the token's `sub`.

    **TOTP only.** A recovery code is a single-use break-glass credential; it neither works here
    nor is it consumed (`_accept_second_factor(allow_recovery=False)`, and a recovery-shaped
    input is refused before anything is read or counted).

    **Runs on a bare session and commits for itself**, like `verify_mfa`, because the failure it
    counts must survive the request that raises. It is not unbound, though: the tenant GUC is
    the transaction's first statement (`_bind_tenant`), which is the property the chokepoint
    exists to guarantee for a route that holds a principal. Every table it touches is user-keyed
    and outside the RLS policy (migration `b7c41d9e02aa`), so this is the net, not the filter.
    The two upstream calls (directory, entitlements) come *before* it, so no connection is held
    across them.

    **The credential row is locked before the code is checked** (`with_for_update`), serialising
    concurrent guesses — and so concurrent submissions of one correct code: the second blocks,
    then reads `last_used_step` already advanced and is refused as a replay.

    **Only a factor this session may spend is checked** (`_factor_usable_in_session`): one that
    predates the session, or that was enrolled through it by the password-gated enrolment. A
    member whose only factors were added after the session began, some other way, gets
    `mfa-reauthentication-required` and is asked to sign in again.

    **The result is an access token and nothing else**: no refresh token, no `RefreshToken` row,
    no cookie. `otp` and the step-up marker (`STEP_UP_AMR`) are added to the *token's* `amr` and
    are **not** written to `Session.amr`, so neither refresh nor rotation can ever carry them
    into a later token — they exist for `step_up_token_ttl_seconds` and then they are gone. The
    token cannot be revoked inside that window; that is the accepted cost (ADR-0023).

    **Why a marker as well as `otp`**: a session that presented a code at *sign-in* has `otp` in
    `Session.amr`, so all of its tokens already carry it, for up to the session's seven days.
    Confirming an authenticator turns MFA on for every later sign-in, so that is every session
    opened after enrolment. A guard that asked only for `otp` could not tell such a token from a
    step-up one, and a stolen session token would reveal with no code. The reveal routes ask for
    both (`require(..., amr={"otp", "stepup"})`), and this is the only function that mints the
    second.
    """
    session_id = _principal_session_id(principal)
    gone = UnauthorizedProblem("That session is no longer valid.")

    if not security.looks_like_totp_code(code):
        message = (
            "Enter the 6-digit code from your authenticator app. Recovery codes cannot be "
            "used for this."
        )
        raise ValidationProblem(message, errors=_field_error("code", message, kind="totp_required"))

    # Everything that talks to another service happens **before** the database is touched
    # (identity comes from the token, so none of it needs a query first): a slow account-service
    # or product-service must never hold a pooled connection, nor the credential row lock below,
    # and with it every other second-factor attempt this person makes. The price is that a token
    # whose session is already dead still costs two upstream calls before the 401.
    user = await directory.get(principal.sub)
    if user is None or not user.is_active:
        raise gone
    membership = user.membership_for(principal.organization_id)
    if membership is None:
        raise UnauthorizedProblem("Your access to that organization has ended.")
    granted = await entitlements.for_organization(principal.organization_id)

    # The transaction's first statement — the same guarantee `get_tenant_session` gives.
    await _bind_tenant(session, cfg, principal.organization_id)
    auth_session = await _live_session(session, session_id, principal.sub)

    if not _is_password_session(auth_session):
        # Fails closed: anything that is not a plain password session. See the problem's docstring.
        raise MfaUnavailableForSsoProblem(
            "This session was started through single sign-on, which cannot be used to verify "
            "a one-time code. Sign in with your password and two-factor authentication to "
            "reveal protected details."
        )

    credential = await session.get(Credential, principal.sub, with_for_update=True)
    if (
        credential is not None
        and credential.mfa_locked_until is not None
        and credential.mfa_locked_until > datetime.now(UTC)
    ):
        log.warning(
            "auth.step_up_refused",
            reason="locked",
            user_id=str(principal.sub),
            organization_id=str(principal.organization_id),
            session_id=str(session_id),
        )
        raise MfaLockedProblem(MFA_LOCKED_MESSAGE)

    confirmed = (
        (
            await session.execute(
                select(MfaFactor).where(
                    MfaFactor.user_id == principal.sub, MfaFactor.confirmed_at.isnot(None)
                )
            )
        )
        .scalars()
        .all()
    )
    if credential is None or not confirmed:
        raise MfaNotEnrolledProblem(
            "Set up two-factor authentication in your security settings, then try again."
        )
    # B1: a factor this session did not start with and did not enrol through cannot be spent by
    # it. Refused, uncounted and unchecked — the code is not even compared.
    usable = [f for f in confirmed if _factor_usable_in_session(f, auth_session)]
    if not usable:
        log.warning(
            "auth.step_up_refused",
            reason="factor_added_after_session_started",
            user_id=str(principal.sub),
            organization_id=str(principal.organization_id),
            session_id=str(session_id),
        )
        raise MfaReauthenticationRequiredProblem(
            "Two-factor authentication was set up after this session started. Sign in again to "
            "use it here."
        )

    try:
        accepted = await _accept_second_factor(
            session, principal.sub, code, cfg=cfg, allow_recovery=False, factors=usable
        )
    except MfaResealPendingProblem as exc:
        # That problem's own message tells the member to use a recovery code to sign in, which
        # is wrong advice here. Nothing was counted: it is our state, not their code.
        raise MfaResealPendingProblem(
            "Two-factor verification is temporarily unavailable for this account while it is "
            "being upgraded. Try again later."
        ) from exc

    if accepted is None:
        replayed = await _totp_was_replayed(session, principal.sub, code, cfg=cfg)
        locked = await _register_mfa_failure(
            session,
            credential=credential,
            auth_session=auth_session,
            cfg=cfg,
            reason=STEP_UP_LOCKOUT_REASON,
        )
        # Field *names* and outcomes only. The submitted code is never an input to this row.
        record_audit(
            session,
            AuditLog,
            organization_id=principal.organization_id,
            actor_id=principal.sub,
            action=EVT_STEP_UP_FAILED,
            entity="session",
            entity_id=session_id,
            after={
                "session_id": str(session_id),
                "reason": "replayed_code" if replayed else "invalid_code",
                "locked": locked,
            },
            request=request,
        )
        log.warning(
            "auth.step_up_failed",
            reason="replayed_code" if replayed else "invalid_code",
            locked=locked,
            user_id=str(principal.sub),
            organization_id=str(principal.organization_id),
            session_id=str(session_id),
        )
        # Committed *before* raising: the count, the lock and the audit row must outlive the
        # request that refuses, or a guesser would be rate-limited only in the response.
        await session.commit()
        if replayed:
            message = (
                "That code was already used. Wait for the next code in your authenticator "
                "app, then try again."
            )
            raise MfaCodeReplayedProblem(
                message, errors=_field_error("code", message, kind="code_replayed")
            )
        message = "That code is not valid. Check your device's clock and try again."
        raise MfaCodeInvalidProblem(
            message, errors=_field_error("code", message, kind="code_invalid")
        )

    credential.mfa_failed_attempts = 0
    credential.mfa_locked_until = None

    # Never longer than an ordinary access token: a deployment that shortens
    # `access_token_ttl_seconds` below the step-up window must not end up with a stepped-up
    # token that outlives a normal one.
    ttl = min(cfg.step_up_token_ttl_seconds, cfg.access_token_ttl_seconds)
    issued = await _mint_access_only(
        pair=pair,
        cfg=cfg,
        auth_session=auth_session,
        membership=membership,
        entitlements=entitlements,
        audience=principal.audience or "app",
        extra_amr=("otp", STEP_UP_AMR),
        ttl_override=ttl,
        granted=granted,
    )
    record_audit(
        session,
        AuditLog,
        organization_id=principal.organization_id,
        actor_id=principal.sub,
        action=EVT_STEP_UP_VERIFIED,
        entity="session",
        entity_id=session_id,
        after={"session_id": str(session_id), "method": "totp", "ttl_seconds": ttl},
        request=request,
    )
    log.info(
        "auth.step_up_verified",
        user_id=str(principal.sub),
        organization_id=str(principal.organization_id),
        session_id=str(session_id),
    )
    await session.commit()
    return issued


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


@dataclass(frozen=True, slots=True)
class OperatorAccessOnlyIssued:
    """The operator-side `AccessOnlyIssued` — see its doc comment. A grace-window hit on
    `operator_rotate_refresh` mints only this, never a second refresh token."""

    access_token: str
    expires_in: int
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
        amr=_dedupe_amr(_session_amr(auth_session)),
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
        ttl_seconds=_refresh_ttl_seconds(cfg, auth_session),
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


def _mint_operator_access_only(
    *, pair: KeyPair, cfg: Settings, auth_session: Session, staff: StaffAccount
) -> OperatorAccessOnlyIssued:
    """The access-only half of `_issue_operator`, for a grace-window hit on
    `operator_rotate_refresh` — see `AccessOnlyIssued`."""
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
        amr=_dedupe_amr(_session_amr(auth_session)),
        permission_version=0,
        subject_ref=None,
        ttl_seconds=cfg.access_token_ttl_seconds,
        staff=True,
    )
    return OperatorAccessOnlyIssued(
        access_token=access.token,
        expires_in=access.expires_in,
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
    auth_session.amr = _dedupe_amr([*_session_amr(auth_session), "otp"])
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
    auth_session.amr = _dedupe_amr([*_session_amr(auth_session), "otp"])

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
) -> OperatorIssued | OperatorAccessOnlyIssued:
    """Rotate an operator refresh token, resolving the staff account rather than the directory.

    The same theft detection as the tenant path: a spent token ends the whole family, unless
    it was spent moments ago by rotation rather than revocation — the same grace window
    `rotate_refresh` documents, marker-only, mint-access-only (M1 of the 2026-09-24 SSO
    review). A *tenant* refresh token presented here resolves to no `staff_accounts` row and is
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

    jti = str(claims["jti"])
    # `with_for_update`: see `rotate_refresh` — the identical race, forking an operator's
    # session family instead of a tenant's.
    stored = await session.get(RefreshToken, jti, with_for_update=True)
    if stored is None:
        raise UnauthorizedProblem("That session has expired. Please sign in again.")

    grace_hit = False
    if stored.is_spent:
        if stored.revoked_at is None and await _grace_marker_present(
            cfg, jti=jti, rotated_at=stored.rotated_at
        ):
            grace_hit = True
        else:
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

    if grace_hit:
        access_only = _mint_operator_access_only(
            pair=pair, cfg=cfg, auth_session=auth_session, staff=staff
        )
        await session.commit()
        return access_only

    stored.rotated_at = datetime.now(UTC)
    auth_session.last_seen_at = datetime.now(UTC)
    issued = _issue_operator(
        session, pair=pair, cfg=cfg, auth_session=auth_session, staff=staff, family=stored.family
    )
    # Before the commit, with cleanup on failure — see `rotate_refresh`'s identical ordering
    # for why (the row-lock release race a commit-first order would open).
    await _remember_grace_marker(cfg, jti=jti)
    try:
        await session.commit()
    except Exception:
        await cache_delete(_grace_marker_key(jti))
        raise
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


# --------------------------------------------------------------------- self-serve social login
#
# The platform-wide sibling of single sign-on above. Where SSO resolves a provider from a
# DNS-verified domain and an organization's own configuration, this starts from a fixed
# platform registration (`PlatformOAuthProvider`) that exists before any organization does —
# there is no domain to verify and nothing to bind the transaction to. `oauth_social_start`
# is deliberately the thin half: it only ever has to decide which provider to send the
# browser to and remember the PKCE verifier until the callback. What happens with the
# asserted identity — provisioning a new account, honouring an invitation, linking an
# existing one — is `oauth_social_callback`'s job (a later task in this plan).

#: The only providers this flow knows how to send anyone to. Checked before the database is
#: even asked, so a provider name that was never going to be real — typo'd, scraped from
#: another product's URL scheme — gets the same 404 as one the platform simply has not
#: enabled yet, rather than a lookup on an unbounded string.
OAUTH_PROVIDERS = ("google", "microsoft")

#: What the `amr` claim says after a self-serve social sign-in. Its own value rather than
#: reusing `SSO_AMR`: the two flows are asserted by different kinds of issuer (a platform-wide
#: registration here, an organization's own configuration there), and a step-up gate that
#: means to exclude one must not silently also exclude the other because they share a label.
OAUTH_AMR = "oauth"

EVT_OAUTH_SIGNED_IN = "core.session.oauth_signed_in"


@dataclass(frozen=True, slots=True)
class OAuthRedirect:
    """Where to send the browser, and how long the request stays open.

    `SsoRedirect`'s platform-wide sibling: no `organization_slug`/`provider_name` because
    there is no organization to name yet at this point in the flow.
    """

    authorization_url: str
    state: str
    expires_in: int


async def _get_enabled_provider(session: AsyncSession, provider: str) -> PlatformOAuthProvider:
    """The platform's registration for `provider`, or a 404 that does not distinguish
    "not a real provider", "never configured" and "turned off" — the same enumeration
    posture `SsoNotConfiguredProblem` holds for single sign-on, and for the same reason."""
    if provider not in OAUTH_PROVIDERS:
        raise NotFoundProblem("That sign-in method is not available")
    row = await session.get(PlatformOAuthProvider, provider)
    if row is None or not row.enabled:
        raise NotFoundProblem("That sign-in method is not available")
    return row


async def oauth_social_start(
    session: AsyncSession,
    *,
    cfg: Settings,
    store: oidc.StateStore,
    provider: str,
    intent: str,
    invitation_token: str | None = None,
    organization_name: str | None = None,
    slug: str | None = None,
) -> OAuthRedirect:
    """Begin a self-serve Google/Microsoft sign-in.

    `intent` carries which flow the callback must run: `signup` resolves by email first and
    only provisions a new account if that comes back empty (`oauth_social_callback`'s own
    resolve-by-subject-then-email order), `invite` redeems a specific invitation and therefore
    needs the token that names it.

    **`organization_name`/`slug` are accepted here but never required.** A `signup` redirect
    is the *one* button both a returning user and a genuinely new one land on — there is no
    way to tell which this is until the callback's email (and, before that, its
    provider-subject) lookup actually runs, and that happens long after this function returns.
    Requiring a workspace name/slug here would 422 a returning user before the flow ever
    reaches the lookup that would have recognized them. They are threaded through to
    `SocialAuthorizationRequest` purely as a convenience — a frontend that already collected
    them (because *it* expects this to be a new signup) does not have to ask the person again
    after the provider sends the browser back — and it is `_resolve_or_provision_identity`
    (Task 6) that actually enforces they are present, but only once `find_by_email` has
    genuinely come back empty. A frontend that gets this wrong (new signup, no details
    collected) finds out one step later, at the callback, rather than here; that edge case is
    not one a correctly-built frontend ever reaches, since it collects these before ever
    redirecting to Google.

    `intent` and (for `invite`) `invitation_token` are still validated here, before anything is
    written to Redis, so a malformed request never gets as far as occupying a state slot.
    """
    if intent not in ("signup", "invite"):
        raise ValidationProblem("intent must be 'signup' or 'invite'")
    if intent == "invite" and not invitation_token:
        raise ValidationProblem("invitation_token is required for intent=invite")

    config = await _get_enabled_provider(session, provider)

    verifier = oidc.new_verifier()
    pending = oidc.SocialAuthorizationRequest(
        provider=provider,
        intent=intent,
        invitation_token=invitation_token,
        nonce=secrets.token_urlsafe(24),
        code_verifier=verifier,
        created_at=oidc.now_iso(),
        organization_name=organization_name,
        slug=slug,
    )
    state = await store.issue(pending, ttl_seconds=cfg.sso_state_ttl_seconds)

    url = oidc.authorization_url(
        authorization_endpoint=str(config.authorization_endpoint),
        client_id=str(config.client_id),
        redirect_uri=cfg.oauth_social_redirect_uri_template.format(provider=provider),
        scopes=list(oidc.DEFAULT_SCOPES),
        state=state,
        nonce=pending.nonce,
        code_challenge=oidc.challenge_for(verifier),
    )
    log.info("oauth_social.started", provider=provider, intent=intent)
    return OAuthRedirect(authorization_url=url, state=state, expires_in=cfg.sso_state_ttl_seconds)


async def oauth_social_callback(
    session: AsyncSession,
    *,
    cfg: Settings,
    pair: KeyPair,
    directory: Directory,
    provisioning: ProvisioningClient,
    entitlements: Entitlements,
    idp: oidc.IdpClient,
    store: oidc.StateStore,
    provider: str,
    code: str,
    state: str,
    request: Request | None = None,
    audience: str = "app",
) -> LoginOutcome:
    """Finish self-serve Google/Microsoft sign-in: verify, resolve-or-provision, link, mint.

    `sso_callback`'s own mechanics, reused rather than re-derived: consume the single-use
    state, exchange the code with the PKCE verifier that never left this service, verify the
    ID token against the provider's published key, and apply the same ASCII-hardening gate on
    the asserted and the matched email (see that function's comment for the concrete
    IDNA-vs-`CITEXT` mismatch this closes — it applies identically here, nothing about it is
    specific to enterprise SSO). Like `login`, this also checks the resolved account is active,
    requires its second factor if one is enrolled, and requires an active membership before it
    ever mints anything — it is a session-minting path like any other and gets the same floor.

    **Resolved by `(provider, subject)` first, email only as a fallback.** The provider's
    `sub` is the strongest identifier available — stable even if the person's address at the
    provider changes — so an already-linked `ExternalIdentity` is checked *before*
    `_resolve_or_provision_identity` ever runs. Resolving by email first unconditionally would
    let provisioning's side effects (a new organization, an accepted invitation) happen before
    `_link_external_identity` discovers the subject is already bound to a *different* account,
    leaving an orphaned, unreachable workspace behind — exactly the scenario a Google account's
    email change while already linked elsewhere would trigger.

    **No domain binding and no organization pin — the one deliberate difference from
    `sso_callback`.** Enterprise SSO resolves a provider from an organization's own verified
    domain, so the identity it asserts is scoped to that one organization and the resulting
    session is pinned to it (`_federated_organization_pin`, `idp="oidc:<organization_id>"`).
    This flow starts from a fixed, platform-wide registration that exists before any
    organization does — there is no domain to prove and no single organization to bind the
    session to, because the same Google or Microsoft account may belong to several of them.
    `idp` is therefore `oauth:<provider>`, a prefix `_federated_organization_pin` does not
    recognise, so the session comes out unpinned and free to move between every organization
    the person belongs to — exactly like a password session, and exactly the posture spec §6
    calls for. There is consequently no domain re-resolution step either: with no domain
    proved at the start of the flow, there is nothing here to re-check against.
    """
    pending = await store.consume(state, oidc.SocialAuthorizationRequest.from_json)
    if pending.provider != provider:
        raise oidc.SsoExchangeProblem("That sign-in could not be completed. Please start again.")

    config = await _get_enabled_provider(session, provider)
    try:
        client_secret = pii.open_sealed(config.client_secret_enc or b"", settings=cfg)
    except pii.PiiError as exc:
        log.error("oauth.client_secret_unopenable", provider=provider)
        raise UpstreamUnavailableProblem(
            "Single sign-on is temporarily unavailable. Please try again."
        ) from exc

    tokens = await idp.exchange_code(
        str(config.token_endpoint),
        code=code,
        redirect_uri=cfg.oauth_social_redirect_uri_template.format(provider=provider),
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

    # Same ASCII hardening as `sso_callback` — the Kelvin-sign/IDNA-vs-CITEXT class of
    # mismatch applies identically here (see that function's comment for the concrete case):
    # two representations of the same string cannot disagree if only one encoding is ever
    # allowed through, so a non-ASCII assertion is refused outright rather than normalised.
    if not identity.email.isascii():
        log.warning("oauth.callback_refused", reason="non_ascii_identity", provider=provider)
        raise oidc.SsoExchangeProblem("That sign-in could not be completed. Please start again.")

    # Resolve by the provider's own subject first — see the docstring above. Only when this
    # account has never been linked here before does resolution fall through to
    # `_resolve_or_provision_identity`'s email-first lookup (and, from there, provisioning).
    existing_link = (
        await session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.provider == provider, ExternalIdentity.subject == identity.subject
            )
        )
    ).scalar_one_or_none()
    if existing_link is not None:
        user = await directory.get(existing_link.user_id)
        if user is None:  # pragma: no cover - defensive; auth and the directory disagree
            log.error("oauth.callback_linked_user_missing", provider=provider)
            raise oidc.SsoExchangeProblem(
                "That sign-in could not be completed. Please start again."
            )
    else:
        user = await _resolve_or_provision_identity(
            directory=directory,
            provisioning=provisioning,
            intent=pending.intent,
            invitation_token=pending.invitation_token,
            identity=identity,
            full_name_hint=identity.email.split("@", 1)[0],
            organization_name=pending.organization_name,
            slug=pending.slug,
        )

    # Same posture as every other minting path (`_verify_credentials`, `sso_callback`,
    # `switch_organization`, `rotate_refresh`): a deactivated account does not get a session
    # just because this request named it, whichever branch above resolved it.
    if not user.is_active:
        log.warning("oauth.callback_refused", reason="inactive_user", provider=provider)
        raise oidc.SsoExchangeProblem("That sign-in could not be completed. Please start again.")

    # Defence in depth against a resolved/provisioned user whose own stored address does not
    # actually match what the provider asserted — the same posture `sso_callback` holds for
    # `find_by_email`'s result, applied here to whichever path (returning user, fresh
    # signup, invitation) `_resolve_or_provision_identity` took.
    if not user.email.isascii() or user.email.strip().lower() != identity.email.strip().lower():
        log.warning(
            "oauth.callback_refused", reason="matched_user_email_mismatch", provider=provider
        )
        raise oidc.SsoExchangeProblem("That sign-in could not be completed. Please start again.")

    await _link_external_identity(
        session, provider=provider, subject=identity.subject, user=user, email=identity.email
    )

    active = user.active_memberships()
    if not active:
        raise ForbiddenProblem("This account is not a member of any organization")

    auth_session = await start_session(
        session,
        user_id=user.user_id,
        cfg=cfg,
        amr=[OAUTH_AMR],
        idp=f"oauth:{provider}",
        request=request,
    )
    record_audit(
        session,
        AuditLog,
        organization_id=active[0].organization_id,
        actor_id=user.user_id,
        action="auth.oauth_signed_in",
        entity="session",
        entity_id=auth_session.id,
        request=request,
    )

    # Same second-factor gate `login` applies, in the same position (after the session row
    # exists — its id is what the pre-auth token names — but before anything is minted for
    # it): proving a Google or Microsoft identity is not the account's own second factor, and
    # an MFA-enrolled account must not become reachable through a door that skips it. The
    # existing `/auth/mfa/verify` route finishes this the same way it finishes a password
    # sign-in's pre-auth token — no new route or token reason needed.
    credential = await get_or_create_credential(session, user.user_id)
    if await mfa_required(session, credential, user.user_id):
        token = tk.mint_pre_auth(
            pair,
            issuer=cfg.jwt_issuer,
            user_id=user.user_id,
            session_id=auth_session.id,
            reason="mfa",
        )
        await session.commit()
        return LoginOutcome(status="mfa_required", pre_auth_token=token.token)

    membership = resolve_organization(user, org_hint=None)
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
        event_type=EVT_OAUTH_SIGNED_IN,
        aggregate_type="session",
        aggregate_id=auth_session.id,
        payload={
            "user_id": str(user.user_id),
            "session_id": str(auth_session.id),
            "organization_id": str(membership.organization_id),
            "idp": f"oauth:{provider}",
            "amr": [OAUTH_AMR],
        },
    )
    await session.commit()
    log.info(
        "oauth_social.signed_in",
        provider=provider,
        user_id=str(user.user_id),
        session_id=str(auth_session.id),
    )
    return LoginOutcome(status="complete", issued=issued)


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


#: What an IDNA A-label, and nothing else, ever looks like. Enforced on `normalise_sso_domain`'s
#: own output, not just trusted from `idna.encode` succeeding: the round-3 review found that a
#: string `idna` refuses outright used to come back from this function *unchanged* (see the
#: "never raises" note this docstring used to carry), and unchanged means "whatever the caller
#: typed" — including a path segment like `../users/by-email?email=...`, which `directory.py`'s
#: `resolve_domain` then spliced straight into an internal URL. A domain that cannot be made to
#: match this can never have been verified by account-service either, so refusing it here as
#: firmly as a bare 404 costs nothing real.
_DOMAIN_SHAPE = re.compile(r"^[a-z0-9.-]+$")


def normalise_sso_domain(raw: str) -> str:
    """Lower-cased, every trailing dot stripped, IDNA/UTS-46 A-label — the same normalisation
    account-service applies to a domain it claims (`services/account/app/domain.py`), so a
    Unicode email domain and the ASCII form account-service verified and stored compare equal
    no matter which side of the request it arrives on: `sso_start`'s own target, the value
    handed to `directory.resolve_domain`, and the domain half of the ID token's `email` in
    `sso_callback` all go through this one function.

    Never raises, but as of round 3 no longer ever returns anything an IDNA A-label could not
    actually be either: on any failure to encode, or on an encoded result that still is not
    `_DOMAIN_SHAPE`, this returns `""` — the same "nothing entered" a caller already has to
    handle — rather than the raw input verbatim. Returning the raw string used to let a value
    that was never going to resolve to anything (a URL, a query string, a path-traversal
    payload) travel on into `directory.resolve_domain`, which built an internal URL by
    f-string; that call site is now also defended on its own (`HttpDirectory.resolve_domain`
    percent-encodes its path segment regardless), but a domain-shaped value is the contract
    this function exists to guarantee, and it should not depend on every caller downstream
    getting its own defence right too.
    """
    value = raw.strip().lower().rstrip(".")
    if not value:
        return ""
    try:
        encoded = idna.encode(value, uts46=True).decode("ascii")
    except (idna.IDNAError, UnicodeError):
        return ""
    encoded = encoded.rstrip(".")
    if not encoded or not _DOMAIN_SHAPE.match(encoded):
        return ""
    return encoded


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
    target = normalise_sso_domain(domain or (domain_of(email) if email else ""))
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
    5. **The asserted email's domain must be the domain this flow proved, re-resolved as
       still verified to this organization.** An ID token that is otherwise entirely valid
       but asserts an address at some *other* domain is refused here — a provider proving
       ownership of one domain is not a provider anyone should trust to vouch for identities
       at any other. See the check's own comment for the takeover this closes.
    6. **Map to a member.** An address the directory does not know, or knows without an
       active membership in this organization, is refused with one message either way.
    7. **Mint through `issue_for`**, the same call sign-in, org selection, org switching and
       refresh all use.
    """
    pending: oidc.AuthorizationRequest = await store.consume(state)
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

    # **The asserted email must be ASCII — as received, not after any normalisation.** Round 3's
    # finding: Postgres's `CITEXT`
    # (what `find_by_email`, below, ultimately compares against) folds case with the database's
    # collation, and Python's own casefold/IDNA path folds Unicode by a different table entirely
    # — `İ` (U+0130, LATIN CAPITAL LETTER I WITH DOT ABOVE) is one concrete example: Postgres's
    # default collation lower-cases it to plain `i`, while `idna`'s UTS-46 mapping keeps its
    # combining dot and encodes an entirely different A-label. A provider that controls its own
    # oddly-accented domain (verified honestly — the account-takeover check above still holds)
    # could still assert an email whose domain half is *this* function's normal form but whose
    # *Postgres-folded* form matches some unrelated real user's plain-ASCII address, and reach
    # `find_by_email` with it. Refusing any non-ASCII identity outright removes the mismatch
    # entirely: two representations of the same string cannot disagree if only one encoding is
    # ever allowed through. An organization whose real, legitimate directory needs a
    # non-ASCII address is not served by this — but nothing does yet, in either direction, and a
    # generic refusal is the safe default until it does. **Document for operators wiring up an
    # IdP:** the domain half of every asserted email must already be the IDNA A-label
    # (`xn--...`) form, never the Unicode one — the same form account-service stores.
    #
    # **Raw, deliberately — no NFKC.** An earlier version tested `NFKC(identity.email)`, but
    # `find_by_email` and the comparison below both receive the *raw* string, so NFKC turned
    # the gate into a check on a different string than the one actually used: `K` (U+212A,
    # KELVIN SIGN) NFKC-normalises to plain ASCII `K`, sailed through, and then folded to `k`
    # under `lower()`/`casefold()` and matched a real user's plain-ASCII address. The check has
    # to run on exactly the bytes every later step sees.
    if not identity.email.isascii():
        log.warning(
            "sso.callback_refused",
            reason="non_ascii_identity",
            organization_id=str(organization_id),
        )
        raise no_access

    # **The identity provider's own email must belong to the domain this flow proved, not
    # merely to some domain or other.** `verify_id_token` already refuses an email the
    # provider itself does not claim is verified — but "verified by the provider" says
    # nothing about *which* domain the provider is trusted for. Without this check, an
    # organization that configures its own OIDC provider for a domain it controls (proving
    # ownership honestly) can then have that same provider assert *any* address — including
    # one belonging to a real user who is a member of this organization through a completely
    # different, unrelated domain — and mint a session as that person. Requiring the
    # asserted email's domain to equal `pending.domain` closes it: the provider can only ever
    # vouch for identities under the one domain this organization actually proved.
    identity_domain = normalise_sso_domain(domain_of(identity.email))
    if identity_domain != pending.domain:
        log.warning(
            "sso.callback_refused",
            reason="email_domain_mismatch",
            organization_id=str(organization_id),
            expected_domain=pending.domain,
        )
        raise no_access

    # **Re-resolved, not merely trusted from the redirect.** `pending.domain` was proved
    # verified for this organization when the flow *started* — seconds, or a slow browser's
    # worth of minutes, ago. Revoking a domain (`DELETE /domains/{id}` in account-service),
    # or another organization winning the verification race for it in the meantime, must take
    # effect immediately rather than after this in-flight callback still completes on stale
    # information.
    reresolved = await directory.resolve_domain(pending.domain)
    if (
        reresolved is None
        or not reresolved.verified
        or reresolved.organization_id != organization_id
    ):
        log.warning(
            "sso.callback_refused",
            reason="domain_no_longer_verified",
            organization_id=str(organization_id),
            domain=pending.domain,
        )
        raise no_access

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

    # **Defence in depth against `find_by_email` itself matching the wrong person.** The ASCII
    # requirement above closes the concrete IDNA-vs-`CITEXT` mismatch this round found, but
    # this does not depend on that being the only such mismatch that will ever exist — a
    # directory lookup implemented on top of a case-insensitive column is a fold, and every
    # fold has its own edges (locale, width, some future collation change). Three checks, all
    # against the row `find_by_email` actually returned rather than the identity asserted:
    #
    # (0) the **matched user's stored email must itself be ASCII.** The assertion is known to
    # be (the gate above), but the *stored* address is whatever account-service holds — a local
    # part is not restricted to ASCII there — and a stored address containing, say, a Kelvin
    # sign would fold to a plain `k` under `lower()`/`casefold()` and compare equal to an
    # all-ASCII assertion it was never the same string as. A stored address that is not ASCII
    # can never be the address an ASCII-only assertion names, so it is refused, and it also
    # keeps (1) and (2) below from ever folding a non-ASCII string at all.
    if not user.email.isascii():
        log.warning(
            "sso.callback_refused",
            reason="matched_user_not_ascii",
            organization_id=str(organization_id),
        )
        raise no_access
    # (1) the **matched user's own stored email** must itself belong to `pending.domain` — the
    # domain this flow proved, independent of what the identity provider claimed. A lookup that
    # matched someone whose real, stored address is under a different domain entirely proves
    # the fold found the wrong person, not that the right person changed domains.
    if normalise_sso_domain(domain_of(user.email)) != pending.domain:
        log.warning(
            "sso.callback_refused",
            reason="matched_user_domain_mismatch",
            organization_id=str(organization_id),
        )
        raise no_access
    # (2) the **matched user's stored email** must equal the asserted one under a plain ASCII
    # case-insensitive comparison — not "some resolver's idea of equal enough". Both sides are
    # ASCII by (0) and the gate above, so `lower()` here is exactly ASCII lower-casing and
    # cannot itself become a second fold to attack.
    if user.email.strip().lower() != identity.email.strip().lower():
        log.warning(
            "sso.callback_refused",
            reason="matched_user_email_mismatch",
            organization_id=str(organization_id),
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
        session,
        user_id=user.user_id,
        cfg=cfg,
        amr=[SSO_AMR],
        idp=provider,
        sso_domain=pending.domain,
        request=request,
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


async def _resolve_or_provision_identity(
    *,
    directory: Directory,
    provisioning: ProvisioningClient,
    intent: str,
    invitation_token: str | None,
    identity: oidc.VerifiedIdentity,
    full_name_hint: str,
    organization_name: str | None = None,
    slug: str | None = None,
) -> DirectoryUser:
    """Find the account a verified OAuth identity belongs to, or create/join one.

    `intent == "invite"` is handled first and separately from `"signup"`, and deliberately
    does **not** start with the email-first lookup the signup branch below relies on: an
    invitation names a specific organization, and an invitee who already holds a platform
    account elsewhere (or under a different flow) must still actually be added to it
    (`account-service`'s `add_member` already supports attaching an existing user to a
    second organization — the public `/invitations/accept` route works this way too). Short-
    circuiting on `find_by_email` here, the way `"signup"` does, would silently skip the
    invite for exactly the existing-user case it most needs to run for — the invitation would
    stay pending forever with no signal to the frontend. This is safe to do unconditionally
    because the caller (`oauth_social_callback`) already checked `ExternalIdentity(provider,
    subject)` first and only reaches this function when that lookup came back empty — a user
    who is already linked by subject is resolved and signed in before this function is ever
    called, so a repeat accept of the same invitation from the same linked identity never
    gets here at all.

    Only `ConflictProblem` ("That person is already a member of this organization" —
    account-service's `add_member`, raised when the invitation itself was genuine but the
    invitee already holds an active membership in the invited organization) is caught here
    and treated as non-fatal **when the asserted identity already resolves to a directory
    user**: the invitation was valid, only the "add" half is redundant, so the person is
    signed in on their existing account rather than the whole flow failing.

    `NotFoundProblem` is deliberately **not** caught: it is account-service's one opaque
    message for every *invalid*-invitation cause (unknown token, expired, revoked, wrong
    email — see `services.accept_invitation` there), including "already accepted", with no
    way to tell those apart from the exception type alone — that opacity is the whole point
    of the email-match guard (an attacker holding a forged token or asserting the wrong email
    must learn nothing about *why* it failed). Catching it here to special-case "already
    accepted" would catch all the others too, and silently sign someone who merely happens to
    already have an account under the asserted email into that unrelated account instead of
    refusing — spec §3 requires a clearly-wrong invitation to refuse, not resolve quietly.
    So `NotFoundProblem` always propagates unchanged, exactly like every other invalid-
    invitation case; only a true `ConflictProblem` falls back to `find_by_email`, and even
    then only when that lookup actually finds someone — otherwise the conflict itself
    propagates (there is nothing to fall back to).
    """
    if intent == "invite":
        assert invitation_token is not None  # enforced in oauth_social_start
        try:
            provisioned = await provisioning.accept_invitation_federated(
                token=invitation_token, email=identity.email, full_name=full_name_hint
            )
        except ConflictProblem:
            existing = await directory.find_by_email(identity.email)
            if existing is None:
                raise
            return existing
        created = await directory.get(provisioned.user_id)
        if created is None:  # pragma: no cover - defensive; account-service just created this row
            raise UpstreamUnavailableProblem("The directory is unavailable")
        return created

    # intent == "signup": always resolves by email first — the same shortcut enterprise SSO
    # has no need of (it already knows the organization from the domain) but a platform-wide
    # flow does: an email that already has an account never gets a second organization
    # (ADR-0013 §3), it falls through to the returning-user path instead. See spec §3.
    existing = await directory.find_by_email(identity.email)
    if existing is not None:
        return existing

    if not organization_name or not slug:
        raise ValidationProblem("organization_name and slug are required to create a workspace")
    provisioned = await provisioning.signup_federated(
        organization_name=organization_name,
        slug=slug,
        full_name=full_name_hint,
        email=identity.email,
    )

    created = await directory.get(provisioned.user_id)
    if created is None:  # pragma: no cover - defensive; account-service just created this row
        raise UpstreamUnavailableProblem("The directory is unavailable")
    return created


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
