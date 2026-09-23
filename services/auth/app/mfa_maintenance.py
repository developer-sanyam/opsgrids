"""One-off MFA maintenance tasks, run by hand at deploy time — never by the API, and never on
a schedule. Each is idempotent, so a re-run after a partial failure repeats no damage.

`bin/reseal-mfa-factors` (MAJOR 3 of the 2026-09-24 security review): force every factor with a
stored secret through the tolerant-legacy-read-and-reseal path in `services._open_factor_secret`,
rather than waiting for each account's next sign-in to reseal it lazily. Safe to run repeatedly
and safe to run against a database with nothing left to reseal — it prints only a count.

`bin/reset-staff-mfa --all` (MINOR 10): wipes every staff account's MFA factors and recovery
codes outright. Dev/staging only, and `--all` is mandatory — there is no per-account form,
because the reason this exists is a whole environment having enrolled factors under a bug
(`5ba575e36b1a`'s fix for BLOCKER 1) that makes their provenance untrustworthy, not one account
needing a reset (that is `POST /auth/operator/staff/{id}/mfa:reset`, the audited owner route).
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import delete, select

from app import services
from app.config import Settings, settings
from app.models import MfaFactor, MfaRecoveryCode, StaffAccount
from platform_core.db import dispose_engine, init_engine, session_scope
from platform_core.logging import get_logger

log = get_logger("auth.mfa_maintenance")

#: Environments this destructive reset may ever run against. Production and anything shaped
#: like it (staging, uat — see `ServiceSettings.model_post_init`) refuse outright: a real
#: staff roster's MFA is not something a deploy script wipes on a hunch.
_RESET_ALLOWED_ENVIRONMENTS = ("local", "development", "test")


async def reseal_mfa_factors(cfg: Settings) -> int:
    """Run every factor with a stored secret through the tolerant read once, so legacy rows
    are resealed now instead of at whatever sign-in happens to come next. Returns the count
    resealed."""
    init_engine(cfg)
    try:
        async with session_scope() as session:
            return await services.reseal_legacy_mfa_factors(session, cfg=cfg)
    finally:
        await dispose_engine()


def reseal_main() -> None:
    count = asyncio.run(reseal_mfa_factors(settings()))
    print(f"resealed {count} legacy mfa factor row(s)")  # noqa: T201 - deploy script output


async def reset_all_staff_mfa(cfg: Settings) -> int:
    """Delete every staff account's MFA factors and recovery codes. Returns the number of
    staff accounts touched (whether or not each one actually had anything to delete)."""
    if cfg.environment not in _RESET_ALLOWED_ENVIRONMENTS:
        raise SystemExit(
            f"refusing to reset operator MFA in {cfg.environment}: dev/test only. Production "
            "and staging roster resets go through the audited owner route, one account at a "
            "time, never this script."
        )
    init_engine(cfg)
    try:
        async with session_scope() as session:
            staff_ids = (await session.execute(select(StaffAccount.id))).scalars().all()
            if staff_ids:
                await session.execute(
                    delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id.in_(staff_ids))
                )
                await session.execute(delete(MfaFactor).where(MfaFactor.user_id.in_(staff_ids)))
            return len(staff_ids)
    finally:
        await dispose_engine()


def reset_main() -> None:
    if "--all" not in sys.argv[1:]:
        raise SystemExit("bin/reset-staff-mfa requires --all: there is no per-account form here")
    count = asyncio.run(reset_all_staff_mfa(settings()))
    print(f"wiped mfa factors and recovery codes for {count} staff account(s)")  # noqa: T201


if __name__ == "__main__":
    reset_main()
