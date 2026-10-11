"""The customer 360: one contact, everything the platform knows, assembled from other services.

`GET /customers/{id}/360` answers from **this service's own rows** for the contact, its account
and its owner, and asks the rest of the platform for the remainder over HTTP — never a
cross-schema query (ADR-0000 §10). The design is built around one fact: a 360 is only as fast
and as available as its slowest section, and a customer card that blanks out because support's
database is slow is worse than a card with a hole in it.

So every upstream call is:

  - **concurrent**   `asyncio.gather`, so the wall time is the slowest section, not the sum;
  - **bounded**      an `asyncio.timeout` on top of the HTTP client's own, so a slow upstream
                     costs at most `customer360_upstream_timeout_seconds`;
  - **isolated**     any failure (unreachable, timeout, non-200, a body that does not parse) is
                     caught, logged with the section and organization, and becomes `None` — the
                     section is `null` and its name is in `unavailable`. Nothing else is affected.

**Identity is propagated, not replaced.** The deal call carries the caller's own forwarded
identity headers, so deal applies *the caller's* permission and ownership scope: a rep who may
only see their own deals sees only their own here too. A service token would have widened that to
the whole organization. The messaging summary has no user-facing route, so it is called under a
service token addressed to notification whose `org` claim is the tenant.

**Entitlement-aware.** A section whose product the organization does not have is not called and
comes back `null`, not as an error. `unavailable` is deliberately the union of "failed" and "not
entitled": the card needs to know only that it has no data, and the reason is in the logs.

**What is missing, on purpose.** Orders, finance and support have no endpoint that can be asked
about a CRM contact today — see `MISSING_UPSTREAMS`. Those sections are always `null` and always
listed, rather than approximated from a name match.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

import httpx
from pydantic import BaseModel, ConfigDict

from app.c1_schemas import (
    ChannelsSection,
    CrmSection,
    Customer360,
    Customer360Customer,
    OwnerRef,
    SectionName,
    TimelineEntry,
)
from app.config import Settings
from app.models import Company, Contact, Owner
from platform_core.auth import Principal
from platform_core.internal_auth import FORWARDED_HEADERS, mint_service_token
from platform_core.logging import get_logger
from platform_core.pii import mask

log = get_logger("customer.360")

NOTIFICATION_AUDIENCE: Final = "notification"

#: Sections with no upstream that can answer for a CRM contact. Each is `null` and listed in
#: `unavailable` on every response until one exists:
#:   orders   no service exposes customer orders (quotation has no read routes; storefront and
#:            operations hold enquiries and purchase orders, not sales orders per contact)
#:   finance  finance keys invoices by its own `finance.customers` id; a row can carry a
#:            `crm_contact_id`, but no caller sets it yet and no route says what a contact owes
#:   support  tickets carry a denormalised `requester_company` string and no CRM id, and there is
#:            no filter to ask by contact
MISSING_UPSTREAMS: Final[tuple[SectionName, ...]] = ("orders", "finance", "support")

#: The identity the gateway forwarded, copied verbatim to a tenant-scoped upstream so it applies
#: the caller's own permissions and scope. An allow-list: nothing else on the request travels.
#: It is exactly the set the hop token binds — drop one and the upstream refuses the digest.
_IDENTITY_HEADERS: Final = ("Authorization", "X-Internal-Token", *FORWARDED_HEADERS)

#: An open deal at or above this probability, or in its pipeline's last open stage, makes the
#: account "hot". Which stages are open, and how far along each is, is deal's to say: this module
#: reads the outcome and position deal returns on every row and holds no list of stage keys.
_HOT_PROBABILITY: Final = 70
#: The deal list is bounded; an account with more open deals than this is reported by what fits.
_DEAL_PAGE_SIZE: Final = 200
_TIMELINE_LIMIT: Final = 10

#: Deal's public list route as the platform mounts it. `create_app` includes every router under
#: `/api/v1` (`libs/platform-core/src/platform_core/app.py:362-363`) and the route is declared
#: `@router.get("/deals", ...)` (`services/deal/app/api/routes.py:78-84`), so the real path is this
#: one. `DEAL_INTERNAL_URL` names a host and a port and no path.
DEAL_LIST_PATH: Final = "/api/v1/deals"


class UpstreamUnavailable(Exception):
    """An upstream answered, but not with usable data — a non-200 or an unparseable body."""


ClientFactory = Callable[[float], httpx.AsyncClient]


def _default_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


#: Tests substitute this to put a fake at the HTTP boundary.
client_factory: ClientFactory = _default_client


@dataclass(frozen=True)
class CallerContext:
    """Who is asking, as far as the upstream calls are concerned."""

    organization_id: uuid.UUID
    user_id: uuid.UUID
    principal: Principal
    forward_headers: Mapping[str, str]

    def has_product(self, product: str) -> bool:
        return self.principal.has_product(product)


def forwarded_identity(headers: Mapping[str, str]) -> dict[str, str]:
    lowered = {k.lower(): v for k, v in headers.items()}
    return {name: lowered[name.lower()] for name in _IDENTITY_HEADERS if name.lower() in lowered}


# --------------------------------------------------------------------------- upstream models


class _DealRow(BaseModel):
    # An upstream may add fields; only these are read here. The `stage_*` fields are required on
    # purpose: a deal row without them (an older deal image during a deploy, or a contract that
    # drifted) fails the parse, the CRM section degrades to null and is named in `unavailable`
    # — the one outcome that cannot mislead. A key-literal fallback would be exactly the
    # hard-coded stage list this module no longer has.
    model_config = ConfigDict(extra="ignore")

    stage: str
    stage_outcome: Literal["open", "won", "lost"]
    stage_position: int
    stage_is_last_open: bool
    probability: int
    created_at: datetime


class _DealPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[_DealRow]


# --------------------------------------------------------------------------- sections


async def fetch_crm(
    client: httpx.AsyncClient,
    cfg: Settings,
    caller: CallerContext,
    company_id: uuid.UUID,
) -> tuple[CrmSection, datetime | None]:
    """Open opportunities on the contact's account, from deal-service's tenant-scoped list under
    the caller's own identity. Also returns the most recent deal's creation time for the
    timeline."""
    response = await client.get(
        f"{cfg.deal_internal_url}{DEAL_LIST_PATH}",
        params={"company_id": str(company_id), "size": _DEAL_PAGE_SIZE},
        headers=dict(caller.forward_headers),
    )
    if response.status_code != 200:
        raise UpstreamUnavailable(f"deal returned {response.status_code}")
    page = _DealPage.model_validate(response.json())

    open_deals = [d for d in page.items if d.stage_outcome == "open"]
    furthest = max(open_deals, key=lambda d: d.stage_position, default=None)
    stage = furthest.stage if furthest is not None else None
    hot = any(d.probability >= _HOT_PROBABILITY or d.stage_is_last_open for d in open_deals)
    latest = max((d.created_at for d in page.items), default=None)
    return CrmSection(stage=stage, open_opportunities=len(open_deals), hot=hot), latest


async def fetch_channels(
    client: httpx.AsyncClient,
    cfg: Settings,
    caller: CallerContext,
    contact_id: uuid.UUID,
    email: str | None,
) -> ChannelsSection:
    """WhatsApp and email standing, from notification-service's internal messaging summary."""
    token = mint_service_token(
        cfg,
        audience=NOTIFICATION_AUDIENCE,
        caller=cfg.service_name,
        claims={"org": str(caller.organization_id), "sub": str(caller.user_id)},
    )
    headers = {"X-Internal-Token": token}
    if email:
        # A header, not the URL: the address must not land in an access log.
        headers["X-Contact-Email"] = email
    response = await client.get(
        f"{cfg.notification_internal_url}/internal/v1/contacts/{contact_id}/messaging-summary",
        headers=headers,
    )
    if response.status_code != 200:
        raise UpstreamUnavailable(f"notification returned {response.status_code}")
    return ChannelsSection.model_validate(response.json())


