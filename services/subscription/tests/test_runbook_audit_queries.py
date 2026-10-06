"""The RUNBOOK's read-only audit for ADR-0037 lists what it says it lists.

The audit is the **only net** for the organizations the stop-event and hand-activation defects
already hit, so what it prints is read by the owner as "serving a product its subscription ended".
A query that also prints an organization whose product is legitimately on makes the owner switch
off, or chase, something the customer is paying for, and buries the real rows.

These tests do not restate the SQL. They lift it out of the RUNBOOK section the owner will copy it
from, run it as written on the admin connection against rows seeded in the shapes the services
leave, and read which organizations come back. Editing the queries and this module together is the
point: a query changed in the RUNBOOK alone is a query nobody has run.

The shapes (each seeded for its own organization; every one is a state the fold really leaves):

* a standalone subscription **cancelled or suspended** beside an activation that stayed on: a
  dropped stop, or a hand activation over the row the subscription's fold wrote. Query 1 lists it.
* the same cancelled standalone row beside a **live suite**: the customer moved to a bundle, the
  suite's source keeps the product on, and a subscription keeps its row for ever (one per
  organization and product). Neither query lists it.
* a **lapsed trial**, which keeps `status = 'trial'` and stops entitling by the clock alone: not on.
* a cancelled or suspended **suite** whose member is still on with nothing else feeding it: query 2.
* console's snapshot disagreeing with a stopped subscription: query 3.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.config import settings

# The cross-service lock on the schemas this module reads and seeds, and the cleanup after it. The
# fixtures live with the stop-event folds, which share the same product and console schemas; they
# are resolved by name, so they are imported for pytest and requested through `usefixtures`.
from tests.test_stop_event_folds import (  # noqa: F401 - fixtures, resolved by name
    cleanup_folds,
    folded_schemas,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("cleanup_folds")]

RUNBOOK = Path(__file__).resolve().parents[3] / "infrastructure" / "edge" / "RUNBOOK.md"
SECTION = "### Stop events and the hand-activation gate"

NOW = datetime.now(UTC)
PAST = NOW - timedelta(days=3)
FUTURE = NOW + timedelta(days=3)
#: The envelope time a fold stamps on the rows it writes. Any value: only "set" matters here.
FOLDED_AT = NOW - timedelta(days=40)


def _audit_queries() -> list[str]:
    """The three audit statements, as the RUNBOOK's psql heredoc spells them, one string each."""
    text_ = RUNBOOK.read_text()
    start = text_.index(SECTION)
    end = text_.index("\n### ", start + len(SECTION))
    section = text_[start:end]
    match = re.search(r"<<'SQL'\n(.*?)\nSQL\n", section, flags=re.DOTALL)
    assert match, "the audit heredoc is gone from the RUNBOOK section"
    body = "\n".join(
        line for line in match.group(1).splitlines() if not line.lstrip().startswith("--")
    )
    statements = [s.strip() for s in body.split(";") if s.strip()]
    guard, *queries = statements
    assert guard == "SET default_transaction_read_only = on", guard
    assert len(queries) == 3, f"expected the three audit queries, found {len(queries)}"
    for query in queries:
        assert query.upper().startswith("SELECT"), query
    return queries


QUERY_1, QUERY_2, QUERY_3 = _audit_queries()


async def _add_subscription(
    conn: AsyncConnection,
    org: uuid.UUID,
    product_key: str,
    state: str,
) -> uuid.UUID:
    sid = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO subscription.subscriptions"
            " (id, organization_id, product_key, plan_version_id, state, currency)"
            " VALUES (:id, :org, :product, :pv, :state, 'INR')"
        ),
        {"id": sid, "org": org, "product": product_key, "pv": uuid.uuid4(), "state": state},
    )
    return sid


async def _add_activation(
    conn: AsyncConnection,
    org: uuid.UUID,
    product_key: str,
    status: str,
    *,
    trial_ends_at: datetime | None = None,
    deactivated_at: datetime | None = None,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO product.activations"
            " (id, organization_id, product_key, status, trial_ends_at, deactivated_at, features)"
            " VALUES (:id, :org, :product, :status, :trial_ends_at, :deactivated_at, '{}'::jsonb)"
        ),
        {
            "id": uuid.uuid4(),
            "org": org,
            "product": product_key,
            "status": status,
            "trial_ends_at": trial_ends_at,
            "deactivated_at": deactivated_at,
        },
    )


