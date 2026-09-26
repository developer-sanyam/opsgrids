"""Email tracking end to end (slice E1): the send path rewrites, the public endpoints verify and
record, the analytics reads fold. Real PostgreSQL as the app role; nothing opens a socket.

Public-endpoint tests run through the real router with no principal, exactly as a mail client
would call it. The authenticated reads override the principal the way the other Engage tests do.
"""

from __future__ import annotations

import asyncio
import re
import threading
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from app import consumers, e1_analytics, e1_models, e1_tracking, models, services
from app.config import settings
from app.transports import RecordingTransport
from platform_core.auth import Scope
from platform_core.db import dispose_engine, init_engine, session_scope
from tests.helpers import ctx_for, principal_for
from tests.test_engage_campaigns import (
    CAMPAIGNS,
    K_READ,
    K_WRITE,
    _contacts,
    _drain,
    _envelope,
    _seed_identity,
    _send,
    as_principal,
    caller,
)

pytestmark = pytest.mark.integration

TRACK = "/api/v1/engage/t"
DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
PHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)
SALE = "https://shop.example.com/sale?a=1&b=2"
BODY = (
    "<html><body><p>"
    f'<a href="{SALE.replace("&", "&amp;")}">Sale</a> '
    '<a href="mailto:help@example.com">Mail</a> '
    '<a href="https://example.com/unsubscribe/me">Stop</a>'
    "</p></body></html>"
)


@pytest_asyncio.fixture
async def worker_engine(session) -> AsyncIterator[None]:
    init_engine(settings())
    yield
    await dispose_engine()


async def _campaign(session, org_id, sender_id, *, opens=True, clicks=True, body=BODY) -> uuid.UUID:
    async with session.begin():
        row = await services.create_campaign(
            session,
            ctx_for(org_id, K_WRITE, Scope.ORG),
            fields={
                "name": "Autumn",
                "sender_id": sender_id,
                "subject": "News",
                "html_body": body,
                "text_body": "Hello",
                "audience": "all_sendable",
                "list_id": None,
                "scheduled_at": None,
                "track_opens": opens,
                "track_clicks": clicks,
            },
        )
    return row.id


async def _run(
    session, org_id, *, opens=True, clicks=True, body=BODY, to=("reader@example.com",), batch=200
):
    """A sent campaign: `(campaign_id, [messages], [recipient ids])`."""
    sender_id = await _seed_identity(session, org_id)
    await _contacts(session, org_id, *to)
    campaign_id = await _campaign(session, org_id, sender_id, opens=opens, clicks=clicks, body=body)
    await _send(session, org_id, campaign_id)
    assert await consumers.expand_campaign_send(_envelope(org_id, campaign_id)) == "expanded"
    wire = RecordingTransport()
    assert await _drain(session, org_id, campaign_id, transport=wire, batch_size=batch) == "done"
    async with session.begin():
        ids = list(
            (
                await session.execute(
                    select(models.CampaignRecipient.id).where(
                        models.CampaignRecipient.campaign_id == campaign_id
                    )
                )
            ).scalars()
        )
    return campaign_id, wire.sent, ids


def _tokens(html: str, kind: str) -> list[str]:
    return re.findall(rf"/engage/t/{kind}/([^\"'<> ]+)", html)


async def _age_sends(session, org_id) -> None:
    """Move `sent_at` back so requests are not inside the machine window."""
    async with session.begin():
        await session.execute(
            update(models.CampaignRecipient)
            .where(models.CampaignRecipient.organization_id == org_id)
            .values(sent_at=datetime.now(UTC) - timedelta(hours=2))
        )


async def _events(session, org_id) -> list[e1_models.EmailEvent]:
    async with session.begin():
        return list(
            (
                await session.execute(
                    select(e1_models.EmailEvent)
                    .where(e1_models.EmailEvent.organization_id == org_id)
                    .order_by(e1_models.EmailEvent.occurred_at)
                )
            ).scalars()
        )


# ----------------------------------------------------------------------------- the send path


