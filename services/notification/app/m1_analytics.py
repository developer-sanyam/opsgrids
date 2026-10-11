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
  * Category is what Meta's `pricing` object on a delivery receipt said (`messages.
    pricing_category`); until a receipt says, the *template's* Meta category, and a message with
    no provider template is a free-text reply inside the service window and is `service`.
  * Cost is estimated on the billable part of the `sent` set (`billable` not false: Meta said it
    charges, or no receipt has said yet), per recipient market (longest calling-code prefix of
    `to_address`) and category, `messages x rate / 1000`, half-up once per market and category.
"""

from __future__ import annotations

import importlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final

from sqlalchemy import Date, case, cast, exists, func, literal_column, select, text, true
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
    CostSource,
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
from platform_core.db import set_tenant
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
_RATE_ROWS_CAP: Final = 2000


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

    **What Meta said**, when a status receipt carried a `pricing` object: its category folded onto
    the four priced ones (`m1_cost.META_CATEGORY_ALIASES`; anything else Meta reports is free,
    `service`). Otherwise an estimate: the template's own category when it is one of the three
    Meta prices; `service` when there is no provider template at all (a free-text reply inside
    the 24-hour window); and, for a message whose template row has since gone, `marketing` if the
    staging bucket said so, else `utility`.
    """
    return case(
        *[
            (Message.pricing_category == meta, priced)
            for meta, priced in m1_cost.META_CATEGORY_ALIASES.items()
        ],
        (Message.pricing_category.is_not(None), "service"),
        (
            ProviderTemplate.category.in_(("marketing", "utility", "authentication")),
            ProviderTemplate.category,
        ),
        (Message.provider_template_code.is_(None), "service"),
        (Message.category == "marketing", "marketing"),
        else_="utility",
    )


def _with_template(stmt: Any) -> Any:
    """Left-join the message's provider template. The join is on the template's unique
    `(organization_id, code, channel)`, so it matches at most one row and never fans a message
    out into two."""
    return stmt.select_from(Message).outerjoin(
        ProviderTemplate,
        (ProviderTemplate.organization_id == Message.organization_id)
        & (ProviderTemplate.channel == "whatsapp")
        & (ProviderTemplate.code == Message.provider_template_code)
        & ProviderTemplate.deleted_at.is_(None),
    )


#: The first four digits of the recipient number: enough for the longest market prefix (`m1_cost.
#: market_code`), and few enough that grouping by it stays a handful of rows per market.
def _number_prefix() -> Any:
    return func.left(func.regexp_replace(Message.to_address, "[^0-9]", "", "g"), 4)


#: A message Meta has not said is free. Null (no receipt priced it yet) is estimated as billable.
_BILLABLE = Message.billable.is_not(False)


@dataclass(frozen=True, slots=True)
class CostGroup:
    """Sent messages of one billing category to one number prefix."""

    category: str
    prefix: str
    messages: int
    billable: int
    priced_by_meta: int


async def sent_by_category(
    session: AsyncSession, org: uuid.UUID, window: DateWindow
) -> dict[str, int]:
    """Messages in the `sent` set per billing category."""
    counts = dict.fromkeys(CATEGORIES, 0)
    for group in await cost_groups(session, org, window):
        counts[group.category] += group.messages
    return counts


async def cost_groups(session: AsyncSession, org: uuid.UUID, window: DateWindow) -> list[CostGroup]:
    """The `sent` set grouped by billing category and number prefix, with how many of each are
    billable and how many Meta itself priced. One grouped query, one left join."""
    category = _category_expr().label("category")
    prefix = _number_prefix().label("prefix")
    stmt = (
        _with_template(
            select(
                category,
                prefix,
                func.count().label("messages"),
                func.count().filter(_BILLABLE).label("billable"),
                func.count(Message.pricing_category).label("priced_by_meta"),
            )
        )
        .where(*_outbound_in_window(org, window), Message.state.in_(SENT_STATES))
        .group_by(category, prefix)
    )
    return [
        CostGroup(r.category, r.prefix or "", r.messages, r.billable, r.priced_by_meta)
        for r in (await session.execute(stmt)).all()
    ]


