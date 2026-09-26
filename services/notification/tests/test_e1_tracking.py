"""Signed tracking tokens, machine classification and link rewriting (slice E1). Pure functions:
no database, no network."""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs

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


# ------------------------------------------------------------------- hostile HTML stays linear

#: Repeats per pattern. Small enough that the old quadratic pattern needs seconds to chew through
#: even the mildest of these (`"<a " * 12000` took ~4 s, the unclosed-tag shapes 7 to 14 s) and
#: the linear one needs a few milliseconds. The body limit is 500 KB, so the real exposure was
#: minutes per pass, once in `extract_links` and once per recipient in `render`.
_REPEATS = 12000
_BUDGET_SECONDS = 1.0

_PATHOLOGICAL = {
    "unclosed tags": "<a " * _REPEATS,
    "unclosed tags with an attribute": '<a x="' * _REPEATS,
    "unclosed href, no quote": "<a href=" * _REPEATS,
    "unclosed href, open quote": '<a href="' * _REPEATS,
    "single-quoted attributes": "<a b=' " * _REPEATS,
    "one tag, many hrefs": "<a " + "href=' " * _REPEATS,
    "one tag, long whitespace run": "<a href" + " " * (3 * _REPEATS),
    "one tag, long whitespace after the equals": "<a href=" + " " * (3 * _REPEATS),
    "deeply nested quotes": '<a href="' + "'\"" * (5 * _REPEATS),
    "tags closed late": "<a " * _REPEATS + ">",
    # The values are unescaped before they are judged, so one enormous one is worth a check too.
    "one huge href of entities": '<a href="' + "&amp;" * (10 * _REPEATS) + '">',
    "one huge href of bare ampersands": '<a href="' + "&" * (30 * _REPEATS) + '">',
    "many entity-shaped hrefs": ("<a href='&" + "b" * 31 + "'>") * _REPEATS,
}


@pytest.mark.parametrize("shape", list(_PATHOLOGICAL))
def test_pathological_html_is_extracted_and_rendered_in_linear_time(shape: str) -> None:
    """A tenant author controls the body, and the send path parses it for every recipient. The
    link pattern used to rescan the rest of the document from every unclosed `<a`, which is
    quadratic: one 120 KB body froze the process for ~20 s a pass."""
    html = _PATHOLOGICAL[shape]
    plan = t.TrackingPlan(
        key=KEY, base="https://api.example.com/api/v1", link_ids={}, opens=True, clicks=True
    )

    started = time.perf_counter()
    t.extract_links(html)
    plan.render(html, organization_id=ORG, recipient_id=RECIPIENT)
    elapsed = time.perf_counter() - started

    assert elapsed < _BUDGET_SECONDS, f"{shape}: {elapsed:.2f}s"


def test_a_body_at_the_size_limit_of_ordinary_anchors_is_still_fast() -> None:
    """The bound is not bought by giving up on real mail: thousands of links in a 500 KB body."""
    html = "".join(
        f'<a href="https://shop.example.com/p/{n}" class="x">item {n}</a>\n' for n in range(5000)
    )
    assert len(html) < 500_000
    links = {url: uuid.uuid4() for url in t.extract_links(html)}
    plan = t.TrackingPlan(
        key=KEY, base="https://api.example.com/api/v1", link_ids=links, opens=True, clicks=True
    )

    started = time.perf_counter()
    out = plan.render(html, organization_id=ORG, recipient_id=RECIPIENT)
    assert time.perf_counter() - started < _BUDGET_SECONDS
    assert out.count("/engage/t/c/") == len(links) == t.MAX_LINKS_PER_CAMPAIGN


def test_an_anchor_with_a_raw_angle_bracket_before_its_href_is_left_untracked() -> None:
    """The price of the linear pattern, stated: such a tag is neither registered nor rewritten,
    and the two passes agree, so a link is never rewritten without being registered."""
    html = '<a title="1 < 2" href="https://shop.example.com/x">x</a> <a href="https://ok.example.com/">ok</a>'
    assert t.extract_links(html) == ["https://ok.example.com/"]
    plan = t.TrackingPlan(
        key=KEY,
        base="https://api.example.com/api/v1",
        link_ids={"https://ok.example.com/": uuid.uuid4()},
        opens=False,
        clicks=True,
    )
    out = plan.render(html, organization_id=ORG, recipient_id=RECIPIENT)
    assert 'href="https://shop.example.com/x"' in out
    assert out.count("/engage/t/c/") == 1


# ------------------------------------------------------------------------ the tracking key


def _configured(monkeypatch: pytest.MonkeyPatch, **update: object) -> None:
    cfg = t.settings().model_copy(update=update)
    monkeypatch.setattr(t, "settings", lambda: cfg)


def test_an_explicit_tracking_secret_is_the_key_when_it_is_long_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "r" * 40
    _configured(monkeypatch, environment="production", engage_tracking_secret=SecretStr(secret))
    assert t.tracking_key() == secret.encode()


@pytest.mark.parametrize("secret", ["short", "x" * (t.MIN_TRACKING_SECRET_LENGTH - 1)])
def test_a_short_explicit_tracking_secret_turns_tracking_off_in_every_environment(
    monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    """Refused rather than used (a guessable MAC key forges every token) and rather than swapped
    for the derived key (the operator asked for a different one)."""
    for environment in ("local", "production"):
        _configured(monkeypatch, environment=environment, engage_tracking_secret=SecretStr(secret))
        assert t.tracking_key() is None


def test_the_shortest_allowed_explicit_secret_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "y" * t.MIN_TRACKING_SECRET_LENGTH
    _configured(monkeypatch, environment="production", engage_tracking_secret=SecretStr(secret))
    assert t.tracking_key() == secret.encode()


@pytest.mark.parametrize("prefix", ["dev-only-", "ci-only-"])
def test_a_placeholder_tracking_secret_is_refused_only_in_a_real_environment(
    monkeypatch: pytest.MonkeyPatch, prefix: str
) -> None:
    secret = prefix + "z" * 40
    _configured(monkeypatch, environment="production", engage_tracking_secret=SecretStr(secret))
    assert t.tracking_key() is None
    _configured(monkeypatch, environment="local", engage_tracking_secret=SecretStr(secret))
    assert t.tracking_key() == secret.encode()


def test_with_no_explicit_secret_the_key_is_derived_from_the_unsubscribe_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configured(monkeypatch, environment="local", engage_tracking_secret=SecretStr(""))
    derived = t.tracking_key()
    assert derived is not None and len(derived) == 32


def test_a_refused_explicit_secret_is_named_in_the_log_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The routes ask for the key on every request, and they are public: logging each refusal
    would hand anyone holding a tracking URL a way to fill the log."""
    monkeypatch.setattr(t, "_REFUSALS_LOGGED", set())
    with capture_logs() as logs:
        _configured(monkeypatch, environment="local", engage_tracking_secret=SecretStr("hunter2"))
        assert t.tracking_key() is None
        assert t.tracking_key() is None
        _configured(
            monkeypatch,
            environment="production",
            engage_tracking_secret=SecretStr("ci-only-" + "z" * 40),
        )
        assert t.tracking_key() is None
        assert t.tracking_key() is None
    assert [entry["event"] for entry in logs] == [
        "engage.tracking.secret_too_short",
        "engage.tracking.secret_is_placeholder",
    ]
    assert "hunter2" not in repr(logs)  # the value itself is never logged
