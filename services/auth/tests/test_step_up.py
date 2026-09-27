"""`POST /auth/mfa/step-up` — a signed-in member proves a one-time code and gets a short-lived
access token whose `amr` includes `otp` (ADR-0023).

That token is the only thing that opens the dangerous "reveal" routes for a member session that
did not present a code at login, so this file is written refusals first: everything that must
not produce a token, then the one thing that must, then the properties the token must *not*
have (a refresh token, a session-wide `otp`, a life longer than its TTL).

The wrong-code, lockout and replay tests drive the real route through a session override that
**rolls back on an exception**, the way `platform_core.db.get_session` does. The shared `client`
fixture's override does not, and with it a service that forgot to commit before raising would
still pass — the failure counter would simply survive in the test's one long transaction. Here
the counter can only survive if the service committed it.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, ClassVar

import jwt
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import platform_core.auth as core_auth
from app import security, services
from app import tokens as tk
from app.config import Settings, settings
from app.deps import get_directory, get_key
from app.directory import DirectoryUser
from app.keys import KeyPair, generate_keypair
from app.main import app
from app.models import (
    SCHEMA,
    AuditLog,
    Credential,
    MfaFactor,
    MfaRecoveryCode,
    RefreshToken,
    Session,
)
from platform_core.auth import Principal, Scope, get_principal, require
from platform_core.db import TENANT_GUC, get_session
from platform_core.errors import ForbiddenProblem, register_error_handlers
from platform_core.gateway import DEFAULT_PUBLIC_PATHS
from tests.conftest import (
    MEMBERSHIP_A,
    ORG_A,
    ORG_B,
    PASSWORD,
    USER,
    FakeDirectory,
    FakeEntitlements,
    membership,
)

pytestmark = pytest.mark.integration

STEP_UP = "/api/v1/auth/mfa/step-up"
SLUG = "https://errors.opsgrids.dev/"


# ---------------------------------------------------------------------------- helpers


class _StaticJwks:
    """Stands in for `PyJWKClient`: verifies with the key this test run generated, so a real
    bearer token goes through the real `get_principal` without a JWKS endpoint to fetch."""

    def __init__(self, pair: KeyPair) -> None:
        self._pair = pair

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return SimpleNamespace(key=self._pair.public)


@pytest_asyncio.fixture
async def http(
    session: AsyncSession,
    directory: FakeDirectory,
    pair: KeyPair,
    redis: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    async def _session_override() -> AsyncIterator[AsyncSession]:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_key] = lambda: pair
    app.dependency_overrides[get_directory] = lambda: directory
    app.state.entitlements = FakeEntitlements()
    monkeypatch.setattr(core_auth, "_jwk_client", _StaticJwks(pair))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def _now_step() -> int:
    return int(datetime.now(UTC).timestamp()) // security.TOTP_STEP


def _code(secret: str, offset: int = 0) -> str:
    return security._totp_at(secret, _now_step() + offset)


def _wrong_code(secret: str) -> str:
    """A six-digit code that is not valid for any step the server could be on."""
    valid = {_code(secret, offset) for offset in range(-3, 4)}
    return next(c for c in ("000000", "111111", "222222") if c not in valid)


async def _sign_in(
    session: AsyncSession, cfg: Settings, pair: KeyPair, directory: FakeDirectory
) -> services.Issued:
    outcome = await services.login(
        session,
        cfg=cfg,
        pair=pair,
        directory=directory,
        entitlements=FakeEntitlements(),
        email="sam@example.com",
        password=PASSWORD,
    )
    assert outcome.status == "complete" and outcome.issued is not None
    await session.commit()
    return outcome.issued


async def _enrol(session: AsyncSession, cfg: Settings) -> tuple[str, list[str]]:
    """A confirmed authenticator that **predates** every session the test has already opened,
    handed over with no step spent.

    Two things the test does to the row, neither of which the service does. Enrolment consumes
    the current step (`confirm_mfa_enrolment`), which would make the first step-up in every test
    a replay, so `last_used_step` is reset. And `confirmed_at` is moved an hour back: step-up only
    spends a factor that predates the session or was enrolled through it
    (`_factor_usable_in_session`), and these tests sign in *before* they enrol — the
    "factor added after this session began" case has its own tests in `test_mfa_enrolment.py`.
    """
    factor, secret, _uri = await services.begin_mfa_enrolment(
        session, USER, account_email="sam@example.com", cfg=cfg
    )
    recovery = await services.confirm_mfa_enrolment(
        session, factor_id=factor.id, user_id=USER, code=_code(secret), cfg=cfg
    )
    factor.last_used_step = None
    factor.confirmed_at = datetime.now(UTC) - timedelta(hours=1)
    await session.commit()
    return secret, recovery


def _headers(token: str, **extra: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Request-ID": "step-up-test-request",
        "User-Agent": "step-up-test/1",
        **extra,
    }


def _mint(
    pair: KeyPair,
    cfg: Settings,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID = USER,
    organization_id: uuid.UUID = ORG_A,
    audience: str = "app",
    amr: list[str] | None = None,
    ttl: int = 900,
) -> str:
    """An access token minted directly, for the cases a real sign-in would never produce."""
    return tk.mint_access(
        pair,
        issuer=cfg.jwt_issuer,
        user_id=user_id,
        organization_id=organization_id,
        membership_id=MEMBERSHIP_A,
        audience=audience,
        session_id=session_id,
        entitlements=["core"],
        roles=["MEMBER"],
        locations=[],
        amr=amr or ["pwd"],
        permission_version=1,
        subject_ref=None,
        ttl_seconds=ttl,
    ).token


async def _credential(session: AsyncSession) -> tuple[int, datetime | None]:
    row = (
        await session.execute(
            select(Credential.mfa_failed_attempts, Credential.mfa_locked_until).where(
                Credential.user_id == USER
            )
        )
    ).one()
    # Ends the read transaction: a route that opens its own (`get_tenant_session`) refuses a
    # session that is already in one.
    await session.rollback()
    return row[0], row[1]


async def _factor_step(session: AsyncSession) -> int | None:
    step = (await session.execute(select(MfaFactor.last_used_step))).scalar_one()
    await session.rollback()
    return step


async def _session_row(session: AsyncSession, session_id: uuid.UUID) -> Session:
    await session.rollback()
    row = await session.get(Session, session_id, populate_existing=True)
    assert row is not None
    return row


async def _audit(session: AsyncSession, action: str) -> list[SimpleNamespace]:
    """Snapshots, not ORM rows: the next call's rollback would expire them, and reading an
    expired attribute on an async session is an error rather than a reload."""
    await session.rollback()
    rows = (
        (
            await session.execute(
                select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.at)
            )
        )
        .scalars()
        .all()
    )
    columns = [c.name for c in AuditLog.__table__.columns]
    snapshot = [SimpleNamespace(**{name: getattr(row, name) for name in columns}) for row in rows]
    await session.rollback()  # a route that opens its own transaction refuses one already open
    return snapshot


def _leaves(value: Any) -> list[str]:
    """Every key and every scalar in a JSON-ish value, as strings."""
    if isinstance(value, dict):
        return [*map(str, value), *(leaf for v in value.values() for leaf in _leaves(v))]
    if isinstance(value, list):
        return [leaf for v in value for leaf in _leaves(v)]
    return [str(value)]


async def _audit_values(session: AsyncSession) -> tuple[list[str], list[str]]:
    """Every value of every audit row, for asserting what is *not* in there: `(leaves,
    free_text)`. `leaves` is every column and JSON leaf; `free_text` is the subset that could
    carry an arbitrary string (not an id, a timestamp or a number). Compared two ways because a
    six-digit code is a plausible *substring* of a UUID or a microsecond field — a substring test
    over everything is a flaky test, an exact-value test over everything misses a code buried in
    a sentence."""
    await session.rollback()
    rows = (await session.execute(select(AuditLog))).scalars().all()
    columns = [c.name for c in AuditLog.__table__.columns]
    leaves: list[str] = []
    free_text: list[str] = []
    for row in rows:
        for name in columns:
            value = getattr(row, name)
            if isinstance(value, dict):
                found = _leaves(value)
                leaves.extend(found)
                free_text.extend(x for x in found if not _looks_like_an_id(x))
            else:
                leaves.append(str(value))
                if name in ("action", "entity", "reason", "user_agent", "request_id"):
                    free_text.append(str(value))
    return leaves, free_text


def _looks_like_an_id(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return value.replace(".", "").isdigit()
    return True


def _problem_type(response: Any) -> str:
    return str(response.json()["type"]).removeprefix(SLUG)


# ---------------------------------------------------------------- refused: who is calling


class TestTheCallerMustBeASignedInMember:
    async def test_no_bearer_token_is_401_and_it_is_not_a_public_route(
        self, http: AsyncClient
    ) -> None:
        response = await http.post(STEP_UP, json={"code": "123456"})
        assert response.status_code == 401
        assert STEP_UP not in DEFAULT_PUBLIC_PATHS

    async def test_a_garbage_bearer_is_401(self, http: AsyncClient) -> None:
        response = await http.post(STEP_UP, json={"code": "123456"}, headers=_headers("nope"))
        assert response.status_code == 401

    async def test_a_token_signed_by_another_key_is_401(
        self, http: AsyncClient, session, cfg, pair, registered
    ) -> None:
        forged = _mint(generate_keypair(), cfg, session_id=uuid.uuid4())
        response = await http.post(STEP_UP, json={"code": "123456"}, headers=_headers(forged))
        assert response.status_code == 401

    async def test_an_expired_bearer_is_401(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        stale = _mint(pair, cfg, session_id=issued.session_id, ttl=-30)
        response = await http.post(STEP_UP, json={"code": "123456"}, headers=_headers(stale))
        assert response.status_code == 401

    async def test_a_refresh_token_is_not_a_bearer(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        response = await http.post(
            STEP_UP, json={"code": "123456"}, headers=_headers(issued.refresh_token)
        )
        assert response.status_code == 401

    async def test_an_operator_audience_token_is_refused(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        token = _mint(pair, cfg, session_id=issued.session_id, audience="operator")
        response = await http.post(STEP_UP, json={"code": "123456"}, headers=_headers(token))
        assert response.status_code == 401

    async def test_an_unknown_session_is_401(
        self, http: AsyncClient, session, cfg, pair, registered
    ) -> None:
        token = _mint(pair, cfg, session_id=uuid.uuid4())
        response = await http.post(STEP_UP, json={"code": "123456"}, headers=_headers(token))
        assert response.status_code == 401

    async def test_a_revoked_session_is_401_and_nothing_is_counted(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        await services._revoke_session(session, issued.session_id, reason="test")
        await session.commit()

        response = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )

        assert response.status_code == 401
        assert await _credential(session) == (0, None)
        assert await _factor_step(session) is None

    async def test_a_session_belonging_to_someone_else_is_401(
        self, http: AsyncClient, session, cfg, pair, registered
    ) -> None:
        """The token names `sub=USER` but a `sid` that is another person's live session: the
        pair is checked, not each half on its own."""
        theirs = await services.start_session(
            session, user_id=uuid.uuid4(), cfg=cfg, amr=["pwd", "otp"]
        )
        await session.commit()
        token = _mint(pair, cfg, session_id=theirs.id)
        response = await http.post(STEP_UP, json={"code": "123456"}, headers=_headers(token))
        assert response.status_code == 401

    async def test_a_token_for_an_organization_the_member_does_not_belong_to_is_401(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        token = _mint(pair, cfg, session_id=issued.session_id, organization_id=ORG_B)

        response = await http.post(STEP_UP, json={"code": _code(secret)}, headers=_headers(token))

        assert response.status_code == 401
        assert await _factor_step(session) is None

    async def test_a_member_whose_membership_has_ended_is_401(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        directory.users = [
            DirectoryUser(
                user_id=USER,
                email="sam@example.com",
                full_name="Sam Singleton",
                is_active=True,
                memberships=[membership(ORG_A, MEMBERSHIP_A, "acme", "Acme", status="removed")],
            )
        ]

        response = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )

        assert response.status_code == 401
        assert await _factor_step(session) is None

    async def test_a_deactivated_account_is_401(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        directory.users = [
            DirectoryUser(
                user_id=USER,
                email="sam@example.com",
                full_name="Sam Singleton",
                is_active=False,
                memberships=directory.users[0].memberships,
            )
        ]
        response = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )
        assert response.status_code == 401


# ------------------------------------------------------------------ refused: the request


class TestTheRequestBody:
    async def test_the_body_cannot_name_the_user_or_the_session(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)

        response = await http.post(
            STEP_UP,
            json={"code": _code(secret), "user_id": str(uuid.uuid4())},
            headers=_headers(issued.access_token),
        )

        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "extra_forbidden"
        assert await _factor_step(session) is None

    @pytest.mark.parametrize("body", [{}, {"code": "123"}, {"code": ""}, {"code": None}])
    async def test_a_missing_or_short_code_is_a_validation_error(
        self, http: AsyncClient, session, cfg, pair, directory, registered, body: dict
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        response = await http.post(STEP_UP, json=body, headers=_headers(issued.access_token))
        assert response.status_code == 422

    async def test_letters_are_not_a_code_and_are_not_counted(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        await _enrol(session, cfg)
        response = await http.post(
            STEP_UP, json={"code": "abcdef"}, headers=_headers(issued.access_token)
        )
        assert response.status_code == 422
        assert await _credential(session) == (0, None)


# -------------------------------------------------------------- refused: account state


class TestWhenThereIsNothingToStepUpWith:
    async def test_a_member_with_no_factor_gets_a_409_with_a_stable_slug(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)

        response = await http.post(
            STEP_UP, json={"code": "123456"}, headers=_headers(issued.access_token)
        )

        assert response.status_code == 409
        assert _problem_type(response) == "mfa-not-enrolled"
        assert await _credential(session) == (0, None)

    async def test_a_factor_that_was_never_confirmed_is_not_enrolled(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        _factor, secret, _uri = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        await session.commit()

        response = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )

        assert response.status_code == 409
        assert _problem_type(response) == "mfa-not-enrolled"

    async def test_a_member_with_no_credential_row_at_all_is_not_enrolled_and_gets_none_made(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        await session.execute(Credential.__table__.delete())
        await session.commit()

        response = await http.post(
            STEP_UP, json={"code": "123456"}, headers=_headers(issued.access_token)
        )

        assert response.status_code == 409
        await session.rollback()
        count = await session.execute(select(func.count()).select_from(Credential))
        assert count.scalar_one() == 0


class TestSingleSignOnSessions:
    async def _sso_bearer(
        self, session: AsyncSession, cfg: Settings, pair: KeyPair, directory: FakeDirectory
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

    async def test_a_federated_session_is_refused_with_its_own_slug(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        secret, _ = await _enrol(session, cfg)
        token, sso_id = await self._sso_bearer(session, cfg, pair, directory)

        response = await http.post(STEP_UP, json={"code": _code(secret)}, headers=_headers(token))

        assert response.status_code == 403
        assert _problem_type(response) == "mfa-unavailable-for-sso"
        assert "single sign-on" in response.json()["detail"]
        # The code was neither checked nor spent, and nothing was counted.
        assert await _factor_step(session) is None
        assert await _credential(session) == (0, None)
        assert "otp" not in (await _session_row(session, sso_id)).amr

    async def test_an_unrecognised_identity_provider_prefix_fails_closed(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        secret, _ = await _enrol(session, cfg)
        odd = await services.start_session(
            session, user_id=USER, cfg=cfg, amr=["pwd"], idp="something-new"
        )
        await session.commit()
        token = _mint(pair, cfg, session_id=odd.id)

        response = await http.post(STEP_UP, json={"code": _code(secret)}, headers=_headers(token))

        assert response.status_code == 403
        assert _problem_type(response) == "mfa-unavailable-for-sso"


# ---------------------------------------------------------------------- refused: recovery


class TestRecoveryCodesAreNotAccepted:
    async def test_a_valid_recovery_code_is_refused_and_left_unspent(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        _secret, recovery = await _enrol(session, cfg)

        response = await http.post(
            STEP_UP, json={"code": recovery[0]}, headers=_headers(issued.access_token)
        )

        assert response.status_code == 422
        body = response.json()
        assert body["errors"][0]["field"] == "code"
        assert body["errors"][0]["code"] == "totp_required"
        assert "Recovery codes cannot be used" in body["detail"]
        assert "access_token" not in body

        await session.rollback()
        used = (
            await session.execute(
                select(func.count())
                .select_from(MfaRecoveryCode)
                .where(MfaRecoveryCode.used_at.isnot(None))
            )
        ).scalar_one()
        assert used == 0
        # Refused before it was read or counted: no strike, no audit row.
        assert await _credential(session) == (0, None)
        assert await _audit(session, "auth.step_up_failed") == []

    async def test_the_verifier_itself_cannot_burn_a_recovery_code(
        self, session, cfg, registered
    ) -> None:
        """The shape gate above is the first line; this is the structural one. With recovery
        turned off, `_accept_second_factor` never reads `mfa_recovery_codes` for writing, so a
        future caller that forgets the gate still cannot spend a break-glass code."""
        _secret, recovery = await _enrol(session, cfg)

        refused = await services._accept_second_factor(
            session, USER, recovery[0], cfg=cfg, allow_recovery=False
        )
        assert refused is None
        unspent = (
            await session.execute(
                select(func.count())
                .select_from(MfaRecoveryCode)
                .where(MfaRecoveryCode.used_at.is_(None))
            )
        ).scalar_one()
        assert unspent == 10

        # The control: the login path, unchanged, does spend it.
        assert await services._accept_second_factor(session, USER, recovery[1], cfg=cfg) == (
            "recovery"
        )


# ----------------------------------------------------------- refused: wrong and replayed


class TestWrongCodes:
    async def test_a_wrong_code_is_a_422_that_is_counted_and_audited(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        wrong = _wrong_code(secret)

        response = await http.post(
            STEP_UP, json={"code": wrong}, headers=_headers(issued.access_token)
        )

        # 422 and not 401: see `MfaCodeInvalidProblem` — a 401 would make the shared client
        # refresh and re-send this very request, counting the same typo twice.
        assert response.status_code == 422
        assert _problem_type(response) == "mfa-code-invalid"
        assert "access_token" not in response.json()
        assert wrong not in response.text
        # These two survive only because the service committed before it raised (the `http`
        # fixture rolls back on an exception).
        assert (await _credential(session))[0] == 1
        failed = await _audit(session, "auth.step_up_failed")
        assert len(failed) == 1
        assert failed[0].after["reason"] == "invalid_code"
        assert failed[0].after["locked"] is False

    async def test_a_correct_code_after_wrong_ones_clears_the_count(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        for _ in range(2):
            await http.post(
                STEP_UP, json={"code": _wrong_code(secret)}, headers=_headers(issued.access_token)
            )
        assert (await _credential(session))[0] == 2

        ok = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )

        assert ok.status_code == 200
        assert await _credential(session) == (0, None)

    async def test_the_last_wrong_code_locks_the_account_and_ends_the_session(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        attacker = await _sign_in(session, cfg, pair, directory)
        other_device = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)

        for attempt in range(cfg.max_failed_attempts):
            response = await http.post(
                STEP_UP,
                json={"code": _wrong_code(secret)},
                headers=_headers(attacker.access_token),
            )
            # Same as `verify_mfa`: the attempt that trips the lock still answers "not valid".
            assert response.status_code == 422, attempt

        failures, locked_until = await _credential(session)
        assert locked_until is not None and locked_until > datetime.now(UTC)
        # The count restarts with the lock, so its expiry is a real unlock.
        assert failures == 0

        ended = await _session_row(session, attacker.session_id)
        assert ended.revoked_at is not None
        assert ended.revoked_reason == services.STEP_UP_LOCKOUT_REASON
        live_tokens = (
            await session.execute(
                select(func.count())
                .select_from(RefreshToken)
                .where(
                    RefreshToken.session_id == attacker.session_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        assert live_tokens == 0
        failed = await _audit(session, "auth.step_up_failed")
        assert len(failed) == cfg.max_failed_attempts
        assert failed[-1].after["locked"] is True

        # The same door, on a session that is still alive, is now shut — even for the
        # correct code, which is not checked and so not spent.
        shut = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(other_device.access_token)
        )
        assert shut.status_code == 403
        assert _problem_type(shut) == "mfa-locked"
        assert shut.json()["detail"] == services.MFA_LOCKED_MESSAGE
        assert await _factor_step(session) is None

    async def test_the_lock_is_the_one_login_uses_and_expiry_is_the_unlock(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """The coupling ADR-0023 records as an accepted risk: guesses made through step-up lock
        the *login* second factor too, because it is one secret with one counter."""
        attacker = await _sign_in(session, cfg, pair, directory)
        other_device = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        for _ in range(cfg.max_failed_attempts):
            await http.post(
                STEP_UP,
                json={"code": _wrong_code(secret)},
                headers=_headers(attacker.access_token),
            )

        login = await services.login(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            email="sam@example.com",
            password=PASSWORD,
        )
        assert login.status == "mfa_required"
        with pytest.raises(ForbiddenProblem, match="temporarily locked"):
            await services.verify_mfa(
                session,
                cfg=cfg,
                pair=pair,
                directory=directory,
                entitlements=FakeEntitlements(),
                pre_auth_token=login.pre_auth_token,
                code=_code(secret),
            )
        await session.rollback()

        await session.execute(
            Credential.__table__.update().values(
                mfa_locked_until=datetime.now(UTC) - timedelta(seconds=1)
            )
        )
        await session.commit()
        ok = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(other_device.access_token)
        )
        assert ok.status_code == 200


class TestReplay:
    async def test_the_same_code_twice_in_one_step_is_refused_with_a_wait_message(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        code = _code(secret)

        first = await http.post(STEP_UP, json={"code": code}, headers=_headers(issued.access_token))
        again = await http.post(STEP_UP, json={"code": code}, headers=_headers(issued.access_token))

        assert first.status_code == 200
        assert again.status_code == 422
        assert _problem_type(again) == "mfa-code-replayed"
        assert "Wait for the next code" in again.json()["detail"]
        assert "access_token" not in again.json()
        # Counted, like any refusal: a replay is not a free guess.
        assert (await _credential(session))[0] == 1
        failed = await _audit(session, "auth.step_up_failed")
        assert [row.after["reason"] for row in failed] == ["replayed_code"]

    async def test_the_code_that_just_signed_the_member_in_cannot_be_reused(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """The realistic collision: type a code at login, open a reveal a few seconds later,
        and the authenticator app is still showing the same six digits."""
        secret, _ = await _enrol(session, cfg)
        login = await services.login(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            email="sam@example.com",
            password=PASSWORD,
        )
        code = _code(secret)
        done = await services.verify_mfa(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            pre_auth_token=login.pre_auth_token,
            code=code,
        )
        assert done.issued is not None
        await session.commit()

        response = await http.post(
            STEP_UP, json={"code": code}, headers=_headers(done.issued.access_token)
        )
        assert response.status_code == 422
        assert _problem_type(response) == "mfa-code-replayed"

        # The next code works, on the very same session.
        fresh = await http.post(
            STEP_UP, json={"code": _code(secret, 1)}, headers=_headers(done.issued.access_token)
        )
        assert fresh.status_code == 200

    async def test_two_submissions_of_one_correct_code_at_once_mint_exactly_one_token(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """A real race on two connections: the credential row lock serialises them, so the
        loser reads `last_used_step` already advanced and is refused as a replay. Without the
        lock both would read the old step and both would be handed a token."""
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        code = _code(secret)
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

        async def _attempt(s: AsyncSession) -> Any:
            return await services.step_up(
                s,
                cfg=cfg,
                pair=pair,
                directory=directory,
                entitlements=FakeEntitlements(),
                principal=principal,
                code=code,
            )

        try:
            results = await asyncio.gather(*(_attempt(s) for s in sessions), return_exceptions=True)
        finally:
            for s in sessions:
                await s.close()
            for engine in engines:
                await engine.dispose()

        minted = [r for r in results if isinstance(r, services.AccessOnlyIssued)]
        refused = [r for r in results if isinstance(r, services.MfaCodeReplayedProblem)]
        assert len(minted) == 1, results
        assert len(refused) == 1, results


# ---------------------------------------------------------------------------- the success


class TestTheStepUpToken:
    async def test_it_carries_otp_and_everything_else_the_session_token_carried(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        before = jwt.decode(issued.access_token, options={"verify_signature": False})
        assert before["amr"] == ["pwd"]

        response = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {
            "status",
            "access_token",
            "token_type",
            "expires_in",
            "organization_id",
            "membership_id",
        }
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == cfg.step_up_token_ttl_seconds == 180
        assert body["organization_id"] == str(ORG_A)
        assert body["membership_id"] == str(MEMBERSHIP_A)

        claims = jwt.decode(
            body["access_token"],
            pair.public,
            algorithms=["EdDSA"],
            audience="app",
            issuer=cfg.jwt_issuer,
        )
        assert claims["typ"] == "access"
        assert claims["amr"] == ["pwd", "otp"]
        for same in ("sub", "act_org", "sid", "mem", "pv", "aud", "iss", "ent", "roles", "loc"):
            assert claims[same] == before[same], same
        assert claims["jti"] != before["jti"]
        assert "stf" not in claims
        assert claims["exp"] - claims["iat"] == cfg.step_up_token_ttl_seconds
        assert claims["exp"] <= int(datetime.now(UTC).timestamp()) + cfg.step_up_token_ttl_seconds

    async def test_it_is_an_access_token_and_nothing_else(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        tokens_before = (
            await session.execute(select(func.count()).select_from(RefreshToken))
        ).scalar_one()

        response = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )

        assert response.status_code == 200
        assert "refresh_token" not in response.json()
        assert "set-cookie" not in response.headers
        assert response.headers["cache-control"] == "no-store"
        await session.rollback()
        tokens_after = (
            await session.execute(select(func.count()).select_from(RefreshToken))
        ).scalar_one()
        assert tokens_after == tokens_before

    async def test_the_session_does_not_remember_it(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)

        response = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )
        assert response.status_code == 200

        row = await _session_row(session, issued.session_id)
        assert list(row.amr) == ["pwd"]
        assert row.mfa_verified_at is None
        assert row.revoked_at is None

    async def test_a_refresh_after_a_step_up_does_not_gain_otp(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        stepped = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )
        assert jwt.decode(stepped.json()["access_token"], options={"verify_signature": False})[
            "amr"
        ] == ["pwd", "otp"]

        rotated = await services.rotate_refresh(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            raw_token=issued.refresh_token,
        )
        assert isinstance(rotated, services.Issued)
        assert jwt.decode(rotated.access_token, options={"verify_signature": False})["amr"] == [
            "pwd"
        ]
        # ...and the rotation after that one, too.
        again = await services.rotate_refresh(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            raw_token=rotated.refresh_token,
        )
        assert jwt.decode(again.access_token, options={"verify_signature": False})["amr"] == ["pwd"]

    async def test_a_session_that_already_holds_otp_is_not_given_it_twice(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        secret, _ = await _enrol(session, cfg)
        login = await services.login(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            email="sam@example.com",
            password=PASSWORD,
        )
        done = await services.verify_mfa(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            pre_auth_token=login.pre_auth_token,
            code=_code(secret),
        )
        assert done.issued is not None
        await session.commit()

        response = await http.post(
            STEP_UP,
            json={"code": _code(secret, 1)},
            headers=_headers(done.issued.access_token),
        )

        assert response.status_code == 200
        claims = jwt.decode(response.json()["access_token"], options={"verify_signature": False})
        assert claims["amr"] == ["pwd", "otp"]

    async def test_the_ttl_is_the_configured_one(
        self, http: AsyncClient, session, cfg, pair, directory, registered, monkeypatch
    ) -> None:
        monkeypatch.setattr(cfg, "step_up_token_ttl_seconds", 45)
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)

        response = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )

        assert response.json()["expires_in"] == 45
        claims = jwt.decode(response.json()["access_token"], options={"verify_signature": False})
        assert claims["exp"] - claims["iat"] == 45

    async def test_it_never_outlives_an_ordinary_access_token(
        self, http: AsyncClient, session, cfg, pair, directory, registered, monkeypatch
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        monkeypatch.setattr(cfg, "access_token_ttl_seconds", 60)

        response = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )

        assert response.json()["expires_in"] == 60

    async def test_a_success_binds_the_tenant_before_it_reads_anything(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """The route holds a principal but takes a bare session (it must commit its own failure
        counter), so the chokepoint's guarantee is given by the service instead: the tenant GUC
        is the transaction's first statement."""
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        seen: list[tuple[str, Any]] = []

        def _record(conn: Any, cursor: Any, statement: str, parameters: Any, *_: Any) -> None:
            seen.append((statement, parameters))

        assert isinstance(session.bind, AsyncEngine)
        engine = session.bind.sync_engine
        event.listen(engine, "before_cursor_execute", _record)
        try:
            response = await http.post(
                STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
            )
        finally:
            event.remove(engine, "before_cursor_execute", _record)

        assert response.status_code == 200
        first, params = seen[0]
        assert f"set_config('{TENANT_GUC}'" in first, first
        assert str(ORG_A) in str(params)


