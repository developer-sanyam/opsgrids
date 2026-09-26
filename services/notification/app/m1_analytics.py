"""Messaging analytics and cost estimates (slice M1): reads, the rate-card write, and assembly.

**Where the numbers come from.** Everything is computed on demand from tables this service
already owns — `messages` (outbound, with the state ladder the receipt fold maintains),
`deliveries` (the append-only attempt ledger, read only for the *reason* a dispatch failed),
`inbound_messages`, `conversations`, `provider_templates` (Meta's category) and `rules`. Nothing is
stored or cached, so nothing can drift from the source of truth.

**One grouped statement per metric.** Each metric is a single `GROUP BY` with the organization
filter and the date bounds in the `WHERE`; no metric loops over rows and asks a question per row.
Every statement filters on `organization_id` explicitly — row-level security is the net under it,
not a licence to skip the predicate.

**Definitions** (also on the schemas, and repeated in the API doc):

  * A message is counted in the bucket of its `created_at`, as a **UTC** calendar day.
  * `sent` = current state `sent`, `delivered` or `read` (handed to WhatsApp, not failed);
    `delivered` = `delivered` or `read`; `read` = `read`; `failed` = `failed`. So
    `sent >= delivered >= read`, and `sent + failed` is everything that left the queue.
  * Category is the *template's* Meta category; a message with no provider template is a
    free-text reply inside the service window and is `service`.
  * Cost is estimated on the `sent` set, per category, `messages x rate / 1000`, half-up.
"""

from __future__ import annotations

import importlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final

