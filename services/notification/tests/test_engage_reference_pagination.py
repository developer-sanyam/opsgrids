"""Paging the sender and domain lists must be stable, or a reader loses rows.

The Engage app fills its pickers by reading every page of `GET /engage/senders` and
`GET /engage/domains` (`frontend/product-engage/src/lib/refs.ts`). Both are `OFFSET` pages, and an
`OFFSET` page over an order with ties is not a stable read: two senders may share a display name,
and every domain written in one transaction shares a `created_at`, so which of the tied rows falls
on which side of a page boundary is left to the planner — and can differ between the request for
page 1 and the request for page 2. A row can then appear on both pages, or on neither.

Both selectors now break the tie on `id`. This pins that, with more tied rows than a page holds,
so a page boundary falls inside a run of ties.

**These tests truncate the tenant tables** (every test in this service does, through the `session`
fixture), so they must only ever run against an isolated database. The guard below refuses to run
against `opsgrids` itself.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from app import models
from app.config import settings
from app.main import app
from platform_core.auth import Principal, get_principal
from platform_core.testing import is_shared_dev_database
from tests.helpers import principal_for

pytestmark = pytest.mark.integration

SENDERS = "/api/v1/engage/senders"
DOMAINS = "/api/v1/engage/domains"
S_READ = "engage.setup.sender.read"
D_READ = "engage.setup.domain.read"

#: More tied rows than a page holds, and an odd remainder, so a boundary lands inside the ties.
ROWS = 9
PAGE = 4


@pytest.fixture(autouse=True)
def _only_an_isolated_database() -> None:
    if is_shared_dev_database(settings().database_url):
        pytest.fail(
            "This module's fixtures truncate the tenant tables; refusing to run against the "
            "shared `opsgrids` database. Point DATABASE_URL at an isolated one."
        )


@contextlib.contextmanager
def _as(org_id: uuid.UUID) -> Iterator[None]:
    principal: Principal = principal_for(
        org_id, [f"{S_READ}:ORG", f"{D_READ}:ORG"], entitlements=["engage"]
    )
    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)


async def _read_every_page(client, path: str) -> list[str]:
    """Ids in the order a reader paging the whole list would see them."""
    seen: list[str] = []
    page = 1
    while True:
        response = await client.get(path, params={"page": page, "size": PAGE})
        assert response.status_code == 200, response.text
        body = response.json()
        seen.extend(item["id"] for item in body["items"])
        if page >= body["pages"]:
            return seen
        page += 1


async def test_senders_sharing_a_name_page_without_skipping_or_repeating(client, session, org_id):
    async with session.begin():
        domain = models.EmailDomain(organization_id=org_id, domain="company.com", status="verified")
        session.add(domain)
        await session.flush()
        senders = [
            models.Sender(
                organization_id=org_id,
                domain_id=domain.id,
                name="Support",  # the same display name on every row
                address=f"support{n}@company.com",
            )
            for n in range(ROWS)
        ]
        session.add_all(senders)
        await session.flush()
        expected = sorted(str(row.id) for row in senders)

    with _as(org_id):
        seen = await _read_every_page(client, SENDERS)

    # Every row exactly once, and in the one order the tie-break defines.
    assert len(seen) == ROWS
    assert len(set(seen)) == ROWS
    assert seen == expected


async def test_domains_written_together_page_without_skipping_or_repeating(client, session, org_id):
    instant = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    async with session.begin():
        rows = [
            models.EmailDomain(
                organization_id=org_id,
                domain=f"d{n}.example.com",
                status="verified",
                created_at=instant,  # one transaction, one timestamp: every row ties
            )
            for n in range(ROWS)
        ]
        session.add_all(rows)
        await session.flush()
        expected = sorted(str(row.id) for row in rows)

    with _as(org_id):
        seen = await _read_every_page(client, DOMAINS)

    assert len(seen) == ROWS
    assert len(set(seen)) == ROWS
    assert seen == expected


async def test_a_second_read_returns_the_same_order(client, session, org_id):
    """The property a paging reader relies on: the order does not depend on which request asked."""
    async with session.begin():
        domain = models.EmailDomain(organization_id=org_id, domain="company.com", status="verified")
        session.add(domain)
        await session.flush()
        for n in range(ROWS):
            session.add(
                models.Sender(
                    organization_id=org_id,
                    domain_id=domain.id,
                    name="Support",
                    address=f"s{n}@company.com",
                )
            )

    with _as(org_id):
        first = await _read_every_page(client, SENDERS)
        second = await _read_every_page(client, SENDERS)

    assert first == second
