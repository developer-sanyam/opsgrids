#!/usr/bin/env python3
"""Promote a membership to organization owner — a demo-org repair.

A real organization is born with an owner: the creating membership carries ``is_owner``
and holds ``ORG_OWNER``, which is what gates role administration
(``core.identity.role.write``) and product activation. The demo seed created its admin
directly, bypassing that path, which leaves the org with no principal who can administer
roles — and leaves ``wire_product.py`` with no owner to hand a product's admin role to.

This makes the membership what organization creation would have made it: ``is_owner``
set, ``ORG_OWNER`` held, and the permission version bumped so the next token reflects it.
Idempotent.

Usage, from the repository root (DATABASE_URL pointed at the intended database):

    uv run python tools/demo/promote_owner.py <org_id> <membership_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _main() -> None:
    parser = argparse.ArgumentParser(description="Promote a membership to organization owner")
    parser.add_argument("org_id", type=uuid.UUID)
    parser.add_argument("membership_id", type=uuid.UUID)
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "services" / "account"))

    from sqlalchemy import select

    from app import selectors  # type: ignore[import-not-found]
    from app.config import settings  # type: ignore[import-not-found]
    from app.models import Membership, MembershipRole  # type: ignore[import-not-found]
    from platform_core.cache import bump_permission_version, close_redis, init_redis
    from platform_core.db import dispose_engine, init_engine, session_scope

    async def _run() -> None:
        cfg = settings()
        init_engine(cfg)
        init_redis(cfg.redis_url)
        changed = False
        try:
            async with session_scope(organization_id=args.org_id) as session:
                membership = (
                    await session.execute(
                        select(Membership).where(
                            Membership.id == args.membership_id,
                            Membership.organization_id == args.org_id,
                        )
                    )
                ).scalar_one_or_none()
                if membership is None:
                    raise SystemExit("membership not found in that organization")

                if not membership.is_owner:
                    membership.is_owner = True
                    changed = True

                role = await selectors.get_role_by_code(session, args.org_id, "ORG_OWNER")
                if role is None:
                    raise SystemExit("ORG_OWNER role missing — core registration not installed")
                held = (
                    await session.execute(
                        select(MembershipRole).where(
                            MembershipRole.membership_id == membership.id,
                            MembershipRole.role_id == role.id,
                        )
                    )
                ).scalar_one_or_none()
                if held is None:
                    session.add(
                        MembershipRole(
                            membership_id=membership.id, role_id=role.id, granted_by=None
                        )
                    )
                    changed = True

            if changed:
                await bump_permission_version(str(args.org_id))
        finally:
            await dispose_engine()
            await close_redis()
        print(
            json.dumps(
                {
                    "org_id": str(args.org_id),
                    "membership_id": str(args.membership_id),
                    "changed": changed,
                }
            )
        )

    asyncio.run(_run())


if __name__ == "__main__":
    _main()