from sqlalchemy import Date, case, cast, exists, func, literal_column, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import m1_cost
from app.m1_cost import CATEGORIES, DateWindow
from app.m1_models import PLACEHOLDER_LABEL, PLATFORM_ORG_ID, MessagingRateCard
from app.m1_schemas import (
    ActivityItem,
    CategoryCount,
    ChannelCost,
    ChannelStatus,
    Cost,
    CostCategory,
    FailureReason,
    HubCosts,
    HubOverview,
    Money,
    RateCardOut,
    RateCardRef,
    RateOut,
    SeriesPoint,
    StatusCount,
    WhatsAppOverview,
    WhatsAppTotals,
)
from app.models import (
    SCHEMA,
    AuditLog,
    Conversation,
    EmailConnection,
    InboundMessage,
    Message,
    ProviderAccount,
    ProviderTemplate,
    Rule,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.logging import get_logger

log = get_logger("notification.m1_analytics")

PLATFORM_ORG: Final = uuid.UUID(PLATFORM_ORG_ID)

#: The states a message is "sent" under: handed to WhatsApp and not failed. The billing basis too.
SENT_STATES: Final = ("sent", "delivered", "read")
DELIVERED_STATES: Final = ("delivered", "read")
QUEUED_STATES: Final = ("pending", "sending")

#: How many failure reasons and recent-activity rows one response carries.
MAX_FAILURE_REASONS: Final = 10
MAX_ACTIVITY: Final = 10

#: Most price points one organization can accumulate before the read stops looking further back.
_RATE_ROWS_CAP: Final = 400


# --------------------------------------------------------------------------- failure labels

#: Meta's documented WhatsApp Cloud API error codes, in plain words. Best effort: a code this
#: table does not know is shown as "WhatsApp error <code>" rather than dropped.
FAILURE_LABELS: Final[dict[str, str]] = {
    "100": "Invalid parameter",
    "190": "Access token expired or invalid",
    "368": "Temporarily blocked for policy violations",
    "130429": "Rate limit hit",
    "131000": "Something went wrong at WhatsApp",
    "131008": "A required parameter is missing",
    "131009": "A parameter value is not valid",
    "131026": "Message undeliverable",
    "131030": "Recipient is not in the allowed list",
    "131031": "Business account locked",
    "131042": "Business payment issue",
    "131047": "More than 24 hours since the customer last replied",
    "131048": "Spam rate limit hit",
    "131049": "Meta limited delivery to keep the ecosystem healthy",
    "131050": "Customer opted out of marketing messages",
    "131051": "Unsupported message type",
    "131052": "Media download error",
    "131053": "Media upload error",
    "131056": "Too many messages to one customer too fast",
    "132000": "Template parameter count does not match",
    "132001": "Template does not exist",
    "132005": "Translated text is too long",
    "132007": "Template content violates a WhatsApp policy",
    "132012": "Template parameter format does not match",
    "132015": "Template is paused",
    "132016": "Template is disabled",
    "133010": "Phone number is not registered",
    "dispatch_failed": "Could not be handed to WhatsApp",
    "http_400": "WhatsApp refused the request (HTTP 400)",
    "http_401": "WhatsApp refused the credential (HTTP 401)",
    "http_403": "WhatsApp refused the credential (HTTP 403)",
    "http_404": "WhatsApp could not find the sender or template (HTTP 404)",
    "http_429": "Rate limited by WhatsApp (HTTP 429)",
}


def failure_label(code: str) -> str:
    """A plain-language label for a failure code; never empty."""
    known = FAILURE_LABELS.get(code)
    if known is not None:
        return known
    if code.startswith("http_5"):
        return f"WhatsApp was unavailable (HTTP {code[5:]})"
    if code.startswith("http_"):
        return f"WhatsApp refused the request (HTTP {code[5:]})"
    return f"WhatsApp error {code}"


# --------------------------------------------------------------------------- reads


@dataclass(frozen=True, slots=True)
class DayRow:
    day: date
    sent: int
    delivered: int
    read: int
    failed: int
    queued: int
    suppressed: int


def _outbound_in_window(org: uuid.UUID, window: DateWindow) -> list[Any]:
    """The predicate every outbound-message metric shares: this org, WhatsApp, outbound, and
    created inside the window. Kept as one list so no metric can forget a clause."""
    return [
        Message.organization_id == org,
        Message.channel == "whatsapp",
        Message.direction == "outbound",
        Message.deleted_at.is_(None),
        Message.created_at >= window.start,
        Message.created_at < window.end,
    ]


async def outbound_series(
    session: AsyncSession, org: uuid.UUID, window: DateWindow
) -> list[DayRow]:
    """Per UTC day: sent / delivered / read / failed / queued / suppressed. One grouped query."""
    day = cast(func.timezone(literal_column("'UTC'"), Message.created_at), Date).label("day")
    stmt = (
        select(
            day,
            func.count().filter(Message.state.in_(SENT_STATES)).label("sent"),
            func.count().filter(Message.state.in_(DELIVERED_STATES)).label("delivered"),
            func.count().filter(Message.state == "read").label("read"),
            func.count().filter(Message.state == "failed").label("failed"),
            func.count().filter(Message.state.in_(QUEUED_STATES)).label("queued"),
            func.count().filter(Message.state == "suppressed").label("suppressed"),
        )
        .where(*_outbound_in_window(org, window))
        .group_by(day)
        .order_by(day)
    )
    rows = (await session.execute(stmt)).all()
    return [
        DayRow(r.day, r.sent, r.delivered, r.read, r.failed, r.queued, r.suppressed) for r in rows
    ]


def _category_expr() -> Any:
    """The Meta category a message is billed under.

    The template's own category when it is one of the three Meta prices; `service` when there is
    no provider template at all (a free-text reply inside the 24-hour window); and, for a message
    whose template row has since gone, `marketing` if the staging bucket said so, else `utility`.
    """
    return case(
        (
            ProviderTemplate.category.in_(("marketing", "utility", "authentication")),
            ProviderTemplate.category,
        ),
        (Message.provider_template_code.is_(None), "service"),
        (Message.category == "marketing", "marketing"),
        else_="utility",
    )


async def sent_by_category(
    session: AsyncSession, org: uuid.UUID, window: DateWindow
) -> dict[str, int]:
    """Messages in the `sent` set per billing category. One grouped query, one left join.

    The join is on the template's unique `(organization_id, code, channel)`, so it can match at
    most one row and never fans a message out into two.
    """
    category = _category_expr().label("category")
    stmt = (
        select(category, func.count().label("messages"))
        .select_from(Message)
        .outerjoin(
            ProviderTemplate,
            (ProviderTemplate.organization_id == Message.organization_id)
            & (ProviderTemplate.channel == "whatsapp")
            & (ProviderTemplate.code == Message.provider_template_code)
            & ProviderTemplate.deleted_at.is_(None),
        )
        .where(*_outbound_in_window(org, window), Message.state.in_(SENT_STATES))
        .group_by(category)
    )
    counts = dict.fromkeys(CATEGORIES, 0)
    for row in (await session.execute(stmt)).all():
        counts[row.category] = row.messages
    return counts


#: Meta's own error code out of the JSON body a refused dispatch recorded, else the HTTP status
#: the transport prefixed it with (`"400: {...}"`), else nothing.
_BODY_CODE = r"""'"code"\s*:\s*(\d+)'"""
_HTTP_STATUS = r"""'^(\d{3}):'"""


async def failure_reasons(
    session: AsyncSession, org: uuid.UUID, window: DateWindow
) -> list[tuple[str, int]]:
    """Failed messages grouped by why, most frequent first. One grouped query.

    The reason is the receipt's `provider_error_code` when Meta reported the failure after
    accepting the message; otherwise it is read from the message's *last failed attempt* in the
    append-only `deliveries` ledger (a lateral join, still one statement): Meta's code from the
    refusal body if present, else `http_<status>`, else `dispatch_failed`.
    """
    stmt = text(
        f"""
        SELECT COALESCE(m.provider_error_code,
                        substring(d.detail from {_BODY_CODE}),
                        'http_' || substring(d.detail from {_HTTP_STATUS}),
                        'dispatch_failed') AS code,
               count(*) AS n
          FROM {SCHEMA}.messages m
          LEFT JOIN LATERAL (
                SELECT dl.detail
                  FROM {SCHEMA}.deliveries dl
                 WHERE dl.organization_id = m.organization_id
                   AND dl.message_id = m.id
                   AND dl.outcome = 'failed'
                 ORDER BY dl.attempt_no DESC
                 LIMIT 1) d ON true
         WHERE m.organization_id = :org
           AND m.channel = 'whatsapp'
           AND m.direction = 'outbound'
           AND m.deleted_at IS NULL
           AND m.state = 'failed'
           AND m.created_at >= :start
           AND m.created_at < :end
         GROUP BY 1
         ORDER BY n DESC, code ASC
         LIMIT :limit
        """  # noqa: S608 - only module constants are interpolated; every value is a bind parameter
    ).bindparams(org=org, start=window.start, end=window.end, limit=MAX_FAILURE_REASONS)
    return [(row.code, row.n) for row in (await session.execute(stmt)).all()]


async def reply_counts(
    session: AsyncSession, org: uuid.UUID, window: DateWindow
) -> tuple[int, int]:
    """`(replies, inbound)` in the window. One grouped query.

    `inbound` is every customer message that arrived in the window. `replies` is the subset that
    landed on a conversation which had an outbound message (that actually left the queue) in the
    same window. The link is `messages.conversation_id`, which only an inbox reply carries — a
    template send has none — so this is a lower bound for template-driven sends, stated as such.
    """
    answered = exists().where(
        Message.organization_id == InboundMessage.organization_id,
        Message.conversation_id == InboundMessage.conversation_id,
        Message.direction == "outbound",
        Message.state.in_(SENT_STATES),
        Message.deleted_at.is_(None),
        Message.created_at >= window.start,
        Message.created_at < window.end,
    )
    stmt = select(
        func.count().label("inbound"),
        func.count().filter(answered).label("replies"),
    ).where(
        InboundMessage.organization_id == org,
        InboundMessage.deleted_at.is_(None),
        InboundMessage.occurred_at >= window.start,
        InboundMessage.occurred_at < window.end,
    )
    row = (await session.execute(stmt)).one()
    return int(row.replies), int(row.inbound)


async def totals(session: AsyncSession, org: uuid.UUID) -> WhatsAppTotals:
    """Point-in-time counts, not range-bound: what exists now. One statement, five scalar
    subqueries, each with its own organization filter."""

    def count_of(model: Any, *where: Any) -> Any:
        return (
            select(func.count())
            .select_from(model)
            .where(model.organization_id == org, model.deleted_at.is_(None), *where)
            .scalar_subquery()
        )

    stmt = select(
        count_of(Rule, Rule.channel == "whatsapp", Rule.active.is_(True)).label("automations"),
        count_of(
            ProviderTemplate,
            ProviderTemplate.channel == "whatsapp",
            ProviderTemplate.status == "approved",
        ).label("approved"),
        count_of(
            ProviderTemplate,
            ProviderTemplate.channel == "whatsapp",
            ProviderTemplate.status == "pending",
        ).label("pending"),
        count_of(
            Conversation, Conversation.channel == "whatsapp", Conversation.status == "open"
        ).label("open"),
        count_of(
            Conversation,
            Conversation.channel == "whatsapp",
            Conversation.status == "open",
            Conversation.assignee_membership_id.is_(None),
        ).label("unassigned"),
    )
    row = (await session.execute(stmt)).one()
    return WhatsAppTotals(
        active_automations=row.automations,
        approved_templates=row.approved,
        pending_templates=row.pending,
        active_conversations=row.open,
        unassigned_conversations=row.unassigned,
    )


async def recent_activity(
    session: AsyncSession, org: uuid.UUID, window: DateWindow
) -> list[ActivityItem]:
    """The newest things that happened in the window: template decisions, new conversations and
    new automations. One statement (three bounded branches under one `UNION ALL`). The text of a
    conversation is its **masked** number, never the real one."""
    stmt = text(
        f"""
        SELECT kind, title, detail, at FROM (
          (SELECT 'template_' || status AS kind,
                  CASE status WHEN 'approved' THEN 'Template approved'
                              WHEN 'rejected' THEN 'Template rejected'
                              ELSE 'Template ' || status END AS title,
                  provider_name || ' (' || language || ')' AS detail,
                  COALESCE(status_checked_at, updated_at) AS at
             FROM {SCHEMA}.provider_templates
            WHERE organization_id = :org AND channel = 'whatsapp' AND deleted_at IS NULL
              AND status IN ('approved', 'rejected', 'paused', 'disabled')
              AND COALESCE(status_checked_at, updated_at) >= :start
              AND COALESCE(status_checked_at, updated_at) < :end
            ORDER BY at DESC LIMIT :limit)
          UNION ALL
          (SELECT 'conversation_started', 'New conversation', wa_address_masked, created_at
             FROM {SCHEMA}.conversations
            WHERE organization_id = :org AND channel = 'whatsapp' AND deleted_at IS NULL
              AND created_at >= :start AND created_at < :end
            ORDER BY created_at DESC LIMIT :limit)
          UNION ALL
          (SELECT 'automation_created', 'Automation created', code, created_at
             FROM {SCHEMA}.rules
            WHERE organization_id = :org AND channel = 'whatsapp' AND deleted_at IS NULL
              AND created_at >= :start AND created_at < :end
            ORDER BY created_at DESC LIMIT :limit)
        ) recent
        ORDER BY at DESC, kind ASC
        LIMIT :limit
        """  # noqa: S608 - only module constants are interpolated; every value is a bind parameter
    ).bindparams(org=org, start=window.start, end=window.end, limit=MAX_ACTIVITY)
    return [
        ActivityItem(kind=r.kind, title=r.title, text=r.detail, at=r.at)
        for r in (await session.execute(stmt)).all()
    ]


async def channel_statuses(session: AsyncSession, org: uuid.UUID) -> list[ChannelStatus]:
    """Which channels the organization has connected. WhatsApp and email are real connections;
    SMS and push do not exist on this service and are reported as not connected."""
    whatsapp = (
        await session.execute(
            select(ProviderAccount.status, ProviderAccount.display_number).where(
                ProviderAccount.organization_id == org,
                ProviderAccount.channel == "whatsapp",
                ProviderAccount.deleted_at.is_(None),
            )
        )
    ).first()
    email = (
        await session.execute(
            select(EmailConnection.status, EmailConnection.kind).where(
                EmailConnection.organization_id == org,
                EmailConnection.deleted_at.is_(None),
            )
        )
    ).first()
    return [
        ChannelStatus(
            key="whatsapp",
            connected=whatsapp is not None and whatsapp.status == "connected",
            detail=(whatsapp.display_number or None) if whatsapp is not None else None,
        ),
        ChannelStatus(
            key="email",
            connected=email is not None and email.status == "connected",
            detail=email.kind if email is not None else None,
        ),
        ChannelStatus(key="sms", connected=False, detail=None),
        ChannelStatus(key="push", connected=False, detail=None),
    ]


# --------------------------------------------------------------------------- rate card


async def _rate_rows(session: AsyncSession, org: uuid.UUID) -> list[MessagingRateCard]:
    """Every WhatsApp price point that could apply to this organization: its own and the
    platform's. Explicit `IN (org, platform)` — the platform rows are readable through their own
    RLS policy, but the filter says what is wanted rather than leaning on the net."""
    stmt = (
        select(MessagingRateCard)
        .where(
            MessagingRateCard.organization_id.in_([org, PLATFORM_ORG]),
            MessagingRateCard.channel == "whatsapp",
            MessagingRateCard.deleted_at.is_(None),
        )
        .order_by(MessagingRateCard.effective_from.desc())
        .limit(_RATE_ROWS_CAP)
    )
    return list((await session.execute(stmt)).scalars().all())


def pick_rates(
    rows: list[MessagingRateCard], org: uuid.UUID, as_of: date
) -> dict[str, MessagingRateCard]:
    """The one rate per category in force on `as_of`.

    Precedence, per category:

      1. the organization's own row with the latest `effective_from` on or before `as_of`;
      2. else the platform default with the latest `effective_from` on or before `as_of`;
      3. else, for a range that predates every row, the *earliest* row there is (the
         organization's before the platform's) — an estimate over old history uses the oldest
         known price rather than pricing it at zero.

    An organization override always beats the default once it is in force, whatever the two
    `effective_from` dates are relative to each other.
    """
    picked: dict[str, MessagingRateCard] = {}
    for category in CATEGORIES:
        mine = [r for r in rows if r.category == category and r.organization_id == org]
        base = [r for r in rows if r.category == category and r.organization_id != org]
        chosen: MessagingRateCard | None = None
        for pool in (mine, base):
            in_force = [r for r in pool if r.effective_from <= as_of]
            if in_force:
                chosen = max(in_force, key=lambda r: r.effective_from)
                break
        if chosen is None:
            for pool in (mine, base):
                if pool:
                    chosen = min(pool, key=lambda r: r.effective_from)
                    break
        if chosen is not None:
            picked[category] = chosen
    return picked


def _rate_card_ref(picked: dict[str, MessagingRateCard], org: uuid.UUID) -> RateCardRef:
    own = [r.organization_id == org for r in picked.values()]
    if own and all(own):
        source = "organization"
    elif any(own):
        source = "mixed"
    else:
        source = "platform_default"
    placeholder = any(r.is_placeholder for r in picked.values())
    version = next((r.label for r in picked.values() if r.is_placeholder), "organization")
    return RateCardRef(source=source, placeholder=placeholder, version=version)


_PLACEHOLDER_NOTE: Final = (
    f"These rates are PLACEHOLDERS ({PLACEHOLDER_LABEL}), not Meta's published prices for your "
    "country. Enter your real per-1000 rates to make the cost estimate meaningful."
)
_OVERRIDE_NOTE: Final = "Rates entered by your organization. Costs are estimates, not invoices."


async def read_rate_card(session: AsyncSession, org: uuid.UUID, *, as_of: date) -> RateCardOut:
    picked = pick_rates(await _rate_rows(session, org), org, as_of)
    ref = _rate_card_ref(picked, org)
    return RateCardOut(
        channel="whatsapp",
        placeholder=ref.placeholder,
        version=ref.version,
        note=_PLACEHOLDER_NOTE if ref.placeholder else _OVERRIDE_NOTE,
        rates=[
            RateOut(
                category=c,
                amount_minor_per_1000_messages=picked[c].amount_minor_per_1000_messages,
                currency=picked[c].currency,
                effective_from=picked[c].effective_from,
                source="organization" if picked[c].organization_id == org else "platform_default",
                placeholder=picked[c].is_placeholder,
            )
            for c in CATEGORIES
            if c in picked
        ],
    )


async def override_rate_card(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    rates: dict[str, tuple[int, str]],
    effective_from: date,
    request: Request | None = None,
) -> None:
    """Write the organization's own rates for `effective_from`, one row per category, in the
    caller's transaction, with an audit row. Idempotent on `(org, channel, category,
    effective_from)`: sending the same body twice leaves one set of rows, and a corrected body for
    the same day replaces that day's rates. A later day adds a new price point; nothing earlier
    is edited or deleted."""
    org = ctx.organization_id
    for category, (amount, currency) in rates.items():
        stmt = pg_insert(MessagingRateCard).values(
            organization_id=org,
            channel="whatsapp",
            category=category,
            amount_minor_per_1000_messages=amount,
            currency=currency,
            effective_from=effective_from,
            label="organization",
            is_placeholder=False,
            created_by=str(ctx.principal.sub),
            updated_by=str(ctx.principal.sub),
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["organization_id", "channel", "category", "effective_from"],
                set_={
                    "amount_minor_per_1000_messages": amount,
                    "currency": currency,
                    "label": "organization",
                    "is_placeholder": False,
                    "deleted_at": None,
                    "updated_by": str(ctx.principal.sub),
                    "updated_at": func.now(),
                },
            )
        )
    record_audit(
        session,
        AuditLog,
        organization_id=org,
        actor_id=ctx.principal.sub,
        action="messaging_rate_card.overridden",
        entity="messaging_rate_card",
        after={
            "effective_from": effective_from.isoformat(),
            "rates": {
                c: {"amount_minor_per_1000_messages": a, "currency": cur}
                for c, (a, cur) in rates.items()
            },
        },
        request=request,
    )
    await session.flush()


