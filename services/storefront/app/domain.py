"""Pure rules for the store's identity and its serviceability arithmetic. No I/O, no session,
no settings, no clock of its own.

Five things live here, and each is here because it is a real rule rather than a line of glue:

0. **The store's identity** (ADR-0011) — brand text is trimmed, bounded and refused when it is
   blank or unrenderable; a custom domain is parsed into a hostname or refused by name; the slug
   is immutable; and `published` is reached only through a named transition. Note what is
   *absent*: nothing here escapes anything for HTML, and nothing here verifies that a merchant
   owns a domain. The first belongs to the renderer, the second does not exist.

0b. **The host a buyer arrives on** (ADR-0012) — an untrusted string from the open internet is
   normalised to one comparable form or refused, a platform host is split back into its slug,
   the `www` pair is derived, and one of a store's two addresses is named canonical. All of it
   pure, because it runs before any query and decides what the cache key is allowed to be.

1. **Postal-code normalisation and validation.** One country, one validator, one entry in the
   map. Not generalised — a generalisation with one use case is a guess (ADR-0010 §2).
2. **The promise.** `DATE_OFFSET` resolves to a date *in the store's timezone*, because a buyer
   in Singapore looking at a Mumbai store must see the Mumbai answer (ADR-0000 "Also settled").
3. **The CSV parse.** Duplicate detection happens here, before any SQL, so a 19,000-row file is
   refused with line numbers rather than applied last-wins (ADR-0010 §9).
4. **The `SLOTTED` refusal**, as a message that names slice 2 rather than a bare 422.

Nothing here decides *whether* a postal code is serviceable. That is one query and it lives in
`selectors.resolve_serviceability`, which is the only place an answer is ever computed.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import idna

from app.models import (
    ENQUIRY_STATUSES,
    LOGO_TEXT_LENGTH,
    MAX_THEME_DOCUMENT_BYTES,
    MAX_THEME_PAGES,
    POSTAL_CODE_LENGTH,
    STORE_NAME_LENGTH,
    TAGLINE_LENGTH,
    THEME_MODES,
)
from platform_core.errors import ConflictProblem, ValidationProblem

#: There is one country, so there is one entry. The column is already wide enough for a UK or
#: Canadian code, so the second country is a line in this dict and a migration for nobody
#: (ADR-0010 §2). India's PIN is six digits and never starts with zero.
_POSTAL_CODE_PATTERNS: dict[str, re.Pattern[str]] = {
    "IN": re.compile(r"^[1-9][0-9]{5}$"),
}

#: Slice 1 has no `country` column on `storefronts` — adding one that every row would set to the
#: same value, for a validator with one entry, is the speculative column ADR-0010 §2 refuses.
#: When a second country's storefront appears, the column and this parameter arrive together.
DEFAULT_COUNTRY = "IN"

#: Bounded so a merchant's paste cannot become a 400 MB request the gateway has already
#: buffered. ~19,000 rows is the whole of India's PIN space; 50,000 is generous headroom and is
#: still far inside the gateway's 2 MiB default body cap (ADR-0010 §9's size arithmetic).
MAX_IMPORT_ROWS = 50_000

#: The textarea path, not the CSV one — what a merchant pastes into the zone editor.
MAX_POSTAL_CODES_PER_ZONE = 20_000


class Rejected(ValidationProblem):
    """Refused by a rule, with a message that is safe to show a caller verbatim.

    A value, not a surprise: `code` is a stable string the frontend branches on, `message` is
    already human, and `lines` names the offending rows of an import so a merchant can fix the
    file instead of guessing. Nothing here ever echoes back more than the input it is refusing.

    **It subclasses the platform's 422 rather than being translated in each route**, and that is
    the one concession this module makes to the transport. The reason is specific: the anonymous
    serviceability route must contain **no exception handling at all** (ADR-0010 §13 — a check
    that cannot complete must never render as "we do not deliver here"), and a domain error that
    needs a `try/except` to become a response would put the first `except` in the platform on
    precisely the path where one must never appear. A rejection *is* a 422; making it one removes
    the translation step instead of hiding it.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: str = "postal_code",
        lines: list[int] | None = None,
    ) -> None:
        error: dict[str, object] = {"field": field, "code": code, "message": message}
        if lines:
            error["lines"] = lines
        super().__init__(message, errors=[error])
        self.code = code
        self.message = message
        self.lines = lines or []


class Conflicted(ConflictProblem):
    """Refused because something else already holds this value, or because the row's own state
    forbids the change. A **409 with a name**, which is the whole point.

    The platform's `IntegrityError` handler already turns a unique-constraint violation into a
    409 — deliberately anonymous, because echoing a constraint name leaks schema detail to
    whoever is probing. That is right for an unexpected collision and useless for an expected
    one: "that conflicts with something that already exists" does not tell a merchant *which*
    field to change, and a client cannot branch on it. So the two collisions this service can
    actually have — a slug another organization holds, a custom domain another organization has
    claimed — are caught by name and re-raised with a stable `code`, and nothing about the other
    tenant is disclosed beyond the fact that the value is taken.

    Same shape as `Rejected` on the wire: `errors[0].code` is the stable string, `detail` is
    already a sentence. `problem_type` is set per instance so the `type` URI is distinct too.
    """

    def __init__(self, code: str, message: str, *, field: str) -> None:
        super().__init__(message, errors=[{"field": field, "code": code, "message": message}])
        self.code = code
        self.field = field
        self.problem_type = code.replace("_", "-")


