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
import sys

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import oidc, security, services
from app.config import Settings, settings
from app.models import MfaFactor, PlatformOAuthProvider, StaffAccount
from platform_core import pii
from platform_core.db import dispose_engine, init_engine, session_scope
from platform_core.logging import get_logger

log = get_logger("auth.bootstrap")

#: Each provider's discovery document — the one admin-free way to learn its current
#: authorization/token/JWKS endpoints, so a provider rotating infrastructure behind its
#: well-known document never needs a code change here, only a reseed.
OAUTH_DISCOVERY_URLS = {
    "google": "https://accounts.google.com/.well-known/openid-configuration",
    "microsoft": "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
}

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
            # MINOR 9 of the 2026-09-24 security review: printed, not logged. A structured
            # log line is exactly the kind of thing that ends up shipped to a log aggregator,
            # and this is the one place a real enrolment code is ever produced outside the
            # audited API — it belongs on the terminal of whoever ran this command, once, and
            # nowhere a log pipeline might retain it.
            print(  # noqa: T201 - deliberately not a log line, see above
                f"Operator enrolment code for {DEV_OWNER_EMAIL}: {code}"
            )
            log.info(
                "auth.bootstrap.enrollment_code_issued",
                staff_id=str(staff.id),
                email=DEV_OWNER_EMAIL,
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


async def seed_oauth_providers(session: AsyncSession, *, cfg: Settings) -> None:
    """Seed `platform_oauth_providers` from deployment env vars.

    Run after `alembic upgrade head`, once per environment, and again whenever a client
    secret rotates. A provider with no `client_id`/`client_secret` configured is left
    disabled — "inert without keys, by design", the same posture `payment/app/config.py`
    already holds for Razorpay (ADR-0013 §6) — rather than half-registered with a stray
    `client_id` and no way to complete a token exchange. Idempotent by `provider` (the
    table's primary key): re-running updates the existing row instead of failing on a
    duplicate insert, which is what makes "run again after a secret rotates" a safe
    instruction rather than a manual UPDATE.

    Discovery is only attempted once both credentials are present — a provider with no
    secret gets no network call at all, so a bare `bin/seed-oauth-providers` on a fresh box
    with nothing configured yet never reaches out to Google or Microsoft.
    """
    idp = oidc.HttpIdpClient()
    try:
        creds: dict[str, tuple[str | None, SecretStr | None]] = {
            "google": (cfg.google_oauth_client_id, cfg.google_oauth_client_secret),
            "microsoft": (cfg.microsoft_oauth_client_id, cfg.microsoft_oauth_client_secret),
        }
        for provider, (client_id, client_secret) in creds.items():
            row = await session.get(PlatformOAuthProvider, provider)
            if row is None:
                row = PlatformOAuthProvider(provider=provider)
                session.add(row)
            if not client_id or not client_secret:
                # Half a credential pair is not a usable one — leave it disabled rather
                # than storing a `client_id` with nothing to exchange it against.
                row.enabled = False
                continue
            doc = await idp.discover(OAUTH_DISCOVERY_URLS[provider])
            row.client_id = client_id
            row.client_secret_enc, _mask = pii.seal(
                client_secret.get_secret_value(), kind="generic", settings=cfg
            )
            row.issuer = doc["issuer"]
            row.authorization_endpoint = doc["authorization_endpoint"]
            row.token_endpoint = doc["token_endpoint"]
            row.jwks_uri = doc["jwks_uri"]
            row.enabled = True
            log.info("auth.bootstrap.oauth_provider_seeded", provider=provider, enabled=True)
    finally:
        await idp.aclose()


async def seed_platform_oauth_providers(cfg: Settings) -> None:
    """Deploy-time entrypoint for `bin/seed-oauth-providers` — owns the engine lifecycle the
    same way `seed_dev_owner` does, around the actual row-level work in
    `seed_oauth_providers`."""
    init_engine(cfg)
    try:
        async with session_scope() as session:
            await seed_oauth_providers(session, cfg=cfg)
    finally:
        await dispose_engine()


def main() -> None:
    """`python -m app.bootstrap` seeds the dev operator owner (`bin/seed-staff`'s shape,
    unchanged for anyone already relying on the no-argument form); `python -m app.bootstrap
    seed-oauth-providers` (`bin/seed-oauth-providers`) runs the OAuth provider seed instead.
    Anything else — no argument, or an argument that is not that one — falls through to the
    original dev-operator behaviour, so this stays additive rather than a restructure."""
    if len(sys.argv) > 1 and sys.argv[1] == "seed-oauth-providers":
        asyncio.run(seed_platform_oauth_providers(settings()))
        return
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
