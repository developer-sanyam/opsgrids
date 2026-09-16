"""Provision one fresh organization and its owner. Run **inside `services/account`**.

This is the "tenant provisioned" half of the Wave 0 exit criterion, and it is a seeder rather
than an HTTP call for the reason `_seed_account.py` already gives: **there is no route that
creates an organization**, by design — the tenant policy compares `id` to the bound organization
and there is no id to bind before the row exists. So this runs account-service's own
`services.create_organization` against the real database on the admin role, exactly as the
platform's own provisioning would.

Unlike `_seed_account.py` it **truncates nothing**. It is called after that seeder has already
built alpha and beta, and wiping the account schema here would take the CRM journey's owners with
it. Everything it creates is namespaced by a per-run suffix, so the suite can be run repeatedly
against the same database without manual cleanup and without two runs colliding on a slug or an
email address.

The organization it creates is deliberately a **brand-new tenant on the current code**: it is
created from account-service's in-code `CORE_REGISTRATION`, which does not — and per ADR-0007 §2
cannot — carry approval-service's slice of `core`. That is what makes the reconcile step of the
journey load-bearing rather than setup noise: without it every `core.approval.*` endpoint answers
403 to every principal in this tenant, and the journey can prove that before and after.

Prints a single JSON object on stdout.

Usage: `_seed_approval_org.py <service_dir> <slug_suffix>`
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

SERVICE_DIR = Path(sys.argv[1]).resolve()
RUN = sys.argv[2]

sys.path.insert(0, str(SERVICE_DIR))

from app import services  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import SCHEMA  # noqa: E402
from app.schemas import OrganizationIn  # noqa: E402


async def main() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    cfg = settings()
    engine = create_async_engine(
        cfg.admin_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )

    async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
        org = await services.create_organization(
            session,
            OrganizationIn(name="Gamma Gearworks", slug=f"e2e-gamma-{RUN}"),
            actor_user_id=None,
        )
        email = f"e2e-gamma-owner-{RUN}@example.com"
        membership = await services.add_member(
            session,
            org_id=org.id,
            email=email,
            full_name="Gamma Gearworks Owner",
            actor_user_id=None,
            role_codes=["ORG_OWNER"],
        )
        # Holding `ORG_OWNER` is not the same as *being* the owner: `is_owner` is a column, and
        # it is what activation's "grant the product's admin roles to the owners" selects on.
        # `add_member` cannot set it — only organization creation does, and there is no route for
        # that. Without this the journey activates HRMS and grants HR_ADMIN to nobody.
        membership.is_owner = True
        await session.flush()

        payload: dict[str, Any] = {
            "org_id": str(org.id),
            "org_slug": org.slug,
            "owner": {
                "user_id": str(membership.user_id),
                "membership_id": str(membership.id),
                "email": email,
            },
        }

    await engine.dispose()
    print(json.dumps(payload))


asyncio.run(main())