# --------------------------------------------------------------------- the store's identity

#: Whitespace a merchant can paste but nobody can see. Collapsed to one ordinary space so
#: "Verma   Traders" and "Verma Traders" are the same shop sign, and a pasted non-breaking space
#: does not become a name that sorts and searches differently from the one on screen.
_WHITESPACE = re.compile(r"\s+")

#: C0 and C1 control characters, and the Unicode bidirectional overrides — checked **after**
#: whitespace has been collapsed, so tabs and newlines are already gone. A merchant pasting a
#: spreadsheet cell gets their name; a bidi override, which is a display-spoofing trick, is
#: refused, and so is anything else that cannot be drawn.
#:
#: **This is not an HTML-escaping scheme and must not grow into one.** Output encoding belongs to
#: whatever renders the page — the front end owns it, and doing it here would double-escape the
#: day anything else reads this column (ADR-0011 §6).
_UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]")

_BRAND_LIMITS = {
    "name": STORE_NAME_LENGTH,
    "tagline": TAGLINE_LENGTH,
    "logo_text": LOGO_TEXT_LENGTH,
}

_REQUIRED_MESSAGES = {
    "name": "Give the store a name — it is what a buyer sees, and it is not the theme's.",
    "tagline": "Enter a tagline, or leave it empty.",
    "logo_text": "Enter the logo text, or leave it empty.",
}


def collapse_whitespace(raw: str) -> str:
    """Trim the ends and collapse every run of whitespace to one ordinary space.

    Shared with `schemas`, which applies it **before** pydantic checks the length: a name that
    fits but was pasted with a trailing space is a name that fits, and refusing it with "at most
    80 characters" would be arguing with a merchant who is looking at 78 letters.
    """
    return _WHITESPACE.sub(" ", raw).strip()


def clean_brand_text(raw: str | None, *, field: str, required: bool = False) -> str | None:
    """Trim, collapse whitespace, refuse the unrenderable, and enforce the length.

    **Empty after trimming is not a value.** For `name` it is a refusal — a store with a blank
    name is a page with a blank header, and a row every admin surface has to invent a label for.
    For `tagline` and `logo_text` it is `None`: a merchant who clears the box means "there isn't
    one", and storing `''` would make "cleared" and "never set" two states that render
    identically and compare differently.

    The limit is checked **after** trimming, so trailing spaces cannot push a name that fits over
    the edge, and it is the column's own limit rather than a second number — a string that passes
    here cannot be truncated by the database.
    """
    limit = _BRAND_LIMITS[field]
    if raw is None:
        if required:
            raise Rejected(f"{field}_required", _REQUIRED_MESSAGES[field], field=field)
        return None
    value = collapse_whitespace(raw)
    if _UNPRINTABLE.search(value):
        raise Rejected(
            f"{field}_invalid",
            "That contains characters that cannot be shown on a page. Remove them and try again.",
            field=field,
        )
    if not value:
        if required:
            raise Rejected(f"{field}_required", _REQUIRED_MESSAGES[field], field=field)
        return None
    if len(value) > limit:
        raise Rejected(
            f"{field}_too_long",
            f"That is {len(value)} characters; the limit is {limit}.",
            field=field,
        )
    return value


#: A hostname label: 1 to 63 characters, letters, digits and hyphens, never starting or ending
#: with a hyphen. The same rule the column's check constraint states.
_DOMAIN_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

#: RFC 1035's ceiling for a fully-qualified name. The column is two characters wider, so a later
#: decision about internationalised domains is a change here rather than an ALTER.
MAX_CUSTOM_DOMAIN_LENGTH = 253

#: Characters that mean the merchant pasted a URL rather than typed a hostname, plus the two
#: (`*`, `_`) that mean they typed something a Host header will never carry.
_NOT_IN_A_HOSTNAME = "/\\ \t@:?#*_"

#: Hostnames this platform will not let a merchant claim, **because nothing verifies ownership**
#: (ADR-0011 §5). A claim is stored on the word of whoever typed it, so the one claim that must
#: not be storable is the platform's own: an unverified row naming `api.opsgrids.com` is a squat
#: that becomes live routing the day somebody builds host-based routing and trusts this column.
#: Everything else — including another merchant's domain — is bounded by uniqueness and settled
#: by verification, which is the thing that does not exist yet.
RESERVED_DOMAIN_SUFFIXES = ("opsgrids.com", "opsgrids.dev", "opsgrids.local", "localhost")