#: How many (source, category, prefix) groups the per-source read considers. shortcut: ordered by
#: message count, not cost, so a source with few but expensive messages past the cap is missed;
#: compute cost in SQL if an organization ever has more than this many paying sources a window.
_SOURCE_GROUPS_CAP: Final = 500
#: How many sources one response lists.
MAX_COST_SOURCES: Final = 10


@dataclass(frozen=True, slots=True)
class SourceGroup:
    kind: str
    key: str
    category: str
    prefix: str
    billable: int


async def source_groups(
    session: AsyncSession, org: uuid.UUID, window: DateWindow
) -> list[SourceGroup]:
    """Billable, paying (non-`service`) sent messages grouped by what sent them: the automation
    (`rule_code`) or the inbox conversation. One grouped query, bounded by `_SOURCE_GROUPS_CAP`."""
    category = _category_expr().label("category")
    prefix = _number_prefix().label("prefix")
    stmt = (
        _with_template(
            select(
                Message.rule_code,
                Message.conversation_id,
                category,
                prefix,
                func.count().label("billable"),
            )
        )
        .where(
            *_outbound_in_window(org, window),
            Message.state.in_(SENT_STATES),
            _BILLABLE,
            _category_expr() != "service",
            (Message.rule_code.is_not(None)) | (Message.conversation_id.is_not(None)),
        )
        .group_by(Message.rule_code, Message.conversation_id, category, prefix)
        .order_by(func.count().desc())
        .limit(_SOURCE_GROUPS_CAP)
    )
    out: list[SourceGroup] = []
    for r in (await session.execute(stmt)).all():
        # A conversation reply is attributed to its thread; a rule-driven send to its rule.
        if r.conversation_id is not None:
            out.append(
                SourceGroup(
                    "conversation", str(r.conversation_id), r.category, r.prefix or "", r.billable
                )
            )
        else:
            out.append(
                SourceGroup("automation", str(r.rule_code), r.category, r.prefix or "", r.billable)
            )
    return out


async def conversation_labels(
    session: AsyncSession, org: uuid.UUID, ids: list[uuid.UUID]
) -> dict[str, str]:
    """The **masked** number of each conversation, never the real one."""
    if not ids:
        return {}
    rows = await session.execute(
        select(Conversation.id, Conversation.wa_address_masked).where(
            Conversation.organization_id == org, Conversation.id.in_(ids)
        )
    )
    return {str(r.id): r.wa_address_masked for r in rows.all()}


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
    """Point-in-time counts, not range-bound: what exists now. One statement joining three
    one-row aggregates — each table is scanned once, with the counts split by `FILTER`, and each
    aggregate carries its own organization filter."""
    rules = (
        select(func.count().label("automations"))
        .where(
            Rule.organization_id == org,
            Rule.deleted_at.is_(None),
            Rule.channel == "whatsapp",
            Rule.active.is_(True),
        )
        .subquery("r")
    )
    templates = (
        select(
            func.count().filter(ProviderTemplate.status == "approved").label("approved"),
            func.count().filter(ProviderTemplate.status == "pending").label("pending"),
        )
        .where(
            ProviderTemplate.organization_id == org,
            ProviderTemplate.deleted_at.is_(None),
            ProviderTemplate.channel == "whatsapp",
        )
        .subquery("t")
    )
    conversations = (
        select(
            func.count().label("open"),
            func.count().filter(Conversation.assignee_membership_id.is_(None)).label("unassigned"),
        )
        .where(
            Conversation.organization_id == org,
            Conversation.deleted_at.is_(None),
            Conversation.channel == "whatsapp",
            Conversation.status == "open",
        )
        .subquery("c")
    )
    # Each side is exactly one row, so the joins are on `true` by design.
    stmt = select(
        rules.c.automations,
        templates.c.approved,
        templates.c.pending,
        conversations.c.open,
        conversations.c.unassigned,
    ).select_from(rules.join(templates, true()).join(conversations, true()))
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


