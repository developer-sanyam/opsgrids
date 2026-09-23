"""Operator (OpsGrids staff) sign-in, and the boundary that keeps it apart from tenant login.

The security property this file exists to hold: an operator token is a *different audience*
carrying `stf`, minted only when a `staff_accounts` row authenticates — and nothing on the
tenant path can produce it, while nothing on this path touches a tenant. The two identities
share a signing key and a verify path and nothing else.

MFA is mandatory for staff (ADR-0003 addendum, 2026-09-23): `services.operator_login` never
returns a token from a password alone, so most tests here drive a staff account through
enrolment (`_enrol_operator_mfa`) once, the way an operator's very first sign-in must. A
password alone is also not enough to *reach* enrolment: an out-of-band enrolment code is
required too (a later security review found that a password-only enrolment could hijack an
already-enrolled account — see `TestEnrolmentCannotBeHijacked`), so the fixtures here issue one
the way `bin/seed-staff` or an owner's audited request would.
"""

from __future__ import annotations

from datetime import UTC, datetime

import jwt
import pytest
from sqlalchemy import select

from app import security, services
from app.bootstrap import bootstrap_staff_owner
from app.models import AuditLog, RefreshToken, Session, StaffAccount, StaffEnrollmentCode
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


async def _issue_code(session, staff_id) -> str:
    """The out-of-band enrolment code, issued the way `bin/seed-staff` issues the first
    owner's — no operator actor, since this stands in for a step outside the API."""
    return await services.issue_staff_enrollment_code(session, staff_id=staff_id, actor_id=None)


