"""Operator (OpsGrids staff) sign-in, and the boundary that keeps it apart from tenant login.

The security property this file exists to hold: an operator token is a *different audience*
carrying `stf`, minted only when a `staff_accounts` row authenticates — and nothing on the
tenant path can produce it, while nothing on this path touches a tenant. The two identities
share a signing key and a verify path and nothing else.

MFA is mandatory for staff (ADR-0003 addendum, 2026-09-23): `services.operator_login` never
returns a token from a password alone, so most tests here drive a staff account through
enrolment (`_enrol_operator_mfa`) once, the way an operator's very first sign-in must.
"""

from __future__ import annotations

from datetime import UTC, datetime

import jwt
import pytest

from app import security, services
from app.bootstrap import bootstrap_staff_owner
from app.models import RefreshToken, Session, StaffAccount
from platform_core.errors import ForbiddenProblem, UnauthorizedProblem
from tests.conftest import (
    ORG_A,
    PASSWORD,
    USER,
    FakeDirectory,
    FakeEntitlements,
    user_in_one_org,
)

pytestmark = pytest.mark.integration

STAFF_EMAIL = "ops@opsgrids.tech"
STAFF_PASSWORD = "operator-correct-horse-9"  # pragma: allowlist secret


async def _make_staff(
    session,
    *,
    email: str = STAFF_EMAIL,
    password: str = STAFF_PASSWORD,
    roles: list[str] | None = None,
    status: str = "active",
) -> StaffAccount:
    staff = StaffAccount(
        email=email,
        password_hash=security.hash_password(password),
        display_name="Ops Person",
        operator_roles=roles if roles is not None else ["owner", "billing_ops"],
        status=status,
    )
    session.add(staff)
    await session.flush()
    return staff


async def _enrol_operator_mfa(
    session, cfg, pair, *, email: str = STAFF_EMAIL, password: str = STAFF_PASSWORD
):
    """Drive a fresh staff account through password + enrolment, the only path to its first
    confirmed factor. Returns the completed `OperatorIssued` plus the recovery codes."""
    outcome = await services.operator_login(
        session, cfg=cfg, pair=pair, email=email, password=password
    )
    assert outcome.status == "mfa_enrollment_required"
    factor, secret, _ = await services.operator_begin_mfa_enrolment(
        session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token
    )
    step = int(datetime.now(UTC).timestamp()) // 30
    issued, codes = await services.confirm_operator_mfa_enrolment(
        session,
        cfg=cfg,
        pair=pair,
        pre_auth_token=outcome.pre_auth_token,
        factor_id=factor.id,
        code=security._totp_at(secret, step),
    )
    return issued, codes, secret, step


async def _sign_in_enrolled_operator(
    session,
    cfg,
    pair,
    *,
    secret: str,
    step: int,
    email: str = STAFF_EMAIL,
    password: str = STAFF_PASSWORD,
):
    """A staff account that already has a confirmed factor: password, then the ordinary
    `mfa_required` step. `secret`/`step` come from the `_enrol_operator_mfa` call that set the
    factor up. Returns the completed `OperatorIssued`."""
    outcome = await services.operator_login(
        session, cfg=cfg, pair=pair, email=email, password=password
    )
    assert outcome.status == "mfa_required"
    return await services.operator_verify_mfa(
        session,
        cfg=cfg,
        pair=pair,
        pre_auth_token=outcome.pre_auth_token,
        # A later step: confirmation already consumed the current one.
        code=security._totp_at(secret, step + 1),
    )


def _verify_via_jwks(token: str, pair, cfg, *, audience: str = "operator") -> dict:
    """Decode the way a product service or gateway would — public key only, no private half."""
    return jwt.decode(
        token,
        pair.public,
        algorithms=["EdDSA"],
        audience=audience,
        issuer=cfg.jwt_issuer,
        options={"require": ["exp", "iat", "sub", "iss"]},
    )


