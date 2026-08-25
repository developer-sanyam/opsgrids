#!/usr/bin/env python3
"""Install a product's permission manifest for an organization, by hand.

The ops and demo fallback for the path the account consumer drives on
`core.product.activated`: same manifest (`libs/contracts/permissions/<product>.json`), same
installer (`account.services.install_product_registration`), same connection shape (the
NOBYPASSRLS app role with the tenant GUC bound per transaction) — so what this writes is
byte-for-byte what an activation event would have written. Use it for an organization whose
product was activated before the consumer existed, or when a manifest shipped after the
activation that needed it.

Idempotent: permissions and roles are upserted, owners already holding the admin role are
skipped, and the permission-version bump happens only when a grant was actually added.

Usage, from the repository root:

    uv run python tools/wire_product.py <org_id> <product_key> [--no-grant-owners]

Point `DATABASE_URL` at the intended database before running — in a worktree that is
`atmora_wt`, never the shared `atmora`. Redis must be reachable when a grant lands, because
the permission-version bump refuses to be lost.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Install a product's permission manifest for an organization"
    )
    parser.add_argument("org_id", type=uuid.UUID)
    parser.add_argument("product_key")
    parser.add_argument(
        "--no-grant-owners",
        action="store_true",
        help="install permissions and roles only; do not grant the admin role to the owners",
    )
    args = parser.parse_args()

    # The account service directory becomes the import root, exactly as its own processes
    # run: every service exposes a top-level `app`, so one process holds exactly one.
    sys.path.insert(0, str(ROOT / "services" / "account"))

    import asyncio

    from app import services  # type: ignore[import-not-found]  # loaded from the service dir
    from app.catalogue import load_product_manifest  # type: ignore[import-not-found]
    from app.config import settings  # type: ignore[import-not-found]
    from platform_core.cache import close_redis, init_redis
    from platform_core.db import dispose_engine, init_engine, session_scope

    async def _run() -> None:
        cfg = settings()
        init_engine(cfg)
        init_redis(cfg.redis_url)

        manifest = load_product_manifest(args.product_key)
        if manifest is None:
            raise SystemExit(
                f"no permission manifest for {args.product_key!r} in "
                "libs/contracts/permissions/ — run `make openapi` if the product's "
                "catalogue exists and the manifest is missing"
            )

        try:
            async with session_scope(organization_id=args.org_id) as session:
                summary = await services.install_product_registration(
                    session,
                    org_id=args.org_id,
                    manifest=manifest,
                    acting_as="wire-product-cli",
                    grant_admin_to_owners=not args.no_grant_owners,
                )
        finally:
            await dispose_engine()
            await close_redis()
        print(json.dumps({"org_id": str(args.org_id), **summary}))

    asyncio.run(_run())


if __name__ == "__main__":
    _main()