async def test_a_tracked_send_rewrites_links_and_adds_a_pixel_but_never_touches_unsubscribe(
    session, org_id, worker_engine
):
    campaign_id, sent, ids = await _run(session, org_id)
    (message,) = sent
    html = message.html or ""
    unsubscribe = services.unsubscribe_url_for_recipient(org_id, ids[0])

    assert SALE not in html and "shop.example.com" not in html
    assert len(_tokens(html, "c")) == 1 and len(_tokens(html, "o")) == 1
    assert 'href="mailto:help@example.com"' in html
    assert 'href="https://example.com/unsubscribe/me"' in html
    assert f'<a href="{unsubscribe}">Unsubscribe</a>' in html  # the footer link, verbatim
    assert message.headers is not None
    assert message.headers["List-Unsubscribe"] == f"<{unsubscribe}>"
    assert message.body.startswith("Hello") and "/engage/t/" not in message.body

    async with session.begin():
        links = list(
            (
                await session.execute(
                    select(e1_models.EmailLink).where(e1_models.EmailLink.organization_id == org_id)
                )
            ).scalars()
        )
    assert [(link.url, link.campaign_id) for link in links] == [(SALE, campaign_id)]


async def test_batches_share_one_link_row_and_each_recipient_gets_their_own_tokens(
    session, org_id, worker_engine
):
    """One batch per recipient: the link registration runs again each time and stays one row."""
    _, sent, ids = await _run(session, org_id, to=("a@example.com", "b@example.com"), batch=1)
    assert len(sent) == 2
    async with session.begin():
        assert len((await session.execute(select(e1_models.EmailLink))).scalars().all()) == 1
    key = e1_tracking.tracking_key()
    assert key is not None
    seen = set()
    for message in sent:
        (token,) = _tokens(message.html or "", "c")
        claims = e1_tracking.verify_token("click", token, key)
        assert claims is not None and claims.recipient_id in ids
        seen.add(claims.recipient_id)
    assert seen == set(ids)


async def test_an_untracked_campaign_is_sent_exactly_as_written(session, org_id, worker_engine):
    _, sent, _ = await _run(session, org_id, opens=False, clicks=False)
    html = sent[0].html or ""
    assert "/engage/t/" not in html and 'href="https://shop.example.com/sale?a=1&amp;b=2"' in html
    async with session.begin():
        assert (await session.execute(select(e1_models.EmailLink))).first() is None


async def test_open_and_click_tracking_are_independent(session, org_id, worker_engine):
    _, sent, _ = await _run(session, org_id, opens=True, clicks=False)
    assert _tokens(sent[0].html or "", "o") and not _tokens(sent[0].html or "", "c")


# ------------------------------------------------------------------------ the public endpoints


async def test_a_click_redirects_only_to_the_registered_url_and_records_a_human_click(
    client, session, org_id, worker_engine
):
    _, sent, _ = await _run(session, org_id)
    await _age_sends(session, org_id)
    token = _tokens(sent[0].html or "", "c")[0]

    # A `url` query parameter is ignored: nothing the requester sends becomes the Location.
    hit = await client.get(
        f"{TRACK}/c/{token}?url=https://evil.example.com&next=//evil.example.com",
        headers={"User-Agent": DESKTOP},
    )
    assert hit.status_code == 302, hit.text
    assert hit.headers["location"] == SALE
    assert hit.headers["cache-control"].startswith("no-store")

    (event,) = await _events(session, org_id)
    assert (event.kind, event.user_agent_class, event.is_machine) == ("click", "desktop", False)
    assert event.link_id is not None and event.organization_id == org_id

    # The same click again straight away is one event, not two: a replayed link cannot flood.
    again = await client.get(f"{TRACK}/c/{token}", headers={"User-Agent": DESKTOP})
    assert again.status_code == 302
    assert len(await _events(session, org_id)) == 1


async def test_a_forged_swapped_or_wrong_kind_token_is_a_404_and_records_nothing(
    client, session, org_id, worker_engine
):
    _, sent, _ = await _run(session, org_id)
    await _age_sends(session, org_id)
    html = sent[0].html or ""
    click, pixel = _tokens(html, "c")[0], _tokens(html, "o")[0]

    body, mac = click.split(".")
    tampered = f"{body}.{mac[:-2]}{'AA' if not mac.endswith('AA') else 'BB'}"
    for bad in (tampered, pixel, "garbage", f"{body}."):
        response = await client.get(f"{TRACK}/c/{bad}", headers={"User-Agent": DESKTOP})
        assert response.status_code == 404, bad
        assert "location" not in response.headers
    assert await _events(session, org_id) == []

    # The pixel endpoint refuses a click token the same way: the GIF, and nothing recorded.
    served = await client.get(f"{TRACK}/o/{click}", headers={"User-Agent": DESKTOP})
    assert served.status_code == 200 and served.headers["content-type"] == "image/gif"
    assert await _events(session, org_id) == []


