"""Signed tracking tokens, machine classification and link rewriting (slice E1). Pure functions:
no database, no network."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app import e1_tracking as t

KEY = b"k" * 32
ORG = uuid.uuid4()
RECIPIENT = uuid.uuid4()
LINK = uuid.uuid4()


def _click(**overrides) -> str:
    args = {"organization_id": ORG, "recipient_id": RECIPIENT, "link_id": LINK}
    return t.make_token("click", KEY, **{**args, **overrides})


def test_a_click_token_round_trips_and_carries_only_opaque_ids() -> None:
    token = _click()
    claims = t.verify_token("click", token, KEY)
    assert claims == t.TokenClaims(ORG, RECIPIENT, LINK)
    assert "@" not in token and "http" not in token


def test_an_open_token_has_no_link() -> None:
    token = t.make_token("open", KEY, organization_id=ORG, recipient_id=RECIPIENT)
    assert t.verify_token("open", token, KEY) == t.TokenClaims(ORG, RECIPIENT, None)


def test_a_tampered_signature_or_payload_is_refused() -> None:
    token = _click()
    body, mac = token.split(".")
    flipped_mac = mac[:-2] + ("AA" if not mac.endswith("AA") else "BB")
    assert t.verify_token("click", f"{body}.{flipped_mac}", KEY) is None
    other = _click(recipient_id=uuid.uuid4()).split(".")[0]
    assert t.verify_token("click", f"{other}.{mac}", KEY) is None  # payload swapped, mac kept
    assert t.verify_token("click", body, KEY) is None
    assert t.verify_token("click", f"{body}.{mac}.extra", KEY) is None


def test_a_token_signed_with_another_key_is_refused() -> None:
    assert t.verify_token("click", _click(), b"z" * 32) is None
    assert t.verify_token("click", _click(), None) is None


def test_an_open_token_cannot_be_used_as_a_click_token_and_back() -> None:
    open_token = t.make_token("open", KEY, organization_id=ORG, recipient_id=RECIPIENT)
    assert t.verify_token("click", open_token, KEY) is None
    assert t.verify_token("open", _click(), KEY) is None


@pytest.mark.parametrize("junk", ["", ".", "a.b", "!!!.???", "x" * 500, "AAAA.AAAA", "é.é"])
def test_garbage_never_verifies_and_never_raises(junk: str) -> None:
    assert t.verify_token("click", junk, KEY) is None
    assert t.verify_token("open", junk, KEY) is None


@pytest.mark.parametrize(
    ("agent", "klass", "machine"),
    [
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            "desktop",
            False,
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
            "Version/17.0 Mobile/15E148 Safari/604.1",
            "mobile",
            False,
        ),
        (
            "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 "
            "Safari/604.1",
            "tablet",
            False,
        ),
        ("Mozilla/5.0 (Linux; Android 14) Chrome/120 Mobile Safari/537.36", "mobile", False),
        ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "bot", True),
        ("Barracuda Sentinel (EquenceAI)", "bot", True),
        ("python-requests/2.31", "bot", True),
        ("Mozilla/5.0 (Windows NT 10.0) Microsoft Office/16.0 SafeLinks", "bot", True),
        ("", "unknown", False),
        (None, "unknown", False),
    ],
)
def test_user_agents_are_classified_and_scanners_are_machines(
    agent: str | None, klass: str, machine: bool
) -> None:
    assert t.classify(agent) == (klass, machine)


def test_apple_mail_privacy_proxy_is_a_machine_by_address_and_by_agent_shape() -> None:
    safari = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17 Safari/605"
    )
    assert t.classify(safari, client_ip="17.58.1.2") == ("desktop", True)
    assert t.classify(safari, client_ip="203.0.113.9") == ("desktop", False)
    assert t.classify(safari, client_ip="17.58.1.2, 10.0.0.1")[1] is True
    mimic = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)"
    )
    assert t.classify(mimic, client_ip="203.0.113.9")[1] is True
    assert t.classify(safari, client_ip="not-an-ip")[1] is False


def test_an_event_seconds_after_the_provider_accepted_the_mail_is_a_machine() -> None:
    chrome = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120 Safari/537.36"
    now = datetime.now(UTC)
    assert t.classify(chrome, sent_at=now - timedelta(seconds=1))[1] is True
    assert t.classify(chrome, sent_at=now - timedelta(minutes=5))[1] is False


HTML = (
    "<html><body><p>"
    '<a href="https://shop.example.com/sale?a=1&amp;b=2">Sale</a> '
    "<a class='x' href='https://shop.example.com/sale?a=1&amp;b=2'>Sale again</a> "
    '<a href="https://other.example.org/page">Other</a> '
    '<a href="mailto:help@example.com">Mail</a> '
    '<a href="tel:+911234567890">Call</a> '
    '<a href="https://example.com/unsubscribe/abc">Stop</a> '
    '<a href="https://api.example.com/v1/engage/unsubscribe/tok">Ours</a> '
    '<a href="/relative">Relative</a> <a href="#top">Top</a> '
    '<a href="javascript:alert(1)">JS</a> <a href="ftp://x.example.com/f">FTP</a> '
    '<a href="//evil.example.com/x">Scheme-relative</a>'
    "</p></body></html>"
)


def test_only_absolute_http_urls_that_are_not_unsubscribe_links_are_extracted() -> None:
    assert t.extract_links(HTML) == [
        "https://shop.example.com/sale?a=1&b=2",
        "https://other.example.org/page",
    ]


def _plan(*, opens: bool, clicks: bool) -> t.TrackingPlan:
    links = {url: uuid.uuid4() for url in t.extract_links(HTML)}
    return t.TrackingPlan(
        key=KEY, base="https://api.example.com/api/v1", link_ids=links, opens=opens, clicks=clicks
    )


def test_rewriting_touches_only_trackable_links_and_leaves_unsubscribe_and_mailto_alone() -> None:
    plan = _plan(opens=True, clicks=True)
    out = plan.render(HTML, organization_id=ORG, recipient_id=RECIPIENT)
    assert out.count("https://api.example.com/api/v1/engage/t/c/") == 3
    assert "shop.example.com" not in out and "other.example.org" not in out
    for untouched in (
        'href="mailto:help@example.com"',
        'href="tel:+911234567890"',
        'href="https://example.com/unsubscribe/abc"',
        'href="https://api.example.com/v1/engage/unsubscribe/tok"',
        'href="/relative"',
        'href="#top"',
        'href="javascript:alert(1)"',
        'href="ftp://x.example.com/f"',
        'href="//evil.example.com/x"',
    ):
        assert untouched in out
    assert "class='x'" in out  # other attributes and the quote style survive
    pixel_at = out.index("/engage/t/o/")
    assert pixel_at < out.index("</body>")
    assert out.count("/engage/t/o/") == 1


def test_each_rewritten_link_verifies_and_names_this_recipient() -> None:
    plan = _plan(opens=False, clicks=True)
    out = plan.render(HTML, organization_id=ORG, recipient_id=RECIPIENT)
    tokens = re.findall(r"/engage/t/c/([^\"']+)", out)
    assert len(tokens) == 3
    for token in tokens:
        claims = t.verify_token("click", token, KEY)
        assert claims is not None
        assert claims.organization_id == ORG and claims.recipient_id == RECIPIENT
        assert claims.link_id in plan.link_ids.values()
    assert "/engage/t/o/" not in out  # clicks only: no pixel


def test_opens_only_adds_a_pixel_and_leaves_every_link_as_written() -> None:
    plan = _plan(opens=True, clicks=False)
    out = plan.render(HTML, organization_id=ORG, recipient_id=RECIPIENT)
    assert "/engage/t/c/" not in out
    assert 'href="https://shop.example.com/sale?a=1&amp;b=2"' in out
    assert out.count("/engage/t/o/") == 1


def test_a_body_without_a_body_tag_gets_the_pixel_appended() -> None:
    plan = _plan(opens=True, clicks=False)
    out = plan.render("<p>hi</p>", organization_id=ORG, recipient_id=RECIPIENT)
    assert out.startswith("<p>hi</p><img ")


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,x",
        "ftp://x.example.com",
        "//evil.example.com",
        "/relative",
        "mailto:a@b.co",
        "https://example.com/Unsubscribe",
        "https://example.com/a b",
        "https://example.com/a\r\nLocation: https://evil.example.com",
        "https:///nohost",
        "",
    ],
)
def test_urls_that_must_never_be_tracked_or_redirected_to(url: str) -> None:
    assert t.is_trackable_url(url) is False


def test_a_plain_https_url_is_trackable() -> None:
    assert t.is_trackable_url("https://shop.example.com/a?b=c#d") is True
    assert t.is_trackable_url("http://shop.example.com/") is True