async def _guarded[T](
    section: str,
    caller: CallerContext,
    budget_seconds: float,
    call: Awaitable[T],
) -> T | None:
    """Run one section, turning every failure into `None`. The reason is logged with the section
    and organization only — never a URL, header or body, which can carry the caller's token or a
    customer's address."""
    try:
        async with asyncio.timeout(budget_seconds):
            return await call
    except Exception as exc:
        log.warning(
            "customer.360.section_unavailable",
            section=section,
            organization_id=str(caller.organization_id),
            reason=type(exc).__name__,
        )
        return None


# --------------------------------------------------------------------------- assembly


def _status(contact: Contact) -> str:
    return "inactive" if contact.status == "dormant" else "active"


def _timeline(
    contact: Contact,
    channels: ChannelsSection,
    latest_deal_at: datetime | None,
) -> list[TimelineEntry]:
    events: list[tuple[str, str, datetime | None]] = [
        ("contact_created", "Added to the CRM", contact.created_at),
        ("activity", "Last logged activity", contact.last_activity_at),
        ("deal_opened", "Most recent deal opened", latest_deal_at),
        (
            "whatsapp_message",
            "Last WhatsApp message",
            channels.whatsapp.last_message_at if channels.whatsapp else None,
        ),
        (
            "email_sent",
            "Last email sent",
            channels.email.last_email_at if channels.email else None,
        ),
    ]
    dated = [TimelineEntry(kind=k, title=t, at=at) for k, t, at in events if at is not None]
    dated.sort(key=lambda e: e.at, reverse=True)
    return dated[:_TIMELINE_LIMIT]


