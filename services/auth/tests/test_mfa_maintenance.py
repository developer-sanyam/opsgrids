"""The one-off deploy scripts behind `bin/reseal-mfa-factors` and `bin/reset-staff-mfa`.

MAJOR 3 and MINOR 10 of the 2026-09-24 security review.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import security, services
from app.mfa_maintenance import reseal_mfa_factors, reset_all_staff_mfa
from app.models import AuditLog, MfaFactor, MfaRecoveryCode, StaffAccount
from platform_core import pii
from tests.test_operator import STAFF_EMAIL, _enrol_operator_mfa, _make_staff

pytestmark = pytest.mark.integration


@pytest.fixture
def legacy_cfg(cfg):
    """A box that has deliberately turned `MFA_ACCEPT_LEGACY_PLAINTEXT` on for the reseal
    window (m4 of the third-round review) — off everywhere else, including the plain `cfg`."""
    return cfg.model_copy(update={"mfa_accept_legacy_plaintext": True})


class TestResealMfaFactors:
    async def test_a_legacy_plaintext_row_is_resealed_and_counted(
        self, session, cfg, legacy_cfg
    ) -> None:
        staff = await _make_staff(session)
        secret = security.generate_totp_secret()
        factor = MfaFactor(
            user_id=staff.id,
            type="totp",
            label="Authenticator app",
            secret_enc=secret.encode("ascii"),
        )
        session.add(factor)
        await session.commit()

        count = await reseal_mfa_factors(legacy_cfg)
        assert count == 1

        # `reseal_mfa_factors` writes through its own connection — refresh past this session's
        # identity map to see it, rather than the pre-reseal object `get` would hand back.
        await session.refresh(factor)
        assert factor.secret_enc != secret.encode("ascii")
        aad = f"mfa_factor:{staff.id}".encode()
        assert pii.open_sealed(factor.secret_enc, settings=cfg, aad=aad) == secret

    async def test_an_already_sealed_row_is_not_counted(self, session, cfg, pair) -> None:
        await _make_staff(session)
        await _enrol_operator_mfa(session, cfg, pair)
        await session.commit()

        count = await reseal_mfa_factors(cfg)
        assert count == 0

    async def test_a_row_with_no_secret_is_skipped_without_error(self, session, cfg) -> None:
        staff = await _make_staff(session)
        factor = MfaFactor(user_id=staff.id, type="totp", label="Authenticator app")
        session.add(factor)
        await session.commit()

        count = await reseal_mfa_factors(cfg)
        assert count == 0

    async def test_running_it_twice_is_idempotent(self, session, cfg, legacy_cfg) -> None:
        staff = await _make_staff(session)
        secret = security.generate_totp_secret()
        factor = MfaFactor(
            user_id=staff.id, type="totp", label="Authenticator app", secret_enc=secret.encode()
        )
        session.add(factor)
        await session.commit()

        first = await reseal_mfa_factors(legacy_cfg)
        second = await reseal_mfa_factors(legacy_cfg)
        assert first == 1
        assert second == 0, "a row already sealed must not be counted again"


class TestLegacyPlaintextIsOptIn:
    """m4 of the third-round security review: a raw base32 value in `secret_enc` is only a
    legitimate legacy row on a box that predates PII sealing. Nothing authenticates it, so it is
    honoured only while `mfa_accept_legacy_plaintext` is set."""

    async def _plaintext_factor(self, session, staff, secret: str) -> MfaFactor:
        factor = MfaFactor(
            user_id=staff.id,
            type="totp",
            label="Authenticator app",
            secret_enc=secret.encode("ascii"),
            confirmed_at=datetime.now(UTC),
        )
        session.add(factor)
        await session.commit()
        return factor

    async def test_a_plaintext_factor_is_refused_when_the_setting_is_off(
        self, session, cfg
    ) -> None:
        assert cfg.mfa_accept_legacy_plaintext is False
        staff = await _make_staff(session)
        secret = security.generate_totp_secret()
        factor = await self._plaintext_factor(session, staff, secret)
        code = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)

        assert await services._accept_second_factor(session, staff.id, code, cfg=cfg) is None
        await session.refresh(factor)
        assert factor.secret_enc == secret.encode("ascii"), "a refused row is left as it was"

    async def test_a_refused_plaintext_factor_does_not_lose_the_recovery_codes(
        self, session, cfg
    ) -> None:
        staff = await _make_staff(session)
        await self._plaintext_factor(session, staff, security.generate_totp_secret())
        session.add(
            MfaRecoveryCode(user_id=staff.id, code_hash=security.hash_recovery_code("abcd-efgh"))
        )
        await session.commit()

        assert (
            await services._accept_second_factor(session, staff.id, "abcd-efgh", cfg=cfg)
            == "recovery"
        )

    async def test_a_plaintext_factor_is_honoured_and_resealed_when_the_setting_is_on(
        self, session, legacy_cfg
    ) -> None:
        staff = await _make_staff(session)
        secret = security.generate_totp_secret()
        factor = await self._plaintext_factor(session, staff, secret)
        code = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)

        assert (
            await services._accept_second_factor(session, staff.id, code, cfg=legacy_cfg) == "totp"
        )
        aad = f"mfa_factor:{staff.id}".encode()
        assert pii.open_sealed(factor.secret_enc, settings=legacy_cfg, aad=aad) == secret

    async def test_the_reseal_script_leaves_plaintext_alone_when_the_setting_is_off(
        self, session, cfg
    ) -> None:
        staff = await _make_staff(session)
        secret = security.generate_totp_secret()
        factor = await self._plaintext_factor(session, staff, secret)

        assert await reseal_mfa_factors(cfg) == 0
        await session.refresh(factor)
        assert factor.secret_enc == secret.encode("ascii")


class TestEnvelopesSealedBeforeTheAad:
    """m5: an envelope written before the per-row AAD existed is authentic — GCM verified it
    under a key we hold — so it is accepted regardless of the plaintext setting, and resealed
    with the AAD and audited."""

    async def test_a_no_aad_envelope_is_opened_resealed_with_the_aad_and_audited(
        self, session, cfg
    ) -> None:
        staff = await _make_staff(session)
        secret = security.generate_totp_secret()
        old_envelope, _mask = pii.seal(secret, kind="generic", settings=cfg)
        factor = MfaFactor(
            user_id=staff.id,
            type="totp",
            label="Authenticator app",
            secret_enc=old_envelope,
            confirmed_at=datetime.now(UTC),
        )
        session.add(factor)
        await session.commit()
        assert cfg.mfa_accept_legacy_plaintext is False

        code = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)
        assert await services._accept_second_factor(session, staff.id, code, cfg=cfg) == "totp"

        aad = f"mfa_factor:{staff.id}".encode()
        assert factor.secret_enc != old_envelope
        assert pii.open_sealed(factor.secret_enc, settings=cfg, aad=aad) == secret
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "auth.mfa_factor_resealed", AuditLog.entity_id == factor.id
                )
            )
        ).scalar_one_or_none()
        assert audit is not None

    async def test_an_envelope_sealed_for_another_row_is_still_refused(self, session, cfg) -> None:
        """The AAD tier must not become a way to open a copied ciphertext: an envelope bound
        to a *different* user's AAD fails both the with-AAD and the no-AAD attempts."""
        staff = await _make_staff(session)
        secret = security.generate_totp_secret()
        other = f"mfa_factor:{staff.id.int + 1}".encode()
        foreign, _mask = pii.seal(secret, kind="generic", settings=cfg, aad=other)
        factor = MfaFactor(
            user_id=staff.id,
            type="totp",
            label="Authenticator app",
            secret_enc=foreign,
            confirmed_at=datetime.now(UTC),
        )
        session.add(factor)
        await session.commit()

        code = security._totp_at(secret, int(datetime.now(UTC).timestamp()) // 30)
        assert await services._accept_second_factor(session, staff.id, code, cfg=cfg) is None


class TestResetAllStaffMfa:
    @pytest.mark.parametrize("environment", ["staging", "uat", "production"])
    async def test_it_refuses_in_every_real_environment(self, cfg, environment: str) -> None:
        """m9: the allowed names are exactly `local`, `test` and `ci` — the valid environment
        names — so every other one refuses, not just production."""
        target = cfg.model_copy(update={"environment": environment})
        with pytest.raises(SystemExit, match="local/test/ci only"):
            await reset_all_staff_mfa(target)

    def test_the_allowed_environments_are_all_valid_environment_names(self) -> None:
        """A name that is not one of `ServiceSettings.environment`'s valid values (the old
        list carried `development`, which no deployment can even be configured with) is dead
        weight that reads as a supported environment."""
        import re

        from app.mfa_maintenance import _RESET_ALLOWED_ENVIRONMENTS
        from platform_core.config import ServiceSettings

        pattern = ServiceSettings.model_fields["environment"].metadata[0].pattern
        assert all(re.match(pattern, name) for name in _RESET_ALLOWED_ENVIRONMENTS)
        assert set(_RESET_ALLOWED_ENVIRONMENTS) == {"local", "test", "ci"}

    async def test_it_deletes_every_staff_accounts_factors_and_recovery_codes(
        self, session, cfg, pair
    ) -> None:
        await _make_staff(session)
        await _enrol_operator_mfa(session, cfg, pair)
        await session.commit()

        staff = (
            await session.execute(select(StaffAccount).where(StaffAccount.email == STAFF_EMAIL))
        ).scalar_one()
        assert (
            await session.execute(select(MfaFactor).where(MfaFactor.user_id == staff.id))
        ).first()
        assert (
            await session.execute(
                select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == staff.id)
            )
        ).first()

        touched = await reset_all_staff_mfa(cfg)
        assert touched == 1

        await session.commit()
        remaining_factors = (
            await session.execute(select(MfaFactor).where(MfaFactor.user_id == staff.id))
        ).first()
        remaining_codes = (
            await session.execute(
                select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == staff.id)
            )
        ).first()
        assert remaining_factors is None
        assert remaining_codes is None

    async def test_it_also_clears_the_mfa_lock_and_counter(self, session, cfg, pair) -> None:
        await _make_staff(session)
        await _enrol_operator_mfa(session, cfg, pair)
        staff = (
            await session.execute(select(StaffAccount).where(StaffAccount.email == STAFF_EMAIL))
        ).scalar_one()
        staff.mfa_failed_attempts = 3
        staff.mfa_locked_until = datetime.now(UTC) + timedelta(minutes=10)
        await session.commit()

        await reset_all_staff_mfa(cfg)
        await session.refresh(staff)
        assert staff.mfa_failed_attempts == 0
        assert staff.mfa_locked_until is None
