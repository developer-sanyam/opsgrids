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
from app.models import SCHEMA, Session
from platform_core.errors import UnauthorizedProblem
from tests.test_operator import STAFF_EMAIL, STAFF_PASSWORD, _issue_code, _make_staff

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