async def test_a_token_for_another_organization_reaches_nothing(
    client, session, org_id, worker_engine
):
    _, _, ids = await _run(session, org_id)
    await _age_sends(session, org_id)
    async with session.begin():
        link_id = (await session.execute(select(e1_models.EmailLink.id))).scalar_one()
    key = e1_tracking.tracking_key()
    assert key is not None
    stranger = uuid.uuid4()

    # Validly signed, but for an organization that owns neither the recipient nor the link.
    foreign_click = e1_tracking.make_token(
        "click", key, organization_id=stranger, recipient_id=ids[0], link_id=link_id
    )
    foreign_open = e1_tracking.make_token(
        "open", key, organization_id=stranger, recipient_id=ids[0]
    )
    assert (await client.get(f"{TRACK}/c/{foreign_click}")).status_code == 404
    assert (await client.get(f"{TRACK}/o/{foreign_open}")).status_code == 200
    assert await _events(session, org_id) == []
    assert await _events(session, stranger) == []

    # And this organization's recipient cannot be paired with a link of another campaign.
    other_campaign = uuid.uuid4()
    async with session.begin():
        session.add(
            e1_models.EmailLink(
                organization_id=org_id,
                campaign_id=other_campaign,
                url="https://elsewhere.example.com/",
                url_hash=e1_tracking.url_hash("https://elsewhere.example.com/"),
            )
        )
    async with session.begin():
        elsewhere = (
            await session.execute(
                select(e1_models.EmailLink.id).where(
                    e1_models.EmailLink.campaign_id == other_campaign
                )
            )
        ).scalar_one()
    mismatched = e1_tracking.make_token(
        "click", key, organization_id=org_id, recipient_id=ids[0], link_id=elsewhere
    )
    assert (await client.get(f"{TRACK}/c/{mismatched}")).status_code == 404
    assert await _events(session, org_id) == []


@pytest.mark.parametrize(
    "destination",
    ["javascript:alert(1)", "ftp://x.example.com/f", "//evil.example.com/x", "data:text/html,x"],
)
async def test_a_registered_link_that_is_not_http_is_never_redirected_to(
    client, session, org_id, worker_engine, destination
):
    """Defence in depth: even a validly signed token for a link row that somehow holds a
    non-http(s) destination is refused, so the redirect can never leave http(s)."""
    _, _, ids = await _run(session, org_id)
    async with session.begin():
        campaign_id = (
            (await session.execute(select(models.CampaignRecipient.campaign_id))).scalars().first()
        )
        row = e1_models.EmailLink(
            organization_id=org_id,
            campaign_id=campaign_id,
            url=destination,
            url_hash=e1_tracking.url_hash(destination),
        )
        session.add(row)
        await session.flush()
        link_id = row.id
    key = e1_tracking.tracking_key()
    assert key is not None
    token = e1_tracking.make_token(
        "click", key, organization_id=org_id, recipient_id=ids[0], link_id=link_id
    )
    response = await client.get(f"{TRACK}/c/{token}")
    assert response.status_code == 404 and "location" not in response.headers


async def test_a_url_in_the_path_is_not_a_token_and_is_not_followed(client, worker_engine):
    for probe in ("https://evil.example.com", "%2F%2Fevil.example.com", "..%2F..%2Fcampaigns"):
        response = await client.get(f"{TRACK}/c/{probe}")
        assert response.status_code == 404
        assert "location" not in response.headers