async def _add_source(
    conn: AsyncConnection,
    org: uuid.UUID,
    product_key: str,
    source_key: str,
    status: str,
    *,
    subscription_id: uuid.UUID | None = None,
    trial_ends_at: datetime | None = None,
) -> None:
    """A source row as the fold leaves it: carrying the subscription's id and the event's time.
    A hand activation has neither (`subscription_id=None` leaves both NULL)."""
    await conn.execute(
        text(
            "INSERT INTO product.activation_sources"
            " (id, organization_id, product_key, source_product_key, source_subscription_id,"
            "  status, trial_ends_at, state_occurred_at)"
            " VALUES (:id, :org, :product, :source, :sid, :status, :trial_ends_at, :at)"
        ),
        {
            "id": uuid.uuid4(),
            "org": org,
            "product": product_key,
            "source": source_key,
            "sid": subscription_id,
            "status": status,
            "trial_ends_at": trial_ends_at,
            "at": FOLDED_AT if subscription_id is not None else None,
        },
    )


async def _add_console_state(conn: AsyncConnection, org: uuid.UUID, states: dict[str, str]) -> None:
    await conn.execute(
        text(
            "INSERT INTO console.org_commercial_state"
            " (id, organization_id, mrr_minor, active_products, subscription_states)"
            " VALUES (:id, :org, 0, '[]'::jsonb, CAST(:states AS jsonb))"
        ),
        {
            "id": uuid.uuid4(),
            "org": org,
            "states": json.dumps({k: {"state": v} for k, v in states.items()}),
        },
    )


@pytest.fixture
def folded_orgs() -> list[uuid.UUID]:
    """The organizations this test seeded, so `cleanup_folds` finds every one. Each test adds its
    own through `_new_org`; none shares an organization with another."""
    return []


@pytest_asyncio.fixture
async def conn(folded_orgs: list[uuid.UUID]) -> AsyncIterator[AsyncConnection]:
    """An admin (BYPASSRLS) connection, in a transaction the test commits by leaving it open.

    Seeds and runs the audit on the one connection, as the owner's psql session would see the
    committed rows; the rows are removed again by `cleanup_folds` (product, console) and by the
    delete below (subscription), so no other suite sees them.
    """
    engine = create_async_engine(settings().admin_dsn)
    async with engine.connect() as connection:
        yield connection
        await connection.rollback()
    async with engine.begin() as cleanup:
        await cleanup.execute(
            text("DELETE FROM subscription.subscriptions WHERE organization_id = ANY(:orgs)"),
            {"orgs": folded_orgs},
        )
    await engine.dispose()


async def _run(connection: AsyncConnection, query: str, orgs: list[uuid.UUID]) -> list[Any]:
    """The audit's rows for `orgs` only: the schemas may hold other suites' leftovers."""
    result = await connection.execute(text(query))
    return [row for row in result.mappings().all() if row["organization_id"] in orgs]


def _new_org(folded_orgs: list[uuid.UUID]) -> uuid.UUID:
    org = uuid.uuid4()
    folded_orgs.append(org)
    return org


# ----------------------------------------------------------------------------- query 1


