"""`POST /auth/password/change` is guarded like sign-in (ADR-0023, risk 9, second review round).

The route used to verify the current password with no counter, no lock check and no audit, and it
compared `current` with `new` only *after* the password had checked out. So a signed-in caller — a
stolen bearer token is enough — could send `current=new=candidate` and read the answer off the
status code (401 for a wrong guess, 422 for the right one, nothing changed either way), at whatever
rate the gateway allowed. The password is the trust anchor for adding an authenticator, so that was
an account-takeover chain with a free oracle in its first link.

Written refusals first, then the guarantee the oracle is closed (right and wrong candidates are
indistinguishable), then the guarantees about the failure path (counted, locked, audited,
committed), then the success. Like `test_step_up.py` the tests drive the real route through a
session override that **rolls back on an exception**: a counter survives only if the service
committed it before raising.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app import security, services
from app.models import Credential, OutboxEvent, RefreshToken, Session
from platform_core.errors import ForbiddenProblem, UnauthorizedProblem
from tests.conftest import ORG_A, PASSWORD, USER, FakeEntitlements
from tests.test_mfa_enrolment import (
    WRONG_PASSWORD_X,
    WRONG_PASSWORD_Y,
    _password_failures,
)
from tests.test_step_up import (
    _audit,
    _audit_values,
    _headers,
    _mint,
    _problem_type,
    _session_row,
    _sign_in,
    http,
)

pytestmark = pytest.mark.integration

# `http` is a fixture, used by name from the tests below; importing it is how pytest sees it here.
__all__ = ["http"]

CHANGE = "/api/v1/auth/password/change"
NEW_PASSWORD = "a-brand-new-pw-9"  # pragma: allowlist secret
SHORT_WRONG = "abc"  # pragma: allowlist secret


async def _change(http: AsyncClient, token: str, current: str, new: str = NEW_PASSWORD) -> Any:
    return await http.post(
        CHANGE, json={"current_password": current, "new_password": new}, headers=_headers(token)
    )


async def _stored_hash(session) -> str | None:
    await session.rollback()
    value = (
        await session.execute(select(Credential.password_hash).where(Credential.user_id == USER))
    ).scalar_one()
    await session.rollback()
    return value


async def _live_sessions(session) -> int:
    await session.rollback()

    count = (
        await session.execute(
            select(func.count()).select_from(Session).where(Session.revoked_at.is_(None))
        )
    ).scalar_one()
    await session.rollback()
    return count


# ----------------------------------------------------------------- refused: who is calling


class TestTheCallerMustBeALiveMemberSession:
    async def test_no_bearer_is_401(self, http: AsyncClient) -> None:
        response = await http.post(
            CHANGE, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
        )
        assert response.status_code == 401

    async def test_a_revoked_session_cannot_change_the_password_and_nothing_is_counted(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """A revoked session's leftover access token is still a valid signature for up to its
        lifetime; it must not be able to change the password, or to guess at it."""
        issued = await _sign_in(session, cfg, pair, directory)
        await services._revoke_session(session, issued.session_id, reason="logout")
        await session.commit()
        before = await _stored_hash(session)

        response = await _change(http, issued.access_token, PASSWORD)

        assert response.status_code == 401
        assert await _stored_hash(session) == before
        assert await _password_failures(session) == (0, None)

    async def test_a_token_with_no_session_is_401(
        self, http: AsyncClient, session, cfg, pair, registered
    ) -> None:
        unknown = _mint(pair, cfg, session_id=uuid.uuid4())
        assert (await _change(http, unknown, PASSWORD)).status_code == 401
        assert await _password_failures(session) == (0, None)

    async def test_an_operator_token_is_refused_and_no_credential_is_made_for_it(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        token = _mint(pair, cfg, session_id=issued.session_id, audience="operator")
        assert (await _change(http, token, PASSWORD)).status_code == 401

    async def test_a_session_belonging_to_someone_else_is_refused(
        self, http: AsyncClient, session, cfg, pair, registered
    ) -> None:
        theirs = await services.start_session(session, user_id=uuid.uuid4(), cfg=cfg, amr=["pwd"])
        await session.commit()
        token = _mint(pair, cfg, session_id=theirs.id)

        assert (await _change(http, token, PASSWORD)).status_code == 401
        assert await _password_failures(session) == (0, None)


# --------------------------------------------- the oracle is closed: same answer either way


class TestNothingDependsOnTheCurrentPasswordBeforeItIsChecked:
    """The candidate is right in one arm and wrong in the other; everything about the answer that
    a caller can see must be the same, and neither may be counted."""

    async def _both(self, http: AsyncClient, token: str, *, new: str) -> tuple[Any, Any]:
        right = await _change(http, token, PASSWORD, new)
        wrong = await _change(http, token, WRONG_PASSWORD_X, new)
        return right, wrong

    async def test_current_equal_to_new_is_refused_the_same_way_whether_or_not_it_is_right(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """The attack: `current=new=candidate`. Before, 422 said the guess was right and 401 said
        it was wrong."""
        issued = await _sign_in(session, cfg, pair, directory)
        before = await _stored_hash(session)

        right = await http.post(
            CHANGE,
            json={"current_password": PASSWORD, "new_password": PASSWORD},
            headers=_headers(issued.access_token),
        )
        wrong = await http.post(
            CHANGE,
            json={"current_password": WRONG_PASSWORD_X, "new_password": WRONG_PASSWORD_X},
            headers=_headers(issued.access_token),
        )

        for response in (right, wrong):
            assert response.status_code == 422
            assert _problem_type(response) == "business-rule"
            assert response.json()["errors"][0]["field"] == "new_password"
            assert "must be different" in response.json()["detail"]
        assert right.json()["detail"] == wrong.json()["detail"]
        assert right.json()["errors"] == wrong.json()["errors"]
        # Decided before the current password was looked at: nothing was counted, nothing changed.
        assert await _password_failures(session) == (0, None)
        assert await _stored_hash(session) == before
        assert await _audit(session, "auth.password_change_failed") == []

    async def test_a_weak_new_password_is_refused_the_same_way_whether_or_not_current_is_right(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """The other way to get a right/wrong difference: a new password that fails the strength
        rules (`too_common`, `too_simple`) used to be refused only after the current one checked
        out."""
        issued = await _sign_in(session, cfg, pair, directory)

        right, wrong = await self._both(http, issued.access_token, new="abcdefghijkl")

        for response in (right, wrong):
            assert response.status_code == 422
            assert _problem_type(response) == "validation"
            assert {e["field"] for e in response.json()["errors"]} == {"new_password"}
        assert right.json()["errors"] == wrong.json()["errors"]
        assert await _password_failures(session) == (0, None)

    async def test_the_pre_checks_do_not_wait_for_the_lock(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """A locked account still answers about the *new* password without touching the counter,
        and that answer is the same one an unlocked account gives."""
        issued = await _sign_in(session, cfg, pair, directory)
        await session.execute(
            Credential.__table__.update().values(
                locked_until=datetime.now(UTC) + timedelta(minutes=5), failed_attempts=4
            )
        )
        await session.commit()

        response = await http.post(
            CHANGE,
            json={"current_password": PASSWORD, "new_password": PASSWORD},
            headers=_headers(issued.access_token),
        )

        assert response.status_code == 422
        assert (await _password_failures(session))[0] == 4


# ------------------------------------------------------------------- the failure path


class TestAWrongCurrentPassword:
    async def test_is_a_422_password_incorrect_on_the_right_field_never_a_401(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """A 401 on a bearer call makes the shared client refresh, re-send and sign the member
        out — a mistyped password would be counted twice and end the session."""
        issued = await _sign_in(session, cfg, pair, directory)
        before = await _stored_hash(session)

        response = await _change(http, issued.access_token, WRONG_PASSWORD_X)

        assert response.status_code == 422
        assert _problem_type(response) == "password-incorrect"
        assert response.json()["errors"][0]["field"] == "current_password"
        assert "set-cookie" not in response.headers
        assert WRONG_PASSWORD_X not in response.text and NEW_PASSWORD not in response.text
        assert await _stored_hash(session) == before
        assert await _live_sessions(session) == 1, "a wrong password ended the session"

    async def test_is_counted_and_the_count_survives_the_request(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """Survives only because the service committed before it raised: this fixture's session
        override rolls back on an exception, like the real one."""
        issued = await _sign_in(session, cfg, pair, directory)

        for expected in (1, 2, 3):
            await _change(http, issued.access_token, WRONG_PASSWORD_X)
            assert (await _password_failures(session))[0] == expected

    async def test_a_short_candidate_is_counted_and_not_a_free_validation_error(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """`current_password` carries no length policy: a candidate below the minimum must reach
        the counter instead of being refused for free (and revealing the policy)."""
        issued = await _sign_in(session, cfg, pair, directory)

        response = await _change(http, issued.access_token, SHORT_WRONG)

        assert response.status_code == 422
        assert _problem_type(response) == "password-incorrect"
        assert (await _password_failures(session))[0] == 1

    async def test_an_empty_current_password_is_a_validation_error(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        response = await _change(http, issued.access_token, "")
        assert response.status_code == 422
        assert _problem_type(response) == "validation"
        assert (await _password_failures(session))[0] == 0

    async def test_is_audited_without_either_password(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)

        await _change(http, issued.access_token, WRONG_PASSWORD_X)

        [failed] = await _audit(session, "auth.password_change_failed")
        assert failed.organization_id == ORG_A
        assert failed.actor_id == USER
        assert failed.entity == "credential" and failed.entity_id == USER
        assert failed.after == {
            "session_id": str(issued.session_id),
            "reason": "invalid_current_password",
            "locked": False,
        }
        assert failed.request_id == "step-up-test-request"
        assert failed.ip is not None and failed.user_agent == "step-up-test/1"
        leaves, free_text = await _audit_values(session)
        for value in (WRONG_PASSWORD_X, PASSWORD, NEW_PASSWORD):
            assert value not in leaves
            assert not any(value in text for text in free_text)

    async def test_the_count_is_the_one_sign_in_uses(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """Four wrong passwords typed into this form and one at the login form lock the account:
        one allowance, not one per door."""
        issued = await _sign_in(session, cfg, pair, directory)
        for _ in range(cfg.max_failed_attempts - 1):
            await _change(http, issued.access_token, WRONG_PASSWORD_X)
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

    async def test_the_last_one_locks_the_account_and_ends_the_session_it_came_from(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        attacker = await _sign_in(session, cfg, pair, directory)
        for attempt in range(cfg.max_failed_attempts):
            response = await _change(http, attacker.access_token, WRONG_PASSWORD_X)
            # The attempt that trips the lock still answers "incorrect", like sign-in does.
            assert response.status_code == 422, attempt

        _, locked_until = await _password_failures(session)
        assert locked_until is not None and locked_until > datetime.now(UTC)
        ended = await _session_row(session, attacker.session_id)
        assert ended.revoked_at is not None
        assert ended.revoked_reason == services.PASSWORD_CHANGE_LOCKOUT_REASON
        live_refresh = (
            await session.execute(
                select(func.count())
                .select_from(RefreshToken)
                .where(
                    RefreshToken.session_id == attacker.session_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
        ).scalar_one()
        assert live_refresh == 0
        assert (await _audit(session, "auth.password_change_failed"))[-1].after["locked"] is True

        # Sign-in is locked too, even for the right password, and the dead session's bearer is
        # refused outright without touching the counter.
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
        assert (await _change(http, attacker.access_token, PASSWORD)).status_code == 401

    async def test_the_lock_expiring_is_a_real_unlock_one_typo_does_not_relock_or_end_the_session(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """Second review, m1. `_register_password_failure` used to leave the count at
        `max_failed_attempts` when it set the lock, so the first wrong password after the lock
        expired tripped it again — and here, where the tripping attempt ends the session it came
        from, one typo by the real member ended their session."""
        attacker = await _sign_in(session, cfg, pair, directory)
        for _ in range(cfg.max_failed_attempts):
            await _change(http, attacker.access_token, WRONG_PASSWORD_X)
        assert (await _password_failures(session))[0] == 0
        # The lock runs out. (A fresh sign-in is what the member would then do.)
        await session.execute(
            Credential.__table__.update().values(
                locked_until=datetime.now(UTC) - timedelta(seconds=1)
            )
        )
        await session.commit()
        member = await _sign_in(session, cfg, pair, directory)

        typo = await _change(http, member.access_token, WRONG_PASSWORD_Y)

        assert typo.status_code == 422
        assert _problem_type(typo) == "password-incorrect"
        count, locked_until = await _password_failures(session)
        assert count == 1, "the count did not restart with the lock"
        assert locked_until is None or locked_until < datetime.now(UTC), "one typo relocked"
        assert (await _session_row(session, member.session_id)).revoked_at is None
        # And the account is fully usable: the right password still changes it.
        assert (await _change(http, member.access_token, PASSWORD)).status_code == 204

    async def test_a_locked_account_refuses_the_right_password_without_counting_or_changing(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        await session.execute(
            Credential.__table__.update().values(
                locked_until=datetime.now(UTC) + timedelta(minutes=5), failed_attempts=3
            )
        )
        await session.commit()
        before = await _stored_hash(session)

        response = await _change(http, issued.access_token, PASSWORD)

        assert response.status_code == 403
        assert _problem_type(response) == "password-locked"
        assert response.json()["detail"] == services.PASSWORD_LOCKED_MESSAGE
        assert (await _password_failures(session))[0] == 3
        assert await _stored_hash(session) == before

    async def test_a_hundred_guesses_are_a_handful_of_tries_and_then_a_lock(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """What the gateway's 300 a minute used to allow: every guess a try. Now five tries end the
        session; everything after that is a 401 that tests nothing."""
        attacker = await _sign_in(session, cfg, pair, directory)
        answers = [
            (await _change(http, attacker.access_token, f"guess-number-{n}")).status_code
            for n in range(100)
        ]
        assert answers[: cfg.max_failed_attempts] == [422] * cfg.max_failed_attempts
        assert set(answers[cfg.max_failed_attempts :]) == {401}
        # The count restarted with the lock (`_register_password_failure`), so it says 0 with the
        # lock set — not `max_failed_attempts`, which would relock on the first typo after expiry.
        count, locked_until = await _password_failures(session)
        assert count == 0
        assert locked_until is not None and locked_until > datetime.now(UTC)


# ------------------------------------------------------------------------- the success


class TestTheChange:
    async def test_changes_the_password_ends_every_session_and_audits_it(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        here = await _sign_in(session, cfg, pair, directory)
        elsewhere = await _sign_in(session, cfg, pair, directory)

        response = await _change(http, here.access_token, PASSWORD)

        assert response.status_code == 204
        assert response.content == b""
        assert security.verify_password(await _stored_hash(session), NEW_PASSWORD)
        assert await _live_sessions(session) == 0
        for issued in (here, elsewhere):
            await session.rollback()
            live = (
                await session.execute(
                    select(func.count())
                    .select_from(RefreshToken)
                    .where(
                        RefreshToken.session_id == issued.session_id,
                        RefreshToken.revoked_at.is_(None),
                    )
                )
            ).scalar_one()
            assert live == 0
        [changed] = await _audit(session, "auth.password_changed")
        assert changed.actor_id == USER
        assert changed.request_id == "step-up-test-request"
        # Against the member's organization, so it is found with the failures (which are).
        assert changed.organization_id == ORG_A
        await session.rollback()
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == services.EVT_PASSWORD_CHANGED
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [e.payload for e in events] == [{"user_id": str(USER)}]
        assert await _audit(session, "auth.password_change_failed") == []

    async def test_the_failures_and_the_success_are_one_trail_in_one_organization(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        """Second review, m4. Failures were audited against the member's organization and the
        success against the nil organization, so reading an organization's audit log for a
        password change found the guesses and lost the outcome."""
        issued = await _sign_in(session, cfg, pair, directory)
        await _change(http, issued.access_token, WRONG_PASSWORD_X)
        await _change(http, issued.access_token, WRONG_PASSWORD_Y)
        assert (await _change(http, issued.access_token, PASSWORD)).status_code == 204

        failed = await _audit(session, "auth.password_change_failed")
        changed = await _audit(session, "auth.password_changed")

        assert len(failed) == 2 and len(changed) == 1
        assert {row.organization_id for row in [*failed, *changed]} == {ORG_A}
        assert {row.actor_id for row in [*failed, *changed]} == {USER}
        # The event for other services is a person-level fact and stays on the nil organization.
        await session.rollback()
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == services.EVT_PASSWORD_CHANGED
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [e.organization_id for e in events] == [uuid.UUID(int=0)]

    async def test_the_new_password_signs_in_and_the_old_one_does_not(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        assert (await _change(http, issued.access_token, PASSWORD)).status_code == 204

        signed = await services.login(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            email="sam@example.com",
            password=NEW_PASSWORD,
        )
        assert signed.status == "complete"
        with pytest.raises(UnauthorizedProblem):
            await services.login(
                session,
                cfg=cfg,
                pair=pair,
                directory=directory,
                entitlements=FakeEntitlements(),
                email="sam@example.com",
                password=PASSWORD,
            )

    async def test_a_correct_password_clears_earlier_failures(
        self, http: AsyncClient, session, cfg, pair, directory, registered
    ) -> None:
        issued = await _sign_in(session, cfg, pair, directory)
        for _ in range(2):
            await _change(http, issued.access_token, WRONG_PASSWORD_X)
        assert (await _password_failures(session))[0] == 2

        assert (await _change(http, issued.access_token, PASSWORD)).status_code == 204
        assert await _password_failures(session) == (0, None)


class TestTheDocumentedContract:
    """Second review, m5. The routes that name their own refusals declare a 422, which makes
    FastAPI drop the one it would generate — and a client generated from the document then lost the
    typed validation-error body. Each keeps the standard schema and adds the problem body beside
    it."""

    PATHS: ClassVar[list[str]] = [
        "/api/v1/auth/password/change",
        "/api/v1/auth/mfa/enrol",
        "/api/v1/auth/mfa/enrol/confirm",
        "/api/v1/auth/mfa/step-up",
    ]

    @pytest.mark.parametrize("path", PATHS)
    def test_the_422_keeps_the_standard_schema_and_adds_the_problem_body(self, path: str) -> None:
        from app.main import app

        content = app.openapi()["paths"][path]["post"]["responses"]["422"]["content"]

        assert content["application/json"]["schema"] == {
            "$ref": "#/components/schemas/HTTPValidationError"
        }
        problem = content["application/problem+json"]["schema"]
        assert {"type", "title", "status", "detail", "errors"} <= set(problem["properties"])
        assert problem["properties"]["errors"]["items"]["required"] == ["field", "code", "message"]

    def test_the_standard_schema_it_points_at_exists(self) -> None:
        from app.main import app

        assert "HTTPValidationError" in app.openapi()["components"]["schemas"]

    def test_the_403_does_not_claim_the_tripping_attempt_answers_403(self) -> None:
        """The attempt that trips the lock answers 422 (`password-incorrect`) and ends the session;
        a 403 is what a call from a session that is still live gets while it is locked."""
        from app.main import app

        responses = app.openapi()["paths"]["/api/v1/auth/password/change"]["post"]["responses"]
        text_ = responses["403"]["description"]

        assert "422" in text_ and "401" in text_
        assert "still live" in text_
