"""Pure rules for email-domain verification. No I/O, no session, no settings, no clock of its
own — the DNS lookup lives in `dns_check.py`, and the write transaction lives in `services.py`.

This is the vocabulary ADR-0000 §1 depends on: "email domain never silently selects an
organization; it may only select an identity provider, and only from a domain that has been
DNS-verified." Everything here exists to make that one sentence enforceable — a shape a domain
must have before it is stored, the token and TXT record that stand in for proof of ownership,
and the clock a claim runs against while nobody has proved anything yet.

**What is deliberately absent.** Nothing here decides *whether* a domain is currently free to
claim or verify — `account.org_domains`'s two constraints are the arbiter of that
(`uq_org_domains_org_domain` per organization, `uq_org_domains_domain_verified` platform-wide
but verified-only; see `OrgDomain`'s own docstring in `models.py`), the same way
`services/storefront/app/services.py`'s own docstring explains a domain claim should be
arbitrated: a pre-check `SELECT` against another tenant's row would itself be a cross-tenant
read that row-level security correctly refuses to answer, and it would still race a concurrent
claim. See `services.claim_domain` and `services.verify_domain` for what this means for
cross-organization takeover safety.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Final

import idna
from publicsuffixlist import PublicSuffixList

from platform_core.errors import ValidationProblem

#: The real Public Suffix List, offline — the wheel ships a snapshot
#: (`publicsuffixlist/public_suffix_list.dat`) and this constructor never touches the network;
#: verified by hand against a patched `socket.connect` before this was trusted (see the
#: dependency's own comment in `pyproject.toml`). Built once, at import, the same way `idna`'s
#: own Unicode tables are a one-time cost rather than per-call I/O.
_PSL = PublicSuffixList()

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

#: RFC 6761 / IANA special-use names. None of these can ever resolve on the public internet by
#: definition — `.arpa` is infrastructure, `.onion` is Tor-only, and `local`/`internal`/`test`/
#: `example`/`invalid`/`localhost`/`home.arpa` are reserved precisely so nobody's real DNS ever
#: answers for them. A TXT record "published" at `_opsgrids-verify.acme.internal` cannot mean
#: anything a public resolver would ever see, so a claim on one is not a claim this platform can
#: ever honestly verify — refused at the door rather than left to fail every check forever.
SPECIAL_USE_SUFFIXES: Final = (
    "local",
    "internal",
    "test",
    "example",
    "invalid",
    "onion",
    "localhost",
    "home.arpa",
    "arpa",
)


def is_public_suffix(host: str) -> bool:
    """Whether `host` (already an ASCII A-label) is itself a full entry on the Public Suffix
    List — `com`, `co.uk`, `github.io` — rather than a domain registered under one.

    Backed by `publicsuffixlist`'s bundled, offline snapshot (`pyproject.toml`'s own comment
    explains the pin), not a hand-curated list: "owning" a public suffix is not a fact DNS or
    this platform can express, and a role whose SSO domain was accidentally set to one would
    silently match every address on the internet that happens to end in it, the day anything
    trusts a domain claim for more than exact string equality. A curated subset used to stand
    in here; it covered the common TLDs a support ticket would actually hit and said so, but
    "the common ones" is exactly the kind of list that is right until the day a customer's
    domain sits on the entry it missed.
    """
    return bool(_PSL.is_public(host))


#: 253 is the wire limit for a whole FQDN, but the domain half alone has to leave room for the
#: label this service prepends to check it: `_opsgrids-verify.` is 17 characters, and
#: `253 - 17 = 236`. A domain right at the old 253-character ceiling could never actually be
#: verified — the TXT record's own name would be rejected by every resolver before DNS was
#: ever asked — so the claim is refused up front instead of accepted and left permanently
#: unverifiable.
MAX_DOMAIN_LENGTH: Final = 236

#: How many domains one organization may claim at once, pending or verified together. Not a
#: security boundary — claiming costs nothing on its own — but an unbounded claim table is an
#: unbounded row count for `due_for_recheck` to walk forever and an unbounded list for
#: `GET /domains` to page, for an organization that has, realistically, a handful of real
#: domains. Raised on request if a legitimate customer ever needs more; nobody has yet.
MAX_DOMAINS_PER_ORGANIZATION: Final = 50


def normalise_domain(raw: str) -> str:
    """A hostname stored as an ASCII A-label, or `DomainRejected`.

    The steps, in order: strip whitespace, lower-case, strip every trailing dot, IDNA/UTS-46
    encode. **IDNA2008 with UTS-46 (via the `idna` package), not the standard library's
    IDNA2003 `encodings.idna` codec** — the same choice `services/storefront/app/domain.py`
    makes and for the same reason (`pyproject.toml`'s own comment on the dependency): UTS-46
    folds case and width the way a mail client resolving the domain half of an address actually
    would, and it is the one library in this platform's dependency set that agrees with a
    browser rather than with a 20-year-old RFC. The stored, compared and published form is
    always the A-label (`xn--...`) — `_opsgrids-verify.xn--kln-sna.de`, never the Unicode form —
    because that is the form a DNS resolver is ever asked to look up.

    Refused, as a 422 naming `field: "domain"`:

    * empty (including a string of nothing but dots), or longer than `MAX_DOMAIN_LENGTH`
      octets either before or after encoding;
    * a scheme, path, port, credentials, wildcard or whitespace — the marks of a pasted URL or
      an email address rather than a bare domain;
    * a single label (`localhost`) or an all-digit last label (an IP address, or close enough
      to one that no registrar would issue it);
    * anything `idna` itself refuses: an over-long label, an empty label, a leading or trailing
      hyphen, a codepoint with no A-label;
    * a domain that still carries a trailing dot after encoding — defensive: nothing observed
      produces one, and a name DNS would read as absolute is refused rather than trusted;
    * a platform domain (`PLATFORM_DOMAIN_SUFFIXES`), an RFC 6761 special-use name
      (`SPECIAL_USE_SUFFIXES` — nothing public can ever resolve one) or a bare public suffix
      (`is_public_suffix`, the real Public Suffix List) — nothing proves ownership of any of
      the three, ever.
    """
    value = raw.strip()
    if not value:
        raise DomainRejected("domain_required", "Enter a domain to claim.")

    value = value.lower().rstrip(".")
    if not value:
        raise DomainRejected("domain_required", "Enter a domain to claim.")
    if len(value) > MAX_DOMAIN_LENGTH:
        raise DomainRejected(
            "domain_too_long", f"A domain name is at most {MAX_DOMAIN_LENGTH} characters."
        )
    if any(ch in value for ch in _NOT_IN_A_DOMAIN):
        raise DomainRejected(
            "domain_invalid",
            "Enter the domain on its own — no scheme, path, @ or port. For example acme.com.",
        )

    try:
        host = idna.encode(value, uts46=True).decode("ascii")
    except (idna.IDNAError, UnicodeError) as exc:
        raise DomainRejected("domain_invalid", "That does not look like a domain name.") from exc

    if host.endswith("."):
        raise DomainRejected("domain_invalid", "That does not look like a domain name.")
    if len(host) > MAX_DOMAIN_LENGTH:
        raise DomainRejected(
            "domain_too_long", f"A domain name is at most {MAX_DOMAIN_LENGTH} characters."
        )

    # Checked ahead of the "at least two labels" rule below: a bare `com` or `org` is a
    # single label, and the more useful refusal names the actual mistake — a public suffix —
    # rather than the generic shape complaint a two-label domain would also get for the
    # unrelated reason of being too short.
    if is_public_suffix(host):
        raise DomainRejected(
            "domain_public_suffix",
            "That is a public suffix, not a domain anyone can own. Enter your own domain, "
            "for example acme.com rather than com.",
        )
    if any(host == s or host.endswith(f".{s}") for s in PLATFORM_DOMAIN_SUFFIXES):
        raise DomainRejected(
            "domain_reserved", "That domain belongs to the platform and cannot be claimed."
        )
    if any(host == s or host.endswith(f".{s}") for s in SPECIAL_USE_SUFFIXES):
        raise DomainRejected(
            "domain_reserved",
            "That is a reserved name (RFC 6761) and can never resolve publicly, so it can "
            "never be verified.",
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


# --------------------------------------------------------------------- re-verification and demotion
#
# Verifying a domain once, at claim time, proves ownership *then*. It says nothing about a
# year later — a DNS migration, an expired zone, a deliberately removed record — and until
# this policy existed nothing here ever checked again. `services.recheck_domain` re-asks DNS
# on a schedule (`app/cron.py`); this section is the *policy* for what a sustained run of
# failures means, kept here, pure, so the threshold is one place to read and one place to
# change rather than a number buried in the sweep.
#
# **Demotion, not silence.** A single missed check is almost always transient — a registrar
# hiccup, a zone edit mid-flight — and revoking single sign-on for an entire organization on
# the first one would fail far more often than it protects anything. So failures accumulate
# (`OrgDomain.consecutive_failures`, reset to zero the moment a check passes) and only a
# *sustained* run — both long in wall-clock time and repeated on distinct checks — clears
# `verified_at`. Both conditions, not either: a sweep run every few minutes during an incident
# should not demote a domain by lunchtime on failure count alone, and a domain that fails
# once, recovers, and fails again once a month for over two weeks should not demote on
# elapsed time alone. Demotion is recorded (`OrgDomain.demoted_at`, an audit row, and
# `core.identity.domain.demoted`) precisely because it is consequential: the internal SSO
# lookup (`api/internal.py`) filters on `verified_at IS NOT NULL`, so a demoted domain stops
# routing sign-in the instant it happens, silently to the browser but never silently to the
# organization — the event and the audit row exist so an administrator finds out. Re-earning
# `verified_at` after a demotion is an ordinary re-claim and re-verify; nothing here does it
# for them. See docs/adr/0018-domain-verification-lifecycle.md for the decision record and
# infrastructure/edge/RUNBOOK.md for what an operator does when this fires.
DEMOTION_FAILING_FOR: Final = timedelta(days=14)
DEMOTION_MIN_CONSECUTIVE_FAILURES: Final = 3


def should_demote(
    *, failing_since: datetime | None, consecutive_failures: int, now: datetime | None = None
) -> bool:
    """Whether a verified domain has failed for long enough, and often enough, to demote."""
    if failing_since is None:
        return False
    at = now or datetime.now(UTC)
    return (
        at - failing_since >= DEMOTION_FAILING_FOR
        and consecutive_failures >= DEMOTION_MIN_CONSECUTIVE_FAILURES
    )