async def test_the_pixel_records_an_open_and_scanners_are_kept_but_marked_machine(
    client, session, org_id, worker_engine
):
    _, sent, _ = await _run(session, org_id)
    await _age_sends(session, org_id)
    token = _tokens(sent[0].html or "", "o")[0]

    first = await client.get(f"{TRACK}/o/{token}", headers={"User-Agent": PHONE})
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/gif"
    assert first.content == e1_tracking.PIXEL_GIF and first.content[:3] == b"GIF"
    assert "no-store" in first.headers["cache-control"]

    bot = await client.get(
        f"{TRACK}/o/{token}", headers={"User-Agent": "Barracuda Sentinel (EquenceAI)"}
    )
    assert bot.status_code == 200
    apple = await client.get(
        f"{TRACK}/o/{token}",
        headers={"User-Agent": DESKTOP, "X-Forwarded-For": "17.58.4.4"},
    )
    assert apple.status_code == 200

    events = await _events(session, org_id)
    assert sorted((e.user_agent_class, e.is_machine) for e in events) == [
        ("bot", True),
        ("desktop", True),
        ("mobile", False),
    ]
    assert all(e.kind == "open" and e.link_id is None for e in events)


async def test_an_open_seconds_after_sending_is_a_machine(client, session, org_id, worker_engine):
    _, sent, _ = await _run(session, org_id)  # sent_at is now: inside the machine window
    token = _tokens(sent[0].html or "", "o")[0]
    await client.get(f"{TRACK}/o/{token}", headers={"User-Agent": DESKTOP})
    (event,) = await _events(session, org_id)
    assert event.is_machine is True


async def test_email_events_are_append_only_for_the_app_role(session, org_id, worker_engine):
    async with session.begin():
        session.add(
            e1_models.EmailEvent(
                organization_id=org_id,
                campaign_id=uuid.uuid4(),
                recipient_id=uuid.uuid4(),
                kind="open",
                user_agent_class="desktop",
                is_machine=False,
            )
        )
    with pytest.raises(DBAPIError):
        async with session.begin():
            await session.execute(update(e1_models.EmailEvent).values(is_machine=True))


# ------------------------------------------------------------------- bounded event volume


def _event_kwargs(org_id, **overrides):
    base = {
        "organization_id": org_id,
        "recipient_id": uuid.uuid4(),
        "campaign_id": uuid.uuid4(),
        "kind": "open",
        "link_id": None,
        "user_agent_class": "desktop",
        "is_machine": False,
    }
    return {**base, **overrides}


async def test_a_recipient_stops_accumulating_events_at_the_cap(
    session, org_id, worker_engine, monkeypatch
):
    """The repeat window only flattens a burst. The cap is what bounds the append-only table:
    events older than the window are all distinct as far as the repeat check can tell."""
    monkeypatch.setattr(e1_tracking, "MAX_EVENTS_PER_RECIPIENT_KIND", 3)
    kwargs = _event_kwargs(org_id)
    old = datetime.now(UTC) - timedelta(hours=1)
    async with session.begin():
        for _ in range(3):
            session.add(
                e1_models.EmailEvent(
                    organization_id=org_id,
                    campaign_id=kwargs["campaign_id"],
                    recipient_id=kwargs["recipient_id"],
                    kind="open",
                    user_agent_class="desktop",
                    is_machine=False,
                    occurred_at=old,
                )
            )

    async with session.begin():
        assert await e1_tracking.record_event(session, **kwargs) is False
    assert len(await _events(session, org_id)) == 3

    # The limit is per recipient, per kind and per machine flag: none of these is affected.
    async with session.begin():
        assert await e1_tracking.record_event(session, **{**kwargs, "is_machine": True}) is True
    async with session.begin():
        other_recipient = {**kwargs, "recipient_id": uuid.uuid4()}
        assert await e1_tracking.record_event(session, **other_recipient) is True
    async with session.begin():
        link = e1_models.EmailLink(
            organization_id=org_id,
            campaign_id=kwargs["campaign_id"],
            url="https://shop.example.com/",
            url_hash=e1_tracking.url_hash("https://shop.example.com/"),
        )
        session.add(link)
        await session.flush()
        click = {**kwargs, "kind": "click", "link_id": link.id}
        assert await e1_tracking.record_event(session, **click) is True
    assert len(await _events(session, org_id)) == 6


