"""Email analytics reads (slice E1): one campaign's engagement, and the organization-wide overview.

Every query filters on `organization_id`; the campaign read also goes through
`selectors.campaigns_stmt`, so the caller's scope (own / team / organization) is applied to which
campaign they may see. Each function issues a fixed number of queries whatever the data size.

**What the numbers mean.**

- `delivered` is *accepted by the provider*: the recipient row reached `sent`. Nothing here sees
  the receiving mailbox, so it is not proof of inbox placement. `bounced` counts recipients the
  provider refused at send time. No asynchronous bounce or complaint feedback is processed by
  this slice, so `complaints` is 0 and means "not measured", not "none".
- `opened` and `clicked` are **distinct recipients** with at least one *human* event. Machine
  events (scanners, prefetchers, Apple Mail Privacy Protection) are stored but excluded from
  every figure here. `open_rate` and `click_rate` are **fractions between 0 and 1**
  (`opened / delivered`, `clicked / delivered`, 0.0 when nothing was delivered), rounded to four
  places, not percentages.
- `by_device` counts distinct recipients per device class among human opens; a recipient who opened
  on two devices appears under both. `by_link` counts distinct recipients per destination among
  human clicks. `series` counts distinct recipients per UTC day.
- When a campaign's `tracking` flags are off, zero opens means "not measured", not "nobody
  opened": the response carries the flags so the UI can say so.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Date, Select, and_, cast, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.e1_models import EmailEvent, EmailLink
from app.models import (
    Campaign,
    CampaignRecipient,
    ConsentEvent,
    Contact,
    EmailConnection,
    Rule,
    Suppression,
    Template,
)
from platform_core.auth import ScopeContext

#: Longest overview window. A wider one is clamped to its most recent days.
MAX_RANGE_DAYS = 366
#: Minimum delivered messages before a campaign's open rate counts as "high" in the activity feed,
#: and the rate that counts.
HIGH_OPEN_MIN_DELIVERED = 20
HIGH_OPEN_RATE = 0.4
_REFUSED = "The provider refused this message"
_SOURCE_UNSUBSCRIBE_LINK = "unsubscribe_link"


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _day(column: Any) -> Any:
    return cast(func.timezone("UTC", column), Date)


async def campaign_analytics(
    session: AsyncSession, ctx: ScopeContext, campaign_id: uuid.UUID
) -> dict[str, Any] | None:
    """`GET /engage/campaigns/{id}/analytics`, or None when the caller has no such campaign."""
    org = ctx.organization_id
    campaign = (
        await session.execute(selectors.campaigns_stmt(ctx).where(Campaign.id == campaign_id))
    ).scalar_one_or_none()
    if campaign is None:
        return None

    total, delivered, bounced = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(CampaignRecipient.status == "sent"),
                func.count().filter(
                    and_(CampaignRecipient.status == "failed", CampaignRecipient.error == _REFUSED)
                ),
            ).where(
                CampaignRecipient.organization_id == org,
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.deleted_at.is_(None),
            )
        )
    ).one()

    since = campaign.started_at or campaign.created_at
    unsubscribed = (
        await session.execute(
            select(func.count(func.distinct(CampaignRecipient.id)))
            .join(
                ConsentEvent,
                and_(
                    ConsentEvent.organization_id == CampaignRecipient.organization_id,
                    ConsentEvent.subject_index == CampaignRecipient.email_index,
                ),
            )
            .where(
                CampaignRecipient.organization_id == org,
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.erased_at.is_(None),
                ConsentEvent.channel == "email",
                ConsentEvent.purpose == "marketing",
                ConsentEvent.status == "opted_out",
                ConsentEvent.source == _SOURCE_UNSUBSCRIBE_LINK,
                ConsentEvent.occurred_at >= since,
            )
        )
    ).scalar_one()

    human = and_(
        EmailEvent.organization_id == org,
        EmailEvent.campaign_id == campaign.id,
        EmailEvent.is_machine.is_(False),
    )
    kind_counts = dict(
        (
            await session.execute(
                select(EmailEvent.kind, func.count(func.distinct(EmailEvent.recipient_id)))
                .where(human)
                .group_by(EmailEvent.kind)
            )
        ).all()
    )
    opened = int(kind_counts.get("open", 0))
    clicked = int(kind_counts.get("click", 0))

    devices = (
        await session.execute(
            select(
                EmailEvent.user_agent_class,
                func.count(func.distinct(EmailEvent.recipient_id)).label("n"),
            )
            .where(human, EmailEvent.kind == "open")
            .group_by(EmailEvent.user_agent_class)
            .order_by(func.count(func.distinct(EmailEvent.recipient_id)).desc())
        )
    ).all()
    links = (
        await session.execute(
            select(
                EmailLink.url,
                func.count(func.distinct(EmailEvent.recipient_id)).label("n"),
            )
            .join(
                EmailLink,
                and_(
                    EmailLink.id == EmailEvent.link_id,
                    EmailLink.organization_id == EmailEvent.organization_id,
                ),
            )
            .where(human, EmailEvent.kind == "click")
            .group_by(EmailLink.url)
            .order_by(func.count(func.distinct(EmailEvent.recipient_id)).desc(), EmailLink.url)
            .limit(50)
        )
    ).all()
    day = _day(EmailEvent.occurred_at)
    by_day: dict[date, dict[str, int]] = {}
    for d, kind, n in (
        await session.execute(
            select(day, EmailEvent.kind, func.count(func.distinct(EmailEvent.recipient_id)))
            .where(human)
            .group_by(day, EmailEvent.kind)
            .order_by(day)
        )
    ).all():
        by_day.setdefault(d, {})[kind] = int(n)

    return {
        "recipients": int(total),
        "delivered": int(delivered),
        "opened": opened,
        "clicked": clicked,
        "unsubscribed": int(unsubscribed),
        "bounced": int(bounced),
        "complaints": 0,
        "open_rate": _rate(opened, int(delivered)),
        "click_rate": _rate(clicked, int(delivered)),
        "by_device": [{"device": device, "opens": int(n)} for device, n in devices],
        "by_link": [{"url": url, "clicks": int(n)} for url, n in links],
        "series": [
            {"date": d.isoformat(), "opens": v.get("open", 0), "clicks": v.get("click", 0)}
            for d, v in sorted(by_day.items())
        ],
        "tracking": {"opens": bool(campaign.track_opens), "clicks": bool(campaign.track_clicks)},
    }


def _window(date_from: date | datetime, date_to: date | datetime) -> tuple[date, date]:
    start = date_from.date() if isinstance(date_from, datetime) else date_from
    end = date_to.date() if isinstance(date_to, datetime) else date_to
    if end < start:
        start, end = end, start
    if (end - start).days >= MAX_RANGE_DAYS:
        start = end - timedelta(days=MAX_RANGE_DAYS - 1)
    return start, end


async def email_overview(
    session: AsyncSession,
    organization_id: uuid.UUID,
    date_from: date | datetime,
    date_to: date | datetime,
) -> dict[str, Any]:
    """The email overview for `[date_from, date_to]` (inclusive UTC calendar dates), as a plain
    dict in the shape of `GET /engage/analytics/email/overview`. The hub route embeds it as its
    `email` block. Runs in the caller's tenant-bound transaction.

    Counts campaign email only, by the day the message was attempted: `sent` is every attempt,
    `delivered` the ones the provider accepted, `failed` the ones it refused or that could not
    be sent. `opened` / `clicked` are distinct recipients with a human event in the window.
    `automation_executed` never appears in `recent_activity`: this slice has no automation run
    data to report.
    """
    start, end = _window(date_from, date_to)
    lo = datetime.combine(start, time.min, tzinfo=UTC)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)

    attempted_at = func.coalesce(CampaignRecipient.sent_at, CampaignRecipient.updated_at)
    sent_day = _day(attempted_at)
    per_day: dict[date, dict[str, int]] = {}
    for d, status, n in (
        await session.execute(
            select(sent_day, CampaignRecipient.status, func.count())
            .where(
                CampaignRecipient.organization_id == organization_id,
                CampaignRecipient.deleted_at.is_(None),
                CampaignRecipient.status.in_(("sent", "failed")),
                attempted_at >= lo,
                attempted_at < hi,
            )
            .group_by(sent_day, CampaignRecipient.status)
        )
    ).all():
        per_day.setdefault(d, {})[status] = int(n)

    event_day = _day(EmailEvent.occurred_at)
    engaged_filter = (
        EmailEvent.organization_id == organization_id,
        EmailEvent.is_machine.is_(False),
        EmailEvent.occurred_at >= lo,
        EmailEvent.occurred_at < hi,
    )
    for d, kind, n in (
        await session.execute(
            select(event_day, EmailEvent.kind, func.count(func.distinct(EmailEvent.recipient_id)))
            .where(*engaged_filter)
            .group_by(event_day, EmailEvent.kind)
        )
    ).all():
        per_day.setdefault(d, {})[kind] = int(n)
    overall = dict(
        (
            await session.execute(
                select(EmailEvent.kind, func.count(func.distinct(EmailEvent.recipient_id)))
                .where(*engaged_filter)
                .group_by(EmailEvent.kind)
            )
        ).all()
    )

    series: list[dict[str, Any]] = []
    sent = delivered = failed = 0
    for offset in range((end - start).days + 1):
        d = start + timedelta(days=offset)
        row = per_day.get(d, {})
        ok, bad = row.get("sent", 0), row.get("failed", 0)
        sent += ok + bad
        delivered += ok
        failed += bad
        series.append(
            {
                "date": d.isoformat(),
                "sent": ok + bad,
                "delivered": ok,
                "opened": row.get("open", 0),
                "clicked": row.get("click", 0),
                "failed": bad,
            }
        )

    return {
        "sent": sent,
        "delivered": delivered,
        "opened": int(overall.get("open", 0)),
        "clicked": int(overall.get("click", 0)),
        "failed": failed,
        "series": series,
        "recent_activity": await _recent_activity(session, organization_id, lo, hi),
        "totals": await _totals(session, organization_id),
    }


async def _recent_activity(
    session: AsyncSession, organization_id: uuid.UUID, lo: datetime, hi: datetime
) -> list[dict[str, Any]]:
    finished: Sequence[Campaign] = (
        (
            await session.execute(
                select(Campaign)
                .where(
                    Campaign.organization_id == organization_id,
                    Campaign.deleted_at.is_(None),
                    Campaign.status == "sent",
                    Campaign.completed_at >= lo,
                    Campaign.completed_at < hi,
                )
                .order_by(Campaign.completed_at.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    items: list[dict[str, Any]] = []
    for campaign in finished:
        assert campaign.completed_at is not None
        items.append(
            {
                "kind": "campaign_sent",
                "title": campaign.name,
                "text": f"Sent to {campaign.sent_count} recipients",
                "at": campaign.completed_at.isoformat(),
            }
        )
    if finished:
        opens = {
            campaign_id: (int(n), last)
            for campaign_id, n, last in (
                await session.execute(
                    select(
                        EmailEvent.campaign_id,
                        func.count(func.distinct(EmailEvent.recipient_id)),
                        func.max(EmailEvent.occurred_at),
                    )
                    .where(
                        EmailEvent.organization_id == organization_id,
                        EmailEvent.campaign_id.in_([c.id for c in finished]),
                        EmailEvent.kind == "open",
                        EmailEvent.is_machine.is_(False),
                    )
                    .group_by(EmailEvent.campaign_id)
                )
            ).all()
        }
        for campaign in finished:
            n, last = opens.get(campaign.id, (0, None))
            if (
                last is not None
                and campaign.sent_count >= HIGH_OPEN_MIN_DELIVERED
                and _rate(n, campaign.sent_count) >= HIGH_OPEN_RATE
            ):
                items.append(
                    {
                        "kind": "high_open_rate",
                        "title": campaign.name,
                        "text": f"{_rate(n, campaign.sent_count):.0%} of recipients opened it",
                        "at": last.isoformat(),
                    }
                )
    joined, latest = (
        await session.execute(
            select(func.count(), func.max(Contact.created_at)).where(
                Contact.organization_id == organization_id,
                Contact.deleted_at.is_(None),
                Contact.created_at >= lo,
                Contact.created_at < hi,
            )
        )
    ).one()
    if joined:
        items.append(
            {
                "kind": "new_subscriber",
                "title": "New subscribers",
                "text": f"{joined} new contacts joined your audience",
                "at": latest.isoformat(),
            }
        )
    items.sort(key=lambda item: item["at"], reverse=True)
    return items[:10]


def _subscribed_stmt(organization_id: uuid.UUID) -> Select[Any]:
    """Contacts a campaign may email right now: not suppressed, and the latest email/marketing
    consent event is an opt-in. The same predicate `selectors.sendable_contacts` reads with."""
    suppressed = exists().where(
        Suppression.organization_id == Contact.organization_id,
        Suppression.email_index == Contact.email_index,
        Suppression.deleted_at.is_(None),
    )
    return (
        select(func.count())
        .select_from(Contact)
        .where(
            Contact.organization_id == organization_id,
            Contact.deleted_at.is_(None),
            ~suppressed,
            selectors.latest_consent_is(
                "opted_in",
                organization_id=Contact.organization_id,
                channel="email",
                purpose="marketing",
                subject_index=Contact.email_index,
            ),
        )
    )


async def _totals(session: AsyncSession, organization_id: uuid.UUID) -> dict[str, int]:
    def count_of(model: Any, *where: Any) -> Any:
        return (
            select(func.count())
            .select_from(model)
            .where(model.organization_id == organization_id, model.deleted_at.is_(None), *where)
            .scalar_subquery()
        )

    subscribed = _subscribed_stmt(organization_id)
    row = (
        await session.execute(
            select(
                count_of(EmailConnection, EmailConnection.status == "connected"),
                count_of(Rule, Rule.active.is_(True), Rule.channel == "email"),
                count_of(Template, Template.channel == "email"),
                count_of(Contact),
                subscribed.scalar_subquery(),
            )
        )
    ).one()
    keys = (
        "connected_accounts",
        "active_automations",
        "templates",
        "contacts",
        "subscribed_contacts",
    )
    return {key: int(value) for key, value in zip(keys, row, strict=True)}
