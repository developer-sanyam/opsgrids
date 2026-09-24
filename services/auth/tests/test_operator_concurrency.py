"""Genuine concurrency, against real PostgreSQL and two independent connections — the same
shape `services/leave/tests/test_concurrency.py` and `services/finance/tests/test_concurrency.py`
use, for the identical reason: only a real server can arbitrate two transactions racing for the
same row. The one shared fixture `session` cannot exercise a race against itself.

Two races, both closing MAJOR 4 of the 2026-09-24 security review:

1. Two concurrent redemptions of the same enrolment code must not both succeed — the code is a
   single-use proof, and `_redeem_enrollment_code`'s atomic claim (plus the `StaffAccount` row
   lock every operator MFA function now takes) is what makes that true under a real race rather
   than only under the check-then-act ordering a single test connection would happen to exercise.
2. Two concurrent, otherwise-valid completions of the same MFA step must not both mint a token
   pair for the same session — `_claim_mfa_verified`'s atomic `UPDATE ... WHERE mfa_verified_at
   IS NULL` is what decides the second one lost, rather than a plain Python assignment that
   cannot tell it was racing anyone.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import services
from app.config import settings
from app.models import SCHEMA, RefreshToken, Session
from platform_core.errors import UnauthorizedProblem
from tests.test_operator import (
    STAFF_EMAIL,
    STAFF_PASSWORD,
    _enrol_operator_mfa,
    _issue_code,
    _make_staff,
)

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _second_session() -> tuple[AsyncSession, AsyncEngine]:
    """A second, independent connection — the race needs two real sessions, not the one shared
    fixture session everything else in this suite uses."""
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    return maker(), engine


class TestEnrolmentCodeRedemptionRace:
    async def test_two_concurrent_redemptions_of_the_same_code_do_not_both_succeed(
        self, session, cfg, pair
    ) -> None:
        staff = await _make_staff(session)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        code = await _issue_code(session, staff.id)
        await session.commit()

        second, engine = await _second_session()
        try:

            async def _begin(s: AsyncSession) -> object:
                return await services.operator_begin_mfa_enrolment(
                    s,
                    cfg=cfg,
                    pair=pair,
                    pre_auth_token=outcome.pre_auth_token,
                    enrollment_code=code,
                )

            results = await asyncio.gather(_begin(session), _begin(second), return_exceptions=True)
        finally:
            await second.close()
            await engine.dispose()

        successes = [r for r in results if not isinstance(r, BaseException)]
        failures = [r for r in results if isinstance(r, BaseException)]
        assert len(successes) == 1, f"exactly one redemption must win, got {results!r}"
        assert len(failures) == 1
        assert isinstance(failures[0], UnauthorizedProblem)
        assert "not valid" in str(failures[0])


class TestMfaVerifiedClaimRace:
    """`_claim_mfa_verified` itself, raced directly rather than through `operator_verify_mfa`.

    Going through the full function would not actually exercise a *lost* claim: the
    `StaffAccount` row lock every operator MFA function now takes (MAJOR 4) already
    serialises the two calls end to end, so by the time the second one runs, the first has
    already committed and the second fails earlier, on an already-spent code. That is still a
    correct outcome, but it tests the row lock, not the claim. This races the claim primitive
    on its own — two sessions, the same `Session` row, concurrently — which is what actually
    proves the atomic `UPDATE ... WHERE mfa_verified_at IS NULL` is what decides the loser,
    independent of any lock a caller happens to also take.
    """

    async def test_two_concurrent_claims_of_the_same_session_do_not_both_win(
        self, session, cfg, pair
    ) -> None:
        await _make_staff(session)
        outcome = await services.operator_login(
            session, cfg=cfg, pair=pair, email=STAFF_EMAIL, password=STAFF_PASSWORD
        )
        await session.commit()
        claims = jwt.decode(outcome.pre_auth_token, options={"verify_signature": False})
        session_id = uuid.UUID(claims["sid"])

        second, engine = await _second_session()
        try:
            first_row = await session.get(Session, session_id)
            second_row = await second.get(Session, session_id)
            assert first_row is not None and second_row is not None

            async def _claim(s: AsyncSession, row: Session) -> bool:
                won = await services._claim_mfa_verified(s, row)
                await s.commit()
                return won

            results = await asyncio.gather(_claim(session, first_row), _claim(second, second_row))
        finally:
            await second.close()
            await engine.dispose()

        assert sorted(results) == [False, True], f"exactly one claim must win, got {results!r}"


def _jti(raw: str) -> str:
    return jwt.decode(raw, options={"verify_signature": False})["jti"]


class TestOperatorRefreshGraceWindow:
    """The operator side of M1 and MINOR 3 of the 2026-09-24 SSO review — `operator_rotate_
    refresh` shares `rotate_refresh`'s marker-only grace window and row lock verbatim; this is
    what proves "operator path identical" rather than assuming it from reading the same code."""

    async def test_a_grace_hit_answers_access_only_and_stores_no_token_in_redis(
        self, session, cfg, pair, redis
    ) -> None:
        await _make_staff(session)
        issued, _codes, _secret, _step = await _enrol_operator_mfa(session, cfg, pair)
        await session.commit()
        spent = issued.refresh_token

        winner = await services.operator_rotate_refresh(
            session, cfg=cfg, pair=pair, raw_token=spent
        )
        assert isinstance(winner, services.OperatorIssued)

        marker_values = [v for k, v in redis.store.items() if k.startswith("auth:refresh_grace:")]
        assert len(marker_values) == 1
        assert winner.access_token not in marker_values[0]
        assert winner.refresh_token not in marker_values[0]
        assert f"auth:refresh_grace:{_jti(spent)}" not in redis.store

        grace = await services.operator_rotate_refresh(session, cfg=cfg, pair=pair, raw_token=spent)
        assert isinstance(grace, services.OperatorAccessOnlyIssued)
        assert not hasattr(grace, "refresh_token")
        assert grace.access_token != winner.access_token
        assert grace.staff_id == winner.staff_id

    async def test_a_revoked_operator_family_never_gets_the_grace_window(
        self, session, cfg, pair, redis
    ) -> None:
        await _make_staff(session)
        issued, _codes, _secret, _step = await _enrol_operator_mfa(session, cfg, pair)
        await session.commit()
        spent = issued.refresh_token

        winner = await services.operator_rotate_refresh(
            session, cfg=cfg, pair=pair, raw_token=spent
        )
        assert isinstance(winner, services.OperatorIssued)

        # The thief replays the captured token, well outside any legitimate two-tabs shape —
        # simulated here directly, the same way `test_signin.py`'s tenant equivalent does, by
        # pushing `rotated_at` back past the grace window rather than waiting on a real clock.
        stored = await session.get(RefreshToken, _jti(spent))
        assert stored is not None
        stored.rotated_at = datetime.now(UTC) - timedelta(
            seconds=cfg.refresh_reuse_grace_seconds + 1
        )
        await session.commit()

        with pytest.raises(UnauthorizedProblem, match="security reasons"):
            await services.operator_rotate_refresh(session, cfg=cfg, pair=pair, raw_token=spent)

    async def test_two_truly_concurrent_first_rotations_do_not_fork_the_operator_family(
        self, session, cfg, pair, redis
    ) -> None:
        await _make_staff(session)
        issued, _codes, _secret, _step = await _enrol_operator_mfa(session, cfg, pair)
        await session.commit()
        captured = issued.refresh_token

        second, engine = await _second_session()
        try:

            async def _rotate(s: AsyncSession) -> object:
                return await services.operator_rotate_refresh(
                    s, cfg=cfg, pair=pair, raw_token=captured
                )

            results = await asyncio.gather(_rotate(session), _rotate(second))
        finally:
            await second.close()
            await engine.dispose()

        refreshed = [r for r in results if isinstance(r, services.OperatorIssued)]
        access_only = [r for r in results if isinstance(r, services.OperatorAccessOnlyIssued)]
        assert len(refreshed) == 1
        assert len(access_only) == 1
