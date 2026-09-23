"""Seed one operator owner for local development, and issue MFA enrolment codes.

The operator console needs a staff account to sign in with, and there is no self-serve
sign-up for operators — they are provisioned. This gives local development exactly one,
idempotently, so `make infra && make migrate && bin/seed-staff` lands a working operator.

**No real credential is committed.** The default password here is an obviously-fake dev
placeholder; override it with `OPSGRIDS_DEV_OPERATOR_PASSWORD` for anything shared. The seed
refuses to run outside development, so it can never create a known-password account in a
real environment — an operator with a default password is a back door, and the safest
place for that mistake to fail is at the seed, loudly.

**A password is no longer enough to reach MFA enrolment** (ADR-0003 addendum, and the security
review that found a password-only enrolment could hijack an already-enrolled account): every
staff account also needs an out-of-band enrolment code. `seed_dev_owner` issues one for a
freshly created dev owner and logs it — nowhere else in this process is a code ever printed in
the clear, because this is the one place a human is meant to read it off a terminal rather than
receive it through an audited API call. `issue_enrollment_codes_for_unenrolled_staff` is the
deploy-time counterpart for every account that existed *before* this rule did: run it once,
right after the migration that made MFA mandatory ships, and hand each staff member their code
out of band (chat, a phone call — anything that is not this service's own logs in a shared
environment). Accounts that already have a confirmed factor are left alone; there is nothing
for a code to unlock for them.
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import security, services
from app.config import Settings, settings
from app.models import MfaFactor, StaffAccount
from platform_core.db import dispose_engine, init_engine, session_scope
from platform_core.logging import get_logger

log = get_logger("auth.bootstrap")

#: The dev operator's handle and roles. `owner` is the widest operator role; the console
#: will treat it as holding every operator capability.
DEV_OWNER_EMAIL = "owner@opsgrids.local"
DEV_OWNER_ROLES = ["owner", "billing_ops", "support"]

#: Obviously fake, and never a real password. Overridable for a shared dev box. Refused in
#: any non-development environment by `seed_dev_owner`.
DEV_OWNER_PASSWORD = "dev-operator-not-a-real-password"  # noqa: S105  # pragma: allowlist secret


async def bootstrap_staff_owner(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str,
    roles: list[str],
) -> tuple[StaffAccount, bool]:
    """Create the staff account if it is missing; return the existing one otherwise.

    Idempotent by email: re-running never duplicates and never rewrites a password that
    somebody may have changed. The second element of the return says whether this call is
    the one that created the row — the caller needs that to decide whether an enrolment code
    is this account's *first*, since issuing one for an account that already has a confirmed
    factor would be a code with nothing to unlock.
    """
    existing = (
        await session.execute(select(StaffAccount).where(StaffAccount.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    staff = StaffAccount(
        email=email,
        password_hash=security.hash_password(password),
        display_name=display_name,
        operator_roles=list(roles),
        status="active",
    )
    session.add(staff)
    await session.flush()
    return staff, True


async def seed_dev_owner(cfg: Settings) -> None:
    """Seed the single dev operator owner, and its first enrolment code. Refuses outside
    development."""
    if cfg.environment in ("production", "staging", "uat"):
        raise SystemExit(
            f"refusing to seed a known-password operator in {cfg.environment}: operators are "
            "provisioned there, not seeded with a default credential"
        )

    password = os.environ.get("OPSGRIDS_DEV_OPERATOR_PASSWORD", DEV_OWNER_PASSWORD)

    init_engine(cfg)
    try:
        async with session_scope() as session:
            staff, created = await bootstrap_staff_owner(
                session,
                email=DEV_OWNER_EMAIL,
                password=password,
                display_name="OpsGrids Owner (dev)",
                roles=DEV_OWNER_ROLES,
            )
            code = None
            if created:
                code = await services.issue_staff_enrollment_code(
                    session, staff_id=staff.id, actor_id=None
                )
        log.info(
            "auth.bootstrap.operator_seeded",
            staff_id=str(staff.id),
            email=DEV_OWNER_EMAIL,
            roles=DEV_OWNER_ROLES,
        )
        if code:
            # Deliberately the one place this ever appears in a log: a fresh dev seed with no
            # owner yet to issue the real owner a code through the audited route. Anything
            # after this first account goes through `POST /auth/operator/enrollment-codes`.
            log.info(
                "auth.bootstrap.enrollment_code_issued",
                staff_id=str(staff.id),
                email=DEV_OWNER_EMAIL,
                enrollment_code=code,
            )
    finally:
        await dispose_engine()


async def issue_enrollment_codes_for_unenrolled_staff(cfg: Settings) -> list[tuple[str, str]]:
    """The deploy-time step for every staff account that existed before MFA became mandatory.

    Issues a fresh enrolment code for every staff account with **no confirmed MFA factor**,
    and returns `(email, code)` pairs for whoever runs this to hand out — deliberately
    returned rather than only logged, since a real deploy should not leave every staff
    member's enrolment code sitting in a shared log stream. An account that already has a
    confirmed factor is left untouched: it does not need to enrol again, and a fresh code
    for it would be an unused, unnecessary credential.
    """
    init_engine(cfg)
    try:
        async with session_scope() as session:
            confirmed_ids = set(
                (
                    await session.execute(
                        select(MfaFactor.user_id).where(MfaFactor.confirmed_at.isnot(None))
                    )
                )
                .scalars()
                .all()
            )
            staff_rows = (
                (await session.execute(select(StaffAccount).where(StaffAccount.status == "active")))
                .scalars()
                .all()
            )
            issued: list[tuple[str, str]] = []
            for staff in staff_rows:
                if staff.id in confirmed_ids:
                    continue
                code = await services.issue_staff_enrollment_code(
                    session, staff_id=staff.id, actor_id=None
                )
                issued.append((staff.email, code))
            return issued
    finally:
        await dispose_engine()


def main() -> None:
    asyncio.run(seed_dev_owner(settings()))


def issue_codes_main() -> None:
    """Entry point for `bin/issue-enrollment-codes` — the migration deploy step for staff
    accounts that predate mandatory MFA. Prints each `email code` pair to stdout, once, for
    whoever runs the deploy to relay out of band; nothing here is retrievable again."""
    pairs = asyncio.run(issue_enrollment_codes_for_unenrolled_staff(settings()))
    if not pairs:
        print("No unenrolled staff accounts found.")  # noqa: T201 - deploy script output
        return
    for email, code in pairs:
        print(f"{email} {code}")  # noqa: T201 - deploy script output


if __name__ == "__main__":
    main()