@pytest.mark.parametrize("state", ["cancelled", "suspended"])
class TestQuery1TheStandaloneCase:
    async def test_lists_a_stopped_subscription_whose_product_stayed_on(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID], state: str
    ) -> None:
        """The victim: a dropped stop (or a hand activation over the fold's row) leaves the
        subscription stopped and its own source still live."""
        org = _new_org(folded_orgs)
        sid = await _add_subscription(conn, org, "hrms", state)
        await _add_activation(conn, org, "hrms", "active")
        await _add_source(conn, org, "hrms", "hrms", "active", subscription_id=sid)
        await conn.commit()

        rows = await _run(conn, QUERY_1, [org])
        assert [(r["product_key"], r["subscription_state"]) for r in rows] == [("hrms", state)]

    async def test_lists_a_hand_trial_that_has_not_lapsed(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID], state: str
    ) -> None:
        org = _new_org(folded_orgs)
        sid = await _add_subscription(conn, org, "hrms", state)
        await _add_activation(conn, org, "hrms", "trial", trial_ends_at=FUTURE)
        await _add_source(
            conn, org, "hrms", "hrms", "trial", subscription_id=sid, trial_ends_at=FUTURE
        )
        await conn.commit()

        assert len(await _run(conn, QUERY_1, [org])) == 1

    async def test_does_not_list_a_product_a_live_suite_keeps_on(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID], state: str
    ) -> None:
        """The false positive. A customer moved from standalone CRM to the trio: the standalone
        subscription is stopped and keeps its row for ever, and the suite's source keeps the
        product on. Nothing here is a victim."""
        org = _new_org(folded_orgs)
        standalone = await _add_subscription(conn, org, "crm", state)
        suite = await _add_subscription(conn, org, "suite", "active")
        await _add_activation(conn, org, "crm", "active")
        await _add_source(conn, org, "crm", "crm", "suspended", subscription_id=standalone)
        await _add_source(conn, org, "crm", "suite", "active", subscription_id=suite)
        await conn.commit()

        assert await _run(conn, QUERY_1, [org]) == []

    async def test_does_not_list_a_product_a_suite_keeps_on_through_a_live_trial(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID], state: str
    ) -> None:
        org = _new_org(folded_orgs)
        standalone = await _add_subscription(conn, org, "crm", state)
        suite = await _add_subscription(conn, org, "suite", "trial")
        await _add_activation(conn, org, "crm", "trial", trial_ends_at=FUTURE)
        await _add_source(conn, org, "crm", "crm", "suspended", subscription_id=standalone)
        await _add_source(
            conn, org, "crm", "suite", "trial", subscription_id=suite, trial_ends_at=FUTURE
        )
        await conn.commit()

        assert await _run(conn, QUERY_1, [org]) == []

    async def test_lists_a_product_whose_only_other_source_is_a_lapsed_suite_trial(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID], state: str
    ) -> None:
        """A lapsed trial feeds nothing: the standalone's own live source is what keeps this on."""
        org = _new_org(folded_orgs)
        standalone = await _add_subscription(conn, org, "crm", state)
        suite = await _add_subscription(conn, org, "suite", "trial")
        await _add_activation(conn, org, "crm", "active")
        await _add_source(conn, org, "crm", "crm", "active", subscription_id=standalone)
        await _add_source(
            conn, org, "crm", "suite", "trial", subscription_id=suite, trial_ends_at=PAST
        )
        await conn.commit()

        assert len(await _run(conn, QUERY_1, [org])) == 1

    async def test_does_not_list_a_lapsed_trial(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID], state: str
    ) -> None:
        """`status` stays `trial` after the end passes; the gateway stops entitling by the clock,
        so the product is not on."""
        org = _new_org(folded_orgs)
        sid = await _add_subscription(conn, org, "hrms", state)
        await _add_activation(conn, org, "hrms", "trial", trial_ends_at=PAST)
        await _add_source(
            conn, org, "hrms", "hrms", "trial", subscription_id=sid, trial_ends_at=PAST
        )
        await conn.commit()

        assert await _run(conn, QUERY_1, [org]) == []

    async def test_does_not_list_a_product_that_was_switched_off(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID], state: str
    ) -> None:
        """The stop folded: the activation is suspended and stamped deactivated."""
        org = _new_org(folded_orgs)
        sid = await _add_subscription(conn, org, "hrms", state)
        await _add_activation(conn, org, "hrms", "suspended", deactivated_at=PAST)
        await _add_source(conn, org, "hrms", "hrms", "suspended", subscription_id=sid)
        await conn.commit()

        assert await _run(conn, QUERY_1, [org]) == []


async def test_query_1_does_not_list_a_live_subscription(
    conn: AsyncConnection, folded_orgs: list[uuid.UUID]
) -> None:
    org = _new_org(folded_orgs)
    sid = await _add_subscription(conn, org, "hrms", "active")
    await _add_activation(conn, org, "hrms", "active")
    await _add_source(conn, org, "hrms", "hrms", "active", subscription_id=sid)
    await conn.commit()

    assert await _run(conn, QUERY_1, [org]) == []


# ----------------------------------------------------------------------------- query 2