# --------------------------------------------------------------------------- assembly


def _money(amount_minor: int, currency: str) -> Money:
    return Money(amount_minor=amount_minor, currency=currency)


def build_cost(
    by_category: dict[str, int], picked: dict[str, MessagingRateCard], org: uuid.UUID
) -> Cost:
    """Price each category once (half-up), then sum the already-rounded amounts.

    Summing rounded per-category amounts (rather than rounding a grand total) is the rule the
    contract states, and it means the rows shown always add up to the total shown. A category
    with no rate row is priced at zero in the currency of the others.
    """
    currency = next(iter(picked.values())).currency if picked else "INR"
    lines: list[CostCategory] = []
    total = 0
    for category in CATEGORIES:
        messages = by_category.get(category, 0)
        rate = picked.get(category)
        amount = (
            m1_cost.estimate_minor(messages, rate.amount_minor_per_1000_messages) if rate else 0
        )
        total += amount
        lines.append(
            CostCategory(
                category=category,
                messages=messages,
                amount=_money(amount, currency),
            )
        )
    return Cost(
        total=_money(total, currency),
        by_category=lines,
        rate_card=_rate_card_ref(picked, org),
    )


def _series_out(rows: list[DayRow], window: DateWindow) -> list[SeriesPoint]:
    """A dense series: every day in the window, zeros where nothing was sent."""
    by_day = {r.day: r for r in rows}
    out: list[SeriesPoint] = []
    for day in window.days():
        r = by_day.get(day)
        out.append(
            SeriesPoint(
                date=day.isoformat(),
                sent=r.sent if r else 0,
                delivered=r.delivered if r else 0,
                read=r.read if r else 0,
                failed=r.failed if r else 0,
            )
        )
    return out


