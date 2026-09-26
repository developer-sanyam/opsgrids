"""Signed open/click tracking for campaign email (slice E1).

The Engage design sends through the customer's own provider and runs no tracking domain, so
tracking here is **opt-in per campaign** (`track_opens`, `track_clicks`) and served from the
platform's own public API base, with no DNS the customer has to touch.

**Tokens.** `base64url(payload) "." base64url(mac)`. The payload is a version byte, the
organization id, the recipient id and a link id (all-zero for an open), 49 bytes of opaque
identifiers and no address, name or URL. The MAC is HMAC-SHA256 over the token kind (`o` or `c`)
and the payload, so an open token can never be replayed as a click token, and it is compared in
constant time. The key is derived from the unsubscribe secret unless `ENGAGE_TRACKING_SECRET`
is set, and is unavailable (tracking is skipped, never faked) wherever the unsubscribe key is
refused, the same "a forgeable key is no key" rule.

**No open redirect.** A click token names a link id, and the redirect target is read from
`email_links`, populated only from the anchors of the campaign body when it was sent. Nothing a
requester supplies ever becomes a `Location`. Only `http` and `https` targets are registered and
the scheme is checked again before redirecting.

**Never rewritten.** `mailto:`, `tel:`, fragments, relative URLs, anything not `http(s)`, and any
URL whose path mentions `unsubscribe` (this platform's own link, and a customer's own preference
page) are left exactly as written. The unsubscribe footer and the `List-Unsubscribe` headers are
added or built outside this module.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import re
import struct
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape as html_escape
from html import unescape as html_unescape
from typing import Final, Literal
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.e1_models import EmailEvent, EmailLink
from platform_core.logging import get_logger

log = get_logger(__name__)

Kind = Literal["open", "click"]
DeviceClass = Literal["desktop", "mobile", "tablet", "bot", "unknown"]

_VERSION: Final = 1
_NIL: Final = uuid.UUID(int=0)
_PAYLOAD_LEN: Final = 1 + 16 + 16 + 16
_MAC_LEN: Final = 16
#: Distinct links registered per campaign. A body with more is tracked on the first ones only.
MAX_LINKS_PER_CAMPAIGN: Final = 200
#: A repeat of the same event by the same recipient inside this window is not written again, so a
#: valid token replayed in a loop cannot grow the table without bound.
DUPLICATE_WINDOW: Final = timedelta(seconds=5)
#: Mail is not opened by a person this soon after the provider accepted it; a fetch this fast is a
#: scanner or prefetcher.
MACHINE_WINDOW: Final = timedelta(seconds=3)

#: The 1x1 transparent GIF, byte for byte.
PIXEL_GIF: Final = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


# ------------------------------------------------------------------------------------ tokens


def tracking_key() -> bytes | None:
    """The tracking MAC key, or None where tracking must not run (see the module docstring)."""
    from app import services  # local: services imports this module at send time

    cfg = settings()
    explicit = cfg.engage_tracking_secret.get_secret_value()
    if explicit:
        return explicit.encode()
    base = services.unsubscribe_key()
    if base is None:
        return None
    return hmac.new(base, b"opsgrids-engage-tracking-v1", hashlib.sha256).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(key: bytes, kind: Kind, payload: bytes) -> bytes:
    return hmac.new(key, kind.encode() + b"|" + payload, hashlib.sha256).digest()[:_MAC_LEN]


def make_token(
    kind: Kind,
    key: bytes,
    *,
    organization_id: uuid.UUID,
    recipient_id: uuid.UUID,
    link_id: uuid.UUID | None = None,
) -> str:
    payload = (
        struct.pack("B", _VERSION)
        + organization_id.bytes
        + recipient_id.bytes
        + (link_id or _NIL).bytes
    )
    return f"{_b64(payload)}.{_b64(_mac(key, kind, payload))}"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    organization_id: uuid.UUID
    recipient_id: uuid.UUID
    link_id: uuid.UUID | None


def verify_token(kind: Kind, token: str, key: bytes | None) -> TokenClaims | None:
    """The claims a token proves, or None for anything malformed, forged, of the other kind, or
    checked with no key. One answer for every failure: a caller learns nothing about why."""
    if key is None or len(token) > 200 or token.count(".") != 1:
        return None
    body, _, mac_text = token.partition(".")
    try:
        payload = _unb64(body)
        mac = _unb64(mac_text)
    except ValueError:
        return None
    if len(payload) != _PAYLOAD_LEN or payload[0] != _VERSION:
        return None
    if not hmac.compare_digest(mac, _mac(key, kind, payload)):
        return None
    link = uuid.UUID(bytes=payload[33:49])
    if (kind == "open") != (link == _NIL):
        return None
    return TokenClaims(
        organization_id=uuid.UUID(bytes=payload[1:17]),
        recipient_id=uuid.UUID(bytes=payload[17:33]),
        link_id=None if link == _NIL else link,
    )


# ------------------------------------------------------------------------ classification

_BOT_MARKERS: Final = (
    "bot",
    "crawler",
    "spider",
    "scanner",
    "preview",
    "headless",
    "curl/",
    "wget/",
    "python-requests",
    "python-urllib",
    "go-http-client",
    "java/",
    "okhttp",
    "libwww",
    "apache-httpclient",
    "barracuda",
    "proofpoint",
    "mimecast",
    "trendmicro",
    "symantec",
    "safelinks",
    "microsoft office",
    "outlook-ios-prefetch",
    "facebookexternalhit",
    "slackbot",
    "whatsapp/",
    "skypeuripreview",
    "google-safety",
    "linkchecker",
    "urlscan",
)
_APPLE_PROXY_NET: Final = ipaddress.ip_network("17.0.0.0/8")


def _apple_proxy(client_ip: str | None) -> bool:
    if not client_ip:
        return False
    try:
        return ipaddress.ip_address(client_ip.split(",")[0].strip()) in _APPLE_PROXY_NET
    except ValueError:
        return False


def classify(
    user_agent: str | None, *, client_ip: str | None = None, sent_at: datetime | None = None
) -> tuple[DeviceClass, bool]:
    """`(device class, is_machine)` for one request. Only the class is ever stored: the user
    agent and the address are read here, at request time, and dropped.

    Machine: a known scanner or prefetch agent, Apple Mail Privacy Protection (its proxy fetches
    from Apple's 17.0.0.0/8 and presents a WebKit agent with no `Version/`/`Safari/` token), or a
    fetch within seconds of the provider accepting the message.
    """
    ua = (user_agent or "").strip()
    lowered = ua.lower()
    if any(marker in lowered for marker in _BOT_MARKERS):
        return "bot", True
    machine = _apple_proxy(client_ip)
    if "applewebkit" in lowered and "safari/" not in lowered and "version/" not in lowered:
        machine = True
    if sent_at is not None and datetime.now(UTC) - sent_at < MACHINE_WINDOW:
        machine = True
    if not ua:
        return "unknown", machine
    if "ipad" in lowered or "tablet" in lowered:
        return "tablet", machine
    if any(m in lowered for m in ("iphone", "ipod", "android", "mobile")):
        return "mobile", machine
    if any(m in lowered for m in ("windows", "macintosh", "x11", "linux", "cros")):
        return "desktop", machine
    return "unknown", machine


# ------------------------------------------------------------------------------ rewriting

_ANCHOR_HREF: Final = re.compile(
    r"(<a\b[^>]*?\bhref\s*=\s*)(\"[^\"]*\"|'[^']*')", re.IGNORECASE | re.DOTALL
)


def is_trackable_url(url: str) -> bool:
    """An absolute http(s) URL that is not an unsubscribe link. mailto, tel, fragments, relative
    and every other scheme are never rewritten."""
    if not url or len(url) > 2048:
        return False
    if any(ord(ch) <= 32 or ord(ch) == 127 for ch in url):
        return False  # whitespace and control characters (header injection, ambiguous parsing)
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return False
    return "unsubscribe" not in parts.path.lower()


def extract_links(html: str) -> list[str]:
    """Distinct trackable anchor URLs, in order of first appearance."""
    seen: dict[str, None] = {}
    for match in _ANCHOR_HREF.finditer(html):
        url = html_unescape(match.group(2)[1:-1]).strip()
        if is_trackable_url(url):
            seen.setdefault(url, None)
    return list(seen)[:MAX_LINKS_PER_CAMPAIGN]


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TrackingPlan:
    """What to do to one campaign's HTML for each recipient. Built once per batch."""

    key: bytes
    base: str
    link_ids: dict[str, uuid.UUID]
    opens: bool
    clicks: bool

    def render(self, html: str, *, organization_id: uuid.UUID, recipient_id: uuid.UUID) -> str:
        """`html` with its trackable links rewritten (clicks) and a pixel appended (opens)."""
        if self.clicks:

            def swap(match: re.Match[str]) -> str:
                quote = match.group(2)[0]
                url = html_unescape(match.group(2)[1:-1]).strip()
                link_id = self.link_ids.get(url)
                if link_id is None or not is_trackable_url(url):
                    return match.group(0)
                token = make_token(
                    "click",
                    self.key,
                    organization_id=organization_id,
                    recipient_id=recipient_id,
                    link_id=link_id,
                )
                href = html_escape(f"{self.base}/engage/t/c/{token}", quote=True)
                return f"{match.group(1)}{quote}{href}{quote}"

            html = _ANCHOR_HREF.sub(swap, html)
        if self.opens:
            token = make_token(
                "open", self.key, organization_id=organization_id, recipient_id=recipient_id
            )
            src = html_escape(f"{self.base}/engage/t/o/{token}", quote=True)
            pixel = (
                f'<img src="{src}" width="1" height="1" alt="" '
                'style="display:none;border:0;width:1px;height:1px" />'
            )
            lowered = html.lower()
            at = lowered.rfind("</body>")
            html = f"{html}{pixel}" if at < 0 else f"{html[:at]}{pixel}{html[at:]}"
        return html


async def prepare_plan(  # type: ignore[no-untyped-def]
    session: AsyncSession, campaign
) -> TrackingPlan | None:
    """The tracking plan for a campaign about to send a batch, or None when tracking is off for
    it (or cannot be done safely). Registers the campaign's link destinations, idempotently, in
    the caller's transaction: two queries per batch, none per recipient."""
    if not (campaign.track_opens or campaign.track_clicks):
        return None
    key = tracking_key()
    if key is None:
        log.warning("engage.tracking.unavailable", campaign_id=str(campaign.id))
        return None
    link_ids: dict[str, uuid.UUID] = {}
    if campaign.track_clicks:
        urls = extract_links(campaign.html_body)
        if urls:
            await session.execute(
                pg_insert(EmailLink)
                .values(
                    [
                        {
                            "id": uuid.uuid4(),
                            "organization_id": campaign.organization_id,
                            "campaign_id": campaign.id,
                            "url": url,
                            "url_hash": url_hash(url),
                        }
                        for url in urls
                    ]
                )
                .on_conflict_do_nothing(constraint="uq_email_link_url")
            )
            rows = (
                await session.execute(
                    select(EmailLink.url, EmailLink.id).where(
                        EmailLink.organization_id == campaign.organization_id,
                        EmailLink.campaign_id == campaign.id,
                    )
                )
            ).all()
            link_ids = {row.url: row.id for row in rows}
    return TrackingPlan(
        key=key,
        base=settings().engage_public_api_base.rstrip("/"),
        link_ids=link_ids,
        opens=bool(campaign.track_opens),
        clicks=bool(campaign.track_clicks),
    )


# ------------------------------------------------------------------------------- recording


async def link_destination(
    session: AsyncSession, organization_id: uuid.UUID, link_id: uuid.UUID
) -> EmailLink | None:
    stmt = select(EmailLink).where(
        EmailLink.id == link_id, EmailLink.organization_id == organization_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def record_event(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    recipient_id: uuid.UUID,
    campaign_id: uuid.UUID,
    kind: Kind,
    link_id: uuid.UUID | None,
    user_agent_class: DeviceClass,
    is_machine: bool,
) -> bool:
    """Append one event, unless the same recipient produced the same event (same kind, link,
    device class and machine flag) moments ago. Returns whether a row was written. Runs in the
    caller's tenant-bound transaction."""
    recent = select(EmailEvent.id).where(
        EmailEvent.organization_id == organization_id,
        EmailEvent.recipient_id == recipient_id,
        EmailEvent.kind == kind,
        # Machine and human fetches, and each device class, are distinct: a scanner arriving
        # first must never swallow the person's own open that follows it.
        EmailEvent.is_machine.is_(is_machine),
        EmailEvent.user_agent_class == user_agent_class,
        EmailEvent.occurred_at > datetime.now(UTC) - DUPLICATE_WINDOW,
    )
    recent = recent.where(
        EmailEvent.link_id.is_(None) if link_id is None else EmailEvent.link_id == link_id
    ).limit(1)
    if (await session.execute(recent)).first() is not None:
        return False
    session.add(
        EmailEvent(
            organization_id=organization_id,
            campaign_id=campaign_id,
            recipient_id=recipient_id,
            link_id=link_id,
            kind=kind,
            user_agent_class=user_agent_class,
            is_machine=is_machine,
        )
    )
    await session.flush()
    return True
