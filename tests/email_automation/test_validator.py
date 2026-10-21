"""Tests for email syntax and domain verification."""

from tools.email_automation.validator import (
    normalize_email,
    validate_email_domain,
    validate_email_syntax,
    verify_email,
)


def test_normalize_email() -> None:
    assert normalize_email("  User.Name@Example.COM  ") == "user.name@example.com"


def test_validate_email_syntax_valid() -> None:
    valid_emails = [
        "support@opsgrids.com",
        "alex.smith+tag@company.co.uk",
        "john_doe-123@subdomain.domain.org",
    ]
    for email in valid_emails:
        ok, reason = validate_email_syntax(email)
        assert ok, f"Expected {email} to be valid, got: {reason}"


def test_validate_email_syntax_invalid() -> None:
    invalid_cases = [
        "",
        "plainaddress",
        "@missinglocal.com",
        "missingatsign.com",
        "two..dots@domain.com",
        "user@.leadingdot.com",
        "a" * 65 + "@domain.com",  # Local part > 64 chars
    ]
    for email in invalid_cases:
        ok, reason = validate_email_syntax(email)
        assert not ok, f"Expected {email} to be invalid, but passed: {reason}"


def test_validate_email_disposable_domain() -> None:
    disposable = "spammer@mailinator.com"
    ok, reason = validate_email_domain(disposable, check_mx=False)
    assert not ok
    assert "disposable_domain" in reason


def test_verify_email_non_existent_domain() -> None:
    fake_domain_email = "test@domain-that-does-not-exist-at-all-xyz98741.com"
    ok, reason = verify_email(fake_domain_email, check_dns=True)
    assert not ok
    assert "domain_not_found" in reason or "no_mail_exchange" in reason
