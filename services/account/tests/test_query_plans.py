"""Query plans under row-level security: an index that serves the tenant qual must exist.

The first entry in quality-gate row 15 (query plans). ADR-0002 observed that once policies are
on, every statement on a tenant table carries `organization_id = current_org()` whether or not
the application wrote it — so an index that does not lead with that column reads the whole
platform's rows for a value and discards all but one tenant's. The migration recomposed the
three that would have hurt first. These tests hold the line: the recomposed indexes exist, the
single-column ones they replace are gone, and the planner can actually use the new ones for the
queries they were built for.

**Why `SET LOCAL enable_seqscan = off` is legitimate here.** The tables hold a handful of rows
in a test, and for a handful of rows a sequential scan is genuinely the cheapest plan; asking
"does the planner pick the index?" at this size would be asking the wrong question and getting
a meaningless no. Turning sequential scans off forces the planner to show which index it *can*
use for the qual — and that is the claim under test: "an index exists that serves the
tenant-scoped access pattern", not "the planner chooses it at this row count". The moment the
index is missing or leads with the wrong column, the plan falls to a different index or a
filtered scan, and the assertion fails for the right reason.

Run on the A-bound app-role session on purpose. The policy qual is part of the query the
planner sees, which is exactly the shape production runs.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Membership, Organization

pytestmark = pytest.mark.integration

#: The single-column indexes `AuditLogMixin` declared on `account.audit_logs`, all retired.
RETIRED_AUDIT_INDEXES = frozenset(
    {
        "ix_account_audit_logs_at",
        "ix_account_audit_logs_action",
        "ix_account_audit_logs_entity",
        "ix_account_audit_logs_entity_id",
        "ix_account_audit_logs_actor_id",
        "ix_account_audit_logs_organization_id",
    }
)

#: Their tenant-leading replacements.
TENANT_LEADING_AUDIT_INDEXES = frozenset(
    {
        "ix_audit_logs_org_at",
        "ix_audit_logs_org_entity",
        "ix_audit_logs_org_actor",
        "ix_audit_logs_org_action",
    }
)


def _index_names(plan: Any) -> set[str]:
    """Every index the plan tree touches, at any depth."""
    found: set[str] = set()
    if isinstance(plan, dict):
        if "Index Name" in plan:
            found.add(plan["Index Name"])
        for value in plan.values():
            found |= _index_names(value)
    elif isinstance(plan, list):
        for value in plan:
            found |= _index_names(value)
    return found


async def _indexes_usable_for(session: AsyncSession, sql: str, params: dict[str, Any]) -> set[str]:
    """The indexes the planner reaches for when it may not scan the table."""
    async with session.begin():
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        raw = (await session.execute(text(f"EXPLAIN (FORMAT JSON) {sql}"), params)).scalar_one()
    # asyncpg hands JSON back as text unless a codec is registered; accept either.
    plan = raw if isinstance(raw, list) else json.loads(raw)
    return _index_names(plan)


async def _audit_log_indexes(session: AsyncSession) -> dict[str, str]:
    """`pg_indexes` for the audit table: name to definition."""
    rows = await session.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'account' AND tablename = 'audit_logs'"
        )
    )
    return dict(rows.all())


class TestTheTenantLeadingIndexesServeTheirQueries:
    async def test_an_organizations_audit_log_newest_first_is_served_by_the_org_at_index(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """The audit log is read newest-first, and it is the highest-volume table here.

        `(organization_id, at DESC)` serves both the tenant qual and the ordering, so "the
        latest fifty" walks the index and stops rather than sorting everything the tenant owns.
        """
        used = await _indexes_usable_for(
            session,
            "SELECT * FROM account.audit_logs WHERE organization_id = :o ORDER BY at DESC LIMIT 50",
            {"o": org.id},
        )
        assert "ix_audit_logs_org_at" in used, (
            f"the newest-first audit read was planned over {sorted(used) or 'no index'}. Under "
            "row-level security that query always carries the tenant; the index serving it must "
            "lead with organization_id and carry `at DESC`."
        )

    async def test_an_organizations_members_by_manager_are_served_by_the_manager_index(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """TEAM scope is "who reports to X", asked once per scoped request."""
        used = await _indexes_usable_for(
            session,
            "SELECT id FROM account.memberships "
            "WHERE organization_id = :o AND manager_membership_id = :m",
            {"o": org.id, "m": people["manager"].id},
        )
        assert "ix_memberships_manager" in used, (
            f"the reports-of-a-manager lookup was planned over {sorted(used) or 'no index'}; "
            "ix_memberships_manager should lead with the tenant and carry manager_membership_id."
        )

    async def test_an_organizations_units_by_parent_are_served_by_the_parent_index(
        self, session: AsyncSession, org: Organization, people: dict[str, Membership]
    ) -> None:
        """`descendant_ids` walks the parent link one level at a time; each level is this query."""
        used = await _indexes_usable_for(
            session,
            "SELECT id FROM account.org_units WHERE organization_id = :o AND parent_id = :p",
            {"o": org.id, "p": uuid.uuid4()},
        )
        assert "ix_org_units_parent" in used, (
            f"the children-of-a-unit lookup was planned over {sorted(used) or 'no index'}; "
            "ix_org_units_parent should lead with the tenant and carry parent_id."
        )


class TestTheSingleColumnAuditIndexesAreGone:
    async def test_the_retired_audit_log_indexes_no_longer_exist(
        self, session: AsyncSession, org: Organization
    ) -> None:
        """Dropped, not merely superseded.

        Leaving them would cost every audit insert six index writes instead of five for no
        read that could use them, on the table that takes the most inserts on the platform.
        """
        present = set(await _audit_log_indexes(session))
        lingering = present & RETIRED_AUDIT_INDEXES
        assert not lingering, (
            f"{sorted(lingering)} still exist on account.audit_logs. The migration that "
            "recomposed the audit indexes has not been applied, or a later one brought them back."
        )
        missing = TENANT_LEADING_AUDIT_INDEXES - present
        assert not missing, f"{sorted(missing)} are missing from account.audit_logs"

    async def test_every_secondary_audit_log_index_leads_with_the_tenant(
        self, session: AsyncSession, org: Organization
    ) -> None:
        """The property, stated so it survives the next index somebody adds.

        The primary key is the one legitimate exception: it is how a single row is addressed,
        and the policy qual is applied to it as a filter. Everything else on this table exists
        to serve a tenant-scoped read and must say so in its first column.
        """
        offenders: list[str] = []
        for name, definition in (await _audit_log_indexes(session)).items():
            if name.startswith("pk_"):
                continue
            match = re.search(r"USING \w+ \((\w+)", definition)
            assert match is not None, f"could not read the columns of {name}: {definition}"
            if match.group(1) != "organization_id":
                offenders.append(f"{name}: {definition}")
        assert not offenders, (
            "these account.audit_logs indexes do not lead with organization_id, so under "
            "row-level security they read every tenant's rows for a value and discard most of "
            f"them: {offenders}"
        )
