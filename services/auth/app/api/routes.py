"""HTTP surface for the auth service.

The refresh token lives in an httpOnly, path-scoped cookie and never appears in a response
body. Only the short-lived access token is returned, and the SPA keeps it in memory — a
token in `localStorage` is readable by any XSS payload on the page, and a *refresh* token
there is a long-lived credential handed to whoever gets a script in.

`/.well-known/jwks.json` is mounted at the root rather than under `/api/v1`, because it is
a standard discovery path and clients look for it there.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.config import Settings, settings
from app.deps import DirectoryDep, EntitlementsDep, IdpDep, KeyDep, SsoStateDep, require_admin_hop
from app.keys import jwks as build_jwks
from app.models import SCHEMA, OrgIdpConfig, StaffAccount
from app.schemas import (
    LoginIn,
    LoginResult,
    MeOut,
    MfaEnrolConfirmIn,
    MfaEnrolled,
    MfaEnrolStart,
    MfaRequired,
    MfaVerifyIn,
    OperatorEnrollmentCodeIn,
    OperatorEnrollmentCodeOut,
    OperatorLoginIn,
    OperatorLoginResult,
    OperatorMeOut,
    OperatorMfaEnrolConfirmIn,
    OperatorMfaEnrolIn,
    OperatorMfaEnrolled,
    OperatorMfaEnrollmentRequired,
    OperatorMfaRequired,
    OperatorMfaVerifyIn,
    OperatorTokenOut,
    OrganizationChoice,
    OrgSelectionRequired,
    PasswordChangeIn,
    PasswordForgotIn,
    PasswordResetIn,
    SelectOrgIn,
    ServiceInfo,
    SsoConfigIn,
    SsoConfigOut,
    SsoStartOut,
    SwitchOrgIn,
    TokenOut,
)
from platform_core.auth import (
    CurrentPrincipal,
    Principal,
    Scope,
    ScopeContext,
    require,
    require_operator,
)
from platform_core.db import get_session, get_tenant_session
from platform_core.errors import NotFoundProblem, UnauthorizedProblem, ValidationProblem

router = APIRouter(tags=["auth"])
wellknown = APIRouter(tags=["discovery"])

# Two kinds of session, decided by whether a principal exists. The pre-organization routes
# — sign-in, the second factor, refresh, logout, password reset — take a bare session:
# there is no tenant to bind before an organization has been chosen. Every route with a
# `CurrentPrincipal` takes `TenantSessionDep`, which opens the request's one transaction
# and binds it to the caller's organization first (ADR-0002 §1); none of them opens its own.
SessionDep = Annotated[AsyncSession, Depends(get_session)]
TenantSessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
#: Any authenticated operator, no role filter — `/auth/operator/me` answers "who am I", which
#: every operator role may ask.
OperatorPrincipalDep = Annotated[Principal, Depends(require_operator())]


def _set_refresh_cookie(response: Response, cfg: Settings, token: str) -> None:
    """Path-scoped so it is not sent with every API call.

    A credential that rides on requests which do not need it has a wider blast radius
    than it needs.
    """
    response.set_cookie(
        cfg.refresh_cookie_name,
        token,
        max_age=cfg.refresh_token_ttl_seconds,
        httponly=True,
        secure=cfg.environment not in ("local", "test", "ci"),
        samesite="lax",
        path="/api/v1/auth",
        domain=cfg.refresh_cookie_domain,
    )


def _token_out(issued: services.Issued) -> TokenOut:
    return TokenOut(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        organization_id=issued.organization_id,
        membership_id=issued.membership_id,
    )


def _choices(memberships: Any) -> list[OrganizationChoice]:
    return [
        OrganizationChoice(
            organization_id=m.organization_id,
            slug=m.organization_slug,
            name=m.organization_name,
            roles=m.roles,
        )
        for m in (memberships or [])
    ]


def _to_result(outcome: services.LoginOutcome, response: Response, cfg: Settings) -> Any:
    if outcome.status == "complete" and outcome.issued is not None:
        _set_refresh_cookie(response, cfg, outcome.issued.refresh_token)
        return _token_out(outcome.issued)
    if outcome.status == "mfa_required":
        return MfaRequired(pre_auth_token=outcome.pre_auth_token or "")
    return OrgSelectionRequired(
        pre_auth_token=outcome.pre_auth_token or "",
        organizations=_choices(outcome.organizations),
    )


@router.get("/auth/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


@wellknown.get("/.well-known/jwks.json", summary="Public signing keys")
async def jwks(pair: KeyDep) -> dict[str, list[dict[str, Any]]]:
    """Every verifier in the platform fetches this.

    Public by design: it contains only public halves, and a client that cannot reach it
    cannot verify anything at all.
    """
    return build_jwks([pair])


# --------------------------------------------------------------------- sign-in


@router.post("/auth/login", response_model=LoginResult, summary="Sign in")
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    session: SessionDep,
    directory: DirectoryDep,
    entitlements: EntitlementsDep,
    pair: KeyDep,
) -> Any:
    """Exchange credentials for tokens, or for the next step.

    Three outcomes: signed in, needs a second factor, or needs to pick an organization.
    The organization comes from an explicit slug, the host the gateway saw, or a single
    membership — never inferred from the email domain.
    """
    cfg = settings()
    hint = payload.organization or request.headers.get("X-Org-Slug")
    outcome = await services.login(
        session,
        cfg=cfg,
        pair=pair,
        directory=directory,
        entitlements=entitlements,
        email=payload.email,
        password=payload.password,
        org_hint=hint,
        request=request,
    )
    return _to_result(outcome, response, cfg)


@router.post("/auth/select-org", response_model=TokenOut, summary="Finish signing in")
async def select_org(
    payload: SelectOrgIn,
    response: Response,
    session: SessionDep,
    directory: DirectoryDep,
    entitlements: EntitlementsDep,
    pair: KeyDep,
) -> TokenOut:
    cfg = settings()
    issued = await services.select_organization(
        session,
        cfg=cfg,
        pair=pair,
        directory=directory,
        entitlements=entitlements,
        pre_auth_token=payload.pre_auth_token,
        organization_id=payload.organization_id,
    )
    _set_refresh_cookie(response, cfg, issued.refresh_token)
    return _token_out(issued)


@router.post("/auth/switch-org", response_model=TokenOut, summary="Change organization")
async def switch_org(
    payload: SwitchOrgIn,
    response: Response,
    session: TenantSessionDep,
    directory: DirectoryDep,
    entitlements: EntitlementsDep,
    pair: KeyDep,
    principal: CurrentPrincipal,
) -> TokenOut:
    """Move a live session to another organization, keeping the same session id."""
    cfg = settings()
    if principal.session_id is None:
        raise UnauthorizedProblem("That session is no longer valid.")
    issued = await services.switch_organization(
        session,
        cfg=cfg,
        pair=pair,
        directory=directory,
        entitlements=entitlements,
        session_id=uuid.UUID(principal.session_id),
        user_id=principal.sub,
        organization_id=payload.organization_id,
    )
    _set_refresh_cookie(response, cfg, issued.refresh_token)
    return _token_out(issued)


@router.post("/auth/refresh", response_model=TokenOut, summary="Renew the access token")
async def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    directory: DirectoryDep,
    entitlements: EntitlementsDep,
    pair: KeyDep,
) -> TokenOut:
    """Rotate the refresh token and mint a new access token.

    Presenting a token that was already rotated ends the whole family — see
    `services.rotate_refresh` for why that trade is the right one.
    """
    cfg = settings()
    raw = request.cookies.get(cfg.refresh_cookie_name)
    if not raw:
        raise UnauthorizedProblem("No session to refresh")
    issued = await services.rotate_refresh(
        session,
        cfg=cfg,
        pair=pair,
        directory=directory,
        entitlements=entitlements,
        raw_token=raw,
    )
    _set_refresh_cookie(response, cfg, issued.refresh_token)
    return _token_out(issued)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
async def logout(request: Request, session: SessionDep, pair: KeyDep) -> Response:
    """Idempotent: signing out twice is not an error, and neither is a stale cookie."""
    cfg = settings()
    raw = request.cookies.get(cfg.refresh_cookie_name)
    if raw:
        async with session.begin():
            await services.logout(session, pair=pair, cfg=cfg, raw_token=raw)
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    out.delete_cookie(cfg.refresh_cookie_name, path="/api/v1/auth")
    return out


@router.get("/auth/me", response_model=MeOut, summary="Who the caller is")
async def me(
    session: TenantSessionDep, directory: DirectoryDep, principal: CurrentPrincipal
) -> MeOut:
    user = await directory.get(principal.sub)
    if user is None:
        raise UnauthorizedProblem("That session is no longer valid.")
    credential = await services.get_or_create_credential(session, principal.sub)
    return MeOut(
        user_id=user.user_id,
        email=user.email,
        full_name=user.full_name,
        organization_id=principal.organization_id,
        membership_id=principal.membership_id,
        roles=principal.roles,
        amr=principal.amr,
        must_change_password=credential.must_change_password,
        mfa_enabled=credential.mfa_enforced,
        organizations=_choices(user.active_memberships()),
    )


# --------------------------------------------------------------------- MFA


@router.post("/auth/mfa/verify", response_model=LoginResult, summary="Present a second factor")
async def mfa_verify(
    payload: MfaVerifyIn,
    response: Response,
    session: SessionDep,
    directory: DirectoryDep,
    entitlements: EntitlementsDep,
    pair: KeyDep,
) -> Any:
    cfg = settings()
    outcome = await services.verify_mfa(
        session,
        cfg=cfg,
        pair=pair,
        directory=directory,
        entitlements=entitlements,
        pre_auth_token=payload.pre_auth_token,
        code=payload.code,
        org_hint=payload.organization,
    )
    return _to_result(outcome, response, cfg)


@router.post(
    "/auth/mfa/enrol",
    response_model=MfaEnrolStart,
    status_code=status.HTTP_201_CREATED,
    summary="Start setting up an authenticator app",
)
async def mfa_enrol(
    session: TenantSessionDep, directory: DirectoryDep, principal: CurrentPrincipal
) -> MfaEnrolStart:
    user = await directory.get(principal.sub)
    if user is None:
        raise UnauthorizedProblem("That session is no longer valid.")
    factor, secret, uri = await services.begin_mfa_enrolment(
        session,
        principal.sub,
        account_email=user.email,
        cfg=settings(),
        session_id=principal.session_id,
    )
    return MfaEnrolStart(factor_id=factor.id, secret=secret, otpauth_uri=uri)


@router.post(
    "/auth/mfa/enrol/confirm",
    response_model=MfaEnrolled,
    summary="Prove the app works and turn MFA on",
)
async def mfa_confirm(
    payload: MfaEnrolConfirmIn, session: TenantSessionDep, principal: CurrentPrincipal
) -> MfaEnrolled:
    codes = await services.confirm_mfa_enrolment(
        session,
        factor_id=payload.factor_id,
        user_id=principal.sub,
        code=payload.code,
        cfg=settings(),
        session_id=principal.session_id,
    )
    # Shown once. Stored hashed, and never retrievable again.
    return MfaEnrolled(recovery_codes=codes)


# --------------------------------------------------------------------- single sign-on
#
# Two public routes and two administrative ones. The public pair takes a bare session for the
# same reason sign-in does — there is no token yet, so there is no principal to bind a tenant
# from — but unlike sign-in they do bind one: `services.sso_start` resolves the organization
# from the DNS-verified domain and binds the transaction to it before reading
# `org_idp_configs`, which is under row-level security. Bypass-then-narrow, from a verified
# fact rather than from the request (ADR-0002, path 4).

SsoRead = Annotated[ScopeContext, Depends(require("core.identity.sso.read", minimum=Scope.ORG))]
SsoWrite = Annotated[ScopeContext, Depends(require("core.identity.sso.write", minimum=Scope.ORG))]


def _sso_config_out(row: OrgIdpConfig) -> SsoConfigOut:
    """Shape a configuration for the console. The secret is not in the output type at all."""
    return SsoConfigOut(
        id=row.id,
        protocol=row.protocol,
        display_name=row.display_name,
        enabled=row.enabled,
        issuer=row.issuer,
        client_id=row.client_id,
        client_secret_set=row.client_secret_enc is not None,
        discovery_url=row.discovery_url,
        authorization_endpoint=row.authorization_endpoint,
        token_endpoint=row.token_endpoint,
        jwks_uri=row.jwks_uri,
        scopes=list(row.scopes or []),
        allowed_domains=list(row.allowed_domains or []),
        enforce_sso=row.enforce_sso,
        jit_provisioning=row.jit_provisioning,
    )


@router.get(
    "/auth/sso/start",
    response_model=SsoStartOut,
    summary="Begin single sign-on for an email domain",
)
async def sso_start(
    session: SessionDep,
    directory: DirectoryDep,
    store: SsoStateDep,
    email: Annotated[str | None, Query(max_length=320)] = None,
    domain: Annotated[str | None, Query(max_length=253)] = None,
) -> SsoStartOut:
    """Resolve the identity provider for a domain and hand back its authorization URL.

    The domain is what selects a *provider*, and only a domain the organization proved it
    owns (ADR-0000 §1). Every way this can fail answers the same 404 with the same message,
    so it cannot be used to enumerate which domains, organizations or configurations exist.
    """
    if not email and not domain:
        raise ValidationProblem("Provide either an email address or a domain")
    redirect = await services.sso_start(
        session, cfg=settings(), directory=directory, store=store, email=email, domain=domain
    )
    return SsoStartOut(
        authorization_url=redirect.authorization_url,
        state=redirect.state,
        expires_in=redirect.expires_in,
        organization_slug=redirect.organization_slug,
        provider_name=redirect.provider_name,
    )


@router.get("/auth/sso/callback", response_model=TokenOut, summary="Finish single sign-on")
async def sso_callback(
    request: Request,
    response: Response,
    session: SessionDep,
    directory: DirectoryDep,
    entitlements: EntitlementsDep,
    idp: IdpDep,
    store: SsoStateDep,
    pair: KeyDep,
    code: Annotated[str, Query(min_length=1, max_length=2048)],
    state: Annotated[str, Query(min_length=1, max_length=512)],
) -> TokenOut:
    """Where the identity provider sends the browser back.

    Returns exactly what `POST /auth/login` returns on success — the same `TokenOut`, the
    same httpOnly refresh cookie, a token minted by the same `issue_for`. That is deliberate:
    a client should not be able to tell how somebody signed in from the shape of what it got,
    because anything that branched on it would be a second code path to keep correct.

    The redirect URI registered with the provider is this path, and it comes from
    configuration (`sso_redirect_uri`) rather than from anything in the request — a redirect
    target a caller can influence is how an authorization code ends up somewhere else.
    """
    cfg = settings()
    issued = await services.sso_callback(
        session,
        cfg=cfg,
        pair=pair,
        directory=directory,
        entitlements=entitlements,
        idp=idp,
        store=store,
        code=code,
        state=state,
        request=request,
    )
    _set_refresh_cookie(response, cfg, issued.refresh_token)
    return _token_out(issued)


@router.get(
    "/auth/sso/config",
    response_model=SsoConfigOut,
    summary="This organization's identity provider",
)
async def sso_config(session: TenantSessionDep, ctx: SsoRead) -> SsoConfigOut:
    row = await services.get_sso_config(session, ctx.organization_id)
    if row is None:
        raise NotFoundProblem("No identity provider is configured for this organization")
    return _sso_config_out(row)


@router.put(
    "/auth/sso/config",
    response_model=SsoConfigOut,
    summary="Configure the identity provider",
)
async def save_sso_config(
    payload: SsoConfigIn,
    request: Request,
    session: TenantSessionDep,
    idp: IdpDep,
    ctx: SsoWrite,
) -> SsoConfigOut:
    """Create or replace the OIDC configuration.

    Whoever holds `core.identity.sso.write` decides which external issuer may assert who
    their colleagues are, and can turn password sign-in off for everyone — which is why it is
    ORG-scoped and, in the catalogue, marked dangerous.
    """
    row = await services.save_sso_config(
        session,
        cfg=settings(),
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        display_name=payload.display_name,
        issuer=payload.issuer,
        client_id=payload.client_id,
        client_secret=payload.client_secret,
        discovery_url=payload.discovery_url,
        authorization_endpoint=payload.authorization_endpoint,
        token_endpoint=payload.token_endpoint,
        jwks_uri=payload.jwks_uri,
        scopes=payload.scopes,
        allowed_domains=payload.allowed_domains,
        enabled=payload.enabled,
        enforce_sso=payload.enforce_sso,
        idp=idp,
        request=request,
    )
    return _sso_config_out(row)


# --------------------------------------------------------------------- passwords


@router.post(
    "/auth/password/change",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change your password",
)
async def change_password(
    payload: PasswordChangeIn,
    request: Request,
    session: TenantSessionDep,
    principal: CurrentPrincipal,
) -> Response:
    await services.change_password(
        session,
        cfg=settings(),
        user_id=principal.sub,
        current=payload.current_password,
        new=payload.new_password,
        request=request,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------- operator staff
#
# The pre-auth operator sign-in. Deliberately under the `/api/v1/auth` prefix (not a new
# `/api/v1/admin/...` surface) so it rides the auth route both gateways already carry and
# stays inside the "public surface is only authentication" invariant the operator gateway
# asserts. It takes a bare session and no principal — there is no token yet. The token it
# returns is only accepted on `admin-gateway`, by audience.


def _operator_token_out(issued: services.OperatorIssued) -> OperatorTokenOut:
    return OperatorTokenOut(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        operator_roles=issued.operator_roles,
    )


@router.post(
    "/auth/operator/login",
    response_model=OperatorLoginResult,
    summary="Operator (OpsGrids staff) sign in — always stops for a second factor",
    dependencies=[Depends(require_admin_hop)],
)
async def operator_login(
    payload: OperatorLoginIn,
    request: Request,
    session: SessionDep,
    pair: KeyDep,
) -> OperatorMfaRequired | OperatorMfaEnrollmentRequired:
    """Authenticate a staff account. Never returns a token from a password alone.

    A wrong credential answers 401 with the same generic message the tenant login uses, so
    this endpoint is not an operator-account enumeration oracle. A correct one always
    returns one of two "keep going" answers — MFA is mandatory for staff (ADR-0003
    addendum) — never a completed sign-in.
    """
    cfg = settings()
    outcome = await services.operator_login(
        session, cfg=cfg, pair=pair, email=payload.email, password=payload.password, request=request
    )
    if outcome.status == "mfa_enrollment_required":
        return OperatorMfaEnrollmentRequired(pre_auth_token=outcome.pre_auth_token or "")
    return OperatorMfaRequired(pre_auth_token=outcome.pre_auth_token or "")


@router.post(
    "/auth/operator/mfa/enrol",
    response_model=MfaEnrolStart,
    status_code=status.HTTP_201_CREATED,
    summary="Start setting up an operator's authenticator app — the only reachable step "
    "until one is enrolled",
    dependencies=[Depends(require_admin_hop)],
)
async def operator_mfa_enrol(
    payload: OperatorMfaEnrolIn, request: Request, session: SessionDep, pair: KeyDep
) -> MfaEnrolStart:
    cfg = settings()
    factor, secret, uri = await services.operator_begin_mfa_enrolment(
        session,
        cfg=cfg,
        pair=pair,
        pre_auth_token=payload.pre_auth_token,
        enrollment_code=payload.enrollment_code,
        request=request,
    )
    return MfaEnrolStart(factor_id=factor.id, secret=secret, otpauth_uri=uri)


@router.post(
    "/auth/operator/mfa/enrol/confirm",
    response_model=OperatorMfaEnrolled,
    summary="Prove the app works — completes the sign-in that started enrolment",
    dependencies=[Depends(require_admin_hop)],
)
async def operator_mfa_enrol_confirm(
    payload: OperatorMfaEnrolConfirmIn,
    request: Request,
    response: Response,
    session: SessionDep,
    pair: KeyDep,
) -> OperatorMfaEnrolled:
    cfg = settings()
    issued, codes = await services.confirm_operator_mfa_enrolment(
        session,
        cfg=cfg,
        pair=pair,
        pre_auth_token=payload.pre_auth_token,
        factor_id=payload.factor_id,
        code=payload.code,
        request=request,
    )
    _set_refresh_cookie(response, cfg, issued.refresh_token)
    return OperatorMfaEnrolled(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        operator_roles=issued.operator_roles,
        recovery_codes=codes,
    )


@router.post(
    "/auth/operator/mfa/verify",
    response_model=OperatorTokenOut,
    summary="Present an operator's second factor",
    dependencies=[Depends(require_admin_hop)],
)
async def operator_mfa_verify(
    payload: OperatorMfaVerifyIn,
    request: Request,
    response: Response,
    session: SessionDep,
    pair: KeyDep,
) -> OperatorTokenOut:
    cfg = settings()
    issued = await services.operator_verify_mfa(
        session,
        cfg=cfg,
        pair=pair,
        pre_auth_token=payload.pre_auth_token,
        code=payload.code,
        request=request,
    )
    _set_refresh_cookie(response, cfg, issued.refresh_token)
    return _operator_token_out(issued)


@router.get(
    "/auth/operator/me",
    response_model=OperatorMeOut,
    summary="Who this operator is",
    dependencies=[Depends(require_admin_hop)],
)
async def operator_me(session: TenantSessionDep, operator: OperatorPrincipalDep) -> OperatorMeOut:
    """Goes through the same tenancy chokepoint as every other principal-bearing route.

    Harmless rather than meaningful for an operator: `get_tenant_session` binds the
    transaction to `principal.organization_id`, which for an operator token is the platform
    sentinel (`services.PLATFORM_ORG`) — there is no tenant row here to protect, `auth`
    carries no row-level security at all, but going through the one chokepoint every
    principal-bearing route uses is one fewer shape for this module to special-case.
    """
    staff = await session.get(StaffAccount, operator.sub)
    if staff is None:
        raise UnauthorizedProblem("That session is no longer valid.")
    return OperatorMeOut(
        staff_id=staff.id,
        email=staff.email,
        display_name=staff.display_name,
        operator_roles=list(staff.operator_roles),
        amr=operator.amr,
    )


#: The operator bootstrap: only an `owner` may hand another staff account its enrolment code.
OperatorOwnerDep = Annotated[Principal, Depends(require_operator(roles={"owner"}))]


@router.post(
    "/auth/operator/enrollment-codes",
    response_model=OperatorEnrollmentCodeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a staff account's out-of-band MFA enrolment code (owner only)",
    dependencies=[Depends(require_admin_hop)],
)
async def issue_operator_enrollment_code(
    payload: OperatorEnrollmentCodeIn,
    request: Request,
    session: TenantSessionDep,
    operator: OperatorOwnerDep,
) -> OperatorEnrollmentCodeOut:
    """A password alone must never be enough to reach MFA enrolment (ADR-0003 addendum): this
    is the audited, in-product way an `owner` hands a colleague — or a newly provisioned staff
    account — the out-of-band code `POST /auth/operator/mfa/enrol` also requires. For the very
    first owner, with nobody yet to issue one, `bin/seed-staff` prints one instead; for staff
    accounts that predate this rule, `bin/issue-enrollment-codes` is the one-off deploy step.
    """
    code = await services.issue_staff_enrollment_code(
        session, staff_id=payload.staff_id, actor_id=operator.sub, request=request
    )
    return OperatorEnrollmentCodeOut(
        staff_id=payload.staff_id,
        enrollment_code=code,
        expires_in_minutes=services.ENROLLMENT_CODE_TTL_MINUTES,
    )


@router.post(
    "/auth/operator/staff/{staff_id}/mfa:reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Wipe a colleague's MFA after a lost device (owner only)",
    dependencies=[Depends(require_admin_hop)],
)
async def reset_operator_staff_mfa(
    staff_id: uuid.UUID,
    request: Request,
    session: TenantSessionDep,
    operator: OperatorOwnerDep,
) -> Response:
    """The lost-device path (MINOR 5 of the 2026-09-24 security review): deletes the target's
    factors and recovery codes, revokes their live sessions and refresh tokens, and audits the
    act. Refuses to target the caller — resetting one's own MFA is not what this route is for.
    Once this has run, `POST /auth/operator/enrollment-codes` issues the fresh out-of-band code
    the account's next enrolment needs.
    """
    await services.reset_staff_mfa(
        session, target_staff_id=staff_id, actor_id=operator.sub, request=request
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/auth/operator/refresh",
    response_model=OperatorTokenOut,
    summary="Renew an operator access token",
    dependencies=[Depends(require_admin_hop)],
)
async def operator_refresh(
    request: Request, response: Response, session: SessionDep, pair: KeyDep
) -> OperatorTokenOut:
    cfg = settings()
    raw = request.cookies.get(cfg.refresh_cookie_name)
    if not raw:
        raise UnauthorizedProblem("No session to refresh")
    issued = await services.operator_rotate_refresh(session, cfg=cfg, pair=pair, raw_token=raw)
    _set_refresh_cookie(response, cfg, issued.refresh_token)
    return _operator_token_out(issued)


@router.post(
    "/auth/operator/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Operator sign out",
    dependencies=[Depends(require_admin_hop)],
)
async def operator_logout(request: Request, session: SessionDep, pair: KeyDep) -> Response:
    """Idempotent. Shares `services.logout`, which touches only the session and refresh
    tables — no directory, so it ends an operator session as cleanly as a tenant one."""
    cfg = settings()
    raw = request.cookies.get(cfg.refresh_cookie_name)
    if raw:
        async with session.begin():
            await services.logout(session, pair=pair, cfg=cfg, raw_token=raw)
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    out.delete_cookie(cfg.refresh_cookie_name, path="/api/v1/auth")
    return out


@router.post(
    "/auth/password/forgot",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a reset link",
)
async def forgot_password(
    payload: PasswordForgotIn, request: Request, session: SessionDep, directory: DirectoryDep
) -> Response:
    """Always accepted, whether or not the address exists.

    A 404 here would tell whoever is asking which addresses are real, without them
    guessing a single password.
    """
    async with session.begin():
        await services.request_password_reset(
            session, cfg=settings(), directory=directory, email=payload.email, request=request
        )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/auth/password/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set a new password from a reset link",
)
async def reset_password(payload: PasswordResetIn, session: SessionDep) -> Response:
    async with session.begin():
        await services.confirm_password_reset(
            session, cfg=settings(), raw_token=payload.token, new_password=payload.new_password
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
