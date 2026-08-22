"""Seed one organization and one member, then print what the journey needs.

Run **inside `services/account`** as a subprocess. That is not squeamishness: every service
exposes a top-level `app` package, so a single process cannot import two of them, and the
end-to-end suite has to talk to three. Keeping each seeder in its own process is what makes
that possible without import gymnastics that would break the moment a service is renamed.

Prints a single JSON object on stdout. Anything else it has to say goes to stderr, so the
caller can parse without stripping noise.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

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

    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
    async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
        org = await services.create_organization(
            session,
            OrganizationIn(name="End To End Ltd", slug="e2e"),
            actor_user_id=None,
        )
        membership = await services.add_member(
            session,
            org_id=org.id,
            email=email,
            full_name="Eve Endtoend",
            actor_user_id=None,
            role_codes=["ORG_ADMIN"],
        )
        payload = {
            "org_id": str(org.id),
            "org_slug": org.slug,
            "user_id": str(membership.user_id),
            "membership_id": str(membership.id),
            "email": email,
        }

    await engine.dispose()
    print(json.dumps(payload))


asyncio.run(main())