async def _enrol_operator_mfa(
    session, cfg, pair, *, email: str = STAFF_EMAIL, password: str = STAFF_PASSWORD
):
    """Drive a fresh staff account through password + enrolment, the only path to its first
    confirmed factor. Returns the completed `OperatorIssued` plus the recovery codes."""
    outcome = await services.operator_login(
        session, cfg=cfg, pair=pair, email=email, password=password
    )
    assert outcome.status == "mfa_enrollment_required"
    staff = (
        await session.execute(select(StaffAccount).where(StaffAccount.email == email))
    ).scalar_one()
    code = await _issue_code(session, staff.id)
    factor, secret, _ = await services.operator_begin_mfa_enrolment(
        session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token, enrollment_code=code
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
        """MINOR 11 of the 2026-09-24 security review: a disabled account gets the same
        generic 401 a wrong password does, not a distinct message that confirms it exists
        and is disabled specifically."""
        await _make_staff(session, status="disabled")
        with pytest.raises(UnauthorizedProblem, match="not valid"):
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
        # MINOR 11: a locked account answers the same generic 401 a wrong password gets, not
        # a distinct "temporarily locked" message — whatever the *correct* password proves.
        with pytest.raises(UnauthorizedProblem, match="not valid"):
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
        rows = (
            (await session.execute(select(Session).where(Session.user_id == staff.id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        refresh_rows = (
            (await session.execute(select(RefreshToken).where(RefreshToken.user_id == staff.id)))
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

    async def test_a_tenant_mfa_pre_auth_token_is_refused_on_operator_routes(
        self, session, cfg, pair
    ) -> None:
        """Cross-type confusion: a pre-auth token minted for the tenant `mfa` step names a
        reason `_decode_pre_auth` on the operator path never expects."""
        from app import tokens as tk

        tenant_reason_token = tk.mint_pre_auth(
            pair,
            issuer=cfg.jwt_issuer,
            user_id=USER,
            session_id=ORG_A,  # any uuid — the reason mismatch is refused first
            reason="mfa",
        )
        with pytest.raises(UnauthorizedProblem):
            await services.operator_verify_mfa(
                session,
                cfg=cfg,
                pair=pair,
                pre_auth_token=tenant_reason_token.token,
                code="000000",
            )

    async def test_an_operator_enrolment_pre_auth_token_is_refused_on_the_tenant_mfa_route(
        self, session, cfg, pair, directory, registered
    ) -> None:
        await _make_staff(session)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        with pytest.raises(UnauthorizedProblem):
            await services.verify_mfa(
                session,
                cfg=cfg,
                pair=pair,
                directory=directory,
                entitlements=FakeEntitlements(),
                pre_auth_token=outcome.pre_auth_token,
                code="000000",
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

    async def test_refresh_is_refused_when_mfa_was_never_verified(self, session, cfg, pair) -> None:
        """MAJOR 5: a session that never proved a second factor must not keep minting fresh
        operator tokens through rotation. Simulates a session/refresh pair from before MFA
        enforcement (or from any path that reached `_issue_operator` without a completed
        factor) by clearing `mfa_verified_at` after a normal enrolment."""
        await _make_staff(session)
        issued, _codes, _secret, _step = await _enrol_operator_mfa(session, cfg, pair)
        auth_session = await session.get(Session, issued.session_id)
        assert auth_session is not None
        auth_session.mfa_verified_at = None
        await session.flush()

        with pytest.raises(UnauthorizedProblem):
            await services.operator_rotate_refresh(
                session, cfg=cfg, pair=pair, raw_token=issued.refresh_token
            )


class TestEnrolmentCannotBeHijacked:
    """The security review's headline finding: a password-only attacker must not be able to
    enrol their own TOTP factor and obtain an operator token, including after the real staff
    member has already enrolled, and including by replaying a still-live pre-auth token."""

    async def test_a_password_alone_cannot_reach_enrolment_without_a_code(
        self, session, cfg, pair
    ) -> None:
        await _make_staff(session)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        assert outcome.status == "mfa_enrollment_required"
        with pytest.raises(UnauthorizedProblem, match="not valid or has expired"):
            await services.operator_begin_mfa_enrolment(
                session,
                cfg=cfg,
                pair=pair,
                pre_auth_token=outcome.pre_auth_token,
                enrollment_code="whatever-an-attacker-guesses",
            )

    async def test_a_wrong_enrolment_code_is_refused(self, session, cfg, pair) -> None:
        staff = await _make_staff(session)
        await _issue_code(session, staff.id)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        with pytest.raises(UnauthorizedProblem, match="not valid or has expired"):
            await services.operator_begin_mfa_enrolment(
                session,
                cfg=cfg,
                pair=pair,
                pre_auth_token=outcome.pre_auth_token,
                enrollment_code="not-the-code",
            )

    async def test_an_enrolment_code_is_single_use(self, session, cfg, pair) -> None:
        staff = await _make_staff(session)
        code = await _issue_code(session, staff.id)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        await services.operator_begin_mfa_enrolment(
            session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token, enrollment_code=code
        )
        # A second login, second pre-auth token, same (now-spent) code.
        again = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        with pytest.raises(UnauthorizedProblem, match="not valid or has expired"):
            await services.operator_begin_mfa_enrolment(
                session,
                cfg=cfg,
                pair=pair,
                pre_auth_token=again.pre_auth_token,
                enrollment_code=code,
            )

    async def test_an_expired_enrolment_code_is_refused(self, session, cfg, pair) -> None:
        staff = await _make_staff(session)
        code = await _issue_code(session, staff.id)
        row = await session.get(StaffEnrollmentCode, staff.id)
        assert row is not None
        row.expires_at = datetime.now(UTC)
        await session.flush()

        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        with pytest.raises(UnauthorizedProblem, match="not valid or has expired"):
            await services.operator_begin_mfa_enrolment(
                session,
                cfg=cfg,
                pair=pair,
                pre_auth_token=outcome.pre_auth_token,
                enrollment_code=code,
            )

    async def test_a_stale_enrolment_token_is_refused_once_the_real_owner_has_enrolled(
        self, session, cfg, pair
    ) -> None:
        """R1: an attacker's `mfa_enrollment_required` pre-auth token (minted before anyone
        enrolled) must not still work after the legitimate staff member enrols first, even
        though the JWT itself has not expired."""
        staff = await _make_staff(session)
        attacker_outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        attacker_code = await _issue_code(session, staff.id)

        # The real owner enrols through their own, separate login.
        await _enrol_operator_mfa(session, cfg, pair)

        # The attacker's stale token and a (separately issued, still valid) code no longer
        # get anywhere: the account already has a confirmed factor.
        with pytest.raises(UnauthorizedProblem, match="already enrolled"):
            await services.operator_begin_mfa_enrolment(
                session,
                cfg=cfg,
                pair=pair,
                pre_auth_token=attacker_outcome.pre_auth_token,
                enrollment_code=attacker_code,
            )

    async def test_a_replayed_enrolment_pre_auth_token_cannot_confirm_a_second_factor(
        self, session, cfg, pair
    ) -> None:
        """R2: after `confirm_operator_mfa_enrolment` succeeds, presenting the same pre-auth
        token again must not confirm a further factor or mint another token pair."""
        staff = await _make_staff(session)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        code = await _issue_code(session, staff.id)
        factor, secret, _ = await services.operator_begin_mfa_enrolment(
            session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token, enrollment_code=code
        )
        step = int(datetime.now(UTC).timestamp()) // 30
        await services.confirm_operator_mfa_enrolment(
            session,
            cfg=cfg,
            pair=pair,
            pre_auth_token=outcome.pre_auth_token,
            factor_id=factor.id,
            code=security._totp_at(secret, step),
        )
        with pytest.raises(UnauthorizedProblem, match="start signing in again"):
            await services.confirm_operator_mfa_enrolment(
                session,
                cfg=cfg,
                pair=pair,
                pre_auth_token=outcome.pre_auth_token,
                factor_id=factor.id,
                code=security._totp_at(secret, step + 1),
            )

    async def test_a_second_racing_enrolment_cannot_confirm_a_second_factor(
        self, session, cfg, pair
    ) -> None:
        """Isolates the account-level guard from the single-use pre-auth check above: two
        *different* pre-auth tokens (two racing login attempts, e.g. the attacker and the
        real owner, each starting enrolment before either finishes), each enrolling its own
        unconfirmed factor. Whichever confirms first wins the account; the second must not
        also be allowed to confirm its own, different factor afterwards."""
        staff = await _make_staff(session)

        first_login = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        first_code = await _issue_code(session, staff.id)
        first_factor, first_secret, _ = await services.operator_begin_mfa_enrolment(
            session,
            cfg=cfg,
            pair=pair,
            pre_auth_token=first_login.pre_auth_token,
            enrollment_code=first_code,
        )

        second_login = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        second_code = await _issue_code(session, staff.id)
        second_factor, second_secret, _ = await services.operator_begin_mfa_enrolment(
            session,
            cfg=cfg,
            pair=pair,
            pre_auth_token=second_login.pre_auth_token,
            enrollment_code=second_code,
        )
        assert second_factor.id != first_factor.id

        step = int(datetime.now(UTC).timestamp()) // 30
        await services.confirm_operator_mfa_enrolment(
            session,
            cfg=cfg,
            pair=pair,
            pre_auth_token=first_login.pre_auth_token,
            factor_id=first_factor.id,
            code=security._totp_at(first_secret, step),
        )

        with pytest.raises(UnauthorizedProblem, match="already enrolled"):
            await services.confirm_operator_mfa_enrolment(
                session,
                cfg=cfg,
                pair=pair,
                pre_auth_token=second_login.pre_auth_token,
                factor_id=second_factor.id,
                code=security._totp_at(second_secret, step),
            )

    async def test_the_secret_is_sealed_not_stored_in_the_clear(self, session, cfg, pair) -> None:
        staff = await _make_staff(session)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        code = await _issue_code(session, staff.id)
        factor, secret, _ = await services.operator_begin_mfa_enrolment(
            session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token, enrollment_code=code
        )
        assert factor.secret_enc is not None
        assert secret.encode() not in factor.secret_enc

    async def test_unconfirmed_factors_are_capped_per_account(self, session, cfg, pair) -> None:
        """MINOR 12: enrolment can legitimately be restarted (a lost phone, a login racing an
        older one), and each attempt used to leave an unconfirmed row behind forever."""
        from sqlalchemy import select as _select

        from app.models import MfaFactor

        staff = await _make_staff(session)
        for _ in range(services.MAX_UNCONFIRMED_FACTORS_PER_STAFF + 2):
            outcome = await services.operator_login(
                session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
            )
            code = await _issue_code(session, staff.id)
            await services.operator_begin_mfa_enrolment(
                session,
                cfg=cfg,
                pair=pair,
                pre_auth_token=outcome.pre_auth_token,
                enrollment_code=code,
            )

        rows = (
            (await session.execute(_select(MfaFactor).where(MfaFactor.user_id == staff.id)))
            .scalars()
            .all()
        )
        assert len(rows) == services.MAX_UNCONFIRMED_FACTORS_PER_STAFF


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

    async def test_a_wrong_totp_code_at_confirm_is_refused_and_does_not_confirm_the_factor(
        self, session, cfg, pair
    ) -> None:
        staff = await _make_staff(session)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        code = await _issue_code(session, staff.id)
        factor, _secret, _uri = await services.operator_begin_mfa_enrolment(
            session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token, enrollment_code=code
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

    async def test_a_wrong_totp_code_at_confirm_writes_an_audit_row(
        self, session, cfg, pair
    ) -> None:
        staff = await _make_staff(session)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        code = await _issue_code(session, staff.id)
        factor, _secret, _uri = await services.operator_begin_mfa_enrolment(
            session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token, enrollment_code=code
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
                    select(AuditLog).where(
                        AuditLog.actor_id == staff.id,
                        AuditLog.action == "auth.operator_mfa_enrol_failed",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    async def test_enough_wrong_confirm_codes_lock_the_account_and_revoke_the_session(
        self, session, cfg, pair
    ) -> None:
        """MAJOR 4: attempts at `confirm` are capped the same way `_verify_staff` caps
        password guesses, and crossing the threshold ends the session that produced them."""
        staff = await _make_staff(session)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        code = await _issue_code(session, staff.id)
        factor, _secret, _uri = await services.operator_begin_mfa_enrolment(
            session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token, enrollment_code=code
        )
        for _ in range(cfg.max_failed_attempts):
            with pytest.raises(UnauthorizedProblem):
                await services.confirm_operator_mfa_enrolment(
                    session,
                    cfg=cfg,
                    pair=pair,
                    pre_auth_token=outcome.pre_auth_token,
                    factor_id=factor.id,
                    code="000000",
                )
        await session.refresh(staff)
        # BLOCKER 1: a wrong *MFA*/enrolment-confirm code trips `mfa_locked_until`, not the
        # password lockout field — the two counters are independent.
        assert staff.mfa_locked_until is not None
        assert staff.locked_until is None

        import uuid as _uuid

        claims = jwt.decode(outcome.pre_auth_token, options={"verify_signature": False})
        failing_session = await session.get(Session, _uuid.UUID(claims["sid"]))
        assert failing_session is not None and failing_session.revoked_at is not None

        # BLOCKER 1: the MFA lock does not block the *password* step — a fresh login still
        # succeeds (the account has no confirmed factor yet, so it is offered enrolment
        # again); the lock refuses that next MFA-side action instead, on this new token,
        # before the enrolment code is even checked.
        fresh_outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        with pytest.raises(ForbiddenProblem, match="temporarily locked"):
            await services.operator_begin_mfa_enrolment(
                session,
                cfg=cfg,
                pair=pair,
                pre_auth_token=fresh_outcome.pre_auth_token,
                enrollment_code="does-not-matter",
            )

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
                    select(AuditLog).where(
                        AuditLog.actor_id == staff.id,
                        AuditLog.action == "auth.operator_mfa_failed",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    async def test_a_recovery_code_completes_the_sign_in_and_is_audited_distinctly(
        self, session, cfg, pair
    ) -> None:
        staff = await _make_staff(session)
        _issued, codes, _secret, _step = await _enrol_operator_mfa(session, cfg, pair)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        done = await services.operator_verify_mfa(
            session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token, code=codes[0]
        )
        assert done.operator_roles
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.actor_id == staff.id,
                        AuditLog.action == "auth.operator_mfa_recovery_code_used",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    async def test_a_replayed_mfa_pre_auth_token_is_refused_after_success(
        self, session, cfg, pair
    ) -> None:
        await _make_staff(session)
        _issued, _codes, secret, step = await _enrol_operator_mfa(session, cfg, pair)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        await services.operator_verify_mfa(
            session,
            cfg=cfg,
            pair=pair,
            pre_auth_token=outcome.pre_auth_token,
            code=security._totp_at(secret, step + 1),
        )
        with pytest.raises(UnauthorizedProblem, match="start signing in again"):
            await services.operator_verify_mfa(
                session,
                cfg=cfg,
                pair=pair,
                pre_auth_token=outcome.pre_auth_token,
                code=security._totp_at(secret, step + 2),
            )

    async def test_enough_wrong_codes_lock_the_account_before_exhausting_recovery_codes(
        self, session, cfg, pair
    ) -> None:
        """MAJOR 4 (R3): the lockout check runs before recovery-code verification, so a
        locked account cannot be used to keep spending Argon2 comparisons on guesses. Crossing
        the threshold also revokes the session the failures ran against, so a repeat with the
        very same pre-auth token dies at the session check rather than merely the lockout —
        the stronger of the two outcomes."""
        await _make_staff(session)
        await _enrol_operator_mfa(session, cfg, pair)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        for _ in range(cfg.max_failed_attempts):
            with pytest.raises(UnauthorizedProblem):
                await services.operator_verify_mfa(
                    session,
                    cfg=cfg,
                    pair=pair,
                    pre_auth_token=outcome.pre_auth_token,
                    code="000000",
                )
        # The session that produced the failures is gone.
        with pytest.raises(UnauthorizedProblem, match="no longer valid"):
            await services.operator_verify_mfa(
                session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token, code="000000"
            )
        # BLOCKER 1: the MFA lock is a separate counter from the password one, so a fresh
        # password login still succeeds — it must not leak that the *MFA* half is locked any
        # more than a correct password already does not. The lock instead refuses the very
        # next MFA attempt, on the new pre-auth token this login just minted.
        fresh_outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        with pytest.raises(ForbiddenProblem, match="temporarily locked"):
            await services.operator_verify_mfa(
                session,
                cfg=cfg,
                pair=pair,
                pre_auth_token=fresh_outcome.pre_auth_token,
                code="000000",
            )

    async def test_locking_the_account_revokes_the_session_and_its_refresh_tokens(
        self, session, cfg, pair
    ) -> None:
        await _make_staff(session)
        issued, _codes, _secret, _step = await _enrol_operator_mfa(session, cfg, pair)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        for _ in range(cfg.max_failed_attempts):
            with pytest.raises(UnauthorizedProblem):
                await services.operator_verify_mfa(
                    session,
                    cfg=cfg,
                    pair=pair,
                    pre_auth_token=outcome.pre_auth_token,
                    code="000000",
                )
        # The session the failing attempts ran against — bound to the *new* pre-auth token's
        # session, not the one enrolment completed on.
        claims = jwt.decode(outcome.pre_auth_token, options={"verify_signature": False})
        import uuid as _uuid

        failing_session = await session.get(Session, _uuid.UUID(claims["sid"]))
        assert failing_session is not None
        assert failing_session.revoked_at is not None
        # The refresh token from the earlier, successful enrolment is a different session and
        # is untouched — lockout ends the session that produced the failures, not every
        # session the account has ever had.
        earlier_refresh = await session.get(
            RefreshToken,
            jwt.decode(issued.refresh_token, options={"verify_signature": False})["jti"],
        )
        assert earlier_refresh is not None

    async def test_a_correct_password_never_resets_the_mfa_failure_counter_across_rounds(
        self, session, cfg, pair
    ) -> None:
        """BLOCKER 1 of the 2026-09-24 security review, reproduced directly: `_verify_staff`
        used to reset the exact field a wrong MFA code counted against, so a correct password
        between rounds wiped the counter every time and an attacker who already had the
        password could grind the second factor forever, four guesses at a stretch, without
        ever crossing the threshold. 20 rounds of (fresh login + 4 wrong codes) must still
        cross `max_failed_attempts` (5) and lock the account — the failure has to survive the
        password success between rounds, because it no longer lives on the same field."""
        await _make_staff(session)
        await _enrol_operator_mfa(session, cfg, pair)

        for _round in range(20):
            outcome = await services.operator_login(
                session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
            )
            for _attempt in range(4):
                with pytest.raises((UnauthorizedProblem, ForbiddenProblem)):
                    await services.operator_verify_mfa(
                        session,
                        cfg=cfg,
                        pair=pair,
                        pre_auth_token=outcome.pre_auth_token,
                        code="000000",
                    )

        staff = (
            await session.execute(select(StaffAccount).where(StaffAccount.email == STAFF_EMAIL))
        ).scalar_one()
        # Locked, on the MFA-specific field — and the password counter, which 20 correct
        # logins exercised just as many times, was never touched by any of this.
        assert staff.mfa_locked_until is not None
        assert staff.locked_until is None

        revoked = (
            (
                await session.execute(
                    select(Session).where(
                        Session.user_id == staff.id,
                        Session.revoked_reason == "operator_mfa_failed_attempts",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert revoked, "crossing the threshold must revoke the session that produced the failures"

    async def test_the_count_restarts_when_the_lock_trips_so_expiry_is_a_real_unlock(
        self, session, cfg, pair
    ) -> None:
        """m7 of the third-round security review: the counter used to stay at the threshold
        after the lock tripped, so the first wrong code after the lock *expired* pushed it
        straight back over and re-locked the account for a single typo."""
        from datetime import timedelta

        await _make_staff(session)
        await _enrol_operator_mfa(session, cfg, pair)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        for _ in range(cfg.max_failed_attempts):
            with pytest.raises(UnauthorizedProblem):
                await services.operator_verify_mfa(
                    session,
                    cfg=cfg,
                    pair=pair,
                    pre_auth_token=outcome.pre_auth_token,
                    code="000000",
                )
        staff = (
            await session.execute(select(StaffAccount).where(StaffAccount.email == STAFF_EMAIL))
        ).scalar_one()
        assert staff.mfa_locked_until is not None
        assert staff.mfa_failed_attempts == 0, "the count restarts with the lock"

        # The lock runs out.
        staff.mfa_locked_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

        fresh = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        with pytest.raises(UnauthorizedProblem, match="not valid"):
            await services.operator_verify_mfa(
                session, cfg=cfg, pair=pair, pre_auth_token=fresh.pre_auth_token, code="000000"
            )
        await session.refresh(staff)
        assert staff.mfa_failed_attempts == 1, "one typo after expiry is one strike, not a re-lock"
        assert not services._staff_locked(staff)

    async def test_a_refused_legacy_plaintext_factor_never_counts_against_the_operator(
        self, session, cfg, pair
    ) -> None:
        """R1 of the final security review, operator side: a staff factor still stored as
        plaintext (auth refuses it unless told otherwise) stops a correct six-digit code with a
        distinct 503 that is not counted and never trips the MFA lock; a recovery code signs in."""
        from app.models import MfaFactor, MfaRecoveryCode

        assert cfg.mfa_accept_legacy_plaintext is False
        staff = await _make_staff(session)
        secret = security.generate_totp_secret()
        session.add(
            MfaFactor(
                user_id=staff.id,
                type="totp",
                label="Authenticator app",
                secret_enc=secret.encode("ascii"),
                confirmed_at=datetime.now(UTC),
            )
        )
        session.add(
            MfaRecoveryCode(user_id=staff.id, code_hash=security.hash_recovery_code("abcd-efgh"))
        )
        await session.commit()
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        assert outcome.status == "mfa_required"
        correct = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)

        for _ in range(cfg.max_failed_attempts + 2):
            with pytest.raises(services.MfaResealPendingProblem) as raised:
                await services.operator_verify_mfa(
                    session,
                    cfg=cfg,
                    pair=pair,
                    pre_auth_token=outcome.pre_auth_token,
                    code=correct,
                )
            assert raised.value.status_code == 503
        await session.refresh(staff)
        assert staff.mfa_failed_attempts == 0
        assert staff.mfa_locked_until is None

        issued = await services.operator_verify_mfa(
            session, cfg=cfg, pair=pair, pre_auth_token=outcome.pre_auth_token, code="abcd-efgh"
        )
        assert issued.operator_roles


class TestOperatorAuditTrail:
    async def test_password_verification_and_sign_in_are_both_audited(
        self, session, cfg, pair
    ) -> None:
        staff = await _make_staff(session)
        await _enrol_operator_mfa(session, cfg, pair)
        actions = {
            row.action
            for row in (
                await session.execute(select(AuditLog).where(AuditLog.actor_id == staff.id))
            )
            .scalars()
            .all()
        }
        assert "auth.operator_password_verified" in actions
        assert "auth.operator_mfa_enrolled" in actions
        assert "auth.operator_mfa_enrol_started" in actions

    async def test_issuing_an_enrolment_code_is_audited(self, session) -> None:
        staff = await _make_staff(session)
        await services.issue_staff_enrollment_code(session, staff_id=staff.id, actor_id=None)
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == staff.id,
                        AuditLog.action == "auth.operator_enrollment_code_issued",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


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

    async def test_an_operator_token_with_no_completed_second_factor_gets_403(
        self, session, client
    ) -> None:
        from app.main import app as fastapi_app
        from platform_core.auth import Principal, get_principal

        staff = await _make_staff(session)
        await session.commit()
        principal = Principal(
            sub=staff.id,
            organization_id=services.PLATFORM_ORG,
            audience="operator",
            roles=["owner"],
            amr=["pwd"],
        )
        fastapi_app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.get("/api/v1/auth/operator/me")
        finally:
            fastapi_app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 403


class TestEnrollmentCodeRoute:
    async def test_an_owner_can_issue_a_code_for_another_staff_account(
        self, session, client
    ) -> None:
        from app.main import app as fastapi_app
        from platform_core.auth import Principal, get_principal

        owner = await _make_staff(session, email="owner-two@opsgrids.tech", roles=["owner"])
        colleague = await _make_staff(session, email="colleague@opsgrids.tech", roles=["support"])
        await session.commit()
        principal = Principal(
            sub=owner.id,
            organization_id=services.PLATFORM_ORG,
            audience="operator",
            roles=["owner"],
            amr=["pwd", "otp"],
        )
        fastapi_app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.post(
                "/api/v1/auth/operator/enrollment-codes",
                json={"staff_id": str(colleague.id)},
            )
        finally:
            fastapi_app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["staff_id"] == str(colleague.id)
        assert len(body["enrollment_code"]) >= 8

    async def test_a_non_owner_operator_cannot_issue_a_code(self, session, client) -> None:
        from app.main import app as fastapi_app
        from platform_core.auth import Principal, get_principal

        support = await _make_staff(session, email="support-only@opsgrids.tech", roles=["support"])
        colleague = await _make_staff(session, email="colleague2@opsgrids.tech")
        await session.commit()
        principal = Principal(
            sub=support.id,
            organization_id=services.PLATFORM_ORG,
            audience="operator",
            roles=["support"],
            amr=["pwd", "otp"],
        )
        fastapi_app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.post(
                "/api/v1/auth/operator/enrollment-codes",
                json={"staff_id": str(colleague.id)},
            )
        finally:
            fastapi_app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 403


class TestMfaResetRoute:
    """MINOR 5 of the 2026-09-24 security review: the lost-device path."""

    async def test_an_owner_can_reset_a_colleagues_mfa(self, session, cfg, pair, client) -> None:
        from app.main import app as fastapi_app
        from app.models import MfaFactor, MfaRecoveryCode
        from platform_core.auth import Principal, get_principal

        owner = await _make_staff(session, email="owner-reset@opsgrids.tech", roles=["owner"])
        await _make_staff(session, email="locked-out@opsgrids.tech", roles=["support"])
        _issued, _codes, _secret, _step = await _enrol_operator_mfa(
            session, cfg, pair, email="locked-out@opsgrids.tech"
        )
        colleague = (
            await session.execute(
                select(StaffAccount).where(StaffAccount.email == "locked-out@opsgrids.tech")
            )
        ).scalar_one()
        await session.commit()

        principal = Principal(
            sub=owner.id,
            organization_id=services.PLATFORM_ORG,
            audience="operator",
            roles=["owner"],
            amr=["pwd", "otp"],
        )
        fastapi_app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.post(f"/api/v1/auth/operator/staff/{colleague.id}/mfa:reset")
        finally:
            fastapi_app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 204, response.text

        remaining_factors = (
            await session.execute(select(MfaFactor).where(MfaFactor.user_id == colleague.id))
        ).first()
        remaining_codes = (
            await session.execute(
                select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == colleague.id)
            )
        ).first()
        assert remaining_factors is None
        assert remaining_codes is None

        live_sessions = (
            await session.execute(
                select(Session).where(Session.user_id == colleague.id, Session.revoked_at.is_(None))
            )
        ).first()
        assert live_sessions is None

        audit_row = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "auth.operator_mfa_reset",
                    AuditLog.entity_id == colleague.id,
                )
            )
        ).scalar_one_or_none()
        assert audit_row is not None
        assert audit_row.actor_id == owner.id

    async def test_a_non_owner_cannot_reset_anyones_mfa(self, session, client) -> None:
        from app.main import app as fastapi_app
        from platform_core.auth import Principal, get_principal

        support = await _make_staff(session, email="support-reset@opsgrids.tech", roles=["support"])
        colleague = await _make_staff(session, email="colleague-reset@opsgrids.tech")
        await session.commit()

        principal = Principal(
            sub=support.id,
            organization_id=services.PLATFORM_ORG,
            audience="operator",
            roles=["support"],
            amr=["pwd", "otp"],
        )
        fastapi_app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.post(f"/api/v1/auth/operator/staff/{colleague.id}/mfa:reset")
        finally:
            fastapi_app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 403

    async def test_an_owner_cannot_reset_their_own_mfa(self, session, client) -> None:
        from app.main import app as fastapi_app
        from platform_core.auth import Principal, get_principal

        owner = await _make_staff(session, email="self-reset@opsgrids.tech", roles=["owner"])
        await session.commit()

        principal = Principal(
            sub=owner.id,
            organization_id=services.PLATFORM_ORG,
            audience="operator",
            roles=["owner"],
            amr=["pwd", "otp"],
        )
        fastapi_app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.post(f"/api/v1/auth/operator/staff/{owner.id}/mfa:reset")
        finally:
            fastapi_app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 422

    async def test_resetting_mfa_also_clears_the_lock_and_counter(self, session, client) -> None:
        """m7: a colleague locked out *because* they lost their device is what this route is
        for — the lock must not outlive the reset."""
        from datetime import timedelta

        from app.main import app as fastapi_app
        from platform_core.auth import Principal, get_principal

        owner = await _make_staff(session, email="owner-unlock@opsgrids.tech", roles=["owner"])
        locked = await _make_staff(session, email="locked-unlock@opsgrids.tech", roles=["support"])
        locked.mfa_failed_attempts = 4
        locked.mfa_locked_until = datetime.now(UTC) + timedelta(minutes=10)
        await session.commit()

        principal = Principal(
            sub=owner.id,
            organization_id=services.PLATFORM_ORG,
            audience="operator",
            roles=["owner"],
            amr=["pwd", "otp"],
        )
        fastapi_app.dependency_overrides[get_principal] = lambda: principal
        try:
            response = await client.post(f"/api/v1/auth/operator/staff/{locked.id}/mfa:reset")
        finally:
            fastapi_app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 204, response.text

        await session.refresh(locked)
        assert locked.mfa_failed_attempts == 0
        assert locked.mfa_locked_until is None


class TestBootstrap:
    async def test_seeding_the_owner_is_idempotent(self, session) -> None:
        first, first_created = await bootstrap_staff_owner(
            session,
            email="owner@opsgrids.local",
            password="dev-operator-not-a-real-password",  # pragma: allowlist secret
            display_name="OpsGrids Owner (dev)",
            roles=["owner"],
        )
        again, again_created = await bootstrap_staff_owner(
            session,
            email="owner@opsgrids.local",
            password="a-different-one",  # pragma: allowlist secret
            display_name="Someone Else",
            roles=["support"],
        )
        assert first_created is True
        assert again_created is False
        assert again.id == first.id, "re-seeding must not duplicate or rewrite the owner"
        assert again.display_name == "OpsGrids Owner (dev)"

    async def test_issuing_codes_for_unenrolled_staff_skips_already_confirmed_accounts(
        self, session, cfg, pair
    ) -> None:
        from app.bootstrap import issue_enrollment_codes_for_unenrolled_staff

        never_enrolled = await _make_staff(session, email="never@opsgrids.tech")
        await _make_staff(session, email="ops@opsgrids.tech")
        await _enrol_operator_mfa(session, cfg, pair, email="ops@opsgrids.tech")
        await session.commit()

        # `issue_enrollment_codes_for_unenrolled_staff` manages its own engine lifecycle
        # (`init_engine`/`dispose_engine` are idempotent no-ops when already set), the same
        # shape `bin/issue-enrollment-codes` runs it under.
        pairs = await issue_enrollment_codes_for_unenrolled_staff(cfg)

        emails = {email for email, _code in pairs}
        assert "never@opsgrids.tech" in emails
        assert "ops@opsgrids.tech" not in emails
        assert never_enrolled.email == "never@opsgrids.tech"
