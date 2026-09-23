"""Pure rules for email-domain verification. No I/O, no session, no settings, no clock of its
own — the DNS lookup lives in `dns_check.py`, and the write transaction lives in `services.py`.

This is the vocabulary ADR-0000 §1 depends on: "email domain never silently selects an
organization; it may only select an identity provider, and only from a domain that has been
DNS-verified." Everything here exists to make that one sentence enforceable — a shape a domain
must have before it is stored, the token and TXT record that stand in for proof of ownership,
and the clock a claim runs against while nobody has proved anything yet.

**What is deliberately absent.** Nothing here decides *whether* a domain is currently free to
claim — `uq_org_domains_domain` (a plain, platform-wide unique constraint on
`account.org_domains.domain`) is the arbiter of that, the same way `services.py`'s docstring
for storefront's own domain claim explains it should be: a pre-check `SELECT` against another
tenant's row would itself be a cross-tenant read that row-level security correctly refuses to
answer, and it would still race a concurrent claim. See `services.claim_domain` for the
consequence this has for cross-organization takeover semantics.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Final

import idna

from platform_core.errors import ValidationProblem

#: Characters that mean whatever was typed is a pasted URL, an email address, or a Host header
#: nobody's browser ever sent — not a bare domain.
_NOT_IN_A_DOMAIN = "/\\ \t@:?#*_[]"


class DomainRejected(ValidationProblem):
    """A domain failed a shape or policy rule, with a message safe to show verbatim.

    `code` is the stable string a client branches on; `message` is already a sentence. Kept as
    its own subclass, rather than a bare `ValidationProblem(...)` at each call site, so every
    caller raises the same shape and a test can assert on `code` instead of parsing prose.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message, errors=[{"field": "domain", "code": code, "message": message}])
        self.code = code


#: Hostnames this platform will not let an organization claim as its own identity domain.
#: `opsgrids.com` and its subdomains are the platform's own; a customer proving "ownership" of
#: `login.opsgrids.com` by publishing a TXT record they control is not a thing DNS can ever make
#: true, and storing the claim anyway is a squat that becomes dangerous the day anything trusts
#: it. `stores.opsgrids.com` is the storefront platform host (ADR-0011) — every merchant's own
#: subdomain lives under it, and none of them is anybody's corporate email domain.
PLATFORM_DOMAIN_SUFFIXES: Final = (
    "opsgrids.com",
    "opsgrids.dev",
    "opsgrids.local",
    "stores.opsgrids.com",
)

#: A curated set of public suffixes — registrable-domain roots nobody may claim outright,
#: because "owning" `com` or `co.uk` is not a fact DNS or this platform can express, and a role
#: whose SSO domain was accidentally set to one would silently match every address on the
#: internet that happens to end in it once anything trusts `allowed_domains` for more than exact
#: string equality.
#:
#: **Deliberately not the Mozilla Public Suffix List.** The real list is several thousand
#: entries, changes on its own release schedule, and nothing in this platform depends on the
#: long tail of it — a customer typing `example.k12.ca.us` as their whole SSO domain is not a
#: case worth vendoring and refreshing a dependency for today. This is the list a support ticket
#: would actually hit: the common generic and country-code TLDs and their most common
#: second-level delegations. Follow-up, if this ever needs to be exact: vendor `publicsuffix2`
#: (or an equivalent) and replace this tuple with its registrable-domain check.
PUBLIC_SUFFIXES: Final = frozenset(
    {
        "com",
        "org",
        "net",
        "edu",
        "gov",
        "mil",
        "int",
        "io",
        "co",
        "ai",
        "dev",
        "app",
        "in",
        "us",
        "uk",
        "co.in",
        "org.in",
        "net.in",
        "gov.in",
        "co.uk",
        "org.uk",
        "gov.uk",
        "ac.uk",
        "me.uk",
        "com.au",
        "net.au",
        "org.au",
        "co.nz",
        "co.jp",
        "com.br",
        "com.cn",
        "co.za",
    }
)

MAX_DOMAIN_LENGTH: Final = 253