def normalise_custom_domain(raw: str | None) -> str | None:
    """A hostname, lower-cased, or `None`. Raises `Rejected` on anything else.

    **This validates a shape and nothing more.** It does not resolve the name, does not ask DNS
    whether it exists, and above all does not establish that the merchant owns it — no code in
    this platform does, and none is planned in this slice. What a merchant gets by setting this
    is a stored intention (ADR-0011 §5).

    Refused deliberately: a scheme (`https://…`), a path, a port, credentials, a wildcard, a
    single label (`localhost`), an IP address, and any non-ASCII character — the last with a
    message naming punycode, because silently accepting `köln.de` and storing a form no Host
    header will ever carry is worse than asking for `xn--kln-sna.de`.
    """
    if raw is None:
        return None
    value = raw.strip().lower().rstrip(".")
    if not value:
        return None
    if len(value) > MAX_CUSTOM_DOMAIN_LENGTH:
        raise Rejected(
            "custom_domain_too_long",
            f"A domain name is at most {MAX_CUSTOM_DOMAIN_LENGTH} characters.",
            field="custom_domain",
        )
    if not value.isascii():
        raise Rejected(
            "custom_domain_invalid",
            "Enter the domain in its ASCII (punycode) form — for example xn--kln-sna.de rather "
            "than köln.de. That is the form a browser actually sends.",
            field="custom_domain",
        )
    if any(ch in value for ch in _NOT_IN_A_HOSTNAME):
        raise Rejected(
            "custom_domain_invalid",
            "Enter the domain on its own — no https://, no path, no port, no wildcard. For "
            "example shop.example.com.",
            field="custom_domain",
        )
    labels = value.split(".")
    if len(labels) < 2 or not all(_DOMAIN_LABEL.fullmatch(label) for label in labels):
        raise Rejected(
            "custom_domain_invalid",
            "That does not look like a domain name. Use at least two parts, letters, digits and "
            "hyphens only — for example shop.example.com.",
            field="custom_domain",
        )
    if labels[-1].isdigit():
        raise Rejected(
            "custom_domain_invalid",
            "Enter a domain name rather than an IP address.",
            field="custom_domain",
        )
    if any(value == suffix or value.endswith(f".{suffix}") for suffix in RESERVED_DOMAIN_SUFFIXES):
        raise Rejected(
            "custom_domain_reserved",
            "That domain belongs to the platform and cannot be claimed. Use a domain you control.",
            field="custom_domain",
        )
    return value


# --------------------------------------------------------------------- the host a buyer arrives on

#: The `custom_domain_status` values at which a host is answered for (ADR-0012 §2).
#:
#: Only `verified` is reachable today: ADR-0011's check constraint allows `unverified` and
#: `verified`, and the state machine that mints `live` and `failing` is the rest of ADR-0012
#: slice 1. They are listed anyway, on ADR-0011's own precedent for putting `verified` in the
#: constraint before anything could set it — the day the state machine lands, this tuple is
#: already the right one and nobody has to remember to widen a filter that fails *quietly*.
#:
#: `unverified` is deliberately absent, and it is the important entry in this list: a claim is
#: stored on the word of whoever typed it, so serving a host on an unverified claim is how one
#: merchant takes another's traffic.
SERVING_DOMAIN_STATUSES = ("verified", "live", "failing")

#: The one status at which the merchant's own domain becomes canonical (ADR-0012 §8). A domain
#: that is `verified` but not yet routed, or `failing`, leaves the platform host canonical — a
#: canonical pointer at a name that does not currently answer is a redirect into a hole, and the
#: platform host is the fallback that makes a DNS mistake recoverable.
CANONICAL_DOMAIN_STATUS = "live"

#: RFC 1035's ceiling, applied to the *request* host as well as to a claim.
MAX_HOST_LENGTH = MAX_CUSTOM_DOMAIN_LENGTH


def _bad_host(code: str, message: str) -> Rejected:
    return Rejected(code, message, field="host")


def normalise_request_host(raw: str) -> str:
    """A hostname from the open internet, reduced to the one form everything else compares.

    ────────────────────────────────────────────────────────────────────────────────────────
    **This is the only untrusted string on this path, and it becomes a cache key.** Everything
    below is about that: the value that leaves here is lower-cased ASCII, at most
    `MAX_HOST_LENGTH` characters, at least two labels, and syntactically a hostname — so a
    caller cannot mint a Redis key of arbitrary length or arbitrary shape, only one of at most
    253 bounded bytes. A string that does not survive this is a **422 and never reaches Redis
    or Postgres at all**, which is what keeps the cost of garbage at the boundary.
    ────────────────────────────────────────────────────────────────────────────────────────

    The steps are ADR-0012 §2's, in order: lower-case, strip the port, strip the trailing dot,
    U-label → A-label. Each one exists because a browser really sends that form:
    `Host` is case-insensitive, a non-default port is appended to it, a fully-qualified name
    may carry a root dot, and an internationalised name is carried as punycode.

    **Refused, with a 422 rather than a 404** — "that is not a hostname" and "nothing is
    published there" are different facts, exactly as a malformed postal code is distinct from an
    unserved one (ADR-0010 §4):

    * a scheme, a path, credentials, a wildcard or whitespace — the marks of a pasted URL;
    * a port that is not a number, and any bracketed or bare IPv6 literal;
    * an IPv4 address, or anything whose last label is all digits — no store is reached by
      address, and the edge issues no certificate for one;
    * a single label (`localhost`) — it cannot be a store's address and cannot be claimed;
    * anything `idna` refuses: an over-long label, an empty label, a leading or trailing hyphen,
      a codepoint that has no A-label.

    It does **not** refuse a reserved suffix. `RESERVED_DOMAIN_SUFFIXES` governs what a merchant
    may *claim*; `<slug>.stores.opsgrids.com` is the platform's own host and is the single most
    common value this function will ever see.
    """
    value = raw.strip()
    if not value:
        raise _bad_host("host_required", "Tell us which address you are on.")
    if len(value) > MAX_HOST_LENGTH:
        raise _bad_host("host_too_long", f"A hostname is at most {MAX_HOST_LENGTH} characters.")

    value = value.lower()
    if value.startswith("["):
        # `[::1]:443`. Split before the port rule, because an IPv6 literal is all colons and
        # would otherwise be read as a host with a nonsense port.
        raise _bad_host("host_invalid", "That is an IP address rather than a store's address.")
    if ":" in value:
        value, _, port = value.rpartition(":")
        if not port.isdigit() or not value:
            raise _bad_host("host_invalid", "That is not a hostname.")
    if value.endswith("."):
        value = value[:-1]  # the root dot a fully-qualified name may carry

    try:
        # `uts46=True` folds case and width the same way a browser does before it resolves.
        # Everything malformed lands here rather than in a hand-written regex: label length,
        # empty labels, hyphens at a label's edge and codepoints with no A-label are one
        # library's job, and a second opinion on any of them is a disagreement with DNS.
        host = idna.encode(value, uts46=True).decode("ascii")
    except (idna.IDNAError, UnicodeError) as exc:
        raise _bad_host("host_invalid", "That is not a hostname.") from exc

    if len(host) > MAX_HOST_LENGTH:
        # Punycode is longer than what it encodes, so the bound is re-checked on the A-label.
        raise _bad_host("host_too_long", f"A hostname is at most {MAX_HOST_LENGTH} characters.")
    labels = host.split(".")
    if len(labels) < 2:
        raise _bad_host("host_invalid", "That is not a hostname.")
    if labels[-1].isdigit():
        raise _bad_host("host_invalid", "That is an IP address rather than a store's address.")
    return host


