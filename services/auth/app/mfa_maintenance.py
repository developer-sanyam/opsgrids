"""One-off MFA maintenance tasks, run by hand at deploy time — never by the API, and never on
a schedule. Each is idempotent, so a re-run after a partial failure repeats no damage.

`bin/reseal-mfa-factors` (MAJOR 3 of the 2026-09-24 security review): force every factor with a
stored secret through the tolerant-legacy-read-and-reseal path in `services._open_factor_secret`,
rather than waiting for each account's next sign-in to reseal it lazily. Safe to run repeatedly
and safe to run against a database with nothing left to reseal — it prints only a count.
Raw-plaintext rows (pre-PII-sealing) are only resealed when `MFA_ACCEPT_LEGACY_PLAINTEXT=true` is
set for the run (m4 of the third-round review): nothing authenticates such a row, so honouring it
is an explicit decision. Run with it set until it prints `remaining=0` (and exits 0), then
remove it.

`bin/reset-staff-mfa --all` (MINOR 10): wipes every staff account's MFA factors and recovery
codes outright. `local`/`test`/`ci` only, and `--all` is mandatory — there is no per-account form,
because the reason this exists is a whole environment having enrolled factors under a bug
(`5ba575e36b1a`'s fix for BLOCKER 1) that makes their provenance untrustworthy, not one account
needing a reset (that is `POST /auth/operator/staff/{id}/mfa:reset`, the audited owner route).
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import delete, select, update

from app import services
from app.config import Settings, settings
from app.models import MfaFactor, MfaRecoveryCode, StaffAccount
from platform_core.db import dispose_engine, init_engine, session_scope
from platform_core.logging import get_logger

log = get_logger("auth.mfa_maintenance")

#: Environments this destructive reset may ever run against. Production and anything shaped
#: like it (staging, uat — see `ServiceSettings.model_post_init`) refuse outright: a real
#: staff roster's MFA is not something a deploy script wipes on a hunch.
_RESET_ALLOWED_ENVIRONMENTS = ("local", "test", "ci")


async def reseal_mfa_factors(cfg: Settings) -> services.ResealResult:
    """Run every factor with a stored secret through the tolerant read once, so legacy rows
    are resealed now instead of at whatever sign-in happens to come next. Reports how many
    were resealed and how many plaintext rows are *still* there."""
    init_engine(cfg)
    try:
        async with session_scope() as session:
            return await services.reseal_legacy_mfa_factors(session, cfg=cfg)
    finally:
        await dispose_engine()


def reseal_main() -> None:
    """Prints `resealed=N` and `remaining=N` (counts only, nothing else on stdout — the deploy
    step parses it) and **exits 1 when `remaining` is above zero**: a run that leaves
    plaintext behind has not done its job, and the previous "resealed 0" was indistinguishable
    from a database with nothing to do (R1(b) of the final review)."""
    result = asyncio.run(reseal_mfa_factors(settings()))
    print(f"resealed={result.resealed}")  # noqa: T201 - deploy script output
    print(f"remaining={result.remaining}")  # noqa: T201 - deploy script output
    if result.remaining:
        print(  # noqa: T201 - deploy script output
            f"{result.remaining} MFA factor(s) are still plaintext: run with "
            "MFA_ACCEPT_LEGACY_PLAINTEXT=true (RUNBOOK, 'Operator MFA').",
            file=sys.stderr,
        )
        raise SystemExit(1)


async def reset_all_staff_mfa(cfg: Settings) -> int:
    """Delete every staff account's MFA factors and recovery codes. Returns the number of
    staff accounts touched (whether or not each one actually had anything to delete)."""
    if cfg.environment not in _RESET_ALLOWED_ENVIRONMENTS:
        raise SystemExit(
            f"refusing to reset operator MFA in {cfg.environment}: local/test/ci only. Production "
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
                # m7: no factor left to be locked out of, so no lock or half-spent counter
                # either — a re-enrolment after this must start clean.
                await session.execute(
                    update(StaffAccount).values(mfa_failed_attempts=0, mfa_locked_until=None)
                )
            return len(staff_ids)
    finally:
        await dispose_engine()


def reset_main() -> None:
    if "--all" not in sys.argv[1:]:
        raise SystemExit("bin/reset-staff-mfa requires --all: there is no per-account form here")
    count = asyncio.run(reset_all_staff_mfa(settings()))
    print(f"wiped mfa factors and recovery codes for {count} staff account(s)")  # noqa: T201


def main() -> None:
    """`python -m app.mfa_maintenance reseal` | `... reset --all` — the module form the deploy
    step uses; `bin/reseal-mfa-factors` and `bin/reset-staff-mfa` call the same functions."""
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "reseal":
        reseal_main()
    elif command == "reset":
        reset_main()
    else:
        raise SystemExit("usage: python -m app.mfa_maintenance {reseal|reset --all}")


if __name__ == "__main__":
    main()
