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

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.config import Settings, settings
from app.deps import DirectoryDep, EntitlementsDep, KeyDep
from app.keys import jwks as build_jwks
from app.models import SCHEMA
from app.schemas import (
    LoginIn,
    LoginResult,
    MeOut,
    MfaEnrolConfirmIn,
    MfaEnrolled,
    MfaEnrolStart,
    MfaRequired,
    MfaVerifyIn,
    OrganizationChoice,
    OrgSelectionRequired,
    PasswordChangeIn,
    PasswordForgotIn,
    PasswordResetIn,
    SelectOrgIn,
    ServiceInfo,
    SwitchOrgIn,
    TokenOut,
)
from platform_core.auth import CurrentPrincipal
from platform_core.db import get_session, get_tenant_session
from platform_core.errors import UnauthorizedProblem

router = APIRouter(tags=["auth"])
wellknown = APIRouter(tags=["discovery"])

# Two kinds of session, decided by whether a principal exists. The pre-organization routes
# — sign-in, the second factor, refresh, logout, password reset — take a bare session:
# there is no tenant to bind before an organization has been chosen. Every route with a
# `CurrentPrincipal` takes `TenantSessionDep`, which opens the request's one transaction
# and binds it to the caller's organization first (ADR-0002 §1); none of them opens its own.
SessionDep = Annotated[AsyncSession, Depends(get_session)]
TenantSessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]


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
        session, principal.sub, account_email=user.email
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
        session, factor_id=payload.factor_id, user_id=principal.sub, code=payload.code
    )
    # Shown once. Stored hashed, and never retrievable again.
    return MfaEnrolled(recovery_codes=codes)


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