def platform_host_for(slug: str, *, suffix: str) -> str:
    """`greenleaf` → `greenleaf.stores.opsgrids.com`. **Never retires** (ADR-0012 §8).

    Links, QR codes and printed material outlive DNS changes, the merchant's own bookmark is
    usually this one, and it is the fallback when a custom domain breaks. A design that 404s it
    makes a DNS mistake unrecoverable except by us.
    """
    return f"{slug}.{suffix}"


#: The slug shape, restated from `storefronts.slug_shape`. Here because the platform host is
#: parsed before any query runs, and a label that cannot be a slug must miss without one.
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def platform_slug(host: str, *, suffix: str) -> str | None:
    """`greenleaf.stores.opsgrids.com` → `greenleaf`; any other host → `None`.

    **Exactly one label in front of the suffix**, and it has to be a slug. `a.b.stores.…` is not
    a store — reading it as the slug `a.b` would make a name no slug column can hold look like a
    lookup that merely missed, and a wildcard certificate covers one label deep anyway.
    """
    dotted = f".{suffix}"
    if not host.endswith(dotted):
        return None
    label = host[: -len(dotted)]
    return label if _SLUG.fullmatch(label) else None


def www_partner(host: str) -> str:
    """`merchant.com` ⇄ `www.merchant.com`. **Purely syntactic, and no Public Suffix List.**

    The pair is served as a unit: a claim on the apex answers on `www`, and a claim on `www`
    answers on the apex (ADR-0012 §2). A PSL is a bundled list that goes stale on somebody
    else's schedule, and the residual it would buy is small and named: verification proved
    control of the claimed name, not literally of its `www` sibling, and the two are under the
    same zone operator in every case that occurs in practice.
    """
    return host[4:] if host.startswith("www.") else f"www.{host}"


def canonical_host(
    *,
    slug: str,
    custom_domain: str | None,
    custom_domain_status: str | None,
    suffix: str,
) -> str:
    """Which of a store's two addresses the page should point at (ADR-0012 §8).

    The merchant's domain the moment it is `live` — they chose it, it is their brand, and two
    hosts serving identical content split a search ranking between them. Before that, and again
    the moment the domain stops working, the platform host: **`is_canonical` is computed from
    the live state, so a suspended or failing domain makes the platform host canonical again by
    itself.** The fallback only works if it is not also pointing at the broken name.

    Slice 1 does not redirect. This value is what the buyer bundle puts in
    `<link rel="canonical">`, and what every share link, "view your store" button and generated
    QR code renders.
    """
    if custom_domain and custom_domain_status == CANONICAL_DOMAIN_STATUS:
        return custom_domain
    return platform_host_for(slug, suffix=suffix)


# --------------------------------------------------------------------- the domain claim state


#: `secrets.token_hex(16)` — 32 hex characters, 128 bits, minted per claim (ADR-0012 §3). A
#: re-claim of the same domain string mints a **new** token, because a verification attaches to
#: the act of claiming, not to the domain — carrying an old token across a re-claim would let a
#: proof survive the claim it proved.
def mint_verification_token() -> str:
    return secrets.token_hex(16)


#: `_opsgrids-challenge.<host>` — the label, not the value. A constant rather than an inline
#: f-string wherever it is needed, so the probe and the wire format cannot drift onto two
#: spellings of the same record name.
VERIFICATION_TXT_LABEL = "_opsgrids-challenge"
#: The value's fixed prefix; the token is appended by the caller.
VERIFICATION_TXT_PREFIX = "opsgrids-domain-verification="


def verification_txt_name(host: str) -> str:
    """`merchant.com` → `_opsgrids-challenge.merchant.com` (ADR-0012 §3)."""
    return f"{VERIFICATION_TXT_LABEL}.{host}"


def verification_txt_value(token: str) -> str:
    """`<token>` → `opsgrids-domain-verification=<token>` — what the merchant is told to
    publish, and what the ownership probe looks for among a name's TXT answers."""
    return f"{VERIFICATION_TXT_PREFIX}{token}"


#: An unverified claim expires 14 days after its token was minted, and expiry releases the name
#: rather than becoming a sixth `custom_domain_status` (ADR-0012 §3). Fourteen days, not
#: twenty-four hours, because a merchant's DNS is very often in the hands of a web agency who
#: answers next week.
CLAIM_EXPIRY = dt.timedelta(days=14)

