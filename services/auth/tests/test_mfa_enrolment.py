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
import asyncio
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
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import security, services
from app.config import Settings, settings
from app.deps import get_directory
from app.directory import DirectoryUser
from app.keys import KeyPair
from app.main import app
from app.models import (
    SCHEMA,
    AuditLog,
    Credential,
    MfaFactor,
    MfaRecoveryCode,
    OutboxEvent,
    RefreshToken,
)
from platform_core.auth import Principal
from platform_core.db import TENANT_GUC
from platform_core.errors import ForbiddenProblem, NotFoundProblem, UnauthorizedProblem
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
            confirm_failed_attempts=r.confirm_failed_attempts,
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

    async def test_the_lock_expiring_is_a_real_unlock_one_typo_does_not_relock_or_end_the_session(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """Second review, m1: the count restarts with the lock, so the first wrong password after
        it expires is one failure, not a relock that also ends the session it came from."""
        attacker = await _sign_in(session, cfg, pair, directory)
        for _ in range(cfg.max_failed_attempts):
            await _begin(http, attacker.access_token, password=WRONG_PASSWORD_X)
        assert (await _password_failures(session))[0] == 0
        await session.execute(
            Credential.__table__.update().values(
                locked_until=datetime.now(UTC) - timedelta(seconds=1)
            )
        )
        await session.commit()
        member = await _sign_in(session, cfg, pair, directory)

        typo = await _begin(http, member.access_token, password=WRONG_PASSWORD_Y)

        assert typo.status_code == 422
        assert _problem_type(typo) == "password-incorrect"
        count, locked_until = await _password_failures(session)
        assert count == 1
        assert locked_until is None or locked_until < datetime.now(UTC)
        assert (await _session_row(session, member.session_id)).revoked_at is None
        assert (await _begin(http, member.access_token, password=PASSWORD)).status_code == 201

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


# ------------------------------- a pending authenticator is not a standing invitation to guess


async def _backdate(session: AsyncSession, factor_id: str, *, minutes: float) -> None:
    await session.rollback()
    await session.execute(
        MfaFactor.__table__.update()
        .where(MfaFactor.id == uuid.UUID(factor_id))
        .values(created_at=datetime.now(UTC) - timedelta(minutes=minutes))
    )
    await session.commit()


async def _confirm_counters(session: AsyncSession) -> tuple[int, int]:
    """`(password failures, mfa failures)` on the credential: what a wrong confirm code must not
    touch, so that mistyping at the setup screen cannot lock anybody out of signing in."""
    await session.rollback()
    row = (
        await session.execute(
            select(Credential.failed_attempts, Credential.mfa_failed_attempts).where(
                Credential.user_id == USER
            )
        )
    ).one()
    await session.rollback()
    return row[0], row[1]


class TestAPendingFactorExpires:
    async def test_it_cannot_be_confirmed_after_ten_minutes_and_is_gone(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        started = await _begin(http, issued.access_token, password=PASSWORD)
        secret, factor_id = started.json()["secret"], started.json()["factor_id"]
        await _backdate(session, factor_id, minutes=11)

        response = await _confirm(http, issued.access_token, factor_id, _code(secret))

        assert response.status_code == 404
        assert await _factors(session) == [], "an expired factor is voided, not left to retry"
        assert (await session.get(Credential, USER)).mfa_enforced is False  # type: ignore[union-attr]
        assert await _recovery(session) == []
        [row] = await _audit(session, "auth.mfa_enrolment_failed")
        assert row.after["reason"] == "expired" and row.actor_id == USER
        assert _code(secret) not in _leaves(row.after)

    async def test_it_is_still_good_just_inside_the_window(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        started = await _begin(http, issued.access_token, password=PASSWORD)
        secret, factor_id = started.json()["secret"], started.json()["factor_id"]
        await _backdate(session, factor_id, minutes=9)

        assert (
            await _confirm(http, issued.access_token, factor_id, _code(secret))
        ).status_code == 200

    async def test_beginning_again_replaces_a_stale_one(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        first = await _begin(http, issued.access_token, password=PASSWORD)
        stale_id = first.json()["factor_id"]
        await _backdate(session, stale_id, minutes=30)

        second = await _begin(http, issued.access_token, password=PASSWORD)
        assert second.status_code == 201
        fresh_id, secret = second.json()["factor_id"], second.json()["secret"]

        assert [str(f.id) for f in await _factors(session)] == [fresh_id]
        assert (await _confirm(http, issued.access_token, stale_id, "123456")).status_code == 404
        assert (
            await _confirm(http, issued.access_token, fresh_id, _code(secret))
        ).status_code == 200

    async def test_a_second_begin_leaves_only_the_latest_pending_factor(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """One pending enrolment per member: the first window's factor cannot be confirmed once a
        second was begun, and pending rows do not accumulate."""
        issued = await _sign_in(session, cfg, pair, directory)
        first = await _begin(http, issued.access_token, password=PASSWORD)
        second = await _begin(http, issued.access_token, password=PASSWORD)

        assert [str(f.id) for f in await _factors(session)] == [second.json()["factor_id"]]
        gone = await _confirm(
            http, issued.access_token, first.json()["factor_id"], _code(first.json()["secret"])
        )
        assert gone.status_code == 404

    async def test_confirmed_factors_are_not_touched_by_beginning_another(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _codes, factor_id = await _enrol_through(http, issued.access_token)

        started = await _begin(
            http, issued.access_token, password=PASSWORD, current_code=_code(secret, 1)
        )

        assert started.status_code == 201
        ids = {str(f.id) for f in await _factors(session)}
        assert ids == {factor_id, started.json()["factor_id"]}


class TestConfirmGuessesAreCounted:
    """Before this, confirm counted nothing and a pending factor stayed confirmable for ever, so a
    bearer that shared the beginning session could try codes at leisure against a seed only the
    real member had seen — with ten recovery codes as the prize."""

    async def test_a_wrong_code_is_counted_on_the_factor_and_the_count_survives(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        started = await _begin(http, issued.access_token, password=PASSWORD)
        secret, factor_id = started.json()["secret"], started.json()["factor_id"]

        for expected in (1, 2):
            response = await _confirm(http, issued.access_token, factor_id, _wrong_code(secret))
            assert response.status_code == 422
            assert (await _factors(session))[0].confirm_failed_attempts == expected

        rows = await _audit(session, "auth.mfa_enrolment_failed")
        assert [r.after["reason"] for r in rows] == ["invalid_code", "invalid_code"]
        assert all(r.entity_id == uuid.UUID(factor_id) for r in rows)

    async def test_a_typo_or_two_does_not_stop_the_right_code_working(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        started = await _begin(http, issued.access_token, password=PASSWORD)
        secret, factor_id = started.json()["secret"], started.json()["factor_id"]
        for _ in range(cfg_cap() - 1):
            await _confirm(http, issued.access_token, factor_id, _wrong_code(secret))

        done = await _confirm(http, issued.access_token, factor_id, _code(secret))

        assert done.status_code == 200, done.text
        assert len(done.json()["recovery_codes"]) == 10

    async def test_the_last_allowed_wrong_code_voids_the_factor(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        started = await _begin(http, issued.access_token, password=PASSWORD)
        secret, factor_id = started.json()["secret"], started.json()["factor_id"]

        answers = [
            await _confirm(http, issued.access_token, factor_id, _wrong_code(secret))
            for _ in range(cfg_cap())
        ]

        assert [a.status_code for a in answers] == [422] * cfg_cap()
        assert "cancelled" in answers[-1].json()["detail"]
        assert all("cancelled" not in a.json()["detail"] for a in answers[:-1])
        assert _problem_type(answers[-1]) == "mfa-code-invalid"
        assert answers[-1].json()["errors"][0]["field"] == "code"
        assert await _factors(session) == []
        rows = await _audit(session, "auth.mfa_enrolment_failed")
        assert rows[-1].after["reason"] == "attempts_exhausted"

        # The right code is too late: the factor is gone, and it has to be begun again.
        late = await _confirm(http, issued.access_token, factor_id, _code(secret))
        assert late.status_code == 404
        assert (await session.get(Credential, USER)).mfa_enforced is False  # type: ignore[union-attr]
        assert await _recovery(session) == []

    async def test_guessing_never_gets_more_than_the_cap_however_it_is_paced(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """A run of guesses at the confirm route ends with the factor gone — nothing an attacker
        with the beginning session can send afterwards reaches a factor to confirm."""
        issued = await _sign_in(session, cfg, pair, directory)
        started = await _begin(http, issued.access_token, password=PASSWORD)
        secret, factor_id = started.json()["secret"], started.json()["factor_id"]
        wrong = _wrong_code(secret)

        answers = [
            (await _confirm(http, issued.access_token, factor_id, wrong)).status_code
            for _ in range(50)
        ]

        assert answers[: cfg_cap()] == [422] * cfg_cap()
        assert set(answers[cfg_cap() :]) == {404}
        assert await _recovery(session) == []

    async def test_it_does_not_touch_the_sign_in_counters(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """Mistyping a code at the setup screen must not be a way to lock the member (or an
        attacker's target) out of signing in: the count is the factor's."""
        issued = await _sign_in(session, cfg, pair, directory)
        started = await _begin(http, issued.access_token, password=PASSWORD)
        secret, factor_id = started.json()["secret"], started.json()["factor_id"]

        for _ in range(cfg_cap()):
            await _confirm(http, issued.access_token, factor_id, _wrong_code(secret))

        assert await _confirm_counters(session) == (0, 0)
        assert await _credential(session) == (0, None)
        assert (await session.get(Credential, USER)).locked_until is None  # type: ignore[union-attr]

    async def test_a_right_code_is_not_counted(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        await _enrol_through(http, issued.access_token)
        assert (await _factors(session))[0].confirm_failed_attempts == 0
        assert await _audit(session, "auth.mfa_enrolment_failed") == []


def cfg_cap() -> int:
    """The number of wrong codes one pending factor takes (read at call time, so a test that
    changes the setting is honoured)."""
    return services.MFA_CONFIRM_MAX_ATTEMPTS


class TestAFactorBegunBeforeTheFirstOneWasConfirmed:
    """`begin` asks for a code from an existing factor, but a factor begun while the account had
    none was asked for only the password. It must not become a way round that question once a
    working factor exists."""

    async def test_it_cannot_be_confirmed_afterwards_and_leaves_the_real_codes_alone(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        stolen = await _sign_in(session, cfg, pair, directory)
        # Begun while there was no authenticator: the password was all that was asked.
        started = await _begin(http, stolen.access_token, password=PASSWORD)
        assert started.status_code == 201
        pending_secret, pending_id = started.json()["secret"], started.json()["factor_id"]

        # The real member then sets one up, properly, elsewhere. Not `_enrol`: that helper moves
        # `confirmed_at` an hour back so an older session can spend the factor, and this test is
        # about the order the two rows were made in.
        real, real_secret, _uri = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        real_codes = await services.confirm_mfa_enrolment(
            session, factor_id=real.id, user_id=USER, code=_code(real_secret), cfg=cfg
        )
        codes_before = await _recovery(session)
        assert len(codes_before) == 10 and len(real_codes) == 10

        response = await _confirm(http, stolen.access_token, pending_id, _code(pending_secret))

        assert response.status_code == 404
        assert "recovery_codes" not in response.text
        # The real member's set is untouched, and the only working factor is still theirs.
        assert await _recovery(session) == codes_before
        assert [f.confirmed_at is not None for f in await _factors(session)] == [True]
        [row] = await _audit(session, "auth.mfa_enrolment_failed")
        assert row.after["reason"] == "superseded"
        # It cannot be got at by beginning again without the code either.
        again = await _begin(http, stolen.access_token, password=PASSWORD)
        assert again.status_code == 422
        assert _problem_type(again) == "mfa-current-code-required"

    async def test_a_factor_begun_after_the_working_one_is_confirmed_as_usual(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _codes, _factor_id = await _enrol_through(http, issued.access_token)
        started = await _begin(
            http, issued.access_token, password=PASSWORD, current_code=_code(secret, 1)
        )

        done = await _confirm(
            http,
            issued.access_token,
            started.json()["factor_id"],
            _code(started.json()["secret"]),
        )

        assert done.status_code == 200, done.text
        assert done.json()["recovery_codes"] == []


class TestTwoConfirmationsAtOnce:
    async def test_mint_exactly_one_set_of_recovery_codes(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """A real race on two connections. Two pending factors exist for one member (the service
        primitive does not prune, which is how two can); both confirmations read "no other working
        factor" unless the credential row is locked first, and each would mint — and the second
        replace — the recovery codes. Locked, the loser finds the winner's factor confirmed after
        its own was begun, and is refused."""
        issued = await _sign_in(session, cfg, pair, directory)
        pending = []
        for _ in range(2):
            factor, secret, _uri = await services.begin_mfa_enrolment(
                session,
                USER,
                account_email="sam@example.com",
                cfg=cfg,
                session_id=str(issued.session_id),
            )
            pending.append((factor.id, secret))
        await session.commit()
        principal = Principal(
            sub=USER,
            organization_id=ORG_A,
            membership_id=MEMBERSHIP_A,
            audience="app",
            session_id=str(issued.session_id),
            amr=["pwd"],
        )

        engines: list[AsyncEngine] = []
        sessions: list[AsyncSession] = []
        for _ in range(2):
            engine = create_async_engine(
                settings().app_dsn,
                connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
            )
            engines.append(engine)
            sessions.append(async_sessionmaker(engine, expire_on_commit=False)())

        async def _attempt(s: AsyncSession, factor_id: uuid.UUID, secret: str) -> Any:
            return await services.confirm_member_enrolment(
                s,
                cfg=cfg,
                principal=principal,
                factor_id=factor_id,
                code=_code(secret),
            )

        try:
            results = await asyncio.gather(
                *(_attempt(s, fid, sec) for s, (fid, sec) in zip(sessions, pending, strict=True)),
                return_exceptions=True,
            )
        finally:
            for s in sessions:
                await s.close()
            for engine in engines:
                await engine.dispose()

        minted = [r for r in results if isinstance(r, list)]
        refused = [r for r in results if isinstance(r, NotFoundProblem)]
        assert len(minted) == 1 and len(minted[0]) == 10, results
        assert len(refused) == 1, results
        assert len(await _recovery(session)) == 10
        confirmed = [f for f in await _factors(session) if f.confirmed_at is not None]
        assert len(confirmed) == 1
        [issued_row] = await _audit(session, "auth.mfa_recovery_codes_issued")
        assert issued_row.after["count"] == 10 and issued_row.after["replaced_previous"] is False


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
        assert claims["amr"] == ["pwd", "otp", "stepup"]
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

    def test_the_confirm_route_takes_the_session_and_organization_from_the_principal(self) -> None:
        """`confirm_member_enrolment` derives both from the verified token. A route that called
        `confirm_mfa_enrolment` itself would be choosing them, and could leave them out — which
        skips the live-session and same-session checks."""
        assert _route_calls("confirm_mfa_enrolment") == []
        [call] = _route_calls("confirm_member_enrolment")
        assert {"principal", "request"} <= {k.arg for k in call.keywords}
        assert not {"session_id", "organization_id", "user_id"} & {k.arg for k in call.keywords}


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
