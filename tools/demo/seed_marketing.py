"""Seed the demo org's marketing data. Run **inside `services/marketing`** as a subprocess.

Mirrors services/marketing/tests/conftest.py: connects as the NOBYPASSRLS app role and binds the
tenant GUC on every transaction (the GUC is transaction-local), so writes go through the same
row-level-security path a real request does. Audiences and campaigns are created through the real
`services.create_audience` / `services.create_campaign` write paths, so each one stages its audit
row and outbox event exactly as a live create would.

The data is the marketing SPA's fixtures, one for one: six audiences, fourteen campaigns across
the four channels in a realistic mix of statuses, and the eight-month revenue-vs-spend series —
so the live screens look like the fixtures the user already approved.

Idempotent: an audience name / campaign name that already exists is skipped, and the revenue
series is upserted by month, so re-running does not duplicate.

Usage: seed_marketing.py <marketing_service_dir> <org_id> <admin_user_id> <admin_membership_id>
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import date
from pathlib import Path

SERVICE_DIR = Path(sys.argv[1]).resolve()
ORG_ID = uuid.UUID(sys.argv[2])
ADMIN_USER_ID = uuid.UUID(sys.argv[3])
ADMIN_MEMBERSHIP_ID = uuid.UUID(sys.argv[4])

sys.path.insert(0, str(SERVICE_DIR))

from sqlalchemy import select  # noqa: E402

from app import selectors, services  # type: ignore[import-not-found]  # noqa: E402
from app.config import settings  # type: ignore[import-not-found]  # noqa: E402
from app.models import SCHEMA, Campaign  # type: ignore[import-not-found]  # noqa: E402
from app.schemas import AudienceIn, CampaignIn  # type: ignore[import-not-found]  # noqa: E402
from platform_core.auth import Principal, Scope, ScopeContext  # noqa: E402
from platform_core.db import TENANT_GUC  # noqa: E402

CURRENCY = "INR"


def lakh(n: float) -> int:
    """Lakh of rupees → paise (minor units): 1 lakh = ₹1,00,000 = 10,000,000 paise."""
    return round(n * 10_000_000)


# (slug, name, size, criteria, created_at) — slug links campaigns to audiences below.
AUDIENCES = [
    (
        "aud-ent-india",
        "Enterprise India",
        4210,
        "1000+ employees · India HQ · IT, BFSI & manufacturing",
        "2025-11-02",
    ),
    (
        "aud-growth-saas",
        "Growth-stage SaaS",
        9840,
        "SaaS · Series A-C · 50-500 staff · product-led",
        "2025-09-18",
    ),
    (
        "aud-trial-lapsed",
        "Lapsed trials",
        3155,
        "Trial ended 30-90 days ago · no purchase · opened ≥1 email",
        "2026-01-12",
    ),
    (
        "aud-in-market",
        "In-market buyers",
        15620,
        "Pricing-page visit · repeat sessions · high intent score",
        "2025-08-04",
    ),
    (
        "aud-existing-cust",
        "Existing customers",
        2470,
        "Active subscription · health score > 60 · expansion-ready",
        "2025-07-21",
    ),
    (
        "aud-gcc-mid",
        "GCC mid-market",
        6090,
        "UAE & KSA HQ · 200-2000 staff · English-first buyers",
        "2026-02-09",
    ),
]

# (name, channel, status, budget, spent, revenue, pipeline, leads, conversions, goal,
#  audience_slug, start_date, end_date, owner) — all money in lakh of rupees.
CAMPAIGNS = [
    (
        "Diwali Enterprise Push",
        "search",
        "live",
        32,
        28.4,
        142,
        320,
        640,
        34,
        "Enterprise pipeline",
        "aud-ent-india",
        "2026-06-15",
        None,
        "Meghna Rao",
    ),
    (
        "Q3 Webinar Series",
        "events",
        "live",
        18,
        15.2,
        61,
        180,
        410,
        18,
        "MQL volume",
        "aud-growth-saas",
        "2026-07-01",
        None,
        "Arre Farhan",
    ),
    (
        "Retargeting: Abandoned Trials",
        "social",
        "live",
        9,
        8.7,
        47,
        66,
        520,
        41,
        "Trial reactivation",
        "aud-trial-lapsed",
        "2026-05-20",
        None,
        "Priya Nair",
    ),
    (
        "Brand Always-On Search",
        "search",
        "live",
        24,
        22.1,
        98,
        210,
        880,
        52,
        "Brand demand capture",
        "aud-in-market",
        "2026-01-05",
        None,
        "Meghna Rao",
    ),
    (
        "Founder Newsletter",
        "email",
        "live",
        4,
        3.6,
        39,
        54,
        300,
        22,
        "Nurture & recall",
        "aud-existing-cust",
        "2026-02-01",
        None,
        "Dev Sharma",
    ),
    (
        "LinkedIn Thought Leadership",
        "social",
        "paused",
        14,
        11.9,
        22,
        88,
        240,
        7,
        "ABM awareness",
        "aud-ent-india",
        "2026-04-10",
        None,
        "Arre Farhan",
    ),
    (
        "GCC Expansion Roadshow",
        "events",
        "live",
        30,
        19.8,
        74,
        260,
        180,
        16,
        "Field pipeline",
        "aud-gcc-mid",
        "2026-07-12",
        None,
        "Layla Hassan",
    ),
    (
        "Cart Recovery Emails",
        "email",
        "live",
        3,
        2.9,
        51,
        30,
        410,
        58,
        "Recover revenue",
        "aud-trial-lapsed",
        "2026-03-08",
        None,
        "Dev Sharma",
    ),
    (
        "Performance Max — Q2",
        "search",
        "ended",
        26,
        26,
        88,
        140,
        720,
        44,
        "Signup volume",
        "aud-in-market",
        "2026-04-01",
        "2026-06-30",
        "Meghna Rao",
    ),
    (
        "Instagram Reels Test",
        "social",
        "draft",
        6,
        0,
        0,
        0,
        0,
        0,
        "Awareness experiment",
        "aud-growth-saas",
        "2026-09-01",
        None,
        "Priya Nair",
    ),
    (
        "Partner Co-Marketing",
        "events",
        "live",
        12,
        9.4,
        58,
        120,
        150,
        14,
        "Partner-sourced leads",
        "aud-gcc-mid",
        "2026-06-02",
        None,
        "Layla Hassan",
    ),
    (
        "Product Update Digest",
        "email",
        "live",
        2.5,
        2.2,
        28,
        24,
        260,
        19,
        "Expansion nudge",
        "aud-existing-cust",
        "2026-01-20",
        None,
        "Dev Sharma",
    ),
    (
        "Competitor Conquest Search",
        "search",
        "paused",
        16,
        15.6,
        24,
        70,
        290,
        6,
        "Displacement",
        "aud-in-market",
        "2026-05-01",
        None,
        "Meghna Rao",
    ),
    (
        "Analyst Summit 2026",
        "events",
        "ended",
        22,
        21.4,
        96,
        240,
        130,
        21,
        "Enterprise trust",
        "aud-ent-india",
        "2026-02-18",
        "2026-03-31",
        "Arre Farhan",
    ),
]

# (label, revenue, spend) in lakh of rupees — eight months, spend climbing, revenue faster.
REVENUE_SERIES = [
    ("Jan", 172, 58),
    ("Feb", 190, 62),
    ("Mar", 205, 71),
    ("Apr", 198, 66),
    ("May", 244, 78),
    ("Jun", 270, 84),
    ("Jul", 312, 92),
    ("Aug", 336, 88),
]


def _ctx(permission: str, scope: Scope) -> ScopeContext:
    principal = Principal(
        sub=ADMIN_USER_ID,
        organization_id=ORG_ID,
        membership_id=ADMIN_MEMBERSHIP_ID,
        perms=[f"{permission}:{scope.value}"],
        entitlements=["marketing"],
    )
    return ScopeContext(principal=principal, scope=scope, permission=permission)


async def main() -> None:
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    cfg = settings()
    connect_args = {"server_settings": {"search_path": f"{SCHEMA},public"}}
    engine = create_async_engine(cfg.app_dsn, connect_args=connect_args)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    write_ctx = _ctx("marketing.analytics.campaign.write", Scope.ORG)

    audiences_created = audiences_skipped = 0
    campaigns_created = campaigns_skipped = 0
    slug_to_id: dict[str, uuid.UUID] = {}

    async with maker() as session:

        @event.listens_for(session.sync_session, "after_begin")
        def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{ORG_ID}', true)")

        async with session.begin():
            for slug, name, size, criteria, created_at in AUDIENCES:
                existing = await selectors.get_audience_by_name(session, ORG_ID, name)
                if existing is not None:
                    slug_to_id[slug] = existing.id
                    audiences_skipped += 1
                    continue
                audience = await services.create_audience(
                    session,
                    write_ctx,
                    AudienceIn(
                        name=name,
                        size=size,
                        criteria=criteria,
                        created_at=date.fromisoformat(created_at),
                    ),
                )
                slug_to_id[slug] = audience.id
                audiences_created += 1

        async with session.begin():
            existing_names = set(
                (
                    await session.execute(
                        select(Campaign.name).where(Campaign.organization_id == ORG_ID)
                    )
                )
                .scalars()
                .all()
            )
            for (
                name,
                channel,
                status,
                budget,
                spent,
                revenue,
                pipeline,
                leads,
                conversions,
                goal,
                aud_slug,
                start,
                end,
                owner,
            ) in CAMPAIGNS:
                if name in existing_names:
                    campaigns_skipped += 1
                    continue
                await services.create_campaign(
                    session,
                    write_ctx,
                    CampaignIn(
                        name=name,
                        channel=channel,
                        status=status,
                        currency=CURRENCY,
                        budget_minor=lakh(budget),
                        spent_minor=lakh(spent),
                        revenue_minor=lakh(revenue),
                        pipeline_minor=lakh(pipeline),
                        leads=leads,
                        conversions=conversions,
                        goal=goal,
                        audience_id=slug_to_id[aud_slug],
                        start_date=date.fromisoformat(start),
                        end_date=date.fromisoformat(end) if end else None,
                        owner=owner,
                    ),
                )
                campaigns_created += 1

        async with session.begin():
            for position, (label, revenue, spend) in enumerate(REVENUE_SERIES):
                await services.upsert_revenue_point(
                    session,
                    write_ctx,
                    position=position,
                    label=label,
                    revenue_minor=lakh(revenue),
                    spend_minor=lakh(spend),
                    currency=CURRENCY,
                )

    await engine.dispose()
    print(
        json.dumps(
            {
                "audiences_created": audiences_created,
                "audiences_skipped": audiences_skipped,
                "campaigns_created": campaigns_created,
                "campaigns_skipped": campaigns_skipped,
                "revenue_points": len(REVENUE_SERIES),
            }
        )
    )


asyncio.run(main())