#: Once past `unverified`, every probe runs once a day. Fast enough that renewal trouble is
#: caught roughly a month before a 90-day certificate would actually expire (ADR-0012 §5); slow
#: enough that fifty merchants' worth of checks is nothing (ADR-0012 §12).
VERIFIED_CHECK_INTERVAL = dt.timedelta(days=1)

#: §6's backoff while a claim has never been proved, read as cumulative stage boundaries: the
#: ADR states each stage's own *duration* ("every 5 minutes for an hour"), and the boundary
#: below is the sum of every stage before it, so the schedule only ever relaxes and reaches its
#: hourly floor comfortably inside the claim's first day — long before the 14-day expiry.
#: `(elapsed upper bound, interval at that stage)`.
_UNVERIFIED_BACKOFF: tuple[tuple[dt.timedelta, dt.timedelta], ...] = (
    (dt.timedelta(minutes=15), dt.timedelta(seconds=60)),
    (dt.timedelta(hours=1, minutes=15), dt.timedelta(minutes=5)),
    (dt.timedelta(hours=25, minutes=15), dt.timedelta(minutes=30)),
)
#: The floor the backoff reaches and stays at until the claim expires.
_UNVERIFIED_HOURLY = dt.timedelta(hours=1)


def unverified_check_interval(elapsed_since_claim: dt.timedelta) -> dt.timedelta:
    """How long until the next ownership check, for a claim that has never been proved.

    `elapsed_since_claim` is `now - token_issued_at`. Monotonically non-decreasing by
    construction — a later stage's interval is never shorter than an earlier one's — which is
    what makes "the schedule only ever relaxes" a property a test can hold this function to
    rather than a sentence in a docstring.
    """
    for bound, interval in _UNVERIFIED_BACKOFF:
        if elapsed_since_claim < bound:
            return interval
    return _UNVERIFIED_HOURLY


def claim_expires_at(token_issued_at: dt.datetime) -> dt.datetime:
    """`token_issued_at + CLAIM_EXPIRY` — the value stored on the claim row at mint time."""
    return token_issued_at + CLAIM_EXPIRY


def is_claim_expired(expires_at: dt.datetime, *, now: dt.datetime) -> bool:
    """Only meaningful for a claim still `unverified` — once verified, `claim_expires_at`
    stops being read (ADR-0012 §9: it applies "while `unverified`")."""
    return now >= expires_at


#: Seven consecutive **daily** failures end a live domain (ADR-0012 §6) — generous on purpose:
#: a DNS provider's bad day must not take a merchant's shop off the air.
SUSPEND_AFTER_CONSECUTIVE_FAILURES = 7


def promote_from_unverified(*, ownership_ok: bool) -> str:
    """`unverified` moves to `verified` the instant the ownership probe succeeds, and nothing
    else moves it (ADR-0012 §6). Routing and the certificate are not even probed yet while
    `unverified` — the certificate cannot be authorized before ownership is proved, and a
    routing check that cannot yet matter tells a merchant nothing they can act on.
    """
    return "verified" if ownership_ok else "unverified"


@dataclass(frozen=True, slots=True)
class ClaimAdvance:
    """What one probe pass decided for a claim already past `unverified`. `status` is the new
    `custom_domain_status`; `consecutive_failures` is the new counter, reset to zero on every
    full recovery."""

    status: str
    consecutive_failures: int


def advance_verified_or_later(
    *,
    store_published: bool,
    ownership_ok: bool,
    routing_ok: bool,
    certificate_ok: bool,
    consecutive_failures: int,
) -> ClaimAdvance:
    """The state machine for `verified` / `live` / `failing` / `suspended` (ADR-0012 §6, §7).

    **An unpublished store suspends immediately, independent of the failure count.**
    ADR-0011's `unpublish` is the manual kill switch §7 names and the one already-working
    answer to a lapsed entitlement — a merchant who switched their own shop off must not wait
    seven days for the domain to follow it.

    **Every other cause of failure gets the seven-day tolerance.** All three probes have to
    succeed for the domain to be `live`; any one failing is `failing` until either it recovers
    or the count reaches `SUSPEND_AFTER_CONSECUTIVE_FAILURES`. Recovery resets the counter to
    zero — a domain that has been failing for six days and then answers cleanly is not one
    failure away from suspension the next time something hiccups.
    """
    if not store_published:
        return ClaimAdvance(status="suspended", consecutive_failures=consecutive_failures)

    if ownership_ok and routing_ok and certificate_ok:
        return ClaimAdvance(status="live", consecutive_failures=0)

    failures = consecutive_failures + 1
    if failures >= SUSPEND_AFTER_CONSECUTIVE_FAILURES:
        return ClaimAdvance(status="suspended", consecutive_failures=failures)
    return ClaimAdvance(status="failing", consecutive_failures=failures)


#: `storefront.domain.*` per ADR-0012 §15, keyed by the status just **entered**. Fired only on
#: an actual change — see `audit_action_for_transition` — because a domain that stays `live`
#: for a hundred sweeps must not write a hundred audit rows for a published fact.
_TRANSITION_AUDIT_ACTIONS: dict[str, str] = {
    "verified": "storefront.domain.verified",
    "live": "storefront.domain.activated",
    "failing": "storefront.domain.failing",
    "suspended": "storefront.domain.suspended",
}


