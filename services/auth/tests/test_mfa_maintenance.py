"""The one-off deploy scripts behind `bin/reseal-mfa-factors` and `bin/reset-staff-mfa`.

MAJOR 3 and MINOR 10 of the 2026-09-24 security review.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app import security
from app.mfa_maintenance import reseal_mfa_factors, reset_all_staff_mfa
from app.models import MfaFactor, MfaRecoveryCode, StaffAccount
from platform_core import pii
from tests.test_operator import STAFF_EMAIL, _enrol_operator_mfa, _make_staff

pytestmark = pytest.mark.integration


class TestResealMfaFactors:
    async def test_a_legacy_plaintext_row_is_resealed_and_counted(self, session, cfg) -> None:
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

        count = await reseal_mfa_factors(cfg)
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

    async def test_running_it_twice_is_idempotent(self, session, cfg) -> None:
        staff = await _make_staff(session)
        secret = security.generate_totp_secret()
        factor = MfaFactor(
            user_id=staff.id, type="totp", label="Authenticator app", secret_enc=secret.encode()
        )
        session.add(factor)
        await session.commit()

        first = await reseal_mfa_factors(cfg)
        second = await reseal_mfa_factors(cfg)
        assert first == 1
        assert second == 0, "a row already sealed must not be counted again"


class TestResetAllStaffMfa:
    async def test_it_refuses_outside_dev_and_test(self, cfg) -> None:
        production = cfg.model_copy(update={"environment": "production"})
        with pytest.raises(SystemExit, match="dev/test only"):
            await reset_all_staff_mfa(production)

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