def pick_market_rates(
    rows: list[MessagingRateCard], org: uuid.UUID, as_of: date
) -> dict[tuple[str, str], MessagingRateCard]:
    """The one rate per `(calling code, category)` in force on `as_of`.

    Precedence, per category:

      1. the organization's own rows, when any is in force for that category (an organization
         override is all-markets, so it prices every number);
      2. else the platform's rows: per market, the latest `effective_from` on or before `as_of`;
      3. else, for a range that predates every row, the *earliest* row per market there is (the
         organization's before the platform's) — an estimate over old history uses the oldest
         known price rather than pricing it at zero.

    An organization override always beats the default once it is in force, whatever the two
    `effective_from` dates are relative to each other. Only the organization's own rows and the
    platform tenant's are ever considered.
    """
    picked: dict[tuple[str, str], MessagingRateCard] = {}
    for category in CATEGORIES:
        mine = [r for r in rows if r.category == category and r.organization_id == org]
        base = [r for r in rows if r.category == category and r.organization_id == PLATFORM_ORG]
        chosen: dict[str, MessagingRateCard] = {}
        for pool in (mine, base):
            for r in pool:
                if r.effective_from <= as_of:
                    code = r.calling_code or ""
                    if code not in chosen or r.effective_from > chosen[code].effective_from:
                        chosen[code] = r
            if chosen:
                break
        if not chosen:
            for pool in (mine, base):
                for r in pool:
                    code = r.calling_code or ""
                    if code not in chosen or r.effective_from < chosen[code].effective_from:
                        chosen[code] = r
                if chosen:
                    break
        for code, r in chosen.items():
            picked[(code, category)] = r
    return picked


def pick_rates(
    rows: list[MessagingRateCard], org: uuid.UUID, as_of: date
) -> dict[str, MessagingRateCard]:
    """The all-markets ("Other") rate per category in force on `as_of` — see
    `pick_market_rates` for the precedence."""
    return {
        category: r
        for (code, category), r in pick_market_rates(rows, org, as_of).items()
        if code == ""
    }


def rate_for(
    picked: dict[tuple[str, str], MessagingRateCard], number: str, category: str
) -> MessagingRateCard | None:
    """The rate a message to `number` (or a prefix of it) in `category` is priced at."""
    codes = {code for code, cat in picked if cat == category}
    return picked.get((m1_cost.market_code(number, codes), category))


def _rate_card_ref(picked: Any, org: uuid.UUID) -> RateCardRef:
    rows: list[MessagingRateCard] = list(picked.values())
    own = [r.organization_id == org for r in rows]
    if own and all(own):
        source = "organization"
    elif any(own):
        source = "mixed"
    else:
        source = "platform_default"
    placeholder = any(r.is_placeholder for r in rows)
    version = next((r.label for r in rows if r.is_placeholder), None)
    if version is None:
        version = "organization" if source == "organization" else rows[0].label if rows else ""
    return RateCardRef(source=source, placeholder=placeholder, version=version)


_PLACEHOLDER_NOTE: Final = (
    f"These rates are PLACEHOLDERS ({PLACEHOLDER_LABEL}), not Meta's published prices for your "
    "country. Enter your real per-1000 rates to make the cost estimate meaningful."
)
_OVERRIDE_NOTE: Final = "Rates entered by your organization. Costs are estimates, not invoices."
_META_NOTE: Final = (
    "Meta's published per-message list rates for each recipient market ({label}). Messages are "
    "priced by the category and billable flag Meta reports on each delivery receipt; volume-tier "
    "discounts and taxes are not applied, so this is an estimate, not your Meta invoice."
)


