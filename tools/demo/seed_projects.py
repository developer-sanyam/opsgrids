"""Seed the delivery portfolio for the demo org, mirroring the SPA fixtures.

Run **inside `services/projects`** as a subprocess (every service exposes a top-level `app`, so
one process can import only one of them — the same constraint the other seeders live under).

Mirrors `services/projects/tests/conftest.py`: connects as the NOBYPASSRLS app role and binds the
tenant GUC on every transaction (the GUC is transaction-local), so writes go through the same
row-level-security path a real request does, and through the real `services.*` write paths — so
members, projects, milestones and board tasks all land with an audit row and an outbox event,
exactly as the live product produces them.

The dataset is the SPA's `frontend/product-projects/src/api/fixtures.ts`, one-for-one: the same
10 members, 14 projects, 16 milestones and 18 board tasks, cross-linked by member ref and project
code, so the live screens look like the fixtures the user already approved. Money is carried in
paise (integer minor units): the fixtures' rupee figures are multiplied by 100 here.

Idempotent: a member ref / project code / (project, title) that already exists is skipped, so
re-running never duplicates.

Usage: seed_projects.py <projects_service_dir> <org_id> <admin_user_id> <admin_membership_id>
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Any

from _common import bootstrap, date_from_iso

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from platform_core.auth import ScopeContext


# ------------------------------------------------------------------ members (ref, name, role)
MEMBERS: list[tuple[str, str, str]] = [
    ("p-01", "Aarav Sharma", "Delivery Lead"),
    ("p-02", "Lena Nowak", "Product Manager"),
    ("p-03", "Diego Alvarez", "Engineering Lead"),
    ("p-04", "Priya Nair", "Design Lead"),
    ("p-05", "Kenji Tanaka", "Program Manager"),
    ("p-06", "Fatima Al-Sayed", "QA Lead"),
    ("p-07", "Marcus Feng", "Tech Lead"),
    ("p-08", "Sofia Marchetti", "Account Director"),
    ("p-09", "Ravi Deshpande", "DevOps Engineer"),
    ("p-10", "Grace Okoro", "Business Analyst"),
]

# ------------------------------------------------------------------ projects
# (code, name, client, owner_ref, phase, health, progress, start, target, budget_rupees,
#  spent_rupees, team, open_tasks, summary)
PROJECTS: list[tuple[Any, ...]] = [
    (
        "PRJ-104",
        "Northwind Trade Portal",
        "Northwind Trading",
        "p-01",
        "build",
        "on_track",
        62,
        "2026-04-01",
        "2026-10-15",
        12_400_000,
        7_320_000,
        9,
        34,
        "A B2B ordering portal replacing Northwind's spreadsheet-driven purchase flow.",
    ),
    (
        "PRJ-108",
        "Helios Grid Analytics",
        "Helios Energy",
        "p-02",
        "design",
        "at_risk",
        34,
        "2026-06-10",
        "2026-12-20",
        18_600_000,
        5_100_000,
        7,
        41,
        "Real-time load and outage analytics across Helios's regional grid substations.",
    ),
    (
        "PRJ-111",
        "Meridian Mobile Banking",
        "Meridian Bank",
        "p-03",
        "build",
        "on_track",
        71,
        "2026-02-15",
        "2026-09-30",
        24_000_000,
        16_800_000,
        12,
        28,
        "A ground-up mobile banking app with biometric auth and instant transfers.",
    ),
    (
        "PRJ-115",
        "Cobalt Fleet Control",
        "Cobalt Robotics",
        "p-05",
        "stabilize",
        "on_track",
        88,
        "2026-01-20",
        "2026-09-05",
        15_800_000,
        13_900_000,
        8,
        12,
        "Command-and-control dashboard for Cobalt's autonomous warehouse fleet.",
    ),
    (
        "PRJ-119",
        "Verdant Supply Tracker",
        "Verdant Foods",
        "p-10",
        "discovery",
        "on_track",
        12,
        "2026-07-28",
        "2027-02-10",
        9_200_000,
        940_000,
        5,
        47,
        "Farm-to-shelf traceability for Verdant's cold-chain produce network.",
    ),
    (
        "PRJ-122",
        "Atlas Route Optimizer",
        "Atlas Logistics",
        "p-07",
        "build",
        "delayed",
        48,
        "2026-05-05",
        "2026-11-12",
        16_500_000,
        11_200_000,
        9,
        39,
        "A routing engine cutting last-mile mileage across Atlas's delivery network.",
    ),
    (
        "PRJ-126",
        "Lumen Patient Portal",
        "Lumen Health",
        "p-04",
        "launch",
        "at_risk",
        95,
        "2025-11-10",
        "2026-09-01",
        21_300_000,
        20_100_000,
        10,
        9,
        "Appointments, records and messaging portal for Lumen's clinic network.",
    ),
    (
        "PRJ-130",
        "Orion Telemetry Hub",
        "Orion Aerospace",
        "p-03",
        "build",
        "on_track",
        55,
        "2026-03-18",
        "2026-10-30",
        27_500_000,
        15_400_000,
        11,
        33,
        "High-throughput ingestion and replay of flight telemetry streams.",
    ),
    (
        "PRJ-133",
        "Northwind Vendor Onboarding",
        "Northwind Trading",
        "p-08",
        "design",
        "on_track",
        40,
        "2026-06-01",
        "2026-12-05",
        8_800_000,
        3_100_000,
        6,
        26,
        "Self-service vendor registration, KYC and catalogue submission.",
    ),
    (
        "PRJ-137",
        "Helios Field Ops App",
        "Helios Energy",
        "p-09",
        "stabilize",
        "delayed",
        80,
        "2026-02-02",
        "2026-09-18",
        13_600_000,
        12_700_000,
        7,
        18,
        "Offline-first mobile app for Helios field crews logging inspections.",
    ),
    (
        "PRJ-140",
        "Meridian Fraud Engine",
        "Meridian Bank",
        "p-06",
        "build",
        "on_track",
        66,
        "2026-04-22",
        "2026-11-28",
        19_900_000,
        12_100_000,
        8,
        31,
        "A streaming fraud-scoring engine feeding Meridian's transaction pipeline.",
    ),
    (
        "PRJ-143",
        "Cobalt Analytics Cloud",
        "Cobalt Robotics",
        "p-02",
        "discovery",
        "on_track",
        18,
        "2026-07-15",
        "2027-01-22",
        11_400_000,
        1_600_000,
        5,
        44,
        "A tenant-isolated analytics warehouse for Cobalt's robot fleet telemetry.",
    ),
    (
        "PRJ-146",
        "Verdant Retail Sync",
        "Verdant Foods",
        "p-01",
        "launch",
        "on_track",
        92,
        "2025-12-08",
        "2026-08-29",
        10_100_000,
        9_400_000,
        6,
        7,
        "Two-way inventory sync between Verdant's ERP and retail point-of-sale.",
    ),
    (
        "PRJ-150",
        "Atlas Warehouse IoT",
        "Atlas Logistics",
        "p-05",
        "live",
        "on_track",
        100,
        "2025-09-01",
        "2026-06-30",
        14_200_000,
        13_800_000,
        4,
        3,
        "Sensor mesh and dashboards monitoring Atlas's automated warehouses. Now in run.",
    ),
]

# ------------------------------------------------------------------ milestones
# (project_code, title, owner_ref, due, status, progress)
MILESTONES: list[tuple[Any, ...]] = [
    ("PRJ-111", "Beta release to 500 pilot users", "p-03", "2026-08-28", "due_soon", 84),
    ("PRJ-122", "Routing algorithm v2 sign-off", "p-07", "2026-08-22", "slipping", 61),
    ("PRJ-137", "Offline sync general availability", "p-09", "2026-08-20", "slipping", 72),
    ("PRJ-126", "Security audit closure", "p-04", "2026-08-30", "at_risk", 55),
    ("PRJ-146", "Store rollout — wave 1", "p-01", "2026-08-29", "due_soon", 78),
    ("PRJ-126", "Go-live readiness review", "p-04", "2026-09-01", "due_soon", 62),
    ("PRJ-115", "Hardware certification pass", "p-05", "2026-09-02", "on_track", 90),
    ("PRJ-108", "Data model approval", "p-02", "2026-09-05", "at_risk", 30),
    ("PRJ-119", "Discovery workshop wrap-up", "p-10", "2026-09-08", "on_track", 40),
    ("PRJ-104", "Payment gateway integration", "p-01", "2026-09-10", "on_track", 48),
    ("PRJ-140", "Fraud model training complete", "p-06", "2026-09-15", "on_track", 58),
    ("PRJ-130", "Ingestion pipeline live", "p-03", "2026-09-20", "on_track", 44),
    ("PRJ-133", "Vendor pilot kickoff", "p-08", "2026-09-25", "on_track", 36),
    ("PRJ-111", "App store submission", "p-03", "2026-09-28", "on_track", 40),
    ("PRJ-146", "ERP cutover", "p-01", "2026-08-18", "completed", 100),
    ("PRJ-150", "Sensor fleet handover to run team", "p-05", "2026-06-28", "completed", 100),
]

# ------------------------------------------------------------------ tasks (board cards)
# (project_code, title, assignee_ref, stage, priority, due|None, points)
TASKS: list[tuple[Any, ...]] = [
    # Backlog
    ("PRJ-119", "Map cold-chain data sources", "p-10", "backlog", "medium", "2026-09-04", 5),
    ("PRJ-143", "Draft tenant-isolation model", "p-02", "backlog", "high", "2026-09-06", 8),
    ("PRJ-108", "Substation ingestion spike", "p-09", "backlog", "medium", None, 3),
    ("PRJ-104", "Bulk-order CSV import", "p-07", "backlog", "low", "2026-09-12", 5),
    ("PRJ-130", "Telemetry replay UX brief", "p-04", "backlog", "medium", None, 2),
    # In progress
    ("PRJ-111", "Instant-transfer flow", "p-03", "in_progress", "urgent", "2026-08-27", 8),
    ("PRJ-122", "Rewrite routing cost function", "p-07", "in_progress", "urgent", "2026-08-26", 13),
    ("PRJ-140", "Streaming feature store", "p-06", "in_progress", "high", "2026-09-01", 8),
    (
        "PRJ-137",
        "Conflict resolution for offline edits",
        "p-09",
        "in_progress",
        "high",
        "2026-08-28",
        5,
    ),
    ("PRJ-133", "KYC document upload", "p-08", "in_progress", "medium", "2026-09-03", 5),
    # Review
    ("PRJ-126", "Pen-test remediation batch", "p-04", "review", "urgent", "2026-08-26", 8),
    ("PRJ-115", "Fleet command latency fixes", "p-05", "review", "high", "2026-08-27", 5),
    ("PRJ-111", "Biometric enrolment edge cases", "p-03", "review", "high", "2026-08-25", 3),
    ("PRJ-146", "POS sync reconciliation report", "p-01", "review", "medium", "2026-08-28", 3),
    # Done
    ("PRJ-146", "ERP cutover runbook", "p-01", "done", "high", "2026-08-18", 5),
    ("PRJ-104", "Catalogue search indexing", "p-07", "done", "medium", "2026-08-15", 8),
    ("PRJ-115", "Telemetry retention policy", "p-05", "done", "low", "2026-08-12", 2),
    ("PRJ-140", "Transaction schema v3", "p-06", "done", "medium", "2026-08-19", 5),
]


async def seed(session: AsyncSession, ctx: ScopeContext) -> dict[str, int]:
    """Insert the full projects demo dataset through the real service write paths. Idempotent.

    Importable and side-effect-free at module load, so tests can drive it directly. Runs inside a
    transaction the caller opens.
    """
    from sqlalchemy import select

    from app import selectors, services  # type: ignore[import-not-found]
    from app.models import Milestone, Task  # type: ignore[import-not-found]

    counts = {"members": 0, "projects": 0, "milestones": 0, "tasks": 0, "skipped": 0}
    org = ctx.organization_id

    # --- members ---
    for ref, name, role in MEMBERS:
        if await selectors.get_member_by_ref(session, org, ref):
            counts["skipped"] += 1
            continue
        await services.create_member(session, ctx, ref=ref, name=name, role=role)
        counts["members"] += 1

    # --- projects ---
    for (
        code,
        name,
        client,
        owner_ref,
        phase,
        health,
        progress,
        start,
        target,
        budget_rupees,
        spent_rupees,
        team,
        open_tasks,
        summary,
    ) in PROJECTS:
        if await selectors.get_project(session, org, code):
            counts["skipped"] += 1
            continue
        owner = await selectors.get_member_by_ref(session, org, owner_ref)
        await services.create_project(
            session,
            ctx,
            code=code,
            name=name,
            client=client,
            owner_id=owner.id if owner else None,
            phase=phase,
            health=health,
            progress_pct=progress,
            start_date=date_from_iso(start),
            target_date=date_from_iso(target),
            budget_minor=budget_rupees * 100,
            spent_minor=spent_rupees * 100,
            currency="INR",
            team_size=team,
            open_tasks=open_tasks,
            summary=summary,
        )
        counts["projects"] += 1

    # Existing (project_id, title) pairs, so milestones/tasks stay idempotent without a natural key.
    existing_ms = {
        (row[0], row[1])
        for row in (
            await session.execute(
                select(Milestone.project_id, Milestone.title).where(
                    Milestone.organization_id == org
                )
            )
        ).all()
    }
    existing_tasks = {
        (row[0], row[1])
        for row in (
            await session.execute(
                select(Task.project_id, Task.title).where(Task.organization_id == org)
            )
        ).all()
    }

    # --- milestones ---
    for project_code, title, owner_ref, due, status, progress in MILESTONES:
        project = await selectors.get_project(session, org, project_code)
        if project is None:
            counts["skipped"] += 1
            continue
        if (project.id, title) in existing_ms:
            counts["skipped"] += 1
            continue
        owner = await selectors.get_member_by_ref(session, org, owner_ref)
        await services.create_milestone(
            session,
            ctx,
            project_id=project.id,
            title=title,
            owner_id=owner.id if owner else None,
            due_date=date_from_iso(due),
            status=status,
            progress_pct=progress,
        )
        existing_ms.add((project.id, title))
        counts["milestones"] += 1

    # --- tasks ---
    for project_code, title, assignee_ref, stage, priority, due, points in TASKS:
        project = await selectors.get_project(session, org, project_code)
        if project is None:
            counts["skipped"] += 1
            continue
        if (project.id, title) in existing_tasks:
            counts["skipped"] += 1
            continue
        assignee = await selectors.get_member_by_ref(session, org, assignee_ref)
        await services.create_task(
            session,
            ctx,
            project_id=project.id,
            title=title,
            assignee_id=assignee.id if assignee else None,
            stage=stage,
            priority=priority,
            due_date=date_from_iso(due) if due else None,
            points=points,
        )
        existing_tasks.add((project.id, title))
        counts["tasks"] += 1

    return counts


# ------------------------------------------------------------------ standalone entry point


async def _main(
    org_id: uuid.UUID, admin_user_id: uuid.UUID, admin_membership_id: uuid.UUID
) -> None:
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings  # type: ignore[import-not-found]
    from app.models import SCHEMA
    from platform_core.auth import Principal, Scope, ScopeContext
    from platform_core.db import TENANT_GUC

    cfg = settings()
    engine = create_async_engine(
        cfg.app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    ctx = ScopeContext(
        principal=Principal(
            sub=admin_user_id,
            organization_id=org_id,
            membership_id=admin_membership_id,
            perms=["projects.portfolio.project.write:ORG"],
            entitlements=["projects"],
        ),
        scope=Scope.ORG,
        permission="projects.portfolio.project.write",
    )

    async with maker() as session:

        @event.listens_for(session.sync_session, "after_begin")
        def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        async with session.begin():
            counts = await seed(session, ctx)

    await engine.dispose()
    print(json.dumps(counts))


if __name__ == "__main__":
    _org, _user, _membership = bootstrap()
    asyncio.run(_main(_org, _user, _membership))