def audit_action_for_transition(old_status: str, new_status: str) -> str | None:
    """The audit action for one state change, or `None` when nothing actually moved.

    `unverified` has no entry above: the claim itself is audited as `storefront.domain.claimed`
    at the moment it is written (`services.claim_custom_domain`), not as a status transition —
    there is no state before `unverified` to have transitioned *from*.
    """
    if old_status == new_status:
        return None
    return _TRANSITION_AUDIT_ACTIONS.get(new_status)


def assert_slug_is_unchanged(current: str, submitted: str) -> None:
    """A slug is set once and never edited (ADR-0011 §7).

    It is in every link a merchant has published, it is what an anonymous check names in its
    request body, it keys the buyer's `localStorage`, and the slug→store lookup is cached for
    five minutes — so a rename 404s every buyer for the length of that TTL, breaks every shared
    link permanently, and frees the old name for somebody else to claim and inherit the traffic.
    None of that is visible from the form, which is why the refusal is here rather than in a
    warning dialog.

    The settings form still **sends** the slug, because the payload is a full replacement and a
    field that silently ignores what you send it is worse than one that refuses.
    """
    if submitted != current:
        raise Conflicted(
            "slug_immutable",
            f"The store address {current!r} cannot be changed. It is in every link that has been "
            "shared, and it is what a buyer's app asks for by name — renaming it would break "
            "both, permanently. Ask support if you need it moved.",
            field="slug",
        )


def _suspended() -> Conflicted:
    return Conflicted(
        "store_suspended",
        "This store is suspended, so it cannot be published or unpublished from here. "
        "Suspension is set by the platform, not by the store — contact support.",
        field="status",
    )


def assert_publishable(status: str) -> None:
    """`draft → published`, or `published → published`. Nothing else may reach `published`.

    **Publishing is the act that puts a store in front of the internet** — and with it that
    merchant's delivery map, readable by anyone who knows the slug — so it is its own named
    transition rather than a value on a settings form. A save that can go live by accident is
    the failure a dropdown produces by default (ADR-0011 §4).
    """
    if status == "suspended":
        raise _suspended()


def assert_unpublishable(status: str) -> None:
    """`published → draft`, or `draft → draft`. A suspended store does not self-rescue.

    `suspended` is the entitlement-lapse consumer's state (ADR-0010 §4), and that consumer is
    still unowned. Letting a merchant leave the state would make the lapse mechanism advisory
    the day somebody builds it, so both transitions refuse it by name.
    """
    if status == "suspended":
        raise _suspended()


# --------------------------------------------------------------------- postal codes


def normalise_postal_code(raw: str) -> str:
    """Upper-case, strip every non-alphanumeric. `"400 050"` and `"400-050"` are one code.

    Normalising at the edge rather than at comparison time is what makes
    `UNIQUE (organization_id, postal_code)` mean what a merchant thinks it means: two spellings
    of one area cannot end up in two zones.
    """
    return re.sub(r"[^A-Za-z0-9]", "", raw).upper()


def validate_postal_code(raw: str, *, country: str = DEFAULT_COUNTRY) -> str:
    """Normalise and check the shape. Raises `Rejected`; returns the stored form.

    **A malformed postal code is a 422, never an answer.** "That is not a postal code" and "we
    do not deliver there" are different facts, and collapsing them would teach a buyer that a
    typo means the shop does not reach them.
    """
    code = normalise_postal_code(raw)
    if not code:
        raise Rejected("postal_code_missing", "Enter a postal code.")
    if len(code) > POSTAL_CODE_LENGTH:
        raise Rejected("postal_code_invalid", "That postal code is too long.")
    pattern = _POSTAL_CODE_PATTERNS.get(country)
    if pattern is None:  # pragma: no cover - one country, and the caller cannot pick another
        raise Rejected(
            "country_unsupported",
            f"{country} postal codes are not supported yet.",
            field="country",
        )
    if not pattern.fullmatch(code):
        raise Rejected(
            "postal_code_invalid",
            "That does not look like a postal code. Indian PIN codes are six digits, "
            "for example 400050.",
        )
    return code


# --------------------------------------------------------------------- the promise


def resolve_promise_date(
    *,
    now: dt.datetime,
    timezone: str,
    offset_days: int,
    cutoff: dt.time,
) -> dt.date:
    """The date a `DATE_OFFSET` zone promises, computed in the **store's** timezone.

    `now` is UTC and comes from the caller, so this function has no clock and a test can pin
    one. The store's timezone is on `storefronts`; the browser's is never an input, which is the
    whole of ADR-0000's "Also settled" rule applied to the one place a buyer reads a date.

    Orders placed before the local cutoff get `today + offset_days`; after it, one more day.
    """
    local = now.astimezone(_zone(timezone))
    day = local.date() + dt.timedelta(days=offset_days)
    if local.time() >= cutoff:
        day += dt.timedelta(days=1)
    return day


def today_in(timezone: str, *, now: dt.datetime) -> dt.date:
    """The store's own calendar day — what the demand counter is keyed on.

    A merchant's daily totals have to break at their own midnight, not at UTC's: a Mumbai store
    would otherwise see every evening's demand land on the following day's row.
    """
    return now.astimezone(_zone(timezone)).date()


def _zone(timezone: str) -> ZoneInfo:
    """Resolve an IANA name, refusing an unknown one loudly.

    A bad timezone on a storefront row is a configuration error that would otherwise silently
    produce dates in UTC — an answer that is wrong by hours and looks perfectly well-formed.
    """
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise Rejected(
            "timezone_invalid",
            f"{timezone!r} is not a known IANA timezone.",
            field="timezone",
        ) from exc


