"""Sign-in, organization resolution, and token rotation.

The single most important test here is
`test_a_person_in_two_organizations_is_asked_which_one`. The system this platform replaces
passed a configured constant as the organization into its user lookup, which meant a second
customer *literally could not sign in*. Everything about the login flow — the pre-auth
token, the picker, the slug hint — exists so that never happens again, and this file is
what holds it in place.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import security, services
from app.config import Settings
from app.keys import KeyPair
from app.models import Credential, RefreshToken, Session
from platform_core.errors import (
    BusinessRuleProblem,
    ForbiddenProblem,
    UnauthorizedProblem,
)
from tests.conftest import (
    ORG_A,
    ORG_B,
    PASSWORD,
    USER,
    FakeDirectory,
    FakeEntitlements,
    user_in_two_orgs,
)

pytestmark = pytest.mark.integration


async def _login(
    session: AsyncSession,
    cfg: Settings,
    pair: KeyPair,
    directory: FakeDirectory,
    *,
    password: str = PASSWORD,
    org_hint: str | None = None,
) -> services.LoginOutcome:
    return await services.login(
        session,
        cfg=cfg,
        pair=pair,
        directory=directory,
        entitlements=FakeEntitlements(),
        email="sam@example.com",
        password=password,
        org_hint=org_hint,
    )


class TestCredentials:
    async def test_a_correct_password_signs_in(
        self, session, cfg, pair, directory, registered
    ) -> None:
        outcome = await _login(session, cfg, pair, directory)
        assert outcome.status == "complete"
        assert outcome.issued is not None
        assert outcome.issued.organization_id == ORG_A

    async def test_a_wrong_password_and_an_unknown_account_are_indistinguishable(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """The login form must not become an account-enumeration oracle."""
        with pytest.raises(UnauthorizedProblem) as wrong:
            await _login(session, cfg, pair, directory, password="not-the-password")

        empty = FakeDirectory([])
        with pytest.raises(UnauthorizedProblem) as unknown:
            await _login(session, cfg, pair, empty)

        assert str(wrong.value) == str(unknown.value)

    async def test_repeated_failures_lock_the_account(
        self, session, cfg, pair, directory, registered
    ) -> None:
        for _ in range(cfg.max_failed_attempts):
            with pytest.raises(UnauthorizedProblem):
                await _login(session, cfg, pair, directory, password="wrong")

        # The correct password is now refused too — that is the point of a lockout.
        with pytest.raises(ForbiddenProblem, match="temporarily locked"):
            await _login(session, cfg, pair, directory)

    async def test_the_lock_is_checked_before_the_password(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """Otherwise a locked account is still a working password oracle."""
        credential = await session.get(Credential, USER)
        assert credential is not None
        credential.locked_until = datetime.now(UTC) + timedelta(minutes=5)

        with pytest.raises(ForbiddenProblem, match="temporarily locked"):
            await _login(session, cfg, pair, directory, password="definitely-wrong")

    async def test_a_successful_sign_in_clears_the_failure_count(
        self, session, cfg, pair, directory, registered
    ) -> None:
        with pytest.raises(UnauthorizedProblem):
            await _login(session, cfg, pair, directory, password="wrong")
        await _login(session, cfg, pair, directory)

        credential = await session.get(Credential, USER)
        assert credential is not None
        assert credential.failed_attempts == 0

    async def test_a_deactivated_account_cannot_sign_in(
        self, session, cfg, pair, registered
    ) -> None:
        from tests.conftest import user_in_one_org

        user = user_in_one_org()
        deactivated = FakeDirectory(
            [
                type(user)(
                    user_id=user.user_id,
                    email=user.email,
                    full_name=user.full_name,
                    is_active=False,
                    memberships=user.memberships,
                )
            ]
        )
        with pytest.raises(ForbiddenProblem, match="deactivated"):
            await _login(session, cfg, pair, deactivated)


class TestOrganizationResolution:
    async def test_a_person_in_one_organization_goes_straight_in(
        self, session, cfg, pair, directory, registered
    ) -> None:
        outcome = await _login(session, cfg, pair, directory)
        assert outcome.status == "complete"

    async def test_a_person_in_two_organizations_is_asked_which_one(
        self, session, cfg, pair, registered
    ) -> None:
        """The regression this whole flow exists for.

        The old system resolved the organization from a configured constant, so a second
        customer could not sign in at all. Ambiguity here produces a picker, never a guess.
        """
        two = FakeDirectory([user_in_two_orgs()])
        outcome = await _login(session, cfg, pair, two)

        assert outcome.status == "org_selection_required"
        assert outcome.issued is None, "no tokens before an organization is chosen"
        assert outcome.pre_auth_token
        assert {m.organization_id for m in outcome.organizations or []} == {ORG_A, ORG_B}

    async def test_a_slug_hint_resolves_the_ambiguity(self, session, cfg, pair, registered) -> None:
        two = FakeDirectory([user_in_two_orgs()])
        outcome = await _login(session, cfg, pair, two, org_hint="beta")

        assert outcome.status == "complete"
        assert outcome.issued is not None
        assert outcome.issued.organization_id == ORG_B

    async def test_a_hint_for_an_organization_you_do_not_belong_to_is_not_honoured(
        self, session, cfg, pair, directory, registered
    ) -> None:
        # Falls back to the picker rather than silently signing them into their own org.
        outcome = await _login(session, cfg, pair, directory, org_hint="somebody-else")
        assert outcome.status == "org_selection_required"

    async def test_belonging_to_nothing_is_refused_distinctly(
        self, session, cfg, pair, registered
    ) -> None:
        """Different from a bad password: the fix is an invitation, not a retry."""
        from app.directory import DirectoryUser

        orphan = FakeDirectory(
            [
                DirectoryUser(
                    user_id=USER,
                    email="sam@example.com",
                    full_name="Sam Singleton",
                    is_active=True,
                    memberships=[],
                )
            ]
        )
        with pytest.raises(ForbiddenProblem, match="not a member of any organization"):
            await _login(session, cfg, pair, orphan)

    async def test_selecting_an_organization_completes_the_sign_in(
        self, session, cfg, pair, registered
    ) -> None:
        two = FakeDirectory([user_in_two_orgs()])
        outcome = await _login(session, cfg, pair, two)
        assert outcome.pre_auth_token

        issued = await services.select_organization(
            session,
            cfg=cfg,
            pair=pair,
            directory=two,
            entitlements=FakeEntitlements(),
            pre_auth_token=outcome.pre_auth_token,
            organization_id=ORG_B,
        )
        assert issued.organization_id == ORG_B

    async def test_you_cannot_select_an_organization_you_do_not_belong_to(
        self, session, cfg, pair, registered
    ) -> None:
        two = FakeDirectory([user_in_two_orgs()])
        outcome = await _login(session, cfg, pair, two)
        assert outcome.pre_auth_token

        with pytest.raises(ForbiddenProblem, match="not a member"):
            await services.select_organization(
                session,
                cfg=cfg,
                pair=pair,
                directory=two,
                entitlements=FakeEntitlements(),
                pre_auth_token=outcome.pre_auth_token,
                organization_id=uuid.uuid4(),
            )


class TestOrgSwitching:
    async def test_switching_keeps_the_same_session(self, session, cfg, pair, registered) -> None:
        """One browser sign-in, several organizations, one `sid`.

        That is what lets a single back-channel logout end all of it.
        """
        two = FakeDirectory([user_in_two_orgs()])
        outcome = await _login(session, cfg, pair, two, org_hint="acme")
        assert outcome.issued is not None
        first = outcome.issued

        second = await services.switch_organization(
            session,
            cfg=cfg,
            pair=pair,
            directory=two,
            entitlements=FakeEntitlements(),
            session_id=first.session_id,
            user_id=USER,
            organization_id=ORG_B,
        )

        assert second.session_id == first.session_id
        assert second.organization_id == ORG_B

    async def test_the_previous_organizations_refresh_token_stops_working(
        self, session, cfg, pair, registered
    ) -> None:
        two = FakeDirectory([user_in_two_orgs()])
        outcome = await _login(session, cfg, pair, two, org_hint="acme")
        assert outcome.issued is not None
        stale = outcome.issued.refresh_token

        await services.switch_organization(
            session,
            cfg=cfg,
            pair=pair,
            directory=two,
            entitlements=FakeEntitlements(),
            session_id=outcome.issued.session_id,
            user_id=USER,
            organization_id=ORG_B,
        )

        with pytest.raises(UnauthorizedProblem):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=two,
                raw_token=stale,
            )


class TestRefreshRotation:
    async def test_refreshing_returns_a_new_pair(
        self, session, cfg, pair, directory, registered
    ) -> None:
        outcome = await _login(session, cfg, pair, directory)
        assert outcome.issued is not None

        rotated = await services.rotate_refresh(
            session,
            entitlements=FakeEntitlements(),
            cfg=cfg,
            pair=pair,
            directory=directory,
            raw_token=outcome.issued.refresh_token,
        )
        assert rotated.refresh_token != outcome.issued.refresh_token
        assert rotated.session_id == outcome.issued.session_id

    async def test_replaying_a_spent_token_kills_the_whole_family(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """Theft detection.

        A spent token means somebody captured it — the legitimate holder already rotated.
        Revoking only that link would leave the thief holding a working chain.
        """
        outcome = await _login(session, cfg, pair, directory)
        assert outcome.issued is not None
        captured = outcome.issued.refresh_token

        fresh = await services.rotate_refresh(
            session,
            entitlements=FakeEntitlements(),
            cfg=cfg,
            pair=pair,
            directory=directory,
            raw_token=captured,
        )

        # The thief replays the token they captured.
        with pytest.raises(UnauthorizedProblem, match="security reasons"):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token=captured,
            )

        # ...and the legitimate holder's newer token is dead too. Forcing an honest
        # re-login is the correct trade against a silently shared session.
        with pytest.raises(UnauthorizedProblem):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token=fresh.refresh_token,
            )

    async def test_losing_membership_ends_the_session(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """Access must not outlive membership."""
        outcome = await _login(session, cfg, pair, directory)
        assert outcome.issued is not None

        directory.users = [
            type(directory.users[0])(
                user_id=USER,
                email="sam@example.com",
                full_name="Sam Singleton",
                is_active=True,
                memberships=[],
            )
        ]
        with pytest.raises(UnauthorizedProblem, match="access to that organization has ended"):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token=outcome.issued.refresh_token,
            )

    async def test_a_forged_token_is_refused(
        self, session, cfg, pair, directory, registered
    ) -> None:
        with pytest.raises(UnauthorizedProblem):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token="not.a.token",
            )

    async def test_logging_out_ends_the_session(
        self, session, cfg, pair, directory, registered
    ) -> None:
        outcome = await _login(session, cfg, pair, directory)
        assert outcome.issued is not None

        await services.logout(session, pair=pair, cfg=cfg, raw_token=outcome.issued.refresh_token)

        row = await session.get(Session, outcome.issued.session_id)
        assert row is not None and row.revoked_at is not None

        with pytest.raises(UnauthorizedProblem):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token=outcome.issued.refresh_token,
            )

    async def test_logging_out_twice_is_not_an_error(
        self, session, cfg, pair, directory, registered
    ) -> None:
        outcome = await _login(session, cfg, pair, directory)
        assert outcome.issued is not None
        for _ in range(2):
            await services.logout(
                session, pair=pair, cfg=cfg, raw_token=outcome.issued.refresh_token
            )


class TestPasswords:
    async def test_changing_a_password_ends_every_other_session(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """If the old password was captured, the sessions it opened are captured too."""
        outcome = await _login(session, cfg, pair, directory)
        assert outcome.issued is not None

        await services.change_password(
            session, cfg=cfg, user_id=USER, current=PASSWORD, new="a-brand-new-8"
        )

        with pytest.raises(UnauthorizedProblem):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token=outcome.issued.refresh_token,
            )

    async def test_the_new_password_must_differ(self, session, cfg, registered) -> None:
        with pytest.raises(BusinessRuleProblem, match="must be different"):
            await services.change_password(
                session, cfg=cfg, user_id=USER, current=PASSWORD, new=PASSWORD
            )

    async def test_a_reset_for_an_unknown_address_is_silently_accepted(self, session, cfg) -> None:
        """Anything else tells whoever is asking which addresses are real."""
        await services.request_password_reset(
            session, cfg=cfg, directory=FakeDirectory([]), email="nobody@example.com"
        )

    async def test_a_reset_token_works_once(self, session, cfg, directory, registered) -> None:
        from app.models import OutboxEvent
        from platform_core.errors import NotFoundProblem

        await services.request_password_reset(
            session, cfg=cfg, directory=directory, email="sam@example.com"
        )

        # The token travels to notify-service through the outbox, so a crash between
        # writing it and sending the mail cannot lose the reset.
        row = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == services.EVT_PASSWORD_RESET_REQUESTED
                )
            )
        ).scalar_one()
        raw = row.payload["token"]

        await services.confirm_password_reset(
            session, cfg=cfg, raw_token=raw, new_password="fresh-start-9"
        )
        with pytest.raises(NotFoundProblem, match="not valid"):
            await services.confirm_password_reset(
                session, cfg=cfg, raw_token=raw, new_password="another-one-9"
            )

    async def test_a_weak_password_is_refused_with_every_reason(self, session, cfg) -> None:
        from platform_core.errors import ValidationProblem

        with pytest.raises(ValidationProblem) as exc:
            await services.set_password(session, uuid.uuid4(), "password", cfg=cfg)
        codes = {e["code"] for e in exc.value.errors or []}
        assert {"too_short", "too_common", "too_simple"} <= codes


class TestMfa:
    async def test_enrolment_needs_a_working_code_before_it_turns_on(
        self, session, cfg, registered
    ) -> None:
        """Otherwise a mis-scanned QR locks somebody out of their own account."""
        factor, secret, uri = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com"
        )
        assert uri.startswith("otpauth://totp/")

        credential = await session.get(Credential, USER)
        assert credential is not None and credential.mfa_enforced is False

        code = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)
        codes = await services.confirm_mfa_enrolment(
            session, factor_id=factor.id, user_id=USER, code=code
        )

        assert len(codes) == 10
        await session.refresh(credential)
        assert credential.mfa_enforced is True

    async def test_sign_in_stops_for_a_second_factor(
        self, session, cfg, pair, directory, registered
    ) -> None:
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com"
        )
        code = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)
        await services.confirm_mfa_enrolment(session, factor_id=factor.id, user_id=USER, code=code)

        outcome = await _login(session, cfg, pair, directory)
        assert outcome.status == "mfa_required"
        assert outcome.issued is None, "no tokens before the second factor"

    async def test_a_pre_auth_token_for_mfa_cannot_skip_to_org_selection(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """Otherwise the second factor is optional in practice."""
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com"
        )
        code = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)
        await services.confirm_mfa_enrolment(session, factor_id=factor.id, user_id=USER, code=code)

        outcome = await _login(session, cfg, pair, directory)
        assert outcome.pre_auth_token

        with pytest.raises(UnauthorizedProblem, match="start signing in again"):
            await services.select_organization(
                session,
                cfg=cfg,
                pair=pair,
                directory=directory,
                entitlements=FakeEntitlements(),
                pre_auth_token=outcome.pre_auth_token,
                organization_id=ORG_A,
            )

    async def test_a_valid_code_completes_the_sign_in(
        self, session, cfg, pair, directory, registered
    ) -> None:
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com"
        )
        step = int(datetime.now(UTC).timestamp()) // 30
        await services.confirm_mfa_enrolment(
            session, factor_id=factor.id, user_id=USER, code=security._totp_at(secret, step)
        )

        outcome = await _login(session, cfg, pair, directory)
        assert outcome.pre_auth_token

        # A later step, because confirmation consumed the current one — which is exactly
        # the replay protection being exercised.
        done = await services.verify_mfa(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            pre_auth_token=outcome.pre_auth_token,
            code=security._totp_at(secret, step + 1),
        )
        assert done.status == "complete"
        assert done.issued is not None
        assert "otp" in (await session.get(Session, done.issued.session_id)).amr  # type: ignore[union-attr]

    async def test_a_code_cannot_be_replayed(self, session, cfg, registered) -> None:
        """A code shoulder-surfed from a screen must not stay usable for its window."""
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com"
        )
        step = int(datetime.now(UTC).timestamp()) // 30
        code = security._totp_at(secret, step)
        await services.confirm_mfa_enrolment(session, factor_id=factor.id, user_id=USER, code=code)

        accepted = await services._accept_second_factor(session, USER, code)
        assert accepted is False

    async def test_a_recovery_code_works_once(self, session, cfg, registered) -> None:
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com"
        )
        step = int(datetime.now(UTC).timestamp()) // 30
        codes = await services.confirm_mfa_enrolment(
            session, factor_id=factor.id, user_id=USER, code=security._totp_at(secret, step)
        )

        assert await services._accept_second_factor(session, USER, codes[0]) is True
        assert await services._accept_second_factor(session, USER, codes[0]) is False


class TestSessionsAndEvents:
    async def test_signing_in_stages_an_event_for_the_relay(
        self, session, cfg, pair, directory, registered
    ) -> None:
        from app.models import OutboxEvent

        await _login(session, cfg, pair, directory)
        rows = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == services.EVT_SIGNED_IN)
                )
            )
            .scalars()
            .all()
        )
        assert rows and all(r.published_at is None for r in rows)

    async def test_a_refresh_token_is_recorded_against_its_session(
        self, session, cfg, pair, directory, registered
    ) -> None:
        outcome = await _login(session, cfg, pair, directory)
        assert outcome.issued is not None
        rows = (
            (
                await session.execute(
                    select(RefreshToken).where(RefreshToken.session_id == outcome.issued.session_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].organization_id == ORG_A
        assert rows[0].family, "a token always belongs to a rotation family"


class TestEntitlementsReachTheToken:
    """The `ent` claim is what the gateway gates on, so it has to actually arrive.

    Before product-service existed, every token was minted with an empty list and every
    product route refused with payment-required for no reason anyone could point at. These
    assert the wiring end of that fix; product-service's own tests cover which activations
    count as entitling.
    """

    async def _sign_in(self, session, cfg, pair, directory, entitlements):
        return await services.login(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=entitlements,
            email="sam@example.com",
            password=PASSWORD,
        )

    async def test_the_claim_carries_what_the_organization_bought(
        self, session, cfg, pair, directory, registered
    ) -> None:
        entitlements = FakeEntitlements(["core", "hrms"])
        outcome = await self._sign_in(session, cfg, pair, directory, entitlements)

        assert outcome.issued is not None
        claims = jwt.decode(
            outcome.issued.access_token,
            pair.public,
            algorithms=["EdDSA"],
            audience="app",
            issuer=cfg.jwt_issuer,
        )
        assert claims["ent"] == ["core", "hrms"], (
            "the entitlement claim did not reach the token — every product route would refuse "
            "with payment-required regardless of what the customer bought"
        )

    async def test_it_asks_about_the_organization_being_signed_into(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """Not some other organization the person also belongs to.

        Asking about the wrong one would hand a customer the products of a different company
        they happen to be a member of.
        """
        entitlements = FakeEntitlements()
        outcome = await self._sign_in(session, cfg, pair, directory, entitlements)

        assert outcome.issued is not None
        assert entitlements.asked == [str(ORG_A)]

    async def test_an_unreachable_product_service_does_not_block_sign_in(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """Degrade rather than refuse.

        A customer who cannot log in at all is worse than one who logs in and finds a product
        temporarily refusing: the first is a total outage, the second looks exactly like having
        bought nothing, recovers by itself, and leaves them able to reach their account.
        Refusing here would make product-service a single point of failure for the platform.
        """
        outcome = await self._sign_in(session, cfg, pair, directory, FakeEntitlements([]))
        assert outcome.issued is not None, "sign-in must survive product-service being down"
