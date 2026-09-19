"""Pure rules for the store's identity and its serviceability arithmetic. No I/O, no session,
no settings, no clock of its own.

Five things live here, and each is here because it is a real rule rather than a line of glue:

0. **The store's identity** (ADR-0011) — brand text is trimmed, bounded and refused when it is
   blank or unrenderable; a custom domain is parsed into a hostname or refused by name; the slug
   is immutable; and `published` is reached only through a named transition. Note what is
   *absent*: nothing here escapes anything for HTML, and nothing here verifies that a merchant
   owns a domain. The first belongs to the renderer, the second does not exist.

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
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import (
    LOGO_TEXT_LENGTH,
    POSTAL_CODE_LENGTH,
    STORE_NAME_LENGTH,
    TAGLINE_LENGTH,
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
