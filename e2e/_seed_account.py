"""Seed two organizations and their people, then print what the journey needs.

Run **inside `services/account`** as a subprocess. That is not squeamishness: every service
exposes a top-level `app` package, so a single process cannot import two of them, and this
suite has to talk to six. Keeping each seeder in its own process is what makes that possible
without import gymnastics that would break the moment a service is renamed.

Two organizations, not one. "Refused another tenant's data" is not assertable against a
single tenant, and a cross-tenant test whose victim row does not exist passes for the wrong
reason — it would pass just as well against a service that leaked everything.

Alpha carries **both** an owner and an administrator, because the split between them is
load-bearing and tested in both directions: the owner activates a product (it commits money),
the administrator is refused. Beta needs only an owner; it exists to be read *at*.

Prints a single JSON object on stdout. Anything else it has to say goes to stderr, so the
caller can parse without stripping noise.

Usage: `_seed_account.py <service_dir>`
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

SERVICE_DIR = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(SERVICE_DIR))

from app import services  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import SCHEMA, Base  # noqa: E402
from app.schemas import OrganizationIn  # noqa: E402


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    cfg = settings()
    connect_args = {"server_settings": {"search_path": f"{SCHEMA},public"}}

    # The admin role throughout: an organization cannot be created by a tenant — the policy
    # compares `id` to the tenant binding and there is no id to name before the row exists.
    engine = create_async_engine(cfg.admin_dsn, connect_args=connect_args)
    tables = ", ".join(f'"{SCHEMA}"."{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))

    #: `example.com` on purpose: EmailStr rejects .local, .test and .invalid as special-use
    #: reserved domains, and a seed that used one once produced an owner who could not log in.
    run = uuid.uuid4().hex[:8]
    payload: dict[str, Any] = {}

    async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
        for org_key, name in (("alpha", "Alpha Anvils"), ("beta", "Beta Bearings")):
            org = await services.create_organization(
                session,
                OrganizationIn(name=name, slug=f"e2e-{org_key}-{run}"),
                actor_user_id=None,
            )
            entry: dict[str, Any] = {"org_id": str(org.id), "org_slug": org.slug, "name": name}

            people = [("owner", "ORG_OWNER")]
            if org_key == "alpha":
                people.append(("admin", "ORG_ADMIN"))

            for role_key, role_code in people:
                email = f"e2e-{org_key}-{role_key}-{run}@example.com"
                membership = await services.add_member(
                    session,
                    org_id=org.id,
                    email=email,
                    full_name=f"{name} {role_key.title()}",
                    actor_user_id=None,
                    role_codes=[role_code],
                )
                # Holding `ORG_OWNER` is not the same as *being* the owner: `is_owner` is a
                # column on the membership, and it is what `list_owner_memberships` — and so
                # activation's "grant the product's admin roles to the owners" — selects on.
                # `add_member` cannot set it (only organization creation does, and there is no
                # HTTP route for that), which is exactly the gap `tools/demo/promote_owner.py`
                # exists to repair. Without this the journey activates a product and grants
                # its admin roles to nobody.
                if role_code == "ORG_OWNER":
                    membership.is_owner = True
                    await session.flush()

                # `email` as constructed, not `membership.user.email`: the relationship is
                # not loaded and touching it here would lazy-load on an async session.
                entry[role_key] = {
                    "user_id": str(membership.user_id),
                    "membership_id": str(membership.id),
                    "email": email,
                    "role_code": role_code,
                }
            payload[org_key] = entry

    await engine.dispose()
    print(json.dumps(payload))


asyncio.run(main())
