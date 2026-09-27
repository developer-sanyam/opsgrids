"""Enrolling an authenticator is an account-wide credential change, so a bearer token alone must
not be able to do it (ADR-0023, finding B1 of the step-up security review).

The attack this file exists to keep closed: hold a stolen access token, `POST /auth/mfa/enrol` and
`/confirm` with *your own* authenticator app, wait one 30-second step, `POST /auth/mfa/step-up`,
reveal. Every link is refused now, so the tests are written refusals first, in the order the attack
would try them: no password, wrong password, an existing factor with no code or a wrong one, a
confirm from somewhere other than the session that began it, a step-up with a factor that was
slipped in after the session began. The happy paths follow, and last the guarantees that are
about *how* the route behaves (ordering, the primitive not being reachable) rather than what it
answers.

Wrong values are counted, so — like `test_step_up.py` — these drive the real routes through the
`http` fixture, whose session override **rolls back on an exception**: a failure counter survives
only if the service committed it before raising.
"""

from __future__ import annotations

import ast
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app import security, services
from app.config import Settings
from app.deps import get_directory
from app.directory import DirectoryUser
from app.keys import KeyPair
from app.main import app
from app.models import AuditLog, Credential, MfaFactor, MfaRecoveryCode, OutboxEvent, RefreshToken
from platform_core.db import TENANT_GUC
from platform_core.errors import ForbiddenProblem, UnauthorizedProblem
from tests.conftest import (
    MEMBERSHIP_A,
    ORG_A,
    ORG_B,
    PASSWORD,
    USER,
    FakeDirectory,
    FakeEntitlements,
    membership,
    user_in_two_orgs,
)
from tests.test_step_up import (
    STEP_UP,
    _audit,
    _code,
    _credential,
    _enrol,
    _headers,
    _leaves,
    _mint,
    _problem_type,
    _session_row,
    _sign_in,
    _wrong_code,
    http,
)

pytestmark = pytest.mark.integration

# `http` is a fixture, used by name from the tests below; importing it is how pytest sees it here.
__all__ = ["http"]

ENROL = "/api/v1/auth/mfa/enrol"
CONFIRM = "/api/v1/auth/mfa/enrol/confirm"
SWITCH = "/api/v1/auth/switch-org"
ROUTES = Path(__file__).resolve().parent.parent / "app" / "api" / "routes.py"

# Passwords that are meant to be wrong.
ATTACKER_GUESS = "attacker-guess-1"  # pragma: allowlist secret
NOT_THE_PASSWORD = "not-the-password"  # pragma: allowlist secret
WRONG_PASSWORD_X = "wrong-password-x"  # pragma: allowlist secret
WRONG_PASSWORD_Y = "wrong-password-y"  # pragma: allowlist secret


# ---------------------------------------------------------------------------- helpers


async def _begin(http: AsyncClient, token: str, **body: Any) -> Any:
    return await http.post(ENROL, json=body, headers=_headers(token))


async def _confirm(http: AsyncClient, token: str, factor_id: str, code: str) -> Any:
    return await http.post(
        CONFIRM, json={"factor_id": factor_id, "code": code}, headers=_headers(token)
    )


async def _enrol_through(http: AsyncClient, token: str) -> tuple[str, list[str], str]:
    """The whole legitimate flow, over HTTP, in one session: password, then the new app's code.
    Returns `(secret, recovery_codes, factor_id)`."""
    started = await _begin(http, token, password=PASSWORD)
    assert started.status_code == 201, started.text
    secret, factor_id = started.json()["secret"], started.json()["factor_id"]
    done = await _confirm(http, token, factor_id, _code(secret))
    assert done.status_code == 200, done.text
    return secret, done.json()["recovery_codes"], factor_id


async def _factors(session: AsyncSession) -> list[SimpleNamespace]:
    await session.rollback()
    rows = (await session.execute(select(MfaFactor).order_by(MfaFactor.created_at))).scalars().all()
    snapshot = [
        SimpleNamespace(
            id=r.id,
            confirmed_at=r.confirmed_at,
            enrolled_session_id=r.enrolled_session_id,
            last_used_step=r.last_used_step,
        )
        for r in rows
    ]
    await session.rollback()  # a route that opens its own transaction refuses one already open
    return snapshot


async def _recovery(session: AsyncSession) -> list[tuple[str, bool]]:
    """`(hash, used)` for every recovery code, in a stable order."""
    await session.rollback()
    rows = (
        await session.execute(
            select(MfaRecoveryCode.code_hash, MfaRecoveryCode.used_at).order_by(
                MfaRecoveryCode.code_hash
            )
        )
    ).all()
    await session.rollback()
    return [(h, u is not None) for h, u in rows]


async def _password_failures(session: AsyncSession) -> tuple[int, datetime | None]:
    await session.rollback()
    row = (
        await session.execute(
            select(Credential.failed_attempts, Credential.locked_until).where(
                Credential.user_id == USER
            )
        )
    ).one()
    await session.rollback()
    return row[0], row[1]


