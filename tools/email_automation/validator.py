"""Email validation for syntax and domain routing (MX/A records)."""

from __future__ import annotations

import re
from typing import Final

try:
    import dns.resolver

    _HAVE_DNS = True
except ImportError:
    _HAVE_DNS = False

try:
    import email_validator

    _HAVE_EMAIL_VALIDATOR = True
except ImportError:
    _HAVE_EMAIL_VALIDATOR = False

# RFC 5322 compliant regex for basic syntax check when email_validator is not active
_RFC5322_EMAIL_REGEX: Final = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)

# Known disposable or fake domains for early rejection
DISPOSABLE_DOMAINS: Final = {
    "mailinator.com",
    "guerrillamail.com",
    "10minutemail.com",
    "tempmail.com",
    "yopmail.com",
    "trashmail.com",
}


def normalize_email(email: str) -> str:
    """Strips whitespace and converts the address to lowercase."""
    return email.strip().lower()


def validate_email_syntax(email: str) -> tuple[bool, str]:
    """Validates email address syntax against RFC standards.

    Returns:
        (True, "valid") if correct, or (False, reason) if malformed.
    """
    if not email or not isinstance(email, str):
        return False, "email_empty"

    email = normalize_email(email)

    if len(email) > 254:
        return False, "email_too_long"

    if "@" not in email:
        return False, "missing_at_symbol"

    local_part, domain_part = email.rsplit("@", 1)
    if not local_part or not domain_part:
        return False, "empty_local_or_domain"

    if len(local_part) > 64:
        return False, "local_part_too_long"

    if _HAVE_EMAIL_VALIDATOR:
        try:
            email_validator.validate_email(email, check_deliverability=False)
            return True, "valid"
        except email_validator.EmailNotValidError as exc:
            return False, f"syntax_error: {exc!s}"

    if not _RFC5322_EMAIL_REGEX.match(email):
        return False, "syntax_error: does not match RFC 5322 specification"

    return True, "valid"


def validate_email_domain(email: str, check_mx: bool = True) -> tuple[bool, str]:
    """Checks whether the domain portion of an email is valid and can receive mail.

    Checks:
    - Disposable email list
    - MX record existence via DNS
    - Fallback A record existence (RFC 5321 §5.1 allows A record if no MX is declared)
    """
    syntax_ok, reason = validate_email_syntax(email)
    if not syntax_ok:
        return False, reason

    email = normalize_email(email)
    _, domain = email.rsplit("@", 1)

    if domain in DISPOSABLE_DOMAINS:
        return False, f"disposable_domain: {domain} is rejected"

    if not check_mx or not _HAVE_DNS:
        return True, "valid"

    # Test DNS MX and A records
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = 3.0
        resolver.lifetime = 3.0

        # Try MX records first
        try:
            mx_answers = resolver.resolve(domain, "MX")
            if mx_answers and len(mx_answers) > 0:
                return True, "valid"
        except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            # Fallback to A record per RFC 5321
            try:
                a_answers = resolver.resolve(domain, "A")
                if a_answers and len(a_answers) > 0:
                    return True, "valid"
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                return False, f"domain_no_mail_exchange: {domain} has neither MX nor A records"
        except dns.resolver.NXDOMAIN:
            return False, f"domain_not_found: {domain} does not exist"

    except dns.resolver.LifetimeTimeout:
        # On timeout, fail-open on network glitch or return warning depending on policy
        return True, "dns_timeout_deferred"
    except Exception as exc:
        return False, f"dns_lookup_failed: {exc!s}"

    return True, "valid"


def verify_email(email: str, check_dns: bool = True) -> tuple[bool, str]:
    """Unified email check combining syntax and domain checks."""
    syntax_valid, reason = validate_email_syntax(email)
    if not syntax_valid:
        return False, reason

    if check_dns:
        return validate_email_domain(email, check_mx=True)

    return True, "valid"