async def whatsapp_overview(
    session: AsyncSession, org: uuid.UUID, window: DateWindow
) -> WhatsAppOverview:
    """The WhatsApp dashboard for `window`. Nine grouped statements, none inside a loop."""
    rows = await outbound_series(session, org, window)
    sent = sum(r.sent for r in rows)
    delivered = sum(r.delivered for r in rows)
    read = sum(r.read for r in rows)
    failed = sum(r.failed for r in rows)
    queued = sum(r.queued for r in rows)
    suppressed = sum(r.suppressed for r in rows)

    categories = await sent_by_category(session, org, window)
    replies, inbound = await reply_counts(session, org, window)
    reasons = await failure_reasons(session, org, window)
    activity = await recent_activity(session, org, window)
    counts = await totals(session, org)

    as_of = min(window.date_to, datetime.now(UTC).date())
    picked = pick_rates(await _rate_rows(session, org), org, as_of)

    return WhatsAppOverview(
        sent=sent,
        delivered=delivered,
        read=read,
        failed=failed,
        replies=replies,
        inbound_messages=inbound,
        queued=queued,
        suppressed=suppressed,
        series=_series_out(rows, window),
        by_category=[CategoryCount(category=c, messages=categories.get(c, 0)) for c in CATEGORIES],
        status_breakdown=[
            StatusCount(status="read", count=read),
            StatusCount(status="delivered", count=delivered - read),
            StatusCount(status="sent", count=sent - delivered),
            StatusCount(status="failed", count=failed),
        ],
        failures_by_reason=[
            FailureReason(code=code, label=failure_label(code), count=n) for code, n in reasons
        ],
        recent_activity=activity,
        totals=counts,
        cost=build_cost(categories, picked, org),
    )