async def assemble(
    cfg: Settings,
    caller: CallerContext,
    *,
    contact: Contact,
    company: Company | None,
    owner: Owner | None,
) -> Customer360:
    """Build the 360 for a contact the caller has already been authorised to see.

    The database rows are passed in, already read: no session is used here, so the transaction
    is not held open while upstreams answer more than it must be."""
    timeout = cfg.customer360_upstream_timeout_seconds
    unavailable: list[SectionName] = list(MISSING_UPSTREAMS)

    async with client_factory(timeout) as client:
        crm_call = (
            _guarded("crm", caller, timeout, fetch_crm(client, cfg, caller, contact.company_id))
            if caller.has_product("crm")
            else None
        )
        channels_call = (
            _guarded(
                "channels",
                caller,
                timeout,
                fetch_channels(client, cfg, caller, contact.id, contact.email or None),
            )
            if caller.has_product("engage")
            else None
        )
        crm_result, channels_result = await asyncio.gather(
            crm_call if crm_call is not None else _none(),
            channels_call if channels_call is not None else _none(),
        )

    crm: CrmSection | None = None
    latest_deal_at: datetime | None = None
    if crm_result is None:
        unavailable.append("crm")
    else:
        crm, latest_deal_at = crm_result

    if channels_result is None:
        unavailable.append("channels")
    channels = channels_result or ChannelsSection()

    return Customer360(
        customer=Customer360Customer(
            id=str(contact.id),
            name=contact.full_name,
            company=company.name if company else None,
            since=contact.created_at,
            status=_status(contact),
            tags=[],
            owner=OwnerRef(id=str(owner.id), name=owner.full_name) if owner else None,
            phone=contact.phone,
            email=mask(contact.email, kind="email"),
            city=(company.city or None) if company else None,
        ),
        crm=crm,
        orders=None,
        finance=None,
        support=None,
        channels=channels,
        timeline=_timeline(contact, channels, latest_deal_at),
        unavailable=unavailable,
    )


async def _none() -> Any:
    return None