async def read_rate_card(session: AsyncSession, org: uuid.UUID, *, as_of: date) -> RateCardOut:
    picked = pick_market_rates(await _rate_rows(session, org), org, as_of)
    ref = _rate_card_ref(picked, org)
    if ref.placeholder:
        note = _PLACEHOLDER_NOTE
    elif ref.source == "organization":
        note = _OVERRIDE_NOTE
    else:
        note = _META_NOTE.format(label=ref.version)
    order = {c: i for i, c in enumerate(CATEGORIES)}
    return RateCardOut(
        channel="whatsapp",
        placeholder=ref.placeholder,
        version=ref.version,
        note=note,
        rates=[
            RateOut(
                calling_code=code,
                market=r.market or ("Other" if code == "" else ""),
                category=category,
                amount_minor_per_1000_messages=r.amount_minor_per_1000_messages,
                currency=r.currency,
                effective_from=r.effective_from,
                source="organization" if r.organization_id == org else "platform_default",
                placeholder=r.is_placeholder,
            )
            for (code, category), r in sorted(
                picked.items(), key=lambda kv: (kv[0][0] == "", kv[0][0], order[kv[0][1]])
            )
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
            calling_code="",
            market="",
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
                index_elements=[
                    "organization_id",
                    "channel",
                    "calling_code",
                    "category",
                    "effective_from",
                ],
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


@dataclass(frozen=True, slots=True)
class PlatformRate:
    calling_code: str
    market: str
    category: str
    amount_minor_per_1000_messages: int
    currency: str


async def override_platform_rates(
    session: AsyncSession,
    *,
    operator_id: uuid.UUID,
    rates: list[PlatformRate],
    effective_from: date,
    label: str,
    request: Request | None = None,
) -> None:
    """Staff write of the **platform** rate table (every tenant's default), in the caller's
    transaction, bound to the platform tenant so the forced RLS policy admits exactly these rows.
    Idempotent per `(market, category, effective_from)`; a later day adds a price point and
    nothing earlier is edited or deleted. Audited under the platform tenant."""
    await set_tenant(session, PLATFORM_ORG)
    actor = str(operator_id)
    for rate in rates:
        values = {
            "market": rate.market,
            "amount_minor_per_1000_messages": rate.amount_minor_per_1000_messages,
            "currency": rate.currency,
            "label": label,
            "is_placeholder": False,
            "deleted_at": None,
            "updated_by": actor,
        }
        await session.execute(
            pg_insert(MessagingRateCard)
            .values(
                organization_id=PLATFORM_ORG,
                channel="whatsapp",
                calling_code=rate.calling_code,
                category=rate.category,
                effective_from=effective_from,
                created_by=actor,
                **values,
            )
            .on_conflict_do_update(
                index_elements=[
                    "organization_id",
                    "channel",
                    "calling_code",
                    "category",
                    "effective_from",
                ],
                set_={**values, "updated_at": func.now()},
            )
        )
    record_audit(
        session,
        AuditLog,
        organization_id=PLATFORM_ORG,
        actor_id=operator_id,
        action="messaging_rate_card.platform_updated",
        entity="messaging_rate_card",
        after={
            "effective_from": effective_from.isoformat(),
            "label": label,
            "rates": [
                {
                    "calling_code": r.calling_code,
                    "category": r.category,
                    "amount_minor_per_1000_messages": r.amount_minor_per_1000_messages,
                    "currency": r.currency,
                }
                for r in rates
            ],
        },
        request=request,
    )
    await session.flush()


# --------------------------------------------------------------------------- assembly


def _money(amount_minor: int, currency: str) -> Money:
    return Money(amount_minor=amount_minor, currency=currency)


def build_cost(
    by_category: dict[str, int],
    picked: dict[str, MessagingRateCard] | dict[tuple[str, str], MessagingRateCard],
    org: uuid.UUID,
    *,
    groups: list[CostGroup] | None = None,
    sources: list[SourceGroup] | None = None,
    source_labels: dict[str, str] | None = None,
) -> Cost:
    """Price each `(market, category)` once (half-up), then sum the already-rounded amounts.

    Summing rounded amounts (rather than rounding a grand total) is the rule the contract states,
    and it means the rows shown always add up to the total shown. `groups` carries the billable
    count per number prefix; without it every message in `by_category` is billable and priced at
    the all-markets rate. A category with no rate row (or one in a different currency) is priced
    at zero. `sources` are priced the same way and listed most expensive first.
    """
    market_rates: dict[tuple[str, str], MessagingRateCard] = {
        (k if isinstance(k, tuple) else ("", k)): r for k, r in picked.items()
    }
    if groups is None:
        groups = [CostGroup(c, "", n, n, 0) for c, n in by_category.items()]
    rates = list(market_rates.values())
    currency = rates[0].currency if rates else "INR"

    def usable(rate: MessagingRateCard | None, category: str) -> MessagingRateCard | None:
        if rate is not None and rate.currency != currency:
            # The API only ever writes one currency per card, so this is a row edited by hand.
            # One total cannot span two currencies; the odd one is left unpriced (and said so in
            # the log) rather than added to the wrong unit.
            log.warning(
                "notification.analytics.rate_card_mixed_currency",
                organization_id=str(org),
                category=category,
            )
            return None
        return rate

    def price(counts: dict[tuple[str, str], int]) -> dict[str, int]:
        """Rounded amount per category, from billable counts per (prefix, category), rounded
        once per (market, category)."""
        per_market: dict[tuple[str, str], int] = {}
        for (prefix, category), n in counts.items():
            codes = {code for code, cat in market_rates if cat == category}
            key = (m1_cost.market_code(prefix, codes), category)
            per_market[key] = per_market.get(key, 0) + n
        amounts: dict[str, int] = {}
        for (code, category), n in per_market.items():
            rate = usable(market_rates.get((code, category)), category)
            amount = m1_cost.estimate_minor(n, rate.amount_minor_per_1000_messages) if rate else 0
            amounts[category] = amounts.get(category, 0) + amount
        return amounts

    billable: dict[tuple[str, str], int] = {}
    for g in groups:
        billable[(g.prefix, g.category)] = billable.get((g.prefix, g.category), 0) + g.billable
    amounts = price(billable)

    lines = [
        CostCategory(
            category=c, messages=by_category.get(c, 0), amount=_money(amounts.get(c, 0), currency)
        )
        for c in CATEGORIES
    ]

    by_source: dict[tuple[str, str], dict[tuple[str, str], int]] = {}
    for s in sources or []:
        counts = by_source.setdefault((s.kind, s.key), {})
        counts[(s.prefix, s.category)] = counts.get((s.prefix, s.category), 0) + s.billable
    source_lines = sorted(
        (
            CostSource(
                kind=kind,
                key=key,
                label=(source_labels or {}).get(key, key),
                messages=sum(counts.values()),
                amount=_money(sum(price(counts).values()), currency),
            )
            for (kind, key), counts in by_source.items()
        ),
        key=lambda line: (-line.amount.amount_minor, line.kind, line.key),
    )[:MAX_COST_SOURCES]

    return Cost(
        total=_money(sum(amounts.values()), currency),
        by_category=lines,
        rate_card=_rate_card_ref(market_rates, org),
        priced_by_meta=sum(g.priced_by_meta for g in groups),
        by_source=source_lines,
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
    """The WhatsApp dashboard for `window`. Eleven grouped statements, none inside a loop."""
    rows = await outbound_series(session, org, window)
    sent = sum(r.sent for r in rows)
    delivered = sum(r.delivered for r in rows)
    read = sum(r.read for r in rows)
    failed = sum(r.failed for r in rows)
    queued = sum(r.queued for r in rows)
    suppressed = sum(r.suppressed for r in rows)

    groups = await cost_groups(session, org, window)
    categories = dict.fromkeys(CATEGORIES, 0)
    for group in groups:
        categories[group.category] += group.messages
    sources = await source_groups(session, org, window)
    labels = await conversation_labels(
        session, org, [uuid.UUID(s.key) for s in sources if s.kind == "conversation"]
    )
    replies, inbound = await reply_counts(session, org, window)
    reasons = await failure_reasons(session, org, window)
    activity = await recent_activity(session, org, window)
    counts = await totals(session, org)

    as_of = min(window.date_to, datetime.now(UTC).date())
    picked = pick_market_rates(await _rate_rows(session, org), org, as_of)

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
        cost=build_cost(
            categories, picked, org, groups=groups, sources=sources, source_labels=labels
        ),
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
        log.warning("notification.analytics.email_overview_unavailable", organization_id=str(org))
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
