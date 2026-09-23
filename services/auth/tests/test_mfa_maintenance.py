"""The one-off deploy scripts behind `bin/reseal-mfa-factors` and `bin/reset-staff-mfa`.

MAJOR 3 and MINOR 10 of the 2026-09-24 security review.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app import security, services
from app.mfa_maintenance import reseal_mfa_factors, reset_all_staff_mfa
from app.models import AuditLog, MfaFactor, MfaRecoveryCode, StaffAccount
from platform_core import pii
from tests.test_operator import STAFF_EMAIL, _enrol_operator_mfa, _make_staff

pytestmark = pytest.mark.integration


def _deploy_count_sql() -> str:
    """The statement `infrastructure/edge/lib/mfa-reseal.sh` gates the deploy on, read out of
    that file by sourcing it — the real text, not a copy."""
    lib = Path(__file__).resolve().parents[3] / "infrastructure/edge/lib/mfa-reseal.sh"
    return subprocess.run(  # noqa: S603 - a repository script, no untrusted input
        ["bash", "-c", f'source "{lib}"; printf %s "$MFA_PLAINTEXT_COUNT_SQL"'],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout


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

        result = await reseal_mfa_factors(legacy_cfg)
        assert (result.resealed, result.remaining) == (1, 0)

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

        result = await reseal_mfa_factors(cfg)
        assert (result.resealed, result.remaining) == (0, 0)

    async def test_a_row_with_no_secret_is_skipped_without_error(self, session, cfg) -> None:
        staff = await _make_staff(session)
        factor = MfaFactor(user_id=staff.id, type="totp", label="Authenticator app")
        session.add(factor)
        await session.commit()

        result = await reseal_mfa_factors(cfg)
        assert (result.resealed, result.remaining) == (0, 0)

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
        assert first.resealed == 1
        assert second.resealed == 0, "a row already sealed must not be counted again"
        assert second.remaining == 0


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

        with pytest.raises(services.MfaResealPendingProblem):
            await services._accept_second_factor(session, staff.id, code, cfg=cfg)
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

        result = await reseal_mfa_factors(cfg)
        assert result.resealed == 0
        assert result.remaining == 1, "a run that leaves plaintext behind must say so (R1(b))"
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


class TestTheResealCommandLine:
    """R1(b): `reseal` reports what it left, and a run that leaves plaintext behind fails —
    the old "resealed 0" was identical for a database with nothing to do and for one whose
    every legacy factor was still unreadable."""

    def _run(self, monkeypatch, result: services.ResealResult) -> None:
        from app import mfa_maintenance

        async def fake(_cfg):
            return result

        monkeypatch.setattr(mfa_maintenance, "reseal_mfa_factors", fake)
        mfa_maintenance.reseal_main()

    def test_it_prints_both_counts_and_succeeds_when_nothing_remains(
        self, monkeypatch, capsys
    ) -> None:
        self._run(monkeypatch, services.ResealResult(resealed=3, remaining=0))
        assert capsys.readouterr().out.splitlines() == ["resealed=3", "remaining=0"]

    def test_it_exits_1_when_plaintext_remains(self, monkeypatch, capsys) -> None:
        with pytest.raises(SystemExit) as raised:
            self._run(monkeypatch, services.ResealResult(resealed=0, remaining=2))
        assert raised.value.code == 1
        captured = capsys.readouterr()
        assert captured.out.splitlines() == ["resealed=0", "remaining=2"]
        assert "MFA_ACCEPT_LEGACY_PLAINTEXT" in captured.err

    def test_the_module_form_the_deploy_runs_dispatches_to_reseal(
        self, monkeypatch, capsys
    ) -> None:
        import sys

        from app import mfa_maintenance

        async def fake(_cfg):
            return services.ResealResult(resealed=1, remaining=0)

        monkeypatch.setattr(mfa_maintenance, "reseal_mfa_factors", fake)
        monkeypatch.setattr(sys, "argv", ["app.mfa_maintenance", "reseal"])
        mfa_maintenance.main()
        assert "resealed=1" in capsys.readouterr().out

    def test_an_unknown_subcommand_is_refused(self, monkeypatch) -> None:
        import sys

        from app import mfa_maintenance

        monkeypatch.setattr(sys, "argv", ["app.mfa_maintenance", "explode"])
        with pytest.raises(SystemExit, match="usage"):
            mfa_maintenance.main()


class TestTheDeploysCountSql:
    async def test_it_finds_exactly_the_plaintext_rows(self, session, cfg) -> None:
        """The statement `lib/mfa-reseal.sh` gates the deploy on, and the RUNBOOK hands operators,
        run against the real column: a raw base32 secret matches; a sealed envelope, a value too
        short to be a TOTP secret, and a row with no secret do not."""
        from sqlalchemy import text

        sql = _deploy_count_sql()

        staff = await _make_staff(session)
        secret = security.generate_totp_secret()
        sealed, _mask = pii.seal(secret, kind="generic", settings=cfg)
        for stored in (secret.encode("ascii"), sealed, b"ABCDEFG", None):
            session.add(MfaFactor(user_id=staff.id, type="totp", label="x", secret_enc=stored))
        await session.commit()

        assert (await session.execute(text(sql))).scalar_one() == 1


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
