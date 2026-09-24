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
from app.models import Credential, MfaFactor, MfaRecoveryCode, RefreshToken, Session
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
            # pragma: allowlist nextline secret
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
                # pragma: allowlist nextline secret
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
            # pragma: allowlist nextline secret
            await _login(session, cfg, pair, directory, password="definitely-wrong")

    async def test_a_successful_sign_in_clears_the_failure_count(
        self, session, cfg, pair, directory, registered
    ) -> None:
        with pytest.raises(UnauthorizedProblem):
            # pragma: allowlist nextline secret
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

    async def test_a_deactivated_person_cannot_switch_organizations(
        self, session, cfg, pair, registered
    ) -> None:
        """Final-review MAJOR: `rotate_refresh` refuses a deactivated person's still-live
        session; `switch_organization` only asked whether they exist, and minted a fresh pair
        for them."""
        from dataclasses import replace

        two = FakeDirectory([user_in_two_orgs()])
        outcome = await _login(session, cfg, pair, two, org_hint="acme")
        assert outcome.issued is not None
        two.users = [replace(two.users[0], is_active=False)]

        tokens_before = len((await session.execute(select(RefreshToken))).scalars().all())
        with pytest.raises(UnauthorizedProblem):
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
        tokens_after = len((await session.execute(select(RefreshToken))).scalars().all())
        assert tokens_after == tokens_before, "nothing may be minted"

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

    async def test_replaying_a_just_rotated_token_within_the_grace_window_gets_the_same_pair(
        self, session, cfg, pair, directory, registered, redis
    ) -> None:
        """Two tabs sharing one refresh cookie can both present the token an instant apart —
        a proactive refresh in one racing a 401 retry in another. That must not read as
        theft: both walk away with the same successor pair, not a forced re-login."""
        outcome = await _login(session, cfg, pair, directory)
        assert outcome.issued is not None
        captured = outcome.issued.refresh_token

        first = await services.rotate_refresh(
            session,
            entitlements=FakeEntitlements(),
            cfg=cfg,
            pair=pair,
            directory=directory,
            raw_token=captured,
        )

        # The second tab, a moment later, presents the same now-spent token.
        second = await services.rotate_refresh(
            session,
            entitlements=FakeEntitlements(),
            cfg=cfg,
            pair=pair,
            directory=directory,
            raw_token=captured,
        )

        assert second.access_token == first.access_token
        assert second.refresh_token == first.refresh_token

        # The family is intact — the successor still rotates normally, once.
        third = await services.rotate_refresh(
            session,
            entitlements=FakeEntitlements(),
            cfg=cfg,
            pair=pair,
            directory=directory,
            raw_token=first.refresh_token,
        )
        assert third.refresh_token != first.refresh_token

    async def test_replaying_a_token_older_than_the_grace_window_still_reads_as_theft(
        self, session, cfg, pair, directory, registered, redis
    ) -> None:
        outcome = await _login(session, cfg, pair, directory)
        assert outcome.issued is not None
        captured = outcome.issued.refresh_token

        await services.rotate_refresh(
            session,
            entitlements=FakeEntitlements(),
            cfg=cfg,
            pair=pair,
            directory=directory,
            raw_token=captured,
        )

        # Pretend the rotation happened long enough ago that the grace window has closed.
        claims = jwt.decode(captured, options={"verify_signature": False})
        stored = await session.get(RefreshToken, claims["jti"])
        assert stored is not None
        grace = cfg.refresh_reuse_grace_seconds
        stored.rotated_at = datetime.now(UTC) - timedelta(seconds=grace + 5)
        await session.commit()

        with pytest.raises(UnauthorizedProblem, match="security reasons"):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token=captured,
            )

    async def test_a_revoked_family_never_gets_the_grace_window(
        self, session, cfg, pair, directory, registered, redis
    ) -> None:
        """Grace only ever applies to a family still in good standing. Once something else has
        ended it outright — an ordinary logout, here — the token whose rotation is still well
        inside the grace window must not be resurrected by it."""
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
        # Signing out ends the whole family — `captured`'s row is revoked too, not only rotated.
        await services.logout(session, pair=pair, cfg=cfg, raw_token=fresh.refresh_token)

        with pytest.raises(UnauthorizedProblem):
            await services.rotate_refresh(
                session,
                entitlements=FakeEntitlements(),
                cfg=cfg,
                pair=pair,
                directory=directory,
                raw_token=captured,
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
            session,
            cfg=cfg,
            raw_token=raw,
            new_password="fresh-start-9",  # pragma: allowlist secret
        )
        with pytest.raises(NotFoundProblem, match="not valid"):
            await services.confirm_password_reset(
                session,
                cfg=cfg,
                raw_token=raw,
                new_password="another-one-9",  # pragma: allowlist secret
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
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        assert uri.startswith("otpauth://totp/")

        credential = await session.get(Credential, USER)
        assert credential is not None and credential.mfa_enforced is False

        code = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)
        codes = await services.confirm_mfa_enrolment(
            session, factor_id=factor.id, user_id=USER, code=code, cfg=cfg
        )

        assert len(codes) == 10
        await session.refresh(credential)
        assert credential.mfa_enforced is True

    async def test_the_secret_is_sealed_not_stored_in_the_clear(
        self, session, cfg, registered
    ) -> None:
        """MAJOR 6: a TOTP secret is a durable credential and gets the same envelope a
        password hash or an OIDC client secret gets — never the plain base32 bytes."""
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        assert factor.secret_enc is not None
        assert secret.encode() not in factor.secret_enc
        from platform_core import pii

        # MINOR 8: sealed with the per-row AAD, so opening it back requires that same context —
        # not just the key.
        aad = f"mfa_factor:{USER}".encode()
        assert pii.open_sealed(factor.secret_enc, settings=cfg, aad=aad) == secret

    async def test_sign_in_stops_for_a_second_factor(
        self, session, cfg, pair, directory, registered
    ) -> None:
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        code = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)
        await services.confirm_mfa_enrolment(
            session, factor_id=factor.id, user_id=USER, code=code, cfg=cfg
        )

        outcome = await _login(session, cfg, pair, directory)
        assert outcome.status == "mfa_required"
        assert outcome.issued is None, "no tokens before the second factor"

    async def test_a_pre_auth_token_for_mfa_cannot_skip_to_org_selection(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """Otherwise the second factor is optional in practice."""
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        code = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)
        await services.confirm_mfa_enrolment(
            session, factor_id=factor.id, user_id=USER, code=code, cfg=cfg
        )

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
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        step = int(datetime.now(UTC).timestamp()) // 30
        await services.confirm_mfa_enrolment(
            session,
            factor_id=factor.id,
            user_id=USER,
            code=security._totp_at(secret, step),
            cfg=cfg,
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

    async def test_a_replayed_mfa_pre_auth_token_is_refused(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """Once a session has completed its second factor, the same pre-auth token must not
        produce a second completed sign-in (the operator-side vulnerability, exercised here
        for the shared machinery tenant sign-in also goes through)."""
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        step = int(datetime.now(UTC).timestamp()) // 30
        await services.confirm_mfa_enrolment(
            session,
            factor_id=factor.id,
            user_id=USER,
            code=security._totp_at(secret, step),
            cfg=cfg,
        )

        outcome = await _login(session, cfg, pair, directory)
        assert outcome.pre_auth_token
        token = outcome.pre_auth_token

        await services.verify_mfa(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            pre_auth_token=token,
            code=security._totp_at(secret, step + 1),
        )

        with pytest.raises(UnauthorizedProblem, match="start signing in again"):
            await services.verify_mfa(
                session,
                cfg=cfg,
                pair=pair,
                directory=directory,
                entitlements=FakeEntitlements(),
                pre_auth_token=token,
                code=security._totp_at(secret, step + 2),
            )

    async def test_a_code_cannot_be_replayed(self, session, cfg, registered) -> None:
        """A code shoulder-surfed from a screen must not stay usable for its window."""
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        step = int(datetime.now(UTC).timestamp()) // 30
        code = security._totp_at(secret, step)
        await services.confirm_mfa_enrolment(
            session, factor_id=factor.id, user_id=USER, code=code, cfg=cfg
        )

        accepted = await services._accept_second_factor(session, USER, code, cfg=cfg)
        assert accepted is None

    async def test_a_recovery_code_works_once(self, session, cfg, registered) -> None:
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        step = int(datetime.now(UTC).timestamp()) // 30
        codes = await services.confirm_mfa_enrolment(
            session,
            factor_id=factor.id,
            user_id=USER,
            code=security._totp_at(secret, step),
            cfg=cfg,
        )

        assert await services._accept_second_factor(session, USER, codes[0], cfg=cfg) == "recovery"
        assert await services._accept_second_factor(session, USER, codes[0], cfg=cfg) is None

    async def test_confirming_an_already_confirmed_factor_is_refused(
        self, session, cfg, registered
    ) -> None:
        """Re-confirming is not a redo of a prior success — it is either a replay or a race,
        and either way there is nothing left for it to legitimately do."""
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        step = int(datetime.now(UTC).timestamp()) // 30
        await services.confirm_mfa_enrolment(
            session,
            factor_id=factor.id,
            user_id=USER,
            code=security._totp_at(secret, step),
            cfg=cfg,
        )
        with pytest.raises(UnauthorizedProblem, match="already confirmed"):
            await services.confirm_mfa_enrolment(
                session,
                factor_id=factor.id,
                user_id=USER,
                code=security._totp_at(secret, step + 1),
                cfg=cfg,
            )

    async def test_repeated_wrong_codes_lock_the_account_and_revoke_the_session(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """BLOCKER 1 ("also") of the 2026-09-24 security review: tenant `verify_mfa` had no
        failure counter at all — an unlimited number of TOTP/recovery-code guesses against a
        pre-auth session that had already survived the password step. This pins the fix:
        `Credential.mfa_failed_attempts`/`mfa_locked_until`, and a lockout that also revokes
        the session and its refresh tokens, matching the operator side."""
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        step = int(datetime.now(UTC).timestamp()) // 30
        await services.confirm_mfa_enrolment(
            session,
            factor_id=factor.id,
            user_id=USER,
            code=security._totp_at(secret, step),
            cfg=cfg,
        )

        outcome = await _login(session, cfg, pair, directory)
        for _ in range(cfg.max_failed_attempts):
            with pytest.raises(UnauthorizedProblem, match="not valid"):
                await services.verify_mfa(
                    session,
                    cfg=cfg,
                    pair=pair,
                    directory=directory,
                    entitlements=FakeEntitlements(),
                    pre_auth_token=outcome.pre_auth_token,
                    code="000000",
                )

        credential = await session.get(Credential, USER)
        assert credential is not None
        assert credential.mfa_locked_until is not None

        # A fresh login still succeeds — the password counter is untouched by any of this —
        # but the *next* MFA attempt on the new token is refused for the lock, before the
        # code is even checked.
        fresh = await _login(session, cfg, pair, directory)
        with pytest.raises(ForbiddenProblem, match="temporarily locked"):
            await services.verify_mfa(
                session,
                cfg=cfg,
                pair=pair,
                directory=directory,
                entitlements=FakeEntitlements(),
                pre_auth_token=fresh.pre_auth_token,
                code="000000",
            )

        claims = jwt.decode(outcome.pre_auth_token, options={"verify_signature": False})
        failing_session = await session.get(Session, uuid.UUID(claims["sid"]))
        assert failing_session is not None
        assert failing_session.revoked_at is not None
        assert failing_session.revoked_reason == "mfa_failed_attempts"

    async def test_the_tenant_count_restarts_when_the_lock_trips(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """m7, tenant side: after the lock expires, one wrong code is one strike — not an
        immediate re-lock. Expiry *is* the unlock for a tenant member."""
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        step = int(datetime.now(UTC).timestamp()) // 30
        await services.confirm_mfa_enrolment(
            session,
            factor_id=factor.id,
            user_id=USER,
            code=security._totp_at(secret, step),
            cfg=cfg,
        )
        outcome = await _login(session, cfg, pair, directory)
        for _ in range(cfg.max_failed_attempts):
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
        credential = await session.get(Credential, USER)
        assert credential is not None
        assert credential.mfa_locked_until is not None
        assert credential.mfa_failed_attempts == 0

        credential.mfa_locked_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

        fresh = await _login(session, cfg, pair, directory)
        with pytest.raises(UnauthorizedProblem, match="not valid"):
            await services.verify_mfa(
                session,
                cfg=cfg,
                pair=pair,
                directory=directory,
                entitlements=FakeEntitlements(),
                pre_auth_token=fresh.pre_auth_token,
                code="000000",
            )
        await session.refresh(credential)
        assert credential.mfa_failed_attempts == 1

    async def test_a_federated_session_may_not_enrol_mfa(self, session, cfg, registered) -> None:
        """NIT (round 3): enrolling a second factor is an account-wide credential change — a
        federated session only ever proved that one organization's identity provider
        vouches for this person, for one sign-in, and has no standing to make it."""
        auth_session = await services.start_session(
            session, user_id=USER, cfg=cfg, amr=["sso"], idp=f"oidc:{uuid.uuid4()}"
        )
        await session.commit()

        with pytest.raises(ForbiddenProblem, match="password sign-in"):
            await services.begin_mfa_enrolment(
                session,
                USER,
                account_email="sam@example.com",
                cfg=cfg,
                session_id=str(auth_session.id),
            )

    async def test_a_federated_session_may_not_confirm_mfa_either(
        self, session, cfg, registered
    ) -> None:
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        await session.commit()
        auth_session = await services.start_session(
            session, user_id=USER, cfg=cfg, amr=["sso"], idp=f"oidc:{uuid.uuid4()}"
        )
        await session.commit()
        code = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)

        with pytest.raises(ForbiddenProblem, match="password sign-in"):
            await services.confirm_mfa_enrolment(
                session,
                factor_id=factor.id,
                user_id=USER,
                code=code,
                cfg=cfg,
                session_id=str(auth_session.id),
            )

    async def test_a_password_session_may_still_enrol_mfa(self, session, cfg, registered) -> None:
        """The control: an ordinary password session (and a caller that passes no session id
        at all — a request predating this check) is unaffected."""
        auth_session = await services.start_session(session, user_id=USER, cfg=cfg, amr=["pwd"])
        await session.commit()
        _factor, _secret, uri = await services.begin_mfa_enrolment(
            session,
            USER,
            account_email="sam@example.com",
            cfg=cfg,
            session_id=str(auth_session.id),
        )
        assert uri.startswith("otpauth://totp/")


class TestLegacyPlaintextFactorNeverLocksAMemberOut:
    """R1 of the final security review. A box deployed from `main` stores every tenant TOTP
    secret as plaintext, and auth refuses a plaintext row unless told otherwise. Without this,
    every correct code from such a member was counted as a *failure*: five tries, then a
    fifteen-minute lock with no admin unlock. A refusal caused by *our* state must count for
    nothing, say so distinctly, and leave the recovery codes working."""

    async def _legacy_member(self, session, secret: str) -> Credential:
        session.add(
            MfaFactor(
                user_id=USER,
                type="totp",
                label="Authenticator app",
                secret_enc=secret.encode("ascii"),
                confirmed_at=datetime.now(UTC),
            )
        )
        session.add(
            MfaRecoveryCode(user_id=USER, code_hash=security.hash_recovery_code("abcd-efgh"))
        )
        credential = await session.get(Credential, USER)
        assert credential is not None
        credential.mfa_enforced = True
        await session.commit()
        return credential

    async def _verify(self, session, cfg, pair, directory, token: str, code: str):
        return await services.verify_mfa(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            pre_auth_token=token,
            code=code,
        )

    async def test_a_correct_code_is_a_distinct_503_and_never_counts_or_locks(
        self, session, cfg, pair, directory, registered
    ) -> None:
        assert cfg.mfa_accept_legacy_plaintext is False
        secret = security.generate_totp_secret()
        credential = await self._legacy_member(session, secret)
        outcome = await _login(session, cfg, pair, directory)
        assert outcome.status == "mfa_required"
        correct = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)

        for _ in range(cfg.max_failed_attempts + 2):
            with pytest.raises(services.MfaResealPendingProblem) as raised:
                await self._verify(session, cfg, pair, directory, outcome.pre_auth_token, correct)
            assert raised.value.status_code == 503
            assert "recovery code" in raised.value.detail

        await session.refresh(credential)
        assert credential.mfa_failed_attempts == 0
        assert credential.mfa_locked_until is None

    async def test_a_recovery_code_still_signs_them_in(
        self, session, cfg, pair, directory, registered
    ) -> None:
        secret = security.generate_totp_secret()
        await self._legacy_member(session, secret)
        outcome = await _login(session, cfg, pair, directory)
        correct = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)
        with pytest.raises(services.MfaResealPendingProblem):
            await self._verify(session, cfg, pair, directory, outcome.pre_auth_token, correct)

        done = await self._verify(
            session, cfg, pair, directory, outcome.pre_auth_token, "abcd-efgh"
        )
        assert done.status == "complete"

    async def test_a_guess_that_is_not_a_six_digit_code_still_counts(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """Only a six-digit miss is ambiguous (it may have been right). Anything else can only
        have been a recovery-code guess, and must not be free to grind."""
        credential = await self._legacy_member(session, security.generate_totp_secret())
        outcome = await _login(session, cfg, pair, directory)
        with pytest.raises(UnauthorizedProblem, match="not valid"):
            await self._verify(session, cfg, pair, directory, outcome.pre_auth_token, "wxyz-1234")
        await session.refresh(credential)
        assert credential.mfa_failed_attempts == 1

    async def test_a_wrong_code_on_a_sealed_factor_still_counts(
        self, session, cfg, pair, directory, registered
    ) -> None:
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        step = int(datetime.now(UTC).timestamp()) // 30
        await services.confirm_mfa_enrolment(
            session,
            factor_id=factor.id,
            user_id=USER,
            code=security._totp_at(secret, step),
            cfg=cfg,
        )
        outcome = await _login(session, cfg, pair, directory)
        with pytest.raises(UnauthorizedProblem, match="not valid"):
            await self._verify(session, cfg, pair, directory, outcome.pre_auth_token, "000000")
        credential = await session.get(Credential, USER)
        assert credential is not None and credential.mfa_failed_attempts == 1

    async def _sealed_member(self, session, cfg) -> str:
        """A member with a confirmed *sealed* factor; returns its secret."""
        factor, secret, _ = await services.begin_mfa_enrolment(
            session, USER, account_email="sam@example.com", cfg=cfg
        )
        step = int(datetime.now(UTC).timestamp()) // 30
        await services.confirm_mfa_enrolment(
            session,
            factor_id=factor.id,
            user_id=USER,
            code=security._totp_at(secret, step),
            cfg=cfg,
        )
        await session.commit()
        return secret

    async def test_a_mixed_account_counts_guesses_against_the_readable_factor(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """The reviewer's probe. A member with one refused legacy-plaintext factor *and* one
        readable sealed factor is not in the "we could not check" state: a six-digit miss is a
        real miss against a real factor. It used to raise the uncounted 503 whenever *any*
        factor was refused — sixty wrong guesses, counter 0, no lock, then the right code signed
        in."""
        assert cfg.mfa_accept_legacy_plaintext is False
        secret = await self._sealed_member(session, cfg)
        session.add(
            MfaFactor(
                user_id=USER,
                type="totp",
                label="old",
                secret_enc=security.generate_totp_secret().encode("ascii"),
                confirmed_at=datetime.now(UTC),
            )
        )
        await session.commit()
        outcome = await _login(session, cfg, pair, directory)
        assert outcome.status == "mfa_required"
        step = int(datetime.now(UTC).timestamp()) // 30
        valid = {security._totp_at(secret, step + offset) for offset in (-1, 0, 1)}

        guesses = [g for g in (f"{n:06d}" for n in range(100000, 100020)) if g not in valid]
        for guess in guesses[: cfg.max_failed_attempts]:
            with pytest.raises(UnauthorizedProblem, match="not valid"):
                await self._verify(session, cfg, pair, directory, outcome.pre_auth_token, guess)

        credential = await session.get(Credential, USER)
        assert credential is not None
        assert credential.mfa_locked_until is not None, "the guesses counted, so the lock tripped"
        # ... and the right code no longer signs in: the account is locked.
        fresh = await _login(session, cfg, pair, directory)
        with pytest.raises(ForbiddenProblem, match="temporarily locked"):
            await self._verify(
                session,
                cfg,
                pair,
                directory,
                fresh.pre_auth_token,
                security._totp_at(secret, step + 1),
            )

    async def test_a_single_refused_factor_still_gives_the_uncounted_503(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """The other side of the same rule: with *no* readable factor there was nothing to
        check the code against, so it counts for nothing (R1)."""
        secret = security.generate_totp_secret()
        credential = await self._legacy_member(session, secret)
        outcome = await _login(session, cfg, pair, directory)
        correct = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)
        with pytest.raises(services.MfaResealPendingProblem):
            await self._verify(session, cfg, pair, directory, outcome.pre_auth_token, correct)
        await session.refresh(credential)
        assert credential.mfa_failed_attempts == 0

    async def test_confirming_a_new_factor_drops_the_refused_legacy_one(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """At the source: once a working factor is confirmed, a confirmed legacy-plaintext
        factor the deployment refuses is dropped and audited, so the account is no longer mixed."""
        from app.models import AuditLog

        legacy = MfaFactor(
            user_id=USER,
            type="totp",
            label="old",
            secret_enc=security.generate_totp_secret().encode("ascii"),
            confirmed_at=datetime.now(UTC),
        )
        session.add(legacy)
        await session.commit()
        legacy_id = legacy.id

        await self._sealed_member(session, cfg)

        remaining = (
            (await session.execute(select(MfaFactor).where(MfaFactor.user_id == USER)))
            .scalars()
            .all()
        )
        assert [f.id for f in remaining if f.id == legacy_id] == []
        assert len(remaining) == 1
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "auth.mfa_factor_legacy_dropped",
                    AuditLog.entity_id == legacy_id,
                )
            )
        ).scalar_one_or_none()
        assert audit is not None

    async def test_a_legacy_factor_is_kept_while_the_deployment_still_accepts_it(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """With acceptance on the legacy factor still works (and the reseal will seal it), so
        confirming a new one must not throw it away."""
        legacy_cfg = cfg.model_copy(update={"mfa_accept_legacy_plaintext": True})
        session.add(
            MfaFactor(
                user_id=USER,
                type="totp",
                label="old",
                secret_enc=security.generate_totp_secret().encode("ascii"),
                confirmed_at=datetime.now(UTC),
            )
        )
        await session.commit()
        await self._sealed_member(session, legacy_cfg)
        remaining = (
            (await session.execute(select(MfaFactor).where(MfaFactor.user_id == USER)))
            .scalars()
            .all()
        )
        assert len(remaining) == 2

    async def test_a_six_digit_guess_never_runs_the_recovery_hashes(
        self, session, cfg, pair, directory, registered, monkeypatch
    ) -> None:
        """A six-digit input cannot match `xxxx-xxxx-xxxx`, so the Argon2 verify per recovery
        code (up to ten per guess) is skipped; anything else still runs them."""
        await self._sealed_member(session, cfg)
        outcome = await _login(session, cfg, pair, directory)
        calls: list[str] = []
        real = security.verify_recovery_code

        def counting(hashed: str, code: str) -> bool:
            calls.append(code)
            return real(hashed, code)

        monkeypatch.setattr(security, "verify_recovery_code", counting)
        with pytest.raises(UnauthorizedProblem):
            await self._verify(session, cfg, pair, directory, outcome.pre_auth_token, "000000")
        assert calls == []
        with pytest.raises(UnauthorizedProblem):
            await self._verify(session, cfg, pair, directory, outcome.pre_auth_token, "zzzz-zzzz")
        assert calls, "a non-TOTP-shaped guess still has to be checked as a recovery code"

    async def test_a_unicode_digit_code_is_a_counted_miss_not_a_500(
        self, session, cfg, pair, directory, registered
    ) -> None:
        """`str.isdigit()` is true for fullwidth digits and `hmac.compare_digest` raises
        TypeError on non-ASCII — a 500 on a submitted code. It is an ordinary counted miss."""
        await self._sealed_member(session, cfg)
        outcome = await _login(session, cfg, pair, directory)
        fullwidth = "".join(chr(0xFF10 + int(d)) for d in "123456")
        with pytest.raises(UnauthorizedProblem, match="not valid"):
            await self._verify(session, cfg, pair, directory, outcome.pre_auth_token, fullwidth)
        credential = await session.get(Credential, USER)
        assert credential is not None and credential.mfa_failed_attempts == 1


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