async def test_a_rotating_user_agent_cannot_write_past_the_cap_and_is_still_served(
    client, session, org_id, worker_engine, monkeypatch
):
    """The repeat key includes the device class, so changing the User-Agent used to defeat it.
    Over the cap the pixel and the redirect answer exactly as before and nothing is written."""
    monkeypatch.setattr(e1_tracking, "MAX_EVENTS_PER_RECIPIENT_KIND", 3)
    _, sent, _ = await _run(session, org_id)
    await _age_sends(session, org_id)
    html = sent[0].html or ""
    pixel, click = _tokens(html, "o")[0], _tokens(html, "c")[0]
    tablet = (
        "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17 Safari/604"
    )
    agents = [DESKTOP, PHONE, tablet, "totally-unknown-agent/1.0", "another-unknown-agent/2.0"]

    for agent in agents:
        served = await client.get(f"{TRACK}/o/{pixel}", headers={"User-Agent": agent})
        assert served.status_code == 200 and served.content == e1_tracking.PIXEL_GIF
    human = [e for e in await _events(session, org_id) if not e.is_machine]
    assert len(human) == 3  # desktop, mobile, tablet; the rest were refused

    # A scanner is a separate bucket: it is still recorded, and cannot spend the person's share.
    await client.get(f"{TRACK}/o/{pixel}", headers={"User-Agent": "Barracuda Sentinel"})
    assert len([e for e in await _events(session, org_id) if e.is_machine]) == 1

    # Clicks: the cap is the same, and the redirect never stops working.
    monkeypatch.setattr(e1_tracking, "MAX_EVENTS_PER_RECIPIENT_KIND", 1)
    for agent in (DESKTOP, PHONE, tablet):
        hit = await client.get(f"{TRACK}/c/{click}", headers={"User-Agent": agent})
        assert hit.status_code == 302 and hit.headers["location"] == SALE
    clicks = [e for e in await _events(session, org_id) if e.kind == "click"]
    assert len(clicks) == 1


async def test_parallel_requests_for_one_event_queue_on_the_recipients_lock(
    session, org_id, worker_engine
):
    """Check-then-insert with no lock lets two requests each read "nothing recent" and both
    write. Here the first holds its transaction open; the second must wait for it rather than run
    past an empty read, and then find the first's committed row."""
    kwargs = _event_kwargs(org_id)

    async def second() -> bool:
        async with session_scope(org_id) as other:
            return await e1_tracking.record_event(other, **kwargs)

    racer: asyncio.Task[bool] | None = None
    try:
        async with session_scope(org_id) as first:
            assert await e1_tracking.record_event(first, **kwargs) is True
            racer = asyncio.create_task(second())
            await asyncio.sleep(0.5)
            assert not racer.done(), "the second request did not wait for the first"
        assert await asyncio.wait_for(racer, timeout=15) is False
    finally:
        if racer is not None and not racer.done():
            racer.cancel()
    assert len(await _events(session, org_id)) == 1


async def test_parallel_requests_for_different_recipients_do_not_wait_for_each_other(
    session, org_id, worker_engine
):
    async with session_scope(org_id) as first:
        assert await e1_tracking.record_event(first, **_event_kwargs(org_id)) is True
        async with session_scope(org_id) as other:
            done = await asyncio.wait_for(
                e1_tracking.record_event(other, **_event_kwargs(org_id)), timeout=5
            )
            assert done is True


# ------------------------------------------------------------- work off the event loop


async def test_link_extraction_and_rendering_run_off_the_event_loop(
    session, org_id, worker_engine, monkeypatch
):
    """Both are passes over a tenant-authored body of up to 500 KB, and in production this driver
    shares its loop with every tenant's API traffic."""
    extracted: list[int] = []
    rendered: list[int] = []
    real_extract = e1_tracking.extract_links
    real_render = e1_tracking.TrackingPlan.render

    def spy_extract(html):
        extracted.append(threading.get_ident())
        return real_extract(html)

    def spy_render(self, html, **kwargs):
        rendered.append(threading.get_ident())
        return real_render(self, html, **kwargs)

    monkeypatch.setattr(e1_tracking, "extract_links", spy_extract)
    monkeypatch.setattr(e1_tracking.TrackingPlan, "render", spy_render)
    _, sent, _ = await _run(session, org_id, to=("a@example.com", "b@example.com"))

    assert len(sent) == 2 and all(_tokens(m.html or "", "c") for m in sent)  # still correct
    me = threading.get_ident()
    assert extracted and rendered
    assert me not in extracted and me not in rendered


# --------------------------------------------------------------------------------- analytics