async def _sso_bearer(
    session: AsyncSession, cfg: Settings, pair: KeyPair, directory: FakeDirectory
) -> tuple[str, uuid.UUID]:
    sso = await services.start_session(
        session,
        user_id=USER,
        cfg=cfg,
        amr=[services.SSO_AMR],
        idp=f"oidc:{ORG_A}",
        sso_domain="example.com",
    )
    issued = await services.issue_for(
        session,
        pair=pair,
        cfg=cfg,
        auth_session=sso,
        membership=directory.users[0].memberships[0],
        entitlements=FakeEntitlements(),
        audience="app",
    )
    await session.commit()
    return issued.access_token, sso.id


async def _second_session(
    session: AsyncSession, cfg: Settings, pair: KeyPair, directory: FakeDirectory
) -> services.Issued:
    """Another password sign-in for the same person — a second device, or an attacker's copy."""
    return await _sign_in(session, cfg, pair, directory)


# ------------------------------------------- refused: a bearer token alone cannot begin


class TestABearerAloneCannotStartAnEnrolment:
    async def test_the_attack_chain_is_closed_at_its_first_link(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """B1, end to end: the attacker has the bearer and nothing else. Every step they would
        take next is refused, and no factor exists at the end for step-up to accept."""
        stolen = await _sign_in(session, cfg, pair, directory)

        started = await _begin(http, stolen.access_token, password=ATTACKER_GUESS)
        assert started.status_code == 422
        assert _problem_type(started) == "password-incorrect"
        assert "secret" not in started.text and "factor_id" not in started.text

        # They cannot confirm a factor they never obtained, and cannot step up without one.
        confirm = await _confirm(http, stolen.access_token, str(uuid.uuid4()), "123456")
        assert confirm.status_code == 404
        stepped = await http.post(
            STEP_UP, json={"code": "123456"}, headers=_headers(stolen.access_token)
        )
        assert stepped.status_code == 409
        assert _problem_type(stepped) == "mfa-not-enrolled"
        assert await _factors(session) == []

    async def test_a_missing_password_is_a_validation_error_and_creates_nothing(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        for body in ({}, {"password": ""}, {"password": None}, {"current_code": "123456"}):
            response = await http.post(ENROL, json=body, headers=_headers(issued.access_token))
            assert response.status_code == 422, body
            assert response.json()["type"].endswith("/validation")
        # The old bodiless request — what the dashboard sends today — is refused the same way.
        assert (await http.post(ENROL, headers=_headers(issued.access_token))).status_code == 422
        assert await _factors(session) == []
        assert await _password_failures(session) == (0, None)

    async def test_unknown_fields_are_refused(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        response = await _begin(
            http, issued.access_token, password=PASSWORD, user_id=str(uuid.uuid4())
        )
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "extra_forbidden"
        assert await _factors(session) == []

    async def test_a_wrong_password_is_counted_audited_and_creates_nothing(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)

        response = await _begin(http, issued.access_token, password=NOT_THE_PASSWORD)

        # 422 and not 401: the shared client would refresh and re-send the same wrong password.
        assert response.status_code == 422
        assert _problem_type(response) == "password-incorrect"
        assert response.json()["errors"][0]["field"] == "password"
        # Survives only because the service committed before it raised.
        assert (await _password_failures(session))[0] == 1
        assert await _factors(session) == []
        [failed] = await _audit(session, "auth.mfa_enrolment_failed")
        assert failed.organization_id == ORG_A
        assert failed.actor_id == USER
        assert failed.after == {
            "session_id": str(issued.session_id),
            "reason": "invalid_password",
            "locked": False,
        }
        assert failed.request_id == "step-up-test-request"
        assert failed.ip is not None and failed.user_agent == "step-up-test/1"
        assert NOT_THE_PASSWORD not in response.text

    async def test_the_password_count_is_the_one_sign_in_uses(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """Four wrong passwords typed into the enrolment prompt and one at the login form lock
        the account: it is one allowance, not one per door."""
        issued = await _sign_in(session, cfg, pair, directory)
        for _ in range(cfg.max_failed_attempts - 1):
            await _begin(http, issued.access_token, password=WRONG_PASSWORD_X)
        assert (await _password_failures(session))[0] == cfg.max_failed_attempts - 1

        with pytest.raises(UnauthorizedProblem):
            await services.login(
                session,
                cfg=cfg,
                pair=pair,
                directory=directory,
                entitlements=FakeEntitlements(),
                email="sam@example.com",
                password=WRONG_PASSWORD_Y,
            )
        _, locked_until = await _password_failures(session)
        assert locked_until is not None and locked_until > datetime.now(UTC)

    async def test_the_last_wrong_password_locks_the_account_and_ends_the_session(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        attacker = await _sign_in(session, cfg, pair, directory)
        for attempt in range(cfg.max_failed_attempts):
            response = await _begin(http, attacker.access_token, password=WRONG_PASSWORD_X)
            # The attempt that trips the lock still answers "incorrect", like sign-in does.
            assert response.status_code == 422, attempt

        _, locked_until = await _password_failures(session)
        assert locked_until is not None and locked_until > datetime.now(UTC)
        ended = await _session_row(session, attacker.session_id)
        assert ended.revoked_at is not None
        assert ended.revoked_reason == services.ENROLMENT_LOCKOUT_REASON
        live = (
            await session.execute(
                select(func.count())
                .select_from(RefreshToken)
                .where(
                    RefreshToken.session_id == attacker.session_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        assert live == 0
        failed = await _audit(session, "auth.mfa_enrolment_failed")
        assert failed[-1].after["locked"] is True

        # Sign-in is locked too — even for the right password — and the dead session's bearer
        # is refused outright, without touching the counters.
        with pytest.raises(ForbiddenProblem, match="temporarily locked"):
            await services.login(
                session,
                cfg=cfg,
                pair=pair,
                directory=directory,
                entitlements=FakeEntitlements(),
                email="sam@example.com",
                password=PASSWORD,
            )
        assert (await _begin(http, attacker.access_token, password=PASSWORD)).status_code == 401
        assert await _factors(session) == []

    async def test_a_locked_account_refuses_the_right_password_without_counting(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        await session.execute(
            Credential.__table__.update().values(
                locked_until=datetime.now(UTC) + timedelta(minutes=5), failed_attempts=3
            )
        )
        await session.commit()

        response = await _begin(http, issued.access_token, password=PASSWORD)

        assert response.status_code == 403
        assert _problem_type(response) == "password-locked"
        assert response.json()["detail"] == services.PASSWORD_LOCKED_MESSAGE
        assert (await _password_failures(session))[0] == 3
        assert await _factors(session) == []

    async def test_a_correct_password_clears_the_count(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        for _ in range(2):
            await _begin(http, issued.access_token, password=WRONG_PASSWORD_X)
        assert (await _password_failures(session))[0] == 2

        ok = await _begin(http, issued.access_token, password=PASSWORD)

        assert ok.status_code == 201
        assert await _password_failures(session) == (0, None)


# -------------------------------- refused: an existing authenticator has to be proved too


class TestAnExistingAuthenticatorMustBeProved:
    async def _member_with_a_factor(
        self, session: AsyncSession, cfg, pair, directory
    ) -> tuple[services.Issued, str, list[str]]:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, recovery = await _enrol(session, cfg)
        return issued, secret, recovery

    async def test_no_code_is_refused_before_the_password_is_even_checked_and_counts_nothing(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued, _secret, _ = await self._member_with_a_factor(session, cfg, pair, directory)

        response = await _begin(http, issued.access_token, password=PASSWORD)

        assert response.status_code == 422
        assert _problem_type(response) == "mfa-current-code-required"
        assert response.json()["errors"][0]["field"] == "current_code"
        assert len(await _factors(session)) == 1
        assert await _password_failures(session) == (0, None)
        assert (await _credential(session)) == (0, None)
        assert await _audit(session, "auth.mfa_enrolment_failed") == []

    async def test_a_wrong_password_is_refused_first_and_spends_no_code(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued, secret, _ = await self._member_with_a_factor(session, cfg, pair, directory)
        [before] = await _factors(session)

        response = await _begin(
            http,
            issued.access_token,
            password=WRONG_PASSWORD_X,
            current_code=_code(secret),
        )

        assert response.status_code == 422
        assert _problem_type(response) == "password-incorrect"
        [after] = await _factors(session)
        assert after.last_used_step == before.last_used_step, "the code was consumed"
        assert (await _credential(session)) == (0, None)

    async def test_a_wrong_code_is_counted_against_the_mfa_lockout_and_creates_nothing(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued, secret, _ = await self._member_with_a_factor(session, cfg, pair, directory)
        wrong = _wrong_code(secret)

        response = await _begin(http, issued.access_token, password=PASSWORD, current_code=wrong)

        assert response.status_code == 422
        assert _problem_type(response) == "mfa-code-invalid"
        assert response.json()["errors"][0]["field"] == "current_code"
        assert wrong not in response.text
        assert (await _credential(session))[0] == 1, "counted"
        assert await _password_failures(session) == (0, None), "the password was right"
        assert len(await _factors(session)) == 1
        [failed] = await _audit(session, "auth.mfa_enrolment_failed")
        assert failed.after["reason"] == "invalid_code"

    async def test_a_replayed_code_is_refused_with_the_wait_message(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued, secret, _ = await self._member_with_a_factor(session, cfg, pair, directory)
        code = _code(secret)
        first = await _begin(http, issued.access_token, password=PASSWORD, current_code=code)
        assert first.status_code == 201

        again = await _begin(http, issued.access_token, password=PASSWORD, current_code=code)

        assert again.status_code == 422
        assert _problem_type(again) == "mfa-code-replayed"
        assert "Wait for the next code" in again.json()["detail"]

    async def test_the_last_wrong_code_locks_mfa_and_ends_the_session(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        attacker = await _sign_in(session, cfg, pair, directory)
        other_device = await _second_session(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        for _ in range(cfg.max_failed_attempts):
            await _begin(
                http,
                attacker.access_token,
                password=PASSWORD,
                current_code=_wrong_code(secret),
            )

        _, locked_until = await _credential(session)
        assert locked_until is not None and locked_until > datetime.now(UTC)
        ended = await _session_row(session, attacker.session_id)
        assert ended.revoked_reason == services.ENROLMENT_LOCKOUT_REASON
        assert len(await _factors(session)) == 1
        # It is the lock login MFA and step-up read: a session that is still alive is shut out
        # of step-up too, even with the right code.
        shut = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(other_device.access_token)
        )
        assert shut.status_code == 403
        assert _problem_type(shut) == "mfa-locked"

    async def test_a_locked_mfa_refuses_even_the_right_code(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued, secret, _ = await self._member_with_a_factor(session, cfg, pair, directory)
        await session.execute(
            Credential.__table__.update().values(
                mfa_locked_until=datetime.now(UTC) + timedelta(minutes=5)
            )
        )
        await session.commit()

        response = await _begin(
            http, issued.access_token, password=PASSWORD, current_code=_code(secret)
        )

        assert response.status_code == 403
        assert _problem_type(response) == "mfa-locked"
        assert (await _factors(session))[0].last_used_step is None

    async def test_the_right_password_and_code_add_a_second_factor_and_keep_the_recovery_codes(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued, secret, recovery = await self._member_with_a_factor(session, cfg, pair, directory)
        codes_before = await _recovery(session)
        assert len(codes_before) == 10 and not any(used for _, used in codes_before)

        started = await _begin(
            http, issued.access_token, password=PASSWORD, current_code=_code(secret)
        )
        assert started.status_code == 201, started.text
        second_secret, factor_id = started.json()["secret"], started.json()["factor_id"]
        done = await _confirm(http, issued.access_token, factor_id, _code(second_secret))

        assert done.status_code == 200, done.text
        # Only the first factor mints recovery codes; this one leaves the set exactly as it was.
        assert done.json() == {"recovery_codes": []}
        assert await _recovery(session) == codes_before
        confirmed = [f for f in await _factors(session) if f.confirmed_at is not None]
        assert len(confirmed) == 2
        # Audit: the enrolment is recorded, and no recovery-code change is.
        org_rows = [
            r
            for r in await _audit(session, "auth.mfa_recovery_codes_issued")
            if r.organization_id == ORG_A
        ]
        assert org_rows == []
        [confirmed_row] = [
            r
            for r in await _audit(session, "auth.mfa_enrolment_confirmed")
            if r.organization_id == ORG_A
        ]
        assert confirmed_row.after["first_factor"] is False
        assert confirmed_row.after["recovery_codes_issued"] is False
        assert all(code not in _leaves(confirmed_row.after) for code in recovery)

    async def test_a_recovery_code_is_accepted_and_then_spent(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued, _secret, recovery = await self._member_with_a_factor(session, cfg, pair, directory)

        started = await _begin(
            http, issued.access_token, password=PASSWORD, current_code=recovery[0]
        )

        assert started.status_code == 201, started.text
        remaining = await _recovery(session)
        assert sum(1 for _, used in remaining if used) == 1
        [used_row] = [
            r
            for r in await _audit(session, "auth.mfa_recovery_code_used")
            if r.organization_id == ORG_A
        ]
        assert used_row.after == {"session_id": str(issued.session_id), "purpose": "add_factor"}
        assert recovery[0] not in _leaves(used_row.after)
        # Spent means spent: the same code again is a wrong code.
        again = await _begin(http, issued.access_token, password=PASSWORD, current_code=recovery[0])
        assert again.status_code == 422
        assert _problem_type(again) == "mfa-code-invalid"

    async def test_a_legacy_factor_this_deployment_cannot_read_is_a_503_that_counts_nothing(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        session.add(
            MfaFactor(
                user_id=USER,
                type="totp",
                label="old",
                secret_enc=security.generate_totp_secret().encode("ascii"),
                confirmed_at=datetime.now(UTC) - timedelta(days=30),
            )
        )
        await session.commit()
        assert cfg.mfa_accept_legacy_plaintext is False

        response = await _begin(http, issued.access_token, password=PASSWORD, current_code="123456")

        assert response.status_code == 503
        assert _problem_type(response) == "mfa-reseal-pending"
        assert await _credential(session) == (0, None)
        assert len(await _factors(session)) == 1


# ------------------------------------------------- refused: dead, foreign and SSO sessions


class TestOnlyALiveMemberSessionMayEnrol:
    async def test_a_revoked_session_cannot_begin_and_nothing_is_counted(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        await services._revoke_session(session, issued.session_id, reason="test")
        await session.commit()

        response = await _begin(http, issued.access_token, password=WRONG_PASSWORD_X)

        assert response.status_code == 401
        assert await _password_failures(session) == (0, None)
        assert await _factors(session) == []

    async def test_a_session_revoked_mid_flow_cannot_confirm(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        started = await _begin(http, issued.access_token, password=PASSWORD)
        assert started.status_code == 201
        secret, factor_id = started.json()["secret"], started.json()["factor_id"]
        await services._revoke_session(session, issued.session_id, reason="logout")
        await session.commit()

        response = await _confirm(http, issued.access_token, factor_id, _code(secret))

        assert response.status_code == 401
        [factor] = await _factors(session)
        assert factor.confirmed_at is None, "the factor was confirmed from a dead session"
        assert (await session.get(Credential, USER)).mfa_enforced is False  # type: ignore[union-attr]

    async def test_an_expired_session_is_refused_at_both_steps(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        row = await _session_row(session, issued.session_id)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

        assert (await _begin(http, issued.access_token, password=PASSWORD)).status_code == 401
        assert (
            await _confirm(http, issued.access_token, str(uuid.uuid4()), "123456")
        ).status_code == 401

    async def test_a_session_belonging_to_someone_else_is_refused(
        self, http: AsyncClient, session, cfg, pair, registered
    ) -> None:
        theirs = await services.start_session(session, user_id=uuid.uuid4(), cfg=cfg, amr=["pwd"])
        await session.commit()
        token = _mint(pair, cfg, session_id=theirs.id)

        assert (await _begin(http, token, password=PASSWORD)).status_code == 401
        assert await _factors(session) == []

    async def test_a_single_sign_on_session_is_refused_at_begin_and_confirm(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        token, _sid = await _sso_bearer(session, cfg, pair, directory)

        begun = await _begin(http, token, password=WRONG_PASSWORD_X)
        assert begun.status_code == 403
        assert _problem_type(begun) == "mfa-unavailable-for-sso"
        assert "password sign-in" in begun.json()["detail"]
        confirmed = await _confirm(http, token, str(uuid.uuid4()), "123456")
        assert confirmed.status_code == 403
        assert _problem_type(confirmed) == "mfa-unavailable-for-sso"
        # Refused before the password was looked at, so nothing was counted.
        assert await _password_failures(session) == (0, None)
        assert await _factors(session) == []

    async def test_an_ended_membership_and_a_wrong_organization_are_refused(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        foreign = _mint(pair, cfg, session_id=issued.session_id, organization_id=ORG_B)
        assert (await _begin(http, foreign, password=PASSWORD)).status_code == 401

        directory.users = [
            DirectoryUser(
                user_id=USER,
                email="sam@example.com",
                full_name="Sam Singleton",
                is_active=True,
                memberships=[membership(ORG_A, MEMBERSHIP_A, "acme", "Acme", status="removed")],
            )
        ]
        assert (await _begin(http, issued.access_token, password=PASSWORD)).status_code == 401
        assert await _factors(session) == []

    async def test_an_operator_token_is_refused(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        token = _mint(pair, cfg, session_id=issued.session_id, audience="operator")
        assert (await _begin(http, token, password=PASSWORD)).status_code == 401


# --------------------------------------------- confirm is bound to the session that began


class TestConfirmIsBoundToTheSessionThatBegan:
    async def test_a_factor_begun_in_one_session_cannot_be_confirmed_by_another(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        first = await _sign_in(session, cfg, pair, directory)
        second = await _second_session(session, cfg, pair, directory)
        started = await _begin(http, first.access_token, password=PASSWORD)
        secret, factor_id = started.json()["secret"], started.json()["factor_id"]

        stolen_confirm = await _confirm(http, second.access_token, factor_id, _code(secret))

        assert stolen_confirm.status_code == 404
        [factor] = await _factors(session)
        assert factor.confirmed_at is None
        assert factor.enrolled_session_id == first.session_id
        # The session that began it can still finish it.
        assert (
            await _confirm(http, first.access_token, factor_id, _code(secret))
        ).status_code == 200

    async def test_a_factor_created_without_a_session_cannot_be_confirmed_over_http(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """Whatever wrote that pending row — a previous release, a script — it was not created by
        an authenticated `begin`, so no session may confirm it."""
        issued = await _sign_in(session, cfg, pair, directory)
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        await session.commit()

        response = await _confirm(http, issued.access_token, str(factor.id), _code(secret))

        assert response.status_code == 404
        assert (await _factors(session))[0].confirmed_at is None

    async def test_someone_elses_pending_factor_is_indistinguishable_from_none(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        other = uuid.uuid4()
        theirs = await services.start_session(session, user_id=other, cfg=cfg, amr=["pwd"])
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, other, account_email="x@example.com", cfg=cfg, session_id=str(theirs.id)
        )
        await session.commit()

        real = await _confirm(http, issued.access_token, str(factor.id), _code(secret))
        missing = await _confirm(http, issued.access_token, str(uuid.uuid4()), _code(secret))

        assert real.status_code == missing.status_code == 404
        assert real.json()["detail"] == missing.json()["detail"]

    async def test_a_wrong_confirm_code_is_a_422_field_error_and_confirms_nothing(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """The dashboard reads `fieldErrors.code`; a 401 would also have the shared client
        refresh and re-send it, then sign the member out."""
        issued = await _sign_in(session, cfg, pair, directory)
        started = await _begin(http, issued.access_token, password=PASSWORD)
        secret, factor_id = started.json()["secret"], started.json()["factor_id"]

        response = await _confirm(http, issued.access_token, factor_id, _wrong_code(secret))

        assert response.status_code == 422
        assert _problem_type(response) == "mfa-code-invalid"
        assert response.json()["errors"][0]["field"] == "code"
        assert (await _factors(session))[0].confirmed_at is None
        assert await _recovery(session) == []

    async def test_a_confirmed_factor_cannot_be_confirmed_again(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _codes, factor_id = await _enrol_through(http, issued.access_token)
        again = await _confirm(http, issued.access_token, factor_id, _code(secret, 1))
        assert again.status_code == 401
        assert "already confirmed" in again.json()["detail"]


# ------------------------------------------------------------------------ the happy path


class TestTheLegitimateFlow:
    async def test_the_first_factor_mints_recovery_codes_and_everything_is_audited(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)

        started = await _begin(http, issued.access_token, password=PASSWORD)
        assert started.status_code == 201, started.text
        assert started.headers["cache-control"] == "no-store"
        secret, factor_id = started.json()["secret"], started.json()["factor_id"]
        assert started.json()["otpauth_uri"].startswith("otpauth://totp/")
        [pending] = await _factors(session)
        assert pending.enrolled_session_id == issued.session_id
        assert pending.confirmed_at is None

        done = await _confirm(http, issued.access_token, factor_id, _code(secret))

        assert done.status_code == 200, done.text
        assert done.headers["cache-control"] == "no-store"
        codes = done.json()["recovery_codes"]
        assert len(codes) == 10
        assert len(await _recovery(session)) == 10
        assert (await session.get(Credential, USER)).mfa_enforced is True  # type: ignore[union-attr]

        for action in (
            "auth.mfa_enrolment_started",
            "auth.mfa_recovery_codes_issued",
            "auth.mfa_enrolment_confirmed",
        ):
            [row] = await _audit(session, action)
            assert row.organization_id == ORG_A, action
            assert row.actor_id == USER, action
            assert row.ip is not None and row.user_agent == "step-up-test/1", action
            assert row.request_id == "step-up-test-request", action
            assert row.after["session_id"] == str(issued.session_id), action
        [issued_row] = await _audit(session, "auth.mfa_recovery_codes_issued")
        assert issued_row.after["count"] == 10
        assert issued_row.after["replaced_previous"] is False
        [started_row] = await _audit(session, "auth.mfa_enrolment_started")
        assert started_row.after["verified_with"] == ["password"]
        assert started_row.entity_id == uuid.UUID(factor_id)

        # Nothing secret in any audit row: not the password, the seed, the codes, or a TOTP code.
        await session.rollback()
        leaves = [
            leaf
            for row in (await session.execute(select(AuditLog))).scalars().all()
            for leaf in [*_leaves(row.before), *_leaves(row.after)]
        ]
        for secret_value in (PASSWORD, secret, *codes, _code(secret)):
            assert secret_value not in leaves

        # The platform's notification hook for this fires, and says it was the first factor.
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == services.EVT_MFA_ENROLLED)
                )
            )
            .scalars()
            .all()
        )
        assert [e.payload for e in events] == [
            {"user_id": str(USER), "first_factor": True, "recovery_codes_issued": True}
        ]

    async def test_a_member_can_enrol_and_step_up_in_the_same_session(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """The dashboard's whole reason to exist: enrol on the Security page, reveal a phone
        number without signing in again."""
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _codes, _fid = await _enrol_through(http, issued.access_token)

        stepped = await http.post(
            STEP_UP, json={"code": _code(secret, 1)}, headers=_headers(issued.access_token)
        )

        assert stepped.status_code == 200, stepped.text
        claims = jwt.decode(stepped.json()["access_token"], options={"verify_signature": False})
        assert claims["amr"] == ["pwd", "otp"]
        assert claims["sid"] == str(issued.session_id)


# --------------------------------------- step-up refuses a factor added after the session


class TestAFactorAddedAfterTheSessionBegan:
    async def test_it_cannot_be_spent_by_a_session_that_did_not_enrol_it(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """Whatever slipped the factor in — the primitive here stands in for any path that is not
        the password-gated enrolment — the bearer of an *older* session cannot spend it."""
        stolen = await _sign_in(session, cfg, pair, directory)
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        await services.confirm_mfa_enrolment(
            session, factor_id=factor.id, user_id=USER, code=_code(secret), cfg=cfg
        )
        await session.commit()
        [row] = await _factors(session)

        response = await http.post(
            STEP_UP, json={"code": _code(secret, 1)}, headers=_headers(stolen.access_token)
        )

        assert response.status_code == 403
        assert _problem_type(response) == "mfa-reauthentication-required"
        assert "Sign in again" in response.json()["detail"]
        assert "access_token" not in response.json()
        [after] = await _factors(session)
        assert after.last_used_step == row.last_used_step, "the code was consumed"
        assert await _credential(session) == (0, None), "and counted"
        assert await _audit(session, "auth.step_up_failed") == []

    async def test_a_session_older_than_the_factor_is_refused_and_a_newer_one_is_not(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        older = await _sign_in(session, cfg, pair, directory)
        enrolling = await _second_session(session, cfg, pair, directory)
        secret, _codes, _fid = await _enrol_through(http, enrolling.access_token)

        # The phone that signed in before the laptop enrolled: a different session, so no.
        refused = await http.post(
            STEP_UP, json={"code": _code(secret, 1)}, headers=_headers(older.access_token)
        )
        assert refused.status_code == 403
        assert _problem_type(refused) == "mfa-reauthentication-required"

        # A sign-in after the factor exists (minted directly: a real one would stop at the MFA
        # prompt) postdates it, so it may use it — with a code that is not the one just spent.
        newer = await services.start_session(session, user_id=USER, cfg=cfg, amr=["pwd", "otp"])
        newer_token = await services.issue_for(
            session,
            pair=pair,
            cfg=cfg,
            auth_session=newer,
            membership=directory.users[0].memberships[0],
            entitlements=FakeEntitlements(),
            audience="app",
        )
        await session.commit()
        allowed = await http.post(
            STEP_UP, json={"code": _code(secret, 1)}, headers=_headers(newer_token.access_token)
        )
        assert allowed.status_code == 200, allowed.text

    async def test_in_a_mixed_account_only_the_usable_factor_is_checked(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        old_secret, _ = await _enrol(session, cfg)  # predates the session
        slipped, slipped_secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        await services.confirm_mfa_enrolment(
            session, factor_id=slipped.id, user_id=USER, code=_code(slipped_secret), cfg=cfg
        )
        await session.commit()

        # The slipped-in factor's code is simply a wrong code for the factors this session may use.
        refused = await http.post(
            STEP_UP,
            json={"code": _code(slipped_secret, 1)},
            headers=_headers(issued.access_token),
        )
        assert refused.status_code == 422
        assert _problem_type(refused) == "mfa-code-invalid"
        assert (await _credential(session))[0] == 1

        ok = await http.post(
            STEP_UP, json={"code": _code(old_secret)}, headers=_headers(issued.access_token)
        )
        assert ok.status_code == 200, ok.text

    def test_the_rule_is_two_comparisons(self) -> None:
        sid, other = uuid.uuid4(), uuid.uuid4()
        start = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
        auth = SimpleNamespace(id=sid, auth_time=start)

        def factor(confirmed_at: datetime | None, enrolled: uuid.UUID | None) -> Any:
            return SimpleNamespace(confirmed_at=confirmed_at, enrolled_session_id=enrolled)

        usable = services._factor_usable_in_session
        assert usable(factor(start - timedelta(seconds=1), None), auth) is True
        assert usable(factor(start, None), auth) is True, "the boundary is inclusive"
        assert usable(factor(start + timedelta(seconds=1), None), auth) is False
        assert usable(factor(start + timedelta(hours=1), other), auth) is False
        assert usable(factor(start + timedelta(hours=1), sid), auth) is True
        assert usable(factor(start - timedelta(days=9), other), auth) is True
        assert usable(factor(None, None), auth) is False, "an unconfirmed factor is nothing"


# ----------------------------------------------------- switch-org: no bearer-only refresh


class TestSwitchingToTheOrganizationYouAreAlreadyIn:
    async def _two_org_session(
        self, session: AsyncSession, cfg, pair, directory: FakeDirectory
    ) -> services.Issued:
        directory.users = [user_in_two_orgs()]
        outcome = await services.login(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            email="sam@example.com",
            password=PASSWORD,
            org_hint="acme",
        )
        assert outcome.status == "complete" and outcome.issued is not None
        await session.commit()
        return outcome.issued

    async def test_it_is_refused_and_mints_no_refresh_token(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await self._two_org_session(session, cfg, pair, directory)
        before = (
            await session.execute(select(func.count()).select_from(RefreshToken))
        ).scalar_one()
        await session.rollback()  # the route opens its own transaction on this session

        response = await http.post(
            SWITCH, json={"organization_id": str(ORG_A)}, headers=_headers(issued.access_token)
        )

        assert response.status_code == 409
        assert _problem_type(response) == "already-in-organization"
        assert "set-cookie" not in response.headers
        await session.rollback()
        after = (await session.execute(select(func.count()).select_from(RefreshToken))).scalar_one()
        assert after == before
        # The refresh token the session already held is untouched, not revoked as a switch would.
        live = (
            await session.execute(
                select(func.count())
                .select_from(RefreshToken)
                .where(RefreshToken.revoked_at.is_(None))
            )
        ).scalar_one()
        assert live == 1

    async def test_switching_to_a_different_organization_still_works(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """The control: the org switcher's only legitimate call."""
        issued = await self._two_org_session(session, cfg, pair, directory)

        response = await http.post(
            SWITCH, json={"organization_id": str(ORG_B)}, headers=_headers(issued.access_token)
        )

        assert response.status_code == 200, response.text
        assert response.json()["organization_id"] == str(ORG_B)
        assert "opsgrids_refresh" in response.headers["set-cookie"]

    async def test_an_organization_the_member_does_not_belong_to_is_still_a_403(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        response = await http.post(
            SWITCH, json={"organization_id": str(ORG_B)}, headers=_headers(issued.access_token)
        )
        assert response.status_code == 403


# --------------------------------------------------------------- how the routes behave


@contextmanager
def _watching(
    session: AsyncSession, directory: FakeDirectory
) -> Iterator[tuple[list[tuple[str, Any]], list[int], list[int]]]:
    """Record every SQL statement the request runs, and *how many had run* at the moment the
    directory and the entitlement client were asked."""
    seen: list[tuple[str, Any]] = []
    directory_calls: list[int] = []
    entitlement_calls: list[int] = []

    def _record(conn: Any, cursor: Any, statement: str, parameters: Any, *_: Any) -> None:
        seen.append((statement, parameters))

    class _Directory(FakeDirectory):
        async def get(self, user_id: uuid.UUID) -> DirectoryUser | None:
            directory_calls.append(len(seen))
            return await super().get(user_id)

    class _Entitlements(FakeEntitlements):
        async def for_organization(self, organization_id: Any) -> list[str]:
            entitlement_calls.append(len(seen))
            return await super().for_organization(organization_id)

    assert isinstance(session.bind, AsyncEngine)
    engine = session.bind.sync_engine
    recording = _Directory(directory.users)
    app.dependency_overrides[get_directory] = lambda: recording
    app.state.entitlements = _Entitlements()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield seen, directory_calls, entitlement_calls
    finally:
        event.remove(engine, "before_cursor_execute", _record)


class TestOrdering:
    """Both routes ask other services *before* they touch the database, so a slow account-service
    or product-service never holds a pooled connection (m7), and bind the tenant as the first
    statement they do run."""

    async def test_step_up_asks_upstream_before_the_first_statement(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        with _watching(session, directory) as (seen, directory_calls, entitlement_calls):
            response = await http.post(
                STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
            )
        assert response.status_code == 200, response.text
        assert directory_calls == [0], "the directory was asked after a query had run"
        assert entitlement_calls == [0], "entitlements were asked after a query had run"
        assert f"set_config('{TENANT_GUC}'" in seen[0][0]

    async def test_enrolment_asks_the_directory_before_the_first_statement(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        with _watching(session, directory) as (seen, directory_calls, _):
            response = await _begin(http, issued.access_token, password=PASSWORD)
        assert response.status_code == 201, response.text
        assert directory_calls == [0], "the directory was asked after a query had run"
        assert f"set_config('{TENANT_GUC}'" in seen[0][0]


def _route_calls(name: str) -> list[ast.Call]:
    tree = ast.parse(ROUTES.read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
    ]


class TestNothingReachesThePrimitive:
    def test_no_route_calls_the_unauthenticated_enrolment_primitive(self) -> None:
        """`begin_mfa_enrolment` authenticates nobody — operator enrolment and test setup call it.
        A member route that reached it would be B1 again, so the source is checked."""
        assert _route_calls("begin_mfa_enrolment") == []
        assert len(_route_calls("begin_member_enrolment")) == 1

    def test_the_confirm_route_always_supplies_the_session_and_organization(self) -> None:
        [call] = _route_calls("confirm_mfa_enrolment")
        assert {"session_id", "organization_id", "request"} <= {k.arg for k in call.keywords}


class TestTheStepUpTokenIsNotABackdoorToTheSameThings:
    async def test_a_step_up_token_cannot_begin_an_enrolment_either(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """The stepped-up token is still just a bearer: it proves nothing about the password."""
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        stepped = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )
        token = stepped.json()["access_token"]

        # The account has an authenticator, so a code is asked for too; the password is what is
        # checked first, and the step-up token does not stand in for it.
        response = await _begin(http, token, password=WRONG_PASSWORD_X, current_code="123456")

        assert response.status_code == 422
        assert _problem_type(response) == "password-incorrect"