@pytest.mark.parametrize("state", ["cancelled", "suspended"])
class TestQuery2TheBundleCase:
    async def test_lists_each_member_of_a_stopped_suite_that_stayed_on(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID], state: str
    ) -> None:
        org = _new_org(folded_orgs)
        suite = await _add_subscription(conn, org, "suite", state)
        for member in ("hrms", "crm"):
            await _add_activation(conn, org, member, "active")
            await _add_source(conn, org, member, "suite", "active", subscription_id=suite)
        await conn.commit()

        rows = await _run(conn, QUERY_2, [org])
        assert sorted((r["subscription_key"], r["member"]) for r in rows) == [
            ("suite", "crm"),
            ("suite", "hrms"),
        ]

    async def test_does_not_list_a_member_another_live_subscription_feeds(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID], state: str
    ) -> None:
        org = _new_org(folded_orgs)
        suite = await _add_subscription(conn, org, "suite", state)
        standalone = await _add_subscription(conn, org, "hrms", "active")
        await _add_activation(conn, org, "hrms", "active")
        await _add_source(conn, org, "hrms", "suite", "active", subscription_id=suite)
        await _add_source(conn, org, "hrms", "hrms", "active", subscription_id=standalone)
        await conn.commit()

        assert await _run(conn, QUERY_2, [org]) == []

    async def test_lists_a_member_whose_only_other_source_is_a_lapsed_trial(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID], state: str
    ) -> None:
        org = _new_org(folded_orgs)
        suite = await _add_subscription(conn, org, "suite", state)
        standalone = await _add_subscription(conn, org, "hrms", "trial")
        await _add_activation(conn, org, "hrms", "active")
        await _add_source(conn, org, "hrms", "suite", "active", subscription_id=suite)
        await _add_source(
            conn, org, "hrms", "hrms", "trial", subscription_id=standalone, trial_ends_at=PAST
        )
        await conn.commit()

        assert [r["member"] for r in await _run(conn, QUERY_2, [org])] == ["hrms"]

    async def test_does_not_list_a_member_whose_trial_has_lapsed(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID], state: str
    ) -> None:
        org = _new_org(folded_orgs)
        suite = await _add_subscription(conn, org, "suite", state)
        await _add_activation(conn, org, "hrms", "trial", trial_ends_at=PAST)
        await _add_source(
            conn, org, "hrms", "suite", "trial", subscription_id=suite, trial_ends_at=PAST
        )
        await conn.commit()

        assert await _run(conn, QUERY_2, [org]) == []

    async def test_lists_a_member_only_a_hand_activation_keeps_on(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID], state: str
    ) -> None:
        """The leak the gate cannot close (ADR-0037 (e)): the owner hand-activated a bundle member
        before the suite's first event folded, and the suite's cancel left that row on. A hand row
        carries no provenance, so it is not "another subscription's live source", and the audit is
        what finds it."""
        org = _new_org(folded_orgs)
        suite = await _add_subscription(conn, org, "suite", state)
        await _add_activation(conn, org, "hrms", "trial", trial_ends_at=FUTURE)
        await _add_source(conn, org, "hrms", "hrms", "trial", trial_ends_at=FUTURE)
        await _add_source(conn, org, "hrms", "suite", "suspended", subscription_id=suite)
        await conn.commit()

        assert [r["member"] for r in await _run(conn, QUERY_2, [org])] == ["hrms"]

    async def test_does_not_list_a_member_the_stop_switched_off(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID], state: str
    ) -> None:
        org = _new_org(folded_orgs)
        suite = await _add_subscription(conn, org, "suite", state)
        await _add_activation(conn, org, "hrms", "suspended", deactivated_at=PAST)
        await _add_source(conn, org, "hrms", "suite", "suspended", subscription_id=suite)
        await conn.commit()

        assert await _run(conn, QUERY_2, [org]) == []


# ----------------------------------------------------------------------------- query 3


class TestQuery3ConsolesSnapshot:
    async def test_lists_a_snapshot_that_still_counts_a_stopped_subscription(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID]
    ) -> None:
        org = _new_org(folded_orgs)
        await _add_subscription(conn, org, "hrms", "cancelled")
        await _add_console_state(conn, org, {"hrms": "active"})
        await conn.commit()

        rows = await _run(conn, QUERY_3, [org])
        assert [(r["product_key"], r["subscription_state"], r["console_state"]) for r in rows] == [
            ("hrms", "cancelled", "active")
        ]

    async def test_does_not_list_a_snapshot_that_agrees(
        self, conn: AsyncConnection, folded_orgs: list[uuid.UUID]
    ) -> None:
        org = _new_org(folded_orgs)
        await _add_subscription(conn, org, "hrms", "suspended")
        await _add_console_state(conn, org, {"hrms": "suspended"})
        await conn.commit()

        assert await _run(conn, QUERY_3, [org]) == []


# ------------------------------------------------------------------- read-only


def test_the_audit_is_a_set_of_selects_in_a_read_only_session() -> None:
    """Nothing in the audit writes: it runs on the production box, as the owner."""
    for query in (QUERY_1, QUERY_2, QUERY_3):
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER)\b", query, re.I), query