async def _email_overview(
    session: AsyncSession, org: uuid.UUID, window: DateWindow
) -> dict[str, Any] | None:
    """E1's email overview, or `None` when that module is not in this deployment.

    Imported **lazily, inside the function**: the module is another slice's, and a hub that
    could not even import because of it would take the WhatsApp half down too. Only the import
    is guarded — a failure *inside* the call is a real error and propagates, so it is never
    hidden behind a quiet `null`.
    """
    try:
        # `import_module` rather than `from app import e1_analytics`: the module belongs to a
        # parallel slice and may not exist here, which the type checker cannot know either.
        overview = importlib.import_module("app.e1_analytics").email_overview
    except (ImportError, AttributeError):
        log.warning("notification.analytics.email_overview_unavailable")
        return None
    result = await overview(session, org, window.date_from, window.date_to)
    if hasattr(result, "model_dump"):
        dumped: dict[str, Any] = result.model_dump(mode="json")
        return dumped
    return dict(result)


async def hub_overview(session: AsyncSession, org: uuid.UUID, window: DateWindow) -> HubOverview:
    """The Communication hub: both channels' KPIs and one cost summary.

    Email numbers come from E1's `email_overview`, not recomputed here. Email is sent through
    the customer's own provider account, so it carries no cost estimate on this platform: the
    hub's cost is WhatsApp's, and the email line is present at zero so the breakdown lists both
    channels.
    """
    whatsapp = await whatsapp_overview(session, org, window)
    channels = await channel_statuses(session, org)
    email = await _email_overview(session, org, window)
    currency = whatsapp.cost.total.currency
    return HubOverview(
        channels=channels,
        whatsapp=whatsapp,
        email=email,
        costs=HubCosts(
            total=whatsapp.cost.total,
            by_channel=[
                ChannelCost(channel="whatsapp", amount=whatsapp.cost.total),
                ChannelCost(channel="email", amount=_money(0, currency)),
            ],
            rate_card=whatsapp.cost.rate_card,
        ),
    )


__all__ = [
    "FAILURE_LABELS",
    "channel_statuses",
    "failure_label",
    "hub_overview",
    "override_rate_card",
    "pick_rates",
    "read_rate_card",
    "whatsapp_overview",
]