async def _engage(client, session, org_id, sent) -> None:
    await _age_sends(session, org_id)
    html = sent[0].html or ""
    await client.get(f"{TRACK}/o/{_tokens(html, 'o')[0]}", headers={"User-Agent": PHONE})
    await client.get(f"{TRACK}/c/{_tokens(html, 'c')[0]}", headers={"User-Agent": DESKTOP})
    await client.get(
        f"{TRACK}/o/{_tokens(html, 'o')[0]}", headers={"User-Agent": "python-requests/2.31"}
    )


async def test_campaign_analytics_counts_human_events_only(client, session, org_id, worker_engine):
    campaign_id, sent, _ = await _run(session, org_id, to=("a@example.com", "b@example.com"))
    # The two recipients got different tokens; engage as the first, leave the second silent.
    first = next(m for m in sent if m.to_address == "a@example.com")
    await _engage(client, session, org_id, [first])

    with as_principal(caller(org_id, [f"{K_READ}:ORG"])):
        response = await client.get(f"{CAMPAIGNS}/{campaign_id}/analytics")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recipients"] == 2 and body["delivered"] == 2
    assert body["opened"] == 1 and body["clicked"] == 1  # the python-requests open is excluded
    assert body["open_rate"] == 0.5 and body["click_rate"] == 0.5
    assert body["by_device"] == [{"device": "mobile", "opens": 1}]
    assert body["by_link"] == [{"url": SALE, "clicks": 1}]
    assert body["tracking"] == {"opens": True, "clicks": True}
    assert body["bounced"] == 0 and body["unsubscribed"] == 0 and body["complaints"] == 0
    today = datetime.now(UTC).date().isoformat()
    assert body["series"] == [{"date": today, "opens": 1, "clicks": 1}]


async def test_analytics_of_an_untracked_campaign_says_so(client, session, org_id, worker_engine):
    campaign_id, _, _ = await _run(session, org_id, opens=False, clicks=False)
    with as_principal(caller(org_id, [f"{K_READ}:ORG"])):
        body = (await client.get(f"{CAMPAIGNS}/{campaign_id}/analytics")).json()
    assert body["tracking"] == {"opens": False, "clicks": False}
    assert body["opened"] == 0 and body["open_rate"] == 0.0 and body["delivered"] == 1


async def test_an_unsubscribe_through_the_link_is_counted_on_its_campaign(
    client, session, org_id, worker_engine
):
    campaign_id, _, ids = await _run(session, org_id)
    token = services.unsubscribe_token_for_recipient(org_id, ids[0])
    assert (await client.post(f"/api/v1/engage/unsubscribe/{token}")).status_code == 200
    with as_principal(caller(org_id, [f"{K_READ}:ORG"])):
        body = (await client.get(f"{CAMPAIGNS}/{campaign_id}/analytics")).json()
    assert body["unsubscribed"] == 1


async def test_analytics_are_tenant_scoped_and_permission_gated(
    client, session, org_id, worker_engine
):
    campaign_id, sent, _ = await _run(session, org_id)
    await _engage(client, session, org_id, sent)
    stranger = uuid.uuid4()
    today = datetime.now(UTC).date()

    # Another organization's operator: the campaign does not exist for them.
    with as_principal(caller(stranger, [f"{K_READ}:ORG"])):
        assert (await client.get(f"{CAMPAIGNS}/{campaign_id}/analytics")).status_code == 404
        overview = await client.get(
            "/api/v1/engage/analytics/email/overview",
            params={"from": today.isoformat(), "to": today.isoformat()},
        )
    assert overview.status_code == 200
    assert (overview.json()["sent"], overview.json()["opened"], overview.json()["clicked"]) == (
        0,
        0,
        0,
    )

    # No permission, no data: 403 on both reads.
    with as_principal(caller(org_id, ["engage.campaign.campaign.send:ORG"])):
        assert (await client.get(f"{CAMPAIGNS}/{campaign_id}/analytics")).status_code == 403
        assert (await client.get("/api/v1/engage/analytics/email/overview")).status_code == 403

    # Read permission without the engage entitlement: refused too (payment required).
    with as_principal(principal_for(org_id, [f"{K_READ}:ORG"], entitlements=["notification"])):
        assert (await client.get(f"{CAMPAIGNS}/{campaign_id}/analytics")).status_code == 402

    # A narrower-than-ORG grant is refused rather than silently narrowed.
    with as_principal(caller(org_id, [f"{K_READ}:OWN"])):
        assert (await client.get(f"{CAMPAIGNS}/{campaign_id}/analytics")).status_code == 403