class TestOperatorSignIn:
    async def test_a_password_alone_never_completes_sign_in(self, session, cfg, pair) -> None:
        """MFA is mandatory for staff (ADR-0003 addendum): a correct credential earns a
        next-step token, never an access token."""
        await _make_staff(session)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        assert outcome.status == "mfa_enrollment_required"
        assert outcome.issued is None
        assert outcome.pre_auth_token

    async def test_a_staff_account_gets_an_operator_token_with_stf(
        self, session, cfg, pair
    ) -> None:
        """The headline: staff → a token on the operator audience carrying `stf` and roles,
        earned only once a factor is enrolled, and it verifies against the public key alone
        (JWKS)."""
        await _make_staff(session, roles=["owner", "support"])

        issued, _codes, _secret, _step = await _enrol_operator_mfa(session, cfg, pair)
        assert issued.operator_roles == ["owner", "support"]

        claims = _verify_via_jwks(issued.access_token, pair, cfg)
        assert claims["aud"] == "operator"
        assert claims["stf"] is True
        assert claims["typ"] == "access"
        assert claims["roles"] == ["owner", "support"]
        assert claims["amr"] == ["pwd", "otp"]
        # No membership: an operator holds none, so the gateway resolves no tenant grants.
        assert "mem" not in claims
        # The structural org the gateway requires — the platform sentinel, not a tenant.
        assert claims["act_org"] == str(services.PLATFORM_ORG)

    async def test_a_wrong_password_is_a_generic_401(self, session, cfg, pair) -> None:
        await _make_staff(session)
        with pytest.raises(UnauthorizedProblem) as wrong:
            await services.operator_login(
                session,
                cfg=cfg,
                pair=pair,
                email=STAFF_EMAIL,
                password="not-the-password",  # pragma: allowlist secret
            )
        # Same message an unknown operator gives — no enumeration oracle.
        with pytest.raises(UnauthorizedProblem) as unknown:
            await services.operator_login(
                session, cfg=cfg, pair=pair, email="nobody@opsgrids.tech", password=STAFF_PASSWORD
            )
        assert str(wrong.value) == str(unknown.value) == services.INVALID

    async def test_a_disabled_operator_is_refused(self, session, cfg, pair) -> None:
        await _make_staff(session, status="disabled")
        with pytest.raises(ForbiddenProblem, match="disabled"):
            await services.operator_login(
                session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
            )

    async def test_repeated_failures_lock_the_operator(self, session, cfg, pair) -> None:
        await _make_staff(session)
        for _ in range(cfg.max_failed_attempts):
            with pytest.raises(UnauthorizedProblem):
                await services.operator_login(
                    session,
                    cfg=cfg,
                    pair=pair,
                    email=STAFF_EMAIL,
                    password="wrong",  # pragma: allowlist secret
                )
        with pytest.raises(ForbiddenProblem, match="temporarily locked"):
            await services.operator_login(
                session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
            )

    async def test_the_password_step_alone_opens_a_session_but_no_refresh_token(
        self, session, cfg, pair
    ) -> None:
        """A session exists as soon as the password is right — it is what the pre-auth token
        is bound to — but nothing usable for a refresh exists until MFA completes."""
        staff = await _make_staff(session)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        from sqlalchemy import select as _select

        rows = (
            (await session.execute(_select(Session).where(Session.user_id == staff.id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        refresh_rows = (
            (await session.execute(_select(RefreshToken).where(RefreshToken.user_id == staff.id)))
            .scalars()
            .all()
        )
        assert refresh_rows == []
        assert outcome.pre_auth_token

    async def test_sign_in_records_a_session_and_a_refresh_token(self, session, cfg, pair) -> None:
        staff = await _make_staff(session)
        issued, _codes, _secret, _step = await _enrol_operator_mfa(session, cfg, pair)
        auth_session = await session.get(Session, issued.session_id)
        assert auth_session is not None and auth_session.user_id == staff.id
        stored = await session.get(
            RefreshToken,
            jwt.decode(issued.refresh_token, options={"verify_signature": False})["jti"],
        )
        assert stored is not None and stored.user_id == staff.id


class TestTheBoundary:
    async def test_tenant_login_is_unchanged_and_carries_no_stf(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """The regression guard: a tenant token keeps its product audience and never gains
        an `stf` claim from this change."""
        outcome = await services.login(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            email="sam@example.com",
            password=PASSWORD,
        )
        assert outcome.issued is not None
        claims = jwt.decode(
            outcome.issued.access_token,
            pair.public,
            algorithms=["EdDSA"],
            audience="app",
            issuer=cfg.jwt_issuer,
        )
        assert claims["aud"] == "app"
        assert "stf" not in claims, "a tenant token must never carry the staff claim"

    async def test_a_tenant_user_cannot_obtain_an_operator_token(
        self, session, cfg, pair, registered
    ) -> None:
        """The tenant's email lives in the directory, not in `staff_accounts`; the operator
        path resolves nothing for it. No crossover from tenant to staff."""
        with pytest.raises(UnauthorizedProblem):
            await services.operator_login(
                session, cfg=cfg, pair=pair, email="sam@example.com", password=PASSWORD
            )

    async def test_a_tenant_refresh_token_is_not_valid_on_the_operator_path(
        self, session, cfg, pair, directory, registered
    ) -> None:
        tenant = await services.login(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            email="sam@example.com",
            password=PASSWORD,
        )
        assert tenant.issued is not None
        with pytest.raises(UnauthorizedProblem):
            await services.operator_rotate_refresh(
                session, cfg=cfg, pair=pair, raw_token=tenant.issued.refresh_token
            )

    async def test_an_operator_refresh_token_is_not_valid_on_the_tenant_path(
        self, session, cfg, pair
    ) -> None:
        await _make_staff(session)
        issued, _codes, _secret, _step = await _enrol_operator_mfa(session, cfg, pair)
        # The tenant refresh path resolves the subject through the directory; a staff id is
        # not there, so the token is refused rather than honoured across the boundary.
        with pytest.raises(UnauthorizedProblem):
            await services.rotate_refresh(
                session,
                cfg=cfg,
                pair=pair,
                directory=FakeDirectory([user_in_one_org()]),
                entitlements=FakeEntitlements(),
                raw_token=issued.refresh_token,
            )


class TestOperatorRefreshRotation:
    async def test_refreshing_returns_a_new_operator_token(self, session, cfg, pair) -> None:
        await _make_staff(session, roles=["support"])
        issued, _codes, _secret, _step = await _enrol_operator_mfa(session, cfg, pair)
        rotated = await services.operator_rotate_refresh(
            session, cfg=cfg, pair=pair, raw_token=issued.refresh_token
        )
        assert rotated.refresh_token != issued.refresh_token
        assert rotated.session_id == issued.session_id
        claims = _verify_via_jwks(rotated.access_token, pair, cfg)
        assert claims["stf"] is True and claims["roles"] == ["support"]

    async def test_replaying_a_spent_operator_refresh_kills_the_family(
        self, session, cfg, pair
    ) -> None:
        await _make_staff(session)
        issued, _codes, _secret, _step = await _enrol_operator_mfa(session, cfg, pair)
        captured = issued.refresh_token
        fresh = await services.operator_rotate_refresh(
            session, cfg=cfg, pair=pair, raw_token=captured
        )
        with pytest.raises(UnauthorizedProblem, match="security reasons"):
            await services.operator_rotate_refresh(session, cfg=cfg, pair=pair, raw_token=captured)
        # The legitimate holder's newer token is dead too — the whole family is revoked.
        with pytest.raises(UnauthorizedProblem):
            await services.operator_rotate_refresh(
                session, cfg=cfg, pair=pair, raw_token=fresh.refresh_token
            )


class TestOperatorMfaEnrollment:
    """MFA is mandatory, not opt-in — the property the ADR-0003 addendum adds."""

    async def test_a_never_enrolled_staff_account_cannot_reach_a_token_any_other_way(
        self, session, cfg, pair
    ) -> None:
        await _make_staff(session)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        assert outcome.status == "mfa_enrollment_required"

        # The ordinary MFA-verify step refuses this token — it was minted for enrolment,
        # not for presenting an already-confirmed factor.
        with pytest.raises(UnauthorizedProblem, match="start signing in again"):
            await services.operator_verify_mfa(
                session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token, code="000000"
            )

    async def test_enrolling_completes_the_sign_in_directly(self, session, cfg, pair) -> None:
        await _make_staff(session, roles=["support"])
        issued, codes, _secret, _step = await _enrol_operator_mfa(session, cfg, pair)
        assert issued.operator_roles == ["support"]
        assert len(codes) == 10
        claims = _verify_via_jwks(issued.access_token, pair, cfg)
        assert claims["amr"] == ["pwd", "otp"]

    async def test_a_wrong_enrolment_code_is_refused_and_does_not_confirm_the_factor(
        self, session, cfg, pair
    ) -> None:
        await _make_staff(session)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        factor, _secret, _uri = await services.operator_begin_mfa_enrolment(
            session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token
        )
        with pytest.raises(UnauthorizedProblem, match="not valid"):
            await services.confirm_operator_mfa_enrolment(
                session,
                cfg=cfg,
                pair=pair,
                pre_auth_token=outcome.pre_auth_token,
                factor_id=factor.id,
                code="000000",
            )
        assert factor.confirmed_at is None
        # A second login still asks to enrol, not to verify — nothing was confirmed.
        again = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        assert again.status == "mfa_enrollment_required"

    async def test_a_wrong_enrolment_code_still_writes_an_audit_row(
        self, session, cfg, pair
    ) -> None:
        from sqlalchemy import select as _select

        from app.models import AuditLog

        staff = await _make_staff(session)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        factor, _secret, _uri = await services.operator_begin_mfa_enrolment(
            session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token
        )
        with pytest.raises(UnauthorizedProblem):
            await services.confirm_operator_mfa_enrolment(
                session,
                cfg=cfg,
                pair=pair,
                pre_auth_token=outcome.pre_auth_token,
                factor_id=factor.id,
                code="000000",
            )
        rows = (
            (
                await session.execute(
                    _select(AuditLog).where(
                        AuditLog.actor_id == staff.id,
                        AuditLog.action == "auth.operator_mfa_enrol_failed",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    async def test_once_enrolled_a_second_login_asks_to_verify_not_to_enrol(
        self, session, cfg, pair
    ) -> None:
        await _make_staff(session)
        await _enrol_operator_mfa(session, cfg, pair)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        assert outcome.status == "mfa_required"


class TestOperatorMfaVerify:
    async def test_a_correct_code_completes_the_sign_in(self, session, cfg, pair) -> None:
        await _make_staff(session)
        _issued, _codes, secret, step = await _enrol_operator_mfa(session, cfg, pair)
        done = await _sign_in_enrolled_operator(session, cfg, pair, secret=secret, step=step)
        claims = _verify_via_jwks(done.access_token, pair, cfg)
        assert claims["amr"] == ["pwd", "otp"]

    async def test_a_wrong_code_is_refused_and_audited(self, session, cfg, pair) -> None:
        from sqlalchemy import select as _select

        from app.models import AuditLog

        staff = await _make_staff(session)
        await _enrol_operator_mfa(session, cfg, pair)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        assert outcome.status == "mfa_required"
        with pytest.raises(UnauthorizedProblem, match="not valid"):
            await services.operator_verify_mfa(
                session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token, code="000000"
            )
        rows = (
            (
                await session.execute(
                    _select(AuditLog).where(
                        AuditLog.actor_id == staff.id,
                        AuditLog.action == "auth.operator_mfa_failed",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    async def test_a_recovery_code_completes_the_sign_in(self, session, cfg, pair) -> None:
        await _make_staff(session)
        _issued, codes, _secret, _step = await _enrol_operator_mfa(session, cfg, pair)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        done = await services.operator_verify_mfa(
            session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token, code=codes[0]
        )
        assert done.operator_roles


class TestOperatorAuditTrail:
    async def test_password_verification_and_sign_in_are_both_audited(
        self, session, cfg, pair
    ) -> None:
        from sqlalchemy import select as _select

        from app.models import AuditLog

        staff = await _make_staff(session)
        await _enrol_operator_mfa(session, cfg, pair)
        actions = {
            row.action
            for row in (
                await session.execute(_select(AuditLog).where(AuditLog.actor_id == staff.id))
            )
            .scalars()
            .all()
        }
        assert "auth.operator_password_verified" in actions
        assert "auth.operator_mfa_enrolled" in actions


class TestOperatorMe:
    async def test_reports_staff_identity_and_roles(self, session, client) -> None:
        from app.main import app as fastapi_app
        from platform_core.auth import Principal, get_principal

        staff = await _make_staff(session, roles=["owner", "support"])
        # The route opens its own transaction via `get_tenant_session`; the session must not
        # already have one open when the request runs.
        await session.commit()
        principal = Principal(
            sub=staff.id,
            organization_id=services.PLATFORM_ORG,
            audience="operator",
            roles=["owner", "support"],
            amr=["pwd", "otp"],
        )
        fastapi_app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get("/api/v1/auth/operator/me")
        finally:
            fastapi_app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["staff_id"] == str(staff.id)
        assert body["email"] == STAFF_EMAIL
        assert body["operator_roles"] == ["owner", "support"]
        assert body["amr"] == ["pwd", "otp"]

    async def test_a_product_audience_principal_gets_404(self, client) -> None:
        from app.main import app as fastapi_app
        from platform_core.auth import Principal, get_principal

        principal = Principal(sub=USER, organization_id=ORG_A, audience="app", roles=["MEMBER"])
        fastapi_app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get("/api/v1/auth/operator/me")
        finally:
            fastapi_app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 404


class TestBootstrap:
    async def test_seeding_the_owner_is_idempotent(self, session) -> None:
        first = await bootstrap_staff_owner(
            session,
            email="owner@opsgrids.local",
            password="dev-operator-not-a-real-password",  # pragma: allowlist secret
            display_name="OpsGrids Owner (dev)",
            roles=["owner"],
        )
        again = await bootstrap_staff_owner(
            session,
            email="owner@opsgrids.local",
            password="a-different-one",  # pragma: allowlist secret
            display_name="Someone Else",
            roles=["support"],
        )
        assert again.id == first.id, "re-seeding must not duplicate or rewrite the owner"
        assert again.display_name == "OpsGrids Owner (dev)"
