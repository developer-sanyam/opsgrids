"""Reads for the lead service. Visibility rules live here, in one place.

Every scoped list calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
filter is not redundant with row-level security: RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002). Both stay. A list endpoint that builds
its own query instead of coming through a selector is how an organization leaks, so routers
never write SQL.

This module also holds the ORM -> schema projection, because shaping a row for the wire is
part of reading it, so routes stay thin.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AssignmentRule, Lead
from app.schemas import AssignmentRuleOut, LeadOut
from platform_core.auth import ScopeContext

# ------------------------------------------------------------------ leads


def leads_stmt(
    ctx: ScopeContext,
    *,
    status: str | None = None,
    owner_id: uuid.UUID | None = None,
) -> Select[Any]:
    """Scope-filtered lead list, newest first."""
    stmt = select(Lead).where(
        Lead.organization_id == ctx.organization_id,
        Lead.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Lead.owner_id)
    if status is not None:
        stmt = stmt.where(Lead.status == status)
    if owner_id is not None:
        stmt = stmt.where(Lead.owner_id == owner_id)
    return stmt.order_by(Lead.created_at.desc())


async def get_lead(session: AsyncSession, org_id: uuid.UUID, lead_id: uuid.UUID) -> Lead | None:
    """Org-scoped fetch by id. None on a tenant mismatch or a soft-deleted row —
    confirming an id exists in *some* organization is itself a disclosure."""
    stmt = select(Lead).where(
        Lead.id == lead_id,
        Lead.organization_id == org_id,
        Lead.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_lead_for_update(
    session: AsyncSession, org_id: uuid.UUID, lead_id: uuid.UUID
) -> Lead | None:
    """The same fetch as `get_lead`, row-locked for the rest of this transaction —
    `services.claim_conversion` and `services.finalize_conversion`/`fail_conversion` all read
    then write this row, and without the lock two concurrent `:convert` calls on the same
    lead would both read "not yet claimed" and both proceed.

    `populate_existing=True`: `claim_conversion`, `finalize_conversion` and `fail_conversion`
    run as *separate* transactions on the *same* session, so a lead already fetched once this
    session is sitting in SQLAlchemy's identity map. Without this, a second locked fetch for
    the same row returns that cached Python object as-is rather than refreshing it from the
    row Postgres just (re-)locked — the lock is real, but the attributes read off it are
    whatever they were the first time this session touched this id, which is exactly wrong
    for a claim/lease check that exists to see what changed since then.
    """
    stmt = (
        select(Lead)
        .where(Lead.id == lead_id, Lead.organization_id == org_id, Lead.deleted_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_lead_by_email_index(
    session: AsyncSession, org_id: uuid.UUID, email_index: str
) -> Lead | None:
    """The dedup lookup: is this email already a lead in this organization. Reads the blind
    index, never the sealed value — a search is not a read of somebody's address."""
    stmt = select(Lead).where(
        Lead.organization_id == org_id,
        Lead.email_index == email_index,
        Lead.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def lead_out(lead: Lead) -> LeadOut:
    return LeadOut(
        id=str(lead.id),
        full_name=lead.full_name,
        company=lead.company,
        email=lead.email_masked,
        phone=lead.phone_masked,
        source=lead.source,
        status=lead.status,
        owner_id=str(lead.owner_id) if lead.owner_id else None,
        notes=lead.notes,
        converted_company_id=str(lead.converted_company_id) if lead.converted_company_id else None,
        converted_contact_id=str(lead.converted_contact_id) if lead.converted_contact_id else None,
        converted_deal_id=str(lead.converted_deal_id) if lead.converted_deal_id else None,
        converted_at=lead.converted_at,
        created_at=lead.created_at,
    )


# ------------------------------------------------------------------ assignment rule


async def get_assignment_rule(session: AsyncSession, org_id: uuid.UUID) -> AssignmentRule | None:
    stmt = select(AssignmentRule).where(
        AssignmentRule.organization_id == org_id,
        AssignmentRule.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_assignment_rule_for_update(
    session: AsyncSession, org_id: uuid.UUID
) -> AssignmentRule | None:
    """The same fetch as `get_assignment_rule`, row-locked for the rest of this transaction —
    the pick-and-advance step `services._assign_owner` runs needs the read and the cursor
    write to be one atomic step, or two concurrent lead creates can both read cursor `0`, both
    pick `owner_ids[0]`, and both write cursor `1` — the same rep twice, the rotation never
    having moved."""
    stmt = (
        select(AssignmentRule)
        .where(AssignmentRule.organization_id == org_id, AssignmentRule.deleted_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def assignment_rule_out(rule: AssignmentRule) -> AssignmentRuleOut:
    return AssignmentRuleOut(
        mode=rule.mode,
        owner_ids=[str(o) for o in rule.owner_ids],
        cursor=rule.cursor,
    )


# ------------------------------------------------------------------ insights export (internal)


def leads_export_stmt(
    *, after_updated_at: datetime | None, after_id: uuid.UUID | None, limit: int
) -> Select[Any]:
    """Keyset page of **every** lead, soft-deleted included, in `(updated_at, id)` order —
    insights' backfill read (`GET /internal/v1/insights/export/leads`, ADR-0020 §3,
    `docs/insights/api.md` §6.1). No sealed field, no masked field, no `full_name`, `company`
    or `notes` — a lead's person is PII at the source (`docs/insights/events.md` §4), and this
    page carries only what `lead_facts` stores: `status`, `source`, `owner_id`,
    `converted_at`.
    """
    stmt = select(Lead)
    if after_updated_at is not None and after_id is not None:
        stmt = stmt.where(
            tuple_(Lead.updated_at, Lead.id) > tuple_(literal(after_updated_at), literal(after_id))
        )
    return stmt.order_by(Lead.updated_at, Lead.id).limit(limit)