async def test_overview_folds_sends_engagement_and_totals_and_matches_the_function(
    client, session, org_id, worker_engine
):
    _, sent, _ = await _run(session, org_id)
    await _engage(client, session, org_id, sent)
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)

    async with session.begin():
        direct = await e1_analytics.email_overview(session, org_id, yesterday, today)
    assert (direct["sent"], direct["delivered"], direct["failed"]) == (1, 1, 0)
    assert (direct["opened"], direct["clicked"]) == (1, 1)
    assert [p["date"] for p in direct["series"]] == [yesterday.isoformat(), today.isoformat()]
    assert direct["series"][1] == {
        "date": today.isoformat(),
        "sent": 1,
        "delivered": 1,
        "opened": 1,
        "clicked": 1,
        "failed": 0,
    }
    assert direct["totals"] == {
        "connected_accounts": 1,
        "active_automations": 0,
        "templates": 0,
        "contacts": 1,
        "subscribed_contacts": 1,
    }
    assert {a["kind"] for a in direct["recent_activity"]} <= {
        "campaign_sent",
        "high_open_rate",
        "new_subscriber",
    }
    assert any(a["kind"] == "new_subscriber" for a in direct["recent_activity"])

    with as_principal(caller(org_id, [f"{K_READ}:ORG"])):
        response = await client.get(
            "/api/v1/engage/analytics/email/overview",
            params={"from": yesterday.isoformat(), "to": today.isoformat()},
        )
    assert response.status_code == 200, response.text
    assert response.json() == direct  # the hub's `email` block is this same dict

    # A window that ends before the send sees none of it.
    async with session.begin():
        earlier = await e1_analytics.email_overview(
            session, org_id, today - timedelta(days=9), today - timedelta(days=5)
        )
    assert (earlier["sent"], earlier["opened"], earlier["clicked"]) == (0, 0, 0)
    assert len(earlier["series"]) == 5


async def test_overview_refuses_a_backwards_or_oversized_range(client, org_id, worker_engine):
    with as_principal(caller(org_id, [f"{K_READ}:ORG"])):
        base = "/api/v1/engage/analytics/email/overview"
        backwards = await client.get(base, params={"from": "2026-09-10", "to": "2026-09-01"})
        oversized = await client.get(base, params={"from": "2024-01-01", "to": "2026-09-01"})
        malformed = await client.get(base, params={"from": "yesterday", "to": "2026-09-01"})
    assert (backwards.status_code, oversized.status_code, malformed.status_code) == (422, 422, 422)


# -------------------------------------------------------------------------- campaign flags


async def test_the_tracking_flags_default_off_and_are_settable_on_create_and_patch(
    client, session, org_id, worker_engine
):
    sender_id = await _seed_identity(session, org_id)
    with as_principal(caller(org_id, [f"{K_WRITE}:ORG", f"{K_READ}:ORG"])):
        plain = await client.post(
            CAMPAIGNS, json={"name": "Plain", "sender_id": str(sender_id), "subject": "s"}
        )
        assert plain.status_code == 201, plain.text
        assert (plain.json()["track_opens"], plain.json()["track_clicks"]) == (False, False)

        tracked = await client.post(
            CAMPAIGNS,
            json={
                "name": "Tracked",
                "sender_id": str(sender_id),
                "subject": "s",
                "track_opens": True,
            },
        )
        assert (tracked.json()["track_opens"], tracked.json()["track_clicks"]) == (True, False)

        patched = await client.patch(
            f"{CAMPAIGNS}/{plain.json()['id']}", json={"track_clicks": True}
        )
        assert patched.status_code == 200, patched.text
        assert (patched.json()["track_opens"], patched.json()["track_clicks"]) == (False, True)
        off = await client.patch(f"{CAMPAIGNS}/{tracked.json()['id']}", json={"track_opens": False})
        assert off.json()["track_opens"] is False

        unknown = await client.post(
            CAMPAIGNS,
            json={"name": "x", "sender_id": str(sender_id), "track_everything": True},
        )
        assert unknown.status_code == 422