class TestTheAuditTrail:
    async def test_success_and_failure_are_audited_with_who_where_and_which_request(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        await http.post(
            STEP_UP, json={"code": _wrong_code(secret)}, headers=_headers(issued.access_token)
        )
        await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )

        [ok] = await _audit(session, "auth.step_up_verified")
        [bad] = await _audit(session, "auth.step_up_failed")
        for row in (ok, bad):
            assert row.actor_id == USER
            assert row.organization_id == ORG_A
            assert row.entity == "session"
            assert row.entity_id == issued.session_id
            assert row.user_agent == "step-up-test/1"
            assert row.request_id == "step-up-test-request"
            assert row.ip is not None
            assert row.after["session_id"] == str(issued.session_id)
        assert ok.after == {
            "session_id": str(issued.session_id),
            "method": "totp",
            "ttl_seconds": 180,
        }

    async def test_no_audit_row_and_no_response_ever_contains_the_code(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, recovery = await _enrol(session, cfg)
        wrong = _wrong_code(secret)
        good = _code(secret)

        responses = [
            await http.post(STEP_UP, json={"code": wrong}, headers=_headers(issued.access_token)),
            await http.post(STEP_UP, json={"code": good}, headers=_headers(issued.access_token)),
            await http.post(STEP_UP, json={"code": good}, headers=_headers(issued.access_token)),
            await http.post(
                STEP_UP, json={"code": recovery[0]}, headers=_headers(issued.access_token)
            ),
        ]

        leaves, free_text = await _audit_values(session)
        for secret_value in (wrong, good, recovery[0]):
            assert secret_value not in leaves
            assert not any(secret_value in text for text in free_text)
            for response in responses:
                assert secret_value not in response.text
        # `record_audit` redacts by key name and `code` is not on that list (it lives in
        # platform-core, which this slice must not touch), so the guarantee is structural: the
        # `after` payloads are built from a fixed set of outcome fields and the code is never an
        # input to them. Pin that set, so adding a field is a decision, not an accident.
        allowed = {"session_id", "method", "ttl_seconds", "reason", "locked"}
        for action in ("auth.step_up_verified", "auth.step_up_failed"):
            for row in await _audit(session, action):
                assert set(row.after) <= allowed
                assert row.before is None


# ---------------------------------------------------------------- the reveal-route guard


class TestTheTokenOpensTheRevealRoutes:
    """Exercised against `require(perm, amr={"otp"})` itself, on a throwaway app, for the four
    permissions that carry that guard today. The bearer token goes through the real
    `get_principal`; only the permission grant (which the gateway resolves per request and no
    token carries) is supplied by the harness."""

    PERMISSIONS: ClassVar[list[str]] = [
        "crm.book.contact.read_sensitive",
        "hrms.people.employee.read_sensitive",
        "storefront.sales.enquiry.unmask",
        "engage.inbox.message.unmask",
    ]

    @staticmethod
    def _guarded_app(permission: str, *, granted: bool) -> FastAPI:
        mini = FastAPI()
        mini.state.settings = settings()
        register_error_handlers(mini)

        def _principal(request: Request) -> Principal:
            principal = get_principal(request)
            grants = [f"{permission}:ORG"] if granted else []
            return principal.model_copy(update={"perms": grants})

        mini.dependency_overrides[get_principal] = _principal

        # Declared on the decorator rather than as an annotated parameter: this module postpones
        # annotation evaluation, and FastAPI cannot resolve a closure variable (`permission`)
        # inside a string annotation, so the parameter form silently becomes a query field.
        @mini.get(
            "/reveal",
            dependencies=[Depends(require(permission, amr={"otp"}, minimum=Scope.ORG))],
        )
        def reveal() -> dict[str, bool]:
            return {"revealed": True}

        return mini

    @pytest.mark.parametrize("permission", PERMISSIONS)
    async def test_the_step_up_token_passes_and_the_session_token_does_not(
        self, http: AsyncClient, session, cfg, pair, directory, registered, permission: str
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        stepped = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )
        assert stepped.status_code == 200

        async with AsyncClient(
            transport=ASGITransport(app=self._guarded_app(permission, granted=True)),
            base_url="http://guard",
        ) as guard:
            plain = await guard.get("/reveal", headers=_headers(issued.access_token))
            assert plain.status_code == 403
            assert "Additional authentication required: otp" in plain.json()["detail"]

            opened = await guard.get("/reveal", headers=_headers(stepped.json()["access_token"]))
            assert opened.status_code == 200, opened.text
            assert opened.json() == {"revealed": True}

    async def test_the_step_up_does_not_grant_the_permission_itself(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        secret, _ = await _enrol(session, cfg)
        stepped = await http.post(
            STEP_UP, json={"code": _code(secret)}, headers=_headers(issued.access_token)
        )

        async with AsyncClient(
            transport=ASGITransport(app=self._guarded_app(self.PERMISSIONS[0], granted=False)),
            base_url="http://guard",
        ) as guard:
            refused = await guard.get("/reveal", headers=_headers(stepped.json()["access_token"]))
        assert refused.status_code == 403
        assert "Missing permission" in refused.json()["detail"]

    def test_the_claim_lands_on_the_principal(self, pair: KeyPair, cfg: Settings) -> None:
        token = tk.mint_access(
            pair,
            issuer=cfg.jwt_issuer,
            user_id=USER,
            organization_id=ORG_A,
            membership_id=MEMBERSHIP_A,
            audience="app",
            session_id=uuid.uuid4(),
            entitlements=[],
            roles=[],
            locations=[],
            amr=services._dedupe_amr(["pwd", "otp"]),
            permission_version=1,
            subject_ref=None,
            ttl_seconds=30,
        ).token
        claims = jwt.decode(token, options={"verify_signature": False})
        assert core_auth._principal_from_claims(claims).amr == ["pwd", "otp"]


# --------------------------------------------------------------------------- the config


class TestTheTtlSetting:
    def _settings(self, **overrides: object) -> Settings:
        base: dict[str, object] = {
            "internal_service_token": SecretStr("x" * 40 + "-internal-token-value"),
            "pii_encryption_key": SecretStr("y" * 40 + "-pii-encryption-key"),
        }
        return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[arg-type]

    def test_the_default_is_three_minutes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("STEP_UP_TOKEN_TTL_SECONDS", raising=False)
        assert self._settings().step_up_token_ttl_seconds == 180

    @pytest.mark.parametrize("value", [30, 180, 600])
    def test_values_inside_the_range_are_accepted(self, value: int) -> None:
        assert self._settings(step_up_token_ttl_seconds=value).step_up_token_ttl_seconds == value

    @pytest.mark.parametrize("value", [-1, 0, 29, 601, 900, 86_400])
    def test_values_outside_the_range_refuse_to_start(self, value: int) -> None:
        with pytest.raises(ValidationError, match="step_up_token_ttl_seconds"):
            self._settings(step_up_token_ttl_seconds=value)

    def test_it_can_be_set_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STEP_UP_TOKEN_TTL_SECONDS", "90")
        assert self._settings().step_up_token_ttl_seconds == 90
        monkeypatch.setenv("STEP_UP_TOKEN_TTL_SECONDS", "5000")
        with pytest.raises(ValidationError, match="step_up_token_ttl_seconds"):
            self._settings()