def normalise_domain(raw: str) -> str:
    """A hostname stored as an ASCII A-label, or `DomainRejected`.

    The steps, in order: strip whitespace, lower-case, drop a trailing root dot, IDNA/UTS-46
    encode. **IDNA2008 with UTS-46 (via the `idna` package), not the standard library's
    IDNA2003 `encodings.idna` codec** — the same choice `services/storefront/app/domain.py`
    makes and for the same reason (`pyproject.toml`'s own comment on the dependency): UTS-46
    folds case and width the way a mail client resolving the domain half of an address actually
    would, and it is the one library in this platform's dependency set that agrees with a
    browser rather than with a 20-year-old RFC. The stored, compared and published form is
    always the A-label (`xn--...`) — `_opsgrids-verify.xn--kln-sna.de`, never the Unicode form —
    because that is the form a DNS resolver is ever asked to look up.

    Refused, as a 422 naming `field: "domain"`:

    * empty, or longer than 253 octets either before or after encoding;
    * a scheme, path, port, credentials, wildcard or whitespace — the marks of a pasted URL or
      an email address rather than a bare domain;
    * a single label (`localhost`) or an all-digit last label (an IP address, or close enough
      to one that no registrar would issue it);
    * anything `idna` itself refuses: an over-long label, an empty label, a leading or trailing
      hyphen, a codepoint with no A-label;
    * a platform domain (`PLATFORM_DOMAIN_SUFFIXES`) or a bare public suffix
      (`PUBLIC_SUFFIXES`) — nothing proves ownership of either, ever.
    """
    value = raw.strip()
    if not value:
        raise DomainRejected("domain_required", "Enter a domain to claim.")
    if len(value) > MAX_DOMAIN_LENGTH:
        raise DomainRejected(
            "domain_too_long", f"A domain name is at most {MAX_DOMAIN_LENGTH} characters."
        )

    value = value.lower()
    if value.endswith("."):
        value = value[:-1]  # the root dot a fully-qualified name may carry
    if not value:
        raise DomainRejected("domain_required", "Enter a domain to claim.")
    if any(ch in value for ch in _NOT_IN_A_DOMAIN):
        raise DomainRejected(
            "domain_invalid",
            "Enter the domain on its own — no scheme, path, @ or port. For example acme.com.",
        )

    try:
        host = idna.encode(value, uts46=True).decode("ascii")
    except (idna.IDNAError, UnicodeError) as exc:
        raise DomainRejected("domain_invalid", "That does not look like a domain name.") from exc

    if len(host) > MAX_DOMAIN_LENGTH:
        raise DomainRejected(
            "domain_too_long", f"A domain name is at most {MAX_DOMAIN_LENGTH} characters."
        )

    # Checked ahead of the "at least two labels" rule below: a bare `com` or `org` is a
    # single label, and the more useful refusal names the actual mistake — a public suffix —
    # rather than the generic shape complaint a two-label domain would also get for the
    # unrelated reason of being too short.
    if host in PUBLIC_SUFFIXES:
        raise DomainRejected(
            "domain_public_suffix",
            "That is a public suffix, not a domain anyone can own. Enter your own domain, "
            "for example acme.com rather than com.",
        )
    if any(host == s or host.endswith(f".{s}") for s in PLATFORM_DOMAIN_SUFFIXES):
        raise DomainRejected(
            "domain_reserved", "That domain belongs to the platform and cannot be claimed."
        )

    labels = host.split(".")
    if len(labels) < 2:
        raise DomainRejected("domain_invalid", "That does not look like a domain name.")
    if labels[-1].isdigit():
        raise DomainRejected("domain_invalid", "Enter a domain name rather than an IP address.")
    return host


def generate_verification_token() -> str:
    """A fresh, unguessable token. Stored in the clear (unlike an invitation token) because it
    is meant to be shown back to the organization, repeatedly, until DNS carries it."""
    return secrets.token_hex(20)


#: `_opsgrids-verify.<domain>` — the label a caller is told to publish a TXT record at, and the
#: name `dns_check` resolves. A constant prefix rather than an inline f-string at each call
#: site, so the two cannot drift onto different spellings of the same record.
VERIFICATION_TXT_LABEL: Final = "_opsgrids-verify"


def verification_txt_host(domain: str) -> str:
    """`acme.com` -> `_opsgrids-verify.acme.com`."""
    return f"{VERIFICATION_TXT_LABEL}.{domain}"


def verification_txt_value(token: str) -> str:
    """`<token>` -> `opsgrids-verify=<token>` — what the caller publishes, and what the DNS
    check looks for among a name's TXT answers."""
    return f"opsgrids-verify={token}"


#: A claim not yet verified 14 days after it was (re)claimed is treated as abandoned. Chosen
#: over a shorter window because DNS propagation and a Monday-morning IT queue both eat into
#: it, and over a longer one because an abandoned claim on a real customer's domain is exactly
#: the account-takeover surface ADR-0000 §1 exists to close — the same fortnight
#: `services/storefront/app/domain.py`'s own `CLAIM_EXPIRY` already uses for the same tradeoff.
CLAIM_TTL: Final = timedelta(days=14)


def claim_expires_at(claimed_at: datetime) -> datetime:
    return claimed_at + CLAIM_TTL


def is_claim_expired(claimed_at: datetime, *, now: datetime | None = None) -> bool:
    """Only meaningful while unverified — a verified claim's clock is never read again."""
    return (now or datetime.now(UTC)) >= claim_expires_at(claimed_at)
