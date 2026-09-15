"""Seed one operator owner for local development.

The operator console needs a staff account to sign in with, and there is no self-serve
sign-up for operators — they are provisioned. This gives local development exactly one,
idempotently, so `make infra && make migrate && bin/seed-staff` lands a working operator.

**No real credential is committed.** The default password here is an obviously-fake dev
placeholder; override it with `OPSGRIDS_DEV_OPERATOR_PASSWORD` for anything shared. The seed
refuses to run outside development, so it can never create a known-password account in a
real environment — an operator with a default password is a back door, and the safest
place for that mistake to fail is at the seed, loudly.
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import security
from app.config import Settings, settings
from app.models import StaffAccount
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
) -> StaffAccount:
    """Create the staff account if it is missing; return the existing one otherwise.

    Idempotent by email: re-running never duplicates and never rewrites a password that
    somebody may have changed.
    """
    existing = (
        await session.execute(select(StaffAccount).where(StaffAccount.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    staff = StaffAccount(
        email=email,
        password_hash=security.hash_password(password),
        display_name=display_name,
        operator_roles=list(roles),
        status="active",
    )
    session.add(staff)
    await session.flush()
    return staff


async def seed_dev_owner(cfg: Settings) -> None:
    """Seed the single dev operator owner. Refuses outside development."""
    if cfg.environment in ("production", "staging", "uat"):
        raise SystemExit(
            f"refusing to seed a known-password operator in {cfg.environment}: operators are "
            "provisioned there, not seeded with a default credential"
        )

    password = os.environ.get("OPSGRIDS_DEV_OPERATOR_PASSWORD", DEV_OWNER_PASSWORD)

    init_engine(cfg)
    try:
        async with session_scope() as session:
            staff = await bootstrap_staff_owner(
                session,
                email=DEV_OWNER_EMAIL,
                password=password,
                display_name="OpsGrids Owner (dev)",
                roles=DEV_OWNER_ROLES,
            )
        log.info(
            "auth.bootstrap.operator_seeded",
            staff_id=str(staff.id),
            email=DEV_OWNER_EMAIL,
            roles=DEV_OWNER_ROLES,
        )
    finally:
        await dispose_engine()


def main() -> None:
    asyncio.run(seed_dev_owner(settings()))


if __name__ == "__main__":
    main()