# --------------------------------------------------------------------- zone rules


def assert_mode_is_available(mode: str) -> None:
    """`SLOTTED` cannot be set until slice 2, and the refusal says so by name.

    ADR-0010 §3: a half-rendered slot picker in front of a real buyer is worse than a feature
    that is honestly absent. The mode is in the enum from day one so slice 2 is a validation
    change rather than a schema change — which is exactly why the *writer* has to be the thing
    that refuses it.
    """
    if mode == "SLOTTED":
        raise Rejected(
            "mode_not_available",
            "Slot-based delivery is not available yet. Delivery nodes, opening hours and slot "
            "templates arrive in the next slice; until they do, a zone can promise a time "
            "(PROMISED) or be excluded (UNSERVED).",
            field="mode",
        )


@dataclass(frozen=True, slots=True)
class PromiseInput:
    """The promise fields as a merchant supplies them, before they are trusted."""

    kind: str | None
    minutes: int | None
    offset_days: int | None
    cutoff: dt.time | None


def validate_promise(mode: str, promise: PromiseInput) -> None:
    """Refuse an incoherent promise at the boundary, in the merchant's words.

    The same rule is a check constraint on the table. Both, on purpose: the constraint is what
    makes a bad row impossible for a backfill or a hand-written UPDATE, and this is what turns
    it into a sentence a merchant can act on instead of a 409 naming a constraint.
    """
    if mode != "PROMISED":
        if promise.kind is not None:
            raise Rejected(
                "promise_not_applicable",
                "Only a PROMISED zone carries a promise. Clear the promise, or set the mode to "
                "PROMISED.",
                field="promise",
            )
        return
    if promise.kind == "DURATION":
        if promise.minutes is None or promise.minutes <= 0:
            raise Rejected(
                "promise_minutes_required",
                "A duration promise needs a positive number of minutes, for example 30.",
                field="promise_minutes",
            )
        return
    if promise.kind == "DATE_OFFSET":
        if promise.offset_days is None or promise.offset_days < 0:
            raise Rejected(
                "promise_offset_required",
                "A date promise needs a number of days from the order date — 0 for same day, "
                "1 for next day.",
                field="promise_offset_days",
            )
        if promise.cutoff is None:
            raise Rejected(
                "promise_cutoff_required",
                "A date promise needs a daily cutoff time. Orders after it are promised one "
                "day later.",
                field="promise_cutoff_time",
            )
        return
    raise Rejected(
        "promise_kind_required",
        "A PROMISED zone needs a promise: either a duration in minutes, or a number of days "
        "plus a daily cutoff.",
        field="promise_kind",
    )


# --------------------------------------------------------------------- the textarea and the CSV


def parse_postal_code_list(raw: str, *, country: str = DEFAULT_COUNTRY) -> list[str]:
    """The zone editor's textarea: one code per line or comma-separated, pasted from a sheet.

    De-duplicated **silently** here and refused loudly in the CSV import, and the difference is
    deliberate: a merchant pasting a column can see what they pasted, and a duplicate within one
    zone means the same thing twice. A duplicate *across* zones in a 19,000-row file is a
    contradiction the merchant cannot see, and last-wins on it is the silent wrong answer
    (ADR-0010 §9).
    """
    # Split on line breaks, commas and semicolons — **not** on every space. A merchant writes
    # "400 050", and a splitter that treated the internal space as a separator would turn one
    # postal code into two invalid ones and refuse a paste that was perfectly correct.
    tokens = [t.strip() for t in re.split(r"[,;\r\n]+", raw) if t.strip()]
    if len(tokens) > MAX_POSTAL_CODES_PER_ZONE:
        raise Rejected(
            "too_many_postal_codes",
            f"That is {len(tokens)} postal codes; a zone holds at most "
            f"{MAX_POSTAL_CODES_PER_ZONE}. Use the CSV import for a list this size.",
            field="postal_codes",
        )
    seen: dict[str, None] = {}
    for token in tokens:
        seen[validate_postal_code(token, country=country)] = None
    return list(seen)


@dataclass(frozen=True, slots=True)
class ImportRow:
    postal_code: str
    zone_code: str
    line: int


@dataclass(frozen=True, slots=True)
class ParsedImport:
    rows: list[ImportRow]

    @property
    def zone_codes(self) -> set[str]:
        return {r.zone_code for r in self.rows}


_IMPORT_HEADER = ("postal_code", "zone_code")


def parse_import_csv(raw: str, *, country: str = DEFAULT_COUNTRY) -> ParsedImport:
    """Two columns, `postal_code,zone_code`, with a header row. Pure — no SQL, no session.

    **A duplicate postal code in the file is refused, naming the line numbers.** Last-wins on a
    file a merchant cannot read back is the silent wrong answer, and a silent wrong answer in
    19,000 rows is found by a customer rather than by the merchant. Detected here, before a
    single statement runs, which is also what makes "all or nothing" cheap.
    """
    reader = csv.reader(io.StringIO(raw.lstrip("﻿")))
    try:
        header = next(reader)
    except StopIteration:
        raise Rejected("import_empty", "That file is empty.", field="file") from None

    if tuple(h.strip().lower() for h in header[:2]) != _IMPORT_HEADER:
        raise Rejected(
            "import_header_invalid",
            "The first row must be the header `postal_code,zone_code`.",
            field="file",
        )

    rows: list[ImportRow] = []
    first_seen: dict[str, int] = {}
    duplicates: dict[str, list[int]] = {}
    invalid: list[int] = []
    blank_zone: list[int] = []

    for offset, record in enumerate(reader, start=2):
        if not record or not any(cell.strip() for cell in record):
            continue  # a trailing newline, or a blank row a spreadsheet exported
        if len(rows) >= MAX_IMPORT_ROWS:
            raise Rejected(
                "import_too_large",
                f"That file has more than {MAX_IMPORT_ROWS} rows. Split it, or ask for the "
                "import to be run as a background job.",
                field="file",
            )
        raw_code = record[0].strip() if len(record) > 0 else ""
        zone_code = record[1].strip() if len(record) > 1 else ""
        try:
            postal_code = validate_postal_code(raw_code, country=country)
        except Rejected:
            invalid.append(offset)
            continue
        if not zone_code:
            blank_zone.append(offset)
            continue
        if postal_code in first_seen:
            duplicates.setdefault(postal_code, [first_seen[postal_code]]).append(offset)
            continue
        first_seen[postal_code] = offset
        rows.append(ImportRow(postal_code=postal_code, zone_code=zone_code, line=offset))

    if invalid:
        raise Rejected(
            "import_postal_code_invalid",
            f"{len(invalid)} row(s) do not carry a valid postal code. Nothing was imported.",
            field="file",
            lines=invalid[:50],
        )
    if blank_zone:
        raise Rejected(
            "import_zone_code_missing",
            f"{len(blank_zone)} row(s) have no zone code. Nothing was imported.",
            field="file",
            lines=blank_zone[:50],
        )
    if duplicates:
        offenders = sorted({line for lines in duplicates.values() for line in lines})
        raise Rejected(
            "import_duplicate_postal_code",
            f"{len(duplicates)} postal code(s) appear more than once, so which zone they "
            "belong to is ambiguous. Nothing was imported — remove the duplicates and try "
            "again.",
            field="file",
            lines=offenders[:50],
        )
    if not rows:
        raise Rejected("import_empty", "That file has a header and no rows.", field="file")
    return ParsedImport(rows=rows)


# --------------------------------------------------------------------- the theme document


def validate_theme_document(
    *, theme_key: str, mode: str, tokens: dict[str, Any], pages: list[Any]
) -> None:
    """Bound the envelope: `theme_key` and `mode`'s *shape* is pydantic's job, size is this
    module's (ADR-0011 §3). Nothing here reads inside a section — that is the editor's
    business, and this service stores it opaquely.
    """
    if not theme_key.strip():
        raise Rejected(
            "theme_key_required", "A theme document needs a theme_key.", field="theme_key"
        )
    if mode not in THEME_MODES:
        raise Rejected(
            "mode_invalid", f"mode must be one of {', '.join(THEME_MODES)}.", field="mode"
        )
    if len(pages) > MAX_THEME_PAGES:
        raise Rejected(
            "theme_too_many_pages",
            f"A store can hold at most {MAX_THEME_PAGES} pages; this document has {len(pages)}.",
            field="pages",
        )
    size = len(json.dumps(tokens).encode("utf-8")) + len(json.dumps(pages).encode("utf-8"))
    if size > MAX_THEME_DOCUMENT_BYTES:
        raise Rejected(
            "theme_document_too_large",
            f"That theme document is {size} bytes; the limit is {MAX_THEME_DOCUMENT_BYTES}.",
            field="pages",
        )


# --------------------------------------------------------------------- the catalogue


def assert_price_band(price_min_minor: int, price_max_minor: int, mrp_minor: int | None) -> None:
    """A price band cannot invert, and a printed MRP cannot sit below the band's floor.

    Both are also check constraints on `products` and `product_variants` — the constraint is
    what makes a bad row impossible for a backfill or a hand-written UPDATE, this is what turns
    the same rule into a sentence a merchant can act on instead of an anonymous 409.
    """
    if price_max_minor < price_min_minor:
        raise Rejected(
            "price_band_invalid",
            "The maximum price cannot be lower than the minimum price.",
            field="price_max_minor",
        )
    if mrp_minor is not None and mrp_minor < price_min_minor:
        raise Rejected(
            "mrp_below_price",
            "The MRP cannot be lower than the selling price — it would advertise a discount "
            "that does not exist.",
            field="mrp_minor",
        )


# --------------------------------------------------------------------- enquiries

#: new → contacted → won | lost. `won` and `lost` are terminal — an enquiry's outcome, once
#: decided, is not walked back through this route; a correction is a new enquiry, matching the
#: append-only rule on the row itself.
ENQUIRY_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"contacted"}),
    "contacted": frozenset({"won", "lost"}),
    "won": frozenset(),
    "lost": frozenset(),
}


def assert_enquiry_transition(current: str, target: str) -> None:
    """Refuse an illegal status move with a 409 naming both ends, per CLAUDE.md's "no
    free-form status set". Setting the same status again is a no-op, not a refusal."""
    if target not in ENQUIRY_STATUSES:  # pragma: no cover - schema Literal already refuses this
        raise Rejected(
            "status_invalid", f"{target!r} is not a known enquiry status.", field="status"
        )
    if current == target:
        return
    if target not in ENQUIRY_TRANSITIONS.get(current, frozenset()):
        raise Conflicted(
            "enquiry_transition_invalid",
            f"An enquiry cannot move from {current!r} to {target!r}. The path is "
            "new -> contacted -> won or lost.",
            field="status",
        )
